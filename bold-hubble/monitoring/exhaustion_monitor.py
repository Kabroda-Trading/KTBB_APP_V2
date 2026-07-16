"""
IMP-006: Live Exhaustion Monitor

Detects in-trade runner exhaustion signals (PMARP > 95%, BBWP > 85%,
RSI divergence) for active trade candidates.

This module does NOT fetch its own candle data — it receives already-fetched
candles from the caller (Phase 3B/4B loops in ledger_closing_engine.py),
avoiding redundant Kraken API calls.

Integration:
  - ledger_closing_engine.py Phase 3B (15M) and Phase 4B (4H/1H) loops
    call check_exhaustion() with the already-fetched candles variable.
"""

from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Internal calculation helpers (lightweight, no external deps)
# ---------------------------------------------------------------------------


def _calc_ema_series(values: List[float], period: int) -> List[float]:
    """Exponential Moving Average series."""
    if len(values) < period:
        return [0.0] * len(values)
    multiplier = 2.0 / (period + 1)
    ema = [0.0] * len(values)
    # Seed with SMA
    ema[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        ema[i] = (values[i] - ema[i - 1]) * multiplier + ema[i - 1]
    return ema


def _calc_rsi_series(values: List[float], period: int = 14) -> List[float]:
    """Wilder's smoothed RSI series."""
    if len(values) < period + 1:
        return [50.0] * len(values)
    rsi = [50.0] * len(values)
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss < 1e-10:
        rsi[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss < 1e-10:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def _calc_bbwp(candles: List[Dict], period: int = 20) -> float:
    """
    Bollinger Band Width Percentile.

    Measures where current BB width sits within its 252-period history.
    BBWP > 85 indicates blow-off / exhaustion territory.
    """
    if len(candles) < period + 252:
        return 50.0
    closes = [c["close"] for c in candles]
    # Calculate BB width for each bar
    widths = []
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = variance ** 0.5
        width = 4.0 * std / mean  # (upper - lower) / middle
        widths.append(width)
    if len(widths) < 2:
        return 50.0
    current_width = widths[-1]
    # Percentile rank of current width in its history
    count_below = sum(1 for w in widths[:-1] if w <= current_width)
    percentile = (count_below / (len(widths) - 1)) * 100.0
    return percentile


def _calc_pmarp(candles: List[Dict], period: int = 21) -> Tuple[float, bool]:
    """
    Price Moving Average Relative Position.

    Measures where price sits within its 252-period range relative to EMA21.
    PMARP > 95 indicates overextension (potential exhaustion).
    Returns (pmarp_value, is_overextended).
    """
    if len(candles) < period + 252:
        return 50.0, False
    closes = [c["close"] for c in candles]
    ema21 = _calc_ema_series(closes, period)
    if ema21[-1] <= 0:
        return 50.0, False
    # PMARP = (close - ema21) / ATR-like range, normalized to 0-100
    lookback = 252
    if len(closes) < lookback:
        return 50.0, False
    recent_closes = closes[-lookback:]
    recent_ema = ema21[-lookback:]
    above_diffs = [(c - e) for c, e in zip(recent_closes, recent_ema) if c > e]
    below_diffs = [(e - c) for c, e in zip(recent_closes, recent_ema) if e > c]
    max_above = max(above_diffs) if above_diffs else 1.0
    max_below = max(below_diffs) if below_diffs else 1.0
    current_diff = closes[-1] - ema21[-1]
    if current_diff >= 0:
        pmarp = 50.0 + (current_diff / max_above) * 50.0
    else:
        pmarp = 50.0 - (-current_diff / max_below) * 50.0
    pmarp = max(0.0, min(pmarp, 100.0))
    return pmarp, pmarp > 95.0


def _check_rsi_divergence(candles: List[Dict], rsi_period: int = 14) -> bool:
    """
    Check for bearish RSI divergence on the most recent bars.

    Returns True if price made a higher high while RSI made a lower high
    (bearish divergence = potential exhaustion).
    """
    if len(candles) < rsi_period + 10:
        return False
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    rsi = _calc_rsi_series(closes, rsi_period)
    # Look at last 5-10 bars for divergence
    lookback = min(10, len(closes) - rsi_period)
    recent_highs = highs[-lookback:]
    recent_rsi = rsi[-lookback:]
    # Find highest high and its RSI
    max_high_idx = recent_highs.index(max(recent_highs))
    # Find second highest high before that
    if max_high_idx < 2:
        return False
    prev_highs = recent_highs[:max_high_idx]
    if not prev_highs:
        return False
    prev_max_idx = prev_highs.index(max(prev_highs))
    # Check: price made higher high, RSI made lower high
    if (recent_highs[max_high_idx] > recent_highs[prev_max_idx] and
            recent_rsi[max_high_idx] < recent_rsi[prev_max_idx]):
        return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_exhaustion(
    candles_1m: List[Dict],
    candles_5m: List[Dict],
    position: Dict,
) -> Dict:
    """
    Check for runner exhaustion signals.

    Args:
        candles_1m: 1-minute OHLCV candles (already fetched by Phase 3B/4B).
        candles_5m: 5-minute OHLCV candles (fetched by caller or market_data).
        position: Dict with at minimum:
            {"entry_price": float, "current_stop": float, "direction": "LONG"|"SHORT"}

    Returns:
        {
            "level": "NONE" | "WATCH" | "WARNING" | "EXIT",
            "signals": [
                {"type": "PMARP_OVEREXTENDED", "value": 96.5, "detail": "..."},
                ...
            ]
        }
    """
    signals: List[Dict] = []
    level: str = "NONE"

    # Use 5m candles for PMARP/BBWP (more reliable than 1m)
    candles = candles_5m if len(candles_5m) >= 30 else candles_1m
    if len(candles) < 30:
        return {"level": "NONE", "signals": []}

    # --- PMARP check ---
    pmarp_value, is_overextended = _calc_pmarp(candles)
    if is_overextended:
        signals.append({
            "type": "PMARP_OVEREXTENDED",
            "value": round(pmarp_value, 1),
            "detail": f"PMARP at {pmarp_value:.1f} — price overextended from EMA21",
        })

    # --- BBWP check ---
    bbwp_value = _calc_bbwp(candles)
    if bbwp_value > 85.0:
        signals.append({
            "type": "BBWP_BLOWOFF",
            "value": round(bbwp_value, 1),
            "detail": f"BBWP at {bbwp_value:.1f} — volatility blow-off territory",
        })

    # --- RSI divergence check ---
    if _check_rsi_divergence(candles):
        signals.append({
            "type": "RSI_BEARISH_DIVERGENCE",
            "value": 0.0,
            "detail": "Bearish RSI divergence detected on recent bars",
        })

    # --- Determine alert level ---
    if not signals:
        level = "NONE"
    elif any(s["type"] == "PMARP_OVEREXTENDED" for s in signals) and \
         any(s["type"] == "BBWP_BLOWOFF" for s in signals):
        level = "EXIT"
    elif any(s["type"] == "PMARP_OVEREXTENDED" for s in signals) or \
         any(s["type"] == "BBWP_BLOWOFF" for s in signals):
        level = "WARNING"
    else:
        level = "WATCH"

    return {"level": level, "signals": signals}
