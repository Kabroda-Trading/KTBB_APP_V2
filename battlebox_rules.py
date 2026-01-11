# battlebox_rules.py
# ==============================================================================
# BATTLEBOX RULE LAYER (PURE LOGIC)
# ==============================================================================
# Helper functions for Stochastics, Pullbacks, and GO Signals.
# Does NOT fetch data. Does NOT modify engines.
# ==============================================================================

from __future__ import annotations
from typing import Dict, List, Any, Optional

# --- CONFIG (SINGLE SOURCE OF TRUTH) ---
STOCH_K = 14
STOCH_D = 3
STOCH_SMOOTH = 3

OB = 80.0
OS = 20.0

ZONE_TOL_PCT = 0.0010      # 0.10% zone touch tolerance
PUSH_MIN_PCT = 0.0008      # 0.08% push-away confirmation
MAX_GO_BARS_5M = 48        # 4 hours after acceptance

# --- HELPERS ---
def _sma(vals: List[float], n: int) -> float:
    if len(vals) < n:
        return sum(vals) / max(len(vals), 1)
    return sum(vals[-n:]) / n

def compute_stoch(candles: List[Dict[str, Any]], k: int = STOCH_K, d: int = STOCH_D, smooth: int = STOCH_SMOOTH) -> Dict[str, Optional[float]]:
    """Returns last stoch values: k_raw, k_smooth, d"""
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
        if denom <= 0:
            k_val = 50.0
        else:
            k_val = ((closes[i] - ll) / denom) * 100.0
        k_series.append(k_val)

    # Smooth %K
    k_smooth_series: List[float] = []
    for i in range(len(k_series)):
        k_smooth_series.append(_sma(k_series[: i + 1], smooth))

    # %D = SMA of smoothed K
    d_series: List[float] = []
    for i in range(len(k_smooth_series)):
        d_series.append(_sma(k_smooth_series[: i + 1], d))

    return {
        "k_raw": float(k_series[-1]),
        "k_smooth": float(k_smooth_series[-1]),
        "d": float(d_series[-1]),
    }

def stoch_aligned(side: str, st: Dict[str, Optional[float]], ob: float = OB, os: float = OS) -> bool:
    """Checks if Stoch is in the correct zone for the trend side."""
    k = st.get("k_smooth")
    d = st.get("d")
    if k is None or d is None:
        return False
    if side == "SHORT":
        return k >= ob or d >= ob  # Overbought for Short
    if side == "LONG":
        return k <= os or d <= os  # Oversold for Long
    return False

def detect_pullback_go(
    side: str,
    levels: Dict[str, float],
    post_accept_5m: List[Dict[str, Any]],
    stoch_15m_at_accept: Dict[str, Optional[float]],
    use_zone: str = "TRIGGER",  # "TRIGGER" (BO/BD) or "VALUE" (VAH/VAL)
) -> Dict[str, Any]:
    """
    Finds the FIRST GO event after acceptance based on:
      1) Touch zone (Pullback)
      2) Stoch alignment (5m)
      3) Push away close
    """
    if side not in ("LONG", "SHORT") or not post_accept_5m:
        return {"ok": False, "go_type": "NONE", "go_ts": None, "reason": "NO_DATA", "evidence": {}}

    bo = float(levels.get("breakout_trigger", 0.0))
    bd = float(levels.get("breakdown_trigger", 0.0))
    vah = float(levels.get("f24_vah", 0.0))
    val = float(levels.get("f24_val", 0.0))

    if use_zone == "VALUE":
        zone = val if side == "LONG" else vah
    else:
        zone = bo if side == "LONG" else bd

    if zone <= 0:
        return {"ok": False, "go_type": "NONE", "go_ts": None, "reason": "BAD_ZONE", "evidence": {"zone": zone}}

    allow_campaign = stoch_aligned(side, stoch_15m_at_accept)
    allow_scalp = True

    touched = False
    touch_ts = None

    for i in range(min(len(post_accept_5m), MAX_GO_BARS_5M)):
        window = post_accept_5m[: i + 1]
        c = window[-1]
        px = float(c["close"])
        lo = float(c["low"])
        hi = float(c["high"])

        # 1) Touch zone
        tol = zone * ZONE_TOL_PCT
        in_touch = (lo <= zone + tol and hi >= zone - tol) if side == "LONG" else (hi >= zone - tol and lo <= zone + tol)
        
        if side == "LONG":
             if lo <= zone + tol: in_touch = True
        else:
             if hi >= zone - tol: in_touch = True

        if in_touch and not touched:
            touched = True
            touch_ts = int(c["time"])

        if not touched:
            continue

        # 2) Stoch alignment on 5m
        st5 = compute_stoch(window)
        st5_ok = stoch_aligned(side, st5)

        if not st5_ok:
            continue

        # 3) Push away confirmation (close away from zone)
        if side == "LONG":
            push_ok = px >= zone * (1.0 + PUSH_MIN_PCT)
        else:
            push_ok = px <= zone * (1.0 - PUSH_MIN_PCT)

        if not push_ok:
            continue

        # Classify GO type
        go_type = "CAMPAIGN_GO" if allow_campaign else ("SCALP_GO" if allow_scalp else "NONE")
        if go_type == "NONE":
            continue

        return {
            "ok": True,
            "go_type": go_type,
            "go_ts": int(c["time"]),
            "reason": "TOUCH+STOCH+PUSH",
            "evidence": {
                "zone_type": use_zone,
                "zone": zone,
                "touch_ts": touch_ts,
                "stoch_15m_at_accept": stoch_15m_at_accept,
                "stoch_5m_at_go": st5,
                "close_at_go": px,
            },
        }

    return {"ok": False, "go_type": "NONE", "go_ts": None, "reason": "NO_GO_FOUND", "evidence": {"zone": zone}}