# sjan_brain.py
from __future__ import annotations
from typing import List, Dict, Any
import pandas as pd
import numpy as np

def analyze_market_structure(monthly_candles: List[Dict], weekly_candles: List[Dict]) -> Dict[str, Any]:
    """
    S-JAN WEALTH LOGIC v3.3: BATTLEFIELD ZONES
    1. Trends: 200 SMA (River) vs 21 EMA (Speed).
    2. Structure: defines 'Deployment' (Buy) and 'Extraction' (Sell) Zones.
    3. Rotation: Detects Momentum shifts for Hybrid triggers.
    """
    # --- 1. MACRO CONTEXT (The River) ---
    df_macro = pd.DataFrame(monthly_candles)
    if df_macro.empty: return {}
    
    # Absolute Cycle Low/High (Deep History)
    macro_low = float(df_macro['low'].min())
    
    # --- 2. MICRO CONTEXT (The Speed) ---
    df = pd.DataFrame(weekly_candles)
    if df.empty: return {}
    
    # Indicators
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['sma_200'] = df['close'].rolling(window=200).mean()
    
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    # --- 3. TACTICAL ZONES (The Geometry) ---
    # We use a localized high to define the current "Battlefield"
    local_high = float(df['high'].tail(180).max())
    rng = local_high - macro_low
    
    # EXTRACTION ZONE: The Premium Area (Top 15% of range)
    # Target: Liquidate positions / Extract Cash
    extraction_zone = {
        "top": local_high,
        "bottom": local_high - (rng * 0.15),
        "levels": []
    }
    step_s = (extraction_zone['top'] - extraction_zone['bottom']) / 3
    extraction_zone['levels'] = [extraction_zone['bottom'] + (step_s * i) for i in range(4)]

    # DEPLOYMENT ZONE: The Discount Area (0.618 - 0.786)
    # Target: Deploy Capital / Enter Positions
    deployment_zone = {
        "top": local_high - (rng * 0.618),
        "bottom": local_high - (rng * 0.786),
        "levels": []
    }
    step_b = (deployment_zone['top'] - deployment_zone['bottom']) / 3
    deployment_zone['levels'] = [deployment_zone['top'] - (step_b * i) for i in range(4)]

    # --- 4. PHASE DETECTION ---
    is_macro_bull = bool(curr['close'] > curr['sma_200'])
    is_micro_bull = bool(curr['close'] > curr['ema_21'])
    is_rotating = bool((prev['close'] < prev['ema_21']) and (curr['close'] > curr['ema_21']))
    
    phase = "UNKNOWN"
    
    if not is_macro_bull:
        phase = "MACRO_WINTER" # Bear Market
    elif is_macro_bull and is_micro_bull:
        phase = "MOMENTUM_RUN" # Expansion
    elif is_macro_bull and not is_micro_bull:
        phase = "BULL_PULLBACK" # Correction in Uptrend
    
    if is_rotating and is_macro_bull:
        phase = "ROTATION_IGNITION"

    # --- 5. EXPORT ---
    charts = {
        "ema_21": [{"time": int(r['time']), "value": float(r['ema_21'])} for _, r in df.iterrows() if not pd.isna(r['ema_21'])],
        "sma_200": [{"time": int(r['time']), "value": float(r['sma_200'])} for _, r in df.iterrows() if not pd.isna(r['sma_200'])]
    }

    return {
        "price": float(curr['close']),
        "phase": phase,
        "rotation": is_rotating,
        "zones": {"deploy": deployment_zone, "extract": extraction_zone},
        "indicators": {
            "ema_21": float(curr['ema_21']) if not pd.isna(curr['ema_21']) else 0.0, 
            "sma_200": float(curr['sma_200']) if not pd.isna(curr['sma_200']) else 0.0
        },
        "charts": charts
    }