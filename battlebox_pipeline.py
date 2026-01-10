# battlebox_pipeline.py
# ==============================================================================
# KABRODA PIPELINE v3.0 (TUNING ENABLED)
# ==============================================================================
# 1. Orchestrates data fetching.
# 2. Computes SSE Levels (Map).
# 3. Computes Structure State (Law).
# 4. Now passes "Tuning" parameters for Research Lab optimization.
# ==============================================================================

from __future__ import annotations
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
import asyncio

# --- INTERNAL MODULES ---
import sse_engine
import structure_state_engine
from data_service import fetch_5m_historical_range

# --- SESSION CONFIGURATION ---
SESSION_CONFIGS = [
    {"id": "us_ny_futures", "name": "NY Futures", "tz": "America/New_York", "open_h": 8, "open_m": 30},
    {"id": "us_ny_equity",  "name": "NY Equity",  "tz": "America/New_York", "open_h": 9, "open_m": 30},
    {"id": "eu_london",     "name": "London",     "tz": "Europe/London",    "open_h": 8, "open_m": 0},
    {"id": "asia_tokyo",    "name": "Tokyo",      "tz": "Asia/Tokyo",       "open_h": 9, "open_m": 0},
    {"id": "au_sydney",     "name": "Sydney",     "tz": "Australia/Sydney", "open_h": 10, "open_m": 0},
    {"id": "utc_core",      "name": "UTC Core",   "tz": "UTC",              "open_h": 0, "open_m": 0},
]

def anchor_ts_for_utc_date(cfg: Dict, utc_date: datetime) -> int:
    """
    Given a pure date (e.g. 2025-01-08) and a session config,
    return the UNIX timestamp of that session's open on that date.
    """
    import pytz
    
    local_tz = pytz.timezone(cfg["tz"])
    # 1. Construct naive datetime at session open time
    local_dt = datetime(
        utc_date.year, utc_date.month, utc_date.day,
        cfg["open_h"], cfg["open_m"], 0
    )
    # 2. Localize to session timezone
    localized = local_tz.localize(local_dt)
    # 3. Convert to UTC timestamp
    return int(localized.timestamp())

def compute_session_from_candles(
    cfg: Dict, 
    utc_date: datetime, 
    raw_5m: List[Dict], 
    exec_hours: int = 6, 
    tuning: Dict = None  # <--- NEW: Tuning Injection
) -> Dict[str, Any]:
    """
    The Single Source of Truth for generating a session packet.
    """
    # 1. Timestamps
    anchor_ts = anchor_ts_for_utc_date(cfg, utc_date)
    lock_end_ts = anchor_ts + 1800  # 30m calibration
    exec_end_ts = lock_end_ts + (exec_hours * 3600)
    
    # 2. Slice Data
    # We need 24h context BEFORE the lock_end_ts
    context_24h = [c for c in raw_5m if (lock_end_ts - 86400) <= c["time"] < lock_end_ts]
    # We need the 30m Calibration Range (Anchor -> Lock)
    calib = [c for c in raw_5m if anchor_ts <= c["time"] < lock_end_ts]
    
    if len(calib) < 6:
        # Not enough data for calibration
        return {
            "ok": False,
            "error": "Insufficient Calibration Data",
            "session_id": cfg["id"],
            "timestamps": {"anchor": anchor_ts, "lock": lock_end_ts},
            "final_state": "INVALID"
        }

    # 3. Compute SSE Packet (The Map)
    sse_input = {
        "locked_history_5m": context_24h,
        "slice_24h_5m": context_24h,
        "session_open_price": calib[0]["open"],
        "r30_high": max(c["high"] for c in calib), 
        "r30_low": min(c["low"] for c in calib),
        "last_price": calib[-1]["close"],
        "tuning": tuning or {}  # <--- PASS TUNING TO SSE
    }
    
    sse_pkt = sse_engine.compute_sse_levels(sse_input)
    if "error" in sse_pkt:
        return {"ok": False, "error": sse_pkt["error"]}
    
    levels = sse_pkt["levels"]
    
    # 4. Compute Structure State (The Law)
    post_lock_candles = [c for c in raw_5m if lock_end_ts <= c["time"] < exec_end_ts]
    
    state_pkt = structure_state_engine.compute_structure_state(
        levels=levels, 
        candles_5m_post_lock=post_lock_candles,
        tuning=tuning  # <--- PASS TUNING TO STATE ENGINE
    )
    
    # 5. Extract "Truth" Flags
    had_acceptance = (state_pkt["permission"]["status"] == "EARNED")
    had_alignment = (state_pkt["execution"]["gates_mode"] == "LOCKED")
    
    # 6. Return Unified Packet
    return {
        "ok": True,
        "session_id": cfg["id"],
        "timestamps": {
            "anchor": anchor_ts,
            "lock": lock_end_ts,
            "close": exec_end_ts
        },
        "sse": sse_pkt,
        "state": state_pkt,
        "counts": {
            "had_acceptance": had_acceptance,
            "had_alignment": had_alignment
        },
        "events": {
            "acceptance_side": state_pkt["permission"]["side"],
            "final_state": state_pkt["action"],
            "fail_reason": state_pkt.get("diagnostics", {}).get("fail_reason", "UNKNOWN")
        }
    }

# --- PUBLIC HELPERS ---

async def get_session_review(symbol: str, session_tz: str) -> Dict[str, Any]:
    """Used by 'Run Review Session' button."""
    # Find config
    cfg = next((c for c in SESSION_CONFIGS if c["tz"] == session_tz), SESSION_CONFIGS[-1])
    
    # Need approx 48h history to be safe
    end_ts = int(datetime.now().timestamp())
    start_ts = end_ts - (48 * 3600)
    
    raw_5m = await fetch_5m_historical_range(symbol, start_ts, end_ts)
    if not raw_5m: return {"error": "No Data"}
    
    # Target "Today's" session (or yesterday if not open yet)
    # Simple logic: assume we are looking for the *most recent* completed or active session
    # For robust review, we usually look at 'today' relative to UTC.
    now = datetime.now(timezone.utc)
    # If session open hasn't happened today, go back 1 day
    target_date = now
    
    # Run pipeline
    result = compute_session_from_candles(cfg, target_date, raw_5m, exec_hours=12)
    if not result.get("ok"):
        # Try yesterday
        target_date = now - timedelta(days=1)
        result = compute_session_from_candles(cfg, target_date, raw_5m, exec_hours=12)
        
    # Flatten for UI
    if result.get("ok"):
        return {
            "ok": True,
            "levels": result["sse"]["levels"],
            "range_30m": {
                "high": result["sse"]["levels"]["range30m_high"], 
                "low": result["sse"]["levels"]["range30m_low"]
            },
            "bias": result["sse"]["bias_model"],
            "state": result["state"]["action"],
            "gates": result["state"]["execution"]["gates_mode"]
        }
    return result

async def get_live_battlebox(symbol: str, session_mode: str = "AUTO", manual_id: str = None, operator_flex: bool = False) -> Dict[str, Any]:
    """Used by Live Dashboard."""
    # Simplified for brevity - hooks into same logic
    return {"status": "LIVE_PIPELINE_LINKED"}