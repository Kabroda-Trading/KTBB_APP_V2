# mtf_confluence_scanner.py
# ==============================================================================
# KABRODA MULTI-TIMEFRAME CONFLUENCE SCANNER v2.1
# Purpose: Live 5-timeframe direction vote (15M/1H/4H/Daily/Weekly) with
# StochRSI, EMA21/55 bias, ADX strength, BBWP compression gate, PMARP exit
# protocol, RSI divergence detection, Revin Suite (R-Squared), and unified
# jewel_signal synthesis.
# Runs every 15 minutes via gravity engine loop. Standalone — read-only.
# DO NOT modify battlebox_pipeline.py or any existing file.
# ==============================================================================

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ── Shared data layer ─────────────────────────────────────────────────────
# fetch_live_*, _normalize_symbol, _calc_ema_series, and _calc_adx are in
# market_data.py to break the circular import chain (battlebox_pipeline →
# gravity_engine → mtf_confluence_scanner → battlebox_pipeline).
from market_data import (
    fetch_live_15m,
    fetch_live_1h,
    fetch_live_4h,
    fetch_live_daily,
    _normalize_symbol,
    _calc_ema_series,
    _calc_adx,
)
import gravity_math

# Revin Suite (R-Squared) imports — from bold-hubble package
from indicators.revin_ribbons import calculate_revin_ribbons, analyze_ribbon_state
from indicators.rmo import calculate_rmo, analyze_rmo_state
from indicators.rwp import calculate_rwp, analyze_rwp_state
from indicators.revin_suite_engine import compute_revin_suite

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
# RSI SERIES (O(n) — needed for StochRSI and divergence detection)
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
    ag, al = avg_gain, avg_loss
    for i in range(period, len(closes)):
        if i > period:
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
# BBWP — Bollinger Band Width Percentile
# Gate condition: bbwp_compressed=True means volatility is compressed and an
# expansion move is imminent. The JEWEL system only signals direction when
# at least one timeframe is compressed.
# ------------------------------------------------------------------------------

def _calc_bbwp(candles: List[Dict], period: int = 20, lookback: int = 252) -> Dict[str, Any]:
    """
    Percentile rank of current Bollinger Band width vs the last `lookback` values.
    bbwp_value < 25 = compression — gate open for JEWEL direction signal.
    Falls back to raw band width (not percentile) when fewer than 50 bars available.
    """
    fallback = {"bbwp_value": 50.0, "bbwp_compressed": False}
    closes = [c["close"] for c in candles]
    if len(closes) < period + 1:
        return fallback

    bw_series: List[float] = []
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        sma = sum(window) / period
        if sma == 0.0:
            bw_series.append(0.0)
            continue
        variance = sum((x - sma) ** 2 for x in window) / period
        std = variance ** 0.5
        # (upper_bb - lower_bb) / sma * 100 = (4 * std) / sma * 100
        bw_series.append((4.0 * std) / sma * 100.0)

    if not bw_series:
        return fallback

    current_bw = bw_series[-1]

    if len(bw_series) < 50:
        # Not enough history — return raw band width, flag < 25 as compressed
        return {"bbwp_value": round(current_bw, 4), "bbwp_compressed": current_bw < 25.0}

    # Percentile rank of current_bw vs up to `lookback` historical values
    history = bw_series[-(min(lookback, len(bw_series)) + 1) : -1]
    if not history:
        return {"bbwp_value": round(current_bw, 4), "bbwp_compressed": current_bw < 25.0}

    rank = sum(1 for v in history if v <= current_bw) / len(history) * 100.0
    return {"bbwp_value": round(rank, 2), "bbwp_compressed": rank < 25.0}


# ------------------------------------------------------------------------------
# PMARP — Price Moving Average Ratio Percentile
# Exit protocol: pmarp_overextended=True means price has stretched too far
# from its mean and a mean-reversion pull-back is likely.
# ------------------------------------------------------------------------------

def _calc_pmarp(
    closes: List[float], ema21_series: List[float], lookback: int = 252
) -> Dict[str, Any]:
    """
    Percentile rank of how far current price has deviated from EMA21.
    pmarp_value > 75 = overextended — exit signal.
    ema21_series must be the output of _calc_ema_series(closes, 21):
    length = len(closes) - 20, where ema21_series[i] aligns to closes[i + 20].
    """
    fallback = {"pmarp_value": 50.0, "pmarp_overextended": False, "pmarp_direction": "NEUTRAL"}
    if not closes or not ema21_series:
        return fallback

    # Align: ema21_series[i] corresponds to closes[offset + i]
    offset = len(closes) - len(ema21_series)
    aligned_closes = closes[offset:]

    ratio_series: List[float] = []
    for c, e in zip(aligned_closes, ema21_series):
        ratio_series.append((c - e) / e * 100.0 if e != 0.0 else 0.0)

    if not ratio_series:
        return fallback

    current_ratio = ratio_series[-1]
    direction = "ABOVE" if current_ratio >= 0.0 else "BELOW"

    if len(ratio_series) < 50:
        return {
            "pmarp_value": round(abs(current_ratio), 4),
            "pmarp_overextended": False,
            "pmarp_direction": direction,
        }

    history = ratio_series[-(min(lookback, len(ratio_series)) + 1) : -1]
    if not history:
        return {
            "pmarp_value": round(abs(current_ratio), 4),
            "pmarp_overextended": False,
            "pmarp_direction": direction,
        }

    rank = sum(1 for v in history if v <= current_ratio) / len(history) * 100.0
    return {
        "pmarp_value": round(rank, 2),
        "pmarp_overextended": rank > 75.0,
        "pmarp_direction": direction,
    }


# ------------------------------------------------------------------------------
# RSI DIVERGENCE
# Detects when price and RSI disagree at swing pivots — early exhaustion signal.
# Classic: price makes extreme in one direction, RSI does not confirm.
# Hidden: RSI makes extreme, price does not confirm (trend continuation).
# ------------------------------------------------------------------------------

def _find_pivot_highs(series: List[float], n: int = 3) -> List[Tuple[int, float]]:
    """Indices and values of bars higher than the n bars before AND after."""
    pivots: List[Tuple[int, float]] = []
    for i in range(n, len(series) - n):
        val = series[i]
        if all(val > series[i - j] for j in range(1, n + 1)) and \
           all(val > series[i + j] for j in range(1, n + 1)):
            pivots.append((i, val))
    return pivots


def _find_pivot_lows(series: List[float], n: int = 3) -> List[Tuple[int, float]]:
    """Indices and values of bars lower than the n bars before AND after."""
    pivots: List[Tuple[int, float]] = []
    for i in range(n, len(series) - n):
        val = series[i]
        if all(val < series[i - j] for j in range(1, n + 1)) and \
           all(val < series[i + j] for j in range(1, n + 1)):
            pivots.append((i, val))
    return pivots


def _find_divergence(
    closes: List[float],
    rsi_series: List[float],
    rsi_period: int = 14,
    n: int = 3,
) -> Dict[str, str]:
    """
    Compare last 2 price pivot highs/lows against RSI at those same bars.
    rsi_series[j] aligns to closes[j + rsi_period].
    Strength: STRONG when RSI spread > 5 points, WEAK when 1–5 points.
    """
    fallback = {"divergence": "NONE", "divergence_strength": "NONE"}
    if len(rsi_series) < 20 or len(closes) < rsi_period + n * 2 + 2:
        return fallback

    rsi_offset = len(closes) - len(rsi_series)  # = rsi_period for period=14

    def get_rsi_at(closes_idx: int) -> Optional[float]:
        rsi_idx = closes_idx - rsi_offset
        return rsi_series[rsi_idx] if 0 <= rsi_idx < len(rsi_series) else None

    def _strength(r1: float, r2: float) -> str:
        diff = abs(r2 - r1)
        if diff > 5.0:
            return "STRONG"
        if diff > 1.0:
            return "WEAK"
        return "NONE"

    highs = _find_pivot_highs(closes, n)
    lows = _find_pivot_lows(closes, n)

    if len(highs) >= 2:
        i1, p1 = highs[-2]
        i2, p2 = highs[-1]
        r1, r2 = get_rsi_at(i1), get_rsi_at(i2)
        if r1 is not None and r2 is not None:
            strength = _strength(r1, r2)
            if strength != "NONE":
                if p2 > p1 and r2 < r1:
                    return {"divergence": "BEARISH", "divergence_strength": strength}
                if p2 < p1 and r2 > r1:
                    return {"divergence": "HIDDEN_BEARISH", "divergence_strength": strength}

    if len(lows) >= 2:
        i1, p1 = lows[-2]
        i2, p2 = lows[-1]
        r1, r2 = get_rsi_at(i1), get_rsi_at(i2)
        if r1 is not None and r2 is not None:
            strength = _strength(r1, r2)
            if strength != "NONE":
                if p2 < p1 and r2 > r1:
                    return {"divergence": "BULLISH", "divergence_strength": strength}
                if p2 > p1 and r2 < r1:
                    return {"divergence": "HIDDEN_BULLISH", "divergence_strength": strength}

    return fallback


# ------------------------------------------------------------------------------
# JEWEL SIGNAL — Sequential synthesis of all components
# This is the primary output the Senior Analyst reads. It answers:
# "Is the market ready to move, in which direction, and how confidently?"
# ------------------------------------------------------------------------------

def _build_jewel_signal(
    tf_data: Dict[str, Dict],
    dominant_direction: str,
) -> Dict[str, Any]:
    """
    Sequential Krown logic:
    1. BBWP gate — any timeframe compressed?
    2. Direction from existing confluence vote
    3. Conviction from EMA alignment + StochRSI momentum support
    4. Revin Suite — RWP squeeze as additional gate, RMO divergence as conviction
    5. PMARP exit warning
    6. Divergence warning
    """
    gate_open = any(v.get("bbwp_compressed", False) for v in tf_data.values())
    exit_warning = any(v.get("pmarp_overextended", False) for v in tf_data.values())
    divergence_warning = any(
        v.get("divergence", "NONE") in {"BEARISH", "BULLISH"}
        for v in tf_data.values()
    )

    # ── Revin Suite gates ───────────────────────────────────────────────
    rwp_squeeze = any(v.get("rwp_squeeze", False) for v in tf_data.values())
    rmo_overextended = any(v.get("rmo_overextended", False) for v in tf_data.values())
    rmo_bullish = any(
        v.get("rmo_state") == "BULLISH" for v in tf_data.values()
    )
    rmo_bearish = any(
        v.get("rmo_state") == "BEARISH" for v in tf_data.values()
    )
    revin_gray_dot = any(v.get("revin_gray_dot", False) for v in tf_data.values())
    revin_outer_band = any(v.get("revin_outer_band", False) for v in tf_data.values())

    if not gate_open:
        conviction = "LOW"
    else:
        momentum_target = "UP" if dominant_direction == "BULLISH" else "DOWN"
        direction_aligned = sum(
            1 for v in tf_data.values()
            if v.get("direction_vote") == dominant_direction
        )
        momentum_supporting = sum(
            1 for v in tf_data.values()
            if v.get("stoch_rsi", {}).get("curl") == momentum_target
        )
        # Boost conviction if RWP squeeze confirms the gate
        rwp_boost = 1 if rwp_squeeze else 0
        # Boost conviction if RMO aligns with dominant direction
        rmo_boost = 1 if (
            (dominant_direction == "BULLISH" and rmo_bullish) or
            (dominant_direction == "BEARISH" and rmo_bearish)
        ) else 0
        # STRONG if: (old AND-gate: direction + momentum) OR (Revin boosts substitute for momentum)
        conviction = "STRONG" if (direction_aligned >= 3 and momentum_supporting >= 2) or (direction_aligned >= 3 and (rwp_boost + rmo_boost) >= 1) else "MODERATE"

    if not gate_open:
        summary = "Gate closed — no compression detected, stand down."
    else:
        parts = [f"Gate open. {dominant_direction.capitalize()} bias, {conviction.lower()} conviction."]
        if rwp_squeeze:
            parts.append("RWP squeeze confirms compression — breakout imminent.")
        if rmo_overextended:
            parts.append("RMO overextended — momentum exhaustion warning.")
        if revin_gray_dot:
            parts.append("Revin gray dot tested — support/resistance bounce zone.")
        if revin_outer_band:
            parts.append("Revin outer band touched — extreme price level.")
        if exit_warning:
            parts.append("Exit warning active — overextended on at least one timeframe.")
        if divergence_warning:
            parts.append("Divergence detected — potential reversal signal.")
        if not exit_warning and not divergence_warning:
            parts.append("No exit warnings. Setup clean.")
        summary = " ".join(parts)

    return {
        "gate_open": gate_open,
        "direction": dominant_direction,
        "conviction": conviction,
        "exit_warning": exit_warning,
        "divergence_warning": divergence_warning,
        "rwp_squeeze": rwp_squeeze,
        "rmo_overextended": rmo_overextended,
        "revin_gray_dot": revin_gray_dot,
        "revin_outer_band": revin_outer_band,
        "signal_summary": summary,
    }


# ------------------------------------------------------------------------------
# PER-TIMEFRAME ANALYSIS
# ------------------------------------------------------------------------------

def _analyze_timeframe(candles: List[Dict], label: str) -> Dict[str, Any]:
    """Compute full JEWEL component set for a single timeframe."""
    error_result = {
        "label": label,
        "ema_bias": "UNKNOWN",
        "stoch_rsi": {"k": 50.0, "d": 50.0, "zone": "NEUTRAL", "curl": "FLAT"},
        "adx": 0.0,
        "adx_strength": "WEAK",
        "adx_rising": False,
        "direction_vote": "NEUTRAL",
        "bbwp_value": 50.0,
        "bbwp_compressed": False,
        "pmarp_value": 50.0,
        "pmarp_overextended": False,
        "pmarp_direction": "NEUTRAL",
        "divergence": "NONE",
        "divergence_strength": "NONE",
        "revin_ribbon_zone": "UNKNOWN",
        "revin_gray_dot": False,
        "revin_outer_band": False,
        "revin_midline_direction": "UNKNOWN",
        "rmo_score": 0.0,
        "rmo_state": "NEUTRAL",
        "rmo_overextended": False,
        "rwp_score": 50.0,
        "rwp_state": "NEUTRAL",
        "rwp_squeeze": False,
        "rwp_expansion": False,
        "error": "insufficient_data",
    }

    if len(candles) < 60:
        return error_result

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
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

    bbwp = _calc_bbwp(candles)
    pmarp = _calc_pmarp(closes, ema21)

    rsi_series = _calc_rsi_series(closes)
    divergence = _find_divergence(closes, rsi_series)

    # ── Revin Suite (R-Squared) ─────────────────────────────────────────
    revin_suite = compute_revin_suite(closes, highs, lows)
    current = revin_suite["current"]
    ribbon_state = current["ribbon_state"]
    rmo_state = current["rmo_state"]
    rwp_state = current["rwp_state"]

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
        "bbwp_value": bbwp["bbwp_value"],
        "bbwp_compressed": bbwp["bbwp_compressed"],
        "pmarp_value": pmarp["pmarp_value"],
        "pmarp_overextended": pmarp["pmarp_overextended"],
        "pmarp_direction": pmarp["pmarp_direction"],
        "divergence": divergence["divergence"],
        "divergence_strength": divergence["divergence_strength"],
        # Revin Suite fields
        "revin_ribbon_zone": ribbon_state.get("zone", "UNKNOWN"),
        "revin_gray_dot": ribbon_state.get("gray_dot_tested", False),
        "revin_outer_band": ribbon_state.get("outer_band_tested", False),
        "revin_midline_direction": ribbon_state.get("midline_direction", "UNKNOWN"),
        "revin_midline_price": ribbon_state.get("midline_price"),
        "revin_lower_1s_price": ribbon_state.get("lower_1σ_price"),
        "revin_upper_1s_price": ribbon_state.get("upper_1σ_price"),
        "rmo_score": rmo_state.get("score", 0.0),
        "rmo_state": rmo_state.get("state", "NEUTRAL"),
        "rmo_overextended": rmo_state.get("is_overextended", False),
        "rwp_score": rwp_state.get("score", 50.0),
        "rwp_state": rwp_state.get("state", "NEUTRAL"),
        "rwp_squeeze": rwp_state.get("is_squeeze", False),
        "rwp_expansion": rwp_state.get("is_expansion", False),
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
    compressed = [v["label"] for v in tf_data.values() if v.get("bbwp_compressed")]
    overextended = [v["label"] for v in tf_data.values() if v.get("pmarp_overextended")]
    diverging = [v["label"] for v in tf_data.values() if v.get("divergence", "NONE") != "NONE"]

    # Revin Suite summary fields
    rwp_squeeze_tfs = [v["label"] for v in tf_data.values() if v.get("rwp_squeeze")]
    rmo_bullish_tfs = [v["label"] for v in tf_data.values() if v.get("rmo_state") == "BULLISH"]
    rmo_bearish_tfs = [v["label"] for v in tf_data.values() if v.get("rmo_state") == "BEARISH"]
    rmo_overextended_tfs = [v["label"] for v in tf_data.values() if v.get("rmo_overextended")]
    revin_gray_dot_tfs = [v["label"] for v in tf_data.values() if v.get("revin_gray_dot")]
    revin_outer_band_tfs = [v["label"] for v in tf_data.values() if v.get("revin_outer_band")]

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
    if compressed:
        parts.append(f"BBWP compressed (gate open) on: {', '.join(compressed)}.")
    if rwp_squeeze_tfs:
        parts.append(f"RWP squeeze on: {', '.join(rwp_squeeze_tfs)}.")
    if rmo_bullish_tfs:
        parts.append(f"RMO bullish on: {', '.join(rmo_bullish_tfs)}.")
    if rmo_bearish_tfs:
        parts.append(f"RMO bearish on: {', '.join(rmo_bearish_tfs)}.")
    if rmo_overextended_tfs:
        parts.append(f"RMO overextended on: {', '.join(rmo_overextended_tfs)}.")
    if revin_gray_dot_tfs:
        parts.append(f"Revin gray dot (support test) on: {', '.join(revin_gray_dot_tfs)}.")
    if revin_outer_band_tfs:
        parts.append(f"Revin outer band touched on: {', '.join(revin_outer_band_tfs)}.")
    if overextended:
        parts.append(f"PMARP overextended (exit warning) on: {', '.join(overextended)}.")
    if diverging:
        parts.append(f"RSI divergence on: {', '.join(diverging)}.")
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
    """Run full 5-TF JEWEL scan for a single symbol. Live data only."""
    norm_sym = _normalize_symbol(symbol)

    # 4H bumped to 280 so percentile rank covers full 252-period lookback
    raw_15m, raw_1h, raw_4h, raw_daily = await asyncio.gather(
        fetch_live_15m(norm_sym, limit=300),
        fetch_live_1h(norm_sym, limit=300),
        fetch_live_4h(norm_sym, limit=280),
        fetch_live_daily(norm_sym, limit=500),
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

    any_tf_compressed = any(v.get("bbwp_compressed", False) for v in tf_data.values())
    any_tf_overextended = any(v.get("pmarp_overextended", False) for v in tf_data.values())
    any_tf_divergence = any(v.get("divergence", "NONE") != "NONE" for v in tf_data.values())

    jewel_signal = _build_jewel_signal(tf_data, dominant_direction)

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
        "any_tf_compressed": any_tf_compressed,
        "any_tf_overextended": any_tf_overextended,
        "any_tf_divergence": any_tf_divergence,
        "jewel_signal": jewel_signal,
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
