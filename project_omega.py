# project_omega.py
# ==============================================================================
# PROJECT OMEGA ENGINE (v2025 - PLATINUM MASTER)
# ==============================================================================
# 1. TIME GATE: 10:30 AM CST (Soft Kill) -> Becomes "CLOSED" if no Override.
# 2. ENERGY GATE: < 350 bps (Exhaustion Filter)
# 3. WEEKLY GATE: Trend Slope Filter (Strength Check)
# 4. MATH GATE: Targets calculated from LOCKED session levels only.
# ==============================================================================

from __future__ import annotations
from typing import Dict, Any
from datetime import datetime, timezone
import pytz 

import battlebox_pipeline

# ----------------------------
# 1. KINETIC MATH ENGINE
# ----------------------------
def _calc_kinetic_score(
    anchor_price: float, 
    levels: Dict[str, float], 
    context: Dict[str, Any], 
    shelves: Dict[str, Any],
    side: str,
    current_time_utc: datetime
) -> Dict[str, Any]:
    
    score = 0
    breakdown = {}
    is_blocked = False
    block_reason = ""

    # --- A. ENERGY (Spring) ---
    dr = levels.get("daily_resistance", 0)
    ds = levels.get("daily_support", 0)
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

    # --- B. SPACE (Runway) ---
    atr = levels.get("atr", range_size * 0.25) 
    if atr == 0: atr = anchor_price * 0.01 
    
    trigger = levels.get("breakout_trigger") if side == "LONG" else levels.get("breakdown_trigger")
    target = dr if side == "LONG" else ds
    gap = abs(target - trigger)
    r_multiple = gap / atr
    
    if r_multiple > 2.0: score += 30; breakdown['space'] = f"SUPER SONIC ({r_multiple:.1f}R)"
    elif r_multiple > 1.0: score += 15; breakdown['space'] = f"GRIND ({r_multiple:.1f}R)"
    else: score += 0; breakdown['space'] = "BLOCKED (<1.0R)"

    # --- C. TIME GATE (10:30 CST) ---
    tz_cst = pytz.timezone('America/Chicago')
    now_cst = current_time_utc.astimezone(tz_cst)
    kill_end = now_cst.replace(hour=10, minute=30, second=0, microsecond=0)
    
    time_penalty = 1.0
    if now_cst > kill_end:
        time_penalty = 0.5 
        breakdown['time'] = "LATE SESSION"
    else:
        breakdown['time'] = "KILL ZONE"

    # --- D. WEEKLY FORCE (Override) ---
    slope = float(context.get("slope_score", 0))
    weekly_force = "NEUTRAL"
    if slope > 0.25: weekly_force = "BULLISH"
    elif slope < -0.25: weekly_force = "BEARISH"
    
    if (side == "LONG" and weekly_force == "BULLISH") or \
       (side == "SHORT" and weekly_force == "BEARISH"):
        time_penalty = 1.0 # Override the Time Penalty
        breakdown['momentum'] = f"WEEKLY PUSH ({weekly_force})"
        if breakdown['time'] == "LATE SESSION":
            breakdown['time'] = "LATE (OVERRIDE)"
    else:
        breakdown['momentum'] = f"NEUTRAL/FADE ({weekly_force})"

    # --- SCORING ---
    shelf_strength = float(shelves.get("strength", 0))
    if shelf_strength > 0.5: score += 10; breakdown['structure'] = "SOLID"
    else: score += 0; breakdown['structure'] = "MESSY"

    dist_to_trigger = abs(anchor_price - trigger)
    if dist_to_trigger < (atr * 0.5): score += 10; breakdown['location'] = "PRIMED"
    else: score += 0; breakdown['location'] = "CHASING"

    final_score = int(score * time_penalty)

    # --- PROTOCOL ROUTER ---
    brief = ""
    
    if is_blocked:
        protocol = "BLOCKED"
        color = "RED"
        instruction = f"⛔ STAND DOWN. {block_reason}"
        brief = "Market is over-extended. Probability of chop is >90%."
        
    elif breakdown['time'] == "LATE SESSION" and final_score < 40:
        # THE KILL SWITCH (Gray Screen)
        protocol = "CLOSED"
        color = "GRAY"
        instruction = "💤 SESSION CLOSED. GO HOME."
        brief = "Volume has dried up. No Weekly Force detected. Come back tomorrow."
        
    elif final_score >= 71:
        protocol = "SUPERSONIC"
        color = "CYAN"
        instruction = "🔥 MOMENTUM OVERRIDE. DEPLOY AGGRESSIVE."
        brief = "Blue Sky breakout supported by structure. Uncap profits."
        
    elif final_score >= 41:
        protocol = "SNIPER"
        color = "GREEN"
        instruction = "⌖ WAIT FOR 5M CLOSE. BANK 75%."
        brief = "Standard technical breakout. Take income at T1."
            
    else:
        protocol = "DOGFIGHT"
        color = "AMBER"
        instruction = "🛡️ DEFENSIVE. REDUCE RISK."
        brief = "Low energy. Scalp or cash is the best position."

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
# 2. EXECUTION PLANNER
# ----------------------------
def _calc_execution_plan(entry: float, stop: float, dr: float, ds: float, side: str, mode: str, force_align: bool) -> Dict[str, Any]:
    if entry <= 0: return {"targets": [], "stop": 0, "valid": False, "bank_rule": "--", "reason": "--"}
    
    risk = abs(entry - stop)
    if risk == 0: return {"targets": [], "stop": 0, "valid": False, "bank_rule": "--", "reason": "--"}

    # MATH GATE: R/R Check
    min_req_dist = risk * 1.0 
    dist_to_wall = abs(dr - entry) if side == "LONG" else abs(ds - entry)
    
    # Block trades into walls unless Supersonic
    if mode != "SUPERSONIC" and dist_to_wall < min_req_dist:
        return {"targets": [], "stop": 0, "valid": False, "bank_rule": "BAD MATH", "reason": "Target < 1.0R (Blocked)"}

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

    # EXECUTIVE ORDER
    primary_target = t1
    bank_rule = "BANK 75%"
    reason = "Standard"

    if mode == "SUPERSONIC":
        primary_target = "OPEN" 
        bank_rule = "TRAIL 15M"
        reason = "Runner Mode"
    elif mode == "SNIPER":
        if force_align: 
            primary_target = t2
            bank_rule = "BANK 50%"
            reason = "Weekly Assist"
        else: 
            primary_target = t1
            bank_rule = "BANK 75%"
            reason = "Standard"
    elif mode == "CLOSED":
        primary_target = "--"
        bank_rule = "NO TRADE"
        reason = "Session Closed"
    elif mode == "DOGFIGHT":
        primary_target = t1
        bank_rule = "BANK 100%"
        reason = "Scalp"

    return {
        "targets": targets, 
        "stop": stop, 
        "valid": True,
        "primary_target": primary_target,
        "bank_rule": bank_rule,
        "reason": reason
    }

async def get_omega_status(
    symbol: str = "BTCUSDT",
    session_id: str = "us_ny_futures",
    ferrari_mode: bool = False,
) -> Dict[str, Any]:
    
    pipeline_data = await battlebox_pipeline.get_live_battlebox(
        symbol=symbol, session_mode="MANUAL", manual_id=session_id
    )
    
    if pipeline_data.get("status") in ["ERROR", "OFFLINE"]:
         return {"ok": False, "status": "OFFLINE", "msg": "Pipeline Error"}

    current_price = pipeline_data.get("price", 0.0)
    box = pipeline_data.get("battlebox", {})
    levels = box.get("levels", {})
    context = box.get("context", {})
    shelves = box.get("htf_shelves", {})
    
    anchor_price = levels.get("session_open_price") or current_price
    
    # 1. LEVELS
    bo = float(levels.get("breakout_trigger", 0.0))
    bd = float(levels.get("breakdown_trigger", 0.0))
    dr = float(levels.get("daily_resistance", 0.0))
    ds = float(levels.get("daily_support", 0.0))
    r30_high = float(levels.get("range30m_high", 0.0))
    r30_low = float(levels.get("range30m_low", 0.0))
    
    # 2. STATUS
    active_side = "NONE"
    status = "STANDBY"
    
    battle_state = box.get("session_battle", {})
    if battle_state.get("action") == "GO":
        status = "EXECUTING"
        active_side = battle_state.get("permission", {}).get("side", "NONE")
    
    closest_side = "LONG" if (current_price >= (bo + bd)/2) else "SHORT"
    calc_side = active_side if active_side != "NONE" else closest_side

    # 3. KINETIC ENGINE
    now_utc = datetime.now(timezone.utc)
    kinetic = _calc_kinetic_score(
        anchor_price, levels, context, shelves, 
        calc_side, now_utc
    )
    
    # 4. PLANNER
    plan_long = _calc_execution_plan(bo, r30_low, dr, ds, "LONG", kinetic["protocol"], kinetic["force_align"])
    plan_short = _calc_execution_plan(bd, r30_high, dr, ds, "SHORT", kinetic["protocol"], kinetic["force_align"])

    # 5. MATH GATE
    active_plan = plan_long if calc_side == "LONG" else plan_short
    if not active_plan["valid"] and kinetic["protocol"] not in ["BLOCKED", "CLOSED"]:
        kinetic["protocol"] = "BLOCKED"
        kinetic["instruction"] = "⛔ R/R INVALID. TRADE BLOCKED."
        kinetic["color"] = "RED"
        kinetic["brief"] = "Potential target is too close to resistance. Bad math."

    return {
        "ok": True,
        "status": status,
        "symbol": symbol,
        "price": current_price,
        "active_side": active_side if active_side != "NONE" else closest_side,
        "session_mode": kinetic["protocol"],
        "kinetic": kinetic,
        "plans": {
            "LONG": {"trigger": bo, "stop": r30_low, "targets": plan_long["targets"], "bank": plan_long["bank_rule"], "prim": plan_long["primary_target"], "reason": plan_long["reason"]},
            "SHORT": {"trigger": bd, "stop": r30_high, "targets": plan_short["targets"], "bank": plan_short["bank_rule"], "prim": plan_short["primary_target"], "reason": plan_short["reason"]}
        },
        "telemetry": {
            "session_state": "ACTIVE",
            "verification": { "bo": bo, "bd": bd },
        }
    }