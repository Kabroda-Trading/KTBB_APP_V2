# executor_engine.py
# ==============================================================================
# EXECUTOR ENGINE -- orchestration, called from trade_plan_engine.py's
# _apply() hook on the exact ARMED/FILLED transition. Stage 1 of the
# Bitunix executor bot.
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
# No new asyncio loop. Stage 1 needs none -- see database.py's
# ExecutorOrder/ExecutorAuditLog docstrings for what Stage 1 does and
# does not track.
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

    if account.mode in ("PAPER", "LIVE"):
        # Stage 2/3 -- no such account exists yet in Stage 1 (every
        # account created so far defaults to DRY_RUN), so this branch is
        # structurally present but dead code today. Deliberately not
        # implemented further here -- see executor_bitunix_client.py's
        # own header for why.
        import executor_bitunix_client  # noqa: F401 -- imported to confirm the module exists, not called
        raise NotImplementedError("PAPER/LIVE execution is Stage 2/3 -- not built yet")

    # DRY_RUN -- Stage 1's entire story: compute, persist, audit. Only
    # NOW, after computation has already fully succeeded, do we db.add().
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


async def process_fill(db: Session, trade_plan_row: TradePlan) -> None:
    accounts = db.query(ExecutorAccount).filter_by(is_active=True).all()
    for account in accounts:
        try:
            await _process_account(db, trade_plan_row, account)
        except Exception as e:
            print(f"|| EXECUTOR || account {account.id} ({account.label}) failed for "
                  f"trade_plan {trade_plan_row.id}: {e}")
