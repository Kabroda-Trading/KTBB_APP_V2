# cme_contracts.py
# ==============================================================================
# CME FUTURES CONTRACT RESOLVER -- maps a root symbol ("MNQ", "MES", "NQ",
# "ES") to the dated NinjaTrader 8 instrument string the CrossTrade
# GetBars RPC requires ("MNQ 12-26"), with automatic quarterly rollover.
#
# WHY THIS EXISTS: CrossTrade's own docs are explicit that the direct NT8
# API accepts a DATED contract only -- "the caller is responsible for
# instrument selection and rollover... do not rely on bare-root fallbacks.
# NT8 continuous symbols such as 'ES1!' are supported through webhook
# ingestion, not as part of the direct NT8 API contract." The old yfinance
# feed used continuous roots (MNQ=F), and the order path uses "1!" aliases;
# neither works for GetBars. This module is the single source of truth for
# turning a root into the dated front-month the VPS's NT8 is actually
# streaming.
#
# CALENDAR FACTS (CME equity-index futures -- the H/M/U/Z quarterly cycle):
#   - Contract months: H (Mar), M (Jun), U (Sep), Z (Dec).
#   - MNQ (Micro E-mini Nasdaq-100) and MES (Micro E-mini S&P 500) trade
#     the SAME quarterly cycle as their full-size parents NQ and ES.
#   - Expiration: the front-month expires on the 3rd Friday of the contract
#     month, at 9:30 ET. On (or after) that instant the next quarter becomes
#     the front month. For data-request purposes the relevant boundary is
#     "is today >= the 3rd Friday of the contract month" -- prior to that
#     instant the current quarter is still front, at/after it the next
#     quarter is front.
#
# ROLLOVER-DATE RULE (flag for confirmation, not presented as fact): this
# module rolls to the next quarter on the 3rd Friday of the contract month
# (the CME last-trading-day rule for these contracts). Some data consumers
# roll a day or two earlier on open-interest/volume crossover; that is NOT
# what NT8's front-month resolution uses, and the GetBars instrument must
# match what NT8 is actively streaming, so the 3rd-Friday rule is the right
# boundary here. Confirm the exact rollover instant against the VPS's live
# NT8 before trusting a boundary-crossing day -- this is the one date-
# sensitive detail that can't be proven from the code alone.
# ==============================================================================

from __future__ import annotations

import datetime
from typing import Optional

MONTH_CODE = {3: "H", 6: "M", 9: "U", 12: "Z"}

# Root symbols this module can resolve. Kept explicit so an unknown root
# fails loudly rather than silently producing a wrong instrument string.
_SUPPORTED_ROOTS = {"MNQ", "MES", "NQ", "ES"}


def _next_quarter_month(month: int) -> int:
    """The CME quarter cycle: 3->6->9->12->3. Returns the next quarter's
    month number given a current month number (1-12)."""
    return {3: 6, 6: 9, 9: 12, 12: 3}[month]


def _quarter_start_month(month: int) -> int:
    """The quarter month that month `month` falls in (Jan-Mar -> 3, etc.)."""
    for q in (3, 6, 9, 12):
        if month <= q:
            return q
    return 3


def third_friday(year: int, month: int) -> datetime.date:
    """The 3rd Friday of `month`/`year` -- the CME last-trading day for
    the H/M/U/Z equity-index contracts."""
    first = datetime.date(year, month, 1)
    # weekday(): Mon=0 ... Sun=6. Days until the first Friday (weekday 4).
    offset = (4 - first.weekday()) % 7
    return first + datetime.timedelta(days=offset + 14)


def resolve_contract(root_symbol: str, as_of: Optional[datetime.datetime] = None) -> str:
    """Returns the dated NT8 instrument string ("MNQ 12-26") for `root_symbol`
    as of `as_of` (defaults to now, UTC). Uses the 3rd-Friday rollover rule:
    before a contract's 3rd Friday it is still front; on/after that instant
    the next quarter is front. The year is the two-digit year of the
    contract month (so a Dec contract rolling into 2027 yields "H 03-27")."""
    root = root_symbol.strip().upper()
    if root not in _SUPPORTED_ROOTS:
        raise ValueError(f"unsupported CME root '{root_symbol}' -- add it to _SUPPORTED_ROOTS")

    now = as_of or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    # Current quarter (front month candidate) from the calendar month.
    qmonth = _quarter_start_month(now.month)
    qyear = now.year

    # Roll forward if today is on/after that quarter's 3rd Friday.
    if now.date() >= third_friday(qyear, qmonth):
        nxt = _next_quarter_month(qmonth)
        if nxt < qmonth:
            qyear += 1
        qmonth = nxt

    # NT8 dated-instrument format: "<ROOT> <MM>-<YY>", e.g. "MNQ 12-26".
    return f"{root} {qmonth:02d}-{qyear % 100:02d}"
