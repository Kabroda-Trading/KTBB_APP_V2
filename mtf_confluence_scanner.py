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
    _calc_bbwp,
    _calc_pmarp,
)

# Three Drives / Revin Suite (revin_ribbons, rmo, rwp, revin_suite_engine)
# removed 2026-08-17 -- Kabroda Audit AUDIT_FINDINGS.md #1-3/#5: all four
# confirmed byte-for-byte fabricated formulas with false Krown attribution,
# duplicating what's now properly sourced in Trading Knowledge/knowledge/.
# See REBUILD_PLAN.md. _analyze_timeframe() below now returns the same
# neutral placeholder values its own error_result already used for
# insufficient-data cases -- no downstream consumer needed a code change.

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
# BBWP / PMARP now live in market_data.py (imported above), shared with
# battlebox_pipeline.py -- this file used to carry its own separate,
# never-corrected copy (period=20, EMA21-based PMARP, no real zone
# thresholds) that drifted silently after battlebox_pipeline.py got the real
# fix on 2026-08-17. Found and fixed 2026-08-26 (Phase 4 build). The wrapper
# below adapts the shared functions' plain-float return into the
# dict-with-zone-labels shape this file's callers expect, using the real,
# citation-backed zones (EXTERNAL_VALIDATION_REPORT.md, 2026-08-26): BBWP
# <=38/>=75, PMARP >=85 overextended -- not the old, wrong 25.0/75.0 split.
# ------------------------------------------------------------------------------

def _bbwp_reading(closes: List[float]) -> Dict[str, Any]:
    val = _calc_bbwp(closes)
    return {"bbwp_value": val, "bbwp_compressed": val <= 38.0}


def _pmarp_reading(candles: List[Dict], closes: List[float], ema21_series: List[float]) -> Dict[str, Any]:
    val = _calc_pmarp(candles)
    direction = "ABOVE" if (closes and ema21_series and closes[-1] >= ema21_series[-1]) else "BELOW"
    return {"pmarp_value": val, "pmarp_overextended": val >= 85.0, "pmarp_direction": direction}


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


# _build_jewel_signal() removed 2026-08-30 -- the old "Krown/JEWEL" sequential
# scoring system (its own comment called out the "old AND-gate"). Andy's call:
# strip it out entirely, not just stop calling it -- it was never validated
# and doesn't feed the real gate (decision_engine.py). jewel_specialist.py
# (which existed only to snapshot this system's output 6x/day),
# JewelSnapshotLog, the confluence_score/dominant_direction vote-tally below,
# and templates/confluence.html are all removed alongside it.

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
        "three_drives": [],
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

    bbwp = _bbwp_reading(closes)
    pmarp = _pmarp_reading(candles, closes, ema21)

    rsi_series = _calc_rsi_series(closes)
    divergence = _find_divergence(closes, rsi_series)

    # Revin Suite (ribbons/RMO/RWP) + Three Drives removed 2026-08-17 --
    # confirmed fabricated, see import-block comment above. Neutral
    # placeholders below match error_result's own shape exactly.
    ribbon_state = {"zone": "UNKNOWN", "gray_dot_tested": False, "outer_band_tested": False,
                     "midline_direction": "UNKNOWN", "midline_price": None,
                     "lower_1σ_price": None, "upper_1σ_price": None}
    rmo_state = {"score": 0.0, "state": "NEUTRAL", "is_overextended": False}
    rwp_state = {"score": 50.0, "state": "NEUTRAL", "is_squeeze": False, "is_expansion": False}
    three_drives_result = []

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
        "three_drives": three_drives_result,
    }


# _find_key_levels() / _build_summary() removed 2026-08-30 -- both existed
# only to feed the old confluence vote-tally narrative below (nearest_
# resistance/support, plain-English summary strings), no remaining caller.

# ------------------------------------------------------------------------------
# MAIN SCAN FUNCTIONS
# ------------------------------------------------------------------------------

async def run_mtf_confluence_scan(symbol: str) -> Dict[str, Any]:
    """Run the real, per-timeframe indicator scan (EMA/BBWP/PMARP/StochRSI/
    ADX/divergence) for a symbol. Live data only.

    2026-08-30: the old confluence vote-tally (bull/bear count across 5 TFs
    -> confluence_score/dominant_direction/conviction) and the JEWEL signal
    built on top of it are removed -- Andy's call, strip it out entirely.
    Neither ever fed the real gate (decision_engine.py only reads
    timeframes["15M"]["divergence"] from this scan's output, for the real
    15M-divergence hard veto) and the vote-tally was never validated --
    KABRODA_REBUILD_SPEC.md explicitly names this pattern (a multi-factor
    point tally producing the trade call) as the thing that lost money on
    kabroda.com's own real trades. _analyze_timeframe()'s real indicator
    math is untouched below -- that's legitimate, corrected, still-needed
    infrastructure, not part of what's being cut."""
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

    return {
        "symbol": norm_sym,
        "current_price": current_price,
        "timeframes": tf_data,
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
