# project_omega.py
# ==============================================================================
# PROJECT OMEGA SPECIALIST (v16.4 - FULL INTEGRITY RESTORED)
# ==============================================================================
# 1. DRIFT FIX: Locks to Highest Score (No flipping).
# 2. BIAS DETECTION: Explicitly calculates which side has higher potential.
# 3. TEXT LOGIC RESTORED: Full "Supernova" and "Bank Rule" instructions.
# ==============================================================================

from __future__ import annotations
from typing import Dict, Any, List
from datetime import datetime, timezone
import session_manager
import battlebox_pipeline

# ----------------------------
# 1. KINETIC STRATEGY (MATH)
# ----------------------------
def _calculate_locked_strategy(anchor_price, levels, context, shelves, side):
    """
    Calculates Score for a specific direction (LONG or SHORT).
    """
    score = 0
    breakdown = {}
    is_blocked = False
    block_reason = ""

    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)

    if dr == 0 or ds == 0:
        return {"total_score": 0, "protocol": "CALIBRATING", "color": "GRAY", "breakdown": {}}

    # 1. ENERGY (30pts)
    range_size = abs(dr - ds)
    bps = (range_size / anchor_price) * 10000 if anchor_price > 0 else 500

    if bps > 350:
        is_blocked = True
        block_reason = f"EXHAUSTED ({int(bps)}bps)"
        score = 0
    elif bps < 100:
        score += 30
        breakdown['energy'] = f"SUPER COILED ({int(bps)})"
    elif bps < 200:
        score += 15
        breakdown['energy'] = f"STANDARD ({int(bps)})"
    else:
        score += 0
        breakdown['energy'] = f"LOOSE ({int(bps)})"

    # 2. SPACE (30pts)
    atr = float(levels.get("atr", 0))
    if atr == 0: atr = anchor_price * 0.01

    trigger = float(levels.get("breakout_trigger", 0)) if side == "LONG" else float(levels.get("breakdown_trigger", 0))
    target = dr if side == "LONG" else ds
    gap = abs(target - trigger)
    r_multiple = gap / atr if atr > 0 else 0

    if r_multiple > 2.0:
        score += 30
        breakdown['space'] = f"SUPER SONIC ({r_multiple:.1f}R)"
    elif r_multiple > 1.0:
        score += 15
        breakdown['space'] = f"GRIND ({r_multiple:.1f}R)"
    else:
        score += 0
        breakdown['space'] = "BLOCKED (<1.0R)"

    # 3. MOMENTUM (20pts)
    weekly_force = context.get("weekly_force", "NEUTRAL")
    slope_score = float(levels.get("slope", 0.0))
    is_aligned = False
    
    if (side == "LONG" and weekly_force == "BULLISH") or (side == "SHORT" and weekly_force == "BEARISH"):
        is_aligned = True
        score += 10
    
    if (side == "LONG" and slope_score > 0.2) or (side == "SHORT" and slope_score < -0.2):
        score += 10

    breakdown['momentum'] = f"ALIGNED ({weekly_force})" if is_aligned else f"NEUTRAL ({weekly_force})"

    # 4. STRUCTURE (10pts)
    structure_score = float(levels.get("structure_score", 0.0))
    if structure_score > 0.5:
        score += 10
        breakdown['structure'] = f"SOLID ({structure_score:.1f})"
    else:
        breakdown['structure'] = "MESSY"

    # 5. LOCATION (10pts)
    dist_at_open = abs(anchor_price - trigger)
    if dist_at_open < (atr * 0.5):
        score += 10
        breakdown['location'] = "PRIMED"
    else:
        breakdown['location'] = "WIDE"

    # CLASSIFICATION
    protocol = "DOGFIGHT"
    color = "AMBER"
    instruction = "🛡️ DEFENSIVE / SCALP."
    
    if is_blocked:
        protocol = "BLOCKED"; color = "RED"; instruction = f"⛔ STAND DOWN. {block_reason}"
    elif score >= 75: 
        protocol = "SUPERSONIC"; color = "CYAN"; instruction = "🔥 MOMENTUM OVERRIDE."
    elif score >= 50: 
        protocol = "SNIPER"; color = "GREEN"; instruction = "⌖ EXECUTE ON 5M CLOSE."
    elif score <= 40:
        protocol = "GROUNDED"; color = "RED"; instruction = "⛔ LOW ENERGY."

    return {
        "total_score": score,
        "protocol": protocol,
        "color": color,
        "instruction": instruction,
        "breakdown": breakdown,
        "force_align": is_aligned
    }

# ----------------------------
# 2. EXECUTION MATH (RESTORED FULL LOGIC)
# ----------------------------
def _calc_execution_plan(entry, stop, dr, ds, side, mode, force_align):
    
    safe_return = {
        "valid": False, "trigger": 0, "targets": [], "stop": 0,
        "bank_rule": "--", "be_trigger": 0, "protocol_display": mode, "color_override": None
    }

    if entry <= 0 or stop <= 0: return safe_return
    risk = abs(entry - stop)
    if risk == 0: return safe_return

    # Blocking Logic (Restored)
    min_req_dist = risk * 1.0
    dist_to_wall = abs(dr - entry) if side == "LONG" else abs(ds - entry)

    if mode != "SUPERSONIC" and dist_to_wall < min_req_dist:
        return {
            "valid": False, "trigger": entry, "targets": [], "stop": 0,
            "bank_rule": "INVALID", "be_trigger": 0,
            "primary_target": "BLOCKED", "reason": "Target < 1.0R",
            "protocol_display": "BLOCKED", "color_override": "RED"
        }

    targets = []
    if side == "LONG":
        t1 = entry + risk
        t2 = entry + (risk * 2.0)
        t3 = entry + (risk * 4.0)
        targets = [int(t1), int(t2), int(t3)]
        be_trigger = entry + (risk * 0.6)
    else:
        t1 = entry - risk
        t2 = entry - (risk * 2.0)
        t3 = entry - (risk * 4.0)
        targets = [int(t1), int(t2), int(t3)]
        be_trigger = entry - (risk * 0.6)

    # TEXT LOGIC (RESTORED)
    primary_target = t1
    bank_rule = "BANK 75%"
    reason = "Standard"
    protocol_display = mode
    color_override = None

    if mode == "SUPERSONIC":
        if force_align:
            primary_target = "OPEN (Aim T3)"
            bank_rule = "IGNORE TP1. Trail."
            reason = "ALIGNED (Aggressive)"
            protocol_display = "SUPERNOVA"
            color_override = "SUPERNOVA" # Cyan/Gold hybrid handling in UI
        else:
            primary_target = t1
            bank_rule = "BANK 75%. BE Stop."
            reason = "MISALIGNED (Defensive)"
            protocol_display = "HYBRID"
            color_override = "HYBRID"
    elif mode == "SNIPER":
        primary_target = t1
        bank_rule = "BANK 75%"
        reason = "Standard"
    elif mode == "DOGFIGHT":
        primary_target = t1
        bank_rule = "BANK 100%"
        reason = "Scalp"

    return {
        "trigger": entry, "targets": targets, "stop": stop, "valid": True,
        "primary_target": primary_target, "bank_rule": bank_rule,
        "be_trigger": int(be_trigger), "reason": reason,
        "protocol_display": protocol_display, "color_override": color_override
    }

# ----------------------------
# 3. INTERNAL TRIGGER LOGIC
# ----------------------------
def _check_omega_triggers(levels, candles, mode, start_time_filter):
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    if not candles: return {"action": "STANDBY", "side": "NONE", "trigger_time": 0}

    require_close = ("SUPERSONIC" not in mode and "SUPERNOVA" not in mode)
    action, side, trigger_time = "STANDBY", "NONE", 0

    for c in candles:
        t = int(c["time"])
        if t < start_time_filter: continue

        h, l, c_price = float(c["high"]), float(c["low"]), float(c["close"])

        # LONG CHECK
        if require_close:
            if c_price > bo: action, side, trigger_time = "GO", "LONG", t; break
        else:
            if h >= bo: action, side, trigger_time = "GO", "LONG", t; break # Supersonic takes wicks

        # SHORT CHECK
        if require_close:
            if c_price < bd: action, side, trigger_time = "GO", "SHORT", t; break
        else:
            if l <= bd: action, side, trigger_time = "GO", "SHORT", t; break

    return {"action": action, "side": side, "trigger_time": trigger_time}

async def get_omega_status(symbol="BTCUSDT", session_id="us_ny_futures", ferrari_mode=False, force_time_utc=None, force_price=None):
    session_id = "us_ny_futures"
    now_utc = datetime.now(timezone.utc)
    if force_time_utc:
        fake_dt = datetime.strptime(force_time_utc, "%H:%M")
        now_utc = now_utc.replace(hour=fake_dt.hour, minute=fake_dt.minute, second=0)
    
    session_config = session_manager.get_session_config(session_id)
    anchor_ts = session_manager.anchor_ts_for_utc_date(session_config, now_utc)
    anchor_dt = datetime.fromtimestamp(anchor_ts, timezone.utc)
    elapsed = (now_utc - anchor_dt).total_seconds() / 3600.0
    is_session_closed = elapsed > 7.5 or elapsed < 0

    pipeline_data = await battlebox_pipeline.get_live_battlebox(symbol=symbol, session_mode="MANUAL", manual_id=session_id)
    current_price = force_price if force_price is not None else float(pipeline_data.get("price", 0.0))

    if is_session_closed or pipeline_data.get("status") == "CALIBRATING":
        return {
            "ok": True, "status": "CALIBRATING" if not is_session_closed else "CLOSED",
            "price": current_price,
            "kinetic": {"total_score": 0, "protocol": "CALIBRATING", "color": "GRAY", "breakdown": {}},
            "plans": {"LONG": {}, "SHORT": {}}
        }

    # Data Extraction
    box = pipeline_data.get("battlebox", {})
    levels = box.get("levels", {})
    context = box.get("context", {})
    shelves = box.get("htf_shelves", {})
    raw_candles = pipeline_data.get("candles", [])
    anchor_price = float(levels.get("session_open_price") or current_price)
    
    bo = float(levels.get("breakout_trigger", 0.0))
    bd = float(levels.get("breakdown_trigger", 0.0))
    dr = float(levels.get("daily_resistance", 0.0))
    ds = float(levels.get("daily_support", 0.0))
    r30_high = float(levels.get("range30m_high", 0.0))
    r30_low = float(levels.get("range30m_low", 0.0))

    # --- PHASE 2: CALCULATE BOTH SIDES ---
    kinetic_long = _calculate_locked_strategy(anchor_price, levels, context, shelves, "LONG")
    kinetic_short = _calculate_locked_strategy(anchor_price, levels, context, shelves, "SHORT")

    # --- BIAS DETECTION (ANTI-DRIFT) ---
    bias_direction = "NEUTRAL"
    if kinetic_long["total_score"] > kinetic_short["total_score"] + 10:
        bias_direction = "LONG"
        kinetic_display = kinetic_long
    elif kinetic_short["total_score"] > kinetic_long["total_score"] + 10:
        bias_direction = "SHORT"
        kinetic_display = kinetic_short
    else:
        bias_direction = "NEUTRAL"
        # If neutral, default to the stronger one just for display
        kinetic_display = kinetic_long if kinetic_long["total_score"] >= kinetic_short["total_score"] else kinetic_short

    # --- PHASE 3: EXECUTION ---
    exec_state = _check_omega_triggers(levels, raw_candles, kinetic_display["protocol"], start_time_filter=anchor_ts)

    status = "STANDBY"
    active_side = "NONE"

    if exec_state["action"] == "GO":
        status = "EXECUTING"
        active_side = exec_state["side"]
        kinetic_display = kinetic_long if active_side == "LONG" else kinetic_short
        
        if (int(now_utc.timestamp()) - exec_state["trigger_time"]) > 900:
            status = "ACTIVE"

    # Plans
    plan_long = _calc_execution_plan(bo, r30_low, dr, ds, "LONG", kinetic_long["protocol"], kinetic_long["force_align"])
    plan_short = _calc_execution_plan(bd, r30_high, dr, ds, "SHORT", kinetic_short["protocol"], kinetic_short["force_align"])
    plan_long["stop"] = r30_low
    plan_short["stop"] = r30_high

    if active_plan := (plan_long if active_side == "LONG" else plan_short):
        if active_plan.get("protocol_display") and active_plan["valid"]:
            kinetic_display["protocol"] = active_plan["protocol_display"]
            if active_plan.get("color_override"):
                kinetic_display["color"] = active_plan["color_override"]

    return {
        "ok": True,
        "status": status,
        "symbol": symbol,
        "price": current_price,
        "active_side": active_side,
        "bias": bias_direction, 
        "session_mode": kinetic_display["protocol"],
        "kinetic": kinetic_display,
        "plans": {"LONG": plan_long, "SHORT": plan_short}
    }