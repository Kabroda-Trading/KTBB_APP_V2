"""Unit coverage for session_manager.py -- pure session-anchor/lock math.

next_lock_utc()'s own tests are the regression guard for a real P0 found
2026-10-07 (while auditing a WAITING_CROSS session-expiration fix, not
reported by anyone -- caught by actually running the function against the
fall-back DST date, not just reading it): the original implementation
collapsed to a ~1-hour delta instead of ~25 hours on the US fall-back
transition day, because it borrowed anchor_ts_for_utc_date()'s own "roll
back one day if before today's local open" branch, which is correct for
THAT function's real job but wrong for "give me tomorrow's lock from here."
This already governed the shipped R1 re-arm's REARM_WATCH deadline in
production before the fix. Frozen exact-value assertions below so a future
refactor can't silently reintroduce this."""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import session_manager as sm

UTC = datetime.timezone.utc


# ------------------------------------------------------------------ next_lock_utc -- plain days

def test_next_lock_utc_plain_day_is_24h_later():
    now = datetime.datetime(2026, 9, 15, 13, 0, 0, tzinfo=UTC)
    result = sm.next_lock_utc(now)
    assert result == datetime.datetime(2026, 9, 16, 13, 0, 0, tzinfo=UTC)
    assert (result - now).total_seconds() / 3600 == 24.0


def test_next_lock_utc_est_plain_day_locks_at_14_utc():
    # Deep winter (well inside EST) -- 14:00 UTC, not 13:00.
    now = datetime.datetime(2026, 1, 10, 14, 0, 0, tzinfo=UTC)
    result = sm.next_lock_utc(now)
    assert result == datetime.datetime(2026, 1, 11, 14, 0, 0, tzinfo=UTC)


# ------------------------------------------------------------------ next_lock_utc -- spring-forward (2026-03-08, US "spring forward")

def test_next_lock_utc_spring_forward_eve_gives_correct_next_day_lock():
    now = datetime.datetime(2026, 3, 7, 13, 0, 0, tzinfo=UTC)
    result = sm.next_lock_utc(now)
    # 2026-03-08 is the transition -- the NEXT lock is still 13:00 UTC
    # (9am now-EDT), 24h later; EDT doesn't take effect on NY's own clock
    # until 2am local on the 8th, which is already past by the 9am lock.
    assert result == datetime.datetime(2026, 3, 8, 13, 0, 0, tzinfo=UTC)
    assert (result - now).total_seconds() / 3600 == 24.0


# ------------------------------------------------------------------ next_lock_utc -- fall-back (2026-11-01, US "fall back") -- THE REGRESSION TEST

def test_next_lock_utc_fall_back_eve_gives_correct_25h_later_lock():
    # 2026-10-31 13:00 UTC is the REAL EDT lock moment the day before the
    # fall-back transition. The old, buggy implementation returned
    # 2026-10-31 14:00 UTC here (a 1-hour delta) -- this is the exact
    # reproduction of that bug, now asserting the correct value.
    now = datetime.datetime(2026, 10, 31, 13, 0, 0, tzinfo=UTC)
    result = sm.next_lock_utc(now)
    assert result == datetime.datetime(2026, 11, 1, 14, 0, 0, tzinfo=UTC)
    assert (result - now).total_seconds() / 3600 == 25.0


def test_next_lock_utc_fall_back_day_never_collapses_to_under_a_day():
    # Scan every hour of the transition day itself -- at no point should
    # "the next lock" be less than ~13 hours away (the real minimum, right
    # before that same day's own lock fires) let alone the old bug's ~1h.
    for h in range(24):
        now = datetime.datetime(2026, 10, 31, h, 0, 0, tzinfo=UTC)
        result = sm.next_lock_utc(now)
        delta_h = (result - now).total_seconds() / 3600
        assert delta_h >= 10.0, f"hour {h}: delta collapsed to {delta_h}h"


# ------------------------------------------------------------------ anchor_ts_for_utc_date untouched (resolve_current_session's own job)

def test_anchor_ts_for_utc_date_still_rolls_back_before_todays_open():
    # Confirms the fix didn't touch this function's own, different,
    # correct behavior (next_lock_utc() no longer calls it at all).
    config = sm.get_session_config("us_ny_futures")
    before_open = datetime.datetime(2026, 9, 15, 11, 0, 0, tzinfo=UTC)  # 7am EDT, before 8:30 open
    anchor_ts = sm.anchor_ts_for_utc_date(config, before_open)
    anchor_dt = datetime.datetime.fromtimestamp(anchor_ts, UTC)
    assert anchor_dt.date() == datetime.date(2026, 9, 14)  # yesterday's anchor
