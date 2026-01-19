# project_omega.py
# ==============================================================================
# PROJECT OMEGA SPECIALIST (v16.0 - ENLIGHTENED & DATA-DRIVEN)
# ==============================================================================
# STRATEGY:
# - Target: US NY FUTURES (Focused)
# - Logic:
#    1. ASK PHASE 1 (Session Manager): "Are we open?"
#    2. IF OPEN: Run Phase 2 (Math) & Phase 3 (Execution).
#    3. IF CLOSED: Return clean "CLOSED" packet immediately.
#    4. DATA FILTER: Ignore any candles before the Session Start.
#
# UPGRADES (v16):
# - Now consumes REAL ATR, SLOPE, and STRUCTURE SCORE from the Pipeline.
# - No more guessing "1%" for ATR.
# ==============================================================================

from __future__ import annotations
from typing import Dict, Any, List
from datetime import datetime, timezone
import session_manager
import battlebox_pipeline

# ----------------------------
# 1. KINETIC STRATEGY (LOCKED MATH)
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
        return {
            "total_score": 0,
            "protocol": "CALIBRATING",
            "color": "GRAY",
            "instruction": "WAITING FOR LEVELS",
            "brief": "Pipeline building...",
            "force_align": False
        }

    # --- 1. ENERGY (30pts) ---
    # Formula: (Range / Price) * 10,000 = Basis Points
    range_size = abs(dr - ds)
    bps = (range_size / anchor_price) * 10000 if anchor_price > 0 else 500

    if bps > 350:
        is_blocked = True
        block_reason = f"EXHAUSTED ({int(bps)}bps)"
        score = 0
    elif bps < 100: # Tightened per Audit Findings
        score += 30
        breakdown['energy'] = f"SUPER COILED ({int(bps)}bps)"
    elif bps < 200:
        score += 15
        breakdown['energy'] = f"STANDARD ({int(bps)}bps)"
    else:
        score += 0
        breakdown['energy'] = f"LOOSE ({int(bps)}bps)"

    # --- 2. SPACE (30pts) ---
    # UPGRADE: Use REAL ATR from Pipeline (default to 1% only if missing)
    atr = float(levels.get("atr", 0))
    if atr == 0: atr = anchor_price * 0.01 # Fallback

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

    # --- 3. MOMENTUM (20pts) ---
    # UPGRADE: Incorporate Slope + Weekly Force
    weekly_force = context.get("weekly_force", "NEUTRAL")
    slope_score = float(levels.get("slope", 0.0)) # From -1.0 to 1.0

    is_aligned = False
    mom_points = 0

    # Weekly Alignment (10pts)
    if (side == "LONG" and weekly_force == "BULLISH") or \
       (side == "SHORT" and weekly_force == "BEARISH"):
        is_aligned = True
        mom_points += 10

    # Slope Alignment (10pts)
    if (side == "LONG" and slope_score > 0.2) or \
       (side == "SHORT" and slope_score < -0.2):
        mom_points += 10

    score += mom_points
    breakdown['momentum'] = f"ALIGNED ({weekly_force}/{slope_score:.1f})" if is_aligned else f"NEUTRAL ({weekly_force})"

    # --- 4. STRUCTURE (10pts) ---
    # UPGRADE: Read real structure score
    structure_score = float(levels.get("structure_score", 0.0))

    if structure_score > 0.5:
        score += 10
        breakdown['structure'] = f"SOLID ({structure_score:.1f})"
    else:
        score += 0
        breakdown['structure'] = f"MESSY ({structure_score:.1f})"

    # --- 5. LOCATION (10pts) ---
    dist_at_open = abs(anchor_price - trigger)
    if dist_at_open < (atr * 0.5): # Tightened to 0.5 ATR
        score += 10
        breakdown['location'] = "PRIMED (AT OPEN)"
    else:
        score += 0
        breakdown['location'] = "WIDE (AT OPEN)"

    # --- 6. TIME ---
    breakdown['time'] = "KILL ZONE (BASE)"

    # --- CLASSIFICATION ---
    brief = ""
    color = "GRAY"

    if is_blocked:
        protocol = "BLOCKED"
        color = "RED"
        instruction = f"⛔ STAND DOWN. {block_reason}"
        brief = "Market exhausted. High chop risk."
    elif score >= 75: # Adjusted for new scoring model
        protocol = "SUPERSONIC"
        color = "CYAN"
        instruction = "🔥 MOMENTUM OVERRIDE."
        brief = "Blue Sky Protocol Active."
    elif score >= 50:
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
# 2. EXECUTION MATH (LIVE OVERLAYS)
# ----------------------------
def _calc_execution_plan(
    entry: float, stop: float, dr: float, ds: float,
    side: str, mode: str, force_align: bool
) -> Dict[str, Any]:

    safe_return = {
        "trigger": 0, "targets": [], "stop": 0, "valid": False,
        "bank_rule": "--", "be_trigger": 0,
        "primary_target": "--", "reason": "Waiting for Data",
        "protocol_display": mode, "color_override": None
    }

    if entry <= 0 or stop <= 0: return safe_return
    risk = abs(entry - stop)
    if risk == 0: return safe_return

    # Blocking Logic
    min_req_dist = risk * 1.0
    dist_to_wall = abs(dr - entry) if side == "LONG" else abs(ds - entry)

    if mode != "SUPERSONIC" and dist_to_wall < min_req_dist:
        return {
            "trigger": entry, "targets": [], "stop": 0, "valid": False,
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
            color_override = "SUPERNOVA"
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
        "trigger": entry,
        "targets": targets,
        "stop": stop,
        "valid": True,
        "primary_target": primary_target,
        "bank_rule": bank_rule,
        "be_trigger": int(be_trigger),
        "reason": reason,
        "protocol_display": protocol_display,
        "color_override": color_override
    }

# ----------------------------
# 3. INTERNAL EXECUTION LOGIC (PHASE 3)
# ----------------------------
def _check_omega_triggers(
    levels: Dict[str, float],
    candles: List[Dict[str, Any]],
    mode: str,
    start_time_filter: int
) -> Dict[str, Any]:
    """
    CRITICAL RULE: We only look at candles that happened AFTER the Official Session Start.
    We get 'start_time_filter' from the Session Manager via the Pipeline.
    """
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))

    if not candles:
        return {"action": "STANDBY", "side": "NONE", "trigger_time": 0}

    require_close = True
    if "SUPERSONIC" in mode or "SUPERNOVA" in mode:
        require_close = False

    action = "STANDBY"
    side = "NONE"
    trigger_time = 0

    for c in candles:
        t = int(c["time"])

        # --- HIERARCHY CHECK (THE BOSS) ---
        # If this candle is older than the Session Manager's start time, it does not exist.
        if t < start_time_filter:
            continue
        # ----------------------------------

        high = float(c["high"])
        low = float(c["low"])
        close = float(c["close"])

        # LONG CHECK
        if require_close:
            if close > bo:
                action = "GO"; side = "LONG"; trigger_time = t; break
        else:
            if high >= bo:
                action = "GO"; side = "LONG"; trigger_time = t; break

        # SHORT CHECK
        if require_close:
            if close < bd:
                action = "GO"; side = "SHORT"; trigger_time = t; break
        else:
            if low <= bd:
                action = "GO"; side = "SHORT"; trigger_time = t; break

    return {"action": action, "side": side, "trigger_time": trigger_time}


async def get_omega_status(
    symbol: str = "BTCUSDT",
    session_id: str = "us_ny_futures",
    ferrari_mode: bool = False,
    force_time_utc: str = None,
    force_price: float = None
) -> Dict[str, Any]:

    # 1. SET TARGET: We only care about NY Futures (08:30 - 16:00 ET)
    session_id = "us_ny_futures"

    if force_time_utc:
        t_now = datetime.now(timezone.utc)
        fake_dt = datetime.strptime(force_time_utc, "%H:%M")
        now_utc = t_now.replace(hour=fake_dt.hour, minute=fake_dt.minute, second=0, microsecond=0)
        is_simulation = True
    else:
        now_utc = datetime.now(timezone.utc)
        is_simulation = False

    # 2. ASK THE BOSS (Session Manager)
    # We retrieve the official config and anchor time.
    session_config = session_manager.get_session_config(session_id)
    anchor_ts = session_manager.anchor_ts_for_utc_date(session_config, now_utc)
    anchor_dt = datetime.fromtimestamp(anchor_ts, timezone.utc)

    # Calculate elapsed time relative to the OFFICIAL ANCHOR
    elapsed = (now_utc - anchor_dt).total_seconds() / 3600.0

    # HARD RULE: If outside the 7.5 hour window, we are CLOSED.
    # We ignore the '23h' config because we are the Day Session Specialist.
    is_session_closed = elapsed > 7.5 or elapsed < 0

    # 3. FETCH DATA (Pipeline)
    pipeline_data = await battlebox_pipeline.get_live_battlebox(
        symbol=symbol, session_mode="MANUAL", manual_id=session_id
    )
    real_price = float(pipeline_data.get("price", 0.0))
    current_price = force_price if force_price is not None else real_price

    # 4. ENFORCE HIERARCHY: CLOSED STATE
    if is_session_closed:
        return {
            "ok": True,
            "status": "CLOSED",
            "symbol": symbol,
            "price": current_price,
            "active_side": "NONE",
            "next_open": anchor_dt.strftime("%H:%M UTC"),
            "kinetic": {
                "total_score": 0,
                "protocol": "MARKET CLOSED",
                "color": "GRAY",
                "instruction": f"SESSION OPENS 08:30 ET",
                "brief": "Waiting for Session Open...",
                "breakdown": {}
            },
            "plans": {"LONG": {}, "SHORT": {}}
        }

    # 5. CALIBRATION STATE (08:30 - 09:00 ET)
    if pipeline_data.get("status") == "CALIBRATING":
        return {
            "ok": True,
            "status": "CALIBRATING",
            "price": current_price,
            "kinetic": {
                "total_score": 0,
                "protocol": "CALIBRATING",
                "color": "GRAY",
                "instruction": "WAITING FOR 30M LOCK",
                "brief": "System calibrating.",
                "breakdown": {}
            },
            "plans": {"LONG": {}, "SHORT": {}}
        }

    # 6. LIVE OPERATION (09:00 - 16:00 ET)
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

    # 5. AUTO-SELECT SIDE
    dist_long = abs(current_price - bo)
    dist_short = abs(current_price - bd)
    closest_side = "LONG" if dist_long < dist_short else "SHORT"
    calc_side = closest_side

    # 6. PHASE 2 (MATH)
    # This now runs the UPGRADED MATH using real ATR/Slope/Structure from levels
    kinetic = _calculate_locked_strategy(anchor_price, levels, context, shelves, calc_side)

    # 7. PHASE 3 (EXECUTION - WITH HIERARCHY FILTER)
    exec_state = _check_omega_triggers(
        levels, raw_candles, kinetic["protocol"], start_time_filter=anchor_ts
    )

    status = "STANDBY"
    active_side = closest_side

    if exec_state["action"] == "GO":
        status = "EXECUTING"
        active_side = exec_state["side"]
        calc_side = active_side
        kinetic = _calculate_locked_strategy(anchor_price, levels, context, shelves, active_side)

        trigger_ts = exec_state["trigger_time"]
        current_ts = int(now_utc.timestamp())

        if (current_ts - trigger_ts) > 900:
            status = "ACTIVE"

    # UI Warning
    if elapsed > 2.5:
        kinetic["breakdown"]["time"] = "LATE (CAUTION)"
    else:
        kinetic["breakdown"]["time"] = "PRIME (OPEN)"

    # Plans
    plan_long = _calc_execution_plan(
        bo, r30_low, dr, ds, "LONG", kinetic["protocol"], kinetic["force_align"]
    )
    plan_short = _calc_execution_plan(
        bd, r30_high, dr, ds, "SHORT", kinetic["protocol"], kinetic["force_align"]
    )

    plan_long["stop"] = r30_low
    plan_short["stop"] = r30_high

    if active_plan := (plan_long if calc_side == "LONG" else plan_short):
        if active_plan.get("protocol_display") and active_plan["valid"]:
            kinetic["protocol"] = active_plan["protocol_display"]
            if active_plan.get("color_override"):
                kinetic["color"] = active_plan["color_override"]

    return {
        "ok": True,
        "status": status,
        "symbol": symbol,
        "price": current_price,
        "active_side": active_side,
        "session_mode": kinetic["protocol"],
        "is_simulation": is_simulation,
        "simulated_time": now_utc.strftime("%H:%M UTC") if is_simulation else None,
        "kinetic": kinetic,
        "plans": {"LONG": plan_long, "SHORT": plan_short}
    }