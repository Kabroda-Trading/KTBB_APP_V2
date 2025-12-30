# sjan_brain.py
from __future__ import annotations
from typing import List, Dict, Any
import pandas as pd
import numpy as np

def analyze_market_structure(monthly_candles: List[Dict], weekly_candles: List[Dict]) -> Dict[str, Any]:
    """
    AUDITED LOGIC:
    1. Trends: 200 SMA (Macro River) & 21 EMA (Micro Current).
    2. Structure: Trails the 'Local High' to keep Fibs dynamic.
    3. Rotation: Detects when price reclaims momentum after a pullback.
    """
    df = pd.DataFrame(weekly_candles)
    if df.empty: return {}
    
    # --- 1. INDICATORS ---
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['sma_200'] = df['close'].rolling(window=200).mean()
    if pd.isna(df['sma_200'].iloc[-1]): 
        df['sma_200'] = df['close'].rolling(window=50).mean()

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    # --- 2. TRAILING FIBONACCI (Dynamic Ceiling) ---
    macro_low = df['low'].min()
    local_high = df['high'].tail(52).max() # Lookback for dynamic top
    
    fib_range = local_high - macro_low
    fibs = {
        "top": local_high,
        "bottom": macro_low,
        "shallow": local_high - (fib_range * 0.382), # Momentum Support
        "golden": local_high - (fib_range * 0.618),  # Deep Value
        "deep": local_high - (fib_range * 0.786)     # Exhaustion Check
    }

    # --- 3. TREND STATE ---
    macro_state = "MACRO BULL" if curr['close'] > macro_low else "MACRO FAILURE"
    micro_state = "RUN (IMPULSE)" if curr['close'] > curr['ema_21'] else "REST (PULLBACK)"
    
    # Rotation: Reclaiming 21 EMA or 200 SMA
    rotation = False
    if (prev['close'] < prev['ema_21'] and curr['close'] > curr['ema_21']) or \
       (prev['close'] < prev['sma_200'] and curr['close'] > curr['sma_200']):
        rotation = True

    # --- 4. EXPORT FOR CHARTING ---
    ema_21_line = [{"time": int(r['time']), "value": r['ema_21']} for _, r in df.iterrows() if not pd.isna(r['ema_21'])]
    sma_200_line = [{"time": int(r['time']), "value": r['sma_200']} for _, r in df.iterrows() if not pd.isna(r['sma_200'])]

    return {
        "price": curr['close'],
        "macro": macro_state,
        "micro": micro_state,
        "rotation": rotation,
        "charts": {"ema_21": ema_21_line, "sma_200": sma_200_line},
        "fibs": fibs,
        "zones": _scan_zones(df)
    }

def _scan_zones(df: pd.DataFrame) -> Dict[str, List]:
    """Grade zones based on Institutional Velocity (>3% exit move)."""
    demand = []
    window = df.tail(100).reset_index(drop=True)
    for i in range(2, len(window) - 4):
        curr = window.iloc[i]
        if curr['low'] < window.iloc[i-1]['low'] and curr['low'] < window.iloc[i+1]['low']:
            future_high = window.iloc[i+1:i+4]['high'].max()
            move_pct = (future_high - curr['high']) / curr['high']
            if move_pct > 0.03:
                demand.append({"level": curr['low'], "strength": "A" if move_pct > 0.10 else "B"})
    demand.sort(key=lambda x: x['level'], reverse=True)
    return {"demand": demand[:3]}