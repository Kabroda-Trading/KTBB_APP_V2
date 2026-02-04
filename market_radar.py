# market_radar.py
# ==============================================================================
# KABRODA MARKET RADAR v8.5 (ASYMMETRIC SNIPER UPGRADE)
# UPDATE: Implements Hybrid Logic: Bullish=20EMA, Bearish=30EMA
# PRESERVED: User's v7.5 Thresholds, Jailbreak Logic, and Magnet Logic.
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
    PRESERVED: User's logic (BTC tighter, Alts looser).
    """
    if "BTC" in symbol:
        return 0.5, True 
    return 0.8, False 

# --- CORE: BEHAVIOR PREDICTION ENGINE ---
def _analyze_topology(symbol, anchor, levels, bias):
    if anchor == 0: return "DATA_SYNC", "GRAY", 0, "NEUTRAL"

    # --- 1. SNIPER INTERRUPT (ASYMMETRIC) ---
    # We check this FIRST. If the "Rare Event" is happening, we alert immediately.
    d_ema20 = float(levels.get("daily_ema20", 0))
    d_ema30 = float(levels.get("daily_ema30", 0))
    d_ema50 = float(levels.get("daily_ema50", 0))
    
    # Only run logic if Pipeline provided the EMAs
    if d_ema30 > 0:
        # BEARISH MODE: Short the Red Line (30 EMA)
        if bias == "BEARISH":
            dist_to_30 = (anchor - d_ema30) / d_ema30
            is_touching_30 = abs(dist_to_30) < 0.005 
            if is_touching_30 and anchor < d_ema50:
                return "SNIPER: RED LINE SHORT", "NEON_RED", 100, "SHORT"
        
        # BULLISH MODE: Long the Green Line (20 EMA) - More Aggressive
        # We use 20 EMA for Bull Runs to catch the "shallow" dips (FOMO).
        if bias == "BULLISH" and d_ema20 > 0:
            dist_to_20 = (anchor - d_ema20) / d_ema20
            is_touching_20 = abs(dist_to_20) < 0.005
            # Ensure Structure is valid (Price > 50 EMA)
            if is_touching_20 and anchor > d_ema50:
                return "SNIPER: GREEN LINE LONG", "NEON_GREEN", 100, "LONG"

    # --- 2. STANDARD LOGIC (Preserved) ---
    runway_limit, allow_jailbreak = _get_thresholds(symbol)

    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    
    is_inverted_up = bo > dr
    is_inverted_dn = bd < ds
    
    runway_up_pct = ((dr - bo) / anchor) * 100 if bo > 0 else 0
    runway_dn_pct = ((bd - ds) / anchor) * 100 if bd > 0 else 0

    # PROFILE 1: THE JAILBREAK
    if is_inverted_up and bias in ["BULLISH", "NEUTRAL"]:
        return ("JAILBREAK (UP)", "PURPLE", 95, "LONG") if allow_jailbreak else ("JAILBREAK (UNCONFIRMED)", "YELLOW", 40, "NEUTRAL")
    
    if is_inverted_dn and bias in ["BEARISH", "NEUTRAL"]:
        return ("JAILBREAK (DOWN)", "PURPLE", 95, "SHORT") if allow_jailbreak else ("JAILBREAK (UNCONFIRMED)", "YELLOW", 40, "NEUTRAL")

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

    if bias == "NEUTRAL":
        return "DRIFTER / NO GRAVITY", "YELLOW", 25, "NEUTRAL"
        
    return f"BIAS BLOCKADE ({bias})", "RED", 15, "NEUTRAL"

# --- HELPER: ROE GENERATOR ---
def _generate_roe(verdict, levels):
    dr = int(float(levels.get("daily_resistance", 0)))
    ds = int(float(levels.get("daily_support", 0)))
    
    if "SNIPER" in verdict:
        # Dynamic advice based on bias
        if "SHORT" in verdict:
            d_ema30 = int(float(levels.get("daily_ema30", 0)))
            return f"CRITICAL ALPHA. Bearish Force + 30 EMA Rejection ({d_ema30}). Structure is aligned. EXECUTE SHORT."
        else:
            d_ema20 = int(float(levels.get("daily_ema20", 0)))
            return f"CRITICAL ALPHA. Bullish Force + 20 EMA Bounce ({d_ema20}). Momentum is strong. EXECUTE LONG."

    if "JAILBREAK" in verdict and "UNCONFIRMED" not in verdict:
        return "CRITICAL STRUCTURAL FAILURE. Triggers are OUTSIDE walls. High probability of EXPANSION. Authorized."
    
    if "SUFFOCATED" in verdict:
        return "WARNING: CHOP ZONE. Trigger is too close to the Daily Wall (Insufficient Runway). Risk of immediate reversal. HOLD FIRE."
    
    if "MAGNET" in verdict:
        return f"STANDARD OPERATION. Clear runway detected. The Daily Wall ({dr} or {ds}) is the MAGNET. Take profit strictly at the Wall."
        
    return "LOW ENERGY / CONFLICT. Market structure opposes gravity. Stand down."

# --- PRIORITY STOP SELECTOR ---
def _find_predator_stop(entry, direction, levels, verdict):
    # NEW: Sniper Stop (1.7% Rule from Wick Analysis)
    if "SNIPER" in verdict:
        if direction == "SHORT": return entry * 1.017
        if direction == "LONG": return entry * 0.983

    # STANDARD PRESERVED LOGIC
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
    
    # Entry Logic
    if "SNIPER" in verdict:
        entry_price = anchor # We enter AT price (the wall touch)
    else:
        entry_price = bo if vector == "LONG" else bd

    stop_price = _find_predator_stop(entry_price, vector, levels, verdict)
    
    # Target Logic
    if "SNIPER" in verdict:
        # Campaign Mode Targets
        if vector == "LONG":
            t1, t2, t3 = entry_price * 1.02, dr, entry_price * 1.05
        else:
            t1, t2, t3 = entry_price * 0.98, ds, entry_price * 0.95
    else:
        # Standard Targets (Fib Expansion)
        gap = abs(bo - bd) or (entry_price * 0.02)
        if vector == "LONG":
            t1, t2, t3 = entry_price + (gap * 0.618), entry_price + gap, entry_price + (gap * 1.618)
        else:
            t1, t2, t3 = entry_price - (gap * 0.618), entry_price - gap, entry_price - (gap * 1.618)

    return {"valid": True, "bias": vector, "entry": entry_price, "stop": stop_price, "targets": [t1, t2, t3]}

def _make_key(plan, verdict):
    if not plan["valid"]: return "NEUTRAL|HOLD|0|0|0|0|0"
    return f"{plan['bias']}|{verdict}|{plan['entry']:.2f}|{plan['stop']:.2f}|{plan['targets'][0]:.2f}|{plan['targets'][1]:.2f}|{plan['targets'][2]:.2f}"

async def analyze_target(symbol, session_id="us_ny_futures"):
    data = await battlebox_pipeline.get_live_battlebox(symbol, "MANUAL", manual_id=session_id)
    if data.get("status") == "ERROR": return {"ok": False}
    
    price = float(data.get("price", 0))
    levels = data.get("battlebox", {}).get("levels", {})
    bias = data.get("battlebox", {}).get("context", {}).get("weekly_force", "NEUTRAL")

    # EXECUTE ENGINE
    verdict, color, score, vector = _analyze_topology(symbol, price, levels, bias)
    plan = _get_plan(verdict, vector, levels, price)
    roe_text = _generate_roe(verdict, levels)

    # HTML HOOK: Helps the front-end know to show the "Sniper Panel"
    is_sniper = "SNIPER" in verdict

    return {
        "ok": True,
        "result": {
            "symbol": symbol, "price": price, "score": score,
            "status": verdict, "color": color, "advice": roe_text, "bias": bias,
            "roe": roe_text, "plan": plan, "levels": levels,
            "mission_key": _make_key(plan, verdict),
            "indicator_string": _make_indicator_string(levels),
            "full_intel": json.dumps(data, default=str),
            "is_sniper_mode": is_sniper 
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
        levels = res.get("battlebox", {}).get("levels", {})
        bias = res.get("battlebox", {}).get("context", {}).get("weekly_force", "NEUTRAL")

        verdict, color, score, vector = _analyze_topology(sym, price, levels, bias)
        plan = _get_plan(verdict, vector, levels, price)
        is_sniper = "SNIPER" in verdict

        metrics = {"hull": {"val": score, "pct": score, "color": color}, "energy": {"val": 0}, "wind": {"val": 0}, "space": {"val": 0}}
        
        radar_grid.append({
            "symbol": sym, "price": price, "score": score, "status": verdict, "bias": bias, 
            "metrics": metrics, "color_code": color, "has_trade": plan["valid"], 
            "indicator_string": _make_indicator_string(levels),
            "full_intel": json.dumps(res, default=str),
            "is_sniper_mode": is_sniper
        })
    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    return radar_grid