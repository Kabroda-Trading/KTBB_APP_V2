# cme_clock.py
# ==============================================================================
# CME CLOCK -- 2026-11-04, CME Micro Futures via Tradovate (CC_INTERFACE.md,
# Kabroda AI Brain repo, §8). Unlike alt_matrix_clock.py (deliberately NOT
# built on session_manager.py, because Bitunix 4H bars close on fixed UTC
# boundaries with zero DST concerns), this module's whole job IS a DST
# problem: the CME session is anchored to the real NYSE/NASDAQ cash open,
# which is 9:30 AM America/New_York LOCAL WALL-CLOCK time, year-round --
# UTC-4 during EDT (roughly mid-March to early November, ~8 months of the
# year) and UTC-5 only during EST. A naive fixed "now_utc - 5h" offset (the
# bug found in the Brain repo's own scripts/run_cme_live_daemon.py, never
# fixed there, never run live) is wrong for most of the year and would
# silently misalign the entire TPO calibration window from the real market
# open it's supposed to anchor to.
#
# session_manager.py already solved this correctly for the BTC Traveler's
# own NY session (pytz, astimezone(), replace() on the LOCAL datetime, then
# convert back) -- this module copies that exact TECHNIQUE, not its code
# (session_manager.py is BTC/Traveler-specific machinery; importing it would
# violate the Iron Wall). See tests/test_cme_clock.py for the direct DST
# regression test: a known EDT date and a known EST date must each resolve
# to the correct UTC instant for 9:30/10:00/16:00 local.
# ==============================================================================

import datetime
import pytz

NY_TZ = pytz.timezone("America/New_York")

OPEN_H, OPEN_M = 9, 30     # NY cash open -- start of the 30-minute calibration window
LOCK_H, LOCK_M = 10, 0     # TPO 70% value-area lock
FLUSH_H, FLUSH_M = 16, 0   # mandatory session close -- zero overnight hold


def _local_today_at(now_utc: datetime.datetime, hour: int, minute: int) -> datetime.datetime:
    """Today's (in NY local time) HH:MM, converted back to a UTC-aware
    datetime -- DST-correct because pytz resolves the real UTC offset for
    that specific calendar date, not a fixed hardcoded hour."""
    now_local = now_utc.astimezone(NY_TZ)
    target_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target_local.astimezone(pytz.utc)


def today_open_utc(now_utc: datetime.datetime) -> datetime.datetime:
    """Today's 9:30 AM ET, as a UTC instant."""
    return _local_today_at(now_utc, OPEN_H, OPEN_M)


def today_lock_utc(now_utc: datetime.datetime) -> datetime.datetime:
    """Today's 10:00 AM ET lock instant, as a UTC instant."""
    return _local_today_at(now_utc, LOCK_H, LOCK_M)


def today_flush_utc(now_utc: datetime.datetime) -> datetime.datetime:
    """Today's 4:00 PM ET mandatory session-flush instant, as a UTC instant."""
    return _local_today_at(now_utc, FLUSH_H, FLUSH_M)


def next_lock_utc(now_utc: datetime.datetime) -> datetime.datetime:
    """The next 10:00 AM ET lock instant strictly AFTER now_utc -- today's,
    if it hasn't happened yet, otherwise tomorrow's. Weekends/holidays are
    deliberately NOT filtered here (CME equity-index futures trade nearly
    continuously; the engine's own lock logic simply finds an empty/garbage
    30-minute window on a closed day and stands down for lack of valid
    bars -- no separate calendar is needed to get that right)."""
    candidate = today_lock_utc(now_utc)
    if candidate <= now_utc:
        candidate = today_lock_utc(now_utc + datetime.timedelta(days=1))
    return candidate


def most_recent_lock_utc(now_utc: datetime.datetime) -> datetime.datetime:
    """The most recent 10:00 AM ET lock instant AT OR BEFORE now_utc --
    today's, if it's already happened, otherwise yesterday's. Exact mirror
    of next_lock_utc()'s own logic, flipped (same role alt_matrix_clock.py's
    most_recent_eval_utc() plays there) -- used on every engine wake
    (including right after a restart) to find the lock instant to check
    against is_within_catchup_window(). Deliberately NOT `next_lock_utc(now)
    - 1 day`: on a DST transition day, yesterday's and today's lock instants
    aren't exactly 24h apart, so that shortcut would be wrong -- this
    re-resolves the local wall-clock time directly instead, same as every
    other function here."""
    candidate = today_lock_utc(now_utc)
    if candidate > now_utc:
        candidate = today_lock_utc(now_utc - datetime.timedelta(days=1))
    return candidate


def seconds_until_next_lock(now_utc: datetime.datetime) -> float:
    """Seconds to sleep so the session loop wakes exactly at the next real
    10:00 AM ET lock instant -- the same boundary-anchored pattern main.py's
    run_monthly_lti_scheduler() and alt_matrix_clock.py's
    seconds_until_next_eval() both use, never a fixed-duration sleep that
    drifts off the true boundary over time."""
    return (next_lock_utc(now_utc) - now_utc).total_seconds()


def is_within_catchup_window(lock_instant: datetime.datetime, now_utc: datetime.datetime, window_seconds: int) -> bool:
    """True if now_utc is still within window_seconds of a missed
    lock_instant (e.g. after a restart) -- late evaluation of that same
    lock is still safe/intended. Past the window, the caller should record
    the day as MISSED rather than guess at a stale calibration window."""
    return 0 <= (now_utc - lock_instant).total_seconds() <= window_seconds
