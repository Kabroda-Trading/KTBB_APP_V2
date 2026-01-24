# vector.py
# ==============================================================================
# PROJECT VECTOR (v2.0) - THE HYBRID ENGINE
# ==============================================================================
# 1. CORE INPUT: Phase 1 Data from Battlebox Pipeline.
# 2. LOGIC: Full Kinetic Math + Wind Switch (System 4).
# 3. OUTPUT: Velocity (Trend) vs Vortex (Trap) Mission Plans.
# 4. HANDSHAKE: Generates Mission Key for TradingView.
# ==============================================================================

import asyncio
from typing import Dict, Any
import battlebox_pipeline
import session_manager

# ------------------------------------------------------------------------------
# 1. KINETIC MATH ENGINE (Full Fidelity Copy)
# ------------------------------------------------------------------------------
def _calculate_vector_kinetics(anchor_price, levels, context):
    """
    Calculates the 4-Dimensional Kinetic Score with specific focus on WIND.
    This uses the full math from Market Radar to ensure consistency.
    """
    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    
    # Safety Check: If we don't have walls, we are offline.
    if dr == 0 or ds == 0: 
        return {
            "score": 0, 
            "wind_score": 0, 
            "hull_score": 0, 
            "energy_score": 0,
            "status": "OFFLINE"
        }

    # A. ENERGY (30pts) - Volatility
    range_size = abs(dr - ds)
    bps = (range_size / anchor_price) * 10000 if anchor_price > 0 else 500
    
    e_val = 0
    if bps < 100:
        e_val = 30 # Super Coiled
    elif bps < 250:
        e_val = 15 # Standard
    else:
        e_val = 0  # Loose/Exhausted

    # B. SPACE (30pts) - Room to Run
    atr = float(levels.get("atr", 0)) or (anchor_price * 0.01)
    # We apply a baseline space score for the general environment
    s_val = 15 

    # C. WIND (20pts) - THE CRITICAL SWITCH
    # This determines if we are in Velocity (Trend) or Vortex (Trap) mode.
    weekly = context.get("weekly_force", "NEUTRAL")
    slope = float(levels.get("slope", 0.0))
    
    w_score = 0
    # Alignment Bonus (10pts)
    if (slope > 0.1 and weekly == "BULLISH"):
        w_score += 10
    elif (slope < -0.1 and weekly == "BEARISH"):
        w_score += 10
        
    # Momentum Bonus (10pts)
    if abs(slope) > 0.2:
        w_score += 10
        
    # D. HULL (20pts) - Structure
    struct = float(levels.get("structure_score", 0.0))
    h_val = 0
    if struct > 0.7:
        h_val = 20
    elif struct > 0.4:
        h_val = 10

    # Total Score Calculation
    total_score = e_val + s_val + w_score + h_val
    
    return {
        "score": total_score,
        "wind_score": w_score,
        "hull_score": h_val,
        "energy_score": e_val,
        "atr": atr
    }

# ------------------------------------------------------------------------------
# 2. THE VECTOR SWITCH (The New Brain)
# ------------------------------------------------------------------------------
def _determine_vector_mode(kinetics):
    """
    Decides between VELOCITY (Trend) and VORTEX (Trap).
    """
    wind = kinetics["wind_score"]
    score = kinetics["score"]
    hull = kinetics["hull_score"]
    
    # DEFAULT STATE
    mode = "GROUNDED"
    advice = "CONDITIONS POOR. STAND DOWN."
    color = "RED"
    
    # SAFETY VALVE: If Score is too low, we don't trade.
    if score < 50:
        return "GROUNDED", "LOW KINETIC SCORE (<50). STAND DOWN.", "RED"
        
    # SAFETY VALVE: If Structure is broken, we don't trade.
    if hull < 10:
        return "GROUNDED", "MARKET STRUCTURE BROKEN. STAND DOWN.", "RED"
    
    # THE SWITCH: Wind determines the Strategy.
    if wind >= 15:
        # STRONG WIND -> TREND MODE
        mode = "VELOCITY"
        advice = "WIND IS STRONG (TAILWIND). TRUST THE BREAKOUT."
        color = "CYAN"
    else:
        # WEAK WIND -> TRAP MODE
        mode = "VORTEX"
        advice = "WIND IS WEAK (HEADWIND). FADE THE BREAKOUT (TRAP)."
        color = "AMBER"
            
    return mode, advice, color

# ------------------------------------------------------------------------------
# 3. MISSION PLANNER (Execution Math)
# ------------------------------------------------------------------------------
def _generate_vector_plan(mode, levels):
    """
    Generates specific Entry/Stop/Targets based on the Mode.
    """
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    
    # Default: No Trade Plan
    plan = {
        "valid": False,
        "bias": "NEUTRAL",
        "entry": 0, 
        "stop": 0, 
        "targets": [0, 0, 0]
    }
    
    if mode == "GROUNDED":
        return plan

    # --- VELOCITY MODE (Trend Following) ---
    # We look for the Breakout that aligns with the path of least resistance.
    if mode == "VELOCITY":
        dist_up = abs(dr - bo)
        dist_dn = abs(ds - bd)
        
        # If Resistance is further away, go Long.
        if dist_up > dist_dn:
            risk = abs(bo - bd) # Stop is the opposite trigger
            plan = {
                "valid": True, 
                "bias": "LONG",
                "entry": bo,
                "stop": bd,
                "targets": [
                    bo + risk,       # 1R
                    bo + (risk * 2), # 2R
                    bo + (risk * 4)  # 4R (Runner)
                ]
            }
        # If Support is further away, go Short.
        else:
            risk = abs(bo - bd)
            plan = {
                "valid": True, 
                "bias": "SHORT",
                "entry": bd,
                "stop": bo,
                "targets": [
                    bd - risk,       # 1R
                    bd - (risk * 2), # 2R
                    bd - (risk * 4)  # 4R (Runner)
                ]
            }

    # --- VORTEX MODE (Trap Logic) ---
    # We look to FADE the move toward the nearest wall.
    elif mode == "VORTEX":
        mid = (bo + bd) / 2
        dist_to_ceil = dr - mid
        dist_to_flr = mid - ds
        
        # If closer to Support -> Expect Fake Breakdown -> Buy Reclaim
        if dist_to_flr < dist_to_ceil:
            risk = abs(bo - bd) * 0.5 # Tighter stop for Trap
            plan = {
                "valid": True, 
                "bias": "LONG", # Trap the Bears
                "entry": bd,    # We buy when price comes back UP to this level
                "stop": bd - risk,
                "targets": [
                    bo,         # Target range high
                    dr,         # Target Daily Resistance
                    dr + risk   # Bonus
                ]
            }
        # If closer to Resistance -> Expect Fake Breakout -> Short Reclaim
        else:
            risk = abs(bo - bd) * 0.5
            plan = {
                "valid": True, 
                "bias": "SHORT", # Trap the Bulls
                "entry": bo,     # We short when price comes back DOWN to this level
                "stop": bo + risk,
                "targets": [
                    bd,         # Target range low
                    ds,         # Target Daily Support
                    ds - risk   # Bonus
                ]
            }
            
    return plan

# ------------------------------------------------------------------------------
# 4. MISSION KEY GENERATOR (For TradingView Handshake)
# ------------------------------------------------------------------------------
def _generate_mission_key(plan, mode):
    """
    Generates the pipe-separated string for the TradingView HUD.
    Format: BIAS|STATUS|ENTRY|STOP|TP1|TP2|TP3
    """
    if not plan["valid"]:
        return f"NEUTRAL|WEAK|0|0|0|0|0"

    bias = plan['bias']
    # Map Velocity to STRONG and Vortex to WEAK (or leave as AMBER logic in TV)
    status = "STRONG" if mode == "VELOCITY" else "WEAK" 
    
    entry = plan['entry']
    stop = plan['stop']
    targets = plan['targets']
    
    t1 = targets[0] if len(targets) > 0 else 0.0
    t2 = targets[1] if len(targets) > 1 else 0.0
    t3 = targets[2] if len(targets) > 2 else 0.0

    return f"{bias}|{status}|{entry:.2f}|{stop:.2f}|{t1:.2f}|{t2:.2f}|{t3:.2f}"

# ------------------------------------------------------------------------------
# 5. MAIN API ENDPOINT
# ------------------------------------------------------------------------------
async def get_vector_intel(symbol="BTCUSDT", session_id="us_ny_futures"):
    # 1. Get Phase 1 Data (The Core)
    data = await battlebox_pipeline.get_live_battlebox(symbol=symbol, session_mode="MANUAL", manual_id=session_id)
    
    if data.get("status") == "ERROR" or data.get("status") == "CALIBRATING":
        return {
            "ok": True, 
            "status": "CALIBRATING",
            "vector": {"mode": "CALIBRATING", "advice": "WAITING FOR MARKET OPEN..."},
            "plan": {"valid": False}
        }

    price = float(data.get("price", 0))
    box = data.get("battlebox", {})
    levels = box.get("levels", {})
    context = box.get("context", {})
    
    # 2. Run Kinetic Math
    kinetics = _calculate_vector_kinetics(price, levels, context)
    
    # 3. Run Vector Switch (Determine Mode)
    mode, advice, color = _determine_vector_mode(kinetics)
    
    # 4. Generate Mission Plan
    plan = _generate_vector_plan(mode, levels)
    
    # 5. Generate Mission Key
    m_key = _generate_mission_key(plan, mode)
    
    return {
        "ok": True,
        "symbol": symbol,
        "price": price,
        "vector": {
            "mode": mode,
            "color": color,
            "advice": advice,
            "score": kinetics["score"],
            "wind": kinetics["wind_score"]
        },
        "plan": plan,
        "levels": levels,
        "mission_key": m_key # PASSED TO FRONTEND
    }