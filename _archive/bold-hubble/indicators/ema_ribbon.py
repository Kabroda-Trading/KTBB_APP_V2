"""
ema_ribbon.py — EMA Fibonacci Ribbon (5/21/55/377)

Krown Trading's multi-timeframe EMA alignment system.

Provides a Fibonacci-sequence EMA ribbon alongside the existing SMA 20/50
for multi-timeframe trend alignment analysis.

Key concepts:
- EMA 5: Ultra-short term (micro momentum)
- EMA 21: Short term (primary trend — also used as Revin Ribbons midline)
- EMA 55: Medium term (swing trend)
- EMA 377: Long term (macro trend — ~1.5 years of daily data)

Bullish alignment: 5 > 21 > 55 > 377 (all rising)
Bearish alignment: 5 < 21 < 55 < 377 (all falling)
Compression: EMAs converging (low spread) — potential breakout
Expansion: EMAs diverging (high spread) — strong trend

Usage:
    from indicators.ema_ribbon import calculate_ema_ribbon, analyze_ema_ribbon
    ribbon = calculate_ema_ribbon(close_prices)
    state = analyze_ema_ribbon(ribbon, close_prices[-1])
"""

from typing import List, Dict, Optional, Union

from .trend_volatility import calculate_ema


# Fibonacci-sequence EMA periods
FIB_PERIODS = [5, 21, 55, 377]


def calculate_ema_ribbon(
    close_prices: List[float],
    periods: List[int] = None,
) -> Dict[str, List[Optional[float]]]:
    """
    Calculates the full EMA Fibonacci ribbon.

    Args:
        close_prices: OHLC close prices
        periods: List of EMA periods (default: [5, 21, 55, 377])

    Returns:
        Dict with keys 'ema_5', 'ema_21', 'ema_55', 'ema_377'
        and 'ribbon_spread' (max - min EMA as % of price)
    """
    if periods is None:
        periods = FIB_PERIODS

    result: Dict[str, List[Optional[float]]] = {}
    n = len(close_prices)

    for p in periods:
        result[f"ema_{p}"] = calculate_ema(close_prices, p)

    # Ribbon spread: (max_ema - min_ema) / close as percentage
    spread: List[Optional[float]] = [None] * n
    for i in range(n):
        vals = [result[f"ema_{p}"][i] for p in periods if result[f"ema_{p}"][i] is not None]
        if len(vals) >= 2 and close_prices[i] != 0:
            spread[i] = round((max(vals) - min(vals)) / close_prices[i] * 100, 4)

    result["ribbon_spread_pct"] = spread
    return result


def analyze_ema_ribbon(
    ribbon: Dict[str, List[Optional[float]]],
    current_price: float,
    bar_index: int = -1,
) -> Dict[str, Union[str, bool, float]]:
    """
    Analyzes the current EMA ribbon state.

    Returns:
        alignment: "BULLISH" | "BEARISH" | "MIXED" | "COMPRESSED" | "UNKNOWN"
        is_bullish_aligned: All EMAs rising and in correct order
        is_bearish_aligned: All EMAs falling and in correct order
        is_compressed: Ribbon spread below threshold
        dominant_ema: Which EMA is closest to price (nearest support/resistance)
        spread_pct: Current ribbon spread as percentage
    """
    ema_5 = ribbon["ema_5"][bar_index]
    ema_21 = ribbon["ema_21"][bar_index]
    ema_55 = ribbon["ema_55"][bar_index]
    ema_377 = ribbon["ema_377"][bar_index]
    spread = ribbon["ribbon_spread_pct"][bar_index]

    if any(v is None for v in [ema_5, ema_21, ema_55, ema_377]):
        return {
            "alignment": "UNKNOWN",
            "is_bullish_aligned": False,
            "is_bearish_aligned": False,
            "is_compressed": False,
            "dominant_ema": None,
            "spread_pct": 0.0,
        }

    # Check EMA direction (compare last 3 bars)
    def _is_rising(ema_key: str) -> bool:
        vals = ribbon[ema_key]
        idx = bar_index
        if len(vals) < abs(idx) + 3:
            return False
        # Guard against None at idx-1 or idx-2 (warm-up boundary)
        if vals[idx - 1] is None or vals[idx - 2] is None:
            return False
        return vals[idx] > vals[idx - 1] > vals[idx - 2]

    def _is_falling(ema_key: str) -> bool:
        vals = ribbon[ema_key]
        idx = bar_index
        if len(vals) < abs(idx) + 3:
            return False
        # Guard against None at idx-1 or idx-2 (warm-up boundary)
        if vals[idx - 1] is None or vals[idx - 2] is None:
            return False
        return vals[idx] < vals[idx - 1] < vals[idx - 2]

    r5 = _is_rising("ema_5")
    r21 = _is_rising("ema_21")
    r55 = _is_rising("ema_55")
    r377 = _is_rising("ema_377")
    f5 = _is_falling("ema_5")
    f21 = _is_falling("ema_21")
    f55 = _is_falling("ema_55")
    f377 = _is_falling("ema_377")

    # Bullish alignment: 5 > 21 > 55 > 377 and all rising
    bullish_order = ema_5 > ema_21 > ema_55 > ema_377
    bullish_direction = r5 and r21 and r55 and r377
    is_bullish = bullish_order and bullish_direction

    # Bearish alignment: 5 < 21 < 55 < 377 and all falling
    bearish_order = ema_5 < ema_21 < ema_55 < ema_377
    bearish_direction = f5 and f21 and f55 and f377
    is_bearish = bearish_order and bearish_direction

    # Compression: spread below 0.5%
    is_compressed = spread is not None and spread < 0.5

    # Dominant EMA (closest to current price)
    emas = [
        ("ema_5", ema_5),
        ("ema_21", ema_21),
        ("ema_55", ema_55),
        ("ema_377", ema_377),
    ]
    dominant = min(emas, key=lambda x: abs(current_price - x[1]))

    alignment = "MIXED"
    if is_bullish:
        alignment = "BULLISH"
    elif is_bearish:
        alignment = "BEARISH"
    elif is_compressed:
        alignment = "COMPRESSED"

    return {
        "alignment": alignment,
        "is_bullish_aligned": is_bullish,
        "is_bearish_aligned": is_bearish,
        "is_compressed": is_compressed,
        "dominant_ema": dominant[0],
        "dominant_ema_price": round(dominant[1], 2),
        "spread_pct": round(spread, 4) if spread is not None else 0.0,
        "ema_5": round(ema_5, 2),
        "ema_21": round(ema_21, 2),
        "ema_55": round(ema_55, 2),
        "ema_377": round(ema_377, 2),
    }
