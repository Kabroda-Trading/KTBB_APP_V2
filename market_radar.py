# market_radar.py
# ==============================================================================
# KABRODA MARKET RADAR v7.5 (INTEL UPGRADE)
# UPDATE: Added 'full_intel' payload to allow "Copy to Gem" functionality.
# PRESERVED: All v7.3 Thresholds and Logic.
# ==============================================================================
import asyncio
import json
import battlebox_pipeline

TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# --- HELPER: DATA PACKAGING ---
def _make_indicator_string(levels):
    if not levels: return "0,0,0,0,0,0"
    return f"{levels.get('breakout_trigger',0)},{levels.get('breakdown_trigger',0)},{levels.get('daily_resistance',0)},{levels.get('daily_support',0)},{levels.get('range30m_high',0)},{levels.get('range30m_low',0)}"

# --- HELPER: ASSET-SPECIFIC THRESHOLDS ---
def _get_thresholds(symbol):
    """
    Returns (Runway_Min_Pct, Allow_Solo_Jailbreak)
    FIX: Values must be in PERCENT format (e.g. 0.5 for 0.5%), not decimal.
    """
    if "BTC" in symbol:
        return 0.5, True   # v7.3 Logic Preserved
    return 0.8, False      # v7.3 Logic Preserved

# --- CORE: BEHAVIOR PREDICTION ENGINE ---
def _analyze_topology(symbol, anchor, levels, bias):
    if anchor == 0: return "DATA_SYNC", "GRAY", 0, "NEUTRAL"

    # 1. GET THRESHOLDS
    runway_limit, allow_jailbreak = _get_thresholds(symbol)

    # 2. PARSE LEVELS
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    
    # 3. DEFINE TOPOLOGY
    is_inverted_up = bo > dr
    is_inverted_dn = bd < ds
    
    # 4. CALCULATE RUNWAY (Note: Multiplied by 100 for Percentage)
    runway_up_pct = ((dr - bo) / anchor) * 100 if bo > 0 else 0
    runway_dn_pct = ((bd - ds) / anchor) * 100 if bd > 0 else 0

    # --- BEHAVIOR PROFILING ---

    # PROFILE 1: THE JAILBREAK
    if is_inverted_up and bias in ["BULLISH", "NEUTRAL"]:
        if allow_jailbreak:
            return "JAILBREAK (UP)", "PURPLE", 95, "LONG"
        else:
            return "JAILBREAK (UNCONFIRMED)", "YELLOW", 40, "NEUTRAL" 
    
    if is_inverted_dn and bias in ["BEARISH", "NEUTRAL"]:
        if allow_jailbreak:
            return "JAILBREAK (DOWN)", "PURPLE", 95, "SHORT"
        else:
            return "JAILBREAK (UNCONFIRMED)", "YELLOW", 40, "NEUTRAL" 

    # PROFILE 2: THE SUFFOCATION
    if bias == "BULLISH" and 0 < runway_up_pct < runway_limit:
        return f"SUFFOCATED ({runway_up_pct:.2f}%)", "RED", 10, "NEUTRAL"
    if bias == "BEARISH" and 0 < runway_dn_pct < runway_limit:
        return f"SUFFOCATED ({runway_dn_pct:.2f}%)", "RED", 10, "NEUTRAL"

    # PROFILE 3: THE MAGNET
    if bias == "BULLISH" and runway_up_pct >= runway_limit:
        return f"MAGNET LONG ({runway_up_pct:.2f}%)", "GREEN", 75, "LONG"
    if bias == "BEARISH" and runway_dn_pct >= runway_limit:
        return f"MAGNET SHORT ({runway_dn_pct:.2f}%)", "GREEN", 75, "SHORT"

    # PROFILE 4: BIAS BLOCKADE
    if bias == "NEUTRAL":
        return "DRIFTER / NO GRAVITY", "YELLOW", 25, "NEUTRAL"
        
    return f"BIAS BLOCKADE ({bias})", "RED", 15, "NEUTRAL"

# --- HELPER: ROE GENERATOR ---
def _generate_roe(verdict, levels):
    dr = int(float(levels.get("daily_resistance", 0)))
    ds = int(float(levels.get("daily_support", 0)))

    if "JAILBREAK (UP)" in verdict or "JAILBREAK (DOWN)" in verdict:
        return "CRITICAL STRUCTURAL FAILURE. Triggers are OUTSIDE walls. High probability of EXPANSION. Authorized."
    
    if "JAILBREAK (UNCONFIRMED)" in verdict:
        return "WARNING: VOLATILITY TRAP. Jailbreak detected on Altcoin without BTC confirmation. High risk of fake-out. Stand down or wait for BTC."

    if "SUFFOCATED" in verdict:
        return "WARNING: CHOP ZONE. Trigger is too close to the Daily Wall (Insufficient Runway). Risk of immediate reversal. HOLD FIRE."
    
    if "MAGNET" in verdict:
        return f"STANDARD OPERATION. Clear runway detected. The Daily Wall ({dr} or {ds}) is the MAGNET. Take profit strictly at the Wall."
        
    return "LOW ENERGY / CONFLICT. Market structure opposes gravity. Stand down."

# --- PRIORITY STOP SELECTOR ---
def _find_predator_stop(entry, direction, levels):
    pred_h = float(levels.get("range30m_high", 0))
    pred_l = float(levels.get("range30m_low", 0))
    buffer = entry * 0.001 

    if direction == "LONG":
        if pred_l > 0 and pred_l < entry: return pred_l - buffer
        return entry * 0.99 

    elif direction == "SHORT":
        if pred_h > 0 and pred_h > entry: return pred_h + buffer
        return entry * 1.01 
    return 0

# --- TRADE PLANNER ---
def _get_plan(verdict, vector, levels, anchor):
    plan = {"valid": False, "bias": "NEUTRAL", "entry": 0, "stop": 0, "targets": [0,0,0]}
    
    if vector == "NEUTRAL": return plan

    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    
    entry_price = bo if vector == "LONG" else bd
    stop_price = _find_predator_stop(entry_price, vector, levels)
    volatility_gap = abs(bo - bd)
    if volatility_gap < (entry_price * 0.005): volatility_gap = entry_price * 0.02

    if "MAGNET" in verdict:
        t1 = dr if vector == "LONG" else ds
        t2 = t1 
        t3 = t1 
    elif "JAILBREAK" in verdict:
        if vector == "LONG":
            t1 = entry_price + (volatility_gap * 0.618)
            t2 = entry_price + (volatility_gap * 1.0)
            t3 = entry_price + (volatility_gap * 1.618)
        else:
            t1 = entry_price - (volatility_gap * 0.618)
            t2 = entry_price - (volatility_gap * 1.0)
            t3 = entry_price - (volatility_gap * 1.618)
    else:
        return plan 

    return {
        "valid": True, "bias": vector, 
        "entry": entry_price, "stop": stop_price, 
        "targets": [t1, t2, t3]
    }

def _make_key(plan, verdict):
    if not plan["valid"]: return "NEUTRAL|HOLD|0|0|0|0|0"
    return f"{plan['bias']}|{verdict}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}"

async def analyze_target(symbol, session_id="us_ny_futures"):
    data = await battlebox_pipeline.get_live_battlebox(symbol, "MANUAL", manual_id=session_id)
    if data.get("status") == "ERROR": return {"ok": False}
    
    price = float(data.get("price", 0))
    box = data.get("battlebox", {})
    levels = box.get("levels", {})
    context = box.get("context", {})
    static_anchor = float(levels.get("anchor_price", 0))
    bias = context.get("weekly_force", "NEUTRAL")

    # --- EXECUTE BEHAVIOR ENGINE ---
    verdict, color, score, vector = _analyze_topology(symbol, static_anchor, levels, bias)
    plan = _get_plan(verdict, vector, levels, static_anchor)
    roe_text = _generate_roe(verdict, levels)

    return {
        "ok": True,
        "result": {
            "symbol": symbol, "price": price, "score": score,
            "status": verdict, "color": color, "advice": roe_text, "bias": bias,
            "metrics": {"hull": score, "energy": 0, "wind": 0, "space": 0},
            "roe": roe_text,
            "plan": plan, 
            "levels": levels,
            "mission_key": _make_key(plan, verdict),
            "indicator_string": _make_indicator_string(levels),
            "full_intel": json.dumps(data, default=str) # Added for manual check
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
        context = box.get("context", {})
        static_anchor = float(levels.get("anchor_price", 0))
        bias = context.get("weekly_force", "NEUTRAL")

        # --- EXECUTE BEHAVIOR ENGINE ---
        verdict, color, score, vector = _analyze_topology(sym, static_anchor, levels, bias)
        plan = _get_plan(verdict, vector, levels, static_anchor)

        # --- ADD RAW INTEL FOR FRONTEND COPY ---
        # This captures the WHOLE packet (Bias, Levels, POC, etc.)
        full_intel = json.dumps(res, default=str)

        metrics = {
            "hull": {"val": score, "pct": score, "color": color},
            "energy": {"val": 0, "pct": 0, "color": "GRAY"},
            "wind": {"val": 0, "pct": 0, "color": "GRAY"},
            "space": {"val": 0, "pct": 0, "color": "GRAY"}
        }

        radar_grid.append({
            "symbol": sym, price: price, "score": score, "status": verdict, "bias": bias, 
            "metrics": metrics, "color_code": color, "has_trade": plan["valid"], 
            "indicator_string": _make_indicator_string(levels),
            "full_intel": full_intel # <--- THIS IS THE NEW PAYLOAD
        })
    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    return radar_grid