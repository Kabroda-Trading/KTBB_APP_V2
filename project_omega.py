# project_omega.py
# ==============================================================================
# PROJECT OMEGA SPECIALIST (v8.2 CORPORATE STANDARD)
# ==============================================================================
# STANDARD: 
# - Logic is strictly relative to the selected SESSION_ID.
# - Time rules (Kill Zone) are derived from SESSION_MANAGER, not hardcoded.
# ==============================================================================

from __future__ import annotations
from typing import Dict, Any
from datetime import datetime, timezone, timedelta
import pytz 

import battlebox_pipeline
import session_manager # <--- The Boss

# ----------------------------
# 1. KINETIC STRATEGY LAYER
# ----------------------------
def _apply_kinetic_strategy(
    anchor_price: float, 
    levels: Dict[str, float], 
    context: Dict[str, Any], 
    shelves: Dict[str, Any],
    side: str,
    session_config: Dict[str, Any] # <--- Passed from Manager
) -> Dict[str, Any]:
    
    score = 0
    breakdown = {}
    is_blocked = False
    block_reason = ""

    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    
    if dr == 0 or ds == 0:
        return {"total_score": 0, "protocol": "CALIBRATING", "color": "GRAY", "instruction": "WAITING FOR LEVELS", "brief": "Pipeline is building levels.", "breakdown": {}, "force_align": False}

    # --- 1. ENERGY ---
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

    # --- 2. SPACE ---
    atr = float(levels.get("atr", 0) or anchor_price * 0.01)
    trigger = float(levels.get("breakout_trigger", 0)) if side == "LONG" else float(levels.get("breakdown_trigger", 0))
    target = dr if side == "LONG" else ds
    gap = abs(target - trigger)
    r_multiple = gap / atr if atr > 0 else 0
    
    if r_multiple > 2.0: score += 30; breakdown['space'] = f"SUPER SONIC ({r_multiple:.1f}R)"
    elif r_multiple > 1.0: score += 15; breakdown['space'] = f"GRIND ({r_multiple:.1f}R)"
    else: score += 0; breakdown['space'] = "BLOCKED (<1.0R)"

    # --- 3. TIME (THE CORPORATE STANDARD FIX) ---
    # Rule: Kill Zone is the first 2.5 hours of ANY session.
    # We calculate "Hours Elapsed" since the Session Open.
    
    now_utc = datetime.now(timezone.utc)
    
    # Get the official anchor time for THIS session from the Manager
    anchor_ts = session_manager.anchor_ts_for_utc_date(session_config, now_utc)
    anchor_dt = datetime.fromtimestamp(anchor_ts, timezone.utc)
    
    # Calculate how many hours into the session we are
    elapsed = (now_utc - anchor_dt).total_seconds() / 3600.0
    
    # "Kill Zone" = 0 to 2.5 hours
    is_late = elapsed > 2.5
    
    # Logic fix: If elapsed is negative (weird timezone edge case), assume kill zone
    if elapsed < 0: is_late = False

    time_penalty = 1.0
    if is_late:
        time_penalty = 0.5 
        breakdown['time'] = "LATE SESSION"
    else:
        breakdown['time'] = "KILL ZONE"

    # --- 4. MOMENTUM ---
    slope = float(context.get("slope_score", 0))
    weekly_force = "NEUTRAL"
    if slope > 0.25: weekly_force = "BULLISH"
    elif slope < -0.25: weekly_force = "BEARISH"
    
    is_aligned = False
    if (side == "LONG" and weekly_force == "BULLISH") or \
       (side == "SHORT" and weekly_force == "BEARISH"):
        is_aligned = True
        time_penalty = 1.0 # Aligned momentum overrides time penalty
        breakdown['momentum'] = f"ALIGNED ({weekly_force})"
        if breakdown.get('time') == "LATE SESSION":
            breakdown['time'] = "LATE (OVERRIDE)"
    else:
        breakdown['momentum'] = f"NEUTRAL ({weekly_force})"

    # --- 5. STRUCTURE ---
    shelf_strength = float(shelves.get("strength", 0) or 0)
    if shelf_strength > 0.5: score += 10; breakdown['structure'] = "SOLID"
    else: score += 0; breakdown['structure'] = "MESSY"

    # --- 6. LOCATION ---
    dist_to_trigger = abs(anchor_price - trigger)
    if dist_to_trigger < (atr * 0.5): score += 10; breakdown['location'] = "PRIMED"
    else: score += 0; breakdown['location'] = "CHASING"

    final_score = int(score * time_penalty)

    # --- SCORING ---
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
        brief = "Blue Sky Protocol Active."
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
        "force_align": is_aligned
    }

# ----------------------------
# 2. EXECUTION MATH
# ----------------------------
def _calc_execution_plan(entry: float, stop: float, dr: float, ds: float, side: str, mode: str, force_align: bool) -> Dict[str, Any]:
    # SAFE DEFAULT RETURN
    safe_return = {
        "targets": [], 
        "stop": 0, 
        "valid": False, 
        "bank_rule": "--", 
        "primary_target": "--", 
        "reason": "Waiting for Data",
        "protocol_display": mode, 
        "color_override": None
    }

    if entry <= 0 or stop <= 0: return safe_return
    risk = abs(entry - stop)
    if risk == 0: return safe_return

    min_req_dist = risk * 1.0 
    dist_to_wall = abs(dr - entry) if side == "LONG" else abs(ds - entry)
    
    if mode != "SUPERSONIC" and dist_to_wall < min_req_dist:
        return {
            "targets": [], "stop": 0, "valid": False, 
            "bank_rule": "INVALID", "primary_target": "BLOCKED", "reason": "Target < 1.0R",
            "protocol_display": "BLOCKED", "color_override": "RED"
        }

    targets = []
    if side == "LONG":
        t1 = entry + risk; t2 = entry + (risk * 2.0); t3 = entry + (risk * 4.0) 
        targets = [int(t1), int(t2), int(t3)]
    else: 
        t1 = entry - risk; t2 = entry - (risk * 2.0); t3 = entry - (risk * 4.0)
        targets = [int(t1), int(t2), int(t3)]

    # --- LOGIC BRANCHING ---
    primary_target = t1
    bank_rule = "BANK 75%"
    reason = "Standard"
    protocol_display = mode
    color_override = None

    if mode == "SUPERSONIC":
        if force_align:
            # === SUPERNOVA MODE (Aligned) ===
            primary_target = "OPEN (Aim T3)" 
            bank_rule = "IGNORE TP1. Trail."
            reason = "ALIGNED (Aggressive)"
            protocol_display = "SUPERNOVA"
            color_override = "SUPERNOVA" # Neon Green
        else:
            # === HYBRID DEFENSE MODE (Misaligned) ===
            primary_target = t1
            bank_rule = "BANK 75%. BE Stop."
            reason = "MISALIGNED (Defensive)"
            protocol_display = "HYBRID"
            color_override = "HYBRID" # Warning Yellow
    elif mode == "SNIPER":
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
        "primary_target": primary_target,
        "bank_rule": bank_rule,
        "reason": reason,
        "protocol_display": protocol_display,
        "color_override": color_override
    }

async def get_omega_status(
    symbol: str = "BTCUSDT",
    session_id: str = "us_ny_futures",
    ferrari_mode: bool = False,
    force_time_utc: str = None,
    force_price: float = None 
) -> Dict[str, Any]:
    
    # 1. TIME CONTROL
    if force_time_utc:
        t_now = datetime.now(timezone.utc)
        fake_dt = datetime.strptime(force_time_utc, "%H:%M")
        now_utc = t_now.replace(hour=fake_dt.hour, minute=fake_dt.minute, second=0, microsecond=0)
        is_simulation = True
    else:
        now_utc = datetime.now(timezone.utc)
        is_simulation = False

    # 2. FETCH PIPELINE (MANUAL MODE ENFORCED)
    # This ensures we get the data for the requested session ID, not just "whatever is open".
    pipeline_data = await battlebox_pipeline.get_live_battlebox(
        symbol=symbol, session_mode="MANUAL", manual_id=session_id
    )
    
    p_status = pipeline_data.get("status")
    if p_status in ["ERROR", "OFFLINE"]:
         return {"ok": False, "status": "OFFLINE", "msg": "Pipeline Error"}

    # 3. GET SESSION CONFIG (THE BOSS)
    # We need the config to know the specific Open Time for this session
    session_config = session_manager.get_session_config(session_id)

    if p_status == "CALIBRATING":
        return {
            "ok": True, "status": "CALIBRATING", "price": pipeline_data.get("price", 0.0),
            "kinetic": {
                "total_score": 0, "protocol": "CALIBRATING", "color": "GRAY", 
                "instruction": "WAITING FOR 30M LOCK", "brief": "System calibrating.",
                "breakdown": {}
            },
            "plans": {"LONG": {}, "SHORT": {}}
        }

    # === SIMULATION HOOK ===
    real_price = float(pipeline_data.get("price", 0.0))
    current_price = force_price if force_price is not None else real_price
    if force_price is not None: is_simulation = True

    box = pipeline_data.get("battlebox", {})
    levels = box.get("levels", {})
    context = box.get("context", {})
    shelves = box.get("htf_shelves", {})
    anchor_price = float(levels.get("session_open_price") or current_price)
    
    # 4. LEVELS & SIDE
    bo = float(levels.get("breakout_trigger", 0.0))
    bd = float(levels.get("breakdown_trigger", 0.0))
    dr = float(levels.get("daily_resistance", 0.0))
    ds = float(levels.get("daily_support", 0.0))
    r30_high = float(levels.get("range30m_high", 0.0))
    r30_low = float(levels.get("range30m_low", 0.0))
    
    active_side = "NONE"
    status = "STANDBY"
    battle_state = box.get("session_battle", {})
    if battle_state.get("action") == "GO":
        status = "EXECUTING"
        active_side = battle_state.get("permission", {}).get("side", "NONE")
    
    closest_side = "LONG" if (current_price >= (bo + bd)/2) else "SHORT"
    calc_side = active_side if active_side != "NONE" else closest_side

    # 5. KINETIC STRATEGY (PASS CONFIG, NOT JUST ID)
    # This allows the strategy layer to calculate time relative to the Anchor.
    kinetic = _apply_kinetic_strategy(
        anchor_price, levels, context, shelves, calc_side, session_config
    )
    
    # 6. PLANS & OVERRIDES
    plan_long = _calc_execution_plan(bo, r30_low, dr, ds, "LONG", kinetic["protocol"], kinetic["force_align"])
    plan_short = _calc_execution_plan(bd, r30_high, dr, ds, "SHORT", kinetic["protocol"], kinetic["force_align"])

    active_plan = plan_long if calc_side == "LONG" else plan_short
    if active_plan.get("protocol_display") and active_plan["valid"]:
        kinetic["protocol"] = active_plan["protocol_display"]
        if active_plan.get("color_override"):
            kinetic["color"] = active_plan["color_override"]

    if not active_plan["valid"] and kinetic["protocol"] not in ["BLOCKED", "CLOSED", "CALIBRATING"]:
        kinetic["protocol"] = "BLOCKED"
        kinetic["instruction"] = "⛔ R/R INVALID."
        kinetic["color"] = "RED"

    return {
        "ok": True,
        "status": status,
        "symbol": symbol,
        "price": current_price,
        "active_side": active_side if active_side != "NONE" else closest_side,
        "session_mode": kinetic["protocol"],
        "is_simulation": is_simulation,
        "simulated_time": now_utc.strftime("%H:%M UTC") if is_simulation else None,
        "kinetic": kinetic,
        "plans": {"LONG": plan_long, "SHORT": plan_short}
    }