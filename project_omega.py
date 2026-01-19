# project_omega.py
# ==============================================================================
# PROJECT OMEGA SPECIALIST (v9.3 INDEPENDENT GENERAL)
# ==============================================================================
# CORE ARCHITECTURE:
# 1. DATA: Gets Session, Levels, and Raw Candles from Pipeline.
# 2. MATH: Calculates Kinetic Score based on LOCKED Session Open (Anchor).
# 3. EXECUTION: Internally watches candles to trigger "GO" signals based on Mode.
# ==============================================================================

from __future__ import annotations
from typing import Dict, Any, List
from datetime import datetime, timezone
import session_manager 
import battlebox_pipeline

# ----------------------------
# 1. KINETIC STRATEGY (THE LOCKED CALCULATOR)
# ----------------------------
def _calculate_locked_strategy(
    anchor_price: float, 
    levels: Dict[str, float], 
    context: Dict[str, Any], 
    shelves: Dict[str, Any],
    side: str
) -> Dict[str, Any]:
    """
    Calculates the 'Session Mode' based on the moment of truth (Lock Time).
    This output is STABLE for the entire session.
    """
    score = 0
    breakdown = {}
    is_blocked = False
    block_reason = ""

    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    
    if dr == 0 or ds == 0:
        return {"total_score": 0, "protocol": "CALIBRATING", "color": "GRAY", "instruction": "WAITING FOR LEVELS", "brief": "Pipeline building...", "force_align": False}

    # --- 1. ENERGY (30pts) ---
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

    # --- 2. SPACE (30pts) ---
    atr = float(levels.get("atr", 0) or anchor_price * 0.01)
    trigger = float(levels.get("breakout_trigger", 0)) if side == "LONG" else float(levels.get("breakdown_trigger", 0))
    target = dr if side == "LONG" else ds
    gap = abs(target - trigger)
    r_multiple = gap / atr if atr > 0 else 0
    
    if r_multiple > 2.0: score += 30; breakdown['space'] = f"SUPER SONIC ({r_multiple:.1f}R)"
    elif r_multiple > 1.0: score += 15; breakdown['space'] = f"GRIND ({r_multiple:.1f}R)"
    else: score += 0; breakdown['space'] = "BLOCKED (<1.0R)"

    # --- 3. MOMENTUM (20pts) - SOURCE OF TRUTH (WEEKLY) ---
    # We grab the "weekly_force" directly from the pipeline context.
    # This was calculated using the '1w' candle fetch.
    weekly_force = context.get("weekly_force", "NEUTRAL") 
    
    is_aligned = False
    if (side == "LONG" and weekly_force == "BULLISH") or \
       (side == "SHORT" and weekly_force == "BEARISH"):
        is_aligned = True
        score += 20 # <--- MATH AUDIT: Points are now correctly added.
        breakdown['momentum'] = f"ALIGNED ({weekly_force})"
    else:
        breakdown['momentum'] = f"NEUTRAL ({weekly_force})"

    # --- 4. STRUCTURE (10pts) ---
    shelf_strength = float(shelves.get("strength", 0) or 0)
    if shelf_strength > 0.5: score += 10; breakdown['structure'] = "SOLID"
    else: score += 0; breakdown['structure'] = "MESSY"

    # --- 5. LOCATION (10pts - LOCKED) ---
    # Calculated relative to Anchor Price (Session Open), not live price.
    dist_at_open = abs(anchor_price - trigger)
    if dist_at_open < (atr * 1.0): score += 10; breakdown['location'] = "PRIMED (AT OPEN)"
    else: score += 0; breakdown['location'] = "WIDE (AT OPEN)"

    # --- 6. TIME (LOCKED) ---
    # We assume the session starts in the Kill Zone. Live execution handles lateness separately.
    breakdown['time'] = "KILL ZONE (BASE)"

    # --- CLASSIFICATION ---
    brief = ""
    if is_blocked:
        protocol = "BLOCKED"
        color = "RED"
        instruction = f"⛔ STAND DOWN. {block_reason}"
        brief = "Market exhausted. High chop risk."
    elif score >= 71:
        protocol = "SUPERSONIC"
        color = "CYAN" 
        instruction = "🔥 MOMENTUM OVERRIDE."
        brief = "Blue Sky Protocol Active."
    elif score >= 41:
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
        "total_score": score,
        "protocol": protocol,
        "color": color,
        "instruction": instruction,
        "brief": brief,
        "breakdown": breakdown,
        "force_align": is_aligned
    }

# ----------------------------
# 2. EXECUTION MATH (THE PLAN)
# ----------------------------
def _calc_execution_plan(entry: float, stop: float, dr: float, ds: float, side: str, mode: str, force_align: bool) -> Dict[str, Any]:
    # SAFE DEFAULT
    safe_return = {
        "trigger": 0, "targets": [], "stop": 0, "valid": False, 
        "bank_rule": "--", "primary_target": "--", 
        "reason": "Waiting for Data", "protocol_display": mode, "color_override": None
    }

    if entry <= 0 or stop <= 0: return safe_return
    risk = abs(entry - stop)
    if risk == 0: return safe_return

    # Blocking logic based on Locked Mode
    min_req_dist = risk * 1.0 
    dist_to_wall = abs(dr - entry) if side == "LONG" else abs(ds - entry)
    
    if mode != "SUPERSONIC" and dist_to_wall < min_req_dist:
        return {
            "trigger": entry, "targets": [], "stop": 0, "valid": False, 
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

    # PROTOCOL LOGIC
    primary_target = t1
    bank_rule = "BANK 75%"
    reason = "Standard"
    protocol_display = mode
    color_override = None

    if mode == "SUPERSONIC":
        if force_align: # SUPERNOVA
            primary_target = "OPEN (Aim T3)" 
            bank_rule = "IGNORE TP1. Trail."
            reason = "ALIGNED (Aggressive)"
            protocol_display = "SUPERNOVA"
            color_override = "SUPERNOVA" # Neon Green
        else: # HYBRID
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
        "trigger": entry, # <--- UI FIX: Sends the entry price so the cheat sheet works.
        "targets": targets, "stop": stop, "valid": True,
        "primary_target": primary_target, "bank_rule": bank_rule,
        "reason": reason, "protocol_display": protocol_display, "color_override": color_override
    }

# ----------------------------
# 3. INTERNAL EXECUTION LOGIC (STEP 3: THE ANARCHY)
# ----------------------------
# This logic is NOW housed inside Project Omega.
# It does NOT go out to 'structure_state_engine'. It decides for itself.
def _check_omega_triggers(levels: Dict[str, float], candles: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
    """
    Watches live candles against the triggers.
    Enforces the rules: Sniper = Close, Supersonic = Touch.
    """
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    
    if not candles: return {"action": "STANDBY", "side": "NONE"}

    # OMEGA RULES OF ENGAGEMENT
    # If we are in Supersonic or Supernova, we allow a TOUCH entry.
    # Otherwise (Sniper/Dogfight), we REQUIRE a 5-minute candle CLOSE.
    require_close = True
    if "SUPERSONIC" in mode or "SUPERNOVA" in mode:
        require_close = False # Aggressive Mode Active

    action = "STANDBY"
    side = "NONE"

    # Scan the candles that happened AFTER the session locked
    for c in candles:
        high = float(c["high"])
        low = float(c["low"])
        close = float(c["close"])

        # LONG CHECK
        if require_close:
            if close > bo: action = "GO"; side = "LONG"; break
        else:
            if high >= bo: action = "GO"; side = "LONG"; break
            
        # SHORT CHECK
        if require_close:
            if close < bd: action = "GO"; side = "SHORT"; break
        else:
            if low <= bd: action = "GO"; side = "SHORT"; break
            
    return {"action": action, "side": side}


async def get_omega_status(
    symbol: str = "BTCUSDT",
    session_id: str = "us_ny_futures",
    ferrari_mode: bool = False,
    force_time_utc: str = None,
    force_price: float = None 
) -> Dict[str, Any]:
    
    # 1. TIME & SIMULATION CHECK
    if force_time_utc:
        t_now = datetime.now(timezone.utc)
        fake_dt = datetime.strptime(force_time_utc, "%H:%M")
        now_utc = t_now.replace(hour=fake_dt.hour, minute=fake_dt.minute, second=0, microsecond=0)
        is_simulation = True
    else:
        now_utc = datetime.now(timezone.utc)
        is_simulation = False

    # 2. FETCH PIPELINE (LOCKED DATA)
    # The pipeline now returns 'candles' (The Ammo) which we need for Step 3.
    pipeline_data = await battlebox_pipeline.get_live_battlebox(
        symbol=symbol, session_mode="MANUAL", manual_id=session_id
    )
    
    p_status = pipeline_data.get("status")
    if p_status in ["ERROR", "OFFLINE"]: return {"ok": False, "status": "OFFLINE", "msg": "Pipeline Error"}

    # 3. CHECK CORPORATE SCHEDULE
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

    # 4. UNPACK DATA
    box = pipeline_data.get("battlebox", {})
    levels = box.get("levels", {})
    context = box.get("context", {})
    shelves = box.get("htf_shelves", {})
    raw_candles = pipeline_data.get("candles", []) # <--- STEP 3 AMMO
    
    # Anchor Price = The Truth (Session Open)
    real_price = float(pipeline_data.get("price", 0.0))
    current_price = force_price if force_price is not None else real_price
    anchor_price = float(levels.get("session_open_price") or current_price)
    
    if force_price is not None: is_simulation = True

    # Levels
    bo = float(levels.get("breakout_trigger", 0.0))
    bd = float(levels.get("breakdown_trigger", 0.0))
    dr = float(levels.get("daily_resistance", 0.0))
    ds = float(levels.get("daily_support", 0.0))
    r30_high = float(levels.get("range30m_high", 0.0))
    r30_low = float(levels.get("range30m_low", 0.0))
    
    # 5. DETERMINE ACTIVE SIDE (DEFAULT)
    closest_side = "LONG" if (current_price >= (bo + bd)/2) else "SHORT"
    calc_side = closest_side 

    # 6. RUN STEP 2: THE LOCKED MATH
    # Calculates the Kinetic Score and Mode (Sniper/Supersonic).
    # This dictates the rules for Step 3.
    kinetic = _calculate_locked_strategy(
        anchor_price, levels, context, shelves, calc_side
    )
    
    # 7. RUN STEP 3: THE LIVE EXECUTION
    # This is the Independent Logic that watches the trade triggers.
    exec_state = _check_omega_triggers(levels, raw_candles, kinetic["protocol"])
    
    status = "STANDBY"
    active_side = "NONE"
    
    if exec_state["action"] == "GO":
        status = "EXECUTING"
        active_side = exec_state["side"]
        calc_side = active_side # Force math to focus on the active side
        # Re-run math for the active side to ensure colors match
        kinetic = _calculate_locked_strategy(anchor_price, levels, context, shelves, active_side)

    # 8. APPLY LIVE FILTERS (TIME / LOCATION)
    # These update the text but do NOT change the Kinetic Score.
    anchor_ts = session_manager.anchor_ts_for_utc_date(session_config, now_utc)
    anchor_dt = datetime.fromtimestamp(anchor_ts, timezone.utc)
    elapsed = (now_utc - anchor_dt).total_seconds() / 3600.0
    
    if elapsed > 2.5:
        kinetic["breakdown"]["time"] = "LATE (CAUTION)"
    
    atr = float(levels.get("atr", 0) or anchor_price * 0.01)
    trigger_price = bo if calc_side == "LONG" else bd
    dist_now = abs(current_price - trigger_price)
    
    if dist_now > (atr * 0.5) and status == "STANDBY":
        kinetic["breakdown"]["location"] = "CHASING (LIVE)"
    elif status == "STANDBY":
        kinetic["breakdown"]["location"] = "PRIMED (LIVE)"

    # 9. PLANS
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