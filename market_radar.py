# market_radar.py
# ==============================================================================
# MARKET RADAR ENGINE v2.7 (TACTICAL COMMAND)
# ==============================================================================
# 1. MATH: Exact replica of Project Omega v17 Kinetic Math.
# 2. FILTERS: Enforces Asset-Specific Kill Switches (The PDF Rules).
# 3. OUTPUT: Overrides status to "GROUNDED" if Kill Switch is hit.
# ==============================================================================

import asyncio
from typing import Dict, Any, List
import battlebox_pipeline
import session_manager

TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "TRXUSDT"]

# ------------------------------------------------------------------------------
# 1. KINETIC MATH
# ------------------------------------------------------------------------------
def _calculate_side_score(anchor_price, levels, context, side):
    """
    Calculates Score & Rich Metrics for a specific direction.
    """
    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    if dr == 0 or ds == 0: return 0, {}, True # Blocked

    # A. ENERGY (30pts) - Volatility
    range_size = abs(dr - ds)
    bps = (range_size / anchor_price) * 10000 if anchor_price > 0 else 500
    is_exhausted = bps > 350
    
    e_val = 30 if bps < 100 else (15 if bps < 250 else 0)
    e_text = f"SUPER COILED ({int(bps)})" if bps < 100 else (f"STANDARD ({int(bps)})" if bps < 250 else f"LOOSE ({int(bps)})")
    e_color = "CYAN" if bps < 100 else ("GREEN" if bps < 250 else "RED")
    e_pct = min(100, (e_val / 30) * 100)

    # B. SPACE (30pts) - R-Multiple (Trigger to Wall)
    atr = float(levels.get("atr", 0)) or (anchor_price * 0.01)
    trigger = float(levels.get("breakout_trigger", 0)) if side == "LONG" else float(levels.get("breakdown_trigger", 0))
    target = dr if side == "LONG" else ds
    gap = abs(target - trigger)
    r_mult = gap / atr if atr > 0 else 0
    
    is_blocked = r_mult < 1.0 # Hard Block
    s_val = 30 if r_mult > 2.0 else (15 if r_mult > 1.0 else 0)
    s_text = f"SUPERSONIC ({r_mult:.1f}R)" if r_mult > 2.0 else (f"GRIND ({r_mult:.1f}R)" if r_mult > 1.0 else f"BLOCKED ({r_mult:.1f}R)")
    s_color = "CYAN" if r_mult > 2.0 else ("GREEN" if r_mult > 1.0 else "RED")
    s_pct = min(100, (s_val / 30) * 100)

    # C. WIND (20pts) - Momentum
    weekly = context.get("weekly_force", "NEUTRAL")
    slope = float(levels.get("slope", 0.0))
    is_aligned = (side == "LONG" and weekly == "BULLISH") or (side == "SHORT" and weekly == "BEARISH")
    
    w_score = 0
    if is_aligned: w_score += 10
    if (side == "LONG" and slope > 0.1) or (side == "SHORT" and slope < -0.1): w_score += 10
    
    w_text = "TAILWIND" if w_score >= 10 else "HEADWIND"
    w_color = "CYAN" if w_score == 20 else ("GREEN" if w_score == 10 else "YELLOW")
    w_pct = min(100, (w_score / 20) * 100)

    # D. HULL (20pts) - Structure (Boosted Weight)
    struct = float(levels.get("structure_score", 0.0))
    h_val = 20 if struct > 0.7 else (10 if struct > 0.4 else 0)
    h_text = f"SOLID ({struct:.1f})" if struct > 0.7 else f"WEAK ({struct:.1f})"
    h_color = "CYAN" if struct > 0.7 else ("YELLOW" if struct > 0.4 else "RED")
    h_pct = min(100, (h_val / 20) * 100)
    
    total_score = e_val + s_val + w_score + h_val
    total_blocked = is_exhausted or is_blocked

    metrics = {
        "energy": {"val": e_val, "text": e_text, "color": e_color, "pct": e_pct},
        "space": {"val": s_val, "text": s_text, "color": s_color, "pct": s_pct},
        "wind": {"val": w_score, "text": w_text, "color": w_color, "pct": w_pct},
        "hull": {"val": h_val, "text": h_text, "color": h_color, "pct": h_pct}
    }
    
    return total_score, metrics, total_blocked

# ------------------------------------------------------------------------------
# 2. TACTICAL FILTERS (THE KILL SWITCH)
# ------------------------------------------------------------------------------
def _apply_tactical_filter(symbol, analysis):
    """
    Applies ASSET-SPECIFIC Kill Switches based on Project Omega Protocol.
    Overrides status to GROUNDED if rules are violated.
    """
    score = analysis["score"]
    metrics = analysis["metrics"]
    
    kill_reason = None
    
    # Extract Values
    s_val = metrics["space"]["val"]
    e_val = metrics["energy"]["val"]
    w_val = metrics["wind"]["val"]
    h_val = metrics["hull"]["val"]

    # --- TIER 1: BTC (The Flagship) ---
    if "BTC" in symbol:
        # Rule: Score > 50 AND Space > 1.5R (Value 15+)
        if score < 50: kill_reason = "WEAK SIGNAL (<50)"
        elif s_val < 15: kill_reason = "BLOCKED SPACE (<1.5R)"

    # --- TIER 1: SOL (The Athlete) ---
    elif "SOL" in symbol:
        # Rule: High Energy Required (> 0)
        if e_val == 0: kill_reason = "LOW ENERGY (DEAD MONEY)"
        elif score < 60: kill_reason = "WEAK SIGNAL (<60)"

    # --- TIER 2: ETH (The Workhorse) ---
    elif "ETH" in symbol:
        # Rule: Needs Space. 
        if s_val == 0: kill_reason = "CONGESTED (NO SPACE)"
        elif score < 55: kill_reason = "CHOP ZONE (50-55)"

    # --- TIER 2: TRX (The Sniper) ---
    elif "TRX" in symbol:
        # Rule: Structure (Hull) must be solid (Value 20)
        if h_val < 20: kill_reason = "WEAK STRUCTURE (MESSY)"
        elif score < 55: kill_reason = "WEAK SIGNAL"

    # --- TIER 3: DOGE (The Wildcard) ---
    elif "DOGE" in symbol:
        # Rule: Wind > 10 Mandatory
        if w_val < 10: kill_reason = "NO MOMENTUM (NO WIND)"
        elif score < 60: kill_reason = "KILL ZONE (<60)"

    # --- TIER 4: XRP (The Event) ---
    elif "XRP" in symbol:
        # Rule: High Score Only
        if score < 65: kill_reason = "NO CATALYST (WAIT FOR 65+)"

    # --- APPLY OVERRIDE ---
    if kill_reason:
        analysis["status"] = "GROUNDED"
        analysis["advice"] = f"⛔ KILL SWITCH: {kill_reason}"
        # Visually grey out the bars to indicate 'Offline'
        for k in metrics: metrics[k]['color'] = 'RED'
    
    return analysis

def _analyze_session_kinetics(levels, context, price, symbol):
    anchor = float(levels.get("session_open_price") or price)
    
    score_l, m_long, block_l = _calculate_side_score(anchor, levels, context, "LONG")
    score_s, m_short, block_s = _calculate_side_score(anchor, levels, context, "SHORT")

    bias = "NEUTRAL"
    final_metrics = m_long
    final_score = score_l
    
    # 1. Determine Raw Bias
    if block_l and block_s:
        bias = "GROUNDED"; final_score = max(score_l, score_s)
    elif block_l:
        bias = "SHORT"; final_score = score_s; final_metrics = m_short
    elif block_s:
        bias = "LONG"; final_score = score_l; final_metrics = m_long
    elif score_l > score_s + 10:
        bias = "LONG"; final_score = score_l; final_metrics = m_long
    elif score_s > score_l + 10:
        bias = "SHORT"; final_score = score_s; final_metrics = m_short
    else:
        final_score = max(score_l, score_s)
        final_metrics = m_long if score_l >= score_s else m_short
    
    # 2. Determine Raw Status
    status = "DOGFIGHT"
    if bias == "GROUNDED" or final_score <= 45: status = "GROUNDED"
    elif final_score >= 75: status = "SUPERSONIC"
    elif final_score >= 50: status = "SNIPER"

    advice = "WAIT FOR CONVICTION."
    if bias == "LONG": advice = "LOOK FOR LONGS."
    if bias == "SHORT": advice = "LOOK FOR SHORTS."

    analysis = {
        "score": final_score, "status": status, "bias": bias, 
        "metrics": final_metrics, "advice": advice
    }
    
    # 3. APPLY TACTICAL KILL SWITCH (The New Logic)
    return _apply_tactical_filter(symbol, analysis)

def _generate_flight_plan(levels):
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    r30_high = float(levels.get("range30m_high", 0))
    r30_low = float(levels.get("range30m_low", 0))
    
    plans = {"LONG": {"valid": False}, "SHORT": {"valid": False}}
    
    # Basic R Check
    if bo > 0 and r30_low > 0:
        risk = bo - r30_low
        if risk > 0: plans["LONG"] = {"valid": True, "entry": bo, "stop": r30_low, "targets": [bo+risk, bo+2*risk, bo+3*risk]}

    if bd > 0 and r30_high > 0:
        risk = r30_high - bd
        if risk > 0: plans["SHORT"] = {"valid": True, "entry": bd, "stop": r30_high, "targets": [bd-risk, bd-2*risk, bd-3*risk]}
            
    return plans

# ------------------------------------------------------------------------------
# 3. API ENDPOINTS
# ------------------------------------------------------------------------------
async def scan_sector(session_id="us_ny_futures"):
    radar_grid = []
    tasks = [battlebox_pipeline.get_live_battlebox(sym, session_mode="MANUAL", manual_id=session_id) for sym in TARGETS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for sym, res in zip(TARGETS, results):
        if isinstance(res, Exception) or res.get("status") == "ERROR":
            radar_grid.append({"symbol": sym, "score": 0, "status": "OFFLINE", "metrics": {}})
            continue

        price = float(res.get("price", 0))
        box = res.get("battlebox", {})
        levels = box.get("levels", {})
        context = box.get("context", {})
        
        # Now passes SYMBOL to apply specific kill switches
        analysis = _analyze_session_kinetics(levels, context, price, sym)

        radar_grid.append({
            "symbol": sym, "price": price, 
            "score": analysis["score"], "status": analysis["status"], 
            "bias": analysis["bias"], "metrics": analysis["metrics"],
            "advice": analysis.get("advice", "")
        })

    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    return radar_grid

async def analyze_target(symbol, session_id="us_ny_futures"):
    data = await battlebox_pipeline.get_live_battlebox(symbol=symbol, session_mode="MANUAL", manual_id=session_id)
    if data.get("status") == "ERROR": return {"ok": False}

    price = float(data.get("price", 0))
    box = data.get("battlebox", {})
    levels = box.get("levels", {})
    context = box.get("context", {})
    
    analysis = _analyze_session_kinetics(levels, context, price, symbol)
    plans = _generate_flight_plan(levels)

    return {
        "symbol": symbol, "score": analysis["score"], "status": analysis["status"], 
        "bias": analysis["bias"], "metrics": analysis["metrics"], "advice": analysis["advice"],
        "plans": plans, "levels": levels, "price": price
    }