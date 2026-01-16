# battle_control.py
# KABRODA BATTLE CONTROL — Unified Tactical Readout (Consumer Only)

from __future__ import annotations
from fastapi import APIRouter
import battlebox_pipeline

router = APIRouter()

@router.get("/battle-control")
async def battle_control_status(
    symbol: str = "BTCUSDT",
    session_mode: str = "AUTO",     # AUTO or MANUAL
    manual_id: str | None = None,   # e.g. us_ny_futures
    operator_flex: bool = False
):
    """
    IMPORTANT:
    - Does NOT compute anchors/levels locally.
    - Consumes locked truth from battlebox_pipeline.
    """
    try:
        data = await battlebox_pipeline.get_live_battlebox(
            symbol=symbol,
            session_mode=session_mode,
            manual_id=manual_id,
            operator_flex=operator_flex
        )
        return {"ok": True, **data}
    except Exception as e:
        return {"ok": False, "status": "ERROR", "msg": str(e)}
