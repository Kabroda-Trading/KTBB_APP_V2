# market_radar.py
# ==============================================================================
# MARKET RADAR v3.3 (HYBRID PROTOCOL: FERRARI + ETH GUARD)
# ==============================================================================
import asyncio
import os
import battlebox_pipeline

# THE FLEET
TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# CACHE CLEANUP
try:
    if os.path.exists("radar_cache.json"): os.remove("radar_cache.json")
except: pass

# --- 1. CORE KINETIC MATH ---
def _calc_kinetics(anchor, levels, context):
    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    if dr == 0 or ds == 0: 
        return {"score": 0, "wind": 0, "energy": 0, "hull": 0, "bias": "NEUTRAL"}

    # Energy (Volatility)
    range_size = abs(dr - ds)
    bps = (range_size / anchor) * 10000 if anchor > 0 else 500
    e_val = 30 if bps < 100 else (15 if bps < 250 else 0)

    # Wind (Momentum + Bias)
    weekly = context.get("weekly_force", "NEUTRAL") 
    slope = float(levels.get("slope", 0.0))         
    
    w_score = 0
    if (slope > 0.1 and weekly == "BULLISH") or (slope < -0.1 and weekly == "BEARISH"): w_score += 10
    if abs(slope) > 0.2: w_score += 10
        
    # Structure (Hull)
    struct = float(levels.get("structure_score", 0.0))
    h_val = 20 if struct > 0.7 else (10 if struct > 0.4 else 0)
    s_val = 15 

    return {
        "score": e_val + s_val + w_score + h_val,
        "wind": w_score, "hull": h_val, "energy": e_val, "space": s_val,
        "bias": weekly
    }

# --- 2. HYBRID DECISION LOGIC ---
def _get_combat_status(symbol, k):
    # BASE RULE: Score must be decent
    if k["score"] < 50 or k["hull"] < 10:
        return "HOLD FIRE", "CONDITIONS POOR", "RED"

    # HYBRID RULE: ETH needs higher conviction (The Audit Fix)
    if "ETH" in symbol and k["score"] < 55:
        return "HOLD FIRE", "ETH CHOP GUARD (SCORE < 55)", "RED"

    # COMBAT MODES
    if k["wind"] >= 15: return "ASSAULT", "MOMENTUM PEAKING", "CYAN"
    if k["energy"] == 30: return "BREACH", "COILED EXPLOSION", "GOLD"
    
    return "AMBUSH", "TRAP SET", "AMBER"

def _generate_plan(mode, levels, bias):
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    
    plan = {"valid": False, "bias": "NEUTRAL", "entry": 0, "stop": 0, "targets": [0,0,0]}
    if mode == "HOLD FIRE": return plan

    allowed = "BOTH"
    if bias == "BULLISH": allowed = "LONG"
    if bias == "BEARISH": allowed = "SHORT"

    if mode in ["ASSAULT", "BREACH"]:
        dist_up = abs(dr - bo)
        dist_dn = abs(bd - dr) # Fix: Correct distance logic
        # Simple Logic: If price is closer to BO than BD, look Long
        # Real Logic: We trust the Bias and the Triggers.
        
        # Determine Trend Direction by Bias first
        trend_dir = "LONG" if bias == "BULLISH" else ("SHORT" if bias == "BEARISH" else "NEUTRAL")
        
        # If Neutral, follow the triggers distance
        if trend_dir == "NEUTRAL":
             # This is a simplification; ideally we use current price proximity
             return plan 

        risk = abs(bo - bd)
        if trend_dir == "LONG" and bo > 0:
            plan = {"valid": True, "bias": "LONG", "entry": bo, "stop": bd, "targets": [bo+risk, bo+2*risk, bo+4*risk]}
        elif trend_dir == "SHORT" and bd > 0:
            plan = {"valid": True, "bias": "SHORT", "entry": bd, "stop": bo, "targets": [bd-risk, bd-2*risk, bd-4*risk]}

    return plan

def _make_key(plan, status):
    if not plan["valid"]: return f"NEUTRAL|WEAK|0|0|0|0|0"
    s_val = "STRONG" if status in ["ASSAULT", "BREACH"] else "WEAK"
    return f"{plan['bias']}|{s_val}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}"

# --- 3. API ---
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
        context = box.get("context", {})
        
        k = _calc_kinetics(price, levels, context)
        mode, advice, color = _get_combat_status(sym, k)
        
        plan = _generate_plan(mode, levels, k["bias"])
        if not plan["valid"] and mode != "HOLD FIRE":
            mode = "HOLD FIRE"
            color = "RED"
            advice = f"CONFLICT: BIAS IS {k['bias']}"

        metrics = {
            "energy": {"val": k["energy"], "color": "CYAN" if k["energy"]==30 else "GREEN", "pct": (k["energy"]/30)*100},
            "wind": {"val": k["wind"], "color": "CYAN" if k["wind"]>=20 else "YELLOW", "pct": (k["wind"]/20)*100},
            "hull": {"val": k["hull"], "color": "CYAN" if k["hull"]>=20 else "RED", "pct": (k["hull"]/20)*100},
            "space": {"val": 15, "color": "GREEN", "pct": 50}
        }

        radar_grid.append({
            "symbol": sym, "price": price, 
            "score": k["score"], "status": mode, 
            "bias": k["bias"], "metrics": metrics,
            "advice": advice, "color_code": color
        })

    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    return radar_grid

async def analyze_target(symbol, session_id="us_ny_futures"):
    data = await battlebox_pipeline.get_live_battlebox(symbol, "MANUAL", manual_id=session_id)
    if data.get("status") == "ERROR": return {"ok": False}

    price = float(data.get("price", 0))
    levels = data.get("battlebox", {}).get("levels", {})
    context = data.get("battlebox", {}).get("context", {})
    
    k = _calc_kinetics(price, levels, context)
    mode, advice, color = _get_combat_status(symbol, k)
    plan = _generate_plan(mode, levels, k["bias"])
    
    if not plan["valid"] and mode != "HOLD FIRE":
        mode = "HOLD FIRE"
        color = "RED"
        advice = f"CONFLICT: BIAS IS {k['bias']}"

    m_key = _make_key(plan, mode)

    return {
        "ok": True,
        "result": {
            "symbol": symbol, "price": price,
            "score": k["score"], "status": mode, "color": color,
            "bias": k["bias"], "advice": advice,
            "metrics": {
                "energy": {"val": k["energy"], "pct": (k["energy"]/30)*100},
                "wind": {"val": k["wind"], "pct": (k["wind"]/20)*100},
                "hull": {"val": k["hull"], "pct": (k["hull"]/20)*100},
                "space": {"val": 15, "pct": 50}
            },
            "plan": plan, "levels": levels, "mission_key": m_key
        }
    }