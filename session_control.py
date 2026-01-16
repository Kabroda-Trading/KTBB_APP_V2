# session_control.py
# KABRODA SESSION CONTROL ENGINE — Unified Live Session Feed

from fastapi import APIRouter
from session_context import get_session_context
from sse_engine import generate_levels

router = APIRouter()


@router.get("/session-control/{session_id}")
def session_control_status(session_id: str = "us_ny_futures"):
    try:
        # Step 1: Load session context from source of truth
        context = get_session_context(session_id)

        # Step 2: Compute breakout/breakdown/daily levels
        levels = generate_levels(context["calibration_candles"], context)

        # Step 3: Structure payload
        return {
            "ok": True,
            "session_id": session_id,
            "status": context["status"],
            "price": context["price"],
            "next_event_ts": context["lock_end_ts"],
            "levels": {
                "breakout_trigger": levels.get("breakout_trigger"),
                "breakdown_trigger": levels.get("breakdown_trigger"),
                "daily_resistance": levels.get("daily_resistance"),
                "daily_support": levels.get("daily_support")
            },
            "r30_window": {
                "r30_high": context["r30_high"],
                "r30_low": context["r30_low"]
            }
        }

    except Exception as e:
        return {
            "ok": False,
            "status": "ERROR",
            "msg": str(e)
        }
