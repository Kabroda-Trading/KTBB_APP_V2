# cme_calendar.py
# ==============================================================================
# CME CALENDAR -- explicit holiday/early-close dates and Tier-1 news-event
# windows for CME equity index futures (MNQ/MES), 2026-11-11.
#
# WHY THIS EXISTS: before this file, "was today a holiday?" was answered
# only by INFERENCE (an empty 30-minute calibration window -> natural
# stand-down -> MISSED_WINDOW), which is indistinguishable from a genuine
# data-fetch failure or a dead background loop -- exactly the ambiguity
# that caused the 2026-11-11 "was it closed today for Veterans Day?"
# confusion, when the real cause was an unrelated loop-death bug
# (cme_engine.run_cme_session_loop(), fixed same night, commit c0868f7),
# not a holiday at all. Veterans Day does NOT close CME equity index
# futures (confirmed directly against CME's own Veterans Day 2026
# settlement advisory -- only the bond market closes for it; AGENT_LOG.md
# 2026-11-11 has the full research trail). This module makes "was today a
# known holiday" a fact the system can check directly, not something to
# reverse-engineer from a blank desk.
#
# NOT A LIVE FEED. CME publishes its holiday/early-close schedule per-
# holiday (cmegroup.com/tools-information/holiday-calendar.html), finalized
# roughly 2 weeks before each date -- there is no stable API to poll. This
# is a hardcoded table, reviewed at least annually. An unlisted date is
# treated as a normal trading day -- the existing empty-calibration-window
# stand-down path still catches anything this table doesn't know about, so
# an incomplete table is a missing safety net, never a crash.
#
# SAME "NEVER FABRICATE" STANDARD THIS PROJECT APPLIES EVERYWHERE ELSE:
# the holiday/early-close dates below were cross-checked against CME's own
# settlement advisories and multiple independent market-hours sources
# 2026-11-11 (see AGENT_LOG.md same date). The Tier-1 news calendar below
# is DELIBERATELY INCOMPLETE -- FOMC dates/times are well-confirmed and
# included; CPI and Employment Situation (NFP) dates came back genuinely
# conflicting across sources during that same research pass (e.g. two
# BLS pages disagreeing on the same month's jobs-report date) and were
# NOT included rather than guessed. See TIER1_NEWS_EVENTS_UTC's own
# comment below before relying on this for anything but FOMC.
# ==============================================================================

import datetime
from typing import Optional, Union

NY_TZ_NAME = "America/New_York"

# date_key (NY calendar date, "YYYY-MM-DD") -> "CLOSED" (no session at all
# for equity index futures that day) or (hour, minute) in ET, the real
# early-close time. Source: CME Group's own published 2026 holiday
# calendar + per-holiday settlement-time advisories, cross-checked against
# multiple independent market-hours sources 2026-11-11 -- not a guess.
# RECONFIRM against cmegroup.com's own calendar before relying on a date
# that's still more than a few weeks out when you're reading this; CME
# only finalizes the exact early-close minute roughly 2 weeks prior, and
# full-year tables can occasionally shift (a holiday observed on a
# different day, a timing change) between when this was written and when
# the date actually arrives.
CME_EQUITY_INDEX_CALENDAR: dict = {
    "2026-11-26": "CLOSED",     # Thanksgiving Day
    "2026-11-27": (13, 0),      # Day after Thanksgiving -- early close ~1:00 PM ET
    "2026-12-24": (13, 0),      # Christmas Eve -- early close ~1:00 PM ET
    "2026-12-25": "CLOSED",     # Christmas Day
}


def holiday_status(date_key: str) -> Optional[str]:
    """Returns "CLOSED" if date_key (NY calendar date, "YYYY-MM-DD") is a
    known full-closure day for CME equity index futures, else None (either
    a normal day or a known early-close day -- see early_close_time_et()
    for that). An unlisted date always returns None; this is a positive
    confirmation table, never a source of a false "market's open" read."""
    return "CLOSED" if CME_EQUITY_INDEX_CALENDAR.get(date_key) == "CLOSED" else None


def early_close_time_et(date_key: str) -> Optional[datetime.time]:
    """Returns the real early-close (hour, minute) in ET for date_key if
    it's a known early-close day, else None (normal day or full closure --
    see holiday_status() for that)."""
    entry = CME_EQUITY_INDEX_CALENDAR.get(date_key)
    if entry is None or entry == "CLOSED":
        return None
    hour, minute = entry
    return datetime.time(hour, minute)


# Tier-1 news events, as (UTC datetime, label) pairs. MFFU's own Tier-1
# news-trading restriction (confirmed 2026-11-11 against help.
# myfundedfutures.com/en/articles/8230009-news-trading-policy, the real
# page, not a summary) names: FOMC meetings, FOMC minutes, Employment
# Report (NFP), and CPI for all traders -- no orders/positions open 2
# minutes before through 2 minutes after the release. The rule currently
# binds Rapid Sim Funded and Pro Sim Funded accounts only, NOT evaluation
# accounts (Account 17 is still evaluation as of 2026-11-11) -- this
# calendar/check is built and wired in regardless, so it's already correct
# the moment the account transitions, rather than a thing to remember to
# add later.
#
# FOMC: all 8 2026 meeting dates below, decision always released 2:00 PM
# ET -- cross-checked across multiple sources 2026-11-11, consistent, not
# conflicting. FOMC minutes release 3 weeks after each meeting at 2:00 PM
# ET -- NOT included below (not independently confirmed per-date this
# pass); add before relying on this for a minutes release.
#
# CPI / Employment Situation (NFP): DELIBERATELY NOT POPULATED. Research
# 2026-11-11 came back genuinely conflicting (e.g. BLS's own monthly
# schedule page and its CPS release-calendar page disagreeing on the same
# month's jobs-report date) and incomplete (several months' CPI dates not
# found at all) -- entering a guessed date here would be worse than
# leaving the gap visible, per this project's own "a labeled gap is
# honest, a labeled guess is a liability" standard. Pull the real schedule
# directly from https://www.bls.gov/schedule/news_release/empSit.htm (jobs
# report) and the CPI schedule at bls.gov/schedule/2026/ before adding
# these -- both are published a year in advance by BLS, just not reliably
# re-derivable from secondary sources on demand.
TIER1_NEWS_EVENTS_UTC: list = [
    # 2026 FOMC decisions, 2:00 PM ET = 19:00 UTC (EDT, UTC-4) for the
    # March/April/June/July/September meetings, or 19:00 UTC still during
    # EST transition weeks -- verified per-meeting below rather than
    # assumed constant, since DST flips mid-year.
    (datetime.datetime(2026, 1, 28, 19, 0, tzinfo=datetime.timezone.utc), "FOMC Decision (Jan 2026)"),
    (datetime.datetime(2026, 3, 18, 18, 0, tzinfo=datetime.timezone.utc), "FOMC Decision (Mar 2026)"),
    (datetime.datetime(2026, 4, 29, 18, 0, tzinfo=datetime.timezone.utc), "FOMC Decision (Apr 2026)"),
    (datetime.datetime(2026, 6, 17, 18, 0, tzinfo=datetime.timezone.utc), "FOMC Decision (Jun 2026)"),
    (datetime.datetime(2026, 7, 29, 18, 0, tzinfo=datetime.timezone.utc), "FOMC Decision (Jul 2026)"),
    (datetime.datetime(2026, 9, 16, 18, 0, tzinfo=datetime.timezone.utc), "FOMC Decision (Sep 2026)"),
    (datetime.datetime(2026, 10, 28, 18, 0, tzinfo=datetime.timezone.utc), "FOMC Decision (Oct 2026)"),
    (datetime.datetime(2026, 12, 9, 19, 0, tzinfo=datetime.timezone.utc), "FOMC Decision (Dec 2026)"),
]

NEWS_BLACKOUT_MINUTES = 2   # MFFU's rule: no open positions/orders 2 min before/after


def news_blackout_reason(now_utc: datetime.datetime) -> Optional[str]:
    """Returns the event label if now_utc falls within the blackout window
    (event time +/- NEWS_BLACKOUT_MINUTES) of any listed Tier-1 event,
    else None. Pure/no I/O -- same testability convention as cme_signals.py."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=datetime.timezone.utc)
    window = datetime.timedelta(minutes=NEWS_BLACKOUT_MINUTES)
    for event_utc, label in TIER1_NEWS_EVENTS_UTC:
        if (event_utc - window) <= now_utc <= (event_utc + window):
            return label
    return None
