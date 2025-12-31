# sse_engine.py
from __future__ import annotations
from typing import Any, Dict, List, Tuple, Optional
import math

# ---------------------------------------------------------
# 1. VOLUME PROFILE ENGINE (VRVP - Fixed Range)
# ---------------------------------------------------------
def _calculate_vrvp(candles: List[Dict[str, Any]], row_size_pct: float = 0.001) -> Dict[str, float]:
    """
    Calculates POC, VAH, VAL for a specific Fixed Range slice.
    """
    if not candles:
        return {"poc": 0.0, "vah": 0.0, "val": 0.0}

    min_p = min(c['low'] for c in candles)
    max_p = max(c['high'] for c in candles)
    
    if min_p == max_p:
        return {"poc": min_p, "vah": min_p, "val": min_p}

    # Dynamic Bin Size (approx 0.1% of price)
    row_size = max(min_p * row_size_pct, 1.0)
    num_bins = int((max_p - min_p) / row_size) + 1
    volume_profile = [0.0] * num_bins
    total_volume = 0.0

    # Fill Histogram
    for c in candles:
        typical_price = (c['high'] + c['low'] + c['close']) / 3
        vol = c['volume']
        bin_idx = int((typical_price - min_p) / row_size)
        if 0 <= bin_idx < num_bins:
            volume_profile[bin_idx] += vol
            total_volume += vol

    # Find POC (Max Volume)
    max_vol_idx = 0
    max_vol = 0.0
    for i, vol in enumerate(volume_profile):
        if vol > max_vol:
            max_vol = vol
            max_vol_idx = i
            
    poc_price = min_p + (max_vol_idx * row_size)

    # Find Value Area (70% Volume)
    target_vol = total_volume * 0.70
    current_vol = max_vol
    up_idx = max_vol_idx
    down_idx = max_vol_idx
    
    while current_vol < target_vol:
        vol_up = volume_profile[up_idx + 1] if up_idx < num_bins - 1 else 0
        vol_down = volume_profile[down_idx - 1] if down_idx > 0 else 0
        
        if vol_up == 0 and vol_down == 0: break
            
        if vol_up >= vol_down:
            current_vol += vol_up
            up_idx += 1
        else:
            current_vol += vol_down
            down_idx -= 1

    vah_price = min_p + (up_idx * row_size)
    val_price = min_p + (down_idx * row_size)

    return {"poc": poc_price, "vah": vah_price, "val": val_price}

# ---------------------------------------------------------
# 2. PIVOT ENGINE (Supply/Demand Shelves)
# ---------------------------------------------------------
def _resample_candles(candles_15m: List[Dict[str, Any]], timeframe_minutes: int) -> List[Dict[str, Any]]:
    if not candles_15m: return []
    resampled = []
    seconds_per_block = timeframe_minutes * 60
    current_block_start = -1
    temp_candle = None
    sorted_candles = sorted(candles_15m, key=lambda x: x['time'])

    for c in sorted_candles:
        ts = c['time']
        block_start = ts - (ts % seconds_per_block)
        if block_start != current_block_start:
            if temp_candle: resampled.append(temp_candle)
            current_block_start = block_start
            temp_candle = {k: v for k, v in c.items() if k != 'time'}
            temp_candle['time'] = block_start
        else:
            if temp_candle:
                temp_candle["high"] = max(temp_candle["high"], c["high"])
                temp_candle["low"] = min(temp_candle["low"], c["low"])
                temp_candle["close"] = c["close"]
                temp_candle["volume"] += c["volume"]
    if temp_candle: resampled.append(temp_candle)
    return resampled

def _find_pivots(candles: List[Dict[str, Any]], left: int = 3, right: int = 3) -> Tuple[float, float]:
    """
    Standard Pine Script Pivot Algorithm (ta.pivothigh/low)
    """
    if len(candles) < (left + right + 1): return 0.0, 0.0
    last_supply = 0.0
    last_demand = 0.0
    
    for i in range(left, len(candles) - right):
        current = candles[i]
        
        # Supply (Pivot High)
        is_ph = True
        for j in range(1, left + 1):
            if candles[i-j]['high'] > current['high']: is_ph = False; break
        if is_ph:
            for j in range(1, right + 1):
                if candles[i+j]['high'] >= current['high']: is_ph = False; break
        if is_ph: last_supply = current['high']

        # Demand (Pivot Low)
        is_pl = True
        for j in range(1, left + 1):
            if candles[i-j]['low'] < current['low']: is_pl = False; break
        if is_pl:
            for j in range(1, right + 1):
                if candles[i+j]['low'] <= current['low']: is_pl = False; break
        if is_pl: last_demand = current['low']
            
    return last_supply, last_demand

# ---------------------------------------------------------
# 3. TRIGGER LOGIC (The Confluence)
# ---------------------------------------------------------
def _pick_trigger_candidates(
    px: float,
    r30_high: float,
    r30_low: float,
    vrvp_24h: Dict[str, float],
    vrvp_4h: Dict[str, float]
) -> Tuple[float, float]:
    
    # Base: 30m Session Range (Initial Balance)
    bo_base = r30_high
    bd_base = r30_low
    
    # --- CONFLUENCE CHECK: 4H VRVP (Immediate Context) ---
    # If 4H VAH/VAL aligns with Session Range, prefer the Volume Level.
    h4_vah = vrvp_4h.get("vah", 0.0)
    h4_val = vrvp_4h.get("val", 0.0)
    
    # If 4H VAH is defined and slightly above/below breakout, snap to it
    if h4_vah > 0:
        # Check proximity (0.3%). If close, use VAH as stronger confirmation
        if abs(h4_vah - bo_base) / bo_base < 0.003:
            bo_base = h4_vah

    if h4_val > 0:
        if abs(h4_val - bd_base) / bd_base < 0.003:
            bd_base = h4_val

    # --- CONFLUENCE CHECK: 24H VRVP (Session Context) ---
    # 24H Value Area defines the "Fair Value" for the day.
    f24_vah = vrvp_24h.get("vah", 0.0)
    f24_val = vrvp_24h.get("val", 0.0)
    
    # Logic: "Don't breakout inside Yesterday's Value"
    if f24_vah > bo_base:
        # If session high is inside 24H Value, push trigger to 24H VAH
        bo_base = f24_vah
        
    if f24_val > 0 and f24_val < bd_base:
        bd_base = f24_val

    # Safety Buffer (0.2% min from Anchor)
    min_dist = px * 0.002
    bo = max(bo_base, px + min_dist)
    bd = min(bd_base, px - min_dist)
    
    return float(bo), float(bd)

# ---------------------------------------------------------
# 4. MAIN COMPUTE
# ---------------------------------------------------------
def compute_sse_levels(inputs: Dict[str, Any]) -> Dict[str, Any]:
    # 1. Inputs
    raw_15m = inputs.get("raw_15m_candles", [])
    slice_24h = inputs.get("slice_24h", [])
    slice_4h = inputs.get("slice_4h", [])
    
    if not raw_15m: return {"levels": {}, "htf_shelves": {}}

    # 2. Daily Support/Resistance -> Uses PIVOTS (4H/1H Shelves)
    candles_4h = _resample_candles(raw_15m, 240)
    candles_1h = _resample_candles(raw_15m, 60)
    
    sup_4h, dem_4h = _find_pivots(candles_4h)
    sup_1h, dem_1h = _find_pivots(candles_1h)
    
    daily_res = sup_4h if sup_4h > 0 else sup_1h
    daily_sup = dem_4h if dem_4h > 0 else dem_1h
    
    # Fallback
    if daily_res == 0: daily_res = max(c['high'] for c in raw_15m[-96:])
    if daily_sup == 0: daily_sup = min(c['low'] for c in raw_15m[-96:])

    # 3. Triggers -> Uses VRVP (Fixed Range Slices)
    vrvp_24h = _calculate_vrvp(slice_24h)
    vrvp_4h = _calculate_vrvp(slice_4h)
    
    # 4. Calculation
    anchor_px = inputs.get("session_open_price", 0.0)
    r30_h = inputs.get("r30_high", 0.0)
    r30_l = inputs.get("r30_low", 0.0)
    
    bo, bd = _pick_trigger_candidates(anchor_px, r30_h, r30_l, vrvp_24h, vrvp_4h)

    # 5. Output
    return {
        "levels": {
            "daily_support": float(daily_sup),
            "daily_resistance": float(daily_res),
            "breakout_trigger": float(bo),
            "breakdown_trigger": float(bd),
            "range30m_high": float(r30_h),
            "range30m_low": float(r30_l),
            "f24_vah": vrvp_24h["vah"],
            "f24_val": vrvp_24h["val"],
            "f24_poc": vrvp_24h["poc"],
        },
        "htf_shelves": {
            "resistance": [{"level": sup_4h, "tf": "4H"}, {"level": sup_1h, "tf": "1H"}],
            "support": [{"level": dem_4h, "tf": "4H"}, {"level": dem_1h, "tf": "1H"}]
        },
        "intraday_shelves": {}
    }