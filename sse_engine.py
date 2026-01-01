# sse_engine.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple
import math

# ---------------------------------------------------------
# 1. DATA STRUCTURES
# ---------------------------------------------------------
@dataclass(frozen=True)
class Shelf:
    tf: str
    level: float
    kind: str
    strength: float
    primary: bool = False

# ---------------------------------------------------------
# 2. HELPERS (Math & Scoring)
# ---------------------------------------------------------
def _pct(x: float, p: float) -> float:
    return x * p

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def _calculate_sma(prices: List[float], period: int) -> float:
    if len(prices) < period: return 0.0
    return sum(prices[-period:]) / period

# ---------------------------------------------------------
# 3. VRVP ENGINE (Preserved)
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

    # Value Area (Simple Approx for Speed)
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
# 4. EXISTING LOGIC (Shelves & Pivots - Preserved)
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
    
    # Format for JSON output
    htf_out = {
        "resistance": [{"level": s.level, "tf": s.tf, "strength": s.strength} for s in resistance],
        "support": [{"level": s.level, "tf": s.tf, "strength": s.strength} for s in support]
    }
    return float(ds), float(dr), htf_out

def _pick_trigger_candidates(px, r30_h, r30_l, f24_vah, f24_val, daily_sup, daily_res):
    # Base: 30m Range
    bo_base = max(r30_h, f24_vah) if r30_h > 0 else daily_res
    bd_base = min(r30_l, f24_val) if r30_l > 0 else daily_sup
    
    # Minimal Safety Buffer (0.2%)
    min_dist = px * 0.002
    bo = max(bo_base, px + min_dist)
    bd = min(bd_base, px - min_dist)
    
    return float(bo), float(bd)

# ---------------------------------------------------------
# 5. NEW: BIAS INTELLIGENCE ENGINE (The Update)
# ---------------------------------------------------------
def _calculate_bias_model(
    px: float, 
    daily_res: float, daily_sup: float,
    bo_trig: float, bd_trig: float,
    f24_poc: float,
    raw_15m: List[Dict]
) -> Dict[str, Any]:
    """
    Calculates a deterministic 'Lean Score' (-1 to +1) based on structural drivers.
    """
    drivers = []
    total_score = 0.0
    
    # 1. LOCATION VALUE (Price vs 24H POC)
    # Weight: 0.3
    loc_val = 0.0
    if f24_poc > 0:
        if px > f24_poc * 1.001: loc_val = 1.0 # Above Value
        elif px < f24_poc * 0.999: loc_val = -1.0 # Below Value
        else: loc_val = 0.0 # Inside Value
    
    drivers.append({"id": "LOCATION_VALUE", "weight": 0.3, "value": loc_val, "note": "Price relative to 24H Volume Control"})
    total_score += (loc_val * 0.3)

    # 2. TRIGGER ASYMMETRY (Distance to Breakout vs Breakdown)
    # Weight: 0.25
    dist_bo = abs(bo_trig - px)
    dist_bd = abs(bd_trig - px)
    max_dist = max(dist_bo, dist_bd, 1.0)
    
    # If closer to breakout, lean bullish (positive)
    asym_val = _clamp((dist_bd - dist_bo) / max_dist, -1.0, 1.0)
    
    drivers.append({"id": "TRIGGER_ASYMMETRY", "weight": 0.25, "value": asym_val, "note": "Proximity to Breakout vs Breakdown"})
    total_score += (asym_val * 0.25)

    # 3. HTF TREND (Simple Moving Average Slope)
    # Weight: 0.25
    trend_val = 0.0
    if len(raw_15m) > 200:
        closes = [c['close'] for c in raw_15m]
        sma_50 = _calculate_sma(closes, 50) # Short term trend
        sma_200 = _calculate_sma(closes, 200) # Med term trend
        
        if px > sma_50 and sma_50 > sma_200: trend_val = 1.0
        elif px < sma_50 and sma_50 < sma_200: trend_val = -1.0
        elif px > sma_50: trend_val = 0.5
        elif px < sma_50: trend_val = -0.5
        
    drivers.append({"id": "HTF_TREND", "weight": 0.25, "value": trend_val, "note": "Price vs 50/200 SMA Alignment"})
    total_score += (trend_val * 0.25)

    # 4. RANGE POSITION (Where are we in the Daily Box?)
    # Weight: 0.2
    range_val = 0.0
    daily_rng = daily_res - daily_sup
    if daily_rng > 0:
        mid = (daily_res + daily_sup) / 2
        # Normalized position (-1 at support, +1 at resistance)
        range_pos = (px - mid) / (daily_rng / 2)
        range_val = _clamp(range_pos, -1.0, 1.0)
        
    drivers.append({"id": "RANGE_POSITION", "weight": 0.2, "value": range_val, "note": "Position within Daily S/R"})
    total_score += (range_val * 0.2)

    # --- FINAL SCORE ---
    lean_dir = "NEUTRAL"
    if total_score > 0.15: lean_dir = "BULLISH"
    elif total_score < -0.15: lean_dir = "BEARISH"
    
    confidence = min(0.95, abs(total_score)) * 100

    return {
        "direction": lean_dir,
        "confidence": round(confidence, 1),
        "score": round(total_score, 2),
        "drivers": drivers,
        "summary": f"Lean {lean_dir} ({round(confidence)}% Conf). Score: {round(total_score, 2)}"
    }

# ---------------------------------------------------------
# 6. MAIN COMPUTE
# ---------------------------------------------------------
def compute_sse_levels(inputs: Dict[str, Any]) -> Dict[str, Any]:
    def f(k, d=0.0): return float(inputs.get(k) or d)
    
    raw_15m = inputs.get("raw_15m_candles", [])
    slice_24h = inputs.get("slice_24h", [])
    
    # 1. Daily Levels (Using existing Logic)
    # Map raw 4H inputs to lists for helper
    res_list, sup_list = _build_htf_shelves(f("h4_supply"), f("h4_demand"), f("h1_supply"), f("h1_demand"))
    ds, dr, htf_out = _select_daily_levels(res_list, sup_list)
    
    # Fallback if 0
    if dr == 0 and raw_15m: dr = max(c['high'] for c in raw_15m[-96:])
    if ds == 0 and raw_15m: ds = min(c['low'] for c in raw_15m[-96:])

    # 2. VRVP (24H Context)
    vrvp_24h = _calculate_vrvp(slice_24h)
    
    # 3. Triggers
    px = f("last_price")
    r30_h = f("r30_high")
    r30_l = f("r30_low")
    
    bo, bd = _pick_trigger_candidates(px, r30_h, r30_l, vrvp_24h.get("vah", 0), vrvp_24h.get("val", 0), ds, dr)
    
    # 4. BIAS MODEL (The Upgrade)
    bias = _calculate_bias_model(px, dr, ds, bo, bd, vrvp_24h.get("poc", 0), raw_15m)

    # 5. EXECUTION PERMISSION (Gatekeeper)
    # Simple logic: Hold Fire unless breaking triggers
    perm_state = "HOLD_FIRE"
    if px > bo: perm_state = "DIRECTIONAL_LONG"
    elif px < bd: perm_state = "DIRECTIONAL_SHORT"
    elif abs(bias["score"]) < 0.2: perm_state = "ROTATIONAL_PERMITTED"

    return {
        "levels": {
            "daily_support": ds, "daily_resistance": dr,
            "breakout_trigger": bo, "breakdown_trigger": bd,
            "range30m_high": r30_h, "range30m_low": r30_l,
            "f24_poc": vrvp_24h["poc"], "f24_vah": vrvp_24h["vah"], "f24_val": vrvp_24h["val"]
        },
        "bias_model": {
            "daily_lean": bias,
            "permission_state": {
                "state": perm_state,
                "active_side": "long" if perm_state == "DIRECTIONAL_LONG" else ("short" if perm_state == "DIRECTIONAL_SHORT" else "none")
            }
        },
        "htf_shelves": htf_out,
        "intraday_shelves": {}
    }