# market_radar.py
# ==============================================================================
# KABRODA MARKET RADAR v6.2 (POLARITY PATCH APPLIED)
# AUDIT: Fixed Logic Inversion on "Heavy Floor" (SOL Case)
# ==============================================================================
import asyncio
import battlebox_pipeline

# DEFINED TARGETS
TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# --- HELPER: GRADIENT SCORING ---
def _score_gradient(val, min_v, max_v, max_score):
    if val < min_v: return 0
    if val > max_v: return max_score
    pct = (val - min_v) / (max_v - min_v)
    return round(pct * max_score)

# --- HELPER: INDICATOR STRING (For TradingView Copy) ---
def _make_indicator_string(levels):
    if not levels: return "0,0,0,0,0,0"
    return f"{levels.get('breakout_trigger',0)},{levels.get('breakdown_trigger',0)},{levels.get('daily_resistance',0)},{levels.get('daily_support',0)},{levels.get('range30m_high',0)},{levels.get('range30m_low',0)}"

# --- CORE MATH: KINETIC BIAS DETERMINATION (THE FIX) ---
def _determine_kinetic_bias(anchor, dr, ds, weekly_force):
    """
    Solves the SOL Polarity Bug.
    Rule: Physics overrides Weekly Trend.
    If price is resting on Support, it is BULLISH, even if Weekly is Bearish.
    """
    if dr == 0 or ds == 0: return weekly_force

    dist_to_res = abs(dr - anchor)
    dist_to_sup = abs(anchor - ds)

    # 1. BULLISH PHYSICS: Resting on Floor
    # If above support AND (closer to support OR broken above resistance)
    if anchor > ds and (dist_to_sup < dist_to_res or anchor > dr):
        return "BULLISH"

    # 2. BEARISH PHYSICS: Hanging from Ceiling
    # If below resistance AND (closer to resistance OR broken below support)
    if anchor < dr and (dist_to_res < dist_to_sup or anchor < ds):
        return "BEARISH"

    # 3. NEUTRAL ZONE: Fallback to Weekly Context
    return weekly_force

# --- CORE MATH: THE OBSTRUCTION GATE ---
def _check_obstruction(anchor, levels, bias):
    """
    Returns True if a Daily Level stands BETWEEN the Anchor and the Trigger.
    """
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))

    is_obstructed = False

    # 1. LONG SCENARIO CHECK
    if bias == "BULLISH":
        # If Daily Resistance is LOWER than Breakout Trigger, it's in the way.
        if anchor < dr < bo:
            is_obstructed = True

    # 2. SHORT SCENARIO CHECK
    elif bias == "BEARISH":
        # If Daily Support is HIGHER than Breakdown Trigger, it's in the way.
        if anchor > ds > bd:
            is_obstructed = True

    return is_obstructed

# --- CORE MATH: KINETIC CALCULATION ---
def _calc_kinetics(anchor, levels, context):
    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    
    if dr == 0 or ds == 0 or anchor == 0: 
        return {"score": 0, "wind": 0, "energy": 0, "hull": 0, "space": 0, "bias": "NEUTRAL"}

    # --- STEP 1: DETERMINE PHYSICS BIAS ---
    # This fixes the logic. We calculate bias FIRST.
    weekly = context.get("weekly_force", "NEUTRAL") 
    kinetic_bias = _determine_kinetic_bias(anchor, dr, ds, weekly)

    # 2. ENERGY (The Coil) - Max 30 pts
    range_pct = (abs(dr - ds) / anchor) * 100
    e_val = 30 - _score_gradient(range_pct, 1.0, 6.0, 30)

    # 3. SPACE (Proximity) - Max 15 pts
    dist_up = abs(dr - anchor) / anchor * 100
    dist_dn = abs(anchor - ds) / anchor * 100
    nearest = min(dist_up, dist_dn)
    
    if nearest < 0.3: s_val = 15 
    else: s_val = 15 - _score_gradient(nearest, 0.3, 3.0, 15)

    # 4. WIND (The Slope Floor) - Max 25 pts
    slope = float(levels.get("slope", 0.0))
    w_val = 0
    
    if abs(slope) > 0.05:
        w_val += _score_gradient(abs(slope), 0.05, 0.5, 15)
    
    # Alignment Bonus (Using calculated Kinetic Bias)
    if (slope > 0 and kinetic_bias == "BULLISH") or (slope < 0 and kinetic_bias == "BEARISH"):
        w_val += 10
    
    w_val = min(25, w_val)

    # 5. HULL (The Obstruction Gate) - Max 20 pts
    is_blocked = _check_obstruction(anchor, levels, kinetic_bias)
    
    if is_blocked:
        h_val = 0 # Fatal Flaw
    else:
        struct = float(levels.get("structure_score", 0.0))
        h_val = _score_gradient(struct, 0.2, 0.9, 20)

    # --- FINAL SUMMATION ---
    total = e_val + s_val + w_val + h_val

    # Rule 1: The Obstruction Cap
    if h_val == 0:
        total = min(45, total)

    return {"score": int(total), "wind": w_val, "hull": h_val, "energy": e_val, "space": s_val, "bias": kinetic_bias}

# --- DECISION LOGIC ---
def _get_status(symbol, k):
    score = k["score"]

    # 1. OBSTRUCTION CHECK
    if k["hull"] == 0:
        return "HOLD FIRE", "PATH OBSTRUCTED", "RED"

    # 2. SCORE GATING
    if score >= 75: return "BREACH", "HIGH CONVICTION", "GREEN"
    if score >= 50: return "ASSAULT", "STANDARD ENTRY", "GREEN"
    if score >= 35: return "AMBUSH", "AGGRESSIVE/RISKY", "YELLOW"
    
    return "HOLD FIRE", "INSUFFICIENT DATA", "RED"

def _get_plan(mode, levels, bias):
    plan = {"valid": False, "bias": "NEUTRAL", "entry": 0, "stop": 0, "targets": [0,0,0]}
    if mode == "HOLD FIRE": return plan

    trend = "LONG" if bias == "BULLISH" else ("SHORT" if bias == "BEARISH" else "NEUTRAL")
    
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    
    # Calculate Risk Unit
    risk = abs(bo - bd)
    if risk == 0: risk = bo * 0.01

    if trend == "LONG" and bo > 0:
        plan = {"valid": True, "bias": "LONG", "entry": bo, "stop": bd, "targets": [bo+risk, bo+2*risk, bo+3*risk]}
    elif trend == "SHORT" and bd > 0:
        plan = {"valid": True, "bias": "SHORT", "entry": bd, "stop": bo, "targets": [bd-risk, bd-2*risk, bd-3*risk]}
            
    return plan

def _make_key(plan, status):
    if not plan["valid"]: return "NEUTRAL|WEAK|0|0|0|0|0"
    s_txt = "STRONG" if status in ["ASSAULT", "BREACH"] else "WEAK"
    return f"{plan['bias']}|{s_txt}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}"

# --- API ENDPOINTS ---
async def scan_sector(session_id="us_ny_futures"):
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
            "energy": {"val": k["energy"], "pct": (k["energy"]/30)*100, "color": "GREEN" if k["energy"]>=20 else "YELLOW"},
            "wind": {"val": k["wind"], "pct": (k["wind"]/25)*100, "color": "GREEN" if k["wind"]>=10 else "RED"},
            "hull": {"val": k["hull"], "pct": (k["hull"]/20)*100, "color": "GREEN" if k["hull"]>=10 else "RED"},
            "space": {"val": k["space"], "pct": (k["space"]/15)*100, "color": "GREEN" if k["space"]>=10 else "YELLOW"}
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