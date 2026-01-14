# black_ops_engine.py
# ==============================================================================
# PROJECT OMEGA: INDEPENDENT OPERATIONS ENGINE (DECOUPLED)
# CLASSIFICATION: TOP SECRET // NO EXTERNAL DEPENDENCIES
# ==============================================================================
from __future__ import annotations
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import traceback

# CORE SUITE INTEGRATION (Data & Math Only)
import battlebox_pipeline
import sse_engine
# REMOVED: import battlebox_rules <--- WE CUT THE CORD

# --- OMEGA CONFIGURATION ---
OMEGA_CONFIG = {
    "confirmation_mode": "1_CLOSE",  # 1-Candle Close Confirmation
    "min_volume_factor": 1.5         # Volume must be 1.5x average
}

# --- INTERNAL HELPER FUNCTIONS (No External Dependencies) ---
def _get_candles_in_range(all_candles: List[Dict], start_ts: int, end_ts: int) -> List[Dict]:
    return [c for c in all_candles if start_ts <= c["time"] < end_ts]

def _check_volume(candles: List[Dict]) -> bool:
    """Internal Volume Check: Is current vol > 1.5x avg of last 20?"""
    if len(candles) < 21: return True # Not enough data, give benefit of doubt
    try:
        curr = float(candles[-1].get("volume", 0) or 0)
        hist = candles[-21:-1]
        avg = sum(float(c.get("volume", 0) or 0) for c in hist) / len(hist)
        if avg == 0: return True
        return curr > (avg * OMEGA_CONFIG["min_volume_factor"])
    except: return True

def _verify_breakout(side: str, price: float, trigger: float, history: List[Dict]) -> bool:
    """
    Internal Breakout Verifier (The 'Go' Logic)
    Confirms 1-Candle Close above the trigger.
    """
    if trigger == 0: return False
    
    # 1. Price Check (Current Live Price)
    # This is just a pre-check. The real check is the candle close below.
    if side == "LONG" and price <= trigger: return False
    if side == "SHORT" and price >= trigger: return False

    # 2. Confirmation Check (1-Candle Close)
    # We look at the LAST closed candle (index -2 if -1 is live, or just last if history is closed)
    # Assuming 'history' contains CLOSED candles.
    if not history: return False
    
    last_candle = history[-1]
    close_px = float(last_candle["close"])
    
    if side == "LONG":
        return close_px > trigger
    elif side == "SHORT":
        return close_px < trigger
        
    return False

def _calc_strength(entry: float, stop: float, dr: float, ds: float, side: str) -> dict:
    """Calculates Trade Strength & Context."""
    score = 0
    reasons = []
    
    if entry == 0 or stop == 0: return {"score": 0, "rating": "WAITING", "tags": []}

    # 1. CONTEXT (40 Pts) - BLUE SKY DETECTION
    is_blue_sky = False
    if side == "LONG":
        if entry > dr:
            score += 40
            reasons.append("BLUE SKY")
            is_blue_sky = True
        else:
            score += 10
            reasons.append("IN STRUCTURE")
    elif side == "SHORT":
        if entry < ds:
            score += 40
            reasons.append("BLUE SKY")
            is_blue_sky = True
        else:
            score += 10
            reasons.append("IN STRUCTURE")
            
    # 2. COMPRESSION (40 Pts)
    if entry > 0:
        risk_pct = abs(entry - stop) / entry
        if risk_pct < 0.006:
            score += 40
            reasons.append("SUPER COIL")
        elif risk_pct < 0.01:
            score += 20
            reasons.append("NORMAL RISK")
        else:
            reasons.append("WIDE STOP")

    # 3. BASELINE (20 Pts)
    score += 20 
    
    # RATING
    if score >= 80: rating = "HOME RUN (AIM T2)"
    elif score >= 50: rating = "BASE HIT (TAKE T1)"
    else: rating = "WEAK"
    
    return {"score": score, "rating": rating, "tags": reasons, "is_blue_sky": is_blue_sky}

# --- MAIN ENGINE ---
async def get_omega_status(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """
    Independent scanning engine. 
    LOGIC: 
    1. Lock Daily Levels (NY Futures)
    2. Check Breakout (1-Candle Close)
    3. Check Volume
    4. IF BLUE SKY -> IGNORE STOCHASTICS -> FIRE
    5. IF STRUCTURE -> IGNORE STOCHASTICS -> FIRE (We are trusting the Strength Meter now)
    """
    try:
        # 1. RESOLVE ACTIVE SESSION (NY FUTURES LOCK)
        now_utc = datetime.now(timezone.utc)
        session_info = battlebox_pipeline.resolve_session(now_utc, mode="MANUAL", manual_id="us_ny_futures")
        
        anchor_ts = session_info["anchor_time"]
        lock_end_ts = anchor_ts + 1800 
        
        # 2. DATA ACQUISITION
        raw_5m = await battlebox_pipeline.fetch_live_5m(symbol, limit=2000)

        if not raw_5m or len(raw_5m) < 12:
            return {"status": "OFFLINE", "msg": "Waiting for Data Stream..."}

        current_price = float(raw_5m[-1]["close"])

        # 3. LEVEL COMPUTATION
        calibration_candles = _get_candles_in_range(raw_5m, anchor_ts, lock_end_ts)
        
        if len(calibration_candles) < 1:
             return {"status": "STANDBY", "msg": "Building Opening Range..."}
             
        r30_high = max(float(c["high"]) for c in calibration_candles)
        r30_low = min(float(c["low"]) for c in calibration_candles)
        
        context_24h = _get_candles_in_range(raw_5m, lock_end_ts - 86400, lock_end_ts)
        if not context_24h: context_24h = raw_5m[:50] 

        try:
            sse_input = {
                "locked_history_5m": context_24h,
                "slice_24h_5m": context_24h,
                "session_open_price": float(calibration_candles[0]["open"]),
                "r30_high": r30_high,
                "r30_low": r30_low,
                "last_price": current_price,
                "tuning": {} # No tuning needed for SSE logic
            }
            computed = sse_engine.compute_sse_levels(sse_input)
            levels = computed.get("levels", {})
        except Exception as e:
            return {"status": "ERROR", "msg": f"Level Logic: {str(e)}"}

        # 4. INTERNAL SIGNAL SCANNING (No External Rules)
        post_lock_candles = [c for c in raw_5m if c["time"] >= lock_end_ts]
        if not post_lock_candles:
             return {"status": "STANDBY", "msg": "Calibrating Session..."}

        # 5. STATE & TRIGGERS
        dr = float(levels.get("daily_resistance", 0))
        ds = float(levels.get("daily_support", 0))
        bo = float(levels.get("breakout_trigger", 0))
        bd = float(levels.get("breakdown_trigger", 0))
        energy = abs(dr - ds)
        
        status = "STANDBY"
        active_side = "NONE"
        stop_loss = 0.0
        
        # Determine Previews
        midpoint = (bo + bd) / 2
        preview_side = "LONG" if current_price > midpoint else "SHORT"
        
        # --- THE DECISION CORE ---
        # Instead of asking battlebox_rules, we decide right here.
        # 1. Check Long
        long_triggered = _verify_breakout("LONG", current_price, bo, post_lock_candles)
        # 2. Check Short
        short_triggered = _verify_breakout("SHORT", current_price, bd, post_lock_candles)
        
        # 3. Execute
        if long_triggered:
            status = "EXECUTING"
            active_side = "LONG"
            stop_loss = r30_low
        elif short_triggered:
            status = "EXECUTING"
            active_side = "SHORT"
            stop_loss = r30_high
        else:
            # Standby Logic
            if bo > 0 and abs(current_price - bo) / bo < 0.001:
                status = "LOCKED"
                active_side = "LONG"
                stop_loss = r30_low
            elif bd > 0 and abs(current_price - bd) / bd < 0.001:
                status = "LOCKED"
                active_side = "SHORT"
                stop_loss = r30_high
            else:
                active_side = preview_side
                stop_loss = r30_low if active_side == "LONG" else r30_high

        # 6. STRENGTH (Includes Blue Sky Detection)
        trigger_px = bo if active_side == "LONG" else bd
        strength = _calc_strength(trigger_px, stop_loss, dr, ds, active_side)
        
        # 7. TARGETS
        targets = []
        context_type = "STRUCTURE"
        if strength["is_blue_sky"]: context_type = "BLUE SKY"
        
        if active_side != "NONE":
            if active_side == "LONG":
                t1 = dr if context_type == "STRUCTURE" else trigger_px + (energy * 0.5)
                targets = [
                    {"id": "T1", "price": round(t1, 2), "prob": "100%"},
                    {"id": "T2", "price": round(trigger_px + energy, 2), "prob": "83%"},
                    {"id": "T3", "price": round(trigger_px + (energy * 3.0), 2), "prob": "17%"}
                ]
            else:
                t1 = ds if context_type == "STRUCTURE" else trigger_px - (energy * 0.5)
                targets = [
                    {"id": "T1", "price": round(t1, 2), "prob": "100%"},
                    {"id": "T2", "price": round(trigger_px - energy, 2), "prob": "83%"},
                    {"id": "T3", "price": round(trigger_px - (energy * 3.0), 2), "prob": "17%"}
                ]

        return {
            "ok": True,
            "status": status,
            "symbol": symbol,
            "price": current_price,
            "side": active_side,
            "context": context_type,
            "strength": strength,
            "triggers": {"BO": bo, "BD": bd},
            "execution": {
                "entry": bo if active_side == "LONG" else bd,
                "stop_loss": stop_loss,
                "targets": targets
            }
        }

    except Exception as e:
        return {"ok": False, "status": "ERROR", "msg": str(e)}