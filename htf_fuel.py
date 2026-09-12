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


# ==============================================================================
# KROWN CROSS — the v2 gate's trend condition (Kabroda AI Brain repo,
# CC_PACKAGE.md 2026-09-11, d1_meas6_combo.py::cross_state()). A SEPARATE,
# stricter EMA pair from timeframe_trend()'s 9/21 above -- deliberately not a
# parameter change to that function, since v2's measured population requires
# BOTH the 9/21 aligned>=1 read (still evaluated below, unchanged) AND this
# 21/55 read to agree; the two pairs can and do disagree on some crosses.
# "Krown Cross" is the trading library's own name for this dominant-trend
# definition (RESEARCH_BRIEFS.md-adjacent, Kabroda AI Brain repo).
# ==============================================================================

def krown_cross_state(tf_candles: List[Dict[str, Any]], want_bullish: bool) -> bool:
    """True if this timeframe's 21/55 EMA stack + 6-bar fast-EMA slope both
    agree with `want_bullish`. Unlike timeframe_trend(), this has no NEUTRAL
    state -- it's a single boolean "does this vote for the side" (matches
    d1_meas6_combo.py's cross_state() exactly: `(e21[-1]>e55[-1])==want_bull
    and (e21[-1]>e21[-6])==want_bull`)."""
    closes = [float(c["close"]) for c in tf_candles or []]
    e21 = _calc_ema_series(closes, 21)
    e55 = _calc_ema_series(closes, 55)
    if not e21 or not e55 or len(e21) < 7:
        return False
    return (e21[-1] > e55[-1]) == want_bullish and (e21[-1] > e21[-6]) == want_bullish


def krown_cross_votes(candles_1h: List[Dict[str, Any]], candles_4h: List[Dict[str, Any]],
                       side: str) -> Dict[str, Any]:
    """votes==2 (both 1H and 4H krown_cross_state agree with `side`) is the
    v2 gate's trend condition. votes in {0, 1, 2}."""
    want_bullish = side == _LONG
    s1 = krown_cross_state(candles_1h, want_bullish)
    s4 = krown_cross_state(candles_4h, want_bullish)
    votes = int(s1) + int(s4)
    return {"votes": votes, "cross_1h": s1, "cross_4h": s4}
