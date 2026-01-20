# market_radar.py
# ==============================================================================
# MARKET RADAR ENGINE (PROJECT OVERWATCH)
# ==============================================================================
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# 1. CORE PIPELINE (Single Source of Truth)
import battlebox_pipeline 
import session_manager
import sse_engine # Needed for detailed levels in Single View

# TARGET LIST
TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "TRXUSDT"]

def _calculate_kinetic_math(candles_5m):
    """
    Performs Step 2 Math: Energy, Space, Wind, Hull.
    """
    if not candles_5m or len(candles_5m) < 50:
        return 0, "NO_DATA", {}

    df = pd.DataFrame(candles_5m)
    for col in ['close', 'high', 'low']:
        df[col] = df[col].astype(float)

    # 1. ENERGY (Bollinger Band Width)
    df['ma'] = df['close'].rolling(20).mean()
    df['std'] = df['close'].rolling(20).std()
    row = df.iloc[-1]
    
    bb_w = (4 * row['std']) / row['close']
    energy_val = max(0, min(25, 25 - (bb_w * 1000))) 

    # 2. SPACE (ATR)
    df['tr'] = np.maximum(df['high'] - df['low'], abs(df['high'] - df['close'].shift(1)))
    df['atr'] = df['tr'].rolling(14).mean()
    row = df.iloc[-1] 
    
    atr_pct = row['atr'] / row['close']
    space_val = max(0, min(25, atr_pct * 5000))

    # 3. WIND (Momentum)
    df['slope'] = df['close'].diff(5).abs()
    row = df.iloc[-1]
    
    slope_pct = row['slope'] / row['close']
    wind_val = max(0, min(25, slope_pct * 5000))

    # 4. HULL (Structure / Z-Score)
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
    Main entry point for the GRID view.
    """
    session_config = next((s for s in session_manager.SESSION_CONFIGS if s["id"] == session_id), None)
    if not session_config: session_id = "us_ny_futures"

    end_ts = int(datetime.now(timezone.utc).timestamp())
    start_ts = end_ts - (48 * 3600)
    
    radar_grid = []

    for sym in TARGETS:
        try:
            raw = await battlebox_pipeline.fetch_historical_pagination(symbol=sym, start_ts=start_ts, end_ts=end_ts)
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

    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    return radar_grid

# --- NEW: SINGLE TARGET ANALYZER ---
async def analyze_target(symbol, session_id="us_ny_futures"):
    """
    Detailed analysis for the 'Lock Target' page.
    Combines Kinetic Math with Structure Levels (Flight Path).
    """
    # 1. Fetch Data
    end_ts = int(datetime.now(timezone.utc).timestamp())
    start_ts = end_ts - (48 * 3600)
    
    raw = await battlebox_pipeline.fetch_historical_pagination(symbol=symbol, start_ts=start_ts, end_ts=end_ts)
    
    # 2. Run Kinetic Math
    score, status, metrics = _calculate_kinetic_math(raw)
    
    # 3. Run Structure Math (Get the Flight Path Levels)
    # We use a simplified SSE call just to get the triggers
    # This requires us to fake a session context briefly just to extract levels
    session_config = next((s for s in session_manager.SESSION_CONFIGS if s["id"] == session_id), None)
    if not session_config: session_config = session_manager.SESSION_CONFIGS[0]

    # Calculate levels using the last 24h of data
    sse_input = {
        "locked_history_5m": raw[-288:], # Approx 24h
        "slice_24h_5m": raw[-288:],
        "session_open_price": raw[-1]["open"], # Live proxy
        "r30_high": max(c["high"] for c in raw[-6:]), # Last 30m proxy
        "r30_low": min(c["low"] for c in raw[-6:]),
        "last_price": raw[-1]["close"],
        "tuning": {}
    }
    
    levels = {}
    try:
        computed = sse_engine.compute_sse_levels(sse_input)
        if "levels" in computed:
            levels = computed["levels"]
    except:
        pass

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