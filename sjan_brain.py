# sjan_brain.py
from __future__ import annotations
from typing import List, Dict, Any
import pandas as pd
import numpy as np

def analyze_market_structure(monthly_candles: List[Dict], weekly_candles: List[Dict]) -> Dict[str, Any]:
    """
    S-JAN WEALTH LOGIC v3.0:
    1. Macro Structure: Finds Cycle Low (Crypto Winter) from deep history.
    2. Trend Protocol: 200 SMA (River) vs 21 EMA (Speed).
    3. Rotation: Detects the 'Get in the Water' moment.
    """
    # --- 1. MACRO CONTEXT (The River) ---
    df_macro = pd.DataFrame(monthly_candles)
    if df_macro.empty: return {}
    
    # Absolute Cycle Low of the last ~7 years
    macro_low = df_macro['low'].min() 
    
    # --- 2. MICRO CONTEXT (The Speed) ---
    df = pd.DataFrame(weekly_candles) # Daily candles
    if df.empty: return {}
    
    # Indicators
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['sma_200'] = df['close'].rolling(window=200).mean()
    
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    # --- 3. DYNAMIC STRUCTURE (Living Pivots) ---
    # Find local high of the current run (last 6 months)
    local_high = df['high'].tail(180).max()
    
    fib_range = local_high - macro_low
    fibs = {
        "top": local_high,
        "premium_zone": local_high - (fib_range * 0.1), # Top 10% (Take Profit)
        "shallow": local_high - (fib_range * 0.382),    # Momentum Buy
        "golden": local_high - (fib_range * 0.618),     # Value Buy
        "bottom": macro_low
    }

    # --- 4. PHASE DETECTION ---
    is_macro_bull = curr['close'] > curr['sma_200']
    is_micro_bull = curr['close'] > curr['ema_21']
    
    # Rotation: Price was Resting (below 21), now Running (above 21)
    is_rotating = (prev['close'] < prev['ema_21']) and (curr['close'] > curr['ema_21'])
    
    phase = "UNKNOWN"
    action = "HOLD"
    
    if not is_macro_bull:
        phase = "MACRO BEAR / ACCUMULATION"
        action = "DCA DEEP VALUE" 
    elif is_macro_bull and is_micro_bull:
        phase = "MOMENTUM RUN (IMPULSE)"
        action = "ADD ON SHALLOW DIPS"
    elif is_macro_bull and not is_micro_bull:
        phase = "BULL MARKET PULLBACK (REST)"
        action = "BUILD POSITIONS AT FIB LEVELS"
    
    if is_rotating and is_macro_bull:
        phase = "ROTATION (ACCELERATION)"
        action = "DEPLOY HEAVY (CONFIRMED)"

    # --- 5. EXPORT ---
    charts = {
        "ema_21": [{"time": int(r['time']), "value": r['ema_21']} for _, r in df.iterrows() if not pd.isna(r['ema_21'])],
        "sma_200": [{"time": int(r['time']), "value": r['sma_200']} for _, r in df.iterrows() if not pd.isna(r['sma_200'])]
    }

    return {
        "price": curr['close'],
        "phase": phase,
        "action": action,
        "rotation": is_rotating,
        "fibs": fibs,
        "indicators": {"ema_21": curr['ema_21'], "sma_200": curr['sma_200']},
        "charts": charts,
        "zones": _scan_velocity_zones(df)
    }

def _scan_velocity_zones(df: pd.DataFrame) -> List[Dict]:
    """Finds zones where price ripped away (>3% move in 3 days)."""
    zones = []
    subset = df.tail(365).reset_index(drop=True)
    
    for i in range(5, len(subset)-5):
        row = subset.iloc[i]
        # Fractal Low pattern
        if row['low'] < subset.iloc[i-1]['low'] and row['low'] < subset.iloc[i+1]['low']:
            # Check Velocity
            future_price = subset.iloc[i+3]['close']
            move_pct = (future_price - row['high']) / row['high']
            
            if move_pct > 0.03: 
                zones.append({
                    "level": row['low'], 
                    "strength": "A" if move_pct > 0.08 else "B"
                })
    
    zones.sort(key=lambda x: x['level'], reverse=True)
    return zones[:3]