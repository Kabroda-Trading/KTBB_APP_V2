# session_manager.py
# ==============================================================================
# KABRODA SESSION AUTHORITY (SINGLE SOURCE OF TRUTH)
# ==============================================================================
from datetime import datetime, timedelta, timezone
import pytz

# --- THE OFFICIAL CONFIGURATION ---
SESSION_CONFIGS = [
    {"id": "us_ny_futures", "name": "NY Futures", "tz": "America/New_York", "open_h": 8, "open_m": 30},
    {"id": "us_ny_equity",  "name": "NY Equity",  "tz": "America/New_York", "open_h": 9, "open_m": 30},
    {"id": "eu_london",     "name": "London",     "tz": "Europe/London",    "open_h": 8, "open_m": 0},
    {"id": "asia_tokyo",    "name": "Tokyo",      "tz": "Asia/Tokyo",       "open_h": 9, "open_m": 0},
    {"id": "au_sydney",     "name": "Sydney",     "tz": "Australia/Sydney", "open_h": 10, "open_m": 0},
]

def get_session_config(session_id: str):
    """Returns the config dict for a specific session ID."""
    for cfg in SESSION_CONFIGS:
        if cfg["id"] == session_id:
            return cfg
    return SESSION_CONFIGS[0]

def anchor_ts_for_utc_date(cfg: dict, utc_date: datetime) -> int:
    tz = pytz.timezone(cfg["tz"])
    y, m, d = utc_date.year, utc_date.month, utc_date.day
    local_open_naive = datetime(y, m, d, cfg["open_h"], cfg["open_m"], 0)
    local_open = tz.localize(local_open_naive)
    return int(local_open.astimezone(timezone.utc).timestamp())

def resolve_current_session(now_utc: datetime, mode: str = "AUTO", manual_id: str = None) -> dict:
    target_id = "us_ny_futures"
    if mode == "MANUAL" and manual_id:
        target_id = manual_id
        
    cfg = get_session_config(target_id)
    tz = pytz.timezone(cfg["tz"])
    
    now_local = now_utc.astimezone(tz)
    open_time = now_local.replace(hour=cfg["open_h"], minute=cfg["open_m"], second=0, microsecond=0)
    
    if now_local < open_time:
        open_time -= timedelta(days=1)
        
    anchor_ts = int(open_time.astimezone(timezone.utc).timestamp())
    elapsed_min = (now_local - open_time).total_seconds() / 60.0
    
    energy_state = "DEAD"
    if elapsed_min < 30: energy_state = "CALIBRATING"
    elif elapsed_min < 240: energy_state = "PRIME"
    elif elapsed_min < 420: energy_state = "LATE"
    
    return {
        "id": cfg["id"],
        "name": cfg["name"],
        "tz": cfg["tz"],
        "anchor_time": anchor_ts,
        "date_key": open_time.strftime("%Y-%m-%d"),
        "energy": energy_state,
        "elapsed_min": elapsed_min
    }