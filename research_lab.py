import pandas as pd
import numpy as np
from datetime import datetime, timezone

async def run_kinetic_analysis(symbol, raw_5m, start_date, end_date, session_ids, sensors, min_score):
    """
    Step 2 Kinetic Engine: Calculates Energy, Space, Wind, Hull scores.
    """
    if not raw_5m or len(raw_5m) < 100:
        return {"ok": False, "error": "Insufficient Data"}

    # 1. Prepare Dataframe
    df = pd.DataFrame(raw_5m)
    df['time'] = pd.to_datetime(df['time'], unit='ms', utc=True)
    df.set_index('time', inplace=True)
    df.sort_index(inplace=True)

    # 2. Calculate Step 2 Metrics (The Math)
    # Energy: Bollinger Band Width (Lower is better = Coiled)
    df['ma'] = df['close'].rolling(20).mean()
    df['std'] = df['close'].rolling(20).std()
    df['bb_w'] = (4 * df['std']) / df['close']
    
    # Space: ATR (Higher is better = Room to run)
    df['tr'] = np.maximum(df['high'] - df['low'], abs(df['high'] - df['close'].shift(1)))
    df['atr'] = df['tr'].rolling(14).mean()
    
    # Wind: Momentum Slope (Absolute strength)
    df['slope'] = df['close'].diff(5).abs()
    
    # Hull: Structure location (Z-Score proxy)
    df['z'] = (df['close'] - df['ma']) / df['std']

    results = []
    
    # 3. Session Times Map
    SESSION_MAP = {
        "us_ny_futures": "13:30", # 08:30 ET
        "us_ny_equity": "14:30",  # 09:30 ET
        "eu_london": "08:00",     # 03:00 ET
    }

    # 4. Iterate Days
    unique_days = pd.to_datetime(df.index.date).unique()
    
    for day in unique_days:
        day_str = day.strftime("%Y-%m-%d")
        if day_str < start_date or day_str > end_date: continue

        for sess_id in session_ids:
            if sess_id not in SESSION_MAP: continue
            
            # Target Time
            target = f"{day_str} {SESSION_MAP[sess_id]}"
            try:
                row = df.loc[target]
            except KeyError:
                try: row = df.asof(pd.to_datetime(target).replace(tzinfo=timezone.utc))
                except: continue

            if row is None or pd.isna(row['ma']): continue

            # 5. The Scoring Logic
            # Each component is worth 25 points. Total 100.
            score = 0
            comps = {"energy":0, "space":0, "wind":0, "hull":0}

            # Energy (Inverse: Low volatility is good)
            if sensors.get("energy"):
                # Scale: 0.002 width = 25pts, 0.02 width = 0pts
                val = max(0, min(25, 25 - (row['bb_w'] * 1000)))
                score += val
                comps['energy'] = int(val)

            # Space (Direct: High ATR is good)
            if sensors.get("space"):
                # Scale: 0.5% ATR = 25pts
                atr_pct = row['atr'] / row['close']
                val = max(0, min(25, atr_pct * 5000))
                score += val
                comps['space'] = int(val)

            # Wind (Direct: Momentum)
            if sensors.get("wind"):
                val = max(0, min(25, (row['slope'] / row['close']) * 5000))
                score += val
                comps['wind'] = int(val)

            # Hull (Inverse: Extreme Z-score is bad/extended)
            if sensors.get("hull"):
                z = abs(row['z'])
                # < 1.5 sigma = 25pts, > 2.5 sigma = 0pts
                val = 25 if z < 1.5 else max(0, 25 - ((z-1.5)*25))
                score += val
                comps['hull'] = int(val)

            # 6. Normalize Score
            # If user only checks 2 boxes, max raw score is 50.
            # We scale that back to 0-100.
            active_count = sum(1 for v in sensors.values() if v)
            final_score = 0
            if active_count > 0:
                final_score = int((score / (active_count * 25)) * 100)

            results.append({
                "date": f"{day_str} [{sess_id}]",
                "score": final_score,
                "comps": comps
            })

    # Summary
    valid = [r for r in results if r['score'] >= min_score]
    
    return {
        "ok": True,
        "total_sessions": len(results),
        "valid_signals": len(valid),
        "avg_score": int(np.mean([r['score'] for r in results])) if results else 0,
        "fire_rate": int((len(valid)/len(results))*100) if results else 0,
        "results": results
    }