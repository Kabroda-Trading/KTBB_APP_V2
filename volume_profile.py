# volume_profile.py — simple deterministic volume profile from candles
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Optional


@dataclass(frozen=True)
class VP:
    vah: float
    val: float
    poc: float


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def compute_volume_profile_from_candles(
    candles: List[Any],
    *,
    n_bins: int = 200,
    value_area_pct: float = 0.70,
) -> Optional[VP]:
    """
    candles: objects with .h .l .c .v
    Uses typical price per candle and allocates full candle volume to that bin (fast, deterministic).
    """
    if not candles:
        return None

    hi = max(_safe_float(getattr(c, "h", 0.0)) for c in candles)
    lo = min(_safe_float(getattr(c, "l", 0.0)) for c in candles)
    if hi <= 0 or lo <= 0 or hi <= lo:
        return None

    bins = [0.0 for _ in range(n_bins)]
    rng = hi - lo
    if rng <= 0:
        return None

    def to_bin(price: float) -> int:
        p = max(lo, min(hi, price))
        idx = int((p - lo) / rng * (n_bins - 1))
        return max(0, min(n_bins - 1, idx))

    total_vol = 0.0
    for c in candles:
        h = _safe_float(getattr(c, "h", 0.0))
        l = _safe_float(getattr(c, "l", 0.0))
        cl = _safe_float(getattr(c, "c", 0.0))
        v = _safe_float(getattr(c, "v", 0.0))
        if v <= 0:
            continue
        tp = (h + l + cl) / 3.0
        bins[to_bin(tp)] += v
        total_vol += v

    if total_vol <= 0:
        return None

    poc_idx = max(range(n_bins), key=lambda i: bins[i])
    poc_price = lo + (poc_idx + 0.5) / n_bins * rng

    # Build value area around highest-volume bins (classic approach)
    ranked = sorted(range(n_bins), key=lambda i: bins[i], reverse=True)
    keep = set()
    acc = 0.0
    target = total_vol * value_area_pct
    for i in ranked:
        if bins[i] <= 0:
            continue
        keep.add(i)
        acc += bins[i]
        if acc >= target:
            break

    if not keep:
        return None

    val_idx = min(keep)
    vah_idx = max(keep)

    val_price = lo + (val_idx + 0.5) / n_bins * rng
    vah_price = lo + (vah_idx + 0.5) / n_bins * rng

    return VP(vah=float(vah_price), val=float(val_price), poc=float(poc_price))
