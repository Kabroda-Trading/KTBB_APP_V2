# executor_plan_builder.py
# ==============================================================================
# EXECUTOR PLAN BUILDER -- reads an already-FILLED TradePlan row + an
# account's own risk state, and computes the hypothetical order that
# account would place. Pure "compute and return," writes nothing itself --
# the caller (executor_engine.py) owns persistence. This is the layer
# that never re-decides the trade: direction/entry/stop/T1/T2/T3 all come
# straight off the TradePlan row, verbatim. Stage 1 of the Bitunix
# executor bot.
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy.orm import Session

import executor_accounts
import executor_sizing
from database import ExecutorAccount, ExecutorOrder, ExecutorRiskState, TradePlan


def build_hypothetical_order(
    db: Session, trade_plan_row: TradePlan, account: ExecutorAccount, risk_state: ExecutorRiskState,
) -> Dict[str, Any]:
    base = {
        "trade_plan_id": trade_plan_row.id,
        "account_id": account.id,
        "mode": account.mode,
        "symbol": trade_plan_row.symbol,
        "direction": trade_plan_row.direction,
    }

    tradeable, reason = executor_accounts.is_account_tradeable(db, account)
    if not tradeable:
        decision = "SKIPPED_KILL_SWITCH" if "kill switch" in reason else "SKIPPED_ACCOUNT_INACTIVE"
        return {**base, "decision": decision, "decision_reason": reason}

    # Idempotency: an order already exists for this EXACT (trade_plan_id,
    # account_id) pair -- the DB's own unique constraint would refuse a
    # second insert anyway; check here first for a clean decision/reason
    # instead of relying on a caller catching an IntegrityError.
    dup = db.query(ExecutorOrder).filter_by(account_id=account.id, trade_plan_id=trade_plan_row.id).first()
    if dup is not None:
        return {**base, "decision": "SKIPPED_ALREADY_IN_TRADE", "decision_reason": "an order already exists for this exact trade plan + account"}

    # One-trade-at-a-time per account (Andy's methodology: one trade at a
    # time). Stage 1 has no real position/fill tracking to check against
    # (documented non-goal), so this checks against this bot's OWN
    # would-place record for any OTHER trade plan that isn't DONE yet --
    # an approximation, not a guarantee, until Stage 2/3 add real fill
    # detection.
    other_would_places = db.query(ExecutorOrder).filter(
        ExecutorOrder.account_id == account.id,
        ExecutorOrder.decision == "WOULD_PLACE",
        ExecutorOrder.trade_plan_id != trade_plan_row.id,
    ).all()
    for other in other_would_places:
        other_plan = db.query(TradePlan).filter_by(id=other.trade_plan_id).first()
        if other_plan is not None and other_plan.status != "DONE":
            return {
                **base, "decision": "SKIPPED_ALREADY_IN_TRADE",
                "decision_reason": f"account already has an active order from trade_plan_id={other.trade_plan_id}",
            }

    entry_price = trade_plan_row.fill_price or trade_plan_row.trigger_price
    stop_price = trade_plan_row.stop_price
    direction = trade_plan_row.direction
    if not entry_price or not stop_price or not direction:
        return {**base, "decision": "ERROR", "decision_reason": "trade plan is missing entry/stop/direction -- cannot size"}

    try:
        qty = executor_sizing.compute_qty(risk_state.risk_last_usd, entry_price, stop_price)
    except ValueError as e:
        return {**base, "decision": "ERROR", "decision_reason": f"sizing failed: {e}"}

    leverage, lev_detail = executor_sizing.suggest_leverage(
        entry_price=entry_price, stop_price=stop_price, direction=direction, qty=qty,
        leverage_baseline=account.leverage_baseline, free_balance_usd=account.assumed_balance_usd,
        max_margin_pct=account.max_margin_pct_of_balance,
    )
    liq_price = executor_sizing.estimate_liquidation_price(entry_price, leverage, direction)
    liq_ok, liq_detail = executor_sizing.check_liquidation_safety(entry_price, stop_price, liq_price, direction)
    margin_required = (entry_price * qty) / leverage

    result = {
        **base,
        "entry_price": entry_price, "stop_price": stop_price,
        "t1_price": trade_plan_row.t1, "t2_price": trade_plan_row.t2, "t3_price": trade_plan_row.t3,
        "risk_dollars_used": risk_state.risk_last_usd,
        "stop_distance": abs(entry_price - stop_price),
        "qty": qty, "leverage_used": leverage,
        "margin_required_usd": margin_required,
        "liquidation_price_estimate": liq_price,
        "liquidation_check_passed": liq_ok,
        "liquidation_check_detail": liq_detail,
    }
    if not liq_ok:
        return {**result, "decision": "REJECTED", "decision_reason": liq_detail}
    return {**result, "decision": "WOULD_PLACE", "decision_reason": f"{lev_detail}; {liq_detail}"}
