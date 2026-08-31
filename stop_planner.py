# stop_planner.py
# ==============================================================================
# 24H CORE-ZONE STOP PLANNER
# KABRODA_COM_TRADE_PLAN_SPEC.md SS6, ORDER_MECHANICS.md SS5 (Kabroda AI Brain
# repo). Computes the EXECUTION stop for the Trade Plan's order brief -- the
# price Andy actually places at the exchange.
#
# This is a SEPARATE, additive stop. It does NOT flow into CampaignLog.
# stop_loss and is NOT the risk basis for any R-multiple in the system --
# the gate log, the runner mechanic (_frac_r, T1/runner-stop/T3), and every
# backtest R label all stay on the existing r30-based stop
# (decision_engine.py's r30 -+ STOP_BUFFER_BOX*box). See docs/
# STOP_BASIS_ANSWER.md in the Kabroda AI Brain repo for the full "why two
# stops" rationale (confirmed with Andy directly, 2026-08-31, before writing
# this file) -- do not let this module's output touch stop_loss anywhere.
#
# Why it's wider than r30: r30 only sees the first 30 minutes of the
# session. The 24h core zone asks a bigger question -- where is ALL the
# structure from the last 24h a retrace would test. Andy's own 2026-08-29
# audit (his 79,992 wick stop vs the Brain's 79,450 stop-in-price-action)
# is explained by this: the overnight sweep wick that set the right level
# is invisible to r30, which only looks at the session's own opening range.
#
# Honest caveat (Andy's own framing, 2026-08-31): the CONCEPT is
# directionally validated -- wide-stop-beyond-core-zone survived 32/39 fake
# sessions (82%, +0.85R average) vs a much higher wick-hunt rate for a
# tight stop -- but the exact buffer distance (0.1-0.15 ATR here, using the
# midpoint) and the zone-detection thresholds below are forward-test-
# tunable parameters, not backtested gospel. That's what stop_dist_atr in
# the daily log (spec SS9a) and the drift check (SS9b) are for.
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Buffer distance beyond the nearest zone, in units of daily ATR14. Spec
# gives a 0.1-0.15 ATR range; using the midpoint as the starting parameter.
BUFFER_ATR = 0.125

# Fallback stop distance (in daily ATR14) when no distinct 24h zone exists
# in the trade's direction.
FALLBACK_ATR_MULT = 1.5

# A candle's wick counts as a "sweep" zone when it's at least this fraction
# of the candle's total high-low range -- distinguishes a real liquidity
# sweep (long wick, small body) from an ordinary candle.
SWEEP_WICK_RATIO = 0.5


def _find_swing_points(
    candles: List[Dict[str, Any]], left: int = 3, right: int = 3
) -> Tuple[List[float], List[float]]:
    """ALL confirmed swing highs/lows in the window -- not just the most
    recent one (sse_engine._find_pivots() only returns the last, which
    isn't enough here: the nearest zone to price may not be the most
    recent pivot). Same left/right confirmation logic as sse_engine.py's
    pivot detector, reused for consistency rather than reinvented."""
    highs: List[float] = []
    lows: List[float] = []
    if len(candles) < (left + right + 1):
        return highs, lows

    for i in range(left, len(candles) - right):
        curr = candles[i]
        ch = float(curr["high"])
        cl = float(curr["low"])

        if all(float(candles[i - j]["high"]) <= ch for j in range(1, left + 1)) and \
           all(float(candles[i + j]["high"]) < ch for j in range(1, right + 1)):
            highs.append(ch)

        if all(float(candles[i - j]["low"]) >= cl for j in range(1, left + 1)) and \
           all(float(candles[i + j]["low"]) > cl for j in range(1, right + 1)):
            lows.append(cl)

    return highs, lows


def _find_sweep_wicks(
    candles: List[Dict[str, Any]], wick_ratio: float = SWEEP_WICK_RATIO
) -> Tuple[List[float], List[float]]:
    """Candles whose wick is a large fraction of the total range -- a
    liquidity-sweep zone (price probed beyond a level and closed back
    inside), distinct from a confirmed swing pivot: no left/right
    confirmation needed, just the single candle's own shape."""
    upper: List[float] = []
    lower: List[float] = []

    for c in candles:
        o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
        rng = h - l
        if rng <= 0:
            continue
        body_top, body_bot = max(o, cl), min(o, cl)
        upper_wick = h - body_top
        lower_wick = body_bot - l

        if upper_wick / rng >= wick_ratio:
            upper.append(h)
        if lower_wick / rng >= wick_ratio:
            lower.append(l)

    return upper, lower


def plan_stop(
    candles_24h: List[Dict[str, Any]],
    entry_price: float,
    is_long: bool,
    r30_high: float,
    r30_low: float,
    f24_vah: float,
    f24_val: float,
    daily_atr14: float,
    buffer_atr: float = BUFFER_ATR,
    fallback_atr_mult: float = FALLBACK_ATR_MULT,
) -> Dict[str, Any]:
    """Computes the 24h core-zone execution stop for one side of a trade.

    candles_24h should be the SAME 24h 5m slice battlebox_pipeline.py
    already builds for the TPO value-area calc (context_24h in
    _compute_sse_packet) -- same window, same data, so the value area and
    the stop planner never disagree about what "the last 24h" means.

    Returns {"stop_price", "stop_basis", "stop_dist_atr", "zone_price"}.
    zone_price is None on the fallback path (no distinct zone found).
    """
    if daily_atr14 <= 0:
        # Can't buffer or measure distance without a real ATR. Caller should
        # treat this as "stop planner unavailable" (matches decision_
        # engine.py's own ATR-guard convention elsewhere in this codebase).
        return {
            "stop_price": None, "stop_basis": "unavailable: daily_atr14 <= 0",
            "stop_dist_atr": None, "zone_price": None,
        }

    swing_highs, swing_lows = _find_swing_points(candles_24h)
    sweep_upper, sweep_lower = _find_sweep_wicks(candles_24h)
    buffer = buffer_atr * daily_atr14

    if is_long:
        candidates: List[Tuple[float, str]] = []
        candidates += [(lvl, "24h swing low") for lvl in swing_lows if lvl < entry_price]
        if f24_val < entry_price:
            candidates.append((f24_val, "24h value area low (f24 VAL)"))
        if r30_low < entry_price:
            candidates.append((r30_low, "30M range low"))
        candidates += [(lvl, "sweep wick low") for lvl in sweep_lower if lvl < entry_price]

        if candidates:
            # Nearest zone BELOW entry = the largest of the below-entry candidates.
            zone_price, zone_label = max(candidates, key=lambda x: x[0])
            stop_price = zone_price - buffer
            stop_basis = f"beyond {zone_label} (${zone_price:,.2f}), -{buffer_atr:.3f}xATR buffer"
        else:
            zone_price = None
            stop_price = entry_price - fallback_atr_mult * daily_atr14
            stop_basis = f"fallback: {fallback_atr_mult}xATR (no distinct 24h zone found below entry)"
    else:
        candidates = []
        candidates += [(lvl, "24h swing high") for lvl in swing_highs if lvl > entry_price]
        if f24_vah > entry_price:
            candidates.append((f24_vah, "24h value area high (f24 VAH)"))
        if r30_high > entry_price:
            candidates.append((r30_high, "30M range high"))
        candidates += [(lvl, "sweep wick high") for lvl in sweep_upper if lvl > entry_price]

        if candidates:
            # Nearest zone ABOVE entry = the smallest of the above-entry candidates.
            zone_price, zone_label = min(candidates, key=lambda x: x[0])
            stop_price = zone_price + buffer
            stop_basis = f"beyond {zone_label} (${zone_price:,.2f}), +{buffer_atr:.3f}xATR buffer"
        else:
            zone_price = None
            stop_price = entry_price + fallback_atr_mult * daily_atr14
            stop_basis = f"fallback: {fallback_atr_mult}xATR (no distinct 24h zone found above entry)"

    stop_dist_atr = abs(entry_price - stop_price) / daily_atr14

    return {
        "stop_price": round(stop_price, 2),
        "stop_basis": stop_basis,
        "stop_dist_atr": round(stop_dist_atr, 4),
        "zone_price": round(zone_price, 2) if zone_price is not None else None,
    }


def rr_floor_ok(entry_price: float, stop_price: float, t1: float, is_long: bool, floor: float = 1.0) -> Dict[str, Any]:
    """The spec's R:R sanity check (SS6 point 5 / Andy's 'SS6.5'): T1
    distance / execution-stop distance must be >= floor (default 1:1), or
    the plan degrades tier or becomes NO_PLAN. This is a check ONLY -- it
    does not decide tier/NO_PLAN itself; that belongs to the Trade Plan
    builder (spec SS3), which also knows the gate's own tier.

    Deliberately takes entry/stop/t1 rather than a whole TradePlan/gate
    object -- keeps this module's only dependency the numbers themselves,
    matching reachability.py/fuel_gate.py's pattern of small, single-
    purpose functions."""
    stop_dist = abs(entry_price - stop_price)
    t1_dist = abs(t1 - entry_price)
    if stop_dist <= 0:
        return {"ok": False, "ratio": None, "reason": "zero or invalid stop distance"}
    ratio = t1_dist / stop_dist
    return {"ok": ratio >= floor, "ratio": round(ratio, 4)}
