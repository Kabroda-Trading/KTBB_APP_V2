# black_ops_engine.py
# ==============================================================================
# PROJECT OMEGA: INDEPENDENT OPERATIONS ENGINE (v11.1 NATIVE)
# ==============================================================================
from __future__ import annotations
from typing import Dict, Any, List
from datetime import datetime, timezone

import session_manager  # <--- NEW AUTHORITY
import battlebox_pipeline
import sse_engine

# --- OMEGA CONFIGURATION ---
OMEGA_CONFIG = {
    "confirmation_mode": "1_CLOSE",
    "fusion_enabled": True
}

# --- INTERNAL MATH HELPERS ---
def _compute_stoch(candles: List[Dict], k_period: int = 14) -> Dict[str, float]:
    if len(candles) < k_period: return {"k": 50.0, "d": 50.0}
    try:
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        closes = [float(c["close"]) for c in candles]
        hh = max(highs[-k_period:])
        ll = min(lows[-k_period:])
        curr = closes[-1]
        if hh == ll: k_val = 50.0
        else: k_val = ((curr - ll) / (hh - ll)) * 100.0
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
    if trigger == 0 or not history: return False
    close_px = float(history[-1]["close"])
    if side == "LONG": return close_px > trigger
    if side == "SHORT": return close_px < trigger
    return False

def _calc_strength(entry: float, stop: float, dr: float, ds: float, side: str) -> dict:
    score = 0
    reasons = []
    is_blue_sky = False
    
    if entry == 0: return {"score": 0, "rating": "WAITING", "tags": [], "is_blue_sky": False}
    if side == "LONG" and entry > dr: is_blue_sky = True
    if side == "SHORT" and entry < ds: is_blue_sky = True
    
    if is_blue_sky:
        score += 50
        reasons.append("BLUE SKY (EAGLE)")
    else:
        reasons.append("STRUCTURE (VULTURE)")
    
    return {"score": score, "rating": "GO", "tags": reasons, "is_blue_sky": is_blue_sky}

# --- MAIN ENGINE ---
async def get_omega_status(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    current_price = 0.0
    try:
        # 1. RESOLVE SESSION (Using Session Manager)
        now_utc = datetime.now(timezone.utc)
        session_info = session_manager.resolve_current_session(now_utc, mode="MANUAL", manual_id="us_ny_futures")
        
        anchor_ts = session_info["anchor_time"]
        lock_end_ts = anchor_ts + 1800 
        
        # 2. DATA FEED
        raw_5m = await battlebox_pipeline.fetch_live_5m(symbol, limit=2000)
        if not raw_5m or len(raw_5m) < 20:
            return {"ok": False, "status": "OFFLINE", "msg": "Waiting for Data..."}

        current_price = float(raw_5m[-1]["close"])

        # 3. LEVELS (SSE ENGINE)
        calibration_candles = [c for c in raw_5m if anchor_ts <= c["time"] < lock_end_ts]
        
        if len(calibration_candles) < 4:
            if session_info["energy"] == "CALIBRATING":
                return {"ok": True, "status": "STANDBY", "price": current_price, "msg": "Calibrating (8:30-9:00)..."}
            elif session_info["elapsed_min"] > 30:
                calibration_candles = raw_5m[-7:-1]

        r30_high = max(float(c["high"]) for c in calibration_candles) if calibration_candles else 0.0
        r30_low = min(float(c["low"]) for c in calibration_candles) if calibration_candles else 0.0
        
        sse_input = {
            "locked_history_5m": raw_5m,
            "slice_24h_5m": raw_5m[-288:], 
            "session_open_price": float(calibration_candles[0]["open"]) if calibration_candles else 0.0,
            "r30_high": r30_high,
            "r30_low": r30_low,
            "last_price": current_price,
            "tuning": {}
        }
        computed = sse_engine.compute_sse_levels(sse_input)
        levels = computed.get("levels", {})

        # 4. TRIGGERS
        bo = float(levels.get("breakout_trigger", 0))
        bd = float(levels.get("breakdown_trigger", 0))
        dr = float(levels.get("daily_resistance", 0))
        ds = float(levels.get("daily_support", 0))
        
        # 5. EXECUTION LOGIC
        last_candle = raw_5m[-2] if len(raw_5m) > 1 else raw_5m[-1]
        
        status = "LOCKED"
        active_side = "NONE"
        stop_loss = 0.0

        long_go = (float(last_candle["close"]) > bo)
        short_go = (float(last_candle["close"]) < bd)

        if long_go:
            status = "EXECUTING"
            active_side = "LONG"
            stop_loss = r30_low
        elif short_go:
            status = "EXECUTING"
            active_side = "SHORT"
            stop_loss = r30_high
        else:
            if bo > 0 and abs(current_price - bo) / bo < 0.001:
                status = "LOCKED"
                active_side = "LONG"
            elif bd > 0 and abs(current_price - bd) / bd < 0.001:
                status = "LOCKED"
                active_side = "SHORT"

        trigger_px = bo if active_side == "LONG" else bd
        if active_side == "NONE": 
            trigger_px = bo if current_price > (bo+bd)/2 else bd
            
        strength = _calc_strength(trigger_px, stop_loss, dr, ds, active_side)
        
        energy = abs(dr - ds)
        if energy == 0: energy = current_price * 0.01

        targets = []
        if active_side == "LONG" or (active_side == "NONE" and current_price > (bo+bd)/2):
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

        stoch = _compute_stoch(raw_5m[-20:])
        rsi = _compute_rsi(raw_5m[-20:])

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
        return {"ok": False, "status": "ERROR", "price": current_price, "msg": str(e)}