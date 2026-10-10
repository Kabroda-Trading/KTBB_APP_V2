"""Unit coverage for alt_matrix_clock.py -- pure UTC boundary math, no pytz.

Deliberately NOT exercising any DST transition: this module has none of
session_manager.py's complexity because Alt Matrix's clock is fixed UTC
boundaries, not a named-timezone session open. See session_manager.py's own
next_lock_utc() and its 2026-10-07 DST regression tests for the contrast --
that complexity exists there specifically because it's absent here."""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import alt_matrix_clock as amc

UTC = datetime.timezone.utc


# ------------------------------------------------------------------ next_eval_utc

def test_next_eval_from_mid_bar():
    now = datetime.datetime(2026, 10, 9, 1, 30, 0, tzinfo=UTC)
    assert amc.next_eval_utc(now) == datetime.datetime(2026, 10, 9, 4, 0, 5, tzinfo=UTC)


def test_next_eval_exactly_at_a_boundary_open():
    # The bar just closed (now == the open instant of the NEXT bar), but
    # the 5s settle delay hasn't elapsed yet -- still the same boundary's
    # own eval instant, not skipped.
    now = datetime.datetime(2026, 10, 9, 4, 0, 0, tzinfo=UTC)
    assert amc.next_eval_utc(now) == datetime.datetime(2026, 10, 9, 4, 0, 5, tzinfo=UTC)


def test_next_eval_just_after_this_boundarys_own_eval_instant():
    # now is 1s past 04:00:05 -- must return the NEXT boundary (08:00:05),
    # never the same instant twice.
    now = datetime.datetime(2026, 10, 9, 4, 0, 6, tzinfo=UTC)
    assert amc.next_eval_utc(now) == datetime.datetime(2026, 10, 9, 8, 0, 5, tzinfo=UTC)


def test_next_eval_exactly_at_the_eval_instant_itself():
    now = datetime.datetime(2026, 10, 9, 4, 0, 5, tzinfo=UTC)
    assert amc.next_eval_utc(now) == datetime.datetime(2026, 10, 9, 8, 0, 5, tzinfo=UTC)


def test_next_eval_day_rollover():
    now = datetime.datetime(2026, 10, 9, 20, 0, 10, tzinfo=UTC)
    assert amc.next_eval_utc(now) == datetime.datetime(2026, 10, 10, 0, 0, 5, tzinfo=UTC)


def test_next_eval_month_rollover():
    now = datetime.datetime(2026, 10, 31, 20, 0, 10, tzinfo=UTC)
    assert amc.next_eval_utc(now) == datetime.datetime(2026, 11, 1, 0, 0, 5, tzinfo=UTC)


def test_next_eval_covers_all_six_daily_boundaries():
    # Scan every hour of a day -- the returned eval instant's hour must
    # always be one of the six real boundaries, never a fabricated one.
    for h in range(24):
        now = datetime.datetime(2026, 10, 9, h, 17, 0, tzinfo=UTC)
        result = amc.next_eval_utc(now)
        assert result.hour in (0, 4, 8, 12, 16, 20)
        assert result.minute == 0 and result.second == 5
        assert result > now


# ------------------------------------------------------------------ seconds_until_next_eval

def test_seconds_until_next_eval_matches_next_eval_utc():
    now = datetime.datetime(2026, 10, 9, 1, 30, 0, tzinfo=UTC)
    secs = amc.seconds_until_next_eval(now)
    assert secs == (amc.next_eval_utc(now) - now).total_seconds()
    assert secs == 2.5 * 3600 + 5


def test_seconds_until_next_eval_never_negative_or_zero():
    for h in range(24):
        for m in (0, 1, 5, 59):
            now = datetime.datetime(2026, 10, 9, h, m, 0, tzinfo=UTC)
            assert amc.seconds_until_next_eval(now) > 0


# ------------------------------------------------------------------ expected_closed_bar_open

def test_expected_closed_bar_open():
    eval_instant = datetime.datetime(2026, 10, 9, 4, 0, 5, tzinfo=UTC)
    assert amc.expected_closed_bar_open(eval_instant) == datetime.datetime(2026, 10, 9, 0, 0, 0, tzinfo=UTC)


def test_expected_closed_bar_open_is_always_4h_before_the_prior_boundary():
    eval_instant = datetime.datetime(2026, 10, 9, 12, 0, 5, tzinfo=UTC)
    opened = amc.expected_closed_bar_open(eval_instant)
    assert opened == datetime.datetime(2026, 10, 9, 8, 0, 0, tzinfo=UTC)
    assert (eval_instant - opened).total_seconds() == amc.BAR_SECONDS + amc.EVAL_DELAY_SECONDS


# ------------------------------------------------------------------ is_within_catchup_window

def test_within_catchup_window_true_just_inside():
    eval_instant = datetime.datetime(2026, 10, 9, 4, 0, 5, tzinfo=UTC)
    now = datetime.datetime(2026, 10, 9, 4, 10, 0, tzinfo=UTC)   # ~9m55s late
    assert amc.is_within_catchup_window(eval_instant, now, window_seconds=900) is True


def test_within_catchup_window_false_once_past_it():
    eval_instant = datetime.datetime(2026, 10, 9, 4, 0, 5, tzinfo=UTC)
    now = datetime.datetime(2026, 10, 9, 4, 30, 0, tzinfo=UTC)   # ~30m late
    assert amc.is_within_catchup_window(eval_instant, now, window_seconds=900) is False


def test_within_catchup_window_true_at_exactly_zero_lag():
    eval_instant = datetime.datetime(2026, 10, 9, 4, 0, 5, tzinfo=UTC)
    assert amc.is_within_catchup_window(eval_instant, eval_instant, window_seconds=900) is True
