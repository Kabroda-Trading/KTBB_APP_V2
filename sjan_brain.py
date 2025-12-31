# sjan_brain.py
from __future__ import annotations
from typing import List, Dict, Any
import pandas as pd
import numpy as np
import ktbb_intel

def analyze_market_structure(monthly_candles: List[Dict], weekly_candles: List[Dict]) -> Dict[str, Any]:
    """
    S-JAN WEALTH LOGIC v5.0: THE UNIFIED ENGINE
    Combines Macro Cycle, Micro Stair-Steps, and Structural Shelves into weighted Clouds.
    """
    # --- 1. DATA PREP ---
    df = pd.DataFrame(weekly_candles)
    if df.empty: return {}
    
    # Calculate Trends
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['sma_200'] = df['close'].rolling(window=200).mean()
    
    curr = df.iloc[-1]
    price = float(curr['close'])

    # --- 2. MACRO CYCLE (The Map) ---
    # Finds absolute bottom of the history (Crypto Winter)
    macro_low = float(df['low'].min())
    # Finds absolute high since that bottom
    low_idx = df['low'].idxmin()
    df_macro = df.loc[low_idx:].copy()
    macro_high = float(df_macro['high'].max())
    
    macro_rng = macro_high - macro_low
    macro_fibs = {
        "top": macro_high,
        "fib_0_5": macro_high - (macro_rng * 0.5),   # The Safety Line
        "fib_0_618": macro_high - (macro_rng * 0.618), # Deep Value
        "fib_0_786": macro_high - (macro_rng * 0.786), # Winter Bottom
        "bottom": macro_low
    }

    # --- 3. MICRO STAIR-STEP (The Vehicle) ---
    # Detect 21/200 Crosses
    df['cross'] = 0
    df.loc[(df['ema_21'] > df['sma_200']) & (df['ema_21'].shift(1) <= df['sma_200'].shift(1)), 'cross'] = 1 # Golden
    df.loc[(df['ema_21'] < df['sma_200']) & (df['ema_21'].shift(1) >= df['sma_200'].shift(1)), 'cross'] = -1 # Death

    # Find start of CURRENT Stair Step
    last_golden_idx = df[df['cross'] == 1].last_valid_index()
    last_death_idx = df[df['cross'] == -1].last_valid_index()
    
    # Default to macro values if no crosses found
    micro_low = macro_low
    micro_high = macro_high
    
    # Determine Active Micro Trend
    is_micro_bull = True
    if last_golden_idx is not None:
        # If death cross is more recent, we are in a Micro Bear Pullback
        if last_death_idx is not None and last_death_idx > last_golden_idx:
            is_micro_bull = False
            # Micro Bear: High is the top of the failed run, Low is forming
            df_micro = df.loc[last_golden_idx:last_death_idx] # The run up
            micro_high = float(df_micro['high'].max())
            micro_low = float(df.loc[last_death_idx:]['low'].min()) # Current low finding
        else:
            # Micro Bull: Low is the Golden Cross low
            df_micro = df.loc[last_golden_idx:]
            micro_low = float(df_micro['low'].min())
            micro_high = float(df_micro['high'].max())

    micro_rng = micro_high - micro_low
    micro_fibs = {
        "fib_0_382": micro_high - (micro_rng * 0.382), # Shallow Step
        "fib_0_618": micro_high - (micro_rng * 0.618), # Deep Step
        "ext_1_618": micro_high + (micro_rng * 0.618)  # Target Extension
    }

    # --- 4. STRUCTURAL TERRAIN (The Shelves) ---
    shelves = ktbb_intel.find_supply_demand_shelves(weekly_candles)

    # --- 5. SYNTHESIS: DEFINING THE CLOUDS ---
    # We weigh the inputs to define the "Cloud" borders
    
    # DEPLOYMENT CLOUD (The Buy Zone)
    # If Micro Bull: 21 EMA down to Micro 0.382 (The Stair Step)
    # If Macro Bear: Macro 0.618 down to Macro 0.786 (The Winter)
    
    deploy_top = 0
    deploy_bot = 0
    
    if is_micro_bull:
        # Stair Stepping Up
        deploy_top = float(curr['ema_21'])
        deploy_bot = micro_fibs['fib_0_382']
        phase = "STAIR_STEP_CLIMB"
    else:
        # Pulling Back / Winter
        deploy_top = macro_fibs['fib_0_618']
        deploy_bot = macro_fibs['fib_0_786']
        phase = "MACRO_CORRECTION"

    # EXTRACTION CLOUD (The Sell Zone)
    # Target: Previous ATH or Micro Extensions
    extract_top = micro_fibs['ext_1_618']
    extract_bot = micro_high * 0.98 # Just below local high
    
    # --- 6. SAFETY OVERRIDE ---
    # If price breaks Macro 0.5, we scream warning
    is_broken = price < macro_fibs['fib_0_5']
    if is_broken:
        phase = "CYCLE_BREAKDOWN_WARNING"
        deploy_top = macro_fibs['fib_0_786'] # Lower the buy zone
        deploy_bot = macro_low

    # --- 7. GRID GENERATION ---
    deploy_zone = {"levels": [], "top": deploy_top, "bottom": deploy_bot}
    step_d = (deploy_top - deploy_bot) / 3
    deploy_zone['levels'] = [deploy_top - (step_d * i) for i in range(4)]

    extract_zone = {"levels": [], "top": extract_top, "bottom": extract_bot}
    step_e = (extract_top - extract_bot) / 3
    extract_zone['levels'] = [extract_bot + (step_e * i) for i in range(4)]

    # --- 8. EXPORT ---
    charts = {
        "ema_21": [{"time": int(r['time']), "value": float(r['ema_21'])} for _, r in df.iterrows() if not pd.isna(r['ema_21'])],
        "sma_200": [{"time": int(r['time']), "value": float(r['sma_200'])} for _, r in df.iterrows() if not pd.isna(r['sma_200'])]
    }

    return {
        "price": price,
        "phase": phase,
        "rotation": is_micro_bull,
        "broken": is_broken,
        "zones": {"deploy": deploy_zone, "extract": extract_zone},
        "fibs": { # Export Critical Lines Only
            "macro_50": macro_fibs['fib_0_5'],
            "macro_top": macro_high,
            "macro_bot": macro_low
        },
        "indicators": {
            "ema_21": float(curr['ema_21']), 
            "sma_200": float(curr['sma_200'])
        },
        "charts": charts
    }