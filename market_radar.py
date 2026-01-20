# market_radar.py
# ==============================================================================
# MARKET RADAR ENGINE (v2.4 - TIGHTENED MATH)
# ==============================================================================
# 1. MATH: Raised thresholds for "Solid" Structure and "Strong" Wind.
# 2. LOGIC: Bias Detection (Long vs Short) to filter junk.
# 3. PREP: Pre-calculates Flight Plans.
# ==============================================================================

import asyncio
from typing import Dict, Any, List
import battlebox_pipeline
import session_manager

TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "TRXUSDT"]

# ------------------------------------------------------------------------------
# 1. METRIC CALCULATOR (STRICTER WEIGHTS)
# ------------------------------------------------------------------------------
def _calculate_rich_metrics(bps, r_mult, slope, struct_score):
    """Generates the Text, Color, and Percentage objects with TIGHTER standards."""
    metrics = {}

    # A. ENERGY (30pts) - Volatility
    # < 100 is Coiled (Good), > 350 is Exhausted (Bad)
    e_val = 30 if bps < 100 else (15 if bps < 200 else 0)
    e_text = f"SUPER COILED ({int(bps)})" if bps < 100 else (f"STANDARD ({int(bps)})" if bps < 250 else f"LOOSE ({int(bps)})")
    e_color = "CYAN" if bps < 100 else ("GREEN" if bps < 250 else "RED")
    e_pct = min(100, (e_val / 30) * 100)
    metrics["energy"] = {"val": e_val, "text": e_text, "color": e_color, "pct": e_pct}

    # B. SPACE (30pts) - R-Multiple
    # Must be > 1.0 to trade. > 2.0 is Supersonic.
    s_val = 30 if r_mult > 2.0 else (15 if r_mult > 1.0 else 0)
    s_text = f"SUPERSONIC ({r_mult:.1f}R)" if r_mult > 2.0 else (f"GRIND ({r_mult:.1f}R)" if r_mult > 1.0 else f"BLOCKED ({r_mult:.1f}R)")
    s_color = "CYAN" if r_mult > 2.0 else ("GREEN" if r_mult > 1.0 else "RED")
    s_pct = min(100, (s_val / 30) * 100)
    metrics["space"] = {"val": s_val, "text": s_text, "color": s_color, "pct": s_pct}

    # C. WIND (20pts) - Momentum / Slope
    # Tightened: Needs > 0.25 for Strong. Added "HEADWIND" for negative against bias.
    # Note: This function assumes we are checking the 'active' side logic in the caller
    w_val = 0
    w_text = "NEUTRAL"
    w_color = "YELLOW"
    
    if abs(slope) > 0.25:
        w_val = 20; w_text = "STRONG TAILWIND"; w_color = "CYAN"
    elif abs(slope) > 0.1:
        w_val = 10; w_text = "MILD BREEZE"; w_color = "GREEN"
    else:
        w_val = 0; w_text = "STAGNANT"; w_color = "YELLOW"

    w_pct = min(100, (w_val / 20) * 100)
    metrics["wind"] = {"val": w_val, "text": w_text, "color": w_color, "pct": w_pct}

    # D. HULL (10pts) - Market Structure
    # Tightened: Need > 0.70 to be SOLID. 0.5 is just okay.
    h_val = 10 if struct_score > 0.7 else 0
    h_text = f"SOLID ({struct_score:.1f})" if struct_score > 0.7 else (f"OKAY ({struct_score:.1f})" if struct_score > 0.4 else f"FRAGILE ({struct_score:.1f})")
    h_color = "CYAN" if struct_score > 0.7 else ("YELLOW" if struct_score > 0.4 else "RED")
    h_pct = min(100, (h_val / 10) * 100)
    metrics["hull"] = {"val": h_val, "text": h_text, "color": h_color, "pct": h_pct}

    total_score = e_val + s_val + w_val + h_val
    return metrics, total_score

# ------------------------------------------------------------------------------
# 2. ANALYSIS ENGINE (BIAS BRAIN)
# ------------------------------------------------------------------------------
def _analyze_session_kinetics(levels, price):
    """Determines Bias based on dual-side analysis."""
    dr = float(levels.get("daily_resistance", 0) or 0)
    ds = float(levels.get("daily_support", 0) or 0)
    if dr == 0 or ds == 0: return {"score": 0, "status": "OFFLINE", "metrics": {}, "bias": "NONE"}

    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    atr = float(levels.get("atr", 0)) or (price * 0.01)
    
    range_size = abs(dr - ds)
    bps = (range_size / price) * 10000 if price > 0 else 500
    slope = float(levels.get("slope", 0))
    struct = float(levels.get("structure_score", 0))

    r_long = abs(dr - bo) / atr if atr > 0 else 0
    r_short = abs(ds - bd) / atr if atr > 0 else 0

    m_long, s_long = _calculate_rich_metrics(bps, r_long, slope, struct)
    m_short, s_short = _calculate_rich_metrics(bps, r_short, slope, struct)

    # Bias Logic
    bias = "NEUTRAL"
    final_metrics = m_long
    final_score = s_long
    
    if r_long < 1.0 and r_short < 1.0:
        bias = "GROUNDED"
        final_score = max(s_long, s_short)
        # Even if score is 40, if R < 1.0, it is technically zero useful score
    elif r_long > r_short + 0.5:
        bias = "LONG"
        final_metrics = m_long
        final_score = s_long
    elif r_short > r_long + 0.5:
        bias = "SHORT"
        final_metrics = m_short
        final_score = s_short
    else:
        # Neutral - Pick higher
        final_score = max(s_long, s_short)
        final_metrics = m_long if s_long >= s_short else m_short
    
    # Protocol Status
    status = "DOGFIGHT"
    if bias == "GROUNDED" or final_score <= 40: 
        status = "GROUNDED"
        # If Grounded, ensure colors reflect danger even if components are okay
        for k in final_metrics: final_metrics[k]['color'] = 'RED' 
    elif final_score >= 75: status = "SUPERSONIC"
    elif final_score >= 50: status = "SNIPER"

    return {
        "score": final_score, "status": status, "bias": bias, "metrics": final_metrics
    }

def _generate_flight_plan(levels):
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    r30_high = float(levels.get("range30m_high", 0))
    r30_low = float(levels.get("range30m_low", 0))
    
    plans = {"LONG": {"valid": False}, "SHORT": {"valid": False}}

    if bo > 0 and r30_low > 0:
        risk = bo - r30_low
        if risk > 0:
            plans["LONG"] = {"valid": True, "entry": bo, "stop": r30_low, "targets": [bo+risk, bo+2*risk, bo+3*risk]}

    if bd > 0 and r30_high > 0:
        risk = r30_high - bd
        if risk > 0:
            plans["SHORT"] = {"valid": True, "entry": bd, "stop": r30_high, "targets": [bd-risk, bd-2*risk, bd-3*risk]}
            
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
            radar_grid.append({"symbol": sym, "score": 0, "status": "OFFLINE", "bias": "NONE", "metrics": {}})
            continue

        price = float(res.get("price", 0))
        levels = res.get("battlebox", {}).get("levels", {})
        
        analysis = _analyze_session_kinetics(levels, price)

        radar_grid.append({
            "symbol": sym, "price": price, 
            "score": analysis["score"], "status": analysis["status"], 
            "bias": analysis["bias"], "metrics": analysis["metrics"]
        })

    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    return radar_grid

async def analyze_target(symbol, session_id="us_ny_futures"):
    data = await battlebox_pipeline.get_live_battlebox(symbol=symbol, session_mode="MANUAL", manual_id=session_id)
    if data.get("status") == "ERROR": return {"ok": False}

    price = float(data.get("price", 0))
    levels = data.get("battlebox", {}).get("levels", {})
    
    analysis = _analyze_session_kinetics(levels, price)
    plans = _generate_flight_plan(levels)

    return {
        "symbol": symbol, "score": analysis["score"], "status": analysis["status"], 
        "bias": analysis["bias"], "metrics": analysis["metrics"], 
        "plans": plans, "levels": levels, "price": price
    }