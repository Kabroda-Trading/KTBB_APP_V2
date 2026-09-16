"""Unit coverage for mgmt_split_dry_run.py -- v1/v2's real MGMT_SPLIT
management rule (50% off at T1, stop stays at the original level, the
runner exits at T3 or its own stop, the stop NEVER moves), simulated from
candles for DRY_RUN orders with no real exchange position to poll
(Ruling B, DeepSeek, relayed by Andy 2026-09-15)."""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mgmt_split_dry_run as split

ENTRY_TIME = datetime.datetime(2026, 9, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
ENTRY_EPOCH = ENTRY_TIME.timestamp()


def _order(**extra):
    d = {
        "direction": "LONG", "entry_price": 100.0, "stop_price": 90.0,
        "t1_price": 110.0, "t3_price": 130.0,
        "entry_fill_time": ENTRY_TIME,
        "t1_status": None, "t1_fill_price": None, "t1_fill_time": None, "t1_leg_r": None,
    }
    d.update(extra)
    return d


def _bar(close, ts, high=None, low=None):
    return {"close": close, "high": high if high is not None else close, "low": low if low is not None else close, "time": ts}


def test_stop_before_t1_is_a_clean_full_loss():
    order = _order()
    candles = [_bar(85.0, ENTRY_EPOCH + 300, high=95.0, low=84.0)]  # stop (90) touched via low, T1 never reached
    result = split.advance(order, candles)
    assert result["management_state"] == "CLOSED_STOP_BEFORE_T1"
    assert result["close_reason"] == "STOP_BEFORE_T1"
    assert result["exit_reason"] == "STOP_BEFORE_T1"
    assert result["exit_price"] == 90.0
    assert result["realized_pnl_r"] == -1.0


def test_stop_wins_priority_when_both_stop_and_t1_touch_same_bar():
    order = _order()
    candles = [_bar(100.0, ENTRY_EPOCH + 300, high=112.0, low=88.0)]  # both stop (90) and T1 (110) touched in one bar
    result = split.advance(order, candles)
    assert result["management_state"] == "CLOSED_STOP_BEFORE_T1"


def test_t1_fill_with_runner_still_open_returns_partial_only():
    order = _order()
    candles = [_bar(112.0, ENTRY_EPOCH + 300, high=113.0, low=105.0)]  # T1 (110) touched via high, stop/T3 untouched
    result = split.advance(order, candles)
    assert result["t1_status"] == "FILLED"
    assert result["t1_fill_price"] == 110.0
    assert result["management_state"] == "T1_FILLED"
    assert result["t1_leg_r"] == 0.5 * ((110.0 - 100.0) / 10.0)
    assert "realized_pnl_r" not in result   # trade not closed yet


def test_runner_closes_at_t3_after_t1_already_persisted():
    # t1_status already FILLED from a prior poll -- runner phase only.
    order = _order(t1_status="FILLED", t1_fill_price=110.0, t1_fill_time=ENTRY_TIME + datetime.timedelta(minutes=5),
                   t1_leg_r=0.5 * ((110.0 - 100.0) / 10.0))
    t1_fill_epoch = (ENTRY_TIME + datetime.timedelta(minutes=5)).timestamp()
    candles = [
        _bar(110.0, t1_fill_epoch),
        _bar(132.0, t1_fill_epoch + 300, high=133.0, low=125.0),  # T3 (130) touched via high
    ]
    result = split.advance(order, candles)
    assert result["management_state"] == "CLOSED_T3"
    assert result["close_reason"] == "T3"
    assert result["exit_price"] == 130.0
    expected_t1_leg_r = 0.5 * ((110.0 - 100.0) / 10.0)
    expected_runner_r = 0.5 * ((130.0 - 100.0) / 10.0)
    assert result["runner_r"] == expected_runner_r
    assert result["realized_pnl_r"] == expected_t1_leg_r + expected_runner_r


def test_runner_stop_after_t1_never_moves_from_original_level():
    order = _order(t1_status="FILLED", t1_fill_price=110.0, t1_fill_time=ENTRY_TIME + datetime.timedelta(minutes=5),
                   t1_leg_r=0.5 * ((110.0 - 100.0) / 10.0))
    t1_fill_epoch = (ENTRY_TIME + datetime.timedelta(minutes=5)).timestamp()
    candles = [
        _bar(110.0, t1_fill_epoch),
        _bar(95.0, t1_fill_epoch + 300, high=112.0, low=89.5),  # original stop (90) touched via low, NOT a moved BE stop
    ]
    result = split.advance(order, candles)
    assert result["management_state"] == "CLOSED_RUNNER_STOP"
    assert result["close_reason"] == "RUNNER_STOP"
    assert result["exit_price"] == 90.0   # the ORIGINAL stop -- confirms it never moved to breakeven
    expected_t1_leg_r = 0.5 * ((110.0 - 100.0) / 10.0)
    expected_runner_r = 0.5 * ((90.0 - 100.0) / 10.0)
    assert result["runner_r"] == expected_runner_r
    assert result["realized_pnl_r"] == expected_t1_leg_r + expected_runner_r


def test_t1_fill_and_runner_closure_both_land_in_one_catch_up_poll():
    # A gap between polls means T1 fill AND the runner's own exit both
    # happened inside the same batch of newly-confirmed candles -- both
    # sets of fields must come back together in one call, not spread
    # across two (mgmt_e1_stack.py's own "replay full history" style).
    order = _order()
    candles = [
        _bar(112.0, ENTRY_EPOCH + 300, high=113.0, low=105.0),   # T1 touched
        _bar(132.0, ENTRY_EPOCH + 600, high=133.0, low=125.0),   # T3 touched right after
    ]
    result = split.advance(order, candles)
    assert result["t1_status"] == "FILLED"
    assert result["t1_fill_price"] == 110.0
    assert result["management_state"] == "CLOSED_T3"
    expected_t1_leg_r = 0.5 * ((110.0 - 100.0) / 10.0)
    expected_runner_r = 0.5 * ((130.0 - 100.0) / 10.0)
    assert result["realized_pnl_r"] == expected_t1_leg_r + expected_runner_r


def test_short_side_directions_are_mirrored():
    order = _order(direction="SHORT", entry_price=100.0, stop_price=110.0, t1_price=90.0, t3_price=70.0)
    candles = [_bar(88.0, ENTRY_EPOCH + 300, high=91.0, low=87.0)]  # T1 (90) touched via low
    result = split.advance(order, candles)
    assert result["t1_status"] == "FILLED"
    assert result["t1_fill_price"] == 90.0
    assert result["t1_leg_r"] == 0.5 * ((100.0 - 90.0) / 10.0)


def test_short_side_stop_before_t1():
    order = _order(direction="SHORT", entry_price=100.0, stop_price=110.0, t1_price=90.0, t3_price=70.0)
    candles = [_bar(105.0, ENTRY_EPOCH + 300, high=112.0, low=99.0)]  # stop (110) touched via high
    result = split.advance(order, candles)
    assert result["management_state"] == "CLOSED_STOP_BEFORE_T1"
    assert result["exit_price"] == 110.0
    assert result["realized_pnl_r"] == -1.0


def test_still_open_returns_none_when_nothing_touches():
    order = _order()
    candles = [_bar(102.0, ENTRY_EPOCH + 300, high=103.0, low=101.0)]
    assert split.advance(order, candles) is None


def test_missing_entry_fill_time_returns_none():
    order = _order(entry_fill_time=None)
    assert split.advance(order, [_bar(100.0, ENTRY_EPOCH)]) is None


def test_no_bars_since_entry_returns_none():
    order = _order()
    candles = [_bar(100.0, ENTRY_EPOCH - 300)]  # before entry, not after
    assert split.advance(order, candles) is None
