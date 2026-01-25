# vector.py
# ==============================================================================
# PROJECT VECTOR (v4.3 LOCKED ANCHOR) - MEMBER PROTOCOLS
# ==============================================================================
import asyncio
import battlebox_pipeline

# --- 1. HELPER: GRADIENT SCORING (The "Dimmer Switch") ---
def _score_gradient(val, min_v, max_v, max_score):
    """Maps a value to a score range based on intensity."""
    if val < min_v: return 0
    if val > max_v: return max_score
    pct = (val - min_v) / (max_v - min_v)
    return round(pct * max_score)

# --- 2. KINETIC MATH (LOCKED ANCHOR LOGIC) ---
def _calculate_vector_kinetics(anchor, levels, context):
    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    
    # If we have no levels or no anchor, we can't score.
    if dr == 0 or ds == 0 or anchor == 0: 
        return {"score": 0, "wind_score": 0, "hull_score": 0, "energy_score": 0, "space_score": 0, "weekly_bias": "NEUTRAL"}

    # ENERGY (Volatility Compression)
    # Calculated from the ANCHOR, ensuring the score is fixed for the day.
    range_pct = (abs(dr - ds) / anchor) * 100
    e_val = 30 - _score_gradient(range_pct, 0.5, 2.0, 30)

    # SPACE (Room to Run)
    # Calculated from the ANCHOR.
    dist_up = abs(dr - anchor) / anchor * 100
    dist_dn = abs(anchor - ds) / anchor * 100
    nearest = min(dist_up, dist_dn)
    s_val = _score_gradient(nearest, 0.5, 3.0, 15)

    # WIND (Momentum + Bias)
    weekly = context.get("weekly_force", "NEUTRAL") 
    slope = float(levels.get("slope", 0.0))
    w_base = _score_gradient(abs(slope), 0.05, 0.3, 10) 
    w_bias = 10 if (slope > 0 and weekly == "BULLISH") or (slope < 0 and weekly == "BEARISH") else 0
    w_score = w_base + w_bias
        
    # STRUCTURE (Hull)
    struct = float(levels.get("structure_score", 0.0))
    h_val = _score_gradient(struct, 0.4, 0.9, 20)

    total_score = e_val + s_val + w_score + h_val

    return {
        "score": total_score,
        "wind_score": w_score,
        "hull_score": h_val,
        "energy_score": e_val,
        "space_score": s_val,
        "weekly_bias": weekly
    }

# --- 3. COMBAT PROTOCOLS ---
def _determine_vector_mode(kinetics):
    if kinetics["score"] < 45 or kinetics["hull_score"] < 8:
        return "HOLD FIRE", "CONDITIONS POOR. STAND DOWN.", "RED"
    
    if kinetics["wind_score"] >= 18:
        return "ASSAULT", "MOMENTUM PEAKING. PRESS THE ATTACK.", "CYAN"
        
    if kinetics["energy_score"] >= 25: 
        return "BREACH", "MARKET COILED. EXPLOSION IMMINENT.", "GOLD"

    return "AMBUSH", "WEAK PUSH DETECTED. SET THE TRAP.", "AMBER"

# --- 4. MISSION PLANNER ---
def _generate_vector_plan(mode, levels, bias_direction):
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    
    plan = {"valid": False, "bias": "NEUTRAL", "entry": 0, "stop": 0, "targets": [0,0,0]}
    if mode == "HOLD FIRE": return plan

    # Bias Logic
    trend_dir = "LONG" if bias_direction == "BULLISH" else ("SHORT" if bias_direction == "BEARISH" else "NEUTRAL")
    if trend_dir == "NEUTRAL":
         if mode == "BREACH": trend_dir = "LONG" 
         else: return plan

    risk = abs(bo - bd)
    if trend_dir == "LONG" and bo > 0:
        plan = {"valid": True, "bias": "LONG", "entry": bo, "stop": bd, "targets": [bo+risk, bo+2*risk, bo+4*risk]}
    elif trend_dir == "SHORT" and bd > 0:
        plan = {"valid": True, "bias": "SHORT", "entry": bd, "stop": bo, "targets": [bd-risk, bd-2*risk, bd-4*risk]}

    return plan

# --- 5. KEY GEN ---
def _generate_mission_key(plan, mode):
    if not plan["valid"]: return f"NEUTRAL|WEAK|0|0|0|0|0"
    status = "STRONG" if mode in ["ASSAULT", "BREACH"] else "WEAK"
    return f"{plan['bias']}|{status}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}"

# --- 6. API (STRICT LOCK) ---
async def get_vector_intel(symbol="BTCUSDT", session_id="us_ny_futures"):
    data = await battlebox_pipeline.get_live_battlebox(symbol, "MANUAL", manual_id=session_id)
    if data.get("status") in ["ERROR", "CALIBRATING"]: return {"ok": True, "status": "CALIBRATING"}

    # DATA EXTRACTION
    live_price = float(data.get("price", 0))
    box = data.get("battlebox", {})
    levels = box.get("levels", {})
    context = box.get("context", {})
    
    # *** CRITICAL FIX: READ OFFICIAL ANCHOR ***
    # We ask for "anchor_price", which Phase 1 now exports.
    # Default is 0. If 0, the math below returns 0 score. NO FLOATING.
    static_anchor = float(levels.get("anchor_price", 0))
    
    k = _calculate_vector_kinetics(static_anchor, levels, context)
    mode, advice, color = _determine_vector_mode(k)
    plan = _generate_vector_plan(mode, levels, k["weekly_bias"])
    
    if not plan["valid"] and mode != "HOLD FIRE":
        mode = "HOLD FIRE"
        advice = f"MISALIGNED. BIAS IS {k['weekly_bias']}."
        color = "RED"

    m_key = _generate_mission_key(plan, mode)
    
    return {
        "ok": True, "symbol": symbol, "price": live_price,
        "vector": {"mode": mode, "color": color, "advice": advice, "score": k["score"]}, 
        "metrics": {
            "energy": {"val": k["energy_score"], "pct": (k["energy_score"]/30)*100, "color": "CYAN" if k["energy_score"]>=25 else "GREEN"},
            "space": {"val": k["space_score"], "pct": (k["space_score"]/15)*100, "color": "GREEN"},
            "wind": {"val": k["wind_score"], "pct": (k["wind_score"]/20)*100, "color": "CYAN" if k["wind_score"]>=18 else "YELLOW"},
            "hull": {"val": k["hull_score"], "pct": (k["hull_score"]/20)*100, "color": "CYAN" if k["hull_score"]>=15 else "RED"}
        },
        "plan": plan, "levels": levels, "mission_key": m_key
    }