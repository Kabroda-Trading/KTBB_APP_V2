# project_omega.py
# ==============================================================================
# PROJECT OMEGA ENGINE (KINETIC AUTHORITY)
# ==============================================================================
# - Logic: "Current Mode" is now exclusively driven by Kinetic Readiness.
# - Output: SUPERSONIC, SNIPER, or DOGFIGHT.
# ==============================================================================

from __future__ import annotations
from typing import Dict, Any, List

import battlebox_pipeline
import session_manager

# ----------------------------
# KINETIC MATH ENGINE
# ----------------------------
def _calc_kinetic_score(
    anchor_price: float, 
    levels: Dict[str, float], 
    context: Dict[str, Any], 
    shelves: Dict[str, Any],
    side: str
) -> Dict[str, Any]:
    
    score = 0
    breakdown = {}
    
    # 1. ENERGY (30 pts)
    dr = levels.get("daily_resistance", 0)
    ds = levels.get("daily_support", 0)
    range_size = abs(dr - ds)
    bps = (range_size / anchor_price) * 10000 if anchor_price > 0 else 500
    
    energy_desc = ""
    if bps < 150: 
        score += 30; breakdown['energy'] = "COILED (+30)"
        energy_desc = "⚡ Energy is super-coiled."
    elif bps < 300: 
        score += 15; breakdown['energy'] = "STANDARD (+15)"
        energy_desc = "🔋 Energy reserves are standard."
    else: 
        score += 0; breakdown['energy'] = "EXHAUSTED (+0)"
        energy_desc = "🪫 Market energy is exhausted."

    # 2. SPACE (30 pts)
    atr = levels.get("atr", range_size * 0.25) 
    if atr == 0: atr = anchor_price * 0.01 
    
    trigger = levels.get("breakout_trigger") if side == "LONG" else levels.get("breakdown_trigger")
    target = dr if side == "LONG" else ds
    gap = abs(target - trigger)
    r_multiple = gap / atr
    
    space_desc = ""
    if r_multiple > 2.0:
        score += 30; breakdown['space'] = "BLUE SKY (+30)"
        space_desc = "🚀 The runway is wide open."
    elif r_multiple > 1.0:
        score += 15; breakdown['space'] = "GRIND (+15)"
        space_desc = "🚧 Structure is visible overhead."
    else:
        score += 0; breakdown['space'] = "BLOCKED (+0)"
        space_desc = "🛑 The path is blocked by structure."

    # 3. MOMENTUM (20 pts)
    slope = float(context.get("slope_score", 0))
    if slope > 0.2: score += 20; breakdown['momentum'] = "HELPING (+20)"
    elif slope > -0.2: score += 10; breakdown['momentum'] = "NEUTRAL (+10)"
    else: score += 0; breakdown['momentum'] = "FIGHTING (+0)"

    # 4. STRUCTURE (10 pts)
    shelf_strength = float(shelves.get("strength", 0))
    if shelf_strength > 0.5: score += 10; breakdown['structure'] = "SOLID (+10)"
    else: score += 0; breakdown['structure'] = "MESSY (+0)"

    # 5. LOCATION (10 pts)
    dist_to_trigger = abs(price - trigger) if 'price' in locals() else 0 # Safety if needed, but we use anchor mostly
    # For static score, we assume "Primed" if open is within range.
    # Actually, let's use the anchor proximity to trigger.
    bo = levels.get("breakout_trigger")
    bd = levels.get("breakdown_trigger")
    dist_long = abs(anchor_price - bo)
    dist_short = abs(anchor_price - bd)
    closest_dist = min(dist_long, dist_short)

    if closest_dist < (atr * 0.5): score += 10; breakdown['location'] = "PRIMED (+10)"
    else: score += 0; breakdown['location'] = "CHASING (+0)"

    # PROTOCOL ROUTER (The Authority)
    brief = f"{energy_desc} {space_desc}"
    
    if score >= 71:
        protocol = "SUPERSONIC"
        color = "CYAN"
        instruction = "🔥 MOMENTUM OVERRIDE ACTIVE. DEPLOY AGGRESSIVE."
        brief += " Volatility expected to be impulsive. Strike fast."
    elif score >= 41:
        protocol = "SNIPER"
        color = "GREEN"
        instruction = "⌖ WAIT FOR CONFIRMED CLOSE. PRECISION ONLY."
        brief += " Price action is technical. Adhere to strict structure rules."
    else:
        protocol = "DOGFIGHT"
        color = "AMBER"
        instruction = "🛡️ DEFENSIVE POSTURE. SHIELDS UP."
        brief += " Environment is hostile. Protect capital at all costs."

    return {
        "total_score": score,
        "protocol": protocol,
        "color": color,
        "instruction": instruction,
        "brief": brief,
        "breakdown": breakdown
    }

def _calc_targets(entry: float, stop: float, dr: float, ds: float, side: str) -> Dict[str, Any]:
    if entry <= 0: return {"targets": [], "mode": "WAITING"}
    
    # Just used for target calculation scaling, not mode determination anymore
    is_supersonic = False
    if side == "LONG" and entry > dr: is_supersonic = True
    if side == "SHORT" and entry < ds: is_supersonic = True
    
    energy = abs(dr - ds)
    if energy == 0: energy = entry * 0.01
    
    targets = []
    if side == "LONG":
        shield = entry + (energy * 0.3) 
        if is_supersonic:
            t1 = entry + (energy * 0.6); t2 = entry + (energy * 1.2); t3 = entry + (energy * 2.0)
        else: 
            t1 = dr; t2 = dr + (energy * 0.5); t3 = dr + energy
        targets = [int(shield), int(t1), int(t2), int(t3)]
    else: 
        shield = entry - (energy * 0.3)
        if is_supersonic:
            t1 = entry - (energy * 0.6); t2 = entry - (energy * 1.2); t3 = entry - (energy * 2.0)
        else: 
            t1 = ds; t2 = ds - (energy * 0.5); t3 = ds - energy
        targets = [int(shield), int(t1), int(t2), int(t3)]

    return {"targets": targets, "stop": stop}

async def get_omega_status(
    symbol: str = "BTCUSDT",
    session_id: str = "us_ny_futures",
    ferrari_mode: bool = False,
) -> Dict[str, Any]:
    current_price = 0.0
    try:
        pipeline_data = await battlebox_pipeline.get_live_battlebox(
            symbol=symbol, session_mode="MANUAL", manual_id=session_id
        )
        if pipeline_data.get("status") == "ERROR":
             pipeline_data = await battlebox_pipeline.get_live_battlebox(symbol)

        if pipeline_data.get("status") == "ERROR":
            return {"ok": False, "status": "OFFLINE", "msg": "Pipeline Error"}

        current_price = pipeline_data.get("price", 0.0)
        box = pipeline_data.get("battlebox", {})
        levels = box.get("levels", {})
        context = box.get("context", {})
        shelves = box.get("htf_shelves", {})
        session_meta = box.get("session", {})
        
        anchor_price = levels.get("session_open_price") or current_price
        
        bo = float(levels.get("breakout_trigger", 0.0))
        bd = float(levels.get("breakdown_trigger", 0.0))
        dr = float(levels.get("daily_resistance", 0.0))
        ds = float(levels.get("daily_support", 0.0))
        r30_high = float(levels.get("range30m_high", 0.0))
        r30_low = float(levels.get("range30m_low", 0.0))
        
        plan_long = _calc_targets(bo, r30_low, dr, ds, "LONG")
        plan_short = _calc_targets(bd, r30_high, dr, ds, "SHORT")

        status = "STANDBY"
        active_side = "NONE"
        near_radius = 0.0007 if ferrari_mode else 0.0010

        if bo > 0 and bd > 0:
            if current_price > bo: status = "EXECUTING"; active_side = "LONG"
            elif current_price < bd: status = "EXECUTING"; active_side = "SHORT"
            else:
                if abs(current_price - bo) / bo < near_radius: status = "LOCKED"; active_side = "LONG"
                elif abs(current_price - bd) / bd < near_radius: status = "LOCKED"; active_side = "SHORT"
        
        closest_side = "LONG" if (current_price >= (bo + bd)/2) else "SHORT"
        
        # 1. CALCULATE KINETIC SCORE
        kinetic = _calc_kinetic_score(anchor_price, levels, context, shelves, active_side if active_side != "NONE" else closest_side)
        
        # 2. SET SESSION MODE FROM KINETIC (The New Authority)
        session_mode = kinetic["protocol"] # SUPERSONIC, SNIPER, or DOGFIGHT

        return {
            "ok": True,
            "status": status,
            "symbol": symbol,
            "session_id": session_id,
            "ferrari_mode": bool(ferrari_mode),
            "price": current_price,
            "active_side": active_side,
            "closest_side": closest_side,
            "session_mode": session_mode, # Controlled by Kinetic now
            "kinetic": kinetic,
            "plans": {
                "LONG": {"trigger": bo, "stop": r30_low, "targets": plan_long["targets"]},
                "SHORT": {"trigger": bd, "stop": r30_high, "targets": plan_short["targets"]}
            },
            "telemetry": {
                "session_state": "ACTIVE",
                "anchor_ts": session_meta.get("anchor_ts"),
                "verification": {
                    "r30_high": r30_high, "r30_low": r30_low, 
                    "daily_res": dr, "daily_sup": ds,
                    "bo": bo, "bd": bd 
                },
            }
        }
    except Exception as e:
        return {"ok": False, "status": "ERROR", "price": current_price, "msg": str(e)}