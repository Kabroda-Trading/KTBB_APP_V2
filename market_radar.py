# market_radar.py
# ==============================================================================
# MARKET RADAR v5.2 (KINETIC PREDATOR UPDATE)
# FIXED: Kinetic Math Scoring (Space & Wind Logic Inversion)
# ==============================================================================
import asyncio
import os
import battlebox_pipeline

TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# --- HELPER: GRADIENT SCORING ---
def _score_gradient(val, min_v, max_v, max_score):
    if val < min_v: return 0
    if val > max_v: return max_score
    pct = (val - min_v) / (max_v - min_v)
    return round(pct * max_score)

# --- HELPER: INDICATOR STRING ---
def _make_indicator_string(levels):
    if not levels: return "0,0,0,0,0,0"
    return f"{levels.get('breakout_trigger',0)},{levels.get('breakdown_trigger',0)},{levels.get('daily_resistance',0)},{levels.get('daily_support',0)},{levels.get('range30m_high',0)},{levels.get('range30m_low',0)}"

# --- CORE MATH (ACTIVE PHYSICS - TUNED) ---
def _calc_kinetics(anchor, levels, context):
    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    
    if dr == 0 or ds == 0 or anchor == 0: 
        return {"score": 0, "wind": 0, "energy": 0, "hull": 0, "space": 0, "bias": "NEUTRAL"}

    # 1. ENERGY (Range Compression)
    # Rule: Tighter Daily Range = Higher Explosive Potential
    # Tuned: 5.0% max range allowed (was 3.5%) to accommodate BTC volatility
    range_pct = (abs(dr - ds) / anchor) * 100
    e_val = 30 - _score_gradient(range_pct, 0.5, 5.0, 30)

    # 2. SPACE (Kinetic Gap / Room to Run)
    dist_up = abs(dr - anchor) / anchor * 100
    dist_dn = abs(anchor - ds) / anchor * 100
    nearest = min(dist_up, dist_dn)
    
    # FIX: Inverted Logic for Compression
    # If levels are super tight (<0.5%), it's a "Coiled Spring" -> MAX POINTS
    if nearest < 0.5:
        s_val = 15 
    else:
        # Otherwise, score based on "Room to Run" (Wider is better up to a point)
        s_val = _score_gradient(nearest, 0.5, 4.0, 15)

    # 3. WIND (Momentum + Context)
    weekly = context.get("weekly_force", "NEUTRAL") 
    slope = float(levels.get("slope", 0.0))
    
    # FIX: Base points for Context Alignment even if slope is flat (0)
    w_base = 5 if weekly in ["BULLISH", "BEARISH"] else 0
    
    # Add Slope Strength
    w_slope = _score_gradient(abs(slope), 0.0, 0.3, 10)
    
    # Alignment Bonus
    w_align = 0
    if (slope >= 0 and weekly == "BULLISH") or (slope <= 0 and weekly == "BEARISH"):
        w_align = 10
        
    w_val = min(25, w_base + w_slope + w_align) # Cap Wind at 25 pts
        
    # 4. STRUCTURE (Hull)
    struct = float(levels.get("structure_score", 0.0))
    # FIX: Relaxed floor to 0.2 to allow "messy but valid" breakouts
    h_val = _score_gradient(struct, 0.2, 0.8, 20)

    total = e_val + s_val + w_val + h_val
    # Cap total score at 99 to leave room for error
    total = min(99, total)
    
    return {"score": total, "wind": w_val, "hull": h_val, "energy": e_val, "space": s_val, "bias": weekly}

# --- DECISION LOGIC (ACTIVE GENERAL) ---
def _get_status(symbol, k):
    # 1. GLOBAL SAFETY
    # Tuned: Allow lower hull score (was 5) if Total Score is high
    if k["hull"] < 3 and k["score"] < 50: return "HOLD FIRE", "STRUCTURE CRITICAL", "RED"

    # 2. ASSET SPECIFIC RULES (ACTIVE - TUNED)
    # Lowered thresholds slightly to catch early trends like May 1st
    if "ETH" in symbol:
        if k["score"] < 50: return "HOLD FIRE", "ETH WEAK (<50)", "RED" 
    elif "SOL" in symbol:
        if k["score"] < 55: return "HOLD FIRE", "SOL WEAK (<55)", "RED"
    else:
        # BTC ACTIVE RULE: Score 35 is the new floor for aggressive entry
        if k["score"] < 35: return "HOLD FIRE", "BTC WEAK (<35)", "RED"

    # 3. MISSION PROTOCOLS
    if k["score"] >= 70: return "BREACH", "HIGH CONVICTION", "GOLD"
    if k["score"] >= 50: return "ASSAULT", "STANDARD ENTRY", "CYAN"
    return "AMBUSH", "AGGRESSIVE ENTRY", "CYAN" # Scores 35-50

def _get_plan(mode, levels, bias):
    plan = {"valid": False, "bias": "NEUTRAL", "entry": 0, "stop": 0, "targets": [0,0,0]}
    if mode == "HOLD FIRE": return plan

    trend = "LONG" if bias == "BULLISH" else ("SHORT" if bias == "BEARISH" else "NEUTRAL")
    if trend == "NEUTRAL": 
        if mode == "BREACH": trend = "LONG" # Allow breach on neutral if score is high
        else: return plan

    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    
    # Calculate Risk Unit
    risk = abs(bo - bd)
    if risk == 0: risk = bo * 0.01 # Fallback 1% risk if triggers undefined

    if trend == "LONG" and bo > 0:
        plan = {"valid": True, "bias": "LONG", "entry": bo, "stop": bd, "targets": [bo+risk, bo+2*risk, bo+4*risk]}
    elif trend == "SHORT" and bd > 0:
        plan = {"valid": True, "bias": "SHORT", "entry": bd, "stop": bo, "targets": [bd-risk, bd-2*risk, bd-4*risk]}
            
    return plan

def _make_key(plan, status):
    if not plan["valid"]: return "NEUTRAL|WEAK|0|0|0|0|0"
    s_txt = "STRONG" if status in ["ASSAULT", "BREACH"] else "WEAK"
    return f"{plan['bias']}|{s_txt}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}"

# --- API ENDPOINTS ---
async def scan_sector(session_id="us_ny_futures"):
    """Main Fleet Scan for Dashboard"""
    radar_grid = []
    tasks = [battlebox_pipeline.get_live_battlebox(sym, "MANUAL", manual_id=session_id) for sym in TARGETS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for sym, res in zip(TARGETS, results):
        if isinstance(res, Exception) or res.get("status") == "ERROR":
            radar_grid.append({"symbol": sym, "score": 0, "status": "OFFLINE", "metrics": {}})
            continue

        price = float(res.get("price", 0))
        box = res.get("battlebox", {})
        levels = box.get("levels", {})
        static_anchor = float(levels.get("anchor_price", 0))
        
        if static_anchor == 0:
            k = {"score": 0, "wind": 0, "energy": 0, "hull": 0, "space": 0, "bias": "NEUTRAL"}
        else:
            k = _calc_kinetics(static_anchor, levels, box.get("context", {}))

        mode, advice, color = _get_status(sym, k)
        plan = _get_plan(mode, levels, k["bias"])

        if not plan["valid"] and mode != "HOLD FIRE":
            mode = "HOLD FIRE"
            color = "RED"
            advice = f"CONFLICT: BIAS IS {k['bias']}"

        metrics = {
            "energy": {"val": k["energy"], "pct": (k["energy"]/30)*100, "color": "CYAN" if k["energy"]>=25 else "GREEN"},
            "wind": {"val": k["wind"], "pct": (k["wind"]/25)*100, "color": "CYAN" if k["wind"]>=20 else "YELLOW"},
            "hull": {"val": k["hull"], "pct": (k["hull"]/20)*100, "color": "CYAN" if k["hull"]>=15 else "RED"},
            "space": {"val": k["space"], "pct": (k["space"]/15)*100, "color": "GREEN"}
        }

        radar_grid.append({
            "symbol": sym, 
            "price": price, 
            "score": k["score"], 
            "status": mode, 
            "bias": k["bias"], 
            "metrics": metrics, 
            "color_code": color,
            "has_trade": plan["valid"],
            "indicator_string": _make_indicator_string(levels)
        })

    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    return radar_grid

async def analyze_target(symbol, session_id="us_ny_futures"):
    """Detail View for Target Lock"""
    data = await battlebox_pipeline.get_live_battlebox(symbol, "MANUAL", manual_id=session_id)
    if data.get("status") == "ERROR": return {"ok": False}
    
    price = float(data.get("price", 0))
    box = data.get("battlebox", {})
    levels = box.get("levels", {})
    static_anchor = float(levels.get("anchor_price", 0))
    
    if static_anchor == 0:
        k = {"score": 0, "wind": 0, "energy": 0, "hull": 0, "space": 0, "bias": "NEUTRAL"}
    else:
        k = _calc_kinetics(static_anchor, levels, box.get("context", {}))

    mode, advice, color = _get_status(symbol, k)
    plan = _get_plan(mode, levels, k["bias"])
    
    if not plan["valid"] and mode != "HOLD FIRE":
        mode = "HOLD FIRE"
        color = "RED"
        advice = f"CONFLICT: BIAS IS {k['bias']}"

    return {
        "ok": True,
        "result": {
            "symbol": symbol, "price": price, "score": k["score"],
            "status": mode, "color": color, "advice": advice, "bias": k["bias"],
            "metrics": k,
            "plan": plan, 
            "levels": levels,
            "mission_key": _make_key(plan, mode),
            "indicator_string": _make_indicator_string(levels)
        }
    }