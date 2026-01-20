# market_radar.py
# ==============================================================================
# MARKET RADAR ENGINE (PROJECT OVERWATCH)
# ==============================================================================
# ARCHITECTURE COMPLIANCE:
# 1. DATA: Sourced exclusively from 'battlebox_pipeline' (The Truth).
# 2. MATH: Kinetic Scoring (Level 2) applied to Official Levels.
# 3. PLANNING: Trade Plans calculated using Omega logic (30m Stops).
# ==============================================================================

import asyncio
from datetime import datetime, timezone

# CORE PIPELINE (The Source of Truth)
import battlebox_pipeline
import session_manager

# TARGET LIST
TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "TRXUSDT"]

def _score_kinetic_metrics(price, levels):
    """
    Applies the KINETIC MATH (Level 2) on top of the verified LEVELS (Level 1).
    """
    # 1. UNPACK THE TRUTH (From Pipeline)
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    atr = float(levels.get("atr", 0))
    slope_score = float(levels.get("slope", 0))
    struct_score = float(levels.get("structure_score", 0))
    
    if atr == 0 and price > 0: atr = price * 0.01

    score = 0
    metrics = {}

    # A. ENERGY (Range BPS)
    range_size = abs(dr - ds)
    bps = (range_size / price) * 10000 if price > 0 else 500
    
    energy_val = 5
    if bps < 100: energy_val = 25   
    elif bps < 200: energy_val = 15 
    
    score += energy_val
    metrics["energy"] = energy_val

    # B. SPACE (R-Multiple)
    dist_to_dr = abs(dr - price)
    dist_to_ds = abs(ds - price)
    avg_gap = (dist_to_dr + dist_to_ds) / 2
    r_mult = avg_gap / atr if atr > 0 else 0

    space_val = 0
    if r_mult > 2.0: space_val = 25
    elif r_mult > 1.0: space_val = 15
    
    score += space_val
    metrics["space"] = space_val

    # C. WIND (Momentum)
    wind_val = 0
    if abs(slope_score) > 0.2: wind_val = 25
    elif abs(slope_score) > 0.1: wind_val = 15
    
    score += wind_val
    metrics["wind"] = wind_val

    # D. HULL (Structure)
    hull_val = 25 if struct_score > 0.5 else 10
    score += hull_val
    metrics["hull"] = hull_val

    # STATUS
    status = "DOGFIGHT"
    if score >= 75: status = "SUPERSONIC"
    elif score >= 50: status = "SNIPER"
    elif score <= 40: status = "GROUNDED"

    return score, status, metrics

def _generate_flight_plan(levels):
    """
    Generates the Omega-Style Trade Plan.
    STOP LOGIC: Uses the 30m High/Low from the Pipeline.
    TARGET LOGIC: 1R, 2R, 4R.
    """
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    r30_high = float(levels.get("range30m_high", 0))
    r30_low = float(levels.get("range30m_low", 0))
    
    plans = {
        "LONG": {"valid": False}, 
        "SHORT": {"valid": False}
    }

    # LONG PLAN
    if bo > 0 and r30_low > 0:
        risk = bo - r30_low
        if risk > 0:
            plans["LONG"] = {
                "valid": True,
                "entry": bo,
                "stop": r30_low,
                "risk": risk,
                "t1": bo + risk,
                "t2": bo + (risk * 2),
                "t3": bo + (risk * 4)
            }

    # SHORT PLAN
    if bd > 0 and r30_high > 0:
        risk = r30_high - bd
        if risk > 0:
            plans["SHORT"] = {
                "valid": True,
                "entry": bd,
                "stop": r30_high,
                "risk": risk,
                "t1": bd - risk,
                "t2": bd - (risk * 2),
                "t3": bd - (risk * 4)
            }
            
    return plans

async def scan_sector(session_id="us_ny_futures"):
    """
    Main Grid Scanner.
    """
    session_config = next((s for s in session_manager.SESSION_CONFIGS if s["id"] == session_id), None)
    if not session_config: session_id = "us_ny_futures"

    print(f">>> [RADAR] Asking Pipeline for Sector Status ({session_id})")
    radar_grid = []

    for sym in TARGETS:
        try:
            # CALL THE PIPELINE (The Moment of Truth)
            data = await battlebox_pipeline.get_live_battlebox(
                symbol=sym,
                session_mode="MANUAL",
                manual_id=session_id
            )

            if data.get("status") == "ERROR":
                radar_grid.append({"symbol": sym, "score": 0, "status": "OFFLINE", "metrics": {}})
                continue

            price = float(data.get("price", 0))
            box = data.get("battlebox", {})
            levels = box.get("levels", {})

            # KINETIC MATH
            score, status, metrics = _score_kinetic_metrics(price, levels)

            radar_grid.append({
                "symbol": sym,
                "score": score,
                "status": status,
                "price": price,
                "metrics": metrics
            })

        except Exception as e:
            print(f"[RADAR] Pipeline Error on {sym}: {e}")
            radar_grid.append({"symbol": sym, "score": 0, "status": "ERROR", "metrics": {}})

    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    return radar_grid

# --- SINGLE TARGET VIEW ---
async def analyze_target(symbol, session_id="us_ny_futures"):
    """
    Detailed view for Lock Target.
    Returns: Score, Metrics, Levels, AND Trade Plan.
    """
    data = await battlebox_pipeline.get_live_battlebox(
        symbol=symbol,
        session_mode="MANUAL",
        manual_id=session_id
    )

    if data.get("status") == "ERROR":
        return {"ok": False, "msg": "Target Offline"}

    price = float(data.get("price", 0))
    box = data.get("battlebox", {})
    levels = box.get("levels", {})

    # 1. Score
    score, status, metrics = _score_kinetic_metrics(price, levels)
    
    # 2. Plan (The Flight Path)
    plans = _generate_flight_plan(levels)

    return {
        "symbol": symbol,
        "score": score,
        "status": status,
        "metrics": metrics,
        "plans": plans,
        "levels": {
            "breakout": levels.get("breakout_trigger", 0),
            "breakdown": levels.get("breakdown_trigger", 0),
            "resistance": levels.get("daily_resistance", 0),
            "support": levels.get("daily_support", 0)
        }
    }