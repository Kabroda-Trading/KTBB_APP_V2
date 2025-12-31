# sjan_brain.py
from __future__ import annotations
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np
import ktbb_intel

def analyze_market_structure(
    monthly_candles: List[Dict], 
    weekly_candles: List[Dict],
    overrides: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    S-JAN WEALTH LOGIC v6.0: CALIBRATED ENGINE
    - Accepts Manual Anchors (God Mode)
    - 7-Year Auto-Scan Limit
    - Candle Pattern Recognition
    """
    if not overrides: overrides = {}
    
    # --- 1. DATA PREP ---
    df = pd.DataFrame(weekly_candles)
    if df.empty: return {}
    
    # Calculate Trends
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['sma_200'] = df['close'].rolling(window=200).mean()
    
    curr = df.iloc[-1]
    price = float(curr['close'])

    # --- 2. MACRO ANCHORING (The Pivot) ---
    # User Override or Auto-Scan (Last ~350 weeks / 7 Years)
    
    # A. MACRO LOW
    if overrides.get('macro_low_price'):
        macro_low = float(overrides['macro_low_price'])
    else:
        # Auto-Scan last 7 years only
        limit_idx = max(0, len(df) - 350)
        df_recent = df.iloc[limit_idx:]
        macro_low = float(df_recent['low'].min())

    # B. MACRO HIGH (Since that low)
    # If user provided a date for low, filter from there. Else use price index.
    # For simplicity in this version, we find the high *after* the determined low price occurred.
    
    # Find index of the low
    try:
        low_row = df[df['low'] == macro_low].iloc[-1] # Last occurrence
        low_idx = low_row.name
    except:
        low_idx = 0 # Fallback
        
    df_cycle = df.loc[low_idx:].copy()
    macro_high = float(df_cycle['high'].max())
    
    # Find Previous Cycle Top (Context) - Look BEFORE the low
    df_pre_cycle = df.loc[:low_idx]
    prev_top = float(df_pre_cycle['high'].max()) if not df_pre_cycle.empty else 0.0

    # --- 3. FIBONACCI GRID ---
    macro_rng = macro_high - macro_low
    fibs = {
        "top": macro_high,
        "prev_top": prev_top,
        "fib_0_5": macro_high - (macro_rng * 0.5),   
        "fib_0_618": macro_high - (macro_rng * 0.618), 
        "fib_0_786": macro_high - (macro_rng * 0.786), 
        "bottom": macro_low
    }

    # --- 4. MICRO STRUCTURE & PATTERNS ---
    # Check for Micro Low Override
    micro_low = macro_low
    if overrides.get('micro_low_price'):
        micro_low = float(overrides['micro_low_price'])
        
    # Pattern Recognition (Last 3 Candles)
    patterns = []
    recent_candles = df.tail(3)
    for _, c in recent_candles.iterrows():
        body = abs(c['close'] - c['open'])
        wick_lower = min(c['close'], c['open']) - c['low']
        wick_upper = c['high'] - max(c['close'], c['open'])
        
        # HAMMER: Long lower wick, small body
        if wick_lower > (body * 2) and wick_upper < body:
            patterns.append("HAMMER REVERSAL")
            
        # BULLISH ENGULFING (Simplified check)
        # Needs reference to previous candle in loop, simplified here for robustness
        
    patterns = list(set(patterns)) # Unique

    # --- 5. SYNTHESIS ---
    # If 21 > 200, we are climbing stairs.
    is_micro_bull = (curr['ema_21'] > curr['sma_200'])
    
    deploy_top = 0
    deploy_bot = 0
    
    if is_micro_bull:
        deploy_top = float(curr['ema_21'])
        # If user gave micro low, use that for fib calculation
        micro_rng = macro_high - micro_low
        deploy_bot = macro_high - (micro_rng * 0.382) 
        phase = "STAIR_STEP_CLIMB"
    else:
        deploy_top = fibs['fib_0_618']
        deploy_bot = fibs['fib_0_786']
        phase = "MACRO_CORRECTION"

    # Safety Check
    if price < fibs['fib_0_5']:
        phase = "CYCLE_BREAKDOWN_WARNING"

    # Grid Gen
    deploy_zone = {"levels": [], "top": deploy_top, "bottom": deploy_bot}
    step = (deploy_top - deploy_bot) / 3
    deploy_zone['levels'] = [deploy_top - (step * i) for i in range(4)]

    # Extraction (Extensions or Previous Top)
    extract_top = macro_high * 1.05
    extract_bot = macro_high * 0.95
    extract_zone = {"levels": [extract_top, extract_bot], "top": extract_top, "bottom": extract_bot}

    # --- 6. EXPORT ---
    charts = {
        "ema_21": [{"time": int(r['time']), "value": float(r['ema_21'])} for _, r in df.iterrows() if not pd.isna(r['ema_21'])],
        "sma_200": [{"time": int(r['time']), "value": float(r['sma_200'])} for _, r in df.iterrows() if not pd.isna(r['sma_200'])]
    }

    return {
        "price": price,
        "phase": phase,
        "patterns": patterns,
        "zones": {"deploy": deploy_zone, "extract": extract_zone},
        "fibs": {
            "macro_50": fibs['fib_0_5'],
            "macro_top": macro_high,
            "macro_bot": macro_low,
            "prev_top": prev_top
        },
        "indicators": {
            "ema_21": float(curr['ema_21']), 
            "sma_200": float(curr['sma_200'])
        },
        "charts": charts
    }