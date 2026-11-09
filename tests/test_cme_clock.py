"""Unit coverage for cme_clock.py -- the DST-aware America/New_York boundary
math. Unlike alt_matrix_clock.py (deliberately UTC-only, no DST concerns),
this module's entire reason to exist is getting DST right: the real
NYSE/NASDAQ cash open is 9:30 AM LOCAL wall-clock time, year-round, so the
UTC instant for 9:30/10:00/16:00 ET shifts by an hour across the EDT/EST
boundary. This is the direct regression test for the Part-0 finding that
scripts/run_cme_live_daemon.py's hardcoded `now_utc - 5h` offset (the
Kabroda AI Brain repo) is wrong for ~8 months of the year -- same style of
test as tests/test_session_manager.py's own spring-forward/fall-back
regression tests for next_lock_utc()."""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import cme_clock as cc

UTC = datetime.timezone.utc


# ------------------------------------------------------------------ deep EST / deep EDT sanity

def test_today_lock_utc_deep_winter_est_is_15_00_utc():
    # 10:00 AM EST == 15:00 UTC (UTC-5). Well inside EST, no transition
    # nearby.
    now = datetime.datetime(2026, 1, 12, 12, 0, 0, tzinfo=UTC)
    assert cc.today_lock_utc(now) == datetime.datetime(2026, 1, 12, 15, 0, 0, tzinfo=UTC)


def test_today_lock_utc_deep_summer_edt_is_14_00_utc():
    # 10:00 AM EDT == 14:00 UTC (UTC-4). The exact bug this module fixes:
    # scripts/run_cme_live_daemon.py's hardcoded "now_utc - 5h" would
    # compute 10:00 "EST" at 15:00 UTC year-round, firing an hour LATE
    # relative to the real local market open during EDT.
    now = datetime.datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)
    assert cc.today_lock_utc(now) == datetime.datetime(2026, 7, 15, 14, 0, 0, tzinfo=UTC)


def test_today_open_and_flush_also_shift_correctly_in_edt():
    now = datetime.datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)
    assert cc.today_open_utc(now) == datetime.datetime(2026, 7, 15, 13, 30, 0, tzinfo=UTC)
    assert cc.today_flush_utc(now) == datetime.datetime(2026, 7, 15, 20, 0, 0, tzinfo=UTC)


# ------------------------------------------------------------------ next_lock_utc -- spring-forward (2026-03-08)

def test_next_lock_utc_spring_forward_eve_gives_correct_next_day_lock():
    # 2026-03-07 15:00 UTC is Saturday before spring-forward transition.
    # Because CME equity futures are closed weekends, the next lock advances
    # straight to MONDAY 2026-03-09 14:00 UTC (EDT).
    now = datetime.datetime(2026, 3, 7, 15, 0, 0, tzinfo=UTC)
    result = cc.next_lock_utc(now)
    assert result == datetime.datetime(2026, 3, 9, 14, 0, 0, tzinfo=UTC)
    assert (result - now).total_seconds() / 3600 == 47.0


# ------------------------------------------------------------------ next_lock_utc -- fall-back (2026-11-01) -- THE REGRESSION TEST

def test_next_lock_utc_fall_back_eve_gives_correct_25h_later_lock():
    # 2026-10-31 14:00 UTC is Saturday before fall-back transition.
    # Because CME equity futures are closed weekends, the next lock advances
    # straight to MONDAY 2026-11-02 15:00 UTC (EST).
    now = datetime.datetime(2026, 10, 31, 14, 0, 0, tzinfo=UTC)
    result = cc.next_lock_utc(now)
    assert result == datetime.datetime(2026, 11, 2, 15, 0, 0, tzinfo=UTC)
    assert (result - now).total_seconds() / 3600 == 49.0


# ------------------------------------------------------------------ next_lock_utc -- boundary behavior

def test_next_lock_utc_before_todays_lock_returns_today():
    now = datetime.datetime(2026, 1, 12, 14, 0, 0, tzinfo=UTC)   # 9:00 AM EST, before the 10:00 lock
    assert cc.next_lock_utc(now) == datetime.datetime(2026, 1, 12, 15, 0, 0, tzinfo=UTC)


def test_next_lock_utc_exactly_at_todays_lock_rolls_to_tomorrow():
    now = datetime.datetime(2026, 1, 12, 15, 0, 0, tzinfo=UTC)   # exactly 10:00 AM EST
    assert cc.next_lock_utc(now) == datetime.datetime(2026, 1, 13, 15, 0, 0, tzinfo=UTC)


def test_seconds_until_next_lock_matches_next_lock_utc():
    now = datetime.datetime(2026, 1, 12, 14, 0, 0, tzinfo=UTC)
    expected = (cc.next_lock_utc(now) - now).total_seconds()
    assert cc.seconds_until_next_lock(now) == expected


# ------------------------------------------------------------------ most_recent_lock_utc

def test_most_recent_lock_utc_before_todays_lock_returns_yesterdays():
    now = datetime.datetime(2026, 1, 12, 14, 0, 0, tzinfo=UTC)   # 9:00 AM EST on Monday, before today's 10:00 lock
    # On Monday morning before 10:00 AM, the most recent weekday lock is FRIDAY Jan 9 (Sunday Jan 11 is closed).
    assert cc.most_recent_lock_utc(now) == datetime.datetime(2026, 1, 9, 15, 0, 0, tzinfo=UTC)


def test_most_recent_lock_utc_after_todays_lock_returns_today():
    now = datetime.datetime(2026, 1, 12, 16, 0, 0, tzinfo=UTC)   # 11:00 AM EST, after today's 10:00 lock
    assert cc.most_recent_lock_utc(now) == datetime.datetime(2026, 1, 12, 15, 0, 0, tzinfo=UTC)


def test_most_recent_lock_utc_across_fall_back_is_correct_not_off_by_an_hour():
    # Sunday 2026-11-01 is a weekend (CME closed). On Sunday afternoon, the most recent
    # lock was Friday 2026-10-30 at 14:00 UTC (EDT).
    now = datetime.datetime(2026, 11, 1, 16, 0, 0, tzinfo=UTC)
    assert cc.most_recent_lock_utc(now) == datetime.datetime(2026, 10, 30, 14, 0, 0, tzinfo=UTC)


# ------------------------------------------------------------------ catch-up window

def test_within_catchup_window_true_just_after_the_lock():
    lock = datetime.datetime(2026, 1, 12, 15, 0, 0, tzinfo=UTC)
    now = lock + datetime.timedelta(seconds=120)
    assert cc.is_within_catchup_window(lock, now, window_seconds=900) is True


def test_within_catchup_window_false_once_the_window_has_passed():
    lock = datetime.datetime(2026, 1, 12, 15, 0, 0, tzinfo=UTC)
    now = lock + datetime.timedelta(seconds=901)
    assert cc.is_within_catchup_window(lock, now, window_seconds=900) is False


def test_within_catchup_window_false_before_the_lock_even_happened():
    lock = datetime.datetime(2026, 1, 12, 15, 0, 0, tzinfo=UTC)
    now = lock - datetime.timedelta(seconds=5)
    assert cc.is_within_catchup_window(lock, now, window_seconds=900) is False
