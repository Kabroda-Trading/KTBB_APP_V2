"""
rmo.py — Revin Momentum Oscillator (RMO)

Krown Trading's R-Squared Suite — Pillar 2 of 3.

Multi-dimensional momentum composite scoring -100 to +100.

Measures 5 distinct momentum vectors simultaneously:
1. Duration: Length of directional push (how many bars since last pivot)
2. Price Move Magnitude: Amplitude of the move relative to ATR
3. Separation: Distance between moving average envelopes (ribbon spread)
4. Oscillator Level: Normalized RSI-based overbought/oversold baseline
5. Combined Reading: Weighted composite momentum score

Key thresholds:
- RMO > +60: Strong bullish momentum (overextended zone)
- RMO < -60: Strong bearish momentum (oversold zone)
- RMO between -20 and +20: Neutral / low momentum
- RMO divergence vs price: Early reversal warning

Usage:
    from indicators.rmo import calculate_rmo, analyze_rmo_state
    rmo = calculate_rmo(close_prices, high_prices, low_prices)
    state = analyze_rmo_state(rmo[-1])
"""

import math
from typing import List, Dict, Optional, Union

from .trend_volatility import calculate_ema
from .bbwp import calculate_sma
from .rsi_divergence import calculate_rsi, find_local_extrema


def _calculate_atr(
    high_prices: List[float],
    low_prices: List[float],
    close_prices: List[float],
    period: int = 14,
) -> List[Optional[float]]:
    """Average True Range for normalizing price move magnitude."""
    n = len(close_prices)
    tr: List[Optional[float]] = [None] * n
    for i in range(1, n):
        hl = high_prices[i] - low_prices[i]
        hc = abs(high_prices[i] - close_prices[i - 1])
        lc = abs(low_prices[i] - close_prices[i - 1])
        tr[i] = max(hl, hc, lc)
    # SMA of TR
    atr: List[Optional[float]] = [None] * n
    for i in range(period, n):
        window = [t for t in tr[i - period + 1 : i + 1] if t is not None]
        if window:
            atr[i] = sum(window) / len(window)
    return atr


def _calculate_ribbon_separation(
    close_prices: List[float],
    fast_period: int = 8,
    slow_period: int = 21,
) -> List[Optional[float]]:
    """Distance between fast and slow EMAs as a percentage of price."""
    ema_fast = calculate_ema(close_prices, fast_period)
    ema_slow = calculate_ema(close_prices, slow_period)
    n = len(close_prices)
    sep: List[Optional[float]] = [None] * n
    for i in range(n):
        if ema_fast[i] is not None and ema_slow[i] is not None and ema_slow[i] != 0:
            sep[i] = (ema_fast[i] - ema_slow[i]) / ema_slow[i]
    return sep


def calculate_rmo(
    close_prices: List[float],
    high_prices: List[float],
    low_prices: List[float],
    rsi_period: int = 14,
    duration_lookback: int = 20,
    atr_period: int = 14,
) -> List[Optional[float]]:
    """
    Calculates the Revin Momentum Oscillator (-100 to +100).

    Composite of 4 sub-components:
    - Duration score: How long the current directional push has lasted
    - Magnitude score: How large the move is relative to ATR
    - Separation score: EMA ribbon spread (fast - slow)
    - RSI score: Normalized RSI reading

    Returns a list of composite scores (-100 to +100) per bar.
    """
    n = len(close_prices)
    rmo: List[Optional[float]] = [None] * n

    # Sub-component calculations
    rsi = calculate_rsi(close_prices, rsi_period)
    atr = _calculate_atr(high_prices, low_prices, close_prices, atr_period)
    sep = _calculate_ribbon_separation(close_prices, 8, 21)

    # Find pivot points for duration measurement (global, for filtering per-bar)
    all_low_pivots, _ = find_local_extrema(low_prices, order=3)
    all_high_pivots, _ = find_local_extrema(high_prices, order=3)

    for i in range(max(60, rsi_period, atr_period), n):
        # ── Filter pivots to only those confirmed as of bar i ──────────
        # A pivot at index p is only valid if p <= i - order (needs `order`
        # bars of confirmation on each side). Using global pivots without
        # this filter creates look-ahead bias — the code would reference
        # pivots that haven't happened yet from bar i's perspective.
        order = 3
        low_pivots = [p for p in all_low_pivots if p <= i - order]
        high_pivots = [p for p in all_high_pivots if p <= i - order]

        # ── 1. Duration Score (-25 to +25) ──────────────────────────────
        # Count bars since last pivot flip
        bars_since_last_high = i - (high_pivots[-1] if high_pivots else 0)
        bars_since_last_low = i - (low_pivots[-1] if low_pivots else 0)

        # If we're above the most recent low pivot, bullish duration
        if low_pivots and close_prices[i] > low_prices[low_pivots[-1]]:
            duration_raw = min(bars_since_last_low, duration_lookback) / duration_lookback
            duration_score = duration_raw * 25.0
        elif high_pivots and close_prices[i] < high_prices[high_pivots[-1]]:
            duration_raw = min(bars_since_last_high, duration_lookback) / duration_lookback
            duration_score = -duration_raw * 25.0
        else:
            duration_score = 0.0

        # ── 2. Magnitude Score (-25 to +25) ────────────────────────────
        # How far price has moved from the nearest pivot, in ATR units
        if atr[i] is not None and atr[i] > 0:
            if low_pivots and close_prices[i] > low_prices[low_pivots[-1]]:
                move = close_prices[i] - low_prices[low_pivots[-1]]
                atr_units = move / atr[i]
                magnitude_score = min(atr_units, 5.0) / 5.0 * 25.0
            elif high_pivots and close_prices[i] < high_prices[high_pivots[-1]]:
                move = high_prices[high_pivots[-1]] - close_prices[i]
                atr_units = move / atr[i]
                magnitude_score = -min(atr_units, 5.0) / 5.0 * 25.0
            else:
                magnitude_score = 0.0
        else:
            magnitude_score = 0.0

        # ── 3. Separation Score (-25 to +25) ────────────────────────────
        if sep[i] is not None:
            # Normalize: ±5% ribbon spread maps to ±25
            sep_score = max(-25.0, min(25.0, sep[i] / 0.05 * 25.0))
        else:
            sep_score = 0.0

        # ── 4. RSI Score (-25 to +25) ─────────────────────────────────
        if rsi[i] is not None:
            # RSI 50 = 0, RSI 80 = +25, RSI 20 = -25
            rsi_score = (rsi[i] - 50.0) / 30.0 * 25.0
            rsi_score = max(-25.0, min(25.0, rsi_score))
        else:
            rsi_score = 0.0

        # ── Composite ───────────────────────────────────────────────────
        composite = duration_score + magnitude_score + sep_score + rsi_score
        composite = max(-100.0, min(100.0, composite))
        rmo[i] = round(composite, 2)

    return rmo


def analyze_rmo_state(rmo_value: Optional[float]) -> Dict[str, Union[str, bool, float]]:
    """
    Interprets RMO value into actionable momentum states.

    Returns:
        score: The raw RMO value
        state: "STRONG_BULLISH" | "BULLISH" | "NEUTRAL" | "BEARISH" | "STRONG_BEARISH"
        is_overextended: True if RMO > +60 or < -60
        divergence_warning: True if RMO is extreme (potential reversal)
    """
    if rmo_value is None:
        return {
            "score": 0.0,
            "state": "UNKNOWN",
            "is_overextended": False,
            "divergence_warning": False,
        }

    state = "NEUTRAL"
    is_overextended = False
    divergence_warning = False

    if rmo_value >= 60.0:
        state = "STRONG_BULLISH"
        is_overextended = True
        divergence_warning = True
    elif rmo_value >= 30.0:
        state = "BULLISH"
    elif rmo_value <= -60.0:
        state = "STRONG_BEARISH"
        is_overextended = True
        divergence_warning = True
    elif rmo_value <= -30.0:
        state = "BEARISH"

    return {
        "score": rmo_value,
        "state": state,
        "is_overextended": is_overextended,
        "divergence_warning": divergence_warning,
    }
