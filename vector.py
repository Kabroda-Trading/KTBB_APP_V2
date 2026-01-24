# vector.py
# ==============================================================================
# PROJECT VECTOR (v2.4 FERRARI) - IGNITION + BIAS GATE
# ==============================================================================
import asyncio
from typing import Dict, Any
import battlebox_pipeline
import session_manager

# 1. KINETIC MATH
def _calculate_vector_kinetics(anchor_price, levels, context):
    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    if dr == 0 or ds == 0: 
        return {"score": 0, "wind_score": 0, "hull_score": 0, "energy_score": 0, "weekly_bias": "NEUTRAL"}

    # A. ENERGY (Volatility)
    range_size = abs(dr - ds)
    bps = (range_size / anchor_price) * 10000 if anchor_price > 0 else 500
    e_val = 0
    if bps < 100: e_val = 30   # COILED (Ignition Potential)
    elif bps < 250: e_val = 15 # STANDARD
    else: e_val = 0            # EXHAUSTED

    # B. WIND (Live Momentum + Bias)
    weekly = context.get("weekly_force", "NEUTRAL") 
    slope = float(levels.get("slope", 0.0))         
    
    w_score = 0
    # Alignment Bonus
    if (slope > 0.1 and weekly == "BULLISH") or (slope < -0.1 and weekly == "BEARISH"): w_score += 10
    # Raw Force Bonus
    if abs(slope) > 0.2: w_score += 10
        
    # C. STRUCTURE & SPACE
    struct = float(levels.get("structure_score", 0.0))
    h_val = 20 if struct > 0.7 else (10 if struct > 0.4 else 0)
    s_val = 15 

    return {
        "score": e_val + s_val + w_score + h_val,
        "wind_score": w_score,
        "hull_score": h_val,
        "energy_score": e_val,
        "weekly_bias": weekly
    }

# 2. VECTOR SWITCH
def _determine_vector_mode(kinetics):
    wind = kinetics["wind_score"]
    score = kinetics["score"]
    energy = kinetics["energy_score"]
    hull = kinetics["hull_score"]
    
    # SAFETY VALVE
    if score < 50 or hull < 10:
        return "GROUNDED", "CONDITIONS POOR. STAND DOWN.", "RED"
    
    # VELOCITY: Trend is moving
    if wind >= 15:
        return "VELOCITY", "WIND STRONG. RIDE THE MOMENTUM.", "CYAN"
        
    # IGNITION: Coiled Energy (The Whale Catcher)
    if energy == 30: 
        return "IGNITION", "MARKET COILED. VOLATILITY IMMINENT.", "GOLD"

    # VORTEX: Trap Logic
    return "VORTEX", "MOMENTUM WEAK. FADE THE BREAKOUT.", "AMBER"

# 3. MISSION PLANNER (Bias Gated)
def _generate_vector_plan(mode, levels, bias_direction):
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    
    plan = {"valid": False, "bias": "NEUTRAL", "entry": 0, "stop": 0, "targets": [0,0,0]}
    if mode == "GROUNDED": return plan

    # BIAS FILTER
    allowed = "BOTH"
    if bias_direction == "BULLISH": allowed = "LONG"
    if bias_direction == "BEARISH": allowed = "SHORT"

    # VELOCITY / IGNITION (Trend)
    if mode in ["VELOCITY", "IGNITION"]:
        dist_up = abs(dr - bo)
        dist_dn = abs(ds - bd)
        trend_dir = "LONG" if dist_up > dist_dn else "SHORT"
        
        if allowed != "BOTH" and trend_dir != allowed: return plan # GATED

        if trend_dir == "LONG":
            risk = abs(bo - bd)
            plan = {"valid": True, "bias": "LONG", "entry": bo, "stop": bd, "targets": [bo+risk, bo+2*risk, bo+4*risk]}
        else:
            risk = abs(bo - bd)
            plan = {"valid": True, "bias": "SHORT", "entry": bd, "stop": bo, "targets": [bd-risk, bd-2*risk, bd-4*risk]}

    # VORTEX (Trap)
    elif mode == "VORTEX":
        mid = (bo + bd) / 2
        trap_dir = "LONG" if (mid - ds) < (dr - mid) else "SHORT" 
        
        if allowed != "BOTH" and trap_dir != allowed: return plan # GATED

        risk = abs(bo - bd) * 0.5
        if trap_dir == "LONG":
            plan = {"valid": True, "bias": "LONG", "entry": bd, "stop": bd-risk, "targets": [bo, dr, dr+risk]}
        else:
            plan = {"valid": True, "bias": "SHORT", "entry": bo, "stop": bo+risk, "targets": [bd, ds, ds-risk]}
            
    return plan

# 4. TACTICAL ADVISOR
def _generate_tactical_advice(kinetics, mode, plan):
    if not plan["valid"]:
        return {"title": "STAND DOWN", "desc": f"Setup conflicts with {kinetics['weekly_bias']} Bias.", "color": "RED"}
    
    if mode == "IGNITION":
        return {"title": "⚠️ VOLATILITY ALERT", "desc": "Super Coiled. Wait for aggressive confirmation.", "color": "GOLD"}

    score = kinetics["score"]
    energy = kinetics["energy_score"]
    
    if score >= 65 and energy >= 15:
        return {"title": "⚡ 3-MINUTE SCALP", "desc": "High Confidence. Enter on 3m Close.", "color": "CYAN"}
    else:
        return {"title": "🛡️ 5-MINUTE GUARD", "desc": "Lower Score (<65). Use 5m Close & Half Risk.", "color": "GRAY"}

# 5. KEY GEN
def _generate_mission_key(plan, mode):
    if not plan["valid"]: return f"NEUTRAL|WEAK|0|0|0|0|0"
    status = "STRONG" if mode in ["VELOCITY", "IGNITION"] else "WEAK"
    return f"{plan['bias']}|{status}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}"

# 6. API
async def get_vector_intel(symbol="BTCUSDT", session_id="us_ny_futures"):
    data = await battlebox_pipeline.get_live_battlebox(symbol, "MANUAL", manual_id=session_id)
    if data.get("status") in ["ERROR", "CALIBRATING"]: return {"ok": True, "status": "CALIBRATING"}

    price = float(data.get("price", 0))
    levels = data.get("battlebox", {}).get("levels", {})
    context = data.get("battlebox", {}).get("context", {})
    
    kinetics = _calculate_vector_kinetics(price, levels, context)
    mode, advice, color = _determine_vector_mode(kinetics)
    plan = _generate_vector_plan(mode, levels, kinetics["weekly_bias"])
    tactic = _generate_tactical_advice(kinetics, mode, plan)
    
    if not plan["valid"] and mode != "GROUNDED":
        mode = "GROUNDED"
        advice = f"MISALIGNED. BIAS IS {kinetics['weekly_bias']}."
        color = "RED"

    m_key = _generate_mission_key(plan, mode)
    
    return {
        "ok": True, "symbol": symbol, "price": price,
        "vector": {"mode": mode, "color": color, "advice": advice, "score": kinetics["score"]},
        "tactic": tactic, "plan": plan, "levels": levels, "mission_key": m_key
    }