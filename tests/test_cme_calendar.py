"""Coverage for cme_calendar.py -- pure functions, no DB/network, same
testability convention as cme_signals.py."""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import cme_calendar as calendar_mod

UTC = datetime.timezone.utc


# ------------------------------------------------------------------ holiday_status / early_close_time_et

def test_holiday_status_returns_closed_for_a_known_full_closure_day():
    assert calendar_mod.holiday_status("2026-11-26") == "CLOSED"   # Thanksgiving
    assert calendar_mod.holiday_status("2026-12-25") == "CLOSED"   # Christmas


def test_holiday_status_returns_none_for_a_normal_day():
    assert calendar_mod.holiday_status("2026-11-12") is None
    assert calendar_mod.holiday_status("2099-01-01") is None   # unlisted/future date -- never a false positive


def test_holiday_status_returns_none_for_a_known_early_close_day():
    # Early-close days are NOT full closures -- holiday_status() must not
    # conflate the two statuses.
    assert calendar_mod.holiday_status("2026-11-27") is None


def test_early_close_time_et_returns_the_real_time_for_a_known_early_close_day():
    t = calendar_mod.early_close_time_et("2026-11-27")
    assert t == datetime.time(13, 0)
    t2 = calendar_mod.early_close_time_et("2026-12-24")
    assert t2 == datetime.time(13, 0)


def test_early_close_time_et_returns_none_for_a_normal_day():
    assert calendar_mod.early_close_time_et("2026-11-12") is None


def test_early_close_time_et_returns_none_for_a_full_closure_day():
    # Must not misread "CLOSED" as an (hour, minute) tuple.
    assert calendar_mod.early_close_time_et("2026-11-26") is None


# ------------------------------------------------------------------ news_blackout_reason

def test_news_blackout_reason_fires_exactly_at_an_fomc_decision():
    event_utc = datetime.datetime(2026, 1, 28, 19, 0, tzinfo=UTC)
    reason = calendar_mod.news_blackout_reason(event_utc)
    assert reason is not None
    assert "FOMC" in reason


def test_news_blackout_reason_fires_within_the_window_before_and_after():
    event_utc = datetime.datetime(2026, 1, 28, 19, 0, tzinfo=UTC)
    before = event_utc - datetime.timedelta(minutes=1, seconds=59)
    after = event_utc + datetime.timedelta(minutes=1, seconds=59)
    assert calendar_mod.news_blackout_reason(before) is not None
    assert calendar_mod.news_blackout_reason(after) is not None


def test_news_blackout_reason_is_none_just_outside_the_window():
    event_utc = datetime.datetime(2026, 1, 28, 19, 0, tzinfo=UTC)
    well_before = event_utc - datetime.timedelta(minutes=3)
    well_after = event_utc + datetime.timedelta(minutes=3)
    assert calendar_mod.news_blackout_reason(well_before) is None
    assert calendar_mod.news_blackout_reason(well_after) is None


def test_news_blackout_reason_is_none_on_an_ordinary_moment():
    ordinary = datetime.datetime(2026, 6, 1, 15, 0, tzinfo=UTC)
    assert calendar_mod.news_blackout_reason(ordinary) is None


def test_news_blackout_reason_handles_a_naive_datetime_as_utc():
    # Caller might pass a naive datetime (matching datetime.utcnow()'s own
    # convention used elsewhere in this codebase) -- must not crash
    # comparing naive-to-aware.
    event_utc_naive = datetime.datetime(2026, 1, 28, 19, 0)
    assert calendar_mod.news_blackout_reason(event_utc_naive) is not None


def test_all_fomc_dates_use_the_correct_dst_offset():
    # Spot-check: 2:00 PM ET -> 19:00 UTC during EST (Jan, Dec), 18:00 UTC
    # during EDT (Mar through Oct, DST 2026: Mar 8 - Nov 1).
    # "(Dec " not "Dec" -- a bare "Dec" substring-matches "Decision" itself,
    # which is in every label.
    est_events = [e for e, label in calendar_mod.TIER1_NEWS_EVENTS_UTC if "(Jan " in label or "(Dec " in label]
    edt_events = [e for e, label in calendar_mod.TIER1_NEWS_EVENTS_UTC if "(Jan " not in label and "(Dec " not in label]
    assert all(e.hour == 19 for e in est_events)
    assert all(e.hour == 18 for e in edt_events)
    assert len(calendar_mod.TIER1_NEWS_EVENTS_UTC) == 8   # all 8 2026 FOMC meetings
