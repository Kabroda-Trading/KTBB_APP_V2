# market_radar.py
# ==============================================================================
# MARKET RADAR ENGINE (PROJECT OVERWATCH)
# ==============================================================================
# ARCHITECTURE COMPLIANCE:
# 1. BACKBONE: Calls 'battlebox_pipeline.get_live_battlebox' for the Truth.
# 2. MATH: Applies Kinetic Scoring (Level 2) on top of Corporate Levels (Level 1).
# 3. CONSISTENCY: Ensures Scanner matches Omega triggers 100%.
# ==============================================================================

import asyncio
from datetime import datetime, timezone

# CORE PIPELINE (The Source of Truth)
import battlebox_pipeline
import session_manager

# TARGET LIST
TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "TRXUSDT"]

def _score_kinetic_metrics(price, levels, context):
    """
    Applies the KINETIC MATH (Level 2) on top of the verified LEVELS (Level 1).
    Math sourced from Project Omega v16 logic.
    """
    # 1. UNPACK THE TRUTH (From Pipeline)
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    atr = float(levels.get("atr", 0))
    slope_score = float(levels.get("slope", 0))
    struct_score = float(levels.get("structure_score", 0))
    
    # Fallback for ATR if pipeline is calibrating
    if atr == 0: atr = price * 0.01

    score = 0
    metrics = {}

    # --- A. ENERGY (Range BPS) ---
    # Logic: How tight is the Daily Battlebox?
    range_size = abs(dr - ds)
    bps = (range_size / price) * 10000 if price > 0 else 500
    
    energy_val = 5
    if bps < 100: energy_val = 25   # Super Coiled
    elif bps < 200: energy_val = 15 # Standard
    
    score += energy_val
    metrics["energy"] = energy_val

    # --- B. SPACE (R-Multiple) ---
    # Logic: Room to run from Trigger to Wall
    # We check the average distance to the walls
    dist_to_dr = abs(dr - price)
    dist_to_ds = abs(ds - price)
    avg_gap = (dist_to_dr + dist_to_ds) / 2
    r_mult = avg_gap / atr if atr > 0 else 0

    space_val = 0
    if r_mult > 2.0: space_val = 25
    elif r_mult > 1.0: space_val = 15
    
    score += space_val
    metrics["space"] = space_val

    # --- C. WIND (Momentum) ---
    # Logic: Uses the 'slope' calculated by the Pipeline
    wind_val = 0
    if abs(slope_score) > 0.2: wind_val = 25
    elif abs(slope_score) > 0.1: wind_val = 15
    
    score += wind_val
    metrics["wind"] = wind_val

    # --- D. HULL (Structure) ---
    # Logic: Uses the 'structure_score' from the Pipeline
    hull_val = 25 if struct_score > 0.5 else 10
    score += hull_val
    metrics["hull"] = hull_val

    # STATUS
    status = "DOGFIGHT"
    if score >= 75: status = "SUPERSONIC"
    elif score >= 50: status = "SNIPER"
    elif score <= 40: status = "GROUNDED"

    return score, status, metrics

async def scan_sector(session_id="us_ny_futures"):
    """
    Main Grid Scanner.
    Loops through targets and asks the PIPELINE for the Battlebox.
    """
    # 1. VALIDATE SESSION
    session_config = next((s for s in session_manager.SESSION_CONFIGS if s["id"] == session_id), None)
    if not session_config: session_id = "us_ny_futures"

    print(f">>> [RADAR] Asking Pipeline for Sector Status ({session_id})")
    radar_grid = []

    # 2. ASK THE BACKBONE (Loop)
    for sym in TARGETS:
        try:
            # CALL THE PIPELINE (The Moment of Truth)
            # This returns the official levels (BO, BD, DR, DS) established by the engine.
            data = await battlebox_pipeline.get_live_battlebox(
                symbol=sym,
                session_mode="MANUAL",
                manual_id=session_id
            )

            if data.get("status") == "ERROR":
                radar_grid.append({"symbol": sym, "score": 0, "status": "OFFLINE", "metrics": {}})
                continue

            # EXTRACT DATA
            price = float(data.get("price", 0))
            box = data.get("battlebox", {})
            levels = box.get("levels", {})
            context = box.get("context", {})

            # RUN KINETIC MATH (The Scanner's Job)
            # We only do the scoring; we trust the levels provided.
            score, status, metrics = _score_kinetic_metrics(price, levels, context)

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

    # Sort
    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    return radar_grid

# --- SINGLE TARGET VIEW ---
async def analyze_target(symbol, session_id="us_ny_futures"):
    """
    Detailed view for Lock Target.
    Again, asks the PIPELINE for the truth.
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
    context = box.get("context", {})

    score, status, metrics = _score_kinetic_metrics(price, levels, context)

    return {
        "symbol": symbol,
        "score": score,
        "status": status,
        "metrics": metrics,
        "levels": {
            "breakout": levels.get("breakout_trigger", 0),
            "breakdown": levels.get("breakdown_trigger", 0),
            "resistance": levels.get("daily_resistance", 0),
            "support": levels.get("daily_support", 0)
        }
    }