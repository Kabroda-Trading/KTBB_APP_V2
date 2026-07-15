"""
revin_ribbons.py — Revin Ribbons (Core Adaptive Support/Resistance Envelopes)

Krown Trading's R-Squared Suite — Pillar 1 of 3.

Computes an equilibrium midline (21-period EMA) alongside inner reaction
bands (±1.0 StDev) and outer extended volatility bands (±2.5 and ±3.5 StDev).

Key concepts:
- Midline: 21 EMA. Price above = bullish bias, below = bearish bias.
- Inner bands (±1.0σ): High-probability reaction zones where price is
  statistically favored to bounce or reject.
- Outer bands (±2.5σ, ±3.5σ): Extended volatility zones marking potential
  breakout acceleration or structural exhaustion.
- Gray dots: The primary lower reaction band (1st lower band) — the most
  statistically significant support zone.

Usage:
    from indicators.revin_ribbons import calculate_revin_ribbons, analyze_ribbon_state
    ribbons = calculate_revin_ribbons(close_prices)
    state = analyze_ribbon_state(ribbons, close_prices[-1])
"""

import math
from typing import List, Dict, Optional, Union

from .trend_volatility import calculate_ema
from .bbwp import calculate_sma, calculate_stdev


def calculate_revin_ribbons(
    close_prices: List[float],
    midline_period: int = 21,
    stdev_period: int = 21,
    multipliers: List[float] = None,
) -> Dict[str, List[Optional[float]]]:
    """
    Calculates the full Revin Ribbons envelope system.

    Returns a dict with keys:
        midline:      21-period EMA (equilibrium)
        upper_1σ:     Midline + (1.0 × StDev)
        lower_1σ:     Midline - (1.0 × StDev)  ← "gray dots" support zone
        upper_2σ:     Midline + (2.5 × StDev)
        lower_2σ:     Midline - (2.5 × StDev)
        upper_3σ:     Midline + (3.5 × StDev)
        lower_3σ:     Midline - (3.5 × StDev)
        ribbon_width: (Upper_1σ - Lower_1σ) / Midline (as decimal)
    """
    if multipliers is None:
        multipliers = [1.0, 2.5, 3.5]

    n = len(close_prices)
    midline = calculate_ema(close_prices, midline_period)
    stdev = calculate_stdev(close_prices, stdev_period, midline)

    upper_1σ: List[Optional[float]] = [None] * n
    lower_1σ: List[Optional[float]] = [None] * n
    upper_2σ: List[Optional[float]] = [None] * n
    lower_2σ: List[Optional[float]] = [None] * n
    upper_3σ: List[Optional[float]] = [None] * n
    lower_3σ: List[Optional[float]] = [None] * n
    ribbon_width: List[Optional[float]] = [None] * n

    m1, m2, m3 = multipliers[0], multipliers[1], multipliers[2]

    for i in range(n):
        if midline[i] is not None and stdev[i] is not None and midline[i] != 0:
            upper_1σ[i] = midline[i] + (m1 * stdev[i])
            lower_1σ[i] = midline[i] - (m1 * stdev[i])
            upper_2σ[i] = midline[i] + (m2 * stdev[i])
            lower_2σ[i] = midline[i] - (m2 * stdev[i])
            upper_3σ[i] = midline[i] + (m3 * stdev[i])
            lower_3σ[i] = midline[i] - (m3 * stdev[i])
            width = (upper_1σ[i] - lower_1σ[i]) / midline[i]
            ribbon_width[i] = round(width, 6)

    return {
        "midline": midline,
        "upper_1σ": upper_1σ,
        "lower_1σ": lower_1σ,
        "upper_2σ": upper_2σ,
        "lower_2σ": lower_2σ,
        "upper_3σ": upper_3σ,
        "lower_3σ": lower_3σ,
        "ribbon_width": ribbon_width,
    }


def analyze_ribbon_state(
    ribbons: Dict[str, List[Optional[float]]],
    current_price: float,
    bar_index: int = -1,
) -> Dict[str, Union[str, bool, float]]:
    """
    Interprets the current Revin Ribbons state into actionable signals.

    Returns:
        is_above_midline:  Price above 21 EMA → bullish bias
        is_below_midline:  Price below 21 EMA → bearish bias
        zone:              Which band zone price is in
        gray_dot_tested:   Price touched/reached lower_1σ (support test)
        outer_band_tested: Price reached upper_3σ or lower_3σ (exhaustion risk)
        midline_direction: "RISING" | "FALLING" | "FLAT"
    """
    midline = ribbons["midline"][bar_index]
    u1 = ribbons["upper_1σ"][bar_index]
    l1 = ribbons["lower_1σ"][bar_index]
    u2 = ribbons["upper_2σ"][bar_index]
    l2 = ribbons["lower_2σ"][bar_index]
    u3 = ribbons["upper_3σ"][bar_index]
    l3 = ribbons["lower_3σ"][bar_index]

    if midline is None:
        return {
            "is_above_midline": False,
            "is_below_midline": False,
            "zone": "UNKNOWN",
            "gray_dot_tested": False,
            "outer_band_tested": False,
            "midline_direction": "UNKNOWN",
        }

    is_above = current_price > midline
    is_below = current_price < midline

    # Determine which zone price is in
    zone = "AT_MIDLINE"
    if l1 is not None and current_price <= l1:
        zone = "BELOW_LOWER_1σ"
    elif u1 is not None and current_price >= u1:
        zone = "ABOVE_UPPER_1σ"
    if l2 is not None and current_price <= l2:
        zone = "BELOW_LOWER_2σ"
    elif u2 is not None and current_price >= u2:
        zone = "ABOVE_UPPER_2σ"
    if l3 is not None and current_price <= l3:
        zone = "BELOW_LOWER_3σ"
    elif u3 is not None and current_price >= u3:
        zone = "ABOVE_UPPER_3σ"

    # Gray dot test: price within 0.5% of lower_1σ
    gray_dot_tested = False
    if l1 is not None and l1 > 0:
        dist_to_l1 = abs(current_price - l1) / l1
        gray_dot_tested = dist_to_l1 < 0.005

    # Outer band exhaustion test
    outer_band_tested = False
    if u3 is not None and current_price >= u3:
        outer_band_tested = True
    if l3 is not None and current_price <= l3:
        outer_band_tested = True

    # Midline direction (compare last 3 bars)
    mid_prev = ribbons["midline"][bar_index - 1] if len(ribbons["midline"]) > abs(bar_index) + 1 else None
    mid_prev2 = ribbons["midline"][bar_index - 2] if len(ribbons["midline"]) > abs(bar_index) + 2 else None
    midline_direction = "FLAT"
    if mid_prev is not None and mid_prev2 is not None:
        if midline > mid_prev > mid_prev2:
            midline_direction = "RISING"
        elif midline < mid_prev < mid_prev2:
            midline_direction = "FALLING"

    return {
        "is_above_midline": is_above,
        "is_below_midline": is_below,
        "zone": zone,
        "gray_dot_tested": gray_dot_tested,
        "outer_band_tested": outer_band_tested,
        "midline_direction": midline_direction,
        "midline_price": round(midline, 2),
        "lower_1σ_price": round(l1, 2) if l1 is not None else None,
        "upper_1σ_price": round(u1, 2) if u1 is not None else None,
    }
