# sse_engine.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple
import math

# ---------------------------------------------------------
# 1. HELPERS
# ---------------------------------------------------------
def _pct(x: float, p: float) -> float:
    return x * p

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

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

@dataclass(frozen=True)
class Shelf:
    tf: str
    level: float
    kind: str
    strength: float
    primary: bool = False

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
def _build_htf_shelves(h4_supply, h4_demand, h1_supply, h1_demand):
    res = []; sup = []
    if h4_supply > 0: res.append(Shelf("4H", float(h4_supply), "supply", 0.8, True))
    if h1_supply > 0: res.append(Shelf("1H", float(h1_supply), "supply", 0.6, False))
    if h4_demand > 0: sup.append(Shelf("4H", float(h4_demand), "demand", 0.8, True))
    if h1_demand > 0: sup.append(Shelf("1H", float(h1_demand), "demand", 0.6, False))
    return sorted(res, key=lambda s: s.level), sorted(sup, key=lambda s: s.level)

def _select_daily_levels(resistance, support):
    if not resistance or not support: return 0.0, 0.0, {"resistance": [], "support": []}
    dr = max(resistance, key=lambda s: s.strength).level
    ds = max(support, key=lambda s: s.strength).level
    
    htf_out = {
        "resistance": [{"level": s.level, "tf": s.tf, "strength": s.strength} for s in resistance],
        "support": [{"level": s.level, "tf": s.tf, "strength": s.strength} for s in support]
    }
    return float(ds), float(dr), htf_out

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
        if all(candles[i-j]['high'] <= curr['high'] for j in range(1, left+1)) and \
           all(candles[i+j]['high'] < curr['high'] for j in range(1, right+1)):
            last_supply = curr['high']
        if all(candles[i-j]['low'] >= curr['low'] for j in range(1, left+1)) and \
           all(candles[i+j]['low'] > curr['low'] for j in range(1, right+1)):
            last_demand = curr['low']
            
    return last_supply, last_demand

# ---------------------------------------------------------
# 4. TRIGGER LOGIC (FIXED: Uses Anchor Price, NOT Live Price)
# ---------------------------------------------------------
def _pick_trigger_candidates(anchor_px, r30_h, r30_l, vrvp_24h, vrvp_4h, daily_sup, daily_res):
    # Base Calculation
    bo_base = max(r30_h, vrvp_24h.get("vah", 0)) if r30_h > 0 else daily_res
    bd_base = min(r30_l, vrvp_24h.get("val", 0)) if r30_l > 0 else daily_sup
    
    # FIX: Safety buffer is calculated relative to ANCHOR (Session Open), not live price.
    # This ensures the level is "Frozen" in history.
    min_dist = anchor_px * 0.002 
    
    # FIX: Ensure trigger is at least min_dist away from the OPEN, not current price.
    bo = max(bo_base, anchor_px + min_dist)
    bd = min(bd_base, anchor_px - min_dist)
    
    return float(bo), float(bd)

# ---------------------------------------------------------
# 5. CONTEXT & BIAS ENGINE (Preserved)
# ---------------------------------------------------------
def _calculate_shelf_imbalance(px: float, shelves: Dict[str, List[Dict]]) -> float:
    k = 100.0
    eps = 1e-9
    pressure_up = 0.0
    for s in shelves.get("resistance", []):
        dist = abs(s["level"] - px)
        pressure_up += (s["strength"] / (dist + k))
    pressure_down = 0.0
    for s in shelves.get("support", []):
        dist = abs(px - s["level"])
        pressure_down += (s["strength"] / (dist + k))
    total = pressure_down + pressure_up + eps
    return _clamp((pressure_down - pressure_up) / total, -1.0, 1.0)

def _build_context(px: float, bo: float, bd: float, 
                  f24_poc: float, f24_vah: float, f24_val: float, 
                  raw_15m: List[Dict], daily_candles: List[Dict]) -> Dict:
    trend_1d = "range"; trend_4h = "range"; slope_score = 0.0
    
    if len(daily_candles) > 50:
        d_closes = [c['close'] for c in daily_candles]
        if _calculate_sma(d_closes, 20) > _calculate_sma(d_closes, 50): trend_1d = "up"; slope_score += 0.5
        elif _calculate_sma(d_closes, 20) < _calculate_sma(d_closes, 50): trend_1d = "down"; slope_score -= 0.5
        
    if len(raw_15m) > 200:
        closes = [c['close'] for c in raw_15m]
        if _calculate_sma(closes, 50) > _calculate_sma(closes, 200): trend_4h = "up"; slope_score += 0.5
        elif _calculate_sma(closes, 50) < _calculate_sma(closes, 200): trend_4h = "down"; slope_score -= 0.5
    slope_score = _clamp(slope_score, -1.0, 1.0)
    
    loc = "in_value"
    if px > f24_vah: loc = "above_value"
    elif px < f24_val: loc = "below_value"
    
    atr = _calculate_atr(raw_15m)
    comp_score = 0.0
    if atr > 0:
        curr_rng = max(bo - bd, 1.0)
        if curr_rng < atr * 0.5: comp_score = 0.8
        elif curr_rng < atr: comp_score = 0.5
        
    return {
        "htf": { "trend_1d": trend_1d, "trend_4h": trend_4h, "slope_score": slope_score },
        "location": { "opening_location": loc, "vs_f24_poc": px - f24_poc, "distance_to_breakout": bo - px, "distance_to_breakdown": px - bd },
        "volatility": { "atr_14": atr, "compression_score": comp_score },
        "auction": { "overnight_direction": "balanced" }
    }

def _calculate_bias_model(ctx: Dict, px: float, bo: float, bd: float, shelves: Dict) -> Dict:
    drivers = []
    score = 0.0
    
    # Drivers
    drivers.append({"id": "HTF_TREND", "weight": 0.25, "value": ctx["htf"]["slope_score"], "note": f"Trend: {ctx['htf']['trend_4h']}"})
    score += (ctx["htf"]["slope_score"] * 0.25)
    
    loc_val = 1.0 if ctx["location"]["opening_location"] == "above_value" else (-1.0 if ctx["location"]["opening_location"] == "below_value" else 0.0)
    drivers.append({"id": "LOCATION_VALUE", "weight": 0.20, "value": loc_val, "note": f"Opening {ctx['location']['opening_location']}"})
    score += (loc_val * 0.20)
    
    d_bo = abs(ctx["location"]["distance_to_breakout"])
    d_bd = abs(ctx["location"]["distance_to_breakdown"])
    asym_val = _clamp((d_bd - d_bo) / max(d_bo, d_bd, 1.0), -1.0, 1.0)
    drivers.append({"id": "TRIGGER_ASYMMETRY", "weight": 0.20, "value": asym_val, "note": "Trigger Proximity"})
    score += (asym_val * 0.20)
    
    shelf_val = _calculate_shelf_imbalance(px, shelves)
    drivers.append({"id": "SHELF_IMBALANCE", "weight": 0.25, "value": shelf_val, "note": "Shelf Pressure"})
    score += (shelf_val * 0.25)
    
    overnight_val = 0.5 if loc_val > 0 else (-0.5 if loc_val < 0 else 0)
    drivers.append({"id": "OVERNIGHT_DIRECTION", "weight": 0.10, "value": overnight_val, "note": "Context Carry"})
    score += (overnight_val * 0.10)

    # Output
    direction = "long" if score > 0.15 else ("short" if score < -0.15 else "neutral")
    conf = min(0.90, abs(score)) * (1.0 - (ctx["volatility"]["compression_score"] * 0.6)) * 100
    
    return {
        "daily_lean": { "direction": direction, "score": round(score, 2), "confidence": round(conf, 1), "drivers": drivers, "summary": f"Lean {direction.upper()} ({int(conf)}%)" },
        "permission_state": { "state": "HOLD_FIRE", "active_side": "none", "earned_by": ["15m_acceptance"], "evidence": { "reclaim_detected": False } }
    }

# ---------------------------------------------------------
# 6. MAIN COMPUTE
# ---------------------------------------------------------
def compute_sse_levels(inputs: Dict[str, Any]) -> Dict[str, Any]:
    def f(k, d=0.0): return float(inputs.get(k) or d)
    
    raw_15m = inputs.get("raw_15m_candles", [])
    daily_candles = inputs.get("raw_daily_candles", [])
    slice_24h = inputs.get("slice_24h", [])
    slice_4h = inputs.get("slice_4h", [])
    
    # 1. PIVOTS
    candles_4h = _resample_candles(raw_15m, 240)
    candles_1h = _resample_candles(raw_15m, 60)
    sup_4h, dem_4h = _find_pivots(candles_4h)
    sup_1h, dem_1h = _find_pivots(candles_1h)
    
    res_list, sup_list = _build_htf_shelves(sup_4h, dem_4h, sup_1h, dem_1h)
    ds, dr, htf_out = _select_daily_levels(res_list, sup_list)
    if dr == 0 and raw_15m: dr = max(c['high'] for c in raw_15m[-96:])
    if ds == 0 and raw_15m: ds = min(c['low'] for c in raw_15m[-96:])

    # 2. VRVP
    vrvp_24h = _calculate_vrvp(slice_24h)
    vrvp_4h = _calculate_vrvp(slice_4h)
    
    # 3. TRIGGERS (FIXED: USING ANCHOR PRICE)
    # Note: 'last_price' is actually the anchor closing price when passed from dmr_report correctly
    # But strictly, we use session_open_price as the anchor for calculation.
    anchor_px = f("session_open_price")
    live_px = f("last_price")
    
    r30_h = f("r30_high")
    r30_l = f("r30_low")
    
    bo, bd = _pick_trigger_candidates(anchor_px, r30_h, r30_l, vrvp_24h, vrvp_4h, ds, dr)
    
    # 4. CONTEXT
    ctx = _build_context(anchor_px, bo, bd, vrvp_24h["poc"], vrvp_24h["vah"], vrvp_24h["val"], raw_15m, daily_candles)
    
    # 5. BIAS
    bias = _calculate_bias_model(ctx, anchor_px, bo, bd, htf_out)
    
    # 6. PERMISSION (Checked against LIVE price for status)
    perm = bias["permission_state"]
    if live_px > bo: perm["state"] = "DIRECTIONAL_LONG"; perm["active_side"] = "long"
    elif live_px < bd: perm["state"] = "DIRECTIONAL_SHORT"; perm["active_side"] = "short"
    elif abs(bias["daily_lean"]["score"]) < 0.2: perm["state"] = "ROTATIONAL_PERMITTED"

    return {
        "levels": {
            "daily_support": ds, "daily_resistance": dr,
            "breakout_trigger": bo, "breakdown_trigger": bd,
            "range30m_high": r30_h, "range30m_low": r30_l,
            "f24_poc": vrvp_24h["poc"], "f24_vah": vrvp_24h["vah"], "f24_val": vrvp_24h["val"]
        },
        "bias_model": bias,
        "context": ctx,
        "htf_shelves": htf_out,
        "intraday_shelves": {}
    }