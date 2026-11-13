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

import cme_calendar

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


def _at_date(date_key: str, hour: int, minute: int) -> datetime.datetime:
    """HH:MM NY-local on an EXPLICIT calendar date (not "today" relative to
    the caller's clock), converted to a UTC-aware datetime via pytz the
    same DST-correct way _local_today_at() does. The deliberate split from
    _local_today_at() exists so a deadline anchored to a SPECIFIC trading
    day (flush_utc_for_date_key() below) can be computed once and stay
    fixed forever, never re-derived from "now" on a later call."""
    y, m, d = (int(x) for x in date_key.split("-"))
    naive = datetime.datetime(y, m, d, hour, minute)
    local = NY_TZ.localize(naive)
    return local.astimezone(pytz.utc)


def flush_utc_for_date_key(date_key: str) -> datetime.datetime:
    """The mandatory flush instant for a SPECIFIC trading day (a CMEPlan's
    own date_key) -- 4:00 PM ET on a normal day, or the real early-close
    time on a known CME holiday early-close day (cme_calendar.py). Same
    normal/early-close logic today_flush_utc() below uses, just anchored
    to an explicit date instead of "now."

    2026-11-13 (real gap found live, Account 17): cme_engine.py's
    _advance_open_position() used to call today_flush_utc(now_utc), which
    recomputes a FRESH, FUTURE deadline every time it's called, relative
    to whatever "now" happens to be -- including after the wall clock
    crosses midnight. If a position ever survived past its own day's 4:00
    PM ET flush (a dead/restarting loop, an exchange-side outage, a failed
    market_close() retry that never got re-attempted before midnight), the
    very next calendar day's check would see a BRAND NEW future 4pm
    deadline and treat the position as not-yet-overdue -- silently
    granting a full extra day's grace on a mandatory zero-overnight-hold
    rule (MFFU's own rule, see cme_clock.py's module header). Callers that
    need "is THIS position's own trading day's flush deadline already
    past" (i.e. _advance_open_position()) must anchor to the position's
    OWN date_key via this function, never to "today" via today_flush_utc()
    -- a fixed-in-the-past instant can never roll forward again."""
    early = cme_calendar.early_close_time_et(date_key)
    if early is not None:
        return _at_date(date_key, early.hour, early.minute)
    return _at_date(date_key, FLUSH_H, FLUSH_M)


def today_flush_utc(now_utc: datetime.datetime) -> datetime.datetime:
    """Today's mandatory session-flush instant, as a UTC instant -- see
    flush_utc_for_date_key() for the normal/early-close logic itself. Only
    a "what time is today's flush" convenience (e.g. for a countdown
    display) -- NOT for checking whether an existing position is overdue;
    see flush_utc_for_date_key()'s own docstring for why that distinction
    matters."""
    now_local = now_utc.astimezone(NY_TZ)
    date_key = now_local.strftime("%Y-%m-%d")
    return flush_utc_for_date_key(date_key)


def _next_weekday_lock(now_utc: datetime.datetime) -> datetime.datetime:
    """The next 10:00 AM ET lock on a WEEKDAY (Mon-Fri), strictly after
    now_utc. CME equity-index futures are CLOSED on weekends (Friday 17:00
    ET through Sunday 18:00 ET), and Databento returns a hard HTTP 422
    (data_start_after_available_end) rather than an empty list when a
    closed window is queried -- so the lock must advance straight from
    Friday's lock to MONDAY's lock, skipping Saturday and Sunday entirely.
    Added 2026-11-08 per Antigravity's ruling (AGENT_LOG Session 4 Item 4a):
    the prior "find an empty window and stand down" assumption was proven
    false by the 2026-11-07 live 422."""
    candidate = today_lock_utc(now_utc)
    if candidate <= now_utc:
        candidate = today_lock_utc(now_utc + datetime.timedelta(days=1))
    while candidate.astimezone(NY_TZ).weekday() in (5, 6):   # Sat=5, Sun=6
        candidate = today_lock_utc(candidate + datetime.timedelta(days=1))
    return candidate


def next_lock_utc(now_utc: datetime.datetime) -> datetime.datetime:
    """The next 10:00 AM ET lock instant strictly AFTER now_utc -- today's,
    if it hasn't happened yet, otherwise tomorrow's. 2026-11-08 (Antigravity
    ruling AGENT_LOG Session 4 Item 4a): weekends are now filtered -- the
    next lock after Friday's advances straight to MONDAY at 10:00 ET (CME
    equity-index futures are closed Sat/Sun, and Databento 422s rather than
    returning empty bars on a closed window)."""
    return _next_weekday_lock(now_utc)


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
    other function here.

    2026-11-08 (Antigravity ruling AGENT_LOG Session 4 Item 4a): weekends
    are filtered here TOO -- the same "no Sat/Sun lock exists" fact that
    motivated next_lock_utc()'s guard applies on the flip side. On a
    Saturday/Sunday this must return FRIDAY's lock (the last real lock), not
    a phantom "Saturday/Sunday 10:00 ET" instant that never existed. Without
    this, a weekend restart would have the session loop treat a phantom
    Saturday lock as "the most recent lock," mis-skip the catchup window,
    and write a bogus MISSED_WINDOW row for a day the market was closed.
    This was the one half of the ruling's "update BOTH functions" that the
    initial implementation missed -- flagged, not silently left."""
    candidate = today_lock_utc(now_utc)
    if candidate > now_utc:
        candidate = today_lock_utc(now_utc - datetime.timedelta(days=1))
    while candidate.astimezone(NY_TZ).weekday() in (5, 6):   # Sat=5, Sun=6
        candidate = today_lock_utc(candidate - datetime.timedelta(days=1))
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
