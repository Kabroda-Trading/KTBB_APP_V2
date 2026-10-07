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
    {"id": "us_ny_pm",      "name": "NY PM FUTURES", "tz": "America/New_York", "open_h": 13, "open_m": 0}, # NEW: PM Session
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
    if mode == "MANUAL" and manual_id:
        config = get_session_config(manual_id)
    else:
        config = get_session_config("us_ny_futures")

    anchor_ts = anchor_ts_for_utc_date(config, now_utc)
    
    return {
        "id": config["id"],
        "name": config["name"],
        "date_key": datetime.fromtimestamp(anchor_ts, timezone.utc).strftime("%Y-%m-%d"),
        "anchor_time": anchor_ts, 
        "status": "ACTIVE",       
        "energy": "ACTIVE"
    }

def resolve_anchor_time(session_id: str) -> dict:
    now = datetime.now(timezone.utc)
    pkt = resolve_current_session(now, mode="MANUAL", manual_id=session_id)
    return {
        "anchor_ts": pkt["anchor_time"],
        "lock_end_ts": pkt["anchor_time"] + 1800,
        "status": "ACTIVE"
    }


def next_lock_utc(now_utc: datetime, session_id: str = "us_ny_futures") -> datetime:
    """2026-10-06 (R1 re-arm, Andy ruling 15:24 CT): the next session lock
    (anchor + 30min calibration window) -- the boundary TRAVELER_D1_D2_D3_
    SPEC.md's own "REARM_WATCH... ends at next 13:00 UTC lock" wording
    describes. Deliberately NOT a hardcoded `datetime(..., 13, 0)` literal
    -- "13:00 UTC" is only true during EDT (America/New_York daylight
    saving); during EST it's 14:00 UTC, the exact class of DST bug this
    project already hit once this session (the "7:00->8:00 AM CT" slip,
    2026-09-29 AGENT_LOG.md both repos).

    2026-10-07 P0 fix (found while auditing a WAITING_CROSS session-
    expiration fix, not reported by anyone -- caught by actually running
    this function against the fall-back date, not just reading it): the
    original implementation shifted `now_utc` forward by a fixed 24 UTC
    hours and fed that into anchor_ts_for_utc_date(), reusing that
    function's own "if before today's local open, roll back one day"
    branch -- correct for ITS real job (resolve_current_session()'s "find
    the most recent past anchor"), but wrong here: on the US fall-back
    date, a fixed 24-UTC-hour shift lands at local 08:00 (before the 08:30
    open), wrongly firing the rollback and undoing the entire +1 day
    shift -- confirmed by direct reproduction: next_lock_utc(2026-10-31
    13:00 UTC) returned 2026-10-31 14:00 UTC (1 hour later) instead of the
    correct 2026-11-01 14:00 UTC (25 hours later). This already governed
    the shipped R1 re-arm's own REARM_WATCH deadline in production before
    this fix. Rewritten below as its own direct calendar-date computation
    (tz.localize() on tomorrow's own date, not borrowed "shift + rollback"
    logic) -- independently verified to match the old function exactly on
    every plain day and on spring-forward eve, and to give the correct
    23h/25h deltas on the two real transition days. Do not revert to
    reusing anchor_ts_for_utc_date() here -- that function must keep its
    current rollback behavior for resolve_current_session()'s different
    job; this one needs its own."""
    config = get_session_config(session_id)
    tz = pytz.timezone(config["tz"])
    now_local = now_utc.astimezone(tz)
    tomorrow_date = (now_local + timedelta(days=1)).date()
    target_open = tz.localize(datetime(
        tomorrow_date.year, tomorrow_date.month, tomorrow_date.day,
        config["open_h"], config["open_m"]))
    return (target_open + timedelta(seconds=1800)).astimezone(timezone.utc)