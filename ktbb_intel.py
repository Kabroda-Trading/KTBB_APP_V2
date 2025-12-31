# ktbb_intel.py
from typing import List, Dict
import pandas as pd

def find_supply_demand_shelves(candles: List[Dict], pivot_len: int = 3) -> Dict[str, List[float]]:
    """
    Finds structural shelves using Pivot High/Low logic.
    Matches Pine Script: ta.pivothigh(high, 3, 3)
    """
    df = pd.DataFrame(candles)
    if df.empty: return {"supply": [], "demand": []}

    window = pivot_len * 2 + 1
    # Rolling Min/Max for Pivot Detection
    df['is_pivot_high'] = df['high'] == df['high'].rolling(window=window, center=True).max()
    df['is_pivot_low'] = df['low'] == df['low'].rolling(window=window, center=True).min()

    supply_shelves = []
    demand_shelves = []

    # Scan history for pivots
    for i in range(pivot_len, len(df) - pivot_len):
        if df.iloc[i]['is_pivot_high']:
            supply_shelves.append(float(df.iloc[i]['high']))
        if df.iloc[i]['is_pivot_low']:
            demand_shelves.append(float(df.iloc[i]['low']))

    # Return only the most relevant recent levels (last 5)
    return {
        "supply": sorted(supply_shelves)[-5:],
        "demand": sorted(demand_shelves)[:5]
    }