# cme_risk.py
# ==============================================================================
# CME RISK -- the one genuinely new risk concept this subsystem needed that
# Alt Matrix never did: trailing-drawdown-from-a-running-high-water-mark,
# the Apex evaluation's own rule (CC_INTERFACE.md §8, Kabroda AI Brain repo).
# This is NOT the alt_matrix_portfolio.py shape -- CME shares no account or
# margin pool with Bitunix at all (different broker, Tradovate, a
# completely separate physical account), so there is no cross-system
# ExecutorOrder/TravelerPlan read here, and no margin-reserve-floor concept.
# This module only ever reads/writes ExecutorAccount's own trailing_
# drawdown_limit_usd/high_water_mark_usd fields and CME's own CMEOrder/
# CMEConfig tables -- never another subsystem's.
#
# Daily risk-budget split: the business plan's own "Operational Rule"
# (prop_firm_business_and_execution_plan.md) is $350/day total, split $175
# per asset if BOTH MNQ and MES trigger the same session, $350 for whichever
# one trades alone. risk_budget_for_symbol() below encodes this -- do not
# let a single symbol silently consume the full $350 budget on a day both
# triggered.
# ==============================================================================

import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from database import CMEOrder, CMEPlan, ExecutorAccount
import cme_calendar
import cme_signals

OTHER_SYMBOL = {"MNQ": "MES", "MES": "MNQ"}

_OPEN_CME_STATES = (
    "WAITING_RETEST", "FILLED", "RATCHETED", "ENTRY_FILLED_UNPROTECTED",
)
# PENDING_ENTRY is deliberately NOT "open" -- cme_engine.py's own D1 fan-out
# creates TWO PENDING_ENTRY rows per account per plan (one per direction, a
# legitimate OCO-style pair -- only one can ever actually fire). Checked
# 2026-11-04 whether treating PENDING_ENTRY as open here could make the
# SECOND direction processed in that same fan-out loop see its own
# uncommitted sibling row and self-reject as ALREADY_OPEN_ON_SYMBOL_TODAY --
# NOT reproducible as a live bug today: database.py's SessionLocal is
# autoflush=False and the loop's one db.flush() call sits after both
# directions are added, so the sibling genuinely isn't query-visible yet.
# Changed anyway as a defensive correctness fix, not a reproduced-bug fix:
# "open" should mean a row that has actually progressed toward or into a live
# position, matching the business rule's own "once a symbol has
# triggered" wording for the $175/$350 budget split below.


def update_high_water_mark(account: ExecutorAccount, current_equity: float) -> float:
    """Advances account.high_water_mark_usd if current_equity is a new peak.
    Never moves it DOWN -- a high-water mark only ever ratchets up. Returns
    the (possibly unchanged) high-water mark. Caller is responsible for
    committing -- this function only mutates the in-memory ORM object."""
    hwm = account.high_water_mark_usd
    if hwm is None or current_equity > hwm:
        account.high_water_mark_usd = current_equity
        return current_equity
    return hwm


def trailing_drawdown_usd(account: ExecutorAccount, current_equity: float) -> float:
    """Dollars below the running high-water mark right now. Zero if at or
    above the mark (call update_high_water_mark() first in the normal flow
    so this is never negative in practice; clamped at 0 regardless, since a
    'negative drawdown' has no meaning)."""
    hwm = account.high_water_mark_usd
    if hwm is None:
        return 0.0
    return max(0.0, hwm - current_equity)


def check_trailing_drawdown(account: ExecutorAccount, current_equity: float, warn_fraction: float = 0.5) -> Dict[str, Any]:
    """Updates the high-water mark for a new peak, then checks the resulting
    drawdown against account.trailing_drawdown_limit_usd. Fails CLOSED
    (not ok) if the limit itself isn't configured -- an unconfigured
    trailing-DD limit on a PROP_EVALUATION account is a setup error, never
    treated as 'no limit.'"""
    limit = account.trailing_drawdown_limit_usd
    if limit is None or limit <= 0:
        return {
            "ok": False, "reason": "NO_TRAILING_DD_LIMIT_CONFIGURED",
            "drawdown_usd": None, "buffer_remaining_usd": None, "warn": True,
        }

    update_high_water_mark(account, current_equity)
    dd = trailing_drawdown_usd(account, current_equity)
    buffer_remaining = limit - dd

    if buffer_remaining <= 0:
        return {
            "ok": False, "reason": f"TRAILING_DD_BREACHED (${dd:,.2f} >= ${limit:,.2f} limit)",
            "drawdown_usd": dd, "buffer_remaining_usd": buffer_remaining, "warn": True,
        }

    warn = buffer_remaining <= (limit * warn_fraction)
    return {
        "ok": True, "reason": None,
        "drawdown_usd": dd, "buffer_remaining_usd": buffer_remaining, "warn": warn,
    }


def risk_budget_for_symbol(db: Session, account_id: int, symbol: str, daily_risk_budget_usd: float, date_key: str) -> float:
    """$175 (half the daily budget) if the OTHER CME symbol already has an
    open-or-pending order for this account today; the full daily budget
    otherwise. Mirrors the business plan's own 'split $175 max per asset if
    both trigger on the same session' rule -- never let one symbol consume
    the whole day's budget on a day both triggered."""
    other = OTHER_SYMBOL.get(symbol.upper())
    if other is None:
        return daily_risk_budget_usd

    other_open_today = (
        db.query(CMEOrder)
        .join(CMEPlan, CMEOrder.cme_plan_id == CMEPlan.id)
        .filter(
            CMEOrder.account_id == account_id,
            CMEOrder.symbol == other,
            CMEOrder.management_state.in_(_OPEN_CME_STATES),
            CMEPlan.date_key == date_key,
        )
        .first()
    )
    return (daily_risk_budget_usd / 2.0) if other_open_today else daily_risk_budget_usd


def can_open_new_position(
    db: Session, account: ExecutorAccount, symbol: str, date_key: str,
    current_equity: float, daily_risk_budget_usd: float, contracts_cap: int,
    box_points: float, now_utc: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """CME's own admission check -- NOT a port of alt_matrix_portfolio.py's
    shape (no cross-system read; CME shares no account with Bitunix). Checks,
    in order: Tier-1 news blackout, already-open-on-this-symbol-today,
    trailing drawdown, then computes the risk-budget-aware contract count.
    ANY check failing means stand down -- never guess.

    2026-11-11: the Tier-1 news-blackout check (cme_calendar.py) is real
    and wired in regardless of whether it currently binds this account --
    MFFU's own rule only applies to Sim Funded accounts, not evaluation
    (Account 17 is still evaluation as of this date), but the check costs
    nothing to run and is already correct the moment an account
    transitions, rather than a thing to remember to add later. now_utc
    defaults to real current time if not passed -- a parameter so tests
    can set a deterministic time instead of a real blackout window
    happening to be active or not when the suite runs."""
    now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc)
    news_reason = cme_calendar.news_blackout_reason(now_utc)
    if news_reason is not None:
        return {"admitted": False, "reason": f"NEWS_BLACKOUT ({news_reason})", "contracts": 0, "risk_budget_usd": 0.0}

    existing = (
        db.query(CMEOrder)
        .join(CMEPlan, CMEOrder.cme_plan_id == CMEPlan.id)
        .filter(
            CMEOrder.account_id == account.id,
            CMEOrder.symbol == symbol.upper(),
            CMEOrder.management_state.in_(_OPEN_CME_STATES),
            CMEPlan.date_key == date_key,
        )
        .first()
    )
    if existing is not None:
        return {"admitted": False, "reason": "ALREADY_OPEN_ON_SYMBOL_TODAY", "contracts": 0, "risk_budget_usd": 0.0}

    dd_check = check_trailing_drawdown(account, current_equity)
    if not dd_check["ok"]:
        return {"admitted": False, "reason": dd_check["reason"], "contracts": 0, "risk_budget_usd": 0.0}

    risk_budget = risk_budget_for_symbol(db, account.id, symbol, daily_risk_budget_usd, date_key)
    contracts = cme_signals.dynamic_contract_count(symbol, box_points, risk_budget, contracts_cap)

    return {
        "admitted": True, "reason": None,
        "contracts": contracts, "risk_budget_usd": risk_budget,
        "trailing_dd_snapshot": dd_check,
    }
