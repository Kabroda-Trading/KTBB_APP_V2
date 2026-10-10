"""Unit coverage for alt_matrix_management.py -- the pure D3 function,
mirroring mgmt_e1_stack.advance()'s own test style (hand-built candle
scenarios, injected EMA series for deterministic trail-exit checks)."""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import alt_matrix_management as amm

UTC = datetime.timezone.utc
ENTRY_TIME = datetime.datetime(2026, 10, 9, 0, 0, 0, tzinfo=UTC)
ENTRY_EPOCH = int(ENTRY_TIME.timestamp())


def _bar(close, high=None, low=None, offset_bars=1):
    high = high if high is not None else close
    low = low if low is not None else close
    return {"time": ENTRY_EPOCH + offset_bars * 14400, "close": close, "high": high, "low": low}


def _base_order(**overrides):
    d = dict(entry_price=100.0, stop_price=90.0, r_distance=10.0, entry_fill_time=ENTRY_TIME, be_amended=False)
    d.update(overrides)
    return d


# ------------------------------------------------------------------ mfe_through

def test_mfe_through_tracks_running_max_from_bar_highs():
    candles = [_bar(105.0, high=108.0, offset_bars=1), _bar(103.0, high=104.0, offset_bars=2)]
    assert amm.mfe_through(candles, entry_price=100.0, r_distance=10.0) == pytest_approx(0.8)


def pytest_approx(x):
    import pytest
    return pytest.approx(x)


def test_mfe_through_zero_r_distance_is_zero_not_a_crash():
    assert amm.mfe_through([_bar(105.0)], entry_price=100.0, r_distance=0.0) == 0.0


# ------------------------------------------------------------------ advance() -- missing/invalid inputs fail closed to None

def test_advance_missing_entry_price_returns_none():
    order = _base_order(entry_price=None)
    assert amm.advance(order, [_bar(105.0)], ENTRY_TIME) is None


def test_advance_missing_r_distance_returns_none_never_guesses():
    order = _base_order(r_distance=None)
    assert amm.advance(order, [_bar(105.0)], ENTRY_TIME) is None


def test_advance_no_bars_since_entry_returns_none():
    order = _base_order()
    old_bar = {"time": ENTRY_EPOCH - 14400, "close": 100.0, "high": 101.0, "low": 99.0}
    assert amm.advance(order, [old_bar], ENTRY_TIME) is None


def test_advance_flat_price_action_stays_open():
    order = _base_order()
    candles = [_bar(100.0, high=101.0, low=99.0, offset_bars=i) for i in range(1, 5)]
    result = amm.advance(order, candles, ENTRY_TIME)
    assert result is None


# ------------------------------------------------------------------ STOP / BE_STOP priority (checked before amendment/trail on the same bar)

def test_advance_stop_touch_before_amendment_exits_stop_not_amend():
    order = _base_order()   # stop=90, entry=100, r_distance=10
    # This single bar's high would reach MFE 2.0R (high=120 -> (120-100)/10=2.0)
    # AND its own low touches the stop (90) -- stop must win, per the
    # backtest's own per-bar order (stop checked before the MFE-threshold
    # branch).
    candles = [_bar(95.0, high=120.0, low=88.0, offset_bars=1)]
    result = amm.advance(order, candles, ENTRY_TIME)
    assert result == {"action": "EXIT", "exit_reason": "STOP", "exit_price": 90.0, "exit_time": amm._epoch_to_dt(candles[0]["time"])}


def test_advance_stop_touch_after_amendment_is_be_stop_not_stop():
    order = _base_order(be_amended=True, stop_price=100.1)   # already amended to BE (entry + 0.1R)
    candles = [_bar(99.0, high=101.0, low=100.0, offset_bars=1)]   # low touches the BE stop exactly
    result = amm.advance(order, candles, ENTRY_TIME)
    assert result["action"] == "EXIT"
    assert result["exit_reason"] == "BE_STOP"
    assert result["exit_price"] == 100.1


# ------------------------------------------------------------------ AMEND_TO_BE trigger

def test_advance_amendment_fires_the_first_bar_mfe_reaches_2r():
    order = _base_order()   # entry=100, stop=90, r_distance=10 -- 2R = 120
    candles = [
        _bar(110.0, high=115.0, low=105.0, offset_bars=1),   # MFE 1.5R -- not yet
        _bar(118.0, high=121.0, low=112.0, offset_bars=2),   # high 121 -> MFE 2.1R -- fires here
    ]
    result = amm.advance(order, candles, ENTRY_TIME)
    assert result["action"] == "AMEND_TO_BE"
    assert result["be_price"] == pytest_approx(100.0 + 0.1 * 10.0)   # entry + 0.1R = 101.0
    assert result["at_time"] == amm._epoch_to_dt(candles[1]["time"])


def test_advance_already_amended_never_fires_amend_again_even_if_mfe_still_high():
    order = _base_order(be_amended=True, stop_price=101.0)
    candles = [_bar(125.0, high=130.0, low=120.0, offset_bars=1)]   # MFE way past 2R
    result = amm.advance(order, candles, ENTRY_TIME)
    assert result is None or result["action"] != "AMEND_TO_BE"


def test_advance_amendment_checked_before_trail_exit_on_the_same_bar():
    # A bar that BOTH crosses MFE>=2R for the first time AND closes below
    # EMA21 -- the amendment must fire first (the design's own documented
    # "return on first hit" simplification); the trail condition gets
    # re-checked on the NEXT call after the caller applies the amendment.
    order = _base_order(ema21_series=[200.0], ema55_series=[80.0])   # close will be well below this ema21
    candles = [_bar(115.0, high=121.0, low=110.0, offset_bars=1)]   # high 121 -> MFE 2.1R; close 115 < ema21 200
    result = amm.advance(order, candles, ENTRY_TIME)
    assert result["action"] == "AMEND_TO_BE"   # not EMA21_TRAIL, even though close < ema21 on this same bar


# ------------------------------------------------------------------ TRAIL EXIT -- EMA21 (MFE>=2R) vs EMA55 (MFE<2R)

def test_advance_ema21_trail_exit_once_already_amended_and_mfe_ge_2r():
    order = _base_order(be_amended=True, stop_price=101.0, ema21_series=[150.0], ema55_series=[50.0])
    # high keeps MFE >= 2R (already true going in isn't tracked across
    # calls -- this call's own bar must itself show MFE>=2R to re-derive it)
    candles = [_bar(140.0, high=145.0, low=135.0, offset_bars=1)]   # MFE (145-100)/10=4.5R; close 140 < ema21 150
    result = amm.advance(order, candles, ENTRY_TIME)
    assert result == {"action": "EXIT", "exit_reason": "EMA21_TRAIL", "exit_price": 140.0, "exit_time": amm._epoch_to_dt(candles[0]["time"])}


def test_advance_ema55_close_exit_when_mfe_below_2r():
    order = _base_order(ema21_series=[200.0], ema55_series=[115.0])
    candles = [_bar(110.0, high=112.0, low=108.0, offset_bars=1)]   # MFE (112-100)/10=1.2R < 2R; close 110 < ema55 115
    result = amm.advance(order, candles, ENTRY_TIME)
    assert result == {"action": "EXIT", "exit_reason": "EMA55_CLOSE", "exit_price": 110.0, "exit_time": amm._epoch_to_dt(candles[0]["time"])}


def test_advance_no_trail_exit_when_close_stays_above_the_relevant_ema():
    order = _base_order(ema21_series=[50.0], ema55_series=[50.0])
    candles = [_bar(110.0, high=112.0, low=108.0, offset_bars=1)]   # close well above both EMAs
    result = amm.advance(order, candles, ENTRY_TIME)
    assert result is None


def test_advance_computes_ema_series_itself_when_not_provided():
    # No ema21_series/ema55_series in the order dict -- advance() must
    # fall back to computing them itself via alt_matrix_signals.ema_series(),
    # not crash or silently skip the trail check.
    order = _base_order()
    candles = [_bar(c, high=c + 1, low=c - 1, offset_bars=i) for i, c in enumerate(range(100, 80, -2), start=1)]
    result = amm.advance(order, candles, ENTRY_TIME)
    # A sharp, sustained decline with no real MFE ever reached must
    # eventually trail-exit via EMA55_CLOSE once the close falls far
    # enough below its own (fast-reacting, short-history) EMA.
    assert result is not None
    assert result["action"] == "EXIT"
