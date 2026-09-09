# executor_engine.py
# ==============================================================================
# EXECUTOR ENGINE -- orchestration, called from trade_plan_engine.py's
# _apply() hook on the exact ARMED/FILLED transition.
#
# process_fill() iterates every active ExecutorAccount, each in its OWN
# try/except -- one account's failure must never affect another's, and
# (critically) nothing in here may ever raise back into _apply(), which
# would put the real TradePlan write at risk. Per account: all
# COMPUTATION happens first, in plain Python, via executor_plan_builder's
# pure function (which CAN raise); only once that has fully succeeded are
# ExecutorOrder/ExecutorAuditLog objects constructed and db.add()-ed. This
# ordering is deliberate -- see the design plan's own note on why this
# codebase doesn't use SQLAlchemy SAVEPOINT/nested transactions (never
# used anywhere here, and SQLite -- used in every test in this repo --
# has a well-known pysqlite quirk with them). Doing all computation
# before any db.add() gets the same "a bug never leaves partial dirty
# state" property without a new, unproven transaction pattern.
#
# 2026-09-07 (Domain 2 build): DRY_RUN and LIVE now compute/persist/audit
# identically -- sizing and safety checks never differ by mode. LIVE goes
# one step further and places a real resting-LIMIT entry order via
# executor_live_engine.place_entry_order() once build_hypothetical_order()
# says WOULD_PLACE. This function itself still starts no new asyncio loop
# -- the ongoing position WATCH (fill detection, exits, the T2 breakeven
# move, closure) runs in executor_live_engine.run_executor_position_loop(),
# a separate background task registered in main.py's lifespan(), not
# started from here. PAPER stays unimplemented (no PAPER accounts exist).
# ==============================================================================

from __future__ import annotations

import json
from typing import Any, Dict

from sqlalchemy.orm import Session

import executor_accounts
import executor_control
import executor_plan_builder
from database import ExecutorAccount, ExecutorAuditLog, ExecutorOrder, TradePlan

_ORDER_COLUMNS = set(ExecutorOrder.__table__.columns.keys())


def _audit_event_type(order_dict: Dict[str, Any]) -> str:
    decision = order_dict.get("decision")
    if decision == "WOULD_PLACE":
        return "ORDER_WOULD_PLACE"
    if decision == "REJECTED" and order_dict.get("liquidation_check_passed") is False:
        return "LIQUIDATION_CHECK_FAILED"
    return "ORDER_REJECTED"


async def _process_account(db: Session, trade_plan_row: TradePlan, account: ExecutorAccount) -> None:
    risk_state = executor_accounts.get_or_init_risk_state(db, account)

    # Can raise (a bug here must not corrupt the DB) -- now also makes a
    # real, read-only exchange call (query real leverage/margin mode)
    # when the account has credentials set, see executor_plan_builder.py's
    # own header for why.
    order_dict = await executor_plan_builder.build_hypothetical_order(db, trade_plan_row, account, risk_state)

    if account.mode == "PAPER":
        # No PAPER accounts exist yet -- structurally present but dead
        # code today, out of scope for the Domain 2 (LIVE) build. See
        # executor_live_engine.py's own header for what LIVE now does.
        raise NotImplementedError("PAPER execution is not built yet")

    # DRY_RUN and LIVE both compute, persist, audit identically -- sizing
    # and safety checks never differ by mode ("no AI improvisation").
    # Only LIVE goes on to place a real order below.
    order_dict["tier"] = trade_plan_row.tier
    filtered = {k: v for k, v in order_dict.items() if k in _ORDER_COLUMNS}
    order = ExecutorOrder(**filtered)
    db.add(order)
    db.flush()  # populate order.id for the audit row below

    db.add(ExecutorAuditLog(
        account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order.id,
        event_type=_audit_event_type(order_dict), actor="system",
        message=f"{order_dict.get('decision')}: {order_dict.get('decision_reason')}",
        detail_json=json.dumps(order_dict, default=str),
    ))

    if account.mode == "LIVE" and order_dict.get("decision") == "WOULD_PLACE":
        # 2026-09-07 real safety-gate gap found while planning the missing
        # LIVE-mode switch: this was the ONLY place in the entire real
        # executor path (executor_live_engine.py/executor_engine.py/
        # executor_plan_builder.py -- grepped all three) that could ever
        # place a real order, and it never checked the global "Live
        # Orders" switch at all -- only executor_mechanism_test.py's tiny
        # test respected it. Andy has been treating that switch as a
        # master safety gate all session ("both the global switch AND
        # each account's own kill switch must be clear" -- this file's
        # own module header repeats the same two-layer pattern). Without
        # this check, the moment an account reaches LIVE mode it would
        # place real orders regardless of that switch's state. Restored.
        if not executor_control.is_live_orders_enabled(db):
            executor_accounts.write_audit(
                db, "ERROR",
                f"LIVE-mode account {account.id} skipped real order placement -- "
                f"global Live Orders switch is OFF (trade_plan_id={trade_plan_row.id})",
                account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order.id, actor="system")
            return
        # 2026-09-08 real incident, second layer: set_account_mode()'s own
        # DRY_RUN->LIVE gate now refuses to go live without an explicit
        # sizing save (executor_accounts.py) -- but that only protects a
        # FUTURE transition. An account that reached LIVE before that fix
        # existed (Dawson's real one, confirmed still live when this was
        # found) keeps trading on whatever sizing is actually saved,
        # unconfirmed or not, until someone manually re-saves it. This
        # checks the SAME condition on every real order attempt, not just
        # at the moment of going live, so the system itself stops placing
        # further real orders on an account whose sizing was never
        # explicitly confirmed -- it doesn't depend on a human remembering
        # to go fix it after being told.
        policy = executor_accounts.get_or_init_sizing_policy(db, account)
        if policy.preset_name in (None, "steady_grow", "conservative"):
            executor_accounts.write_audit(
                db, "ERROR",
                f"LIVE-mode account {account.id} skipped real order placement -- "
                f"no sizing choice has ever been explicitly saved (still the untouched default) -- "
                f"go to Sizing Policy and click SAVE before this account can trade again "
                f"(trade_plan_id={trade_plan_row.id})",
                account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order.id, actor="system")
            return
        db.flush()
        import executor_live_engine
        await executor_live_engine.place_entry_order(db, account, trade_plan_row, order)


async def process_fill(db: Session, trade_plan_row: TradePlan) -> None:
    accounts = db.query(ExecutorAccount).filter_by(is_active=True).all()
    for account in accounts:
        try:
            await _process_account(db, trade_plan_row, account)
        except Exception as e:
            print(f"|| EXECUTOR || account {account.id} ({account.label}) failed for "
                  f"trade_plan {trade_plan_row.id}: {e}")
