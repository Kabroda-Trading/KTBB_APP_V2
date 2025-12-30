# sjan_brain.py
# ---------------------------------------------------------
# S-JAN INTELLIGENCE: MACRO BULL / MICRO ROTATION ENGINE
# ---------------------------------------------------------
from __future__ import annotations
from typing import List, Dict, Any
import pandas as pd
import numpy as np

def analyze_market_structure(monthly_candles: List[Dict], weekly_candles: List[Dict]) -> Dict[str, Any]:
    """
    Philosophy:
    1. We are in a MACRO BULL unless absolute structure fails.
    2. We identify MICRO RUNS vs MICRO RESTS.
    3. We detect ROTATION (The "Get In" signal).
    """
    # 1. PREPARE DATA
    df = pd.DataFrame(weekly_candles)
    if df.empty: return {}
    
    # --- CORE INDICATORS ---
    # 21 EMA (The Speed / Micro Trend)
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    # 200 SMA (The Baseline / God Line)
    df['sma_200'] = df['close'].rolling(window=200).mean()
    
    # Fallback for young assets (ETH/SOL might not have 200 weeks)
    if pd.isna(df['sma_200'].iloc[-1]): 
        df['sma_200'] = df['close'].rolling(window=50).mean()

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    # --- 2. TRAILING STRUCTURE (The "Breathing") ---
    # Find the Macro Bottom (Absolute Low of the dataset)
    macro_low = df['low'].min()
    
    # Find the "Local High" (Trailing Top)
    # Logic: Look at last 52 weeks. The highest point is our current Fib Anchor.
    # As price breaks up, this moves up.
    local_high = df['high'].tail(52).max()
    
    # Calculate Fibonacci Retracements for this Leg
    fib_range = local_high - macro_low
    fib_0382 = local_high - (fib_range * 0.382) # Shallow (Strong Momentum)
    fib_0618 = local_high - (fib_range * 0.618) # Golden Pocket (Standard Accumulation)
    fib_0786 = local_high - (fib_range * 0.786) # Deep (Macro Shift Risk)

    # --- 3. PHASE DETECTION ---
    # MACRO STATE: Always BULL unless we lose the Macro Low or 200 SMA definitively.
    macro_state = "MACRO BULL"
    if curr['close'] < macro_low: macro_state = "MACRO FAILURE"

    # MICRO STATE:
    # RUN (Impulse): Price > 21 EMA
    # REST (Pullback): Price < 21 EMA
    micro_state = "RUN (IMPULSE)" if curr['close'] > curr['ema_21'] else "REST (PULLBACK)"
    
    # --- 4. ROTATION DETECTOR (The "Get In" Signal) ---
    # Rotation = We were resting, but now Price is reclaiming key levels.
    is_rotating = False
    
    # Scenario A: The 21 Cross (Standard Rotation)
    # Price closed above 21 EMA after being below it
    if prev['close'] < prev['ema_21'] and curr['close'] > curr['ema_21']:
        is_rotating = True
        
    # Scenario B: The 200 Reclaim (Deep Value Rotation)
    if prev['close'] < prev['sma_200'] and curr['close'] > curr['sma_200']:
        is_rotating = True

    # --- 5. EXHAUSTION CHECK (Gravity) ---
    # If we are resting, how deep is the cut?
    exhaustion = "HEALTHY"
    if curr['close'] < fib_0618: exhaustion = "HEAVY"   # Bleeding past Golden Pocket
    if curr['close'] < fib_0786: exhaustion = "CRITICAL" # Threatening Macro Structure

    return {
        "price": curr['close'],
        "macro": macro_state,
        "micro": micro_state,
        "rotation": is_rotating,
        "exhaustion": exhaustion,
        "indicators": {
            "ema_21": curr['ema_21'],
            "sma_200": curr['sma_200']
        },
        "fibs": {
            "top": local_high,
            "bottom": macro_low,
            "shallow": fib_0382,
            "golden": fib_0618,
            "deep": fib_0786
        },
        # We pass empty lists here to satisfy the contract, 
        # actual zones are handled by the legacy engine if needed, 
        # but for Wealth, we focus on Fibs + MAs.
        "zones": {"supply": [], "demand": []} 
    }