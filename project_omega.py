# project_omega.py
# ==============================================================================
# PROJECT OMEGA SPECIALIST (HOTFIXED)
# ==============================================================================
# ROLE: Specialist Consumer.
# FIX: Ensures 'primary_target' key exists in all return paths to prevent 500 Error.
# ==============================================================================

from __future__ import annotations
from typing import Dict, Any
from datetime import datetime, timezone
import pytz 

import battlebox_pipeline

# ----------------------------
# 1. KINETIC STRATEGY LAYER
# ----------------------------
def _apply_kinetic_strategy(
    anchor_price: float, 
    levels: Dict[str, float], 
    context: Dict[str, Any], 
    shelves: Dict[str, Any],
    side: str
) -> Dict[str, Any]:
    
    score = 0
    breakdown = {}
    is_blocked = False
    block_reason = ""

    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    
    if dr == 0 or ds == 0:
        return {"total_score": 0, "protocol": "CALIBRATING", "color": "GRAY", "instruction": "WAITING FOR LEVELS", "brief": "Pipeline is building levels.", "breakdown": {}, "force_align": False}

    range_size = abs(dr - ds)
    bps = (range_size / anchor_price) * 10000 if anchor_price > 0 else 500
    
    if bps > 350:
        is_blocked = True
        block_reason = f"EXHAUSTED ({int(bps)}bps)"
        score = 0
    elif bps < 150: 
        score += 30; breakdown['energy'] = f"SUPER COILED ({int(bps)}bps)"
    else: 
        score += 15; breakdown['energy'] = f"STANDARD ({int(bps)}bps)"

    atr = float(levels.get("atr", 0) or anchor_price * 0.01)
    trigger = float(levels.get("breakout_trigger", 0)) if side == "LONG" else float(levels.get("breakdown_trigger", 0))
    target = dr if side == "LONG" else ds
    gap = abs(target - trigger)
    r_multiple = gap / atr if atr > 0 else 0
    
    if r_multiple > 2.0: score += 30; breakdown['space'] = f"SUPER SONIC ({r_multiple:.1f}R)"
    elif r_multiple > 1.0: score += 15; breakdown['space'] = f"GRIND ({r_multiple:.1f}R)"
    else: score += 0; breakdown['space'] = "BLOCKED (<1.0R)"

    now_utc = datetime.now(timezone.utc)
    tz_cst = pytz.timezone('America/Chicago')
    now_cst = now_utc.astimezone(tz_cst)
    kill_end = now_cst.replace(hour=10, minute=30, second=0, microsecond=0)
    
    time_penalty = 1.0
    if now_cst > kill_end:
        time_penalty = 0.5 
        breakdown['time'] = "LATE SESSION"
    else:
        breakdown['time'] = "KILL ZONE"

    slope = float(context.get("slope_score", 0))
    weekly_force = "NEUTRAL"
    if slope > 0.25: weekly_force = "BULLISH"
    elif slope < -0.25: weekly_force = "BEARISH"
    
    if (side == "LONG" and weekly_force == "BULLISH") or \
       (side == "SHORT" and weekly_force == "BEARISH"):
        time_penalty = 1.0 
        breakdown['momentum'] = f"ALIGNED ({weekly_force})"
        if breakdown.get('time') == "LATE SESSION":
            breakdown['time'] = "LATE (OVERRIDE)"
    else:
        breakdown['momentum'] = f"NEUTRAL ({weekly_force})"

    shelf_strength = float(shelves.get("strength", 0) or 0)
    if shelf_strength > 0.5: score += 10; breakdown['structure'] = "SOLID"
    else: score += 0; breakdown['structure'] = "MESSY"

    dist_to_trigger = abs(anchor_price - trigger)
    if dist_to_trigger < (atr * 0.5): score += 10; breakdown['location'] = "PRIMED"
    else: score += 0; breakdown['location'] = "CHASING"

    final_score = int(score * time_penalty)

    brief = ""
    
    if is_blocked:
        protocol = "BLOCKED"
        color = "RED"
        instruction = f"⛔ STAND DOWN. {block_reason}"
        brief = "Market exhausted. High chop risk."
    elif breakdown.get('time') == "LATE SESSION" and final_score < 40:
        protocol = "CLOSED"
        color = "GRAY"
        instruction = "💤 SESSION CLOSED."
        brief = "Volume low. No Weekly force. Done for the day."
    elif final_score >= 71:
        protocol = "SUPERSONIC"
        color = "CYAN"
        instruction = "🔥 MOMENTUM OVERRIDE."
        brief = "Blue Sky. Uncapped Profits. Trail Stop."
    elif final_score >= 41:
        protocol = "SNIPER"
        color = "GREEN"
        instruction = "⌖ EXECUTE ON 5M CLOSE."
        brief = "Standard breakout. Bank 75% at T1."
    else:
        protocol = "DOGFIGHT"
        color = "AMBER"
        instruction = "🛡️ DEFENSIVE / SCALP."
        brief = "Low energy. Quick hits only."

    return {
        "total_score": final_score,
        "protocol": protocol,
        "color": color,
        "instruction": instruction,
        "brief": brief,
        "breakdown": breakdown,
        "force_align": (weekly_force in breakdown['momentum'])
    }

# ----------------------------
# 2. EXECUTION MATH (FIXED)
# ----------------------------
def _calc_execution_plan(entry: float, stop: float, dr: float, ds: float, side: str, mode: str, force_align: bool) -> Dict[str, Any]:
    # SAFE DEFAULT RETURN
    safe_return = {
        "targets": [], 
        "stop": 0, 
        "valid": False, 
        "bank_rule": "--", 
        "primary_target": "--",  # <--- THIS WAS MISSING
        "reason": "Waiting for Data"
    }

    if entry <= 0 or stop <= 0: return safe_return
    
    risk = abs(entry - stop)
    if risk == 0: return safe_return

    min_req_dist = risk * 1.0 
    dist_to_wall = abs(dr - entry) if side == "LONG" else abs(ds - entry)
    
    if mode != "SUPERSONIC" and dist_to_wall < min_req_dist:
        return {
            "targets": [], 
            "stop": 0, 
            "valid": False, 
            "bank_rule": "INVALID", 
            "primary_target": "BLOCKED", # <--- ENSURED
            "reason": "Target < 1.0R"
        }

    targets = []
    
    if side == "LONG":
        shield = entry + (risk * 0.5) 
        t1 = entry + risk
        t2 = entry + (risk * 2.0)
        t3 = entry + (risk * 4.0) 
        targets = [int(shield), int(t1), int(t2), int(t3)]
    else: 
        shield = entry - (risk * 0.5)
        t1 = entry - risk
        t2 = entry - (risk * 2.0)
        t3 = entry - (risk * 4.0)
        targets = [int(shield), int(t1), int(t2), int(t3)]

    primary_target = t1
    bank_rule = "BANK 75%"
    reason = "Standard"

    if mode == "SUPERSONIC":
        primary_target = "OPEN" 
        bank_rule = "TRAIL 15M"
        reason = "Runner"
    elif mode == "SNIPER":
        if force_align: 
            primary_target = t2
            bank_rule = "BANK 50%"
            reason = "Weekly Push"
        else: 
            primary_target = t1
            bank_rule = "BANK 75%"
            reason = "Standard"
    elif mode == "DOGFIGHT":
        primary_target = t1
        bank_rule = "BANK 100%"
        reason = "Scalp"

    return {
        "targets": targets, 
        "stop": stop, 
        "valid": True,
        "primary_target": primary_target, # <--- GUARANTEED
        "bank_rule": bank_rule,
        "reason": reason
    }

async def get_omega_status(
    symbol: str = "BTCUSDT",
    session_id: str = "us_ny_futures",
    ferrari_mode: bool = False,
) -> Dict[str, Any]:
    
    # 1. FETCH FROM PIPELINE
    pipeline_data = await battlebox_pipeline.get_live_battlebox(
        symbol=symbol, session_mode="MANUAL", manual_id=session_id
    )
    
    p_status = pipeline_data.get("status")
    
    if p_status in ["ERROR", "OFFLINE"]:
         return {"ok": False, "status": "OFFLINE", "msg": "Pipeline Error"}

    if p_status == "CALIBRATING":
        return {
            "ok": True, 
            "status": "CALIBRATING",
            "price": pipeline_data.get("price", 0.0),
            "session_mode": "CALIBRATING",
            "active_side": "NONE",
            "kinetic": {
                "total_score": 0, "protocol": "CALIBRATING", "color": "GRAY", 
                "instruction": "WAITING FOR 30M LOCK", "brief": "System calibrating.",
                "breakdown": {}
            },
            "plans": {
                "LONG": {"trigger": 0, "stop": 0, "bank_rule": "--", "primary_target": "--", "reason": "--"}, 
                "SHORT": {"trigger": 0, "stop": 0, "bank_rule": "--", "primary_target": "--", "reason": "--"}
            }
        }

    current_price = float(pipeline_data.get("price", 0.0))
    box = pipeline_data.get("battlebox", {})
    levels = box.get("levels", {})
    context = box.get("context", {})
    shelves = box.get("htf_shelves", {})
    
    anchor_price = float(levels.get("session_open_price") or current_price)
    
    # 2. LEVELS
    bo = float(levels.get("breakout_trigger", 0.0))
    bd = float(levels.get("breakdown_trigger", 0.0))
    dr = float(levels.get("daily_resistance", 0.0))
    ds = float(levels.get("daily_support", 0.0))
    r30_high = float(levels.get("range30m_high", 0.0))
    r30_low = float(levels.get("range30m_low", 0.0))
    
    # 3. SIDE
    active_side = "NONE"
    status = "STANDBY"
    
    battle_state = box.get("session_battle", {})
    if battle_state.get("action") == "GO":
        status = "EXECUTING"
        active_side = battle_state.get("permission", {}).get("side", "NONE")
    
    closest_side = "LONG" if (current_price >= (bo + bd)/2) else "SHORT"
    calc_side = active_side if active_side != "NONE" else closest_side

    # 4. KINETIC
    now_utc = datetime.now(timezone.utc)
    kinetic = _apply_kinetic_strategy(
        anchor_price, levels, context, shelves, calc_side
    )
    
    # 5. PLANS
    plan_long = _calc_execution_plan(bo, r30_low, dr, ds, "LONG", kinetic["protocol"], kinetic["force_align"])
    plan_short = _calc_execution_plan(bd, r30_high, dr, ds, "SHORT", kinetic["protocol"], kinetic["force_align"])

    # 6. MATH GATE
    active_plan = plan_long if calc_side == "LONG" else plan_short
    if not active_plan["valid"] and kinetic["protocol"] not in ["BLOCKED", "CLOSED", "CALIBRATING"]:
        kinetic["protocol"] = "BLOCKED"
        kinetic["instruction"] = "⛔ R/R INVALID."
        kinetic["color"] = "RED"
        kinetic["brief"] = "Potential target is too close to resistance."

    return {
        "ok": True,
        "status": status,
        "symbol": symbol,
        "price": current_price,
        "active_side": active_side if active_side != "NONE" else closest_side,
        "session_mode": kinetic["protocol"],
        "kinetic": kinetic,
        "plans": {
            # Mapped keys to match HTML expectation
            "LONG": {"trigger": bo, "stop": r30_low, "targets": plan_long["targets"], "bank": plan_long["bank_rule"], "prim": plan_long["primary_target"], "reason": plan_long["reason"]},
            "SHORT": {"trigger": bd, "stop": r30_high, "targets": plan_short["targets"], "bank": plan_short["bank_rule"], "prim": plan_short["primary_target"], "reason": plan_short["reason"]}
        },
        "telemetry": {
            "session_state": "ACTIVE",
            "verification": { "bo": bo, "bd": bd },
        }
    }