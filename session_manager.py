# session_manager.py
# ==============================================================================
# KABRODA SESSION MANAGER (SOURCE OF TRUTH)
# ==============================================================================
# Purpose:
# - Defines the exact start times for all global sessions.
# - Calculates the specific "Anchor Timestamp" (Open Time) for any given session.
# - Handles Timezone conversions (JST, GMT, ET, AEDT).
# ==============================================================================

from datetime import datetime, timedelta, timezone
import pytz  # Requires: pip install pytz

# --- 1. SESSION DEFINITIONS (The "Map") ---
# Timestamps are calculated dynamically based on these rules.
SESSION_CONFIGS = [
    {"id": "us_ny_futures", "name": "NY FUTURES", "tz": "America/New_York", "open_h": 8, "open_m": 30},
    {"id": "us_ny_equity",  "name": "NY EQUITY",  "tz": "America/New_York", "open_h": 9, "open_m": 30},
    {"id": "eu_london",     "name": "LONDON",     "tz": "Europe/London",    "open_h": 8, "open_m": 0},
    {"id": "asia_tokyo",    "name": "TOKYO",      "tz": "Asia/Tokyo",       "open_h": 9, "open_m": 0},
    {"id": "au_sydney",     "name": "SYDNEY",     "tz": "Australia/Sydney", "open_h": 10, "open_m": 0},
    {"id": "utc_default",   "name": "UTC CRYPTO", "tz": "UTC",              "open_h": 0, "open_m": 0},
]

def get_session_config(session_id: str):
    """Finds the config dictionary for a specific ID."""
    for s in SESSION_CONFIGS:
        if s["id"] == session_id:
            return s
    return SESSION_CONFIGS[0] # Default to NY Futures if not found

# --- 2. TIME CALCULATOR (The "Anchor") ---
def anchor_ts_for_utc_date(config: dict, now_utc: datetime) -> int:
    """
    Calculates the exact UNIX timestamp for the Session Open (Anchor) 
    that belongs to the current moment.
    """
    tz = pytz.timezone(config["tz"])
    
    # Convert "Now" to the target timezone (e.g., JST)
    now_local = now_utc.astimezone(tz)
    
    # Create a target time for "Today's Open" in that timezone
    target_open = now_local.replace(hour=config["open_h"], minute=config["open_m"], second=0, microsecond=0)
    
    # Logic: If "Now" is BEFORE the open, we are technically looking at 
    # the session that started Yesterday.
    # Example: It's 8:00 AM Tokyo. Open is 9:00 AM. 
    # We want the session from yesterday, not the one that hasn't started yet.
    if now_local < target_open:
        target_open -= timedelta(days=1)
        
    # Convert back to UTC timestamp
    return int(target_open.timestamp())

# --- 3. PUBLIC RESOLVER (The "Handshake") ---
def resolve_current_session(now_utc: datetime, mode: str = "AUTO", manual_id: str = None) -> dict:
    """
    Returns the complete Session Packet with the calculated Anchor Time.
    This is what the Pipeline consumes.
    """
    # 1. Determine which config to use
    if mode == "MANUAL" and manual_id:
        config = get_session_config(manual_id)
    else:
        # AUTO logic (Simplified for stability: Defaults to NY Futures or active logic)
        # You can expand this later to auto-detect "Which session is active right now?"
        config = get_session_config("us_ny_futures")

    # 2. Calculate the Anchor Time (CRITICAL STEP)
    # This ensures "Tokyo" gets a Tokyo timestamp, not a NY one.
    anchor_ts = anchor_ts_for_utc_date(config, now_utc)
    
    # 3. Return the Packet
    return {
        "id": config["id"],
        "name": config["name"],
        "date_key": datetime.fromtimestamp(anchor_ts, timezone.utc).strftime("%Y-%m-%d"),
        "anchor_time": anchor_ts, # <--- The key that was missing!
        "status": "ACTIVE",       # Placeholder, pipeline determines actual status based on time diff
        "energy": "ACTIVE"
    }

# --- 4. BACKWARD COMPATIBILITY (Safety) ---
# If other files call 'resolve_anchor_time', we map it to the new logic.
def resolve_anchor_time(session_id: str) -> dict:
    now = datetime.now(timezone.utc)
    pkt = resolve_current_session(now, mode="MANUAL", manual_id=session_id)
    # Map to old format if needed by older files
    return {
        "anchor_ts": pkt["anchor_time"],
        "lock_end_ts": pkt["anchor_time"] + 1800,
        "status": "ACTIVE"
    }