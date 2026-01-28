# market_radar.py
# ==============================================================================
# KABRODA MARKET RADAR v6.12 (PREDATOR PRIORITY)
# AUDIT: 1. Stops HARD-CODED to prioritize 30m High/Low (Predator) + Buffer.
#        2. Targets use Chart-Aligned Fib Extensions (0.618, 1.0, 1.618).
#        3. Entries front-run targets to guarantee fills.
# ==============================================================================
import asyncio
import battlebox_pipeline

TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# --- HELPER: GRADIENT SCORING ---
def _score_gradient(val, min_v, max_v, max_score):
    if val < min_v: return 0
    if val > max_v: return max_score
    pct = (val - min_v) / (max_v - min_v)
    return round(pct * max_score)

def _make_indicator_string(levels):
    if not levels: return "0,0,0,0,0,0"
    return f"{levels.get('breakout_trigger',0)},{levels.get('breakdown_trigger',0)},{levels.get('daily_resistance',0)},{levels.get('daily_support',0)},{levels.get('range30m_high',0)},{levels.get('range30m_low',0)}"

# --- MISSION BRIEF TRANSLATOR (ROE) ---
def _generate_roe(k, levels, anchor):
    bias = k['bias']
    score = k['score']
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    
    if anchor == 0 or bo == 0: return "AWAITING DATA SYNC."

    volatility_gap = abs(bo - bd)
    is_wide = volatility_gap > (anchor * 0.015) 
    
    if is_wide and (bd < anchor < bo):
        return f"LIQUIDITY HUNT DETECTED. Triggers are wide ({int(volatility_gap)} pts). Market is hunting stops at {int(dr)} and {int(ds)}. DO NOT CHASE WICKS. Valid entry only on CONFIRMED 15M CLOSE outside {int(bo)} or {int(bd)}."

    if (bias == "BEARISH") and (anchor > dr):
        return f"COUNTER-INSURGENCY. Price has reclaimed Resistance ({int(dr)}). The 'Ceiling' is now a 'Floor'. Longs valid if {int(dr)} holds. Target: {int(bo)}."

    if (bias == "BULLISH") and (anchor < dr):
        return f"OBSTRUCTION AHEAD. Momentum is blocked by the {int(dr)} ceiling. Do not engage until price reclaims {int(dr)}."

    if score >= 75:
        return "GREEN LIGHT / FULL ASSAULT. Structural alignment confirmed. Volatility expansion probable. Aggressive sizing permitted."
    
    return "STANDARD OPERATING PROCEDURE. Follow Predator 15m candle closes. Respect stops at daily levels."

# --- KINETIC CALCULATION ---
def _check_obstruction(anchor, levels, bias):
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))

    if (bo > dr) and (bd < ds): return False 
    if bias == "BULLISH" and (anchor < dr < bo): return True
    if bias == "BEARISH" and (anchor > ds > bd): return True
    return False

def _calc_kinetics(anchor, levels, context):
    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    if dr == 0 or ds == 0 or anchor == 0: 
        return {"score": 0, "wind": 0, "energy": 0, "hull": 0, "space": 0, "bias": "NEUTRAL"}

    range_pct = (abs(dr - ds) / anchor) * 100
    e_val = 30 - _score_gradient(range_pct, 1.0, 6.0, 30)

    dist_up = abs(dr - anchor) / anchor * 100
    dist_dn = abs(anchor - ds) / anchor * 100
    nearest = min(dist_up, dist_dn)
    s_val = 15 if nearest < 0.3 else 15 - _score_gradient(nearest, 0.3, 3.0, 15)

    weekly = context.get("weekly_force", "NEUTRAL") 
    slope = float(levels.get("slope", 0.0))
    w_val = 0
    if abs(slope) > 0.05: w_val += _score_gradient(abs(slope), 0.05, 0.5, 15)
    if (slope > 0 and weekly == "BULLISH") or (slope < 0 and weekly == "BEARISH"): w_val += 10
    w_val = min(25, w_val)

    is_blocked = _check_obstruction(anchor, levels, weekly)
    h_val = 0 if is_blocked else _score_gradient(float(levels.get("structure_score", 0.0)), 0.2, 0.9, 20)

    total = e_val + s_val + w_val + h_val
    if h_val == 0: total = min(45, total)

    return {"score": int(total), "wind": w_val, "hull": h_val, "energy": e_val, "space": s_val, "bias": weekly}

def _get_status(symbol, k):
    score = k["score"]
    if k["hull"] == 0: return "HOLD FIRE", "PATH OBSTRUCTED", "RED"
    if score >= 75: return "BREACH", "HIGH CONVICTION", "GREEN"
    if score >= 50: return "ASSAULT", "STANDARD ENTRY", "GREEN"
    if score >= 35: return "AMBUSH", "AGGRESSIVE/RISKY", "YELLOW"
    return "HOLD FIRE", "INSUFFICIENT DATA", "RED"

# --- PRIORITY STOP SELECTOR (PREDATOR LINES) ---
def _find_predator_stop(entry, direction, levels):
    """
    PRIORITIZES the 30-minute High/Low (Predator Lines).
    Only falls back to Daily Levels if Predator lines are missing or invalid.
    Adds a 0.1% 'Wick Buffer' to survive retests.
    """
    pred_h = float(levels.get("range30m_high", 0))
    pred_l = float(levels.get("range30m_low", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    
    # WICK BUFFER: 0.1% padding (e.g. $90 on BTC)
    buffer = entry * 0.001 

    if direction == "LONG":
        # 1. Try Predator Low (Highest Accuracy Intraday)
        if pred_l > 0 and pred_l < entry:
            return pred_l - buffer
        
        # 2. Fallback: Daily Support (Structural Floor)
        if ds > 0 and ds < entry:
            return ds - buffer
            
        # 3. Last Resort: 1% Safety Net
        return entry * 0.99

    elif direction == "SHORT":
        # 1. Try Predator High
        if pred_h > 0 and pred_h > entry:
            return pred_h + buffer
            
        # 2. Fallback: Daily Resistance
        if dr > 0 and dr > entry:
            return dr + buffer
            
        return entry * 1.01

    return 0

# --- TRADE PLANNER (CHART ALIGNED) ---
def _get_plan(mode, levels, k, anchor):
    plan = {"valid": False, "bias": "NEUTRAL", "entry": 0, "stop": 0, "targets": [0,0,0]}
    if mode == "HOLD FIRE": return plan

    bias = k['bias']
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    
    # Calculate Stored Energy (Trigger Gap)
    volatility_gap = abs(bo - bd)
    if volatility_gap < (bo * 0.005): volatility_gap = bo * 0.02 

    active_trend = "LONG" if bias == "BULLISH" else ("SHORT" if bias == "BEARISH" else "NEUTRAL")
    if bias == "BEARISH" and anchor > dr: active_trend = "LONG"
    if bias == "BULLISH" and anchor < dr: active_trend = "SHORT"

    # BANKER BUFFER: Front-run the chart line by 1.5% of the gap
    front_run = volatility_gap * 0.015

    if active_trend == "LONG" and bo > 0:
        # Stop prioritizes 30m Low
        stop_price = _find_predator_stop(bo, "LONG", levels)
        
        # Chart-Aligned Fib Targets
        plan = {
            "valid": True, "bias": "LONG", 
            "entry": bo, 
            "stop": stop_price,
            "targets": [
                bo + (volatility_gap * 0.618) - front_run, # T1: Golden Pocket
                bo + (volatility_gap * 1.0) - front_run,   # T2: Measured Move
                bo + (volatility_gap * 1.618) - front_run  # T3: Extension
            ]
        }

    elif active_trend == "SHORT" and bd > 0:
        stop_price = _find_predator_stop(bd, "SHORT", levels)
        
        plan = {
            "valid": True, "bias": "SHORT", 
            "entry": bd, 
            "stop": stop_price, 
            "targets": [
                bd - (volatility_gap * 0.618) + front_run,
                bd - (volatility_gap * 1.0) + front_run,
                bd - (volatility_gap * 1.618) + front_run
            ]
        }
            
    return plan

def _make_key(plan, status):
    if not plan["valid"]: return "NEUTRAL|WEAK|0|0|0|0|0"
    s_txt = "STRONG" if status in ["ASSAULT", "BREACH"] else "WEAK"
    return f"{plan['bias']}|{s_txt}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}"

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

    roe_text = _generate_roe(k, levels, static_anchor)
    mode, advice, color = _get_status(symbol, k)
    plan = _get_plan(mode, levels, k, static_anchor)
    
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
            "roe": roe_text,
            "plan": plan, 
            "levels": levels,
            "mission_key": _make_key(plan, mode),
            "indicator_string": _make_indicator_string(levels)
        }
    }

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
        
        if static_anchor == 0: k = {"score": 0, "wind": 0, "energy": 0, "hull": 0, "space": 0, "bias": "NEUTRAL"}
        else: k = _calc_kinetics(static_anchor, levels, box.get("context", {}))

        mode, advice, color = _get_status(sym, k)
        plan = _get_plan(mode, levels, k, static_anchor)

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
            "symbol": sym, price: price, "score": k["score"], "status": mode, "bias": k["bias"], "metrics": metrics, "color_code": color, "has_trade": plan["valid"], "indicator_string": _make_indicator_string(levels)
        })
    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    return radar_grid