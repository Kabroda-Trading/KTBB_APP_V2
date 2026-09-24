# executor_engine.py
# ==============================================================================
# EXECUTOR ENGINE -- orchestration for GATE_TRAVELER's own fill event,
# process_traveler_fill(), called from traveler_plan_engine.py on
# TravelerPlan's ARMED/FILLED transition.
#
# process_traveler_fill() iterates every active ExecutorAccount, each in
# its OWN try/except -- one account's failure must never affect another's,
# and (critically) nothing in here may ever raise back into the caller,
# which would put the real TravelerPlan write at risk. Per account: all
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
# 2026-09-24 (V2 Crown retirement, Step 3f-ii): _process_account()/
# process_fill() (v1/v2's own TradePlan-fill orchestration, GATE_V2-only)
# deleted along with executor_live_engine.py, dry_run_split_engine.py,
# and mgmt_split_dry_run.py -- nothing else in the live codebase reaches
# them. _process_traveler_account()/process_traveler_fill() below are
# structurally independent and untouched by this retirement.
# ==============================================================================

from __future__ import annotations

import json
from typing import Any, Dict

from sqlalchemy.orm import Session

import executor_accounts
import executor_control
import executor_plan_builder
from database import ExecutorAccount, ExecutorAuditLog, ExecutorOrder, TravelerPlan

_ORDER_COLUMNS = set(ExecutorOrder.__table__.columns.keys())


def _audit_event_type(order_dict: Dict[str, Any]) -> str:
    decision = order_dict.get("decision")
    if decision == "WOULD_PLACE":
        return "ORDER_WOULD_PLACE"
    if decision == "REJECTED" and order_dict.get("liquidation_check_passed") is False:
        return "LIQUIDATION_CHECK_FAILED"
    return "ORDER_REJECTED"


async def _process_traveler_account(db: Session, traveler_plan_row: TravelerPlan, account: ExecutorAccount) -> None:
    """GATE_TRAVELER's counterpart to _process_account() above -- fires on
    TravelerPlan's own FILLED transition (traveler_plan_engine.py), never
    on TradePlan's. Skips every account NOT explicitly set to GATE_
    TRAVELER, symmetric to _process_account()'s own skip for GATE_V2."""
    if executor_accounts.gate_profile_of(account) != "GATE_TRAVELER":
        return
    risk_state = executor_accounts.get_or_init_risk_state(db, account)

    order_dict = await executor_plan_builder.build_hypothetical_traveler_order(db, traveler_plan_row, account, risk_state)

    if account.mode == "PAPER":
        raise NotImplementedError("PAPER execution is not built yet")

    order_dict["tier"] = None   # GATE_TRAVELER has no tier concept
    order_dict["gate_profile_used"] = executor_accounts.gate_profile_of(account)
    order_dict["mgmt_profile_used"] = executor_accounts.mgmt_profile_of(account)
    # DRY_RUN never gets a real exchange fill-confirmation callback (unlike
    # v1/v2's LIVE path, check_entry_fill_and_place_exits()) -- the
    # TravelerPlan's own trigger touch fill (already confirmed, real market
    # data) IS the entry fill, known at order-creation time. Sets
    # management_state to a real, watchable state immediately so
    # traveler_plan_engine.py's MGMT_E1_STACK poll (mgmt_e1_stack.py) picks
    # it up on the very next cycle, rather than sitting at PENDING_ENTRY
    # forever (the pre-existing gap for v1/v2's own DRY_RUN orders, which
    # this file does not attempt to fix -- flagged separately, out of this
    # step's scope).
    # DRY_RUN only (2026-09-21 audit, mirrors _process_account()'s own
    # `account.mode == "DRY_RUN"` gate above). This block used to be
    # ungated because LIVE was refused here at the time it was written; once
    # P3 lifted the refusal it kept stamping the DRY_RUN booking (fill price,
    # fill time, ENTRY_FILLED_ORDERS_PLACED) onto LIVE rows -- a fill that had
    # not happened, and a row the simulated E1 walk could then drive to a
    # terminal state while a real position was still open. A LIVE row's fill
    # comes from the exchange (executor_live_e1_engine.py), never from here.
    if account.mode == "DRY_RUN" and order_dict.get("decision") == "WOULD_PLACE":
        order_dict["entry_fill_price"] = traveler_plan_row.fill_price
        order_dict["entry_fill_time"] = traveler_plan_row.fill_time
        order_dict["management_state"] = "ENTRY_FILLED_ORDERS_PLACED"
    filtered = {k: v for k, v in order_dict.items() if k in _ORDER_COLUMNS}
    order = ExecutorOrder(**filtered)
    db.add(order)
    db.flush()  # populate order.id for the audit row below

    db.add(ExecutorAuditLog(
        account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order.id,
        event_type=_audit_event_type(order_dict), actor="system",
        message=f"{order_dict.get('decision')}: {order_dict.get('decision_reason')}",
        detail_json=json.dumps(order_dict, default=str),
    ))

    if account.mode == "LIVE" and order_dict.get("decision") == "WOULD_PLACE":
        # P3 (CC_INTERFACE.md HARD PRE-LIVE BLOCKER, Kabroda AI Brain repo,
        # 2026-09-20 -- Brain spec delivered + Andy-confirmed 14:40/14:50 CT):
        # wired to real order placement now that executor_live_e1_engine.py
        # exists. Before this, the refusal here was the ONLY thing
        # preventing a real fill from reaching executor_live_engine.py's
        # own check_entry_fill_and_place_exits() -- which is hard-coded to
        # MGMT_SPLIT's shape and would place a wrong-sized T1 then crash on
        # T3 (order_row.t3_price is always None for a traveler order). The
        # new engine places ONLY the exchange stop + a full-qty resting T1
        # limit, with all other exits (C5/BBWP/TIME) dynamic and market-
        # close, per the Brain's own spec. Same two-layer safety gate
        # _process_account() already enforces for v2, mirrored exactly.
        if not executor_control.is_live_orders_enabled(db):
            executor_accounts.write_audit(
                db, "ERROR",
                f"LIVE-mode GATE_TRAVELER account {account.id} skipped real order placement -- "
                f"global Live Orders switch is OFF (traveler_plan_id={traveler_plan_row.id})",
                account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order.id, actor="system")
            return
        policy = executor_accounts.get_or_init_sizing_policy(db, account)
        if policy.preset_name in (None, "steady_grow", "conservative"):
            executor_accounts.write_audit(
                db, "ERROR",
                f"LIVE-mode GATE_TRAVELER account {account.id} skipped real order placement -- "
                f"no sizing choice has ever been explicitly saved (still the untouched default) -- "
                f"go to Sizing Policy and click SAVE before this account can trade again "
                f"(traveler_plan_id={traveler_plan_row.id})",
                account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order.id, actor="system")
            return
        db.flush()
        import executor_live_e1_engine
        await executor_live_e1_engine.place_traveler_entry_order(db, account, traveler_plan_row, order)


async def process_traveler_fill(db: Session, traveler_plan_row: TravelerPlan) -> None:
    accounts = db.query(ExecutorAccount).filter_by(is_active=True).all()
    for account in accounts:
        try:
            await _process_traveler_account(db, traveler_plan_row, account)
        except Exception as e:
            print(f"|| EXECUTOR || account {account.id} ({account.label}) failed for "
                  f"traveler_plan {traveler_plan_row.id}: {e}")
