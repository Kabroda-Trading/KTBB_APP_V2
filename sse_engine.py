# sse_engine.py
# ==============================================================================
# STRATEGIC STRUCTURAL ENGINE (SSE) v2.1 - TUNABLE DIAGNOSTIC
# ==============================================================================
# Contract:
# 1) Native input timeframe is 5m.
# 2) 15m/1h/4h are derived internally via resampling.
# 3) Triggers are calculated from 30m anchor range + 24h VRVP edges + pivot shelves.
# 4) Now supports "Tuning" overrides for Research Lab optimization.
# ==============================================================================

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple
import math

# ---------------------------------------------------------
# 1) HELPERS & MATH
# ---------------------------------------------------------
def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def _calculate_sma(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return 0.0
    return sum(prices[-period:]) / period

def _calculate_atr(candles: List[Dict[str, Any]], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    tr_sum = 0.0
    for i in range(1, period + 1):
        c = candles[-i]
        p = candles[-i - 1]
        hl = c["high"] - c["low"]
        hc = abs(c["high"] - p["close"])
        lc = abs(c["low"] - p["close"])
        tr_sum += max(hl, hc, lc)
    return tr_sum / period

def _resample(candles: List[Dict[str, Any]], minutes: int) -> List[Dict[str, Any]]:
    """
    Generic time-bucket resampler. Assumes candles have unix 'time' seconds and OHLCV fields.
    """
    if not candles:
        return []
    resampled: List[Dict[str, Any]] = []
    block_sec = minutes * 60

    curr_block: Dict[str, Any] | None = None
    curr_start = None

    for c in sorted(candles, key=lambda x: x["time"]):
        ts = int(c["time"])
        start_of_block = ts - (ts % block_sec)

        if curr_start != start_of_block:
            if curr_block is not None:
                resampled.append(curr_block)
            curr_start = start_of_block
            curr_block = {
                "time": start_of_block,
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c.get("volume", 0.0)),
            }
        else:
            # same block
            curr_block["high"] = max(curr_block["high"], float(c["high"]))
            curr_block["low"] = min(curr_block["low"], float(c["low"]))
            curr_block["close"] = float(c["close"])
            curr_block["volume"] += float(c.get("volume", 0.0))

    if curr_block is not None:
        resampled.append(curr_block)

    return resampled

def _infer_spacing_seconds(candles: List[Dict[str, Any]]) -> int:
    """Best-effort spacing inference for meta/debug."""
    if not candles or len(candles) < 2:
        return 0
    times = sorted(int(c["time"]) for c in candles[-10:])
    diffs = [times[i] - times[i - 1] for i in range(1, len(times)) if times[i] > times[i - 1]]
    if not diffs:
        return 0
    return int(sorted(diffs)[len(diffs) // 2])  # median

@dataclass(frozen=True)
class Shelf:
    tf: str
    level: float
    kind: str
    strength: float

# ---------------------------------------------------------
# 2) VRVP ENGINE
# ---------------------------------------------------------
def _calculate_vrvp(candles: List[Dict[str, Any]], row_size_pct: float = 0.001) -> Dict[str, float]:
    if not candles:
        return {"poc": 0.0, "vah": 0.0, "val": 0.0}

    min_p = min(float(c["low"]) for c in candles)
    max_p = max(float(c["high"]) for c in candles)
    if min_p == max_p:
        return {"poc": min_p, "vah": min_p, "val": min_p}

    row_size = max(min_p * row_size_pct, 1.0)
    num_bins = int((max_p - min_p) / row_size) + 1
    volume_profile = [0.0] * num_bins
    total_volume = 0.0

    for c in candles:
        typical = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
        vol = float(c.get("volume", 0.0))
        bin_idx = int((typical - min_p) / row_size)
        if 0 <= bin_idx < num_bins:
            volume_profile[bin_idx] += vol
            total_volume += vol

    max_vol_idx = max(range(num_bins), key=lambda i: volume_profile[i])
    poc = min_p + (max_vol_idx * row_size)

    # 70% value area
    target = total_volume * 0.70
    curr = volume_profile[max_vol_idx]
    up = down = max_vol_idx

    while curr < target:
        v_up = volume_profile[up + 1] if up < num_bins - 1 else 0.0
        v_dn = volume_profile[down - 1] if down > 0 else 0.0
        if v_up == 0 and v_dn == 0:
            break
        if v_up >= v_dn:
            curr += v_up
            up += 1
        else:
            curr += v_dn
            down -= 1

    return {"poc": float(poc), "vah": float(min_p + (up * row_size)), "val": float(min_p + (down * row_size))}

# ---------------------------------------------------------
# 3) PIVOT ENGINE
# ---------------------------------------------------------
def _find_pivots(candles: List[Dict[str, Any]], left: int = 3, right: int = 3) -> Tuple[float, float]:
    if len(candles) < (left + right + 1):
        return 0.0, 0.0

    last_sup = 0.0
    last_dem = 0.0

    for i in range(left, len(candles) - right):
        curr = candles[i]
        ch = float(curr["high"])
        cl = float(curr["low"])

        if all(float(candles[i - j]["high"]) <= ch for j in range(1, left + 1)) and \
           all(float(candles[i + j]["high"]) < ch for j in range(1, right + 1)):
            last_sup = ch

        if all(float(candles[i - j]["low"]) >= cl for j in range(1, left + 1)) and \
           all(float(candles[i + j]["low"]) > cl for j in range(1, right + 1)):
            last_dem = cl

    return float(last_sup), float(last_dem)

def _select_daily_levels(resistance: List[Shelf], support: List[Shelf]) -> Tuple[float, float, Dict[str, Any]]:
    if not resistance or not support:
        return 0.0, 0.0, {"resistance": [], "support": []}

    dr = max(resistance, key=lambda s: s.strength).level
    ds = max(support, key=lambda s: s.strength).level

    return float(ds), float(dr), {
        "resistance": [{"level": float(s.level), "tf": s.tf, "strength": float(s.strength)} for s in resistance],
        "support": [{"level": float(s.level), "tf": s.tf, "strength": float(s.strength)} for s in support],
    }

# ---------------------------------------------------------
# 4) CONTEXT & TRIGGER LOGIC
# ---------------------------------------------------------
def _pick_trigger_candidates(
    anchor_px: float, 
    r30_h: float, 
    r30_l: float, 
    vrvp_24h: Dict[str, float], 
    daily_sup: float, 
    daily_res: float,
    tuning: Dict[str, Any] = None  # <--- NEW ARGUMENT
) -> Tuple[float, float]:
    
    # Base: prioritize R30 extremes, then value edge, then daily pivot
    bo_base = max(r30_h, float(vrvp_24h.get("vah", 0.0))) if r30_h > 0 else float(daily_res)
    bd_base = min(r30_l, float(vrvp_24h.get("val", 0.0))) if r30_l > 0 else float(daily_sup)

    # Tuning Logic (Default to 20 bps / 0.2% if no tuning provided)
    tuning = tuning or {}
    bps = tuning.get("min_trigger_dist_bps", 20)
    min_dist = anchor_px * (bps / 10000.0)

    # Safety: ensure triggers are not too close to anchor
    bo = max(float(bo_base), anchor_px + min_dist)
    bd = min(float(bd_base), anchor_px - min_dist)
    return float(bo), float(bd)

def _build_context(
    anchor_px: float,
    live_px: float,
    bo: float,
    bd: float,
    f24_poc: float,
    f24_vah: float,
    f24_val: float,
    candles_15m: List[Dict[str, Any]],
    daily_candles: List[Dict[str, Any]],
) -> Dict[str, Any]:
    slope_score = 0.0

    # Daily trend
    trend_1d = "range"
    if len(daily_candles) > 50:
        d_closes = [float(c["close"]) for c in daily_candles]
        sma20 = _calculate_sma(d_closes, 20)
        sma50 = _calculate_sma(d_closes, 50)
        if sma20 > sma50:
            trend_1d = "up"
            slope_score += 0.5
        elif sma20 < sma50:
            trend_1d = "down"
            slope_score -= 0.5

    # 4H trend proxy using 15m closes (derived from 5m)
    trend_4h = "range"
    if len(candles_15m) > 200:
        closes = [float(c["close"]) for c in candles_15m]
        sma50 = _calculate_sma(closes, 50)
        sma200 = _calculate_sma(closes, 200)
        if sma50 > sma200:
            trend_4h = "up"
            slope_score += 0.5
        elif sma50 < sma200:
            trend_4h = "down"
            slope_score -= 0.5

    slope_score = _clamp(slope_score, -1.0, 1.0)

    opening_loc = "in_value"
    if anchor_px > f24_vah:
        opening_loc = "above_value"
    elif anchor_px < f24_val:
        opening_loc = "below_value"

    # Band status using LIVE price (context only, NOT permission)
    band_status = "inside_band"
    if live_px > bo:
        band_status = "above_breakout"
    elif live_px < bd:
        band_status = "below_breakdown"

    atr = _calculate_atr(candles_15m)
    comp_score = 0.0
    if atr > 0:
        curr_rng = max(bo - bd, 1.0)
        if curr_rng < atr * 0.5:
            comp_score = 0.8
        elif curr_rng < atr:
            comp_score = 0.5

    return {
        "htf": {"trend_1d": trend_1d, "trend_4h": trend_4h, "slope_score": float(slope_score)},
        "location": {"opening_location": opening_loc, "vs_f24_poc": float(anchor_px - f24_poc)},
        "volatility": {"atr_14": float(atr), "compression_score": float(comp_score)},
        "band_status": band_status,  # context only
    }

def _calculate_bias_model(ctx: Dict[str, Any], anchor_px: float, bo: float, bd: float) -> Dict[str, Any]:
    score = 0.0

    # Trend driver
    score += float(ctx["htf"]["slope_score"]) * 0.40

    # Location driver
    loc = ctx["location"]["opening_location"]
    loc_val = 1.0 if loc == "above_value" else (-1.0 if loc == "below_value" else 0.0)
    score += loc_val * 0.30

    # Asymmetry driver
    d_bo = abs(bo - anchor_px)
    d_bd = abs(anchor_px - bd)
    asym_val = _clamp((d_bd - d_bo) / max(d_bo, d_bd, 1.0), -1.0, 1.0)
    score += asym_val * 0.30

    direction = "long" if score > 0.15 else ("short" if score < -0.15 else "neutral")
    conf = min(0.90, abs(score)) * (1.0 - (float(ctx["volatility"]["compression_score"]) * 0.5)) * 100.0

    # IMPORTANT: SSE does not "earn permission"
    permission_state = {
        "state": "HOLD_FIRE",
        "active_side": "none",
        "earned_by": [],
        "requirements_remaining": ["15m_acceptance", "5m_alignment"],
        "evidence": {},
    }

    return {
        "daily_lean": {"direction": direction, "score": round(score, 2), "confidence": round(conf, 1)},
        "permission_state": permission_state,
    }

# ---------------------------------------------------------
# 5) MAIN COMPUTE (API ENTRY)
# ---------------------------------------------------------
def compute_sse_levels(inputs: Dict[str, Any]) -> Dict[str, Any]:
    def _f(key: str, default: float = 0.0) -> float:
        v = inputs.get(key, default)
        try:
            return float(v)
        except Exception:
            return float(default)

    # Required
    anchor_px = _f("session_open_price", 0.0)
    if anchor_px <= 0:
        return {"error": "SSE: session_open_price missing or invalid", "meta": {"ok": False}}

    live_px = _f("last_price", anchor_px)
    r30_h = _f("r30_high", 0.0)
    r30_l = _f("r30_low", 0.0)

    # 5m-native path
    raw_5m = inputs.get("locked_history_5m") or inputs.get("raw_5m_candles")
    is_5m_native = bool(raw_5m)

    meta: Dict[str, Any] = {
        "ok": True,
        "contract": "5m_native",
        "source": "5m_native" if is_5m_native else "legacy_15m",
        "input_spacing_sec": _infer_spacing_seconds(raw_5m) if is_5m_native else _infer_spacing_seconds(inputs.get("raw_15m_candles", [])),
    }

    daily_candles = inputs.get("raw_daily_candles", []) or []

    if is_5m_native:
        # Slices
        slice_24h_5m = inputs.get("slice_24h_5m") or raw_5m[-288:]
        # Derive 15m/1h/4h from locked history
        locked_15m = _resample(raw_5m, 15)
        locked_1h = _resample(raw_5m, 60)
        locked_4h = _resample(raw_5m, 240)

        # VRVP context: use 15m derived from 24h 5m slice
        context_24h_15m = _resample(slice_24h_5m, 15)

        meta.update({
            "slice_24h_5m_count": len(slice_24h_5m),
            "locked_5m_count": len(raw_5m),
            "locked_15m_count": len(locked_15m),
        })
    else:
        # Legacy bridge
        locked_15m = inputs.get("raw_15m_candles", []) or []
        context_24h_15m = inputs.get("slice_24h", locked_15m[-96:]) or []
        locked_1h = _resample(locked_15m, 60)
        locked_4h = _resample(locked_15m, 240)

        meta.update({
            "legacy_15m_count": len(locked_15m),
            "legacy_context_24h_15m_count": len(context_24h_15m),
            "warning": "legacy_15m_mode_degraded; migrate callers to 5m-native inputs",
        })

    # 1) Pivots & shelves
    sup_4h, dem_4h = _find_pivots(locked_4h)
    sup_1h, dem_1h = _find_pivots(locked_1h)

    res_list: List[Shelf] = []
    sup_list: List[Shelf] = []
    if sup_4h > 0:
        res_list.append(Shelf("4H", sup_4h, "supply", 0.8))
    if sup_1h > 0:
        res_list.append(Shelf("1H", sup_1h, "supply", 0.6))
    if dem_4h > 0:
        sup_list.append(Shelf("4H", dem_4h, "demand", 0.8))
    if dem_1h > 0:
        sup_list.append(Shelf("1H", dem_1h, "demand", 0.6))

    ds, dr, htf_out = _select_daily_levels(res_list, sup_list)

    if dr == 0.0 and locked_15m:
        dr = max(float(c["high"]) for c in locked_15m[-96:])
    if ds == 0.0 and locked_15m:
        ds = min(float(c["low"]) for c in locked_15m[-96:])

    # 2) VRVP (24h)
    vrvp_24h = _calculate_vrvp(context_24h_15m)

    # 3) Triggers (anchor-based)
    # NEW: Extract tuning from inputs to pass down
    tuning_cfg = inputs.get("tuning", {}) 
    
    bo, bd = _pick_trigger_candidates(
        anchor_px, r30_h, r30_l, vrvp_24h, ds, dr, 
        tuning=tuning_cfg # <--- PASS IT HERE
    )

    # 4) Context & bias
    ctx = _build_context(
        anchor_px=anchor_px,
        live_px=live_px,
        bo=bo,
        bd=bd,
        f24_poc=vrvp_24h["poc"],
        f24_vah=vrvp_24h["vah"],
        f24_val=vrvp_24h["val"],
        candles_15m=locked_15m,
        daily_candles=daily_candles,
    )
    bias = _calculate_bias_model(ctx, anchor_px, bo, bd)

    return {
        "meta": meta,
        "levels": {
            "daily_support": float(ds),
            "daily_resistance": float(dr),
            "breakout_trigger": float(bo),
            "breakdown_trigger": float(bd),
            "range30m_high": float(r30_h),
            "range30m_low": float(r30_l),
            "f24_poc": float(vrvp_24h["poc"]),
            "f24_vah": float(vrvp_24h["vah"]),
            "f24_val": float(vrvp_24h["val"]),
        },
        "bias_model": bias,
        "context": ctx,
        "htf_shelves": htf_out,
    }