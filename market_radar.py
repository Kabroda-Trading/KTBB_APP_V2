# market_radar.py
# ==============================================================================
# KABRODA MARKET RADAR v9.1 (TRI-ASSET UPGRADE)
# UPDATE: Engine now applies bespoke math for BTC, ETH, and SOL automatically.
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
    if "BTC" in symbol:
        return 0.5, True   # BTC: 0.5% Gap, Jailbreaks Allowed
    if "ETH" in symbol:
        return 0.8, True   # ETH: 0.8% Gap, Jailbreaks Allowed
    if "SOL" in symbol:
        return 1.5, True   # SOL: 1.5% Gap, Jailbreaks Allowed
    return 0.8, False      # Default fallback

# --- CORE: BEHAVIOR PREDICTION ENGINE ---
def _analyze_topology(symbol, anchor, levels, bias):
    if anchor == 0: return "DATA_SYNC", "GRAY", 0, "NEUTRAL"

    # --- 1. SNIPER INTERRUPT (ASYMMETRIC) ---
    d_ema20 = float(levels.get("daily_ema20", 0))
    d_ema30 = float(levels.get("daily_ema30", 0))
    d_ema50 = float(levels.get("daily_ema50", 0))
    
    if d_ema30 > 0:
        if bias == "BEARISH":
            dist_to_30 = (anchor - d_ema30) / d_ema30
            is_touching_30 = abs(dist_to_30) < 0.005 
            if is_touching_30 and anchor < d_ema50:
                return "SNIPER: RED LINE SHORT", "NEON_RED", 100, "SHORT"
        
        if bias == "BULLISH" and d_ema20 > 0:
            dist_to_20 = (anchor - d_ema20) / d_ema20
            is_touching_20 = abs(dist_to_20) < 0.005
            if is_touching_20 and anchor > d_ema50:
                return "SNIPER: GREEN LINE LONG", "NEON_GREEN", 100, "LONG"

    # --- 2. STANDARD LOGIC ---
    runway_limit, allow_jailbreak = _get_thresholds(symbol)

    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    
    # Check Inversion (Jailbreak Conditions)
    is_inverted_up = bo > dr
    is_inverted_dn = bd < ds
    
    gap_up_pct = (abs(bo - dr) / anchor) * 100 if bo > 0 else 0
    gap_dn_pct = (abs(bd - ds) / anchor) * 100 if bd > 0 else 0

    # JAILBREAK LOGIC
    if is_inverted_up and bias in ["BULLISH", "NEUTRAL"]:
        if gap_up_pct < runway_limit:
            return f"JAILBREAK UP (RISKY GAP: {gap_up_pct:.2f}%)", "ORANGE", 20, "NEUTRAL"
        return ("JAILBREAK (UP)", "PURPLE", 95, "LONG") if allow_jailbreak else ("JAILBREAK (UNCONFIRMED)", "YELLOW", 40, "NEUTRAL")
    
    if is_inverted_dn and bias in ["BEARISH", "NEUTRAL"]:
        if gap_dn_pct < runway_limit:
            return f"JAILBREAK DOWN (RISKY GAP: {gap_dn_pct:.2f}%)", "ORANGE", 20, "NEUTRAL"
        return ("JAILBREAK (DOWN)", "PURPLE", 95, "SHORT") if allow_jailbreak else ("JAILBREAK (UNCONFIRMED)", "YELLOW", 40, "NEUTRAL")

    # STANDARD RUNWAY LOGIC
    runway_up_pct = ((dr - bo) / anchor) * 100 if bo > 0 else 0
    runway_dn_pct = ((bd - ds) / anchor) * 100 if bd > 0 else 0

    if bias == "BULLISH" and 0 < runway_up_pct < runway_limit:
        return f"SUFFOCATED ({runway_up_pct:.2f}%)", "RED", 10, "NEUTRAL"
    if bias == "BEARISH" and 0 < runway_dn_pct < runway_limit:
        return f"SUFFOCATED ({runway_dn_pct:.2f}%)", "RED", 10, "NEUTRAL"

    if bias == "BULLISH" and runway_up_pct >= runway_limit:
        return f"MAGNET LONG ({runway_up_pct:.2f}%)", "GREEN", 75, "LONG"
    if bias == "BEARISH" and runway_dn_pct >= runway_limit:
        return f"MAGNET SHORT ({runway_dn_pct:.2f}%)", "GREEN", 75, "SHORT"

    if bias == "NEUTRAL":
        return "DRIFTER / NO GRAVITY", "YELLOW", 25, "NEUTRAL"
        
    return f"BIAS BLOCKADE ({bias})", "RED", 15, "NEUTRAL"

def _generate_roe(verdict, levels):
    dr = int(float(levels.get("daily_resistance", 0)))
    ds = int(float(levels.get("daily_support", 0)))
    
    if "SNIPER" in verdict:
        if "SHORT" in verdict:
            d_ema30 = int(float(levels.get("daily_ema30", 0)))
            return f"CRITICAL ALPHA. Bearish Force + 30 EMA Rejection ({d_ema30}). Structure is aligned. EXECUTE SHORT."
        else:
            d_ema20 = int(float(levels.get("daily_ema20", 0)))
            return f"CRITICAL ALPHA. Bullish Force + 20 EMA Bounce ({d_ema20}). Momentum is strong. EXECUTE LONG."

    if "JAILBREAK" in verdict:
        if "RISKY GAP" in verdict:
            return "WARNING: TIGHT SPREAD. Structure is broken but the gap is too small. Volatility Trap likely. STAND DOWN."
        if "UNCONFIRMED" not in verdict:
            return "CRITICAL STRUCTURAL FAILURE. Triggers are OUTSIDE walls with velocity room. High probability of EXPANSION. Authorized."
    
    if "SUFFOCATED" in verdict:
        return "WARNING: CHOP ZONE. Trigger is too close to the Daily Wall (Insufficient Runway). Risk of immediate reversal. HOLD FIRE."
    
    if "MAGNET" in verdict:
        return f"STANDARD OPERATION. Clear runway detected. The Daily Wall ({dr} or {ds}) is the MAGNET. Take profit strictly at the Wall."
        
    return "LOW ENERGY / CONFLICT. Market structure opposes gravity. Stand down."

def _find_predator_stop(symbol, entry, direction, levels, verdict):
    pred_h = float(levels.get("range30m_high", 0))
    pred_l = float(levels.get("range30m_low", 0))

    # --- ASSET SPECIFIC STOP LOSS LOGIC ---
    
    # 1. SOLANA: Extreme Volatility = WIDE Stops only (30m Range)
    if "SOL" in symbol:
        if direction == "LONG": return pred_l if pred_l > 0 else entry * 0.98
        elif direction == "SHORT": return pred_h if pred_h > 0 else entry * 1.02
        return 0
        
    # 2. ETHEREUM: Moderate Volatility = Armored Stops (0.20% buffer)
    if "ETH" in symbol:
        if "JAILBREAK" in verdict: # Wide stops for Jailbreaks
            return pred_l if direction == "LONG" and pred_l > 0 else (pred_h if direction == "SHORT" and pred_h > 0 else entry)
        else:
            eth_buffer = entry * 0.002
            return entry - eth_buffer if direction == "LONG" else entry + eth_buffer

    # 3. BITCOIN / DEFAULT: Low Volatility = Tight Stops (0.10% buffer)
    if "SNIPER" in verdict:
        if direction == "SHORT": return entry * 1.017
        if direction == "LONG": return entry * 0.983

    btc_buffer = entry * 0.001 
    if direction == "LONG":
        if pred_l > 0 and pred_l < entry: return pred_l - btc_buffer
        return entry * 0.99 
    elif direction == "SHORT":
        if pred_h > 0 and pred_h > entry: return pred_h + btc_buffer
        return entry * 1.01 
        
    return 0

def _get_plan(symbol, verdict, vector, levels, anchor):
    plan = {"valid": False, "bias": "NEUTRAL", "entry": 0, "stop": 0, "targets": [0,0,0]}
    
    if vector == "NEUTRAL": return plan

    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    
    if "SNIPER" in verdict:
        entry_price = anchor 
    else:
        entry_price = bo if vector == "LONG" else bd

    stop_price = _find_predator_stop(symbol, entry_price, vector, levels, verdict)
    
    if "SNIPER" in verdict:
        if vector == "LONG":
            t1, t2, t3 = entry_price * 1.02, dr, entry_price * 1.05
        else:
            t1, t2, t3 = entry_price * 0.98, ds, entry_price * 0.95
    else:
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
    
    if data.get("status") == "CALIBRATING":
        return {
            "ok": True,
            "result": {
                "symbol": symbol, "price": float(data.get("price", 0)), "score": 0,
                "status": "CALIBRATING", "color": "YELLOW", "advice": "Waiting for 30m Candle Close...", 
                "bias": "WAIT", "roe": "WAITING", "plan": {"valid": False}, "levels": {},
                "mission_key": "WAIT", "indicator_string": "0,0,0,0,0,0", "full_intel": json.dumps(data, default=str),
                "is_sniper_mode": False
            }
        }

    price = float(data.get("price", 0))
    levels = data.get("battlebox", {}).get("levels", {})
    bias = data.get("battlebox", {}).get("context", {}).get("weekly_force", "NEUTRAL")

    verdict, color, score, vector = _analyze_topology(symbol, price, levels, bias)
    plan = _get_plan(symbol, verdict, vector, levels, price)
    roe_text = _generate_roe(verdict, levels)
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
        
        if res.get("status") == "CALIBRATING":
            price = float(res.get("price", 0))
            metrics = {"hull": {"val": 0, "pct": 0, "color": "YELLOW"}, "energy": {"val": 0}, "wind": {"val": 0}, "space": {"val": 0}}
            radar_grid.append({
                "symbol": sym, "price": price, "score": 0, "status": "CALIBRATING", "bias": "WAIT", 
                "metrics": metrics, "color_code": "YELLOW", "has_trade": False, 
                "indicator_string": "0,0,0,0,0,0", "full_intel": json.dumps(res, default=str), "is_sniper_mode": False
            })
            continue

        price = float(res.get("price", 0))
        levels = res.get("battlebox", {}).get("levels", {})
        bias = res.get("battlebox", {}).get("context", {}).get("weekly_force", "NEUTRAL")

        verdict, color, score, vector = _analyze_topology(sym, price, levels, bias)
        plan = _get_plan(sym, verdict, vector, levels, price)
        is_sniper = "SNIPER" in verdict

        metrics = {"hull": {"val": score, "pct": score, "color": color}, "energy": {"val": 0}, "wind": {"val": 0}, "space": {"val": 0}}
        
        radar_grid.append({
            "symbol": sym, "price": price, "score": score, "status": verdict, "bias": bias, 
            "metrics": metrics, "color_code": color, "has_trade": plan["valid"], 
            "indicator_string": _make_indicator_string(levels),
            "plan": plan, "advice": _generate_roe(verdict, levels), "mission_key": _make_key(plan, verdict),
            "full_intel": json.dumps(res, default=str),
            "is_sniper_mode": is_sniper
        })
    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    return radar_grid