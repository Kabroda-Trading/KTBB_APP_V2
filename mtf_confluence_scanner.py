# mtf_confluence_scanner.py
# ==============================================================================
# KABRODA MULTI-TIMEFRAME CONFLUENCE SCANNER v1.0
# Purpose: Live 5-timeframe direction vote (15M/1H/4H/Daily/Weekly) with
# StochRSI, EMA21/55 bias, ADX strength, and KDE key level detection.
# Runs every 15 minutes via gravity engine loop. Standalone — read-only.
# DO NOT modify battlebox_pipeline.py or any existing file.
# ==============================================================================

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from battlebox_pipeline import (
    fetch_live_15m,
    fetch_live_1h,
    fetch_live_4h,
    fetch_live_daily,
    _normalize_symbol,
    _calc_ema_series,
    _calc_adx,
)
import gravity_math

TARGETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


# ------------------------------------------------------------------------------
# WEEKLY RESAMPLER
# ------------------------------------------------------------------------------

def _resample_weekly(daily_candles: List[Dict]) -> List[Dict]:
    """Bucket daily candles into weekly bars anchored on Sunday."""
    if not daily_candles:
        return []

    weeks: Dict[str, Dict] = {}
    for c in daily_candles:
        dt = datetime.fromtimestamp(c["time"], tz=timezone.utc)
        days_since_sunday = (dt.weekday() + 1) % 7
        sunday_ts = c["time"] - days_since_sunday * 86400
        key = str(sunday_ts - (sunday_ts % 86400))

        if key not in weeks:
            weeks[key] = {
                "time": int(key),
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
            }
        else:
            weeks[key]["high"] = max(weeks[key]["high"], c["high"])
            weeks[key]["low"] = min(weeks[key]["low"], c["low"])
            weeks[key]["close"] = c["close"]
            weeks[key]["volume"] += c["volume"]

    return sorted(weeks.values(), key=lambda x: x["time"])


# ------------------------------------------------------------------------------
# RSI SERIES (O(n) — needed for StochRSI)
# ------------------------------------------------------------------------------

def _calc_rsi_series(closes: List[float], period: int = 14) -> List[float]:
    """Full RSI series using Wilder's smoothing. Returns one value per close."""
    if len(closes) < period + 1:
        return []

    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_series: List[float] = []
    # Pad with None for alignment — first RSI value corresponds to index `period`
    for i in range(period, len(closes)):
        if i == period:
            ag, al = avg_gain, avg_loss
        else:
            ag = (ag * (period - 1) + gains[i - 1]) / period
            al = (al * (period - 1) + losses[i - 1]) / period

        if al == 0:
            rsi_series.append(100.0)
        else:
            rs = ag / al
            rsi_series.append(round(100.0 - (100.0 / (1.0 + rs)), 4))

    return rsi_series


# ------------------------------------------------------------------------------
# STOCH RSI
# ------------------------------------------------------------------------------

def _calc_stoch_rsi(
    candles: List[Dict],
    rsi_period: int = 14,
    stoch_period: int = 14,
    d_period: int = 3,
) -> Dict[str, Any]:
    """Stochastic formula applied to RSI values (not price). Zones at 20/40/60/80."""
    fallback = {"k": 50.0, "d": 50.0, "zone": "NEUTRAL", "curl": "FLAT"}
    if len(candles) < rsi_period + stoch_period + d_period + 5:
        return fallback

    closes = [c["close"] for c in candles]
    rsi = _calc_rsi_series(closes, rsi_period)
    if len(rsi) < stoch_period + d_period:
        return fallback

    k_vals: List[float] = []
    for i in range(stoch_period - 1, len(rsi)):
        window = rsi[i - stoch_period + 1 : i + 1]
        lo, hi = min(window), max(window)
        if hi == lo:
            k_vals.append(50.0)
        else:
            k_vals.append(round(100.0 * (rsi[i] - lo) / (hi - lo), 4))

    if len(k_vals) < d_period:
        return fallback

    d_vals = [
        sum(k_vals[i - d_period + 1 : i + 1]) / d_period
        for i in range(d_period - 1, len(k_vals))
    ]

    k = k_vals[-1]
    d = d_vals[-1]

    if k < 20:
        zone = "OVERSOLD"
    elif k < 40:
        zone = "VALUE_LOW"
    elif k < 60:
        zone = "NEUTRAL"
    elif k < 80:
        zone = "VALUE_HIGH"
    else:
        zone = "OVERBOUGHT"

    # Curl direction vs 3 periods ago (2-point threshold to avoid noise)
    curl = "FLAT"
    if len(k_vals) >= 4:
        delta = k_vals[-1] - k_vals[-4]
        if delta > 2.0:
            curl = "UP"
        elif delta < -2.0:
            curl = "DOWN"

    return {"k": round(k, 2), "d": round(d, 2), "zone": zone, "curl": curl}


# ------------------------------------------------------------------------------
# PER-TIMEFRAME ANALYSIS
# ------------------------------------------------------------------------------

def _analyze_timeframe(candles: List[Dict], label: str) -> Dict[str, Any]:
    """Compute EMA bias, StochRSI, and ADX for a single timeframe."""
    error_result = {
        "label": label,
        "ema_bias": "UNKNOWN",
        "stoch_rsi": {"k": 50.0, "d": 50.0, "zone": "NEUTRAL", "curl": "FLAT"},
        "adx": 0.0,
        "adx_strength": "WEAK",
        "adx_rising": False,
        "direction_vote": "NEUTRAL",
        "error": "insufficient_data",
    }

    if len(candles) < 60:
        return error_result

    closes = [c["close"] for c in candles]
    ema21 = _calc_ema_series(closes, 21)
    ema55 = _calc_ema_series(closes, 55)

    if not ema21 or not ema55:
        return error_result

    fast = ema21[-1]
    slow = ema55[-1]
    ema_bias = "BULLISH" if fast > slow else "BEARISH"

    stoch_rsi = _calc_stoch_rsi(candles)
    adx_data = _calc_adx(candles)

    adx_val = adx_data.get("adx", 0.0)
    adx_strength = "STRONG" if adx_val > 25 else "WEAK"
    adx_rising = adx_data.get("rising", False)

    return {
        "label": label,
        "ema_bias": ema_bias,
        "ema21": round(fast, 4),
        "ema55": round(slow, 4),
        "stoch_rsi": stoch_rsi,
        "adx": round(adx_val, 2),
        "adx_strength": adx_strength,
        "adx_rising": adx_rising,
        "direction_vote": ema_bias,
    }


# ------------------------------------------------------------------------------
# KEY LEVELS
# ------------------------------------------------------------------------------

def _find_key_levels(
    symbol: str, candles_4h: List[Dict], current_price: float
) -> Dict[str, Optional[float]]:
    """Primary: KDE peaks from gravity_math. Fallback: 4H highs/lows."""
    resistance: Optional[float] = None
    support: Optional[float] = None

    try:
        kde = gravity_math.calculate_gravity_kde(symbol)
        peaks = kde.get("peaks", [])
        if peaks:
            above = [p["price"] for p in peaks if p["price"] > current_price]
            below = [p["price"] for p in peaks if p["price"] < current_price]
            resistance = min(above) if above else None
            support = max(below) if below else None
    except Exception:
        pass

    # Fallback to 4H candle extremes if KDE returned nothing
    if resistance is None or support is None:
        highs = sorted([c["high"] for c in candles_4h if c["high"] > current_price])
        lows = sorted([c["low"] for c in candles_4h if c["low"] < current_price], reverse=True)
        if resistance is None and highs:
            resistance = round(highs[0], 4)
        if support is None and lows:
            support = round(lows[0], 4)

    return {
        "nearest_resistance": round(resistance, 4) if resistance else None,
        "nearest_support": round(support, 4) if support else None,
    }


# ------------------------------------------------------------------------------
# PLAIN-ENGLISH SUMMARY
# ------------------------------------------------------------------------------

def _build_summary(
    tf_data: Dict[str, Dict],
    score: int,
    conviction: str,
    dominant_direction: str,
    current_price: float,
    levels: Dict[str, Optional[float]],
) -> str:
    aligned = [v["label"] for v in tf_data.values() if v.get("direction_vote") == dominant_direction]
    opposing = [v["label"] for v in tf_data.values() if v.get("direction_vote") not in (dominant_direction, "NEUTRAL", "UNKNOWN")]
    curl_up = [v["label"] for v in tf_data.values() if v.get("stoch_rsi", {}).get("curl") == "UP"]
    curl_down = [v["label"] for v in tf_data.values() if v.get("stoch_rsi", {}).get("curl") == "DOWN"]
    overbought = [v["label"] for v in tf_data.values() if v.get("stoch_rsi", {}).get("zone") == "OVERBOUGHT"]
    oversold = [v["label"] for v in tf_data.values() if v.get("stoch_rsi", {}).get("zone") == "OVERSOLD"]
    strong_adx = [v["label"] for v in tf_data.values() if v.get("adx_strength") == "STRONG"]

    res = levels.get("nearest_resistance")
    sup = levels.get("nearest_support")

    if dominant_direction == "NEUTRAL":
        parts = [
            f"SPLIT MARKET — no directional confluence ({score}/5 timeframes agree).",
            f"Price at {current_price}.",
        ]
    else:
        parts = [
            f"{conviction} {dominant_direction} confluence ({score}/5 timeframes aligned).",
            f"Price at {current_price}.",
        ]

    if aligned:
        parts.append(f"Aligned TFs: {', '.join(aligned)}.")
    if opposing:
        parts.append(f"Opposing: {', '.join(opposing)}.")
    if curl_up:
        parts.append(f"StochRSI curling UP on: {', '.join(curl_up)}.")
    if curl_down:
        parts.append(f"StochRSI curling DOWN on: {', '.join(curl_down)}.")
    if overbought:
        parts.append(f"Overbought (risk of rejection): {', '.join(overbought)}.")
    if oversold:
        parts.append(f"Oversold (potential bounce zone): {', '.join(oversold)}.")
    if strong_adx:
        parts.append(f"Strong trend momentum (ADX>25) on: {', '.join(strong_adx)}.")
    if res:
        parts.append(f"Nearest resistance: {res}.")
    if sup:
        parts.append(f"Nearest support: {sup}.")

    return " ".join(parts)


# ------------------------------------------------------------------------------
# MAIN SCAN FUNCTIONS
# ------------------------------------------------------------------------------

async def run_mtf_confluence_scan(symbol: str) -> Dict[str, Any]:
    """Run full 5-TF confluence scan for a single symbol. Live data only."""
    norm_sym = _normalize_symbol(symbol)

    raw_15m, raw_1h, raw_4h, raw_daily = await asyncio.gather(
        fetch_live_15m(norm_sym, limit=300),
        fetch_live_1h(norm_sym, limit=300),
        fetch_live_4h(norm_sym, limit=200),
        fetch_live_daily(norm_sym, limit=500),  # 500 days → ~71 weeks for EMA55 weekly
    )

    raw_weekly = _resample_weekly(raw_daily)

    current_price = raw_15m[-1]["close"] if raw_15m else 0.0

    tf_data = {
        "15M": _analyze_timeframe(raw_15m, "15M"),
        "1H": _analyze_timeframe(raw_1h, "1H"),
        "4H": _analyze_timeframe(raw_4h, "4H"),
        "1D": _analyze_timeframe(raw_daily, "1D"),
        "1W": _analyze_timeframe(raw_weekly, "1W"),
    }

    bull_count = sum(1 for v in tf_data.values() if v.get("direction_vote") == "BULLISH")
    bear_count = sum(1 for v in tf_data.values() if v.get("direction_vote") == "BEARISH")

    if bull_count > bear_count:
        dominant_direction = "BULLISH"
        score = bull_count
    elif bear_count > bull_count:
        dominant_direction = "BEARISH"
        score = bear_count
    else:
        dominant_direction = "NEUTRAL"
        score = max(bull_count, bear_count)

    if score >= 4:
        conviction = "HIGH"
    elif score == 3:
        conviction = "STANDARD"
    else:
        conviction = "LOW"

    levels = _find_key_levels(norm_sym, raw_4h, current_price)

    summary = _build_summary(tf_data, score, conviction, dominant_direction, current_price, levels)

    return {
        "symbol": norm_sym,
        "current_price": current_price,
        "timeframes": tf_data,
        "confluence_score": score,
        "dominant_direction": dominant_direction,
        "conviction": conviction,
        "nearest_resistance": levels["nearest_resistance"],
        "nearest_support": levels["nearest_support"],
        "summary": summary,
        "scanned_at": datetime.now(tz=timezone.utc).isoformat(),
    }


async def run_mtf_scan_all_targets() -> Dict[str, Any]:
    """Run MTF confluence scan for all default TARGETS in parallel."""
    results = await asyncio.gather(
        *[run_mtf_confluence_scan(sym) for sym in TARGETS],
        return_exceptions=True,
    )

    output: Dict[str, Any] = {}
    for sym, result in zip(TARGETS, results):
        if isinstance(result, Exception):
            output[sym] = {"error": str(result), "symbol": sym}
        else:
            output[sym] = result

    return output
