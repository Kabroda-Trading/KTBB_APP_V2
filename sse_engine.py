# sse_engine.py

"""
Structural & level engine for KTBB – Execution Anchor v4.2 / SSE v2.0.

Takes:
  - 4H / 1H HTF shelves (supply / demand)
  - Weekly VRVP (VAL / POC / VAH)
  - 24h FRVP (VAL / POC / VAH)
  - Morning FRVP (VAL / POC / VAH)
  - 30m Opening Range (high / low)

Outputs:
  - daily_support
  - daily_resistance
  - breakout_trigger
  - breakdown_trigger
  - htf_resistance: [ { tf, level, strength, primary }, ... ]
  - htf_support:    [ { tf, level, strength, primary }, ... ]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Tuple


@dataclass
class Shelf:
    tf: str
    level: float
    kind: str  # "supply" or "demand"
    primary: bool = False
    strength: float = 0.0


def _htf_shelves_from_inputs(
    h4_supply: float,
    h4_demand: float,
    h1_supply: float,
    h1_demand: float,
) -> Tuple[List[Shelf], List[Shelf]]:
    """
    Build HTF shelf objects from the four raw HTF levels.
    """
    resistance: List[Shelf] = []
    support: List[Shelf] = []

    if h4_supply > 0:
        resistance.append(
            Shelf(tf="4H", level=h4_supply, kind="supply", primary=True, strength=8.0)
        )
    if h1_supply > 0:
        resistance.append(
            Shelf(tf="1H", level=h1_supply, kind="supply", primary=False, strength=6.0)
        )

    if h4_demand > 0:
        support.append(
            Shelf(tf="4H", level=h4_demand, kind="demand", primary=True, strength=8.0)
        )
    if h1_demand > 0:
        support.append(
            Shelf(tf="1H", level=h1_demand, kind="demand", primary=False, strength=6.0)
        )

    # sort by level ascending for support, descending for resistance
    support.sort(key=lambda s: s.level)
    resistance.sort(key=lambda s: s.level)

    return resistance, support


def _select_daily_band(
    resistance: List[Shelf],
    support: List[Shelf],
) -> Tuple[float, float, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Choose daily_support and daily_resistance from HTF shelves only.
    """
    if not resistance or not support:
        # Failsafe — no shelves: return flat band that will later be adjusted.
        return (
            0.0,
            0.0,
            [],
            [],
        )

    # Daily support = strongest demand (prefer 4H)
    sup_choice = max(
        support,
        key=lambda s: (1 if s.tf == "4H" else 0, s.strength),
    )
    # Daily resistance = strongest supply (prefer 4H)
    res_choice = max(
        resistance,
        key=lambda s: (1 if s.tf == "4H" else 0, s.strength),
    )

    daily_support = float(min(sup_choice.level, res_choice.level))
    daily_resistance = float(max(sup_choice.level, res_choice.level))

    # Mark primary shelves in the ladders
    htf_resistance = [
        {
            "tf": s.tf,
            "level": float(s.level),
            "strength": float(s.strength),
            "primary": (s.level == res_choice.level),
        }
        for s in resistance
    ]
    htf_support = [
        {
            "tf": s.tf,
            "level": float(s.level),
            "strength": float(s.strength),
            "primary": (s.level == sup_choice.level),
        }
        for s in support
    ]

    return daily_support, daily_resistance, htf_resistance, htf_support


def _select_triggers(
    daily_support: float,
    daily_resistance: float,
    f24_val: float,
    f24_poc: float,
    f24_vah: float,
    morn_val: float,
    morn_poc: float,
    morn_vah: float,
    r30_high: float,
    r30_low: float,
) -> Tuple[float, float]:
    """
    Pick breakdown_trigger / breakout_trigger between daily_support and
    daily_resistance, using FRVP and 30m OR as guide. This is a deterministic
    approximation of the full SSE spec.
    """

    if daily_support == 0 and daily_resistance == 0:
        return 0.0, 0.0

    band_width = max(1.0, daily_resistance - daily_support)
    min_spacing_pct = 0.002  # 0.2% gaps at minimum
    min_spacing = band_width * min_spacing_pct

    price_anchor = f24_poc or morn_poc or (r30_high + r30_low) / 2.0
    if price_anchor <= 0:
        price_anchor = (daily_support + daily_resistance) / 2.0

    # start with mid-band
    mid = (daily_support + daily_resistance) / 2.0

    # default positions: slightly above / below mid
    base_gap = max(band_width * 0.05, min_spacing * 2.0)
    tentative_breakdown = mid - base_gap
    tentative_breakout = mid + base_gap

    # bias triggers toward FRVP / OR edges, but keep them inside the band
    upper_edge = max(f24_vah, r30_high, morn_vah)
    lower_edge = min(f24_val, r30_low, morn_val)

    if lower_edge > 0:
        tentative_breakdown = max(lower_edge, daily_support + min_spacing)
    if upper_edge > 0:
        tentative_breakout = min(upper_edge, daily_resistance - min_spacing)

    # Enforce ordering + spacing
    breakdown = max(
        daily_support + min_spacing,
        min(tentative_breakdown, daily_resistance - 2.0 * min_spacing),
    )
    breakout = max(
        breakdown + min_spacing,
        min(tentative_breakout, daily_resistance - min_spacing),
    )

    return breakdown, breakout


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_dm_levels(
    h4_supply: float,
    h4_demand: float,
    h1_supply: float,
    h1_demand: float,
    weekly_val: float,
    weekly_poc: float,
    weekly_vah: float,
    f24_val: float,
    f24_poc: float,
    f24_vah: float,
    morn_val: float,
    morn_poc: float,
    morn_vah: float,
    r30_high: float,
    r30_low: float,
) -> Dict[str, Any]:
    """
    Main entry point used by FastAPI (see main.py).

    Inputs: HTF shelves, weekly VRVP, 24h FRVP, morning FRVP, 30m OR.

    Returns a dict with:
      - daily_support
      - daily_resistance
      - breakout_trigger
      - breakdown_trigger
      - htf_resistance: [ { tf, level, strength, primary }, ... ]
      - htf_support:    [ { tf, level, strength, primary }, ... ]
    """

    # 1) HTF shelves → daily band
    resistance, support = _htf_shelves_from_inputs(
        h4_supply=h4_supply,
        h4_demand=h4_demand,
        h1_supply=h1_supply,
        h1_demand=h1_demand,
    )
    daily_support, daily_resistance, htf_resistance, htf_support = _select_daily_band(
        resistance, support
    )

    # 2) FRVP + OR → triggers
    breakdown, breakout = _select_triggers(
        daily_support=daily_support,
        daily_resistance=daily_resistance,
        f24_val=f24_val,
        f24_poc=f24_poc,
        f24_vah=f24_vah,
        morn_val=morn_val,
        morn_poc=morn_poc,
        morn_vah=morn_vah,
        r30_high=r30_high,
        r30_low=r30_low,
    )

    return {
        "daily_support": float(daily_support),
        "daily_resistance": float(daily_resistance),
        "breakout_trigger": float(breakout),
        "breakdown_trigger": float(breakdown),
        "htf_resistance": htf_resistance,
        "htf_support": htf_support,
    }
