"""
KTBB SSE Engine – Simplified Deterministic Version

This module implements a *practical* version of the Structural Level Engine
described in Execution Anchor v4.2.

Goals:
- Take a single 4H + 1H supply shelf and demand shelf.
- Use Weekly VRVP + 24h FRVP + Morning FRVP + 30m range as context.
- Produce deterministic:
    - daily_support
    - daily_resistance
    - breakout_trigger
    - breakdown_trigger
- Emit HTF shelf metadata in a shape that KTBB Trade Logic v1.6 can consume.

Notes / Limitations:
- We do NOT have full SSE inputs (touch count, volume ratings, bookmap, etc).
  Those are STUBBED to neutral constants and can be extended later.
- Morning FRVP is *only* used as context for triggers, never for daily S/R.
- Current price is not available; distance rules that use "≥0.5% from price"
  are approximated via spacing inside the daily S/R band.
"""

from typing import Dict, Any, Tuple


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _pct_diff(a: float, b: float) -> float:
    """Relative difference |a-b| / max(|b|, tiny)."""
    denom = abs(b) if abs(b) > 1e-9 else 1.0
    return abs(a - b) / denom


def _frvp_alignment(
    level: float,
    role: str,
    weekly_val: float,
    weekly_poc: float,
    weekly_vah: float,
    f24_val: float,
    f24_poc: float,
    f24_vah: float,
) -> int:
    """
    Very simplified FRVP alignment scoring (0–2).

    For resistance shelves we care more about VAH/POC.
    For support shelves we care more about VAL/POC.

    Returns:
        0 = no clear alignment
        1 = loose alignment (within ~0.3%)
        2 = strong alignment (within ~0.15%)
    """
    # Choose relevant edges based on role.
    if role == "resistance":
        candidates = [weekly_vah, weekly_poc, f24_vah, f24_poc]
    else:  # "support"
        candidates = [weekly_val, weekly_poc, f24_val, f24_poc]

    # Compute minimum pct distance.
    min_pct = min(_pct_diff(level, x) for x in candidates)

    if min_pct <= 0.0015:      # ~0.15%
        return 2
    if min_pct <= 0.0030:      # ~0.30%
        return 1
    return 0


def _htf_strength(
    level: float,
    tf: str,
    role: str,
    weekly_val: float,
    weekly_poc: float,
    weekly_vah: float,
    f24_val: float,
    f24_poc: float,
    f24_vah: float,
) -> float:
    """
    Simplified HTF shelf strength following the spec shape:

        htf_strength =
            3 × touches
          + 2 × volume_rating
          + 2 × frvp_alignment
          + 1 × structural_tag   (4H=1, 1H=0)

    We don't know touches or volume_rating here, so we:
        - assume touches = 1 (clean shelf)
        - assume volume_rating = 1 (neutral)
        - frvp_alignment via _frvp_alignment (0–2)
        - structural_tag = 1 for 4H, 0 for 1H

    Then we normalize from raw [0, 12] to [0, 10].
    """

    touches = 1
    volume_rating = 1
    frvp_align = _frvp_alignment(
        level,
        role,
        weekly_val,
        weekly_poc,
        weekly_vah,
        f24_val,
        f24_poc,
        f24_vah,
    )
    structural_tag = 1 if tf == "4H" else 0

    raw = (
        3 * touches
        + 2 * volume_rating
        + 2 * frvp_align
        + 1 * structural_tag
    )  # max ~12, min ~0

    strength = max(0.0, min(10.0, raw * (10.0 / 12.0)))
    return round(strength, 1)


def _choose_daily_levels(
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
) -> Tuple[float, float, list, list]:
    """
    Select daily_support and daily_resistance from HTF shelves only.

    - Score 4H/1H demand shelves for support.
    - Score 4H/1H supply shelves for resistance.
    - Highest strength becomes primary (primary=True).
    - Daily levels come from primary shelves only.
    """

    # --- Support side ---
    s4h = _htf_strength(
        h4_demand,
        "4H",
        "support",
        weekly_val,
        weekly_poc,
        weekly_vah,
        f24_val,
        f24_poc,
        f24_vah,
    )
    s1h = _htf_strength(
        h1_demand,
        "1H",
        "support",
        weekly_val,
        weekly_poc,
        weekly_vah,
        f24_val,
        f24_poc,
        f24_vah,
    )

    if s4h >= s1h:
        daily_support = h4_demand
        primary_support_tf = "4H"
    else:
        daily_support = h1_demand
        primary_support_tf = "1H"

    htf_support = [
        {
            "tf": "4H",
            "level": h4_demand,
            "strength": s4h,
            "primary": primary_support_tf == "4H",
        },
        {
            "tf": "1H",
            "level": h1_demand,
            "strength": s1h,
            "primary": primary_support_tf == "1H",
        },
    ]

    # --- Resistance side ---
    r4h = _htf_strength(
        h4_supply,
        "4H",
        "resistance",
        weekly_val,
        weekly_poc,
        weekly_vah,
        f24_val,
        f24_poc,
        f24_vah,
    )
    r1h = _htf_strength(
        h1_supply,
        "1H",
        "resistance",
        weekly_val,
        weekly_poc,
        weekly_vah,
        f24_val,
        f24_poc,
        f24_vah,
    )

    if r4h >= r1h:
        daily_resistance = h4_supply
        primary_res_tf = "4H"
    else:
        daily_resistance = h1_supply
        primary_res_tf = "1H"

    htf_resistance = [
        {
            "tf": "4H",
            "level": h4_supply,
            "strength": r4h,
            "primary": primary_res_tf == "4H",
        },
        {
            "tf": "1H",
            "level": h1_supply,
            "strength": r1h,
            "primary": primary_res_tf == "1H",
        },
    ]

    # Safety: if levels are inverted, fix ordering.
    if daily_support >= daily_resistance:
        # Fallback: use min demand for support, max supply for resistance.
        daily_support = min(h4_demand, h1_demand)
        daily_resistance = max(h4_supply, h1_supply)

    return daily_support, daily_resistance, htf_resistance, htf_support


def _build_triggers(
    daily_support: float,
    daily_resistance: float,
    h4_supply: float,
    h4_demand: float,
    h1_supply: float,
    h1_demand: float,
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
    Construct breakout and breakdown triggers using:
    - HTF shelves (bias)
    - 24h FRVP (harder backbone)
    - Morning FRVP (must be cleared)
    - 30m range

    Then enforce ordering + spacing:

        daily_support < breakdown < breakout < daily_resistance
    """

    span = max(daily_resistance - daily_support, 1.0)

    # --- Initial directional candidates ---
    breakout_candidate = max(
        h1_supply,
        h4_supply,
        f24_vah,
        f24_poc,
        morn_vah,
        r30_high,
    )

    breakdown_candidate = min(
        h1_demand,
        h4_demand,
        f24_val,
        f24_poc,
        morn_val,
        r30_low,
    )

    # --- Core spacing: ~0.15% of level, at least 5% of the band ---
    min_gap_from_edges = max(span * 0.05, daily_support * 0.0015)

    # Clamp into the (support, resistance) band with padding.
    breakdown = breakdown_candidate
    breakout = breakout_candidate

    # First, push off absolute edges.
    if breakdown <= daily_support + min_gap_from_edges:
        breakdown = daily_support + min_gap_from_edges
    if breakout >= daily_resistance - min_gap_from_edges:
        breakout = daily_resistance - min_gap_from_edges

    # Ensure ordering; if too tight, center them.
    if breakout <= breakdown + min_gap_from_edges:
        mid = (daily_support + daily_resistance) / 2.0
        breakdown = mid - (min_gap_from_edges / 2.0)
        breakout = mid + (min_gap_from_edges / 2.0)

    # Final hard clamp just in case numerical weirdness:
    breakdown = max(daily_support + min_gap_from_edges, min(breakdown, daily_resistance - 2 * min_gap_from_edges))
    breakout = max(breakdown + min_gap_from_edges, min(breakout, daily_resistance - min_gap_from_edges))

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
    Main entry point used by FastAPI.

    Inputs match your current web form:
        - 4H / 1H shelves
        - Weekly VRVP
        - 24h FRVP
        - Morning FRVP
        - 30m opening range

    Returns:
        dict with:
            - daily_support
            - daily_resistance
            - breakout_trigger
            - breakdown_trigger
            - htf_resistance: [ {tf, level, strength, primary}, ... ]
            - htf_support:    [ {tf, level, strength, primary}, ... ]
    """

    # 1) Choose daily support / resistance from HTF shelves only.
    daily_support, daily_resistance, htf_resistance, htf_support = _choose_daily_levels(
        h4_supply,
        h4_demand,
        h1_supply,
        h1_demand,
        weekly_val,
        weekly_poc,
        weekly_vah,
        f24_val,
        f24_poc,
        f24_vah,
    )

    # 2) Build breakout / breakdown triggers.
    breakdown_trigger, breakout_trigger = _build_triggers(
        daily_support,
        daily_resistance,
        h4_supply,
        h4_demand,
        h1_supply,
        h1_demand,
        f24_val,
        f24_poc,
        f24_vah,
        morn_val,
        morn_poc,
        morn_vah,
        r30_high,
        r30_low,
    )

    return {
        "daily_support": round(daily_support, 1),
        "daily_resistance": round(daily_resistance, 1),
        "breakout_trigger": round(breakout_trigger, 1),
        "breakdown_trigger": round(breakdown_trigger, 1),
        "htf_resistance": htf_resistance,
        "htf_support": htf_support,
    }
