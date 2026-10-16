# metasignals_paper_engine.py
# ==============================================================================
# METASIGNALS PAPER TRADING ENGINE -- the $500 simulated bankroll, order
# fill simulator, fee deduction, and breakeven ratchet. Andy directive
# 2026-10-13, METASIGNALS_TRIAGE_AND_EXECUTION_SPEC.md sections 4-5.
#
# Pure DB/arithmetic module -- no network I/O of its own (metasignals_
# watcher.py fetches the live mark price and passes it in). Iron Wall:
# zero import of/call into gate_traveler.py, traveler_plan_engine.py,
# battlebox_pipeline.py, executor_live_e1_engine.py, executor_plan_
# builder.py, executor_sizing.py, or any alt_matrix_*.py module -- this
# engine never touches a real exchange order, a real ExecutorAccount, or
# real credentials; it is a closed-form paper ledger only.
#
# DORMANT EXECUTION-MODE SWITCH (2026-10-13, spec section 6C): POST
# /api/metasignals/config lets an admin set MetaSignalsPaperAccount.
# execution_mode to PAPER | SUBACCOUNT_DRY_RUN | SUBACCOUNT_LIVE. This
# module deliberately IGNORES that field today -- open_paper_trade()/
# advance_order() always simulate as pure paper, regardless of what's
# stored. Wiring SUBACCOUNT_LIVE to place real Bitunix orders through a
# linked ExecutorAccount's real credentials is a separate, much
# higher-stakes build (real credential plumbing, the same Step-6-style
# live-API smoke test Alt Matrix required before going live, and a
# backtest of the 5-pillar gate against real outcomes, which does not
# exist yet -- unlike Traveler and Alt Matrix, both already measured
# against years of real data before a single live order was placed).
# Andy's own directive was "paper trading desk FIRST"; the original
# spec's section 1.5 calls the live path a "dormant switch" explicitly.
# Silently wiring real execution into a UI dropdown toggle would be
# exactly the unauthorized scope expansion this project's own standing
# discipline warns against -- if/when Andy wants SUBACCOUNT_LIVE to
# actually place orders, that is a fresh, explicitly-authorized task.
#
# ACCOUNTING MODEL: cash_balance is a pure running P&L ledger (starts at
# $500; every fee and every realized P&L leg adjusts it directly) -- this
# paper desk simulates fixed-dollar-risk sizing and fee accounting
# exactly as the spec's own worked numbers describe ($50 risk, ~$25 TP1
# gain), not margin/leverage reservation mechanics the spec never asked
# for. equity = cash_balance + unrealized P&L of any currently-open
# remaining size, marked at the live price -- refreshed every tick by
# refresh_equity(), called from metasignals_watcher.py, so Andy can watch
# it move in real time (section 4A.4).
#
# FILL-PRICE CONVENTION: entry_price is set at ORDER CREATION time for
# both entry modes -- the live mark price for a MARKET fill (already
# known from the triage gate's own read), or the alert's own price for a
# resting LIMIT order (the price it will fill AT, once touched). filled_
# at (None vs not-None) is the one authoritative signal for "has this
# actually filled yet," not entry_price's presence -- deliberately, so a
# PENDING_LIMIT order's eventual fill never needs to overwrite a price
# that was already correct from the start.
#
# FEE SCHEDULE (spec section 4A.3/4B, applied literally): ALL entries
# (market or resting-limit) pay the TAKER fee on entry notional -- the
# spec does not carve out a maker rate for a resting entry, only for a
# resting TP exit. TP1/TP2 closes (both explicit limit fills) pay MAKER.
# A STOP close (market/stop-market exit) pays TAKER, matching section 4B
# ("Deducts Taker Fee (0.06%)") explicitly for the stop-hit case too.
#
# DIRECTION-AWARE as of 2026-10-16 (CC_WORK_ORDER_METASIGNALS_SHORTS_
# AND_MAJORS.md section 3D). For a SELL order the geometry is fully
# mirrored: hard_stop/current_stop sits ABOVE entry, tp1_price/tp2_price
# sit BELOW entry, a stop hit is `mark_price >= current_stop` (not
# `<=`), a TP hit is `mark_price <= tp_price` (not `>=`), and P&L is
# `qty * (entry_price - exit_price)` (price DROP is profit). Every
# comparison and P&L formula below multiplies by `_dir(order)` (+1 for
# BUY, -1 for SELL) rather than duplicating the BUY logic with signs
# hand-flipped -- one formula, same for both directions, instead of two
# parallel code paths that could drift.
# ==============================================================================

from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from database import MetaSignalsPaperAccount, MetaSignalsPaperOrder, MetaSignalsPaperLedger, MetaSignalsPlan

TAKER_FEE_RATE = 0.0006
MAKER_FEE_RATE = 0.0002
DEFAULT_ACCOUNT_NAME = "DEFAULT_PAPER_DESK"

_OPEN_STATES = ("OPEN", "HALF_CLOSED_BE")


def _dir(order: MetaSignalsPaperOrder) -> float:
    """+1.0 for BUY, -1.0 for SELL -- the one place the direction sign
    lives, so every P&L/comparison formula below reads identically for
    both sides instead of two hand-mirrored copies."""
    return 1.0 if order.side == "BUY" else -1.0


def _write_ledger(
    db: Session, account: MetaSignalsPaperAccount, plan_id: Optional[int], order_id: Optional[int],
    event_type: str, amount: float, notes: Optional[str] = None,
) -> None:
    db.add(MetaSignalsPaperLedger(
        plan_id=plan_id, order_id=order_id, event_type=event_type, amount=amount,
        cash_balance_after=account.cash_balance, equity_after=account.equity, notes=notes,
    ))
    db.flush()   # SessionLocal is autoflush=False -- a plain query right after this won't see the row otherwise


def get_or_init_paper_account(db: Session) -> MetaSignalsPaperAccount:
    """Lazily creates the one singleton paper account on first real use
    (never by a boot hook or a GET route) -- same "nothing goes live by
    accident" caution as every other lazy-init account row in this
    codebase. Writes the INITIAL_DEPOSIT ledger row exactly once, at
    creation."""
    account = db.query(MetaSignalsPaperAccount).filter_by(name=DEFAULT_ACCOUNT_NAME).first()
    if account is not None:
        return account
    account = MetaSignalsPaperAccount(name=DEFAULT_ACCOUNT_NAME)
    db.add(account)
    db.flush()
    _write_ledger(db, account, None, None, "INITIAL_DEPOSIT", account.starting_balance, notes="Paper desk initialized")
    return account


def open_paper_trade(db: Session, plan: MetaSignalsPlan) -> MetaSignalsPaperOrder:
    """Creates the MetaSignalsPaperOrder for an APPROVED plan. Caller
    (the ingest route) is responsible for only calling this when
    plan.triage_verdict == "YES" -- this function does not re-check the
    verdict itself, matching the rest of this codebase's convention of
    the gate deciding once and the executor trusting that decision."""
    account = get_or_init_paper_account(db)
    now = datetime.datetime.utcnow()

    target_price = plan.live_price_at_alert if plan.entry_mode == "MARKET" else plan.alert_price
    order = MetaSignalsPaperOrder(
        plan_id=plan.id, account_id=account.id, symbol=plan.symbol, side=plan.side,
        entry_mode=plan.entry_mode, entry_price=target_price,
        hard_stop_initial=plan.hard_stop, current_stop=plan.hard_stop,
        tp1_price=plan.tp1_price, tp2_price=plan.tp2_price,
        initial_qty=plan.position_size, remaining_qty=plan.position_size,
        state="PENDING_LIMIT" if plan.entry_mode == "LIMIT" else "OPEN",
    )
    db.add(order)
    db.flush()

    if plan.entry_mode == "MARKET":
        _fill_entry(db, account, order, now)
    return order


def _fill_entry(db: Session, account: MetaSignalsPaperAccount, order: MetaSignalsPaperOrder, now: datetime.datetime) -> None:
    fee = TAKER_FEE_RATE * order.initial_qty * order.entry_price
    account.cash_balance -= fee
    order.fees_paid += fee
    order.filled_at = now
    order.state = "OPEN"
    _write_ledger(db, account, order.plan_id, order.id, "ENTRY_FEE", -fee,
                  notes=f"{order.symbol} entry fill @ {order.entry_price} ({order.entry_mode}), taker fee")


def _finalize_closed_trade(db: Session, account: MetaSignalsPaperAccount, order: MetaSignalsPaperOrder, now: datetime.datetime) -> None:
    account.total_trades += 1
    if order.realized_pnl >= 0:
        account.winning_trades += 1
    else:
        account.losing_trades += 1
    account.total_realized_pnl += order.realized_pnl
    order.closed_at = now


def advance_order(db: Session, order: MetaSignalsPaperOrder, mark_price: float, now: Optional[datetime.datetime] = None) -> Optional[str]:
    """Advances ONE order by at most one state transition this tick,
    given the current live mark price. Returns the event name
    ("ENTRY_FILL" | "TP1_FILL" | "CLOSED_STOP" | "CLOSED_TP2") if
    anything happened, else None. Priority within a tick when a PENDING_
    LIMIT/OPEN/HALF_CLOSED_BE order could satisfy more than one
    condition: STOP is checked before TP1/TP2 -- the conservative
    reading of an ambiguous single mark-price sample (no intra-tick wick
    order is actually known), matching the STOP-first priority this
    codebase's other two systems (mgmt_e1_stack.py, alt_matrix_
    management.py) already use."""
    account = db.query(MetaSignalsPaperAccount).filter_by(id=order.account_id).first()
    if account is None:
        return None
    if now is None:
        now = datetime.datetime.utcnow()

    # Direction-aware as of 2026-10-16 (CC_WORK_ORDER_METASIGNALS_
    # SHORTS_AND_MAJORS.md section 3D) -- a SELL's stop sits ABOVE
    # entry (hit on a RISE) and its TPs sit BELOW entry (hit on a
    # FALL), the mirror image of BUY's own comparisons.
    is_buy = order.side == "BUY"

    if order.state == "PENDING_LIMIT":
        # The resting limit fills when price retraces back to it --
        # DOWN to the limit for a BUY (it was extended upward), UP to
        # the limit for a SELL (it was extended downward).
        limit_touched = (mark_price <= order.entry_price) if is_buy else (mark_price >= order.entry_price)
        if limit_touched:
            _fill_entry(db, account, order, now)
            return "ENTRY_FILL"
        return None

    if order.state == "OPEN":
        stop_hit = (mark_price <= order.current_stop) if is_buy else (mark_price >= order.current_stop)
        if stop_hit:
            _close_full(db, account, order, order.current_stop, "STOP", TAKER_FEE_RATE, now)
            return "CLOSED_STOP"
        tp1_hit = (mark_price >= order.tp1_price) if is_buy else (mark_price <= order.tp1_price)
        if tp1_hit:
            _fill_tp1(db, account, order, now)
            return "TP1_FILL"
        return None

    if order.state == "HALF_CLOSED_BE":
        stop_hit = (mark_price <= order.current_stop) if is_buy else (mark_price >= order.current_stop)
        if stop_hit:
            _close_full(db, account, order, order.current_stop, "STOP", TAKER_FEE_RATE, now)
            return "CLOSED_STOP"
        tp2_hit = (mark_price >= order.tp2_price) if is_buy else (mark_price <= order.tp2_price)
        if tp2_hit:
            _close_full(db, account, order, order.tp2_price, "TP2", MAKER_FEE_RATE, now)
            return "CLOSED_TP2"
        return None

    return None


def _fill_tp1(db: Session, account: MetaSignalsPaperAccount, order: MetaSignalsPaperOrder, now: datetime.datetime) -> None:
    """TP1 hit: closes 50% of the ORIGINAL size (not 50% of whatever is
    currently remaining -- there is only ever one partial on this
    lineage, so the two are the same the first time this runs, but
    initial_qty is the unambiguous base, matching the spec's own "50%
    off" framed against the original position). Ratchets the stop on
    the remainder to breakeven (entry_price) -- spec section 4A.4."""
    half_qty = order.initial_qty * 0.5
    leg_pnl = half_qty * (order.tp1_price - order.entry_price) * _dir(order)
    fee = MAKER_FEE_RATE * half_qty * order.tp1_price

    account.cash_balance += leg_pnl - fee
    order.realized_pnl += leg_pnl
    order.fees_paid += fee
    order.remaining_qty -= half_qty
    order.current_stop = order.entry_price
    order.state = "HALF_CLOSED_BE"

    _write_ledger(db, account, order.plan_id, order.id, "TP1_FILL", leg_pnl - fee,
                  notes=f"{order.symbol} TP1 @ {order.tp1_price}, stop ratcheted to breakeven {order.entry_price}")


def _close_full(
    db: Session, account: MetaSignalsPaperAccount, order: MetaSignalsPaperOrder,
    exit_price: float, reason: str, fee_rate: float, now: datetime.datetime,
) -> None:
    leg_pnl = order.remaining_qty * (exit_price - order.entry_price) * _dir(order)
    fee = fee_rate * order.remaining_qty * exit_price

    account.cash_balance += leg_pnl - fee
    order.realized_pnl += leg_pnl
    order.fees_paid += fee
    order.remaining_qty = 0.0
    order.exit_price = exit_price
    order.state = f"CLOSED_{reason}"

    event_type = "STOP_FILL" if reason == "STOP" else "EXIT"
    _write_ledger(db, account, order.plan_id, order.id, event_type, leg_pnl - fee,
                  notes=f"{order.symbol} closed ({reason}) @ {exit_price}, total realized {order.realized_pnl:.4f}")
    _finalize_closed_trade(db, account, order, now)


def mark_unrealized(order: MetaSignalsPaperOrder, mark_price: float) -> float:
    if order.state in _OPEN_STATES and order.entry_price is not None:
        return order.remaining_qty * (mark_price - order.entry_price) * _dir(order)
    return 0.0


def refresh_equity(db: Session, account: MetaSignalsPaperAccount, mark_price_by_symbol: Dict[str, float]) -> None:
    """Recomputes account.equity = cash_balance + unrealized P&L of
    every currently-open order on this account, marked at the price the
    caller already fetched this tick. A symbol missing from
    mark_price_by_symbol (a fetch failure this tick) contributes exactly
    zero unrealized P&L for that leg this tick -- never a guessed or
    fabricated price -- so a flaky fetch understates equity slightly
    rather than corrupting it with a stale or invented number."""
    open_orders = db.query(MetaSignalsPaperOrder).filter(
        MetaSignalsPaperOrder.account_id == account.id,
        MetaSignalsPaperOrder.state.in_(_OPEN_STATES),
    ).all()
    unrealized_total = 0.0
    for o in open_orders:
        price = mark_price_by_symbol.get(o.symbol)
        if price is not None:
            unrealized_total += mark_unrealized(o, price)
    account.equity = account.cash_balance + unrealized_total
