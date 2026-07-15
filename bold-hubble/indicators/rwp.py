"""
rwp.py — Revin Width Percentile (RWP)

Krown Trading's R-Squared Suite — Pillar 3 of 3.

Volatility regime tracking via Revin Ribbons band width percentile.

Evaluates where the current band width of the Revin Ribbons sits relative
to its complete historical range (percentile 0% to 100%).

Key thresholds:
- RWP <= 10%: Extreme compression — kinetic expansion and violent
  directional breakouts become mathematically imminent.
- RWP >= 90%: Extreme expansion — trend may be exhausting.
- RWP 10-30%: Moderate compression — volatility building.
- RWP 70-90%: Active expansion — strong trend momentum.

Usage:
    from indicators.rwp import calculate_rwp, analyze_rwp_state
    rwp = calculate_rwp(ribbon_width_series)
    state = analyze_rwp_state(rwp[-1])
"""

from typing import List, Dict, Optional, Union


def calculate_rwp(
    ribbon_width_series: List[Optional[float]],
    lookback: int = 252,
) -> List[Optional[float]]:
    """
    Calculates the Revin Width Percentile (RWP).

    Takes the ribbon_width output from revin_ribbons.calculate_revin_ribbons()
    and ranks each value against its historical window.

    Args:
        ribbon_width_series: The 'ribbon_width' output from revin_ribbons
        lookback: Historical window for percentile ranking (default 252)

    Returns:
        List of percentile values (0.0 to 100.0) per bar
    """
    n = len(ribbon_width_series)
    rwp: List[Optional[float]] = [None] * n

    for i in range(n):
        current = ribbon_width_series[i]
        if current is None:
            continue

        # Determine lookback slice
        start_idx = max(0, i - lookback + 1)
        historical = [
            val for val in ribbon_width_series[start_idx : i + 1]
            if val is not None
        ]

        if not historical:
            continue

        # Percentile rank: count how many historical values are smaller
        count_smaller = sum(1 for val in historical if val < current)
        percentile = (count_smaller / len(historical)) * 100.0
        rwp[i] = round(percentile, 2)

    return rwp


def analyze_rwp_state(rwp_value: Optional[float]) -> Dict[str, Union[str, bool, float]]:
    """
    Interprets RWP value into actionable volatility regime states.

    Returns:
        score: The raw RWP percentile
        state: Volatility regime classification
        is_squeeze: True if RWP <= 10% (extreme compression)
        is_expansion: True if RWP >= 70%
        is_extreme_expansion: True if RWP >= 90%
    """
    if rwp_value is None:
        return {
            "score": 0.0,
            "state": "UNKNOWN",
            "is_squeeze": False,
            "is_expansion": False,
            "is_extreme_expansion": False,
        }

    state = "NEUTRAL"
    is_squeeze = False
    is_expansion = False
    is_extreme_expansion = False

    if rwp_value <= 10.0:
        state = "EXTREME_SQUEEZE"
        is_squeeze = True
    elif rwp_value <= 30.0:
        state = "MODERATE_COMPRESSION"
    elif rwp_value >= 90.0:
        state = "EXTREME_EXPANSION"
        is_expansion = True
        is_extreme_expansion = True
    elif rwp_value >= 70.0:
        state = "ACTIVE_EXPANSION"
        is_expansion = True

    return {
        "score": rwp_value,
        "state": state,
        "is_squeeze": is_squeeze,
        "is_expansion": is_expansion,
        "is_extreme_expansion": is_extreme_expansion,
    }
