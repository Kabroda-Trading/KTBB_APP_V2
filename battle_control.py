# battle_control.py
# KABRODA BATTLE CONTROL — Unified Tactical Readout

from fastapi import APIRouter
from session_context import get_session_context
from sse_engine import generate_levels
from strategy_logic import evaluate_trade

router = APIRouter()


@router.get("/battle-control/{session_id}")
def battle_control_status(session_id: str = "us_ny_futures"):
    try:
        # Step 1: Get locked session context
        context = get_session_context(session_id)

        # Step 2: Generate level triggers from shared logic
        levels = generate_levels(context["calibration_candles"], context)

        # Step 3: Strategy configuration (mirrors Black Ops)
        config = {
            "confirmation_mode": "1-Candle Close (Standard)",
            "acceptance_closes": 2,
            "ignore_alignment": True,
            "ignore_stoch": True,
            "stop_risk_bps": 120
        }

        # Step 4: Run tactical logic using strategy module
        directive = evaluate_trade(context, levels, config)

        # Step 5: Determine live price and location
        side = directive["active_side"]
        price = context["price"]
        location = "INSIDE_RANGE"

        if side == "LONG" and price > levels.get("breakout_trigger", float("inf")):
            location = "ABOVE_BREAKOUT"
        elif side == "SHORT" and price < levels.get("breakdown_trigger", float("-inf")):
            location = "BELOW_BREAKDOWN"

        return {
            "ok": True,
            "session_id": session_id,
            "status": context["status"],
            "price": price,
            "directive": directive["directive"],
            "side": side,
            "location": location,
            "levels": {
                "breakout_trigger": levels.get("breakout_trigger"),
                "breakdown_trigger": levels.get("breakdown_trigger"),
                "daily_resistance": levels.get("daily_resistance"),
                "daily_support": levels.get("daily_support")
            },
            "r30_window": {
                "r30_high": context["r30_high"],
                "r30_low": context["r30_low"]
            },
            "timing": {
                "next_event_ts": context["lock_end_ts"]
            }
        }

    except Exception as e:
        return {
            "ok": False,
            "status": "ERROR",
            "msg": str(e)
        }
