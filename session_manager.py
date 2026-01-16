# session_manager.py
# ==============================================================================
# PROJECT OMEGA: CENTRAL SESSION AUTHORITY
# RESPONSIBILITY: Single Source of Truth for Time & Anchors
# ==============================================================================
from datetime import datetime, timedelta, timezone
import pytz

# MASTER CONFIGURATION
SESSIONS = {
    "us_ny_futures": {
        "name": "NY Futures",
        "tz": "America/New_York",
        "open_h": 8, "open_m": 30,
        "duration": 480  # 8 hours active
    },
    "us_ny_equity": {
        "name": "NY Equity",
        "tz": "America/New_York",
        "open_h": 9, "open_m": 30,
        "duration": 390
    },
    "eu_london": {
        "name": "London",
        "tz": "Europe/London",
        "open_h": 8, "open_m": 0,
        "duration": 480
    },
    "asia_tokyo": {
        "name": "Tokyo",
        "tz": "Asia/Tokyo",
        "open_h": 9, "open_m": 0,
        "duration": 360
    }
}

def get_session_config(session_id: str):
    return SESSIONS.get(session_id, SESSIONS["us_ny_futures"])

def resolve_anchor_time(session_id: str = "us_ny_futures") -> dict:
    """
    THE SINGLE SOURCE OF TRUTH.
    Calculates the correct 'Anchor Time' (Open) for the given session relative to NOW.
    If we are before the open, it rolls back to yesterday.
    If we are after the open, it locks to today.
    """
    cfg = get_session_config(session_id)
    tz = pytz.timezone(cfg["tz"])
    
    # Current time in Target Timezone
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)
    
    # Target Open Time for TODAY
    target_open = now_local.replace(hour=cfg["open_h"], minute=cfg["open_m"], second=0, microsecond=0)
    
    # LOGIC: If we are currently BEFORE the open time, the "Active Session" is actually YESTERDAY'S.
    if now_local < target_open:
        target_open -= timedelta(days=1)
        
    # Convert back to UTC Timestamp for the Engine
    anchor_ts = int(target_open.astimezone(timezone.utc).timestamp())
    
    # Calculate Status
    minutes_since_open = (now_local - target_open).total_seconds() / 60.0
    
    status = "ACTIVE"
    if minutes_since_open < 30:
        status = "CALIBRATING"
    elif minutes_since_open > cfg["duration"]:
        status = "CLOSED"
        
    return {
        "anchor_ts": anchor_ts,
        "lock_end_ts": anchor_ts + 1800,  # 30m Lock
        "status": status,
        "minutes_elapsed": minutes_since_open,
        "session_name": cfg["name"]
    }