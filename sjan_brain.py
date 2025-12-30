# sjan_brain.py
from __future__ import annotations
from typing import List, Dict, Any
import pandas as pd
import numpy as np

def analyze_market_structure(monthly_candles: List[Dict], weekly_candles: List[Dict]) -> Dict[str, Any]:
    """
    AUDITED LOGIC:
    1. Trends: 200 SMA (Macro) & 21 EMA (Micro).
    2. Structure: Trails the 'Local High' to keep Fibs dynamic.
    3. Triggers: Detects ROTATION (Price reclaiming momentum).
    """
    df = pd.DataFrame(weekly_candles)
    if df.empty: return {}
    
    # --- 1. CORE INDICATORS (The Math) ---
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['sma_200'] = df['close'].rolling(window=200).mean()
    
    # Fallback for young assets
    if pd.isna(df['sma_200'].iloc[-1]): 
        df['sma_200'] = df['close'].rolling(window=50).mean()

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    # --- 2. TRAILING STRUCTURE (The Context) ---
    # Anchor: Macro Low (Cycle Bottom)
    macro_low = df['low'].min()
    
    # Ceiling: Highest High of the last 52 weeks (Trailing Top)
    # This ensures we aren't using a stale high from 2 years ago if we just broke out.
    local_high = df['high'].tail(52).max()
    
    fib_range = local_high - macro_low
    fibs = {
        "top": local_high,
        "bottom": macro_low,
        "shallow": local_high - (fib_range * 0.382), # Strong Trend Support
        "golden": local_high - (fib_range * 0.618),  # Standard Pullback
        "deep": local_high - (fib_range * 0.786)     # Macro Risk
    }

    # --- 3. TREND STATE (The Decision) ---
    macro_state = "MACRO BULL" if curr['close'] > macro_low else "MACRO FAILURE"
    
    # Micro Trend: Are we above the Speed Line (21 EMA)?
    micro_state = "RUN (IMPULSE)" if curr['close'] > curr['ema_21'] else "REST (PULLBACK)"
    
    # --- 4. ROTATION DETECTOR (The Trigger) ---
    # Rotation = Price was weak, but just reclaimed Strength.
    rotation = False
    
    # A: Reclaimed 21 EMA
    if prev['close'] < prev['ema_21'] and curr['close'] > curr['ema_21']:
        rotation = True
        
    # B: Reclaimed 200 SMA (Deep Value Rotation)
    if prev['close'] < prev['sma_200'] and curr['close'] > curr['sma_200']:
        rotation = True

    # --- 5. ZONE SCANNING (The Map) ---
    zones = _scan_zones(df)

    return {
        "price": curr['close'],
        "macro": macro_state,
        "micro": micro_state,
        "rotation": rotation,
        "indicators": {
            "ema_21": curr['ema_21'],
            "sma_200": curr['sma_200']
        },
        "fibs": fibs,
        "zones": zones 
    }

def _scan_zones(df: pd.DataFrame) -> Dict[str, List]:
    """
    AUDIT CHECK: Velocity Logic.
    Only returns zones where price left Aggressively (>3% move).
    """
    supply = []
    demand = []
    
    # Look at recent history (100 weeks)
    window = df.tail(100).reset_index(drop=True)
    
    for i in range(2, len(window) - 4):
        curr = window.iloc[i]
        prev = window.iloc[i-1]
        next_c = window.iloc[i+1]
        
        # DEMAND: V-Shape Low
        if curr['low'] < prev['low'] and curr['low'] < next_c['low']:
            # VELOCITY CHECK: Did we rip up after?
            future_high = window.iloc[i+1:i+4]['high'].max()
            move_pct = (future_high - curr['high']) / curr['high']
            
            if move_pct > 0.03: # >3% move required to be "Grade A"
                demand.append({
                    "level": curr['low'],
                    "strength": "A" if move_pct > 0.10 else "B"
                })

        # SUPPLY: A-Shape High
        if curr['high'] > prev['high'] and curr['high'] > next_c['high']:
            future_low = window.iloc[i+1:i+4]['low'].min()
            move_pct = (curr['low'] - future_low) / curr['low']
            
            if move_pct > 0.03:
                supply.append({
                    "level": curr['high'],
                    "strength": "A" if move_pct > 0.10 else "B"
                })
                
    # Filter: Only keep zones relevant to current price (+/- 20%)
    last_price = window.iloc[-1]['close']
    demand = [z for z in demand if z['level'] < last_price * 1.1]
    supply = [z for z in supply if z['level'] > last_price * 0.9]
    
    # Sort: Closest first
    demand.sort(key=lambda x: x['level'], reverse=True)
    supply.sort(key=lambda x: x['level'])

    return {"supply": supply[:3], "demand": demand[:3]} # Return top 3