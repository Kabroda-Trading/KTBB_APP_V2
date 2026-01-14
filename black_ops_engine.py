# black_ops_engine.py
# ==============================================================================
# PROJECT OMEGA: INDEPENDENT OPERATIONS ENGINE (FUSION PROTOCOL)
# CLASSIFICATION: TOP SECRET // NO EXTERNAL DEPENDENCIES
# ==============================================================================
from __future__ import annotations
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import traceback

# CORE SUITE INTEGRATION (Data & Math Only)
import battlebox_pipeline
import sse_engine

# --- OMEGA CONFIGURATION ---
OMEGA_CONFIG = {
    "confirmation_mode": "1_CLOSE",  # Instant Fire on Close
    "fusion_enabled": True           # Fusion Mode (Stoch + RSI)
}

# --- INTERNAL MATH HELPERS (Fusion Logic) ---
def _sma(vals: List[float], n: int) -> float:
    if len(vals) < n: return sum(vals) / max(len(vals), 1)
    return sum(vals[-n:]) / n

def _compute_stoch(candles: List[Dict], k_period: int = 14, d_period: int = 3) -> Dict[str, float]:
    if len(candles) < k_period: return {"k": 50.0, "d": 50.0}
    try:
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        closes = [float(c["close"]) for c in candles]
        
        # Calculate K
        hh = max(highs[-k_period:])
        ll = min(lows[-k_period:])
        curr = closes[-1]
        
        if hh == ll: k_val = 50.0
        else: k_val = ((curr - ll) / (hh - ll)) * 100.0
        
        # We simulate the smoothing by just taking the raw K for speed in breakout
        return {"k": k_val, "d": k_val} 
    except: return {"k": 50.0, "d": 50.0}

def _compute_rsi(candles: List[Dict], period: int = 14) -> float:
    if len(candles) < period + 1: return 50.0
    try:
        closes = [float(c["close"]) for c in candles]
        gains, losses = [], []
        
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i-1]
            gains.append(max(delta, 0))
            losses.append(max(-delta, 0))
            
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0: return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
    except: return 50.0

def _verify_breakout(side: str, trigger: float, history: List[Dict]) -> bool:
    """Checks for 1-Candle Close Confirmation"""
    if trigger == 0 or not history: return False
    close_px = float(history[-1]["close"])
    
    if side == "LONG": return close_px > trigger
    if side == "SHORT": return close_px < trigger
    return False

def _calc_strength(entry: float, stop: float, dr: float, ds: float, side: str) -> dict:
    """Calculates Trade Context (Eagle vs Vulture)"""
    score = 0
    reasons = []
    
    if entry == 0 or stop == 0: return {"score": 0, "rating": "WAITING", "tags": [], "is_blue_sky": False}

    # 1. BLUE SKY DETECTION (The Eagle)
    is_blue_sky = False
    if side == "LONG" and entry > dr: is_blue_sky = True
    if side == "SHORT" and entry < ds: is_blue_sky = True
    
    if is_blue_sky:
        score += 50
        reasons.append("BLUE SKY (EAGLE)")
    else:
        reasons.append("STRUCTURE (VULTURE)")

    # 2. COMPRESSION
    risk_pct = abs(entry - stop) / entry
    if risk_pct < 0.01:
        score += 30
        reasons.append("TIGHT RISK")
    
    return {"score": score, "rating": "GO", "tags": reasons, "is_blue_sky": is_blue_sky}

# --- MAIN ENGINE ---
async def get_omega_status(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    try:
        # 1. RESOLVE SESSION (FORCE NY FUTURES)
        now_utc = datetime.now(timezone.utc)
        session_info = battlebox_pipeline.resolve_session(now_utc, mode="MANUAL", manual_id="us_ny_futures")
        anchor_ts = session_info["anchor_time"]
        lock_end_ts = anchor_ts + 1800 
        
        # 2. DATA
        raw_5m = await battlebox_pipeline.fetch_live_5m(symbol, limit=2000)
        if not raw_5m or len(raw_5m) < 20:
            return {"status": "OFFLINE", "msg": "Waiting for Data..."}

        current_price = float(raw_5m[-1]["close"])

        # 3. LEVELS (SSE)
        calibration_candles = [c for c in raw_5m if anchor_ts <= c["time"] < lock_end_ts]
        if not calibration_candles:
             return {"status": "STANDBY", "msg": "Building Range..."}
             
        r30_high = max(float(c["high"]) for c in calibration_candles)
        r30_low = min(float(c["low"]) for c in calibration_candles)
        
        # Compute Levels
        sse_input = {
            "locked_history_5m": raw_5m, # Using full history for robustness
            "slice_24h_5m": raw_5m[-288:], 
            "session_open_price": float(calibration_candles[0]["open"]),
            "r30_high": r30_high,
            "r30_low": r30_low,
            "last_price": current_price,
            "tuning": {}
        }
        computed = sse_engine.compute_sse_levels(sse_input)
        levels = computed.get("levels", {})

        # 4. SIGNALS
        post_lock_candles = [c for c in raw_5m if c["time"] >= lock_end_ts]
        if not post_lock_candles:
             return {"status": "STANDBY", "msg": "Calibrating..."}

        bo = float(levels.get("breakout_trigger", 0))
        bd = float(levels.get("breakdown_trigger", 0))
        dr = float(levels.get("daily_resistance", 0))
        ds = float(levels.get("daily_support", 0))
        
        status = "STANDBY"
        active_side = "NONE"
        stop_loss = 0.0

        # FUSION CHECK (Internal)
        # We calculate it, but for Breakouts, we prioritize Price Action speed
        stoch = _compute_stoch(post_lock_candles)
        rsi = _compute_rsi(post_lock_candles)
        fusion_aligned = True # Default to True (Speed Mode)

        # 5. EXECUTION LOGIC (NO VOLUME CHECK)
        long_go = _verify_breakout("LONG", bo, post_lock_candles)
        short_go = _verify_breakout("SHORT", bd, post_lock_candles)

        if long_go:
            status = "EXECUTING"
            active_side = "LONG"
            stop_loss = r30_low
        elif short_go:
            status = "EXECUTING"
            active_side = "SHORT"
            stop_loss = r30_high
        else:
            # Standby / Locked Logic
            if bo > 0 and abs(current_price - bo) / bo < 0.001:
                status = "LOCKED"
                active_side = "LONG"
            elif bd > 0 and abs(current_price - bd) / bd < 0.001:
                status = "LOCKED"
                active_side = "SHORT"

        # 6. PACKAGING
        trigger_px = bo if active_side == "LONG" else bd
        if active_side == "NONE": trigger_px = bo if current_price > (bo+bd)/2 else bd
        
        strength = _calc_strength(trigger_px, stop_loss, dr, ds, active_side)
        
        # Energy Calculation for Targets
        energy = abs(dr - ds)
        if energy == 0: energy = current_price * 0.01

        targets = []
        if active_side != "NONE":
            # Target Logic uses ENERGY for dynamic spacing
            if active_side == "LONG":
                t1 = dr if not strength["is_blue_sky"] else trigger_px + (energy * 0.5)
                targets = [
                    {"id": "T1", "price": round(t1, 2)},
                    {"id": "T2", "price": round(trigger_px + energy, 2)},
                    {"id": "T3", "price": round(trigger_px + (energy * 3.0), 2)}
                ]
            else:
                t1 = ds if not strength["is_blue_sky"] else trigger_px - (energy * 0.5)
                targets = [
                    {"id": "T1", "price": round(t1, 2)},
                    {"id": "T2", "price": round(trigger_px - energy, 2)},
                    {"id": "T3", "price": round(trigger_px - (energy * 3.0), 2)}
                ]

        return {
            "ok": True,
            "status": status,
            "symbol": symbol,
            "price": current_price,
            "side": active_side,
            "context": "BLUE SKY" if strength["is_blue_sky"] else "STRUCTURE",
            "strength": strength,
            "triggers": {"BO": bo, "BD": bd},
            "execution": {
                "entry": trigger_px,
                "stop_loss": stop_loss,
                "targets": targets,
                "fusion_metrics": {"k": stoch["k"], "rsi": rsi}
            }
        }

    except Exception as e:
        return {"ok": False, "status": "ERROR", "msg": str(e)}