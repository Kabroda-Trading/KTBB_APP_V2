# session_manager.py
# ==============================================================================
# PROJECT OMEGA: CENTRAL SESSION AUTHORITY (Unified)
# RESPONSIBILITY: Single Source of Truth for Time & Anchors
# ==============================================================================
from datetime import datetime, timedelta, timezone
import pytz

# MASTER CONFIGURATION
SESSION_CONFIGS = [
    {
        "id": "us_ny_futures",
        "name": "NY Futures",
        "tz": "America/New_York",
        "open_h": 8, "open_m": 30,
        "duration": 480
    },
    {
        "id": "us_ny_equity",
        "name": "NY Equity",
        "tz": "America/New_York",
        "open_h": 9, "open_m": 30,
        "duration": 390
    },
    {
        "id": "eu_london",
        "name": "London",
        "tz": "Europe/London",
        "open_h": 8, "open_m": 0,
        "duration": 480
    },
    {
        "id": "asia_tokyo",
        "name": "Tokyo",
        "tz": "Asia/Tokyo",
        "open_h": 9, "open_m": 0,
        "duration": 360
    }
]

def get_session_config(session_id: str):
    # Search list for ID, default to first (NY Futures)
    return next((s for s in SESSION_CONFIGS if s["id"] == session_id), SESSION_CONFIGS[0])

# --- OMEGA LOGIC (New System) ---
def resolve_anchor_time(session_id: str = "us_ny_futures") -> dict:
    """
    THE SINGLE SOURCE OF TRUTH for OMEGA.
    Calculates the correct 'Anchor Time' (Open) for the given session relative to NOW.
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

# --- PIPELINE LOGIC (Restored for Backward Compatibility) ---
def anchor_ts_for_utc_date(cfg: dict, utc_date: datetime) -> int:
    """Restored helper for Research Lab and Reviews."""
    tz = pytz.timezone(cfg["tz"])
    local_dt = utc_date.astimezone(tz)
    target = local_dt.replace(hour=cfg["open_h"], minute=cfg["open_m"], second=0, microsecond=0)
    return int(target.astimezone(timezone.utc).timestamp())

def resolve_current_session(now_utc: datetime, mode: str = "AUTO", manual_id: str = None) -> dict:
    """Restored helper for Battle Control Dashboard."""
    # 1. Select Config
    if mode == "MANUAL" and manual_id:
        cfg = get_session_config(manual_id)
    else:
        # Default to NY Futures for AUTO for now
        cfg = SESSION_CONFIGS[0]

    # 2. Use the robust Omega logic to get the time
    omega_data = resolve_anchor_time(cfg["id"])
    
    # 3. Format strictly for BattleBox Pipeline expectations
    return {
        "id": cfg["id"],
        "name": cfg["name"],
        "anchor_time": omega_data["anchor_ts"],
        "date_key": datetime.fromtimestamp(omega_data["anchor_ts"], tz=timezone.utc).strftime("%Y-%m-%d"),
        "energy": omega_data["status"]
    }