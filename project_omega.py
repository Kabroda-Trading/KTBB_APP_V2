# project_omega.py
# ==============================================================================
# PROJECT OMEGA ENGINE (PIPELINE INTEGRATED)
# ==============================================================================
# - Truth source: battlebox_pipeline (Corporate Data)
# - Time source: session_manager (Corporate Clock)
# - Execution: Omega Logic (Ferrari Mode, Zones, Targets)
# ==============================================================================

from __future__ import annotations
from typing import Dict, Any, List

import battlebox_pipeline
import session_manager

# ----------------------------
# Internal Math (Targets/Strength)
# ----------------------------
def _calc_strength(entry: float, dr: float, ds: float, side: str) -> Dict[str, Any]:
    is_blue_sky = False
    if entry <= 0:
        return {"score": 0, "rating": "WAITING", "is_blue_sky": False}
    if side == "LONG" and entry > dr:
        is_blue_sky = True
    if side == "SHORT" and entry < ds:
        is_blue_sky = True
    return {"score": 0, "rating": "GO", "is_blue_sky": is_blue_sky}

# ----------------------------
# Core Omega
# ----------------------------
async def get_omega_status(
    symbol: str = "BTCUSDT",
    session_id: str = "us_ny_futures",
    ferrari_mode: bool = False,
) -> Dict[str, Any]:
    """
    1. Fetches OFFICIAL locked levels from Battlebox Pipeline.
    2. Applies Omega-specific execution logic (Ferrari/Standard).
    3. Returns command packet for UI.
    """
    current_price = 0.0
    
    try:
        # 1) ASK CORPORATE FOR THE TRUTH
        # We use 'get_live_battlebox' because it handles the 30m lock cache logic for us.
        # This ensures Omega sees EXACTLY what Session Control sees.
        pipeline_data = await battlebox_pipeline.get_live_battlebox(
            symbol=symbol,
            session_mode="MANUAL", # Force the specific session ID requested
            manual_id=session_id
        )

        if pipeline_data.get("status") == "ERROR":
            return {"ok": False, "status": "OFFLINE", "msg": "Pipeline Error"}

        # 2) EXTRACT CORPORATE DATA
        current_price = pipeline_data.get("price", 0.0)
        box = pipeline_data.get("battlebox", {})
        levels = box.get("levels", {})
        session_meta = box.get("session", {})
        
        # Corporate Levels (Locked)
        bo = float(levels.get("breakout_trigger", 0.0))
        bd = float(levels.get("breakdown_trigger", 0.0))
        dr = float(levels.get("daily_resistance", 0.0))
        ds = float(levels.get("daily_support", 0.0))
        r30_high = float(levels.get("range30m_high", 0.0))
        r30_low = float(levels.get("range30m_low", 0.0))
        
        # Session State
        # If pipeline says CALIBRATING, Omega must wait.
        if pipeline_data.get("status") == "CALIBRATING":
             return {
                "ok": True,
                "price": current_price,
                "status": "CALIBRATING",
                "telemetry": {
                    "session_state": "CALIBRATING",
                    "verification": {"r30_high": 0, "r30_low": 0, "daily_res": 0, "daily_sup": 0}
                }
            }

        # 3) OMEGA EXECUTION LOGIC (The "Smart" Part)
        status = "STANDBY"
        side = "NONE"
        stop_loss = 0.0
        
        # Ferrari mode: tighter “near trigger” radius (0.07% vs 0.10%)
        near_radius = 0.0007 if ferrari_mode else 0.0010

        if bo > 0 and bd > 0:
            if current_price > bo:
                status = "EXECUTING"
                side = "LONG"
                stop_loss = r30_low # Default structural stop
            elif current_price < bd:
                status = "EXECUTING"
                side = "SHORT"
                stop_loss = r30_high # Default structural stop
            else:
                # "LOCKED" (Near Trigger logic)
                if abs(current_price - bo) / bo < near_radius:
                    status = "LOCKED"
                    side = "LONG"
                elif abs(current_price - bd) / bd < near_radius:
                    status = "LOCKED"
                    side = "SHORT"

        # 4) TARGETING & STRENGTH
        trigger_px = bo if side == "LONG" else bd
        
        # If between zones, pick the closer trigger for display
        if side == "NONE" and bo > 0 and bd > 0:
            mid = (bo + bd) / 2.0
            trigger_px = bo if current_price >= mid else bd

        strength = _calc_strength(trigger_px, dr, ds, side)

        return {
            "ok": True,
            "status": status,
            "symbol": symbol,
            "session_id": session_id,
            "ferrari_mode": bool(ferrari_mode),
            "price": current_price,
            "side": side,
            "strength": strength,
            "triggers": {"BO": bo, "BD": bd}, # Explicit triggers for verification
            "telemetry": {
                "session_state": "ACTIVE",
                "anchor_ts": session_meta.get("anchor_ts"),
                "verification": {
                    "r30_high": r30_high, 
                    "r30_low": r30_low, 
                    "daily_res": dr, 
                    "daily_sup": ds
                },
            },
            "execution": {
                "entry": trigger_px,
                "stop_loss": stop_loss
            },
        }

    except Exception as e:
        return {"ok": False, "status": "ERROR", "price": current_price, "msg": str(e)}