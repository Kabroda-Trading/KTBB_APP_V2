# sse_engine.py
from __future__ import annotations
from typing import Any, Dict, List, Tuple
import math

# ---------------------------------------------------------
# 1. HELPERS
# ---------------------------------------------------------
def _pct(x: float, p: float) -> float:
    return x * p

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def _safe_get(arr: List, idx: int, default: float = 0.0) -> float:
    try: return float(arr[idx])
    except: return default

def _calculate_sma(prices: List[float], period: int) -> float:
    if len(prices) < period: return 0.0
    return sum(prices[-period:]) / period

def _calculate_atr(candles: List[Dict], period: int = 14) -> float:
    if len(candles) < period + 1: return 0.0
    tr_sum = 0.0
    for i in range(1, period + 1):
        c = candles[-i]
        p = candles[-i-1]
        hl = c['high'] - c['low']
        hc = abs(c['high'] - p['close'])
        lc = abs(c['low'] - p['close'])
        tr_sum += max(hl, hc, lc)
    return tr_sum / period

# ---------------------------------------------------------
# 2. VRVP ENGINE (Preserved)
# ---------------------------------------------------------
def _calculate_vrvp(candles: List[Dict[str, Any]], row_size_pct: float = 0.001) -> Dict[str, float]:
    if not candles: return {"poc": 0.0, "vah": 0.0, "val": 0.0}
    
    min_p = min(c['low'] for c in candles)
    max_p = max(c['high'] for c in candles)
    if min_p == max_p: return {"poc": min_p, "vah": min_p, "val": min_p}

    row_size = max(min_p * row_size_pct, 1.0)
    num_bins = int((max_p - min_p) / row_size) + 1
    volume_profile = [0.0] * num_bins
    total_volume = 0.0

    for c in candles:
        typical = (c['high'] + c['low'] + c['close']) / 3
        vol = c['volume']
        bin_idx = int((typical - min_p) / row_size)
        if 0 <= bin_idx < num_bins:
            volume_profile[bin_idx] += vol
            total_volume += vol

    max_vol_idx = 0
    max_vol = 0.0
    for i, vol in enumerate(volume_profile):
        if vol > max_vol: max_vol = vol; max_vol_idx = i
            
    poc = min_p + (max_vol_idx * row_size)

    # Value Area (70%)
    target = total_volume * 0.70
    curr = max_vol
    up, down = max_vol_idx, max_vol_idx
    
    while curr < target:
        v_up = volume_profile[up+1] if up < num_bins-1 else 0
        v_dn = volume_profile[down-1] if down > 0 else 0
        if v_up == 0 and v_dn == 0: break
        if v_up >= v_dn: curr += v_up; up += 1
        else: curr += v_dn; down -= 1
            
    return {"poc": poc, "vah": min_p + (up * row_size), "val": min_p + (down * row_size)}

# ---------------------------------------------------------
# 3. PIVOT ENGINE (Preserved)
# ---------------------------------------------------------
def _resample_candles(candles_15m: List[Dict], timeframe_minutes: int) -> List[Dict]:
    if not candles_15m: return []
    resampled = []
    block_sec = timeframe_minutes * 60
    curr_start = -1
    temp = None
    
    for c in sorted(candles_15m, key=lambda x: x['time']):
        ts = c['time']
        start = ts - (ts % block_sec)
        if start != curr_start:
            if temp: resampled.append(temp)
            curr_start = start
            temp = {k: v for k, v in c.items() if k != 'time'}
            temp['time'] = start
        else:
            if temp:
                temp["high"] = max(temp["high"], c["high"])
                temp["low"] = min(temp["low"], c["low"])
                temp["close"] = c["close"]
                temp["volume"] += c["volume"]
    if temp: resampled.append(temp)
    return resampled

def _find_pivots(candles: List[Dict], left: int = 3, right: int = 3) -> Tuple[float, float]:
    if len(candles) < (left + right + 1): return 0.0, 0.0
    last_supply = 0.0
    last_demand = 0.0
    
    for i in range(left, len(candles) - right):
        curr = candles[i]
        # Supply
        if all(candles[i-j]['high'] <= curr['high'] for j in range(1, left+1)) and \
           all(candles[i+j]['high'] < curr['high'] for j in range(1, right+1)):
            last_supply = curr['high']
        # Demand
        if all(candles[i-j]['low'] >= curr['low'] for j in range(1, left+1)) and \
           all(candles[i+j]['low'] > curr['low'] for j in range(1, right+1)):
            last_demand = curr['low']
            
    return last_supply, last_demand

# ---------------------------------------------------------
# 4. TRIGGER LOGIC (Preserved)
# ---------------------------------------------------------
def _pick_trigger_candidates(px, r30_h, r30_l, vrvp_24h, vrvp_4h):
    bo_base = r30_h
    bd_base = r30_l
    
    # 4H VRVP Confluence
    h4_vah = vrvp_4h.get("vah", 0.0)
    h4_val = vrvp_4h.get("val", 0.0)
    
    if h4_vah > 0 and abs(h4_vah - bo_base)/bo_base < 0.003: bo_base = h4_vah
    if h4_val > 0 and abs(h4_val - bd_base)/bd_base < 0.003: bd_base = h4_val

    # 24H VRVP Context
    f24_vah = vrvp_24h.get("vah", 0.0)
    f24_val = vrvp_24h.get("val", 0.0)
    
    if f24_vah > bo_base: bo_base = f24_vah
    if f24_val > 0 and f24_val < bd_base: bd_base = f24_val

    # Safety
    min_dist = px * 0.002
    bo = max(bo_base, px + min_dist)
    bd = min(bd_base, px - min_dist)
    
    return float(bo), float(bd)

# ---------------------------------------------------------
# 5. NEW: CONTEXT & BIAS ENGINE (The Upgrade)
# ---------------------------------------------------------
def _build_context(px: float, daily_res: float, daily_sup: float, bo: float, bd: float, 
                  f24_poc: float, f24_vah: float, f24_val: float, raw_15m: List[Dict]) -> Dict:
    
    # 1. HTF Trend (Slope of SMA)
    trend_4h = "range"
    trend_1d = "range"
    slope_score = 0.0
    
    if len(raw_15m) > 100:
        closes = [c['close'] for c in raw_15m]
        sma_50 = _calculate_sma(closes, 50)
        sma_200 = _calculate_sma(closes, 200)
        
        if sma_50 > sma_200: 
            trend_4h = "up"
            slope_score = 1.0
        elif sma_50 < sma_200: 
            trend_4h = "down"
            slope_score = -1.0
            
    # 2. Location
    loc = "in_value"
    if px > f24_vah: loc = "above_value"
    elif px < f24_val: loc = "below_value"
    
    # 3. Volatility
    atr = _calculate_atr(raw_15m)
    comp_score = 0.0
    if atr > 0:
        # Simple compression metric: Current Range vs ATR
        curr_rng = bo - bd
        if curr_rng < atr * 0.5: comp_score = 0.8 # Highly Compressed
        elif curr_rng < atr: comp_score = 0.5
        
    return {
        "htf": {
            "trend_4h": trend_4h,
            "trend_1d": trend_1d, # Placeholder for now
            "slope_score": slope_score
        },
        "location": {
            "opening_location": loc,
            "vs_f24_poc": px - f24_poc,
            "distance_to_breakout": bo - px,
            "distance_to_breakdown": px - bd
        },
        "volatility": {
            "atr_14": atr,
            "compression_score": comp_score
        }
    }

def _calculate_bias_model(ctx: Dict, px: float, bo: float, bd: float) -> Dict:
    drivers = []
    score = 0.0
    
    # Driver 1: HTF Trend (Weight 0.25)
    trend_val = ctx["htf"]["slope_score"]
    drivers.append({"id": "HTF_TREND", "weight": 0.25, "value": trend_val, "note": f"4H Trend is {ctx['htf']['trend_4h']}"})
    score += (trend_val * 0.25)
    
    # Driver 2: Location Value (Weight 0.30)
    loc_val = 0.0
    if ctx["location"]["opening_location"] == "above_value": loc_val = 1.0
    elif ctx["location"]["opening_location"] == "below_value": loc_val = -1.0
    drivers.append({"id": "LOCATION_VALUE", "weight": 0.30, "value": loc_val, "note": f"Opening {ctx['location']['opening_location']}"})
    score += (loc_val * 0.30)
    
    # Driver 3: Trigger Asymmetry (Weight 0.25)
    d_bo = abs(ctx["location"]["distance_to_breakout"])
    d_bd = abs(ctx["location"]["distance_to_breakdown"])
    max_d = max(d_bo, d_bd, 1.0)
    asym_val = _clamp((d_bd - d_bo) / max_d, -1.0, 1.0)
    drivers.append({"id": "TRIGGER_ASYMMETRY", "weight": 0.25, "value": asym_val, "note": "Proximity to Triggers"})
    score += (asym_val * 0.25)
    
    # Compression Penalty
    conf_penalty = ctx["volatility"]["compression_score"]
    
    # Final Calculation
    direction = "neutral"
    if score > 0.15: direction = "long"
    elif score < -0.15: direction = "short"
    
    confidence = min(0.85, abs(score)) * (1.0 - (conf_penalty * 0.5)) * 100
    
    return {
        "daily_lean": {
            "direction": direction,
            "score": round(score, 2),
            "confidence": round(confidence, 1),
            "drivers": drivers,
            "summary": f"Lean {direction.upper()} ({int(confidence)}% Conf)"
        },
        "permission_state": {
            "state": "HOLD_FIRE", # Default
            "active_side": "none",
            "earned_by": ["15m_acceptance", "5m_alignment"]
        }
    }

# ---------------------------------------------------------
# 6. MAIN COMPUTE
# ---------------------------------------------------------
def compute_sse_levels(inputs: Dict[str, Any]) -> Dict[str, Any]:
    raw_15m = inputs.get("raw_15m_candles", [])
    slice_24h = inputs.get("slice_24h", [])
    slice_4h = inputs.get("slice_4h", [])
    
    # 1. Pivots (Support/Resistance)
    candles_4h = _resample_candles(raw_15m, 240)
    candles_1h = _resample_candles(raw_15m, 60)
    sup_4h, dem_4h = _find_pivots(candles_4h)
    sup_1h, dem_1h = _find_pivots(candles_1h)
    
    dr = sup_4h if sup_4h > 0 else (sup_1h if sup_1h > 0 else (max(c['high'] for c in raw_15m[-96:]) if raw_15m else 0))
    ds = dem_4h if dem_4h > 0 else (dem_1h if dem_1h > 0 else (min(c['low'] for c in raw_15m[-96:]) if raw_15m else 0))

    # 2. VRVP
    vrvp_24h = _calculate_vrvp(slice_24h)
    vrvp_4h = _calculate_vrvp(slice_4h)
    
    # 3. Triggers
    px = inputs.get("last_price", 0.0)
    r30_h = inputs.get("r30_high", 0.0)
    r30_l = inputs.get("r30_low", 0.0)
    
    bo, bd = _pick_trigger_candidates(px, r30_h, r30_l, vrvp_24h, vrvp_4h)
    
    # 4. CONTEXT & BIAS (New)
    ctx = _build_context(px, dr, ds, bo, bd, vrvp_24h["poc"], vrvp_24h["vah"], vrvp_24h["val"], raw_15m)
    bias = _calculate_bias_model(ctx, px, bo, bd)
    
    # 5. EXECUTION PERMISSION (Gatekeeper Logic)
    perm = bias["permission_state"]
    if px > bo: 
        perm["state"] = "DIRECTIONAL_LONG"
        perm["active_side"] = "long"
    elif px < bd: 
        perm["state"] = "DIRECTIONAL_SHORT"
        perm["active_side"] = "short"
    elif abs(bias["daily_lean"]["score"]) < 0.2: 
        perm["state"] = "ROTATIONAL_PERMITTED"

    return {
        "levels": {
            "daily_support": ds, "daily_resistance": dr,
            "breakout_trigger": bo, "breakdown_trigger": bd,
            "range30m_high": r30_h, "range30m_low": r30_l,
            "f24_poc": vrvp_24h["poc"], "f24_vah": vrvp_24h["vah"], "f24_val": vrvp_24h["val"]
        },
        "bias_model": bias, # v1.2 Contract
        "context": ctx,     # v1.2 Contract
        "htf_shelves": {
            "resistance": [{"level": sup_4h, "tf": "4H", "strength": 0.8}, {"level": sup_1h, "tf": "1H", "strength": 0.6}],
            "support": [{"level": dem_4h, "tf": "4H", "strength": 0.8}, {"level": dem_1h, "tf": "1H", "strength": 0.6}]
        },
        "intraday_shelves": {}
    }