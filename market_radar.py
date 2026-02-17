# market_radar.py
# ==============================================================================
# KABRODA MARKET RADAR v9.4 (THE 3-STATE SYSTEM)
# UPDATE: Simplified to Magnet, Jailbreak, or Death Zone. 
# NOW: Kills trades that are too tight (Chop) OR too wide (Exhaustion).
# ==============================================================================
import asyncio
import json
import battlebox_pipeline

TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

def _make_indicator_string(levels):
    if not levels: return "0,0,0,0,0,0"
    return f"{levels.get('breakout_trigger',0)},{levels.get('breakdown_trigger',0)},{levels.get('daily_resistance',0)},{levels.get('daily_support',0)},{levels.get('range30m_high',0)},{levels.get('range30m_low',0)}"

def _get_thresholds(symbol):
    # Returns: (Min Gap %, Max Gap % before Exhaustion, Allow Jailbreaks)
    if "BTC" in symbol: return 0.5, 1.5, True
    if "ETH" in symbol: return 0.8, 2.5, True
    if "SOL" in symbol: return 1.5, 4.0, True
    return 0.8, 2.5, False

def _analyze_topology(symbol, anchor, levels, bias):
    if anchor == 0: return "DATA SYNC", "GRAY", 0, "NEUTRAL", 0.0, "WAITING"

    d_ema20 = float(levels.get("daily_ema20", 0))
    d_ema30 = float(levels.get("daily_ema30", 0))
    d_ema50 = float(levels.get("daily_ema50", 0))
    
    # 1. SNIPER INTERRUPT
    if d_ema30 > 0:
        if bias == "BEARISH":
            if abs((anchor - d_ema30) / d_ema30) < 0.005 and anchor < d_ema50:
                return "SNIPER", "NEON_RED", 100, "SHORT", 0.0, "PERFECT SQUEEZE"
        if bias == "BULLISH" and d_ema20 > 0:
            if abs((anchor - d_ema20) / d_ema20) < 0.005 and anchor > d_ema50:
                return "SNIPER", "NEON_GREEN", 100, "LONG", 0.0, "PERFECT SQUEEZE"

    if bias == "NEUTRAL":
        return "DEATH ZONE", "GRAY", 10, "NEUTRAL", 0.0, "NO TREND"

    min_gap, max_gap, allow_jailbreak = _get_thresholds(symbol)

    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))

    if bias == "BULLISH":
        gap_pct = (abs(dr - bo) / anchor) * 100 if bo > 0 else 0
        is_inverted = bo > dr and bo > 0 and dr > 0
        vector = "LONG"
    else: # BEARISH
        gap_pct = (abs(ds - bd) / anchor) * 100 if bd > 0 else 0
        is_inverted = bd < ds and bd > 0 and ds > 0
        vector = "SHORT"

    # 2. JAILBREAK ZONES
    if is_inverted:
        if gap_pct < min_gap:
            return "DEATH ZONE", "RED", 15, "NEUTRAL", gap_pct, "JAILBREAK (TOO TIGHT)" 
        if allow_jailbreak:
            return "JAILBREAK", "PURPLE", 95, vector, gap_pct, "JAILBREAK"
        else:
            return "DEATH ZONE", "RED", 15, "NEUTRAL", gap_pct, "JAILBREAK (UNCONFIRMED)"

    # 3. MAGNET ZONES & DEATH ZONES
    if gap_pct < min_gap:
        return "DEATH ZONE", "RED", 15, "NEUTRAL", gap_pct, "DEATH ZONE (CHOP)" 
    elif gap_pct > max_gap:
        return "DEATH ZONE", "RED", 15, "NEUTRAL", gap_pct, "DEATH ZONE (EXHAUSTION)"
    else:
        return "MAGNET", "GREEN", 90, vector, gap_pct, "MAGNET"

def _generate_roe(verdict, levels, zone_tier):
    if "CHOP" in zone_tier or "TOO TIGHT" in zone_tier:
        return "WARNING: CHOP ZONE. Gap is too small. High risk of algorithm wicks and mean-reversion. STAND DOWN."
    if "EXHAUSTION" in zone_tier:
        return "WARNING: EXHAUSTION. The gap is massive. Price has likely exhausted its daily ATR. STAND DOWN."
    if "JAILBREAK" in zone_tier:
        return "CRITICAL STRUCTURAL FAILURE. Triggers are OUTSIDE walls with velocity room. Trail stop loosely."
    if "MAGNET" in zone_tier:
        return "STANDARD OPERATION. Gap is in the exact mathematical sweet spot. Take profit strictly at the Wall."
    if "SQUEEZE" in zone_tier:
        return "CRITICAL ALPHA. Price is touching the Daily EMA with momentum alignment. Execute."
    return "LOW ENERGY / CONFLICT. Market structure opposes gravity. Stand down."

def _find_predator_stop(symbol, entry, direction, levels, verdict):
    pred_h = float(levels.get("range30m_high", 0))
    pred_l = float(levels.get("range30m_low", 0))

    if "SOL" in symbol:
        if direction == "LONG": return pred_l if pred_l > 0 else entry * 0.98
        elif direction == "SHORT": return pred_h if pred_h > 0 else entry * 1.02
        return 0
        
    if "ETH" in symbol:
        if "JAILBREAK" in verdict: 
            return pred_l if direction == "LONG" and pred_l > 0 else (pred_h if direction == "SHORT" and pred_h > 0 else entry)
        else:
            eth_buffer = entry * 0.002
            return entry - eth_buffer if direction == "LONG" else entry + eth_buffer

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

    verdict, color, sort_weight, vector, gap_pct, zone_tier = _analyze_topology(symbol, price, levels, bias)
    plan = _get_plan(symbol, verdict, vector, levels, price)
    roe_text = _generate_roe(verdict, levels, zone_tier)
    is_sniper = "SNIPER" in verdict

    return {
        "ok": True,
        "result": {
            "symbol": symbol, "price": price, "score": gap_pct, 
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
            continue
        
        if res.get("status") == "CALIBRATING":
            radar_grid.append({
                "symbol": sym, "price": float(res.get("price", 0)), "sort_weight": 0, "status": "CALIBRATING", 
                "bias": "WAIT", "color_code": "YELLOW", "has_trade": False, "runway_pct": 0, "tier": "WAITING",
                "indicator_string": "0,0,0,0,0,0", "full_intel": json.dumps(res, default=str), "is_sniper_mode": False
            })
            continue

        price = float(res.get("price", 0))
        levels = res.get("battlebox", {}).get("levels", {})
        bias = res.get("battlebox", {}).get("context", {}).get("weekly_force", "NEUTRAL")

        verdict, color, sort_weight, vector, gap_pct, zone_tier = _analyze_topology(sym, price, levels, bias)
        plan = _get_plan(sym, verdict, vector, levels, price)
        is_sniper = "SNIPER" in verdict
        
        radar_grid.append({
            "symbol": sym, "price": price, "sort_weight": sort_weight, "status": verdict, "bias": bias, 
            "color_code": color, "has_trade": plan["valid"], 
            "runway_pct": gap_pct, "tier": zone_tier, 
            "indicator_string": _make_indicator_string(levels),
            "plan": plan, "advice": _generate_roe(verdict, levels, zone_tier), 
            "mission_key": _make_key(plan, verdict),
            "full_intel": json.dumps(res, default=str),
            "is_sniper_mode": is_sniper
        })
        
    radar_grid.sort(key=lambda x: x['sort_weight'], reverse=True)
    return radar_grid