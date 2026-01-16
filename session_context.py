# session_context.py

from datetime import datetime, timedelta
from typing import Dict, Any
from database import get_locked_candles, get_r30_levels, get_price
from session_manager import get_session_schedule

def get_session_context(session_id: str) -> Dict[str, Any]:
    """
    Builds the authoritative session data context for any KABRODA engine or UI panel.
    """

    schedule = get_session_schedule(session_id)
    now_utc = datetime.utcnow()

    context = {
        "session_id": session_id,
        "anchor_ts": schedule["start_ts"],               # When session opens
        "lock_end_ts": schedule["calibration_end_ts"],   # After calibration ends
        "status": _resolve_status(now_utc, schedule),
        "price": get_price(session_id),
        "r30_high": None,
        "r30_low": None,
        "calibration_candles": [],
        "context_24h": [],
    }

    # Load locked candles (used by sse_engine for levels)
    candles = get_locked_candles(session_id)
    context["calibration_candles"] = candles

    # Get r30 levels for session
    r30 = get_r30_levels(session_id)
    context["r30_high"] = r30.get("high")
    context["r30_low"] = r30.get("low")

    # Optionally, pull 24h context for structure scanning (future)
    # context["context_24h"] = get_context_window(session_id)

    return context


def _resolve_status(now: datetime, schedule: Dict[str, Any]) -> str:
    """
    Determines the current session phase.
    """
    if now < schedule["start_ts"]:
        return "PENDING"
    elif schedule["start_ts"] <= now < schedule["calibration_end_ts"]:
        return "CALIBRATING"
    elif schedule["calibration_end_ts"] <= now < schedule["close_ts"]:
        return "ACTIVE"
    else:
        return "CLOSED"
