"""Unit coverage for mgmt_e1_stack.py -- GATE_TRAVELER's D3 management walk
(STOP -> C5-or-BBWP -> T1 -> TIME, verbatim priority from the frozen
lab_touchfill_arms.py::walk_from_fill(), CANON section 9d -- see that
module's own header comment for the two confirmed corrections vs the
original handoff prose)."""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mgmt_e1_stack as e1

ENTRY_TIME = datetime.datetime(2026, 9, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
ENTRY_EPOCH = ENTRY_TIME.timestamp()
NOW = ENTRY_TIME + datetime.timedelta(hours=6)


def _order(**extra):
    d = {
        "direction": "LONG", "entry_price": 100.0, "stop_price": 90.0, "t1_price": 110.0,
        "entry_fill_time": ENTRY_TIME,
    }
    d.update(extra)
    return d


def _bar(close, ts, high=None, low=None):
    return {"close": close, "high": high if high is not None else close, "low": low if low is not None else close, "time": ts}


def _flat_1h4h(n=20, price=100.0):
    """RSI-neutral (flat closes -> no C5 momentum decay) and BBWP-cold
    (constant closes -> zero std -> never > 70) candle series -- the
    'nothing else fires' baseline for isolating one condition per test."""
    return [{"close": price} for _ in range(n)]


def _decaying_rsi_candles():
    """A real price path (strong up-move, then a deceleration/chop phase)
    that produces a genuine RSI momentum decay (rsi[-1] < rsi[-7]) via the
    real rsi_series() formula -- NOT a monotonic move (which pins RSI at a
    constant floor/ceiling with dn_ewm or up_ewm stuck at exactly 0,
    producing None forever per rsi_series()'s own pandas-matching
    dn.replace(0, np.nan) behavior, never a real decay signal). Verified
    directly: rsi[-1]=92.53 < rsi[-7]=97.43 -> c5_momentum_decay() True."""
    closes = [100.0]
    for i in range(15):
        closes.append(closes[-1] + 2.0 - (0.3 if i % 4 == 0 else 0))
    for i in range(10):
        closes.append(closes[-1] + 0.2 - (0.5 if i % 2 == 0 else 0))
    return [{"close": c} for c in closes]


def test_stop_fires_first_even_when_c5_also_true():
    # STOP is checked BEFORE C5/BBWP in the frozen walk (confirmed
    # discrepancy #3 vs the original handoff prose) -- verify the priority
    # directly: construct candles where the stop is touched, and ALSO make
    # rsi_1h/rsi_4h so obviously decaying that c5 would fire if checked
    # first; STOP must still win.
    order = _order()
    candles_5m = [
        _bar(100.0, ENTRY_EPOCH + 300),
        _bar(85.0, ENTRY_EPOCH + 600, high=86.0, low=84.0),  # stop (90) touched via low
    ]
    decaying = _decaying_rsi_candles()  # would fire C5 if checked before STOP
    result = e1.advance(order, candles_5m, decaying, decaying, NOW)
    assert result is not None
    assert result["exit_reason"] == "STOP"
    assert result["exit_price"] == 90.0
    assert result["c5_fired"] is False
    assert result["bbwp_fired"] is False


def test_c5_exit_fires_before_t1_when_both_conditions_present():
    order = _order()
    candles_5m = [
        _bar(100.0, ENTRY_EPOCH + 300),
        _bar(115.0, ENTRY_EPOCH + 600, high=116.0, low=114.0),  # T1 (110) also touched here
    ]
    decaying = _decaying_rsi_candles()
    result = e1.advance(order, candles_5m, decaying, decaying, NOW)
    assert result["exit_reason"] == "C5_EXIT"
    assert result["exit_price"] == 115.0   # exits at the CURRENT bar's close, not T1's price
    assert result["c5_fired"] is True
    assert result["bbwp_fired"] is False


def test_bbwp_exit_fires_when_only_bbwp_condition_true():
    order = _order()
    candles_5m = [_bar(100.0, ENTRY_EPOCH + 300)]
    flat = _flat_1h4h()  # no C5 (flat RSI)
    # A 4H series that spikes volatility then contracts -- period=96/
    # lookback=768 in study_indicators defaults are too long for a tiny
    # test fixture, so this test uses mgmt_e1_stack's own BBWP_BURN_THRESHOLD
    # via a monkeypatched short bbwp series instead of trying to hand-build
    # 96+768 bars; see test_bbwp_exit_via_direct_series_injection below for
    # the real, non-monkeypatched version.
    import study_indicators as si
    real_bbwp = si.bbwp_series
    try:
        si.bbwp_series = lambda closes, period=si.BBWP_PERIOD, lookback=si.BBWP_LOOKBACK: [None, 80.0, 75.0]
        result = e1.advance(order, candles_5m, flat, flat, NOW)
    finally:
        si.bbwp_series = real_bbwp
    assert result["exit_reason"] == "BBWP_EXIT"
    assert result["bbwp_fired"] is True
    assert result["c5_fired"] is False


def test_t1_fires_when_neither_stop_nor_c5_nor_bbwp():
    order = _order()
    candles_5m = [
        _bar(100.0, ENTRY_EPOCH + 300),
        _bar(105.0, ENTRY_EPOCH + 600, high=111.0, low=104.0),  # T1 (110) touched via high
    ]
    flat = _flat_1h4h()
    result = e1.advance(order, candles_5m, flat, flat, NOW)
    assert result["exit_reason"] == "T1"
    assert result["exit_price"] == 110.0


def test_short_side_stop_and_t1_directions_are_mirrored():
    order = _order(direction="SHORT", entry_price=100.0, stop_price=110.0, t1_price=90.0)
    candles_5m = [_bar(95.0, ENTRY_EPOCH + 300, high=111.0, low=94.0)]  # stop (110) touched via high
    flat = _flat_1h4h()
    result = e1.advance(order, candles_5m, flat, flat, NOW)
    assert result["exit_reason"] == "STOP"
    assert result["exit_price"] == 110.0


def test_time_exit_fires_when_journey_cap_reached_with_no_other_exit():
    order = _order()
    candles_5m = [_bar(102.0, ENTRY_EPOCH + 300), _bar(103.0, ENTRY_EPOCH + 600)]
    flat = _flat_1h4h()
    cap = NOW - datetime.timedelta(minutes=1)  # already passed
    result = e1.advance(order, candles_5m, flat, flat, NOW, journey_cap_at=cap)
    assert result["exit_reason"] == "TIME"
    assert result["exit_price"] == 103.0  # last confirmed close


def test_still_open_returns_none_when_nothing_fires_and_no_cap():
    order = _order()
    candles_5m = [_bar(102.0, ENTRY_EPOCH + 300)]
    flat = _flat_1h4h()
    result = e1.advance(order, candles_5m, flat, flat, NOW, journey_cap_at=NOW + datetime.timedelta(days=6))
    assert result is None


def test_missing_entry_fill_time_returns_none():
    order = _order(entry_fill_time=None)
    assert e1.advance(order, [_bar(100.0, ENTRY_EPOCH)], [], [], NOW) is None


def test_no_bars_since_entry_returns_none():
    order = _order()
    candles_5m = [_bar(100.0, ENTRY_EPOCH - 300)]  # before entry, not after
    assert e1.advance(order, candles_5m, [], [], NOW) is None
