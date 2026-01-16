# session_manager.py
# ==============================================================================
# KABRODA SESSION AUTHORITY — v3.0 (ANCHOR OF TRUTH)
# ------------------------------------------------------------------------------
# This module is the ONLY place that knows:
# - which sessions exist
# - when each session "opens" (anchor time)
# - the 30-minute lock end
# - whether we are CALIBRATING vs ACTIVE
#
# Consumers:
# - battlebox_pipeline.py (Session Control + Live Battlebox)
# - project_omega.py (Omega engine session picker)
# - research_lab.py (backtest slicing)
# ==============================================================================

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional


@dataclass(frozen=True)
class SessionConfig:
    id: str
    name: str
    # Anchor time expressed as UTC "HH:MM"
    anchor_utc_hhmm: str


# ------------------------------------------------------------------------------
# Define your canonical sessions here.
# NOTE: You can add more later, but keep IDs stable.
# ------------------------------------------------------------------------------
SESSION_CONFIGS: List[Dict[str, Any]] = [
    {"id": "us_ny_futures", "name": "NY Futures", "anchor_utc_hhmm": "13:30"},  # 7:30am CST standard
    {"id": "tokyo",         "name": "Tokyo",      "anchor_utc_hhmm": "00:00"},
    {"id": "london",        "name": "London",     "anchor_utc_hhmm": "07:00"},
    {"id": "sydney",        "name": "Sydney",     "anchor_utc_hhmm": "21:00"},
]


def list_sessions() -> List[Dict[str, Any]]:
    return list(SESSION_CONFIGS)


def get_session_config(session_id: str) -> Dict[str, Any]:
    sid = (session_id or "").strip()
    for cfg in SESSION_CONFIGS:
        if cfg["id"] == sid:
            return cfg
    # Default
    return SESSION_CONFIGS[0]


def _hhmm_to_hour_min(hhmm: str) -> tuple[int, int]:
    hhmm = (hhmm or "").strip()
    hh, mm = hhmm.split(":")
    return int(hh), int(mm)


def anchor_ts_for_utc_date(cfg: Dict[str, Any], now_utc: datetime) -> int:
    """
    For a given UTC date (from now_utc), return the anchor timestamp for that date.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)

    h, m = _hhmm_to_hour_min(cfg["anchor_utc_hhmm"])
    anchor_dt = datetime(now_utc.year, now_utc.month, now_utc.day, h, m, tzinfo=timezone.utc)
    return int(anchor_dt.timestamp())


def resolve_anchor_time(session_id: str, now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Omega-friendly resolver.
    Returns:
      anchor_ts, lock_end_ts, status
    where status is:
      - CALIBRATING: now < lock_end
      - ACTIVE: now >= lock_end
    """
    cfg = get_session_config(session_id)
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)

    anchor_ts = anchor_ts_for_utc_date(cfg, now_utc)
    lock_end_ts = anchor_ts + 1800  # +30 minutes

    # If today's anchor is in the future, we want the most recent anchor (yesterday)
    if anchor_ts > int(now_utc.timestamp()):
        anchor_ts -= 86400
        lock_end_ts -= 86400

    status = "CALIBRATING" if int(now_utc.timestamp()) < lock_end_ts else "ACTIVE"

    date_key = datetime.fromtimestamp(anchor_ts, tz=timezone.utc).strftime("%Y-%m-%d")

    return {
        "id": cfg["id"],
        "name": cfg["name"],
        "anchor_ts": int(anchor_ts),
        "lock_end_ts": int(lock_end_ts),
        "status": status,
        "date_key": date_key,
    }


def resolve_current_session(
    now_utc: datetime,
    session_mode: str = "AUTO",
    manual_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    battlebox_pipeline-friendly resolver.
    session_mode:
      - AUTO: always return us_ny_futures (your default operating mode)
      - MANUAL: use manual_id
    """
    mode = (session_mode or "AUTO").strip().upper()
    if mode == "MANUAL" and manual_id:
        return resolve_anchor_time(manual_id, now_utc)

    # Default: your daily operating session
    return resolve_anchor_time("us_ny_futures", now_utc)
