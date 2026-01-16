# session_manager.py
# ==============================================================================
# CENTRAL SESSION AUTHORITY (Single Source of Truth)
# - Computes anchor/open timestamp for a session
# - Handles "if session hasn't opened yet today, use yesterday's open"
# - Exposes helpers used by battlebox_pipeline + research/review pages
# ==============================================================================

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, List
import pytz

SESSION_CONFIGS: List[Dict[str, Any]] = [
    {"id": "us_ny_futures", "name": "NY Futures", "tz": "America/New_York", "open_h": 8, "open_m": 30, "duration": 480},
    {"id": "us_ny_equity",  "name": "NY Equity",  "tz": "America/New_York", "open_h": 9, "open_m": 30, "duration": 390},
    {"id": "eu_london",     "name": "London",     "tz": "Europe/London",     "open_h": 8, "open_m": 0,  "duration": 480},
    {"id": "asia_tokyo",    "name": "Tokyo",      "tz": "Asia/Tokyo",        "open_h": 9, "open_m": 0,  "duration": 360},
]

def get_session_config(session_id: str) -> Dict[str, Any]:
    return next((s for s in SESSION_CONFIGS if s["id"] == session_id), SESSION_CONFIGS[0])

def resolve_anchor_time(session_id: str = "us_ny_futures") -> Dict[str, Any]:
    """
    Single source of truth:
    - Returns anchor_ts (UTC seconds) for the most recent session open.
    - If now is before today's open time, returns yesterday's open.
    """
    cfg = get_session_config(session_id)
    tz = pytz.timezone(cfg["tz"])

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)

    target_open_local = now_local.replace(
        hour=int(cfg["open_h"]),
        minute=int(cfg["open_m"]),
        second=0,
        microsecond=0,
    )

    if now_local < target_open_local:
        target_open_local -= timedelta(days=1)

    anchor_ts = int(target_open_local.astimezone(timezone.utc).timestamp())

    minutes_since_open = (now_local - target_open_local).total_seconds() / 60.0

    # Status ("energy") buckets
    status = "ACTIVE"
    if minutes_since_open < 30:
        status = "CALIBRATING"
    elif minutes_since_open > float(cfg["duration"]):
        status = "CLOSED"

    return {
        "anchor_ts": anchor_ts,
        "lock_end_ts": anchor_ts + 1800,  # 30m lock window
        "status": status,
        "minutes_elapsed": minutes_since_open,
        "session_name": cfg["name"],
        "session_id": cfg["id"],
        "tz": cfg["tz"],
    }

def anchor_ts_for_utc_date(cfg: Dict[str, Any], utc_date: datetime) -> int:
    """
    Backward-compatible helper used by session reviews / research lab.
    Given a UTC datetime, compute that date's open for the cfg.
    """
    tz = pytz.timezone(cfg["tz"])
    local_dt = utc_date.astimezone(tz)
    target = local_dt.replace(
        hour=int(cfg["open_h"]),
        minute=int(cfg["open_m"]),
        second=0,
        microsecond=0,
    )
    return int(target.astimezone(timezone.utc).timestamp())

def resolve_current_session(now_utc: datetime, mode: str = "AUTO", manual_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Used by battlebox_pipeline (live).
    Returns a strict object the pipeline expects.
    """
    if mode == "MANUAL" and manual_id:
        cfg = get_session_config(manual_id)
    else:
        cfg = SESSION_CONFIGS[0]  # AUTO defaults to NY Futures

    omega = resolve_anchor_time(cfg["id"])

    return {
        "id": cfg["id"],
        "name": cfg["name"],
        "anchor_time": omega["anchor_ts"],   # UTC seconds
        "date_key": datetime.fromtimestamp(omega["anchor_ts"], tz=timezone.utc).strftime("%Y-%m-%d"),
        "energy": omega["status"],
    }
