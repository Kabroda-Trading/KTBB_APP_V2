# market_radar.py
# ==============================================================================
# MARKET RADAR ENGINE (MULTI-TARGET KINETIC SCANNER)
# ==============================================================================
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# PIPELINE CONNECTION
import battlebox_pipeline 

TARGETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "TRXUSDT"]

def _calculate_metrics(candles_5m):
    """
    Step 2 Kinetic Math: Calculates Energy, Space, Wind, Hull scores.
    Returns: Score (0-100), Status, and Component Details.
    """
    if not candles_5m or len(candles_5m) < 50:
        return 0, "NO_DATA", {}

    df = pd.DataFrame(candles_5m)
    # Ensure numerics
    for col in ['close', 'high', 'low']:
        df[col] = df[col].astype(float)

    # 1. ENERGY (Bollinger Band Width)
    df['ma'] = df['close'].rolling(20).mean()
    df['std'] = df['close'].rolling(20).std()
    
    # Get last valid row
    row = df.iloc[-1]
    
    # Energy Score (Lower width is better)
    bb_w = (4 * row['std']) / row['close']
    energy_val = max(0, min(25, 25 - (bb_w * 1000))) # Scaling

    # 2. SPACE (ATR)
    df['tr'] = np.maximum(df['high'] - df['low'], abs(df['high'] - df['close'].shift(1)))
    df['atr'] = df['tr'].rolling(14).mean()
    row = df.iloc[-1] # Refresh row
    
    atr_pct = row['atr'] / row['close']
    space_val = max(0, min(25, atr_pct * 5000)) # Scaling

    # 3. WIND (Momentum)
    df['slope'] = df['close'].diff(5).abs()
    row = df.iloc[-1]
    
    slope_pct = row['slope'] / row['close']
    wind_val = max(0, min(25, slope_pct * 5000)) # Scaling

    # 4. HULL (Structure / Z-Score)
    z = abs((row['close'] - row['ma']) / row['std']) if row['std'] else 0
    hull_val = 25 if z < 1.5 else max(0, 25 - ((z-1.5)*25))

    # Total
    total = int(energy_val + space_val + wind_val + hull_val)
    
    # Status
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
    # 48h lookback for sufficient indicator data
    end_ts = int(datetime.now(timezone.utc).timestamp())
    start_ts = end_ts - (48 * 3600)
    
    radar_grid = []

    for sym in TARGETS:
        try:
            # 1. Fetch Data (Level 1)
            raw = await battlebox_pipeline.fetch_historical_pagination(
                symbol=sym, start_ts=start_ts, end_ts=end_ts
            )
            
            # 2. Run Math (Level 2)
            score, status, metrics = _calculate_metrics(raw)
            
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

    # Sort by Score (Highest Priority First)
    radar_grid.sort(key=lambda x: x['score'], reverse=True)
    
    return radar_grid