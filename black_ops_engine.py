# black_ops_engine.py
# ==============================================================================
# PROJECT OMEGA: SPECIAL OPERATIONS LOGIC ENGINE (PRESERVATION VERSION)
# CLASSIFICATION: TOP SECRET // ADMIN EYES ONLY
# ==============================================================================
from __future__ import annotations
from typing import Dict, Any, List, Optional
import asyncio
import time
import traceback

# CORE SUITE INTEGRATION
import battlebox_pipeline
import sse_engine
import battlebox_rules

# --- OMEGA SPECIAL OPS CONFIGURATION ---
# Hardcoded to the high-volatility parameters validated in 2025 Research
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

async def get_omega_status(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """
    Independent scanning engine for Project Omega. 
    Uses stable pipeline functions to protect main site integrity.
    """
    try:
        # 1. TIMING & WINDOW ESTABLISHMENT
        # We calculate the 24h window independently to avoid Pipeline side-effects
        now_ts = int(time.time())
        start_ts = now_ts - 86400 # 24 Hour Lookback
        
        # 2. DATA ACQUISITION (STABLE PATH)
        # Using the exact function name found in your main.py
        raw_5m = await battlebox_pipeline.fetch_historical_pagination(
            symbol=symbol, 
            start_ts=start_ts, 
            end_ts=now_ts
        )

        # Safety Check: Ensure we have enough data to build structure
        if not raw_5m or len(raw_5m) < 12:
            print(f"[OMEGA] Data Gap Detected for {symbol}")
            return {"status": "OFFLINE", "msg": "Waiting for Pipeline Data..."}

        # 3. LEVEL COMPUTATION (SSE ENGINE)
        # We strictly convert all values to floats to prevent math errors
        try:
            current_price = float(raw_5m[-1]["close"])
            
            # Establish the 30-minute 'Opening Range' proxy for live scanning
            # This looks at the most recent 6 candles (30 mins)
            r30_slice = raw_5m[-6:]
            r30_high = max(float(c["high"]) for c in r30_slice)
            r30_low = min(float(c["low"]) for c in r30_slice)
            
            sse_input = {
                "locked_history_5m": raw_5m,
                "slice_24h_5m": raw_5m,
                "session_open_price": float(raw_5m[0]["open"]),
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
            print(f"[OMEGA] SSE Input Error: {e}")
            return {"status": "ERROR", "msg": "Level Logic Failure"}

        # 4. SIGNAL SCANNING (BATTLEBOX RULES)
        # We check the very last candle for a fresh 15m trigger
        # Passing empty dict for 15m stoch as OMEGA_CONFIG ignores it
        recent_window = raw_5m[-3:]
        
        go_long = battlebox_rules.detect_pullback_go(
            side="LONG", 
            levels=levels, 
            post_accept_5m=recent_window, 
            stoch_15m_at_accept={}, 
            use_zone="TRIGGER", 
            **OMEGA_CONFIG
        )
        
        go_short = battlebox_rules.detect_pullback_go(
            side="SHORT", 
            levels=levels, 
            post_accept_5m=recent_window, 
            stoch_15m_at_accept={},
            use_zone="TRIGGER", 
            **OMEGA_CONFIG
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
            # PROXIMITY CHECK (LOCKED state if within 0.1% of trigger)
            if bo > 0 and abs(current_price - bo) / bo < 0.001:
                status = "LOCKED"
                active_side = "LONG"
            elif bd > 0 and abs(current_price - bd) / bd < 0.001:
                status = "LOCKED"
                active_side = "SHORT"

        # 6. TARGET GENERATION (PROBABILITY MAPPING)
        targets = []
        context_type = "STRUCTURE"
        
        if active_side != "NONE":
            is_long = (active_side == "LONG")
            trigger_px = bo if is_long else bd
            
            # BLUE SKY CHECK
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

        # 7. FINAL PAYLOAD
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
        # Full Traceback to Render Logs - Don't hide errors
        print("!!! OMEGA ENGINE CRITICAL FAILURE !!!")
        traceback.print_exc()
        return {"ok": False, "status": "ERROR", "msg": str(e)}