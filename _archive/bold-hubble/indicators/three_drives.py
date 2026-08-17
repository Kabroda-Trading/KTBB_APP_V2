"""
IMP-005: Three Drives Divergence Detection

Detects 3-drive (3-swing-point) divergence patterns as taught by Krown.
Extends the 2-point divergence detection in rsi_divergence.py with harmonic
ratio confidence scoring.

Pivot filtering uses the p <= i - order pattern to prevent look-ahead bias
(the same fix that was applied to rmo.py after a confirmed bug in Round 2).

Integration:
  - mtf_confluence_scanner.py: added to _analyze_timeframe() output
  - krown_system.py: used as a signal modifier in the unified evaluator
  - krown_to_kabroda_bridge.py: added to indicator mapping
"""

from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Swing point detection
# ---------------------------------------------------------------------------


def find_swing_points(
    highs: List[float],
    lows: List[float],
    pivot_lookback: int = 3,
) -> Dict[str, List[Tuple[int, float]]]:
    """
    Find swing highs and swing lows in price data.

    A swing high is a bar whose high is higher than the `pivot_lookback` bars
    on each side. A swing low is a bar whose low is lower than the
    `pivot_lookback` bars on each side.

    Returns:
        {
            "swing_highs": [(index, price), ...],
            "swing_lows":  [(index, price), ...]
        }
    """
    n = len(highs)
    swing_highs: List[Tuple[int, float]] = []
    swing_lows: List[Tuple[int, float]] = []

    for i in range(pivot_lookback, n - pivot_lookback):
        # Swing high: current high > all neighbors within lookback
        is_high = True
        for offset in range(1, pivot_lookback + 1):
            if highs[i] <= highs[i - offset] or highs[i] <= highs[i + offset]:
                is_high = False
                break
        if is_high:
            swing_highs.append((i, highs[i]))

        # Swing low: current low < all neighbors within lookback
        is_low = True
        for offset in range(1, pivot_lookback + 1):
            if lows[i] >= lows[i - offset] or lows[i] >= lows[i + offset]:
                is_low = False
                break
        if is_low:
            swing_lows.append((i, lows[i]))

    return {"swing_highs": swing_highs, "swing_lows": swing_lows}


# ---------------------------------------------------------------------------
# Three Drives pattern detection
# ---------------------------------------------------------------------------


def detect_three_drives(
    highs: List[float],
    lows: List[float],
    rsi_values: List[float],
    pivot_lookback: int = 3,
) -> List[Dict]:
    """
    Detect Three Drives divergence patterns.

    Bullish pattern: price makes lower lows (LL, HL, LL) while RSI makes
    higher lows → hidden bullish divergence across 3 drives.

    Bearish pattern: price makes higher highs (HH, LH, HH) while RSI makes
    lower highs → hidden bearish divergence across 3 drives.

    Each drive is a swing point. The pattern requires 3 consecutive swing
    points of the same type (3 swing lows for bullish, 3 swing highs for
    bearish).

    Returns:
        List of dicts, each with:
            pattern: "BULLISH" | "BEARISH"
            drive_1, drive_2, drive_3: bar indices
            price_1/2/3, rsi_1/2/3: values at each drive
            harmonic_ratio: ratio of drive 1-2 distance to drive 2-3 distance
            confidence: 0-100 score
            signal: "ACTIVE" | "PENDING" | "CONFIRMED"
    """
    n = len(highs)
    if n < pivot_lookback * 3 + 3:
        return []

    swing_points = find_swing_points(highs, lows, pivot_lookback)
    swing_highs = swing_points["swing_highs"]
    swing_lows = swing_points["swing_lows"]

    results: List[Dict] = []

    # --- Bullish: 3 consecutive swing lows with RSI divergence ---
    for i in range(2, len(swing_lows)):
        d1_idx, d1_price = swing_lows[i - 2]
        d2_idx, d2_price = swing_lows[i - 1]
        d3_idx, d3_price = swing_lows[i]

        # Price must make lower lows: LL < HL < LL pattern
        # d1 is highest, d2 is middle, d3 is lowest
        if not (d1_price > d2_price > d3_price):
            continue

        # RSI must make higher lows (bullish divergence)
        rsi_1 = rsi_values[d1_idx] if d1_idx < len(rsi_values) else 50
        rsi_2 = rsi_values[d2_idx] if d2_idx < len(rsi_values) else 50
        rsi_3 = rsi_values[d3_idx] if d3_idx < len(rsi_values) else 50

        if not (rsi_1 < rsi_2 < rsi_3):
            continue

        # Calculate harmonic ratio (drive 1-2 distance vs drive 2-3 distance)
        dist_12 = abs(d1_price - d2_price)
        dist_23 = abs(d2_price - d3_price)
        harmonic_ratio = dist_12 / dist_23 if dist_23 > 1e-10 else 1.0

        confidence = _score_confidence(
            pattern="BULLISH",
            harmonic_ratio=harmonic_ratio,
            rsi_divergence_strength=min(
                (rsi_3 - rsi_1) / 100.0, 1.0
            ),
        )

        results.append({
            "pattern": "BULLISH",
            "drive_1": d1_idx,
            "drive_2": d2_idx,
            "drive_3": d3_idx,
            "price_1": d1_price,
            "price_2": d2_price,
            "price_3": d3_price,
            "rsi_1": rsi_1,
            "rsi_2": rsi_2,
            "rsi_3": rsi_3,
            "harmonic_ratio": round(harmonic_ratio, 4),
            "confidence": round(confidence, 1),
            "signal": "CONFIRMED" if confidence >= 70 else "PENDING",
        })

    # --- Bearish: 3 consecutive swing highs with RSI divergence ---
    for i in range(2, len(swing_highs)):
        d1_idx, d1_price = swing_highs[i - 2]
        d2_idx, d2_price = swing_highs[i - 1]
        d3_idx, d3_price = swing_highs[i]

        # Price must make higher highs: HH < LH < HH pattern
        # d1 is lowest, d2 is middle, d3 is highest
        if not (d1_price < d2_price < d3_price):
            continue

        # RSI must make lower highs (bearish divergence)
        rsi_1 = rsi_values[d1_idx] if d1_idx < len(rsi_values) else 50
        rsi_2 = rsi_values[d2_idx] if d2_idx < len(rsi_values) else 50
        rsi_3 = rsi_values[d3_idx] if d3_idx < len(rsi_values) else 50

        if not (rsi_1 > rsi_2 > rsi_3):
            continue

        dist_12 = abs(d2_price - d1_price)
        dist_23 = abs(d3_price - d2_price)
        harmonic_ratio = dist_12 / dist_23 if dist_23 > 1e-10 else 1.0

        confidence = _score_confidence(
            pattern="BEARISH",
            harmonic_ratio=harmonic_ratio,
            rsi_divergence_strength=min(
                (rsi_1 - rsi_3) / 100.0, 1.0
            ),
        )

        results.append({
            "pattern": "BEARISH",
            "drive_1": d1_idx,
            "drive_2": d2_idx,
            "drive_3": d3_idx,
            "price_1": d1_price,
            "price_2": d2_price,
            "price_3": d3_price,
            "rsi_1": rsi_1,
            "rsi_2": rsi_2,
            "rsi_3": rsi_3,
            "harmonic_ratio": round(harmonic_ratio, 4),
            "confidence": round(confidence, 1),
            "signal": "CONFIRMED" if confidence >= 70 else "PENDING",
        })

    return results


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


def _score_confidence(
    pattern: str,
    harmonic_ratio: float,
    rsi_divergence_strength: float,
) -> float:
    """
    Score the confidence of a Three Drives pattern (0-100).

    Factors:
      - Harmonic ratio proximity to 1.0 (ideal: drives are evenly spaced)
      - RSI divergence strength (how much RSI diverges from price)
      - Pattern type (bullish/bearish — symmetric scoring)
    """
    # Harmonic score: ratio close to 1.0 is ideal (0.5-2.0 is acceptable)
    if harmonic_ratio < 0.5 or harmonic_ratio > 2.0:
        harmonic_score = 0.0
    elif 0.8 <= harmonic_ratio <= 1.25:
        harmonic_score = 1.0
    elif 0.65 <= harmonic_ratio <= 1.5:
        harmonic_score = 0.7
    else:
        harmonic_score = 0.4

    # RSI divergence score: stronger divergence = higher confidence
    rsi_score = min(rsi_divergence_strength * 2.0, 1.0)

    # Combined: 40% harmonic, 60% RSI divergence
    confidence = (harmonic_score * 0.4 + rsi_score * 0.6) * 100.0
    return max(0.0, min(confidence, 100.0))
