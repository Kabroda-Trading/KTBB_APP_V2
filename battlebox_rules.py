# battlebox_rules.py
# ==============================================================================
# BATTLEBOX RULE LAYER v2.0 (MULTI-MODEL)
# ==============================================================================
# 1. Stochastics (Momentum)
# 2. RSI Divergence (Reversal/Continuation)
# 3. Volume Pressure (Breakout Validation)
# ==============================================================================

from __future__ import annotations
from typing import Dict, List, Any, Optional

# --- CONFIG (SINGLE SOURCE OF TRUTH) ---
STOCH_K = 14
STOCH_D = 3
STOCH_SMOOTH = 3

RSI_PERIOD = 14
DIV_LOOKBACK = 10  # Look back X candles for divergence pivot

OB = 80.0
OS = 20.0
RSI_OB = 70.0
RSI_OS = 30.0

ZONE_TOL_PCT = 0.0010      # 0.10% zone touch tolerance
PUSH_MIN_PCT = 0.0008      # 0.08% push-away confirmation
MAX_GO_BARS_5M = 48        # 4 hours after acceptance

# --- HELPERS ---
def _sma(vals: List[float], n: int) -> float:
    if len(vals) < n:
        return sum(vals) / max(len(vals), 1)
    return sum(vals[-n:]) / n

def compute_stoch(candles: List[Dict[str, Any]], k: int = STOCH_K, d: int = STOCH_D, smooth: int = STOCH_SMOOTH) -> Dict[str, Optional[float]]:
    if not candles or len(candles) < k:
        return {"k_raw": None, "k_smooth": None, "d": None}

    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]

    k_series: List[float] = []
    for i in range(k - 1, len(candles)):
        hh = max(highs[i - k + 1 : i + 1])
        ll = min(lows[i - k + 1 : i + 1])
        denom = (hh - ll)
        k_val = 50.0 if denom <= 0 else ((closes[i] - ll) / denom) * 100.0
        k_series.append(k_val)

    k_smooth_series = []
    for i in range(len(k_series)):
        k_smooth_series.append(_sma(k_series[: i + 1], smooth))

    d_series = []
    for i in range(len(k_smooth_series)):
        d_series.append(_sma(k_smooth_series[: i + 1], d))

    return {"k_raw": float(k_series[-1]), "k_smooth": float(k_smooth_series[-1]), "d": float(d_series[-1])}

def stoch_aligned(side: str, st: Dict[str, Optional[float]], ob: float = OB, os: float = OS) -> bool:
    k = st.get("k_smooth")
    d = st.get("d")
    if k is None or d is None: return False
    if side == "SHORT": return k >= ob or d >= ob
    if side == "LONG": return k <= os or d <= os
    return False

# --- RSI & DIVERGENCE ENGINE ---
def compute_rsi(candles: List[Dict[str, Any]], period: int = RSI_PERIOD) -> float:
    if not candles or len(candles) < period + 1: return 50.0
    closes = [float(c["close"]) for c in candles]
    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i-1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def check_divergence(side: str, candles: List[Dict[str, Any]]) -> bool:
    if len(candles) < DIV_LOOKBACK + 5: return False
    current = candles[-1]
    prev_window = candles[-(DIV_LOOKBACK+1):-1]
    curr_rsi = compute_rsi(candles)
    
    if side == "LONG":
        curr_price = float(current["low"])
        lowest_prev = min(prev_window, key=lambda x: float(x["low"]))
        prev_price = float(lowest_prev["low"])
        if curr_price < prev_price and (curr_rsi > RSI_OS and curr_rsi < 50):
            return True
    elif side == "SHORT":
        curr_price = float(current["high"])
        highest_prev = max(prev_window, key=lambda x: float(x["high"]))
        prev_price = float(highest_prev["high"])
        if curr_price > prev_price and (curr_rsi < RSI_OB and curr_rsi > 50):
            return True
    return False

# --- VOLUME PRESSURE ENGINE ---
def check_volume_pressure(candles: List[Dict[str, Any]], multiplier: float = 1.5) -> bool:
    if len(candles) < 21: return False
    current_vol = float(candles[-1].get("volume", 0))
    history = candles[-21:-1]
    avg_vol = sum(float(c.get("volume", 0)) for c in history) / len(history)
    if avg_vol == 0: return True
    return current_vol > (avg_vol * multiplier)

# --- MASTER SIGNAL DETECTOR ---
def detect_pullback_go(
    side: str,
    levels: Dict[str, float],
    post_accept_5m: List[Dict[str, Any]],
    stoch_15m_at_accept: Dict[str, Optional[float]],
    use_zone: str = "TRIGGER",
    require_volume: bool = False, # <--- THIS ARGUMENT WAS MISSING
    require_divergence: bool = False # <--- THIS ARGUMENT WAS MISSING
) -> Dict[str, Any]:
    
    if side not in ("LONG", "SHORT") or not post_accept_5m:
        return {"ok": False, "go_type": "NONE", "go_ts": None}

    bo = float(levels.get("breakout_trigger", 0.0))
    bd = float(levels.get("breakdown_trigger", 0.0))
    vah = float(levels.get("f24_vah", 0.0))
    val = float(levels.get("f24_val", 0.0))

    if use_zone == "VALUE":
        zone = val if side == "LONG" else vah
    else:
        zone = bo if side == "LONG" else bd

    if zone <= 0:
        return {"ok": False, "go_type": "NONE"}

    allow_campaign = stoch_aligned(side, stoch_15m_at_accept)
    allow_scalp = True
    touched = False

    for i in range(min(len(post_accept_5m), MAX_GO_BARS_5M)):
        window = post_accept_5m[: i + 1]
        c = window[-1]
        px = float(c["close"])
        lo = float(c["low"])
        hi = float(c["high"])

        # 1) Touch Zone
        tol = zone * ZONE_TOL_PCT
        in_touch = (lo <= zone + tol) if side == "LONG" else (hi >= zone - tol)
        if in_touch and not touched: touched = True
        if not touched: continue

        # 2) Stoch Alignment
        st5 = compute_stoch(window)
        if not stoch_aligned(side, st5): continue

        # 3) Push Confirmation
        push_ok = (px >= zone * (1.0 + PUSH_MIN_PCT)) if side == "LONG" else (px <= zone * (1.0 - PUSH_MIN_PCT))
        if not push_ok: continue

        # 4) OPTIONAL FILTERS
        if require_volume and not check_volume_pressure(window): continue
        if require_divergence and not check_divergence(side, window): continue

        go_type = "CAMPAIGN_GO" if allow_campaign else ("SCALP_GO" if allow_scalp else "NONE")
        if go_type == "NONE": continue

        return {
            "ok": True, 
            "go_type": go_type, 
            "go_ts": int(c["time"]), 
            "reason": "TOUCH+STOCH+PUSH" + ("+VOL" if require_volume else "") + ("+DIV" if require_divergence else "")
        }

    return {"ok": False, "go_type": "NONE", "go_ts": None}