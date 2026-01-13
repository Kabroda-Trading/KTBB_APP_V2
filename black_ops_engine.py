# black_ops_engine.py
# ==============================================================================
# PROJECT OMEGA: SPECIAL OPERATIONS LOGIC ENGINE
# CLASSIFICATION: TOP SECRET // ADMIN EYES ONLY
# ==============================================================================
from __future__ import annotations
from typing import Dict, Any, List
import asyncio
import battlebox_pipeline
import sse_engine
import battlebox_rules

# --- STRATEGY CONFIGURATION (HARDCODED WINNERS) ---
# Based on Oct 2025 "Blue Sky" Research
OMEGA_CONFIG = {
    "ignore_15m_alignment": True,
    "ignore_5m_stoch": True,
    "require_volume": True,
    "require_divergence": False,
    "confirmation_mode": "1_CLOSE", # Standard 1-Candle Close
    "zone_tolerance_bps": 10
}

async def get_omega_status(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """
    Runs the specific 'Omega' strategy scan on live data.
    Returns a simplified 'Pilot Briefing' for the dashboard.
    """
    try:
        # 1. Fetch Live Data (Fast Fetch)
        # We need enough history for SSE levels + 30m stop calculation
        raw_5m = await battlebox_pipeline.fetch_recent_candles(symbol, limit=288) # Last 24h
        if not raw_5m: return {"status": "OFFLINE", "error": "No Data"}

        # 2. Compute Levels (Standard SSE)
        # We assume NY Futures session logic for the 'Brain'
        # In a real engine, we might detect the active session automatically
        # For now, we calculate levels based on the standard pipeline logic
        current_price = raw_5m[-1]["close"]
        
        # We run a quick SSE computation on the recent slice
        # This mirrors the research_lab logic but optimized for single-pass
        sse_input = {
            "locked_history_5m": raw_5m[:-1], # Historical context
            "slice_24h_5m": raw_5m[-288:],    # 24h window
            "session_open_price": raw_5m[-1]["open"], # Placeholder, refines in real session
            "r30_high": max(c["high"] for c in raw_5m[-6:]),
            "r30_low": min(c["low"] for c in raw_5m[-6:]),
            "last_price": current_price,
            "tuning": OMEGA_CONFIG
        }
        
        computed = sse_engine.compute_sse_levels(sse_input)
        if "error" in computed: return {"status": "ERROR", "msg": "Level Calc Fail"}
        
        levels = computed["levels"]
        
        # 3. Detect Signal (The "GO" Logic)
        # We use a simulated 'post_accept' window of just the last few candles to see if we JUST triggered
        recent_window = raw_5m[-3:] 
        
        # We assume 'LONG' permission if price is generally above Open, 'SHORT' if below
        # For Omega, we are aggressive: check BOTH sides
        
        # Check LONG
        go_long = battlebox_rules.detect_pullback_go(
            side="LONG", levels=levels, post_accept_5m=recent_window, stoch_15m_at_accept={}, 
            use_zone="TRIGGER", **OMEGA_CONFIG
        )
        
        # Check SHORT
        go_short = battlebox_rules.detect_pullback_go(
            side="SHORT", levels=levels, post_accept_5m=recent_window, stoch_15m_at_accept={},
            use_zone="TRIGGER", **OMEGA_CONFIG
        )

        # 4. Analyze Context (Structure vs Blue Sky)
        # Calculate Energy
        dr = levels.get("daily_resistance", 0)
        ds = levels.get("daily_support", 0)
        bo = levels.get("breakout_trigger", 0)
        bd = levels.get("breakdown_trigger", 0)
        energy = abs(dr - ds)
        
        # Determine Status
        status = "STANDBY"
        active_side = "NONE"
        stop_loss = 0.0
        
        if go_long["ok"]:
            status = "EXECUTING"
            active_side = "LONG"
            stop_loss = min(c["low"] for c in raw_5m[-6:]) # Lowest low of last 30m
        elif go_short["ok"]:
            status = "EXECUTING"
            active_side = "SHORT"
            stop_loss = max(c["high"] for c in raw_5m[-6:]) # Highest high of last 30m
        else:
            # Check "NEAR" status (Yellow Alert)
            # If price is within 0.2% of a trigger, we are "LOCKED"
            pct_dist_long = abs(current_price - bo) / bo
            pct_dist_short = abs(current_price - bd) / bd
            
            if pct_dist_long < 0.002:
                status = "LOCKED"
                active_side = "LONG"
            elif pct_dist_short < 0.002:
                status = "LOCKED"
                active_side = "SHORT"

        # 5. Build Targets (Smart Structure)
        targets = []
        context_type = "STRUCTURE"
        
        if active_side == "LONG":
            # Blue Sky Check
            if bo > dr: context_type = "BLUE SKY"
            
            # Target Math
            t1 = dr if context_type == "STRUCTURE" else bo + (energy * 0.5)
            t2 = bo + energy
            t3 = bo + (energy * 3.0)
            
            targets = [
                {"id": "T1", "price": t1, "prob": "100%"},
                {"id": "T2", "price": t2, "prob": "83%"},
                {"id": "T3", "price": t3, "prob": "17% (Moon)"}
            ]
            
        elif active_side == "SHORT":
            if bd < ds: context_type = "BLUE SKY"
            
            t1 = ds if context_type == "STRUCTURE" else bd - (energy * 0.5)
            t2 = bd - energy
            t3 = bd - (energy * 3.0)
            
            targets = [
                {"id": "T1", "price": t1, "prob": "100%"},
                {"id": "T2", "price": t2, "prob": "83%"},
                {"id": "T3", "price": t3, "prob": "17% (Moon)"}
            ]

        # 6. Payload Construction
        return {
            "status": status, # STANDBY, LOCKED, EXECUTING
            "symbol": symbol,
            "price": current_price,
            "side": active_side,
            "context": context_type,
            "triggers": {
                "BO": bo,
                "BD": bd
            },
            "execution": {
                "entry": bo if active_side == "LONG" else bd,
                "stop_loss": stop_loss,
                "targets": targets
            },
            "timestamp": battlebox_pipeline.now_utc_ts()
        }

    except Exception as e:
        print(f"OMEGA ENGINE ERROR: {e}")
        return {"status": "ERROR", "msg": str(e)}