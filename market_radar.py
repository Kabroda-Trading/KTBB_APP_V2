# market_radar.py
# ==============================================================================
# MARKET RADAR v5.0 (IRON ENGINE: HYBRID PREDATOR PROTOCOLS)
# ==============================================================================
import asyncio
import os
import battlebox_pipeline

# TARGET ASSETS
TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# --- CACHE CLEANUP ---
try:
    if os.path.exists("radar_cache.json"): os.remove("radar_cache.json")
except: pass

# --- HELPER: GRADIENT SCORING ---
def _score_gradient(val, min_v, max_v, max_score):
    if val < min_v: return 0
    if val > max_v: return max_score
    pct = (val - min_v) / (max_v - min_v)
    return round(pct * max_score)

# --- HELPER: INDICATOR DATA STRING ---
def _make_indicator_string(levels):
    """Creates the CSV string for the TradingView Predator Command Indicator."""
    if not levels: return "0,0,0,0,0,0"
    bo = levels.get("breakout_trigger", 0)
    bd = levels.get("breakdown_trigger", 0)
    res = levels.get("daily_resistance", 0)
    sup = levels.get("daily_support", 0)
    
    # FIX: Use 'range30m_' keys instead of 'r30_'
    r30h = levels.get("range30m_high", 0) 
    r30l = levels.get("range30m_low", 0)
    
    return f"{bo},{bd},{res},{sup},{r30h},{r30l}"

# --- CORE MATH (KINETIC PHYSICS) ---
def _calc_kinetics(anchor, levels, context):
    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    
    if dr == 0 or ds == 0 or anchor == 0: 
        return {"score": 0, "wind": 0, "energy": 0, "hull": 0, "space": 0, "bias": "NEUTRAL"}

    # 1. ENERGY (Range Compression)
    range_pct = (abs(dr - ds) / anchor) * 100
    e_val = 30 - _score_gradient(range_pct, 0.5, 2.0, 30)

    # 2. SPACE (Room to Run)
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

# --- DECISION LOGIC (THE GENERAL) ---
def _get_status(symbol, k):
    # 1. GLOBAL SAFETY (Structure Check)
    if k["hull"] < 8: return "HOLD FIRE", "STRUCTURE WEAK", "RED"

    # 2. ASSET SPECIFIC SAFETY NETS (THE KILL SWITCH)
    # --- ETHEREUM ---
    if "ETH" in symbol:
        if k["score"] < 60: return "HOLD FIRE", "ETH CHOP GUARD (<60)", "RED"
        if k["space"] < 8: return "HOLD FIRE", "ETH TRAPPED", "RED"
    # --- SOLANA ---
    elif "SOL" in symbol:
        if k["energy"] < 15: return "HOLD FIRE", "SOL LOW ENERGY (<15)", "RED"
        if k["score"] < 65: return "HOLD FIRE", "SOL MOMENTUM LOW (<65)", "RED"
    # --- BITCOIN ---
    else:
        if k["score"] < 50: return "HOLD FIRE", "BTC WEAK (<50)", "RED"

    # 3. MISSION PROTOCOLS
    if k["score"] >= 75 or k["energy"] >= 25: 
        return "BREACH", "SUPERSONIC DETECTED (SINGLE LATCH)", "GOLD"
    
    if k["wind"] >= 18 or k["score"] >= 60: 
        return "ASSAULT", "MOMENTUM PEAKING (DOUBLE LATCH)", "CYAN"
    
    return "AMBUSH", "CAUTION (STRICT DOUBLE LATCH)", "CYAN"

def _get_plan(mode, levels, bias):
    plan = {"valid": False, "bias": "NEUTRAL", "entry": 0, "stop": 0, "targets": [0,0,0]}
    
    if mode == "HOLD FIRE": return plan

    # Bias Logic
    trend = "LONG" if bias == "BULLISH" else ("SHORT" if bias == "BEARISH" else "NEUTRAL")
    
    # NEUTRAL GUARD: Only trade BREACH on Neutral days
    if trend == "NEUTRAL":
        if mode == "BREACH": trend = "LONG" # Assume explosive upside breakout
        else: return plan 

    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
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

        # Force Hold Fire if Plan Invalid
        if not plan["valid"] and mode != "HOLD FIRE":
            mode = "HOLD FIRE"
            color = "RED"
            advice = f"CONFLICT: BIAS IS {k['bias']}"

        metrics = {
            "energy": {"val": k["energy"], "pct": (k["energy"]/30)*100, "color": "CYAN" if k["energy"]>=25 else "GREEN"},
            "wind": {"val": k["wind"], "pct": (k["wind"]/20)*100, "color": "CYAN" if k["wind"]>=18 else "YELLOW"},
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
            "indicator_string": _make_indicator_string(levels) # Added for Copy Button
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
            "metrics": {
                "energy": {"val": k["energy"], "pct": (k["energy"]/30)*100, "color": "CYAN" if k["energy"]>=25 else "GREEN"},
                "wind": {"val": k["wind"], "pct": (k["wind"]/20)*100, "color": "CYAN" if k["wind"]>=18 else "YELLOW"},
                "hull": {"val": k["hull"], "pct": (k["hull"]/20)*100, "color": "CYAN" if k["hull"]>=15 else "RED"},
                "space": {"val": k["space"], "pct": (k["space"]/15)*100, "color": "GREEN"}
            },
            "plan": plan, 
            "levels": levels,
            "mission_key": _make_key(plan, mode),
            "indicator_string": _make_indicator_string(levels) # Added for Copy Button
        }
    }