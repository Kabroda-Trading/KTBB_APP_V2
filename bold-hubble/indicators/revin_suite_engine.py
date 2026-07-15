"""
revin_suite_engine.py — Unified Revin Suite (R-Squared) Engine

Krown Trading's complete 3-pillar quantitative decision system.

Combines:
1. Revin Ribbons — Adaptive support/resistance envelopes (21 EMA ±σ bands)
2. RMO — Revin Momentum Oscillator (-100 to +100 composite)
3. RWP — Revin Width Percentile (volatility regime percentile)

Usage:
    from indicators.revin_suite_engine import compute_revin_suite
    suite = compute_revin_suite(close_prices, high_prices, low_prices)
    # suite["ribbons"], suite["rmo"], suite["rwp"]
"""

from typing import List, Dict, Any, Optional

from .revin_ribbons import calculate_revin_ribbons, analyze_ribbon_state
from .rmo import calculate_rmo, analyze_rmo_state
from .rwp import calculate_rwp, analyze_rwp_state


def compute_revin_suite(
    close_prices: List[float],
    high_prices: List[float],
    low_prices: List[float],
    midline_period: int = 21,
    rwp_lookback: int = 252,
) -> Dict[str, Any]:
    """
    Computes the complete Revin Suite (R-Squared) for the given price data.

    Returns a dict with:
        ribbons: Full Revin Ribbons envelope (midline, bands, widths)
        rmo:     Revin Momentum Oscillator values per bar
        rwp:     Revin Width Percentile values per bar
        current: Latest bar's combined state analysis
    """
    # Pillar 1: Revin Ribbons
    ribbons = calculate_revin_ribbons(close_prices, midline_period=midline_period)

    # Pillar 2: RMO
    rmo_values = calculate_rmo(close_prices, high_prices, low_prices)

    # Pillar 3: RWP (uses ribbon_width from Revin Ribbons)
    rwp_values = calculate_rwp(ribbons["ribbon_width"], lookback=rwp_lookback)

    # Current bar state
    current_price = close_prices[-1]
    ribbon_state = analyze_ribbon_state(ribbons, current_price)
    rmo_state = analyze_rmo_state(rmo_values[-1] if rmo_values else None)
    rwp_state = analyze_rwp_state(rwp_values[-1] if rwp_values else None)

    # Combined signal
    combined_signal = _compute_combined_signal(ribbon_state, rmo_state, rwp_state)

    return {
        "ribbons": ribbons,
        "rmo": rmo_values,
        "rwp": rwp_values,
        "current": {
            "ribbon_state": ribbon_state,
            "rmo_state": rmo_state,
            "rwp_state": rwp_state,
            "combined_signal": combined_signal,
        },
    }


def _compute_combined_signal(
    ribbon_state: Dict[str, Any],
    rmo_state: Dict[str, Any],
    rwp_state: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Synthesizes all 3 pillars into a single actionable signal.

    Priority:
    1. RWP squeeze + price at gray dot = BOUNCE_SETUP
    2. RMO extreme + outer band = EXHAUSTION_WARNING
    3. Above midline + RMO bullish + RWP expanding = TREND_CONFIRMED
    4. Below midline + RMO bearish + RWP expanding = TREND_CONFIRMED
    5. Otherwise = NEUTRAL
    """
    signal = "NEUTRAL"
    confidence = 0.0
    reasons = []

    is_squeeze = rwp_state.get("is_squeeze", False)
    gray_dot = ribbon_state.get("gray_dot_tested", False)
    rmo_score = rmo_state.get("score", 0.0)
    rmo_overextended = rmo_state.get("is_overextended", False)
    outer_band = ribbon_state.get("outer_band_tested", False)
    above_mid = ribbon_state.get("is_above_midline", False)
    below_mid = ribbon_state.get("is_below_midline", False)
    rwp_expanding = rwp_state.get("is_expansion", False)

    # 1. Squeeze + gray dot = bounce setup
    if is_squeeze and gray_dot:
        signal = "BOUNCE_SETUP"
        confidence = 85.0
        reasons.append("RWP squeeze + gray dot support test")

    # 2. RMO extreme + outer band = exhaustion
    if rmo_overextended and outer_band:
        if confidence < 80.0:
            signal = "EXHAUSTION_WARNING"
            confidence = 80.0
            reasons.append(f"RMO extreme ({rmo_score}) + outer band touched")

    # 3. Trend confirmed (bullish)
    if above_mid and rmo_score > 30 and rwp_expanding:
        if confidence < 75.0:
            signal = "TREND_CONFIRMED_BULLISH"
            confidence = 75.0
            reasons.append("Above midline + RMO bullish + RWP expanding")

    # 4. Trend confirmed (bearish)
    if below_mid and rmo_score < -30 and rwp_expanding:
        if confidence < 75.0:
            signal = "TREND_CONFIRMED_BEARISH"
            confidence = 75.0
            reasons.append("Below midline + RMO bearish + RWP expanding")

    # 5. RWP squeeze alone = watch for breakout
    if is_squeeze and not gray_dot:
        if confidence < 50.0:
            signal = "SQUEEZE_WATCH"
            confidence = 50.0
            reasons.append("RWP extreme compression — watch for breakout direction")

    return {
        "signal": signal,
        "confidence": confidence,
        "reasons": "; ".join(reasons) if reasons else "No strong confluence",
    }
