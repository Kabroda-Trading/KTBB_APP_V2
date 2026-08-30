# htf_fuel.py
# ==============================================================================
# HIGHER-TIMEFRAME FUEL — the gate's own read, not the old fuel-gauge feed.
#
# Ported (2026-08-30) from `Kabroda AI Brain`'s brain/engine/htf_fuel.py, part
# of the KABRODA_REBUILD_SPEC.md core. Replaces the old 1H/4H "fuel gauge"
# machinery in battlebox_pipeline.py/kabroda_mas_flow.py — that fed context
# to a decision layer that no longer exists. This is computed fresh from
# candles every time, deterministic and auditable, no dependency on the old
# feed. It also replaces the old independent 1H/4H trading system entirely —
# HTF is an input to the 15M gate now, it doesn't trade on its own.
#
# CALIBRATION.md §12 (Kabroda AI Brain repo): on 1,913 trigger-breaks, 1H+4H
# trend alignment does NOT change whether a break reaches T1 (~60% either
# way) — it changes how far the winners run (avg MFE 1.72R at 0 aligned ->
# 2.57R at 2 aligned). So HTF fuel is CARRY fuel — it belongs in the gate as
# an entry condition (>=1 required) and drives the PREMIUM tier (both
# aligned), not a win-rate filter on its own.
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, List

from market_data import _calc_ema_series

_LONG, _SHORT = "LONG", "SHORT"


def timeframe_trend(tf_candles: List[Dict[str, Any]]) -> str:
    """BULLISH / BEARISH / NEUTRAL for one timeframe from its own candles.

    9/21 EMA stack + the fast-EMA slope over the last 6 bars; both must agree
    or it's NEUTRAL (don't over-call a chop)."""
    closes = [float(c["close"]) for c in tf_candles or []]
    if len(closes) < 25:
        return "NEUTRAL"
    e_fast = _calc_ema_series(closes, 9)
    e_slow = _calc_ema_series(closes, 21)
    if not e_fast or not e_slow or len(e_fast) < 6:
        return "NEUTRAL"
    stacked_up = e_fast[-1] > e_slow[-1]
    slope_up = e_fast[-1] > e_fast[-6]
    if stacked_up and slope_up:
        return "BULLISH"
    if (not stacked_up) and (not slope_up):
        return "BEARISH"
    return "NEUTRAL"


def htf_fuel(candles_1h: List[Dict[str, Any]], candles_4h: List[Dict[str, Any]],
             side: str) -> Dict[str, Any]:
    """How much higher-timeframe carry backs a `side` (LONG/SHORT) break."""
    t1h = timeframe_trend(candles_1h)
    t4h = timeframe_trend(candles_4h)
    want = "BULLISH" if side == _LONG else "BEARISH"
    against = "BEARISH" if side == _LONG else "BULLISH"
    aligned = sum(1 for t in (t1h, t4h) if t == want)
    opposed = sum(1 for t in (t1h, t4h) if t == against)
    return {
        "trend_1h": t1h, "trend_4h": t4h,
        "aligned": aligned, "opposed": opposed,
        "carry": "STRONG" if aligned == 2 else ("SOME" if aligned == 1 else "NONE"),
        "note": f"1H {t1h} / 4H {t4h} - {aligned}/2 back the {side}",
    }
