# vector.py
# ==============================================================================
# PROJECT VECTOR (v2.1) - WITH TACTICAL ADVISOR
# ==============================================================================
import asyncio
from typing import Dict, Any
import battlebox_pipeline
import session_manager

# 1. KINETIC MATH ENGINE
def _calculate_vector_kinetics(anchor_price, levels, context):
    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    if dr == 0 or ds == 0: 
        return {"score": 0, "wind_score": 0, "hull_score": 0, "energy_score": 0, "status": "OFFLINE"}

    # Energy
    range_size = abs(dr - ds)
    bps = (range_size / anchor_price) * 10000 if anchor_price > 0 else 500
    e_val = 30 if bps < 100 else (15 if bps < 250 else 0)

    # Wind
    weekly = context.get("weekly_force", "NEUTRAL")
    slope = float(levels.get("slope", 0.0))
    w_score = 0
    if (slope > 0.1 and weekly == "BULLISH") or (slope < -0.1 and weekly == "BEARISH"): w_score += 10
    if abs(slope) > 0.2: w_score += 10
        
    # Structure
    struct = float(levels.get("structure_score", 0.0))
    h_val = 20 if struct > 0.7 else (10 if struct > 0.4 else 0)

    # Space (Baseline)
    s_val = 15 

    return {
        "score": e_val + s_val + w_score + h_val,
        "wind_score": w_score,
        "hull_score": h_val,
        "energy_score": e_val,
        "atr": float(levels.get("atr", 0)) or (anchor_price * 0.01)
    }

# 2. VECTOR SWITCH
def _determine_vector_mode(kinetics):
    wind = kinetics["wind_score"]
    score = kinetics["score"]
    hull = kinetics["hull_score"]
    
    if score < 50 or hull < 10:
        return "GROUNDED", "CONDITIONS POOR. STAND DOWN.", "RED"
    
    if wind >= 15:
        return "VELOCITY", "WIND STRONG (TAILWIND). TRUST THE BREAKOUT.", "CYAN"
    else:
        return "VORTEX", "WIND WEAK (HEADWIND). FADE THE BREAKOUT (TRAP).", "AMBER"

# 3. MISSION PLANNER
def _generate_vector_plan(mode, levels):
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    
    plan = {"valid": False, "bias": "NEUTRAL", "entry": 0, "stop": 0, "targets": [0,0,0]}
    
    if mode == "GROUNDED": return plan

    if mode == "VELOCITY":
        dist_up = abs(dr - bo)
        dist_dn = abs(ds - bd)
        if dist_up > dist_dn:
            risk = abs(bo - bd)
            plan = {"valid": True, "bias": "LONG", "entry": bo, "stop": bd, "targets": [bo+risk, bo+2*risk, bo+4*risk]}
        else:
            risk = abs(bo - bd)
            plan = {"valid": True, "bias": "SHORT", "entry": bd, "stop": bo, "targets": [bd-risk, bd-2*risk, bd-4*risk]}

    elif mode == "VORTEX":
        mid = (bo + bd) / 2
        risk = abs(bo - bd) * 0.5
        if (mid - ds) < (dr - mid): 
            plan = {"valid": True, "bias": "LONG", "entry": bd, "stop": bd-risk, "targets": [bo, dr, dr+risk]}
        else:
            plan = {"valid": True, "bias": "SHORT", "entry": bo, "stop": bo+risk, "targets": [bd, ds, ds-risk]}
            
    return plan

# 4. TACTICAL ADVISOR (New Feature)
def _generate_tactical_advice(kinetics, mode):
    """
    Determines the Execution Style (Scalp vs Guard).
    """
    if mode == "GROUNDED":
        return {"title": "STAND DOWN", "desc": "No valid entry. Preserve capital.", "color": "RED"}
    
    score = kinetics["score"]
    energy = kinetics["energy_score"]
    hull = kinetics["hull_score"]
    
    if score >= 70 and hull >= 20:
        return {
            "title": "💣 TRIPWIRE (LIMIT ORDER)", 
            "desc": "Structure is Solid. Place pending limit order at Entry. Let market come to you.",
            "color": "GOLD"
        }
    elif score >= 60 and energy >= 20:
        return {
            "title": "⚡ 3-MINUTE SCALP", 
            "desc": "High Energy detected. 5m is too slow. Enter on confirmed 3m Close.",
            "color": "CYAN"
        }
    else:
        return {
            "title": "🛡️ 5-MINUTE GUARD", 
            "desc": "Standard Protocol. Wait for full 5m candle close to confirm entry.",
            "color": "GRAY"
        }

# 5. KEY GENERATOR
def _generate_mission_key(plan, mode):
    if not plan["valid"]: return f"NEUTRAL|WEAK|0|0|0|0|0"
    status = "STRONG" if mode == "VELOCITY" else "WEAK"
    return f"{plan['bias']}|{status}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}"

# 6. API ENDPOINT
async def get_vector_intel(symbol="BTCUSDT", session_id="us_ny_futures"):
    data = await battlebox_pipeline.get_live_battlebox(symbol, "MANUAL", manual_id=session_id)
    if data.get("status") in ["ERROR", "CALIBRATING"]: return {"ok": True, "status": "CALIBRATING"}

    price = float(data.get("price", 0))
    levels = data.get("battlebox", {}).get("levels", {})
    context = data.get("battlebox", {}).get("context", {})
    
    kinetics = _calculate_vector_kinetics(price, levels, context)
    mode, advice, color = _determine_vector_mode(kinetics)
    plan = _generate_vector_plan(mode, levels)
    tactic = _generate_tactical_advice(kinetics, mode)
    m_key = _generate_mission_key(plan, mode)
    
    return {
        "ok": True, "symbol": symbol, "price": price,
        "vector": {"mode": mode, "color": color, "advice": advice},
        "tactic": tactic, # PASSED TO FRONTEND
        "plan": plan, "levels": levels, "mission_key": m_key
    }