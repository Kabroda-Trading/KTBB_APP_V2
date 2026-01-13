# black_ops_engine.py
# ==============================================================================
# PROJECT OMEGA: SPECIAL OPERATIONS LOGIC ENGINE (STRENGTH ENABLED)
# CLASSIFICATION: TOP SECRET // ADMIN EYES ONLY
# ==============================================================================
from __future__ import annotations
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import traceback

# CORE SUITE INTEGRATION
import battlebox_pipeline
import sse_engine
import battlebox_rules

# --- OMEGA SPECIAL OPS CONFIGURATION ---
OMEGA_CONFIG = {
    "ignore_15m_alignment": True,
    "ignore_5m_stoch": True,
    "require_volume": True,
    "require_divergence": False,
    "fusion_mode": False,
    "confirmation_mode": "1_CLOSE",
    "zone_tolerance_bps": 10,
    "min_trigger_dist_bps": 20,
    "acceptance_closes": 2
}

def _get_candles_in_range(all_candles: List[Dict], start_ts: int, end_ts: int) -> List[Dict]:
    return [c for c in all_candles if start_ts <= c["time"] < end_ts]

def _calc_strength(entry: float, stop: float, dr: float, ds: float, side: str) -> dict:
    """
    Calculates Trade Strength based on 2025 'Home Run' DNA.
    Returns a score (0-100) and a rating.
    """
    score = 0
    reasons = []
    
    if entry == 0 or stop == 0: return {"score": 0, "rating": "WAITING", "tags": []}

    # 1. CONTEXT (40 Pts) - Blue Sky is King
    if side == "LONG":
        if entry > dr:
            score += 40
            reasons.append("BLUE SKY")
        else:
            score += 10
            reasons.append("IN STRUCTURE")
    elif side == "SHORT":
        if entry < ds:
            score += 40
            reasons.append("BLUE SKY")
        else:
            score += 10
            reasons.append("IN STRUCTURE")
            
    # 2. COMPRESSION (40 Pts) - Tight Stops = Big Pops
    # Risk % of asset price (e.g. 500 / 90000 = 0.0055)
    if entry > 0:
        risk_pct = abs(entry - stop) / entry
        
        if risk_pct < 0.006: # < 0.6% Risk (Super tight)
            score += 40
            reasons.append("SUPER COIL")
        elif risk_pct < 0.01: # < 1.0% Risk (Standard)
            score += 20
            reasons.append("NORMAL RISK")
        else:
            reasons.append("WIDE STOP")

    # 3. BASELINE (20 Pts) - The system works, so give it credit
    score += 20 
    
    # RATING
    if score >= 80: rating = "HOME RUN (AIM T2)"
    elif score >= 50: rating = "BASE HIT (TAKE T1)"
    else: rating = "WEAK"
    
    return {"score": score, "rating": rating, "tags": reasons}

async def get_omega_status(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """
    Independent scanning engine for Project Omega. 
    Uses fast live fetch + session locking + strength calculation.
    """
    try:
        # 1. RESOLVE ACTIVE SESSION ANCHOR (LOCKING)
        now_utc = datetime.now(timezone.utc)
        # We manually force NY Futures to ensure the daily levels are stable
        session_info = battlebox_pipeline.resolve_session(now_utc, mode="MANUAL", manual_id="us_ny_futures")
        
        anchor_ts = session_info["anchor_time"]
        lock_end_ts = anchor_ts + 1800 
        
        # 2. DATA ACQUISITION (FAST PATH)
        # Pulling 2000 candles from live cache to avoid database timeout
        raw_5m = await battlebox_pipeline.fetch_live_5m(symbol, limit=2000)

        if not raw_5m or len(raw_5m) < 12:
            return {"status": "OFFLINE", "msg": "Waiting for Data Stream..."}

        current_price = float(raw_5m[-1]["close"])

        # 3. LEVEL COMPUTATION (LOCKED)
        calibration_candles = _get_candles_in_range(raw_5m, anchor_ts, lock_end_ts)
        
        if len(calibration_candles) < 1:
             return {"status": "STANDBY", "msg": "Building Opening Range..."}
             
        # LOCKING THE R30
        r30_high = max(float(c["high"]) for c in calibration_candles)
        r30_low = min(float(c["low"]) for c in calibration_candles)
        
        # 24h Context (Lookback from the lock time)
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
                "tuning": OMEGA_CONFIG
            }
            computed = sse_engine.compute_sse_levels(sse_input)
            levels = computed.get("levels", {})
        except Exception as e:
            return {"status": "ERROR", "msg": f"Level Logic: {str(e)}"}

        # 4. SIGNAL SCANNING
        post_lock_candles = [c for c in raw_5m if c["time"] >= lock_end_ts]
        if not post_lock_candles:
             return {"status": "STANDBY", "msg": "Calibrating Session..."}

        recent_window = post_lock_candles[-3:]
        
        go_long = battlebox_rules.detect_pullback_go(
            side="LONG", levels=levels, post_accept_5m=recent_window, stoch_15m_at_accept={}, 
            use_zone="TRIGGER", **OMEGA_CONFIG
        )
        
        go_short = battlebox_rules.detect_pullback_go(
            side="SHORT", levels=levels, post_accept_5m=recent_window, stoch_15m_at_accept={},
            use_zone="TRIGGER", **OMEGA_CONFIG
        )

        # 5. STATE DETERMINATION
        dr = float(levels.get("daily_resistance", 0))
        ds = float(levels.get("daily_support", 0))
        bo = float(levels.get("breakout_trigger", 0))
        bd = float(levels.get("breakdown_trigger", 0))
        energy = abs(dr - ds)
        
        status = "STANDBY"
        active_side = "NONE"
        stop_loss = 0.0
        
        # Determine likely side for Strength Meter even if not triggered yet
        # If price is closer to BO, we check LONG strength. Closer to BD, check SHORT.
        midpoint = (bo + bd) / 2
        preview_side = "LONG" if current_price > midpoint else "SHORT"
        
        if go_long.get("ok"):
            status = "EXECUTING"
            active_side = "LONG"
            stop_loss = r30_low
        elif go_short.get("ok"):
            status = "EXECUTING"
            active_side = "SHORT"
            stop_loss = r30_high
        else:
            if bo > 0 and abs(current_price - bo) / bo < 0.001:
                status = "LOCKED"
                active_side = "LONG"
                stop_loss = r30_low
            elif bd > 0 and abs(current_price - bd) / bd < 0.001:
                status = "LOCKED"
                active_side = "SHORT"
                stop_loss = r30_high
            else:
                # Preview mode
                active_side = preview_side
                stop_loss = r30_low if active_side == "LONG" else r30_high

        # 6. STRENGTH CALCULATION
        # Calculate strength for either the active trade OR the preview trade
        trigger_px = bo if active_side == "LONG" else bd
        strength = _calc_strength(trigger_px, stop_loss, dr, ds, active_side)

        # 7. TARGETS
        targets = []
        context_type = "STRUCTURE"
        
        if active_side != "NONE":
            if (active_side == "LONG" and trigger_px > dr) or (active_side == "SHORT" and trigger_px < ds):
                context_type = "BLUE SKY"
            
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