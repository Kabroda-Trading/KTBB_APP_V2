# sse_engine.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Shelf:
    tf: str
    level: float
    kind: str  # "supply" or "demand"
    strength: float
    primary: bool = False


def _pct(x: float, p: float) -> float:
    return x * p


def _spacing_ok(a: float, b: float, min_pct: float) -> bool:
    if a <= 0 or b <= 0:
        return True
    return abs(a - b) >= min(a, b) * min_pct


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _build_htf_shelves(
    h4_supply: float,
    h4_demand: float,
    h1_supply: float,
    h1_demand: float,
) -> Tuple[List[Shelf], List[Shelf]]:
    resistance: List[Shelf] = []
    support: List[Shelf] = []

    if h4_supply and h4_supply > 0:
        resistance.append(Shelf(tf="4H", level=float(h4_supply), kind="supply", strength=8.0, primary=True))
    if h1_supply and h1_supply > 0:
        resistance.append(Shelf(tf="1H", level=float(h1_supply), kind="supply", strength=6.0, primary=False))

    if h4_demand and h4_demand > 0:
        support.append(Shelf(tf="4H", level=float(h4_demand), kind="demand", strength=8.0, primary=True))
    if h1_demand and h1_demand > 0:
        support.append(Shelf(tf="1H", level=float(h1_demand), kind="demand", strength=6.0, primary=False))

    resistance = sorted(resistance, key=lambda s: s.level)
    support = sorted(support, key=lambda s: s.level)
    return resistance, support


def _select_daily_levels(resistance: List[Shelf], support: List[Shelf]) -> Tuple[float, float, Dict[str, Any]]:
    if not resistance or not support:
        return 0.0, 0.0, {"resistance": [], "support": []}

    ds = max(support, key=lambda s: ((1 if s.tf == "4H" else 0), s.strength)).level
    dr = max(resistance, key=lambda s: ((1 if s.tf == "4H" else 0), s.strength)).level

    daily_support = float(min(ds, dr))
    daily_resistance = float(max(ds, dr))

    htf_out = {
        "resistance": [
            {"tf": s.tf, "level": float(s.level), "strength": float(s.strength), "primary": (s.level == dr)}
            for s in resistance
        ],
        "support": [
            {"tf": s.tf, "level": float(s.level), "strength": float(s.strength), "primary": (s.level == ds)}
            for s in support
        ],
    }
    return daily_support, daily_resistance, htf_out


def _pick_trigger_candidates(
    *,
    px: float,  # This is the ANCHOR price (Session Open), not live price
    daily_support: float,
    daily_resistance: float,
    r30_high: float,
    r30_low: float,
    f24_vah: float,
    f24_val: float,
) -> Tuple[float, float, Dict[str, Any]]:
    """
    DETERMINISTIC ANCHOR LOGIC:
    Calculates triggers based on the ANCHOR price (px).
    Triggers are set at Open and do NOT move with live price.
    """
    # Min distance from ANCHOR (0.5%)
    min_from_px = 0.005
    min_level_spacing = 0.0015

    bo_base = max([v for v in [r30_high, f24_vah] if v and v > 0] or [0.0])
    bd_base = min([v for v in [r30_low, f24_val] if v and v > 0] or [0.0])

    if bo_base <= 0:
        bo_base = daily_support + 0.7 * (daily_resistance - daily_support)
    if bd_base <= 0:
        bd_base = daily_support + 0.3 * (daily_resistance - daily_support)

    # Enforce "away from ANCHOR" (Not current price)
    # This locks the trigger relative to the Session Open.
    bo = max(bo_base, px * (1 + min_from_px))
    bd = min(bd_base, px * (1 - min_from_px))

    inner_lo = daily_support + _pct(daily_support, min_level_spacing)
    inner_hi = daily_resistance - _pct(daily_resistance, min_level_spacing)
    bo = _clamp(bo, inner_lo, inner_hi)
    bd = _clamp(bd, inner_lo, inner_hi)

    if not (daily_support < bd < bo < daily_resistance):
        # deterministic re-center inside band around ANCHOR
        mid = _clamp(px, inner_lo, inner_hi)
        span = max((daily_resistance - daily_support) * 0.12, px * 0.01)
        bd = _clamp(mid - span / 2, inner_lo, inner_hi)
        bo = _clamp(mid + span / 2, inner_lo, inner_hi)

        if bo < px * (1 + min_from_px):
            bo = _clamp(px * (1 + min_from_px), inner_lo, inner_hi)
        if bd > px * (1 - min_from_px):
            bd = _clamp(px * (1 - min_from_px), inner_lo, inner_hi)

        if not (daily_support < bd < bo < daily_resistance):
            bd = daily_support + 0.4 * (daily_resistance - daily_support)
            bo = daily_support + 0.6 * (daily_resistance - daily_support)

    def bump_up(x: float, ref: float) -> float:
        return max(x, ref + ref * min_level_spacing)

    def bump_down(x: float, ref: float) -> float:
        return min(x, ref - ref * min_level_spacing)

    if not _spacing_ok(bd, daily_support, min_level_spacing):
        bd = bump_up(bd, daily_support)
    if not _spacing_ok(bo, daily_resistance, min_level_spacing):
        bo = bump_down(bo, daily_resistance)
    if not _spacing_ok(bo, bd, min_level_spacing):
        bo = bump_up(bo, bd)

    intraday = {
        "breakout_side": [{"level": float(bo_base), "strength": 6.0}],
        "breakdown_side": [{"level": float(bd_base), "strength": 6.0}],
    }
    return float(bo), float(bd), intraday


def compute_sse_levels(inputs: Dict[str, Any]) -> Dict[str, Any]:
    def f(k: str, default: float = 0.0) -> float:
        try:
            v = inputs.get(k, default)
            return float(v) if v is not None else float(default)
        except Exception:
            return float(default)

    # 1. Try to get the Session Open Price (The Anchor)
    # 2. If missing, fall back to Last Price (px)
    px = f("last_price", 0.0)
    anchor_px = f("session_open_price", 0.0)
    
    # CRITICAL FIX: If we have an anchor, use it. If not, use last price.
    reference_price = anchor_px if anchor_px > 0 else px

    resistance, support = _build_htf_shelves(
        h4_supply=f("h4_supply"),
        h4_demand=f("h4_demand"),
        h1_supply=f("h1_supply"),
        h1_demand=f("h1_demand"),
    )

    daily_support, daily_resistance, htf_out = _select_daily_levels(resistance, support)

    if reference_price <= 0:
        # Fallback if literally zero data
        reference_price = (f("f24_poc") or f("weekly_poc") or (daily_support + daily_resistance) / 2.0)

    # Pass 'reference_price' (The Anchor) instead of live price
    bo, bd, intraday = _pick_trigger_candidates(
        px=reference_price, 
        daily_support=daily_support,
        daily_resistance=daily_resistance,
        r30_high=f("r30_high"),
        r30_low=f("r30_low"),
        f24_vah=f("f24_vah"),
        f24_val=f("f24_val"),
    )

    if not (daily_support < bd < bo < daily_resistance):
        band = max(daily_resistance - daily_support, 1.0)
        bd = daily_support + 0.45 * band
        bo = daily_support + 0.55 * band

    return {
        "levels": {
            "daily_support": float(daily_support),
            "daily_resistance": float(daily_resistance),
            "breakout_trigger": float(bo),
            "breakdown_trigger": float(bd),
            "range30m_high": float(f("r30_high")),
            "range30m_low": float(f("r30_low")),
        },
        "htf_shelves": htf_out,
        "intraday_shelves": intraday,
    }