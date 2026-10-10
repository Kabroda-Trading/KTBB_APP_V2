# alt_matrix_clock.py
# ==============================================================================
# ALT MATRIX CLOCK -- 2026-10-09, binding spec ALT_MATRIX_D1_D2_D3_SPEC.md
# (Kabroda AI Brain repo, commit 83942b0). Deliberately NOT built on top of
# session_manager.py -- that module is NY-session/DST-specific machinery
# (pytz, SESSION_CONFIGS, "one session open per calendar day in a named
# local timezone") for a completely different shape of problem than Alt
# Matrix's own clock: SIX FIXED boundaries per day, in pure UTC, with zero
# DST concerns at all. Bitunix 4H bars close at 00:00/04:00/08:00/12:00/
# 16:00/20:00 UTC; the spec's own evaluation rule is "strictly at HH:00:05
# UTC following bar close" -- never on an open, fluctuating bar.
#
# This module is intentionally tiny and has no imports beyond datetime --
# no pytz, no DB, no network. Verified (see tests/test_alt_matrix_clock.py)
# to never drift: callers sleep the exact number of seconds to the next
# real boundary (main.py's own run_monthly_lti_scheduler() pattern), never
# a fixed asyncio.sleep(14400) measured from whenever the previous tick's
# work finished (that's run_outcome_tracker()'s own pattern, and it drifts
# off true clock boundaries over time -- do not copy that one).
# ==============================================================================

import datetime

BAR_SECONDS = 4 * 3600          # 14400 -- one 4H bar
EVAL_DELAY_SECONDS = 5           # evaluate 5s after the bar closes, never on it


def _floor_to_4h(now_utc: datetime.datetime) -> datetime.datetime:
    """The most recent 4H boundary at or before now_utc (00/04/08/12/16/20
    UTC), with microseconds/seconds/minutes zeroed."""
    midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    hours_since_midnight = (now_utc - midnight).total_seconds() / 3600
    boundary_hour = int(hours_since_midnight // 4) * 4
    return midnight + datetime.timedelta(hours=boundary_hour)


def next_eval_utc(now_utc: datetime.datetime) -> datetime.datetime:
    """The next HH:00:05 UTC evaluation instant strictly AFTER now_utc --
    i.e. the next 4H bar close, plus the 5-second settle delay. If now_utc
    is itself sitting exactly on a past eval instant (e.g. a caller re-
    checking immediately after firing one), this still returns the NEXT
    one, never the same instant twice."""
    last_boundary = _floor_to_4h(now_utc)
    candidate = last_boundary + datetime.timedelta(seconds=EVAL_DELAY_SECONDS)
    if candidate <= now_utc:
        candidate += datetime.timedelta(seconds=BAR_SECONDS)
    return candidate


def seconds_until_next_eval(now_utc: datetime.datetime) -> float:
    """Seconds to sleep so a poll loop wakes exactly at the next real
    HH:00:05 UTC boundary -- the boundary-anchored pattern main.py's own
    run_monthly_lti_scheduler() uses, not a fixed-duration sleep."""
    return (next_eval_utc(now_utc) - now_utc).total_seconds()


def most_recent_eval_utc(now_utc: datetime.datetime) -> datetime.datetime:
    """The most recent HH:00:05 UTC evaluation instant AT OR BEFORE
    now_utc -- exact mirror of next_eval_utc()'s own logic, flipped.
    Used by the engine's signal loop on every wake (including right after
    a restart) to find the boundary it should check against
    is_within_catchup_window() for a possible catch-up evaluation."""
    last_boundary = _floor_to_4h(now_utc)
    candidate = last_boundary + datetime.timedelta(seconds=EVAL_DELAY_SECONDS)
    if candidate > now_utc:
        candidate -= datetime.timedelta(seconds=BAR_SECONDS)
    return candidate


def expected_closed_bar_open(eval_instant: datetime.datetime) -> datetime.datetime:
    """Given an eval instant (HH:00:05 UTC), the OPEN time of the 4H bar
    that instant is evaluating -- i.e. eval_instant minus the bar length
    minus the settle delay. Used to confirm a fetched candle series' own
    last bar is actually the one this eval instant expects, not a stale
    fetch."""
    return eval_instant - datetime.timedelta(seconds=EVAL_DELAY_SECONDS + BAR_SECONDS)


def is_within_catchup_window(eval_instant: datetime.datetime, now_utc: datetime.datetime, window_seconds: int) -> bool:
    """True if now_utc is still within window_seconds of a missed
    eval_instant (e.g. after a restart) -- late evaluation of that same
    boundary is still safe/intended. Past the window, the caller should
    record the bar as MISSED rather than guess at a stale evaluation."""
    return (now_utc - eval_instant).total_seconds() <= window_seconds
