# market_radar.py
# ==============================================================================
# MARKET RADAR v4.2 (GRANULAR MATH + BUTTON LOGIC)
# ==============================================================================
import asyncio
import os
import battlebox_pipeline

TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# --- CACHE NUKE ---
try:
    if os.path.exists("radar_cache.json"): os.remove("radar_cache.json")
except: pass

# --- HELPER: GRADIENT SCORING (The "Dimmer Switch") ---
def _score_gradient(val, min_v, max_v, max_score):
    if val < min_v: return 0
    if val > max_v: return max_score
    pct = (val - min_v) / (max_v - min_v)
    return round(pct * max_score)

# --- CORE MATH ---
def _calc_kinetics(anchor, levels, context):
    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    if dr == 0 or ds == 0 or anchor == 0: 
        return {"score": 0, "wind": 0, "energy": 0, "hull": 0, "space": 0, "bias": "NEUTRAL"}

    # 1. ENERGY (Range Compression)
    # Tighter range = Higher potential.
    range_pct = (abs(dr - ds) / anchor) * 100
    # Score 0-30 based on how tight the range is (0.5% to 2.0%)
    e_val = 30 - _score_gradient(range_pct, 0.5, 2.0, 30)

    # 2. SPACE (Room to Run)
    # Score 0-15 based on distance to nearest resistance/support
    dist_up = abs(dr - anchor) / anchor * 100
    dist_dn = abs(anchor - ds) / anchor * 100
    nearest = min(dist_up, dist_dn)
    s_val = _score_gradient(nearest, 0.5, 3.0, 15)

    # 3. WIND (Momentum)
    weekly = context.get("weekly_force", "NEUTRAL") 
    slope = float(levels.get("slope", 0.0))
    w_base = _score_gradient(abs(slope), 0.05, 0.3, 10) 
    w_bias = 10 if (slope > 0 and weekly == "BULLISH") or (slope < 0 and weekly == "BEARISH") else 0
    w_val = w_base + w_bias
        
    # 4. STRUCTURE (Hull)
    struct = float(levels.get("structure_score", 0.0))
    h_val = _score_gradient(struct, 0.4, 0.9, 20)

    total = e_val + s_val + w_val + h_val
    return {"score": total, "wind": w_val, "hull": h_val, "energy": e_val, "space": s_val, "bias": weekly}

# --- DECISION LOGIC ---
def _get_status(symbol, k):
    # Global Kill Switch
    if k["score"] < 45 or k["hull"] < 8: return "HOLD FIRE", "CONDITIONS POOR", "RED"

    # ETH Guard (From Backtest)
    if "ETH" in symbol and k["score"] < 55: return "HOLD FIRE", "ETH CHOP GUARD", "RED"

    # Active Modes
    if k["wind"] >= 18: return "ASSAULT", "MOMENTUM PEAKING", "CYAN"
    if k["energy"] >= 25: return "BREACH", "COILED EXPLOSION", "GOLD"
    
    return "AMBUSH", "TRAP SET", "AMBER"

def _get_plan(mode, levels, bias):
    plan = {"valid": False, "bias": "NEUTRAL", "entry": 0, "stop": 0, "targets": [0,0,0]}
    if mode == "HOLD FIRE": return plan

    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    
    # Determine Direction
    trend = "LONG" if bias == "BULLISH" else ("SHORT" if bias == "BEARISH" else "NEUTRAL")
    
    # If Neutral, check triggers distance
    if trend == "NEUTRAL":
        # Simplified: If Breach, assume breakout imminent
        if mode == "BREACH": trend = "LONG" # Default bias for Breach in Neutral
        else: return plan

    risk = abs(bo - bd)
    if trend == "LONG" and bo > 0:
        plan = {"valid": True, "bias": "LONG", "entry": bo, "stop": bd, "targets": [bo+risk, bo+2*risk, bo+4*risk]}
    elif trend == "SHORT" and bd > 0:
        plan = {"valid": True, "bias": "SHORT", "entry": bd, "stop": bo, "targets": [bd-risk, bd-2*risk, bd-4*risk]}
            
    return plan

def _make_key(plan, status):
    if not plan["valid"]: return "NEUTRAL|WEAK|0|0|0|0|0"
    s_txt = "STRONG" if status in ["ASSAULT", "BREACH"] else "WEAK"
    return f"{plan['bias']}|{s_txt}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}"

# --- API ---
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
        k = _calc_kinetics(price, box.get("levels", {}), box.get("context", {}))
        mode, advice, color = _get_status(sym, k)
        plan = _get_plan(mode, box.get("levels", {}), k["bias"])

        # Override if plan fails
        if not plan["valid"] and mode != "HOLD FIRE":
            mode = "HOLD FIRE"
            color = "RED"
            advice = f"CONFLICT: BIAS IS {k['bias']}"

        # Metric Colors for GUI
        metrics = {
            "energy": {"val": k["energy"], "pct": (k["energy"]/30)*100, "color": "CYAN" if k["energy"]>=25 else "GREEN"},
            "wind": {"val": k["wind"], "pct": (k["wind"]/20)*100, "color": "CYAN" if k["wind"]>=18 else "YELLOW"},
            "hull": {"val": k["hull"], "pct": (k["hull"]/20)*100, "color": "CYAN" if k["hull"]>=15 else "RED"},
            "space": {"val": k["space"], "pct": (k["space"]/15)*100, "color": "GREEN"}
        }

        radar_grid.append({
            "symbol": sym, "price": price, "score": k["score"], 
            "status": mode, "bias": k["bias"], 
            "metrics": metrics, "color_code": color,
            "has_trade": plan["valid"] # FLAG FOR BUTTON VISIBILITY
        })

    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    return radar_grid

async def analyze_target(symbol, session_id="us_ny_futures"):
    data = await battlebox_pipeline.get_live_battlebox(symbol, "MANUAL", manual_id=session_id)
    if data.get("status") == "ERROR": return {"ok": False}
    
    price = float(data.get("price", 0))
    box = data.get("battlebox", {})
    k = _calc_kinetics(price, box.get("levels", {}), box.get("context", {}))
    mode, advice, color = _get_status(symbol, k)
    plan = _get_plan(mode, box.get("levels", {}), k["bias"])
    
    if not plan["valid"] and mode != "HOLD FIRE":
        mode = "HOLD FIRE"
        color = "RED"
        advice = f"CONFLICT: BIAS IS {k['bias']}"

    return {
        "ok": True,
        "result": {
            "symbol": symbol, "price": price, "score": k["score"],
            "status": mode, "color": color, "advice": advice, "bias": k["bias"],
            "metrics": {
                "energy": {"val": k["energy"], "pct": (k["energy"]/30)*100, "color": "CYAN" if k["energy"]>=25 else "GREEN"},
                "wind": {"val": k["wind"], "pct": (k["wind"]/20)*100, "color": "CYAN" if k["wind"]>=18 else "YELLOW"},
                "hull": {"val": k["hull"], "pct": (k["hull"]/20)*100, "color": "CYAN" if k["hull"]>=15 else "RED"},
                "space": {"val": k["space"], "pct": (k["space"]/15)*100, "color": "GREEN"}
            },
            "plan": plan, "levels": box.get("levels", {}),
            "mission_key": _make_key(plan, mode)
        }
    }