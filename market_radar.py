# market_radar.py
# ==============================================================================
# MARKET RADAR ENGINE (PROJECT OVERWATCH)
# ==============================================================================
# Protocol: Defaults to "us_ny_futures" (08:30 ET) session logic.
# Function: Scans Level 1 (Structure) and Level 2 (Kinetics) for multiple assets.
# ==============================================================================

import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# 1. CORE PIPELINE (Single Source of Truth)
import battlebox_pipeline 
import session_manager

# TARGET LIST
TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "TRXUSDT"]

def _calculate_kinetic_math(candles_5m):
    """
    Performs Step 2 Math: Energy, Space, Wind, Hull.
    Returns: Score (0-100), Status, and Component Details.
    """
    if not candles_5m or len(candles_5m) < 50:
        return 0, "NO_DATA", {}

    df = pd.DataFrame(candles_5m)
    for col in ['close', 'high', 'low']:
        df[col] = df[col].astype(float)

    # 1. ENERGY (Bollinger Band Width)
    # Lower width = Coiled = Higher Score
    df['ma'] = df['close'].rolling(20).mean()
    df['std'] = df['close'].rolling(20).std()
    row = df.iloc[-1]
    
    bb_w = (4 * row['std']) / row['close']
    energy_val = max(0, min(25, 25 - (bb_w * 1000))) 

    # 2. SPACE (ATR)
    # Higher ATR = Room to Run = Higher Score
    df['tr'] = np.maximum(df['high'] - df['low'], abs(df['high'] - df['close'].shift(1)))
    df['atr'] = df['tr'].rolling(14).mean()
    row = df.iloc[-1] 
    
    atr_pct = row['atr'] / row['close']
    space_val = max(0, min(25, atr_pct * 5000))

    # 3. WIND (Momentum)
    # Higher Slope = Velocity = Higher Score
    df['slope'] = df['close'].diff(5).abs()
    row = df.iloc[-1]
    
    slope_pct = row['slope'] / row['close']
    wind_val = max(0, min(25, slope_pct * 5000))

    # 4. HULL (Structure / Z-Score)
    # Mid-range (0-1.5) is best. Extremes (>3.0) are bad/extended.
    z = abs((row['close'] - row['ma']) / row['std']) if row['std'] else 0
    hull_val = 25 if z < 1.5 else max(0, 25 - ((z-1.5)*25))

    # TOTAL & STATUS
    total = int(energy_val + space_val + wind_val + hull_val)
    
    status = "DOGFIGHT"
    if total >= 85: status = "SUPERSONIC"
    elif total >= 70: status = "SNIPER"
    elif total <= 40: status = "GROUNDED"

    return total, status, {
        "energy": int(energy_val),
        "space": int(space_val),
        "wind": int(wind_val),
        "hull": int(hull_val),
        "price": row['close'],
        "atr": row['atr']
    }

async def scan_sector(session_id="us_ny_futures"):
    """
    Main entry point. Fetches data for all TARGETS and returns a tactical grid.
    """
    # VALIDATE SESSION
    session_config = next((s for s in session_manager.SESSION_CONFIGS if s["id"] == session_id), None)
    if not session_config:
        print(f"[RADAR] Warning: Invalid Session '{session_id}'. Defaulting to NY Futures.")
        session_id = "us_ny_futures"

    print(f">>> [RADAR] Scanning Sector. Protocol: {session_id}")
    
    # 48h lookback ensures indicators are fully primed
    end_ts = int(datetime.now(timezone.utc).timestamp())
    start_ts = end_ts - (48 * 3600)
    
    radar_grid = []

    for sym in TARGETS:
        try:
            # A. PIPELINE FETCH (Level 1 Data)
            raw = await battlebox_pipeline.fetch_historical_pagination(
                symbol=sym, start_ts=start_ts, end_ts=end_ts
            )
            
            # B. KINETIC MATH (Level 2 Analysis)
            score, status, metrics = _calculate_kinetic_math(raw)
            
            radar_grid.append({
                "symbol": sym,
                "score": score,
                "status": status,
                "price": metrics.get("price", 0),
                "metrics": metrics
            })
            
        except Exception as e:
            print(f"[RADAR] Failed on {sym}: {e}")
            radar_grid.append({"symbol": sym, "score": 0, "status": "ERROR", "metrics": {}})

    # Sort by Score (Tactical Priority)
    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    
    return radar_grid