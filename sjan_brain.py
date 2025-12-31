from __future__ import annotations
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np
import ktbb_intel

def analyze_market_structure(
    monthly_candles: List[Dict], 
    weekly_candles: List[Dict], # This now receives DAILY data
    overrides: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    S-JAN WEALTH LOGIC v6.2: DAILY TIMEFRAME & LOGIC SAFETY
    - Uses Daily Data for 21/200 MAs
    - Prevents inverted Buy Zones (Buying above market)
    """
    if not overrides: overrides = {}
    
    # --- 1. DATA PREP ---
    # NOTE: 'weekly_candles' actually contains DAILY data now from the feed update
    df = pd.DataFrame(weekly_candles)
    if df.empty: return {}
    
    # Calculate Trends (Daily MAs)
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['sma_200'] = df['close'].rolling(window=200).mean()
    
    curr = df.iloc[-1]
    price = float(curr['close'])

    # --- 2. MACRO ANCHORING (Monthly Context) ---
    # We use the monthly dataset for the 15k low to ensure we go back far enough
    df_macro = pd.DataFrame(monthly_candles)
    if not df_macro.empty:
        macro_low = float(df_macro['low'].min())
        # Find high since that low
        try:
            low_idx = df_macro['low'].idxmin()
            macro_high = float(df_macro.loc[low_idx:]['high'].max())
        except:
            macro_high = float(df_macro['high'].max())
    else:
        # Fallback to daily data if monthly fails
        macro_low = float(df['low'].min())
        macro_high = float(df['high'].max())

    # OVERRIDES
    if overrides.get('macro_low_price'):
        macro_low = float(overrides['macro_low_price'])
    
    # --- 3. FIBONACCI GRID ---
    macro_rng = macro_high - macro_low
    fibs = {
        "top": macro_high,
        "fib_0_5": macro_high - (macro_rng * 0.5),   
        "fib_0_618": macro_high - (macro_rng * 0.618), 
        "fib_0_786": macro_high - (macro_rng * 0.786), 
        "bottom": macro_low
    }

    # --- 4. MICRO STRUCTURE (Daily) ---
    micro_low = macro_low
    if overrides.get('micro_low_price'):
        micro_low = float(overrides['micro_low_price'])
    
    # 21 vs 200 Cross Logic (Daily)
    is_micro_bull = (curr['ema_21'] > curr['sma_200'])
    
    deploy_top = 0.0
    deploy_bot = 0.0
    
    if is_micro_bull:
        # Bullish: Buy Zone is between 21 EMA and 0.382 Pullback
        # LOGIC FIX: Ensure we don't set a buy target ABOVE the current local high
        
        # 1. Top of Buy Zone = 21 Daily EMA (Dynamic Support)
        deploy_top = float(curr['ema_21'])
        
        # 2. Bottom of Buy Zone = 0.382 Fib of the current leg
        micro_rng = macro_high - micro_low
        deploy_bot = macro_high - (micro_rng * 0.382)
        
        phase = "STAIR_STEP_CLIMB"
    else:
        # Bearish: Buy Zone is deep value (0.618 - 0.786)
        deploy_top = fibs['fib_0_618']
        deploy_bot = fibs['fib_0_786']
        phase = "MACRO_CORRECTION"

    # --- 5. SAFETY SANITY CHECK (The "No Nonsense" Fix) ---
    # If the calculated "Top" of the buy zone is somehow lower than the "Bottom", swap them
    if deploy_top < deploy_bot:
        deploy_top, deploy_bot = deploy_bot, deploy_top
        
    # If the "Buy Zone" is significantly ABOVE current price (e.g. > 5%), 
    # it means we broke structure downwards. Cap the buy zone to current price.
    if deploy_top > price * 1.05: 
        deploy_top = price * 0.99 # Buy just below market
        
    extract_top = macro_high * 1.05
    extract_bot = macro_high * 0.98
    
    if price < fibs['fib_0_5']:
        phase = "CYCLE_BREAKDOWN_WARNING"

    # Grid Gen
    deploy_zone = {"levels": [], "top": deploy_top, "bottom": deploy_bot}
    step_d = (deploy_top - deploy_bot) / 3
    deploy_zone['levels'] = [deploy_top - (step_d * i) for i in range(4)]

    extract_zone = {"levels": [extract_top, extract_bot], "top": extract_top, "bottom": extract_bot}

    # --- 6. EXPORT ---
    charts = {
        "ema_21": [{"time": int(r['time']), "value": float(r['ema_21'])} for _, r in df.iterrows() if not pd.isna(r['ema_21'])],
        "sma_200": [{"time": int(r['time']), "value": float(r['sma_200'])} for _, r in df.iterrows() if not pd.isna(r['sma_200'])]
    }

    return {
        "price": price,
        "phase": phase,
        "zones": {"deploy": deploy_zone, "extract": extract_zone},
        "fibs": {
            "macro_50": fibs['fib_0_5'],
            "macro_top": macro_high,
            "macro_bot": macro_low
        },
        "indicators": {
            "ema_21": float(curr['ema_21']), 
            "sma_200": float(curr['sma_200'])
        },
        "charts": charts
    }