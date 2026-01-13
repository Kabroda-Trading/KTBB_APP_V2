# black_ops_engine.py
# ==============================================================================
# PROJECT OMEGA: SPECIAL OPERATIONS LOGIC ENGINE (LIVE OPTIMIZED)
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

async def get_omega_status(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """
    Independent scanning engine for Project Omega. 
    OPTIMIZED: Uses fetch_live_5m for high-frequency polling.
    """
    try:
        # 1. RESOLVE ACTIVE SESSION ANCHOR
        now_utc = datetime.now(timezone.utc)
        
        # Use AUTO resolution to find the most relevant active session, or force NY
        # switching to "us_ny_futures" manually ensures consistent daily levels
        session_info = battlebox_pipeline.resolve_session(now_utc, mode="MANUAL", manual_id="us_ny_futures")
        
        anchor_ts = session_info["anchor_time"]
        lock_end_ts = anchor_ts + 1800 
        
        # 2. DATA ACQUISITION (FAST PATH)
        # Instead of pagination (slow), we pull the last 2000 candles from the live cache.
        # 2000 candles * 5m = 10,000 mins = ~6.9 days of data. Plenty for 24h lookback.
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
        
        # 24h Context Logic
        context_24h = _get_candles_in_range(raw_5m, lock_end_ts - 86400, lock_end_ts)
        if not context_24h: context_24h = raw_5m[:50] # Fallback

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
            if "error" in computed:
                return {"status": "ERROR", "msg": "SSE Calculation Failed"}
            
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
            elif bd > 0 and abs(current_price - bd) / bd < 0.001:
                status = "LOCKED"
                active_side = "SHORT"

        # 6. TARGETS
        targets = []
        context_type = "STRUCTURE"
        
        if active_side != "NONE":
            is_long = (active_side == "LONG")
            trigger_px = bo if is_long else bd
            
            if (is_long and trigger_px > dr) or (not is_long and trigger_px < ds):
                context_type = "BLUE SKY"
            
            if is_long:
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
            "triggers": {"BO": bo, "BD": bd},
            "execution": {
                "entry": bo if active_side == "LONG" else bd,
                "stop_loss": stop_loss,
                "targets": targets
            }
        }

    except Exception as e:
        # Keep minimal error logging to avoid flooding logs during high-frequency polling
        return {"ok": False, "status": "ERROR", "msg": str(e)}