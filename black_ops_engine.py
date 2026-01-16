# black_ops_engine.py
# ==============================================================================
# PROJECT OMEGA: INDEPENDENT OPERATIONS ENGINE (v12.3 LOCKED)
# LOGIC: Pulls truth directly from session_manager.resolve_anchor_time
# FIX: Enforces strict 24h context locking to match Session Control
# ==============================================================================
from __future__ import annotations
from typing import Dict, Any, List

# CORE INTEGRATION
import session_manager 
import battlebox_pipeline
import sse_engine

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

def _calc_strength(entry: float, stop: float, dr: float, ds: float, side: str) -> dict:
    score = 0
    is_blue_sky = False
    if entry == 0: return {"score": 0, "rating": "WAITING", "tags": [], "is_blue_sky": False}
    if side == "LONG" and entry > dr: is_blue_sky = True
    if side == "SHORT" and entry < ds: is_blue_sky = True
    return {"score": score, "rating": "GO", "tags": [], "is_blue_sky": is_blue_sky}

# --- MAIN ENGINE ---
async def get_omega_status(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    current_price = 0.0
    try:
        # 1. GET CENTRAL TRUTH
        session = session_manager.resolve_anchor_time("us_ny_futures")
        
        anchor_ts = session["anchor_ts"]
        lock_end_ts = session["lock_end_ts"]
        session_status = session["status"]

        # 2. DATA FEED
        raw_5m = await battlebox_pipeline.fetch_live_5m(symbol, limit=2000)
        if not raw_5m or len(raw_5m) < 20:
            return {"ok": False, "status": "OFFLINE", "msg": "Waiting for Data..."}
        
        current_price = float(raw_5m[-1]["close"])

        # 3. COMPUTE LEVELS (Strictly Locked)
        # Calibration: The 30m window (Anchor -> Lock Time)
        calibration_candles = [c for c in raw_5m if anchor_ts <= c["time"] < lock_end_ts]
        
        # --- THE FIX: Context is FROZEN to the Lock Time ---
        context_start = lock_end_ts - 86400
        context_24h = [c for c in raw_5m if context_start <= c["time"] < lock_end_ts]
        
        r30_high, r30_low = 0.0, 0.0
        dr, ds, bo, bd = 0.0, 0.0, 0.0, 0.0
        
        if len(calibration_candles) >= 4 and len(context_24h) > 100:
            r30_high = max(float(c["high"]) for c in calibration_candles)
            r30_low = min(float(c["low"]) for c in calibration_candles)
            
            # Use the FROZEN context for calculations (The Map)
            sse_input = {
                "locked_history_5m": context_24h,
                "slice_24h_5m": context_24h, 
                "session_open_price": float(calibration_candles[0]["open"]),
                "r30_high": r30_high,
                "r30_low": r30_low,
                "last_price": current_price,
                "tuning": {}
            }
            computed = sse_engine.compute_sse_levels(sse_input)
            levels = computed.get("levels", {})
            
            bo = float(levels.get("breakout_trigger", 0))
            bd = float(levels.get("breakdown_trigger", 0))
            dr = float(levels.get("daily_resistance", 0))
            ds = float(levels.get("daily_support", 0))

        # 4. EXECUTION (The Live Car)
        status = "STANDBY"
        active_side = "NONE"
        stop_loss = 0.0

        if session_status == "ACTIVE" or session_status == "CLOSED":
            if bo > 0 and bd > 0:
                # Use LIVE candles for triggers
                last_candle = raw_5m[-2] if len(raw_5m) > 1 else raw_5m[-1]
                
                # Check LIVE price against LOCKED levels
                if float(last_candle["close"]) > bo:
                    status = "EXECUTING"; active_side = "LONG"; stop_loss = r30_low
                elif float(last_candle["close"]) < bd:
                    status = "EXECUTING"; active_side = "SHORT"; stop_loss = r30_high
                else:
                    if abs(current_price - bo) / bo < 0.001: status = "LOCKED"; active_side = "LONG"
                    elif abs(current_price - bd) / bd < 0.001: status = "LOCKED"; active_side = "SHORT"
        
        if session_status == "CALIBRATING": status = "CALIBRATING"
        if session_status == "CLOSED": status = "CLOSED" 

        # 5. PACKAGING
        trigger_px = bo if active_side == "LONG" else bd
        if active_side == "NONE" and bo > 0: 
            trigger_px = bo if current_price > (bo+bd)/2 else bd
            
        strength = _calc_strength(trigger_px, stop_loss, dr, ds, active_side)
        energy = abs(dr - ds)
        if energy == 0: energy = current_price * 0.01

        targets = []
        if active_side == "LONG" or (active_side == "NONE" and bo > 0 and current_price > (bo+bd)/2):
            t1 = dr if not strength["is_blue_sky"] else trigger_px + (energy * 0.5)
            targets = [{"id": "T1", "price": round(t1, 2)}, {"id": "T2", "price": round(trigger_px + energy, 2)}, {"id": "T3", "price": round(trigger_px + (energy * 3.0), 2)}]
        elif bd > 0:
            t1 = ds if not strength["is_blue_sky"] else trigger_px - (energy * 0.5)
            targets = [{"id": "T1", "price": round(t1, 2)}, {"id": "T2", "price": round(trigger_px - energy, 2)}, {"id": "T3", "price": round(trigger_px - (energy * 3.0), 2)}]

        # Live Indicators
        stoch = _compute_stoch(raw_5m[-20:])
        rsi = _compute_rsi(raw_5m[-20:])

        next_open_ts = anchor_ts + 86400
        
        telemetry = {
            "session_state": session_status,
            "next_event_ts": lock_end_ts if session_status == "CALIBRATING" else next_open_ts,
            "verification": {
                "r30_high": r30_high,
                "r30_low": r30_low,
                "daily_res": dr,
                "daily_sup": ds
            }
        }

        return {
            "ok": True,
            "status": status,
            "symbol": symbol,
            "price": current_price,
            "side": active_side,
            "context": "BLUE SKY" if strength["is_blue_sky"] else "STRUCTURE",
            "strength": strength,
            "triggers": {"BO": bo, "BD": bd},
            "telemetry": telemetry,
            "execution": {
                "entry": trigger_px,
                "stop_loss": stop_loss,
                "targets": targets,
                "fusion_metrics": {"k": stoch["k"], "rsi": rsi}
            }
        }

    except Exception as e:
        return {"ok": False, "status": "ERROR", "price": current_price, "msg": str(e)}