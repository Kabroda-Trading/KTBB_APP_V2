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
