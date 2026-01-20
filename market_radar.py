# market_radar.py
# ==============================================================================
# MARKET RADAR ENGINE (PROJECT OVERWATCH)
# ==============================================================================
# CALIBRATION: STRICT OMEGA v16 WEIGHTS
# - Energy: 30pts (Tight Ranges Only)
# - Space: 30pts (Must be > 1.0R)
# - Momentum: 20pts (Slope + Alignment)
# - Hull: 10pts (Structure)
# - Location: 10pts (Near Open)
# ==============================================================================

import asyncio
from datetime import datetime, timezone
import battlebox_pipeline
import session_manager

TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "TRXUSDT"]

def _score_kinetic_metrics(price, levels):
    """
    Applies EXACT Project Omega v16 Scoring Weights.
    Returns Score, Status, and Rich Metrics (Value + Label + Color).
    """
    # 1. UNPACK TRUTH
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    atr = float(levels.get("atr", 0))
    slope_score = float(levels.get("slope", 0))
    struct_score = float(levels.get("structure_score", 0))
    session_open = float(levels.get("session_open_price", 0))
    
    if atr == 0 and price > 0: atr = price * 0.01
    if session_open == 0: session_open = price

    score = 0
    metrics = {}

    # --- A. ENERGY (Max 30pts) ---
    range_size = abs(dr - ds)
    bps = (range_size / price) * 10000 if price > 0 else 500
    
    e_val = 0
    e_label = f"{int(bps)} BPS"
    e_color = "RED" # Default Bad

    if bps < 100: 
        e_val = 30
        e_label = f"SUPER COILED ({int(bps)})"
        e_color = "CYAN"
    elif bps < 200: 
        e_val = 15
        e_label = f"STANDARD ({int(bps)})"
        e_color = "GREEN"
    else:
        e_val = 0
        e_label = f"LOOSE ({int(bps)})"
        e_color = "RED"
    
    score += e_val
    metrics["energy"] = {"val": e_val, "text": e_label, "color": e_color, "pct": min(100, (e_val/30)*100)}

    # --- B. SPACE (Max 30pts) ---
    dist_to_dr = abs(dr - price)
    dist_to_ds = abs(ds - price)
    avg_gap = (dist_to_dr + dist_to_ds) / 2
    r_mult = avg_gap / atr if atr > 0 else 0

    s_val = 0
    s_label = f"{r_mult:.1f}R"
    s_color = "RED"

    if r_mult > 2.0: 
        s_val = 30
        s_label = f"SUPER SONIC ({r_mult:.1f}R)"
        s_color = "CYAN"
    elif r_mult > 1.0: 
        s_val = 15
        s_label = f"Standard ({r_mult:.1f}R)"
        s_color = "GREEN"
    else:
        s_val = 0
        s_label = f"BLOCKED (<1.0R)"
        s_color = "RED"

    score += s_val
    metrics["space"] = {"val": s_val, "text": s_label, "color": s_color, "pct": min(100, (s_val/30)*100)}

    # --- C. WIND (Max 20pts) ---
    w_val = 0
    w_label = "NEUTRAL"
    w_color = "GRAY"

    # Simple Slope Check (Omega has complex alignment, we use Slope proxy)
    if abs(slope_score) > 0.25:
        w_val = 20
        w_label = "STRONG"
        w_color = "CYAN"
    elif abs(slope_score) > 0.1:
        w_val = 10
        w_label = "MILD"
        w_color = "GREEN"
    
    score += w_val
    metrics["wind"] = {"val": w_val, "text": w_label, "color": w_color, "pct": min(100, (w_val/20)*100)}

    # --- D. HULL (Max 10pts) ---
    h_val = 0
    h_label = f"WEAK ({struct_score:.1f})"
    h_color = "RED"

    if struct_score > 0.5:
        h_val = 10
        h_label = f"SOLID ({struct_score:.1f})"
        h_color = "CYAN"
    
    score += h_val
    metrics["hull"] = {"val": h_val, "text": h_label, "color": h_color, "pct": min(100, (h_val/10)*100)}

    # --- E. LOCATION (Max 10pts) ---
    # Omega Bonus: Are we near the Open?
    dist_open = abs(price - session_open)
    l_val = 0
    if dist_open < (atr * 0.5):
        l_val = 10
    score += l_val
    # We don't display Location bar, just add to total score to match Omega

    # STATUS
    status = "DOGFIGHT"
    if score >= 75: status = "SUPERSONIC"
    elif score >= 50: status = "SNIPER"
    elif score <= 40: status = "GROUNDED"

    return score, status, metrics

def _generate_flight_plan(levels):
    """
    Standard Omega Flight Path.
    """
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    r30_high = float(levels.get("range30m_high", 0))
    r30_low = float(levels.get("range30m_low", 0))
    
    plans = {"LONG": {"valid": False}, "SHORT": {"valid": False}}

    if bo > 0 and r30_low > 0:
        risk = bo - r30_low
        if risk > 0:
            plans["LONG"] = {
                "valid": True, "entry": bo, "stop": r30_low,
                "t1": bo + risk, "t2": bo + (risk * 2), "t3": bo + (risk * 4)
            }

    if bd > 0 and r30_high > 0:
        risk = r30_high - bd
        if risk > 0:
            plans["SHORT"] = {
                "valid": True, "entry": bd, "stop": r30_high,
                "t1": bd - risk, "t2": bd - (risk * 2), "t3": bd - (risk * 4)
            }
            
    return plans

async def scan_sector(session_id="us_ny_futures"):
    session_config = next((s for s in session_manager.SESSION_CONFIGS if s["id"] == session_id), None)
    if not session_config: session_id = "us_ny_futures"

    radar_grid = []
    for sym in TARGETS:
        try:
            data = await battlebox_pipeline.get_live_battlebox(symbol=sym, session_mode="MANUAL", manual_id=session_id)
            if data.get("status") == "ERROR":
                radar_grid.append({"symbol": sym, "score": 0, "status": "OFFLINE", "metrics": {}})
                continue

            price = float(data.get("price", 0))
            levels = data.get("battlebox", {}).get("levels", {})
            score, status, metrics = _score_kinetic_metrics(price, levels)

            radar_grid.append({
                "symbol": sym, "score": score, "status": status, "price": price, "metrics": metrics
            })
        except:
            radar_grid.append({"symbol": sym, "score": 0, "status": "ERROR", "metrics": {}})

    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    return radar_grid

async def analyze_target(symbol, session_id="us_ny_futures"):
    data = await battlebox_pipeline.get_live_battlebox(symbol=symbol, session_mode="MANUAL", manual_id=session_id)
    if data.get("status") == "ERROR": return {"ok": False}

    price = float(data.get("price", 0))
    levels = data.get("battlebox", {}).get("levels", {})
    score, status, metrics = _score_kinetic_metrics(price, levels)
    plans = _generate_flight_plan(levels)

    return {
        "symbol": symbol, "score": score, "status": status, "metrics": metrics, "plans": plans,
        "levels": levels
    }