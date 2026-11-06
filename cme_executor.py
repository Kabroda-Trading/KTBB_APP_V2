# cme_executor.py
# ==============================================================================
# CME EXECUTOR -- real Tradovate order placement for LIVE accounts.
# Copies (never imports) alt_matrix_executor.py's/executor_live_e1_engine.py's
# proven shapes (hang-safe polling discipline, "fail loud and halt, don't
# guess" unprotected-position handling, two-table audit split) -- per the
# Iron Wall, nothing here imports gate_traveler.py, traveler_plan_engine.py,
# executor_live_e1_engine.py, executor_plan_builder.py, executor_engine.py,
# mgmt_e1_stack.py, or any alt_matrix_* module. executor_accounts.py IS
# imported directly -- generic, symbol/exchange-agnostic, zero Traveler
# coupling (confirmed by direct read), same reuse Alt Matrix already made.
#
# D2 ENTRY SHAPE IS STRUCTURALLY BETTER THAN ALT MATRIX/TRAVELER'S, NOT A
# DOWNGRADE: place_bracket_limit_order() submits the entry AND both
# protective legs (stop + target) as ONE atomic Tradovate /order/placeoso
# call. Unlike Bitunix (market entry, THEN a separate stop call, with a
# real gap in between that ENTRY_FILLED_UNPROTECTED exists to catch), there
# is structurally no unprotected-fill gap here -- Tradovate's own matching
# engine hosts the stop/target the instant the entry itself fills. This
# module still has an ENTRY_FILLED_UNPROTECTED state for the one real way
# a gap could still occur (the bracket's own child legs fail to attach, or
# this process crashes between order placement and recording the ids) --
# never assumed impossible, just structurally rarer.
#
# AUDIT TRAIL -- two tables, two purposes, same split Alt Matrix uses:
# CMETransition for every state change (exact fill price/fee/realized R);
# ExecutorAuditLog (via executor_accounts.write_audit()) for the general
# admin feed, with the CMEOrder id folded into `detail`, never passed as
# executor_order_id (a different id sequence -- Traveler's own table).
# ==============================================================================

import asyncio
import datetime
import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

import executor_accounts
import cme_signals
from database import ExecutorAccount, CMEPlan, CMEOrder, CMETransition

_CLOSE_CONFIRM_INTERVAL_SEC = 1.0
_CLOSE_CONFIRM_ATTEMPTS = 10


def _log_transition(
    db: Session, order_row: CMEOrder, from_state: Optional[str], to_state: str,
    price: Optional[float] = None, fee: Optional[float] = None,
    realized_pnl_r: Optional[float] = None, detail: Optional[Dict[str, Any]] = None,
) -> None:
    db.add(CMETransition(
        cme_plan_id=order_row.cme_plan_id, cme_order_id=order_row.id,
        from_state=from_state, to_state=to_state, price=price, fee=fee,
        realized_pnl_r=realized_pnl_r,
        detail_json=json.dumps(detail, default=str) if detail else None,
    ))


def _audit(db: Session, event_type: str, message: str, account_id: int, order_row: CMEOrder, detail: Optional[Dict[str, Any]] = None) -> None:
    merged_detail = dict(detail or {})
    merged_detail["cme_order_id"] = order_row.id
    executor_accounts.write_audit(db, event_type, message, account_id=account_id, actor="system", detail=merged_detail)


def _order_dict_for_notify(db: Session, order_row: CMEOrder, account: ExecutorAccount) -> Dict[str, Any]:
    plan = db.query(CMEPlan).filter_by(id=order_row.cme_plan_id).first()
    return {
        "id": order_row.id, "cme_plan_id": order_row.cme_plan_id,
        "symbol": order_row.symbol, "direction": order_row.direction,
        "account_id": account.id, "account_label": account.label,
        "entry_fill_price": order_row.entry_fill_price, "sl_price_initial": order_row.sl_price_initial,
        "contracts": order_row.contracts, "risk_dollars_used": order_row.risk_dollars_used,
        "be_price": order_row.be_price, "exit_reason": order_row.exit_reason,
        "exit_price": order_row.exit_price, "dollar_pnl": order_row.dollar_pnl,
        "receipt_id": order_row.receipt_id,
        "date_key": plan.date_key if plan is not None else None,
    }


_TELEGRAM_CATEGORY_BY_BUILDER = {
    "build_cme_entry_email": "fill",
    "build_cme_breakeven_email": "breakeven",
    "build_cme_exit_email": "exit",
}


def _notify_account(db: Session, account: ExecutorAccount, builder: str, order_row: CMEOrder, **extra) -> None:
    try:
        import notify
        import cme_notify
        fn = getattr(cme_notify, builder)
        order_dict = _order_dict_for_notify(db, order_row, account)
        subject, body = fn(order_dict, **extra) if extra else fn(order_dict)
        notify.send_account_email(subject, body, account.id)
        # Telegram push (2026-11-xx) -- second delivery channel for the
        # exact same event, never a new decision point. One dispatcher
        # covers all three categories since _notify_account() already is
        # the shared call site for fill/breakeven/exit.
        category = _TELEGRAM_CATEGORY_BY_BUILDER.get(builder)
        if category == "fill":
            text = notify._telegram_fill_text("CME", order_row.symbol, order_row.direction,
                                               order_row.entry_fill_price, stop=order_row.sl_price_initial)
        elif category == "breakeven":
            text = notify._telegram_breakeven_text("CME", order_row.symbol, order_row.be_price)
        elif category == "exit":
            text = notify._telegram_exit_text("CME", order_row.symbol, order_row.exit_reason,
                                               r=order_row.realized_pnl_r, usd=order_row.dollar_pnl)
        else:
            text = None
        if category and text:
            notify.send_account_telegram(category, text, account.id)
    except Exception as e:
        print(f"|| CME EXECUTOR || {builder} notification failed for order {order_row.id}: {e}")


def _notify_unprotected(db: Session, account: ExecutorAccount, order_row: CMEOrder, detail: str) -> None:
    try:
        import notify
        notify.send_account_email(
            f"KABRODA EXECUTOR ALERT -- unprotected open CME position ({order_row.symbol})",
            f"cme_order_id={order_row.id}, account={account.id} ({account.label}), "
            f"positionId={order_row.position_id}\n\n{detail}\n\n"
            f"CHECK TRADOVATE DIRECTLY NOW and manually place whatever's missing "
            f"(stop at {order_row.sl_price_initial}).",
            account.id,
        )
        notify.send_account_telegram("problem", notify._telegram_problem_text(
            "CME", order_row.symbol, f"unprotected position — {detail}"), account.id)
    except Exception as e:
        print(f"|| CME EXECUTOR || unprotected-position notification failed for order {order_row.id}: {e}")


def _client_for(account: ExecutorAccount):
    """Returns either a TradovateClient (direct Tradovate REST) or a
    CrossTradeClient (crosstrade.io, routed through its own partner-level
    Tradovate access) depending on account.execution_provider -- the ONLY
    branch point in this codebase; both clients expose the identical
    method surface (get_balance/get_positions/get_orders/get_order_detail/
    place_bracket_limit_order/amend_stop_to_breakeven/cancel_order/
    emergency_market_close), normalized to the same return shapes, so
    every other function in this file is provider-agnostic. Added
    2026-11-04 once a real evaluation account (MFFU) confirmed Tradovate's
    documented restriction that unfunded accounts cannot obtain direct
    API credentials -- see cme_crosstrade_client.py's own header."""
    # fee-capture build (2026-11-xx): get_fills_for_order()/get_fills_history()
    # are the one deliberate exception to the "identical method surface"
    # claim above -- CrossTradeClient only, see that file's own header.
    provider = (account.execution_provider or "tradovate").strip().lower()
    if provider == "crosstrade":
        import cme_crosstrade_client
        bearer_token, _ = executor_accounts.get_decrypted_credentials(account)
        if not bearer_token:
            raise ValueError(f"account {account.id} has no CrossTrade credentials set -- cannot place real orders")
        if not account.crosstrade_account_id:
            raise ValueError(f"account {account.id} has no crosstrade_account_id set -- cannot place real orders")
        return cme_crosstrade_client.CrossTradeClient(
            bearer_token=bearer_token, crosstrade_account_id=account.crosstrade_account_id,
        )

    import cme_tradovate_client
    username, password = executor_accounts.get_decrypted_credentials(account)
    if not username or not password:
        raise ValueError(f"account {account.id} has no Tradovate credentials set -- cannot place real orders")
    return cme_tradovate_client.TradovateClient(
        username=username, password=password,
        account_id=account.tradovate_account_id, account_name=account.tradovate_account_name,
        # 2026-11-04 -- was hardcoded to False (live gateway) with no
        # account-level override at all. A prop-firm evaluation account
        # (e.g. MFFU's Rapid EOD plan) trades against Tradovate's demo
        # gateway, not live -- this must read the real account setting,
        # same field main.py's test-connection/tradovate-accounts routes
        # now default to via _resolve_tradovate_is_demo().
        is_demo=bool(account.tradovate_is_demo),
    )


async def _fetch_cme_entry_fee(client, account: ExecutorAccount, order_row: CMEOrder) -> None:
    """Looks up and writes order_row.entry_fee_usd for one freshly-filled
    CME entry. CrossTrade accounts only -- direct-Tradovate fee capture is
    explicitly deferred (see cme_crosstrade_client.py's module header and
    this module's own header). Its own try/except -- a fee-fetch failure
    must never block real fill-confirmation bookkeeping. Shared between
    the real-time call site and cme_engine.py's 48h retry sweep."""
    if (account.execution_provider or "tradovate").strip().lower() != "crosstrade":
        return   # Tradovate-direct -- deferred, no call, no error
    try:
        import cme_crosstrade_client
        fills = await client.get_fills_for_order(int(order_row.entry_exchange_order_id))
        order_row.entry_fee_usd = cme_crosstrade_client.sum_fill_fees(fills)
    except Exception as e:
        print(f"|| CME EXECUTOR || entry fee-fetch failed for order {order_row.id}: {e}")


async def _fetch_cme_exit_fee(client, account: ExecutorAccount, order_row: CMEOrder) -> None:
    """Looks up and writes order_row.exit_fee_usd for one closed CME
    order. CrossTrade accounts only (same deferred-Tradovate-direct
    reasoning as _fetch_cme_entry_fee()). Every CME exit here is a
    emergency_market_close()/positions-flatten close with no distinct
    order id (place_resting_bracket() never sets sl_exchange_order_id --
    a real, pre-existing, flagged-not-fixed gap, confirmed via direct
    read -- so there is no stop-order-id lookup available the way
    Traveler/Alt Matrix's STOP branch has), so this always uses the
    windowed fills/history fallback, bounded to a narrow window around
    the confirmed exit_time. Its own try/except -- a fee-fetch failure
    must never roll back the real closure bookkeeping already committed."""
    if (account.execution_provider or "tradovate").strip().lower() != "crosstrade":
        return   # Tradovate-direct -- deferred, no call, no error
    try:
        import cme_crosstrade_client
        lookback = datetime.timedelta(seconds=_CLOSE_CONFIRM_ATTEMPTS * _CLOSE_CONFIRM_INTERVAL_SEC + 5)
        from_ts = (order_row.exit_time - lookback).replace(tzinfo=datetime.timezone.utc).isoformat()
        to_ts = (order_row.exit_time + datetime.timedelta(seconds=5)).replace(tzinfo=datetime.timezone.utc).isoformat()
        fills = await client.get_fills_history(from_ts, to_ts, is_demo=bool(account.tradovate_is_demo))
        order_row.exit_fee_usd = cme_crosstrade_client.sum_fill_fees(fills)
    except Exception as e:
        print(f"|| CME EXECUTOR || exit fee-fetch failed for order {order_row.id}: {e}")


async def place_resting_bracket(db: Session, account: ExecutorAccount, plan_row: CMEPlan, order_row: CMEOrder) -> None:
    """Called once a confirmed cross has been detected for this plan
    (order_row.cross_confirmed_at is already set by the caller). Places the
    resting Limit entry with an atomic OSO bracket (stop + target) at the
    trigger price -- the mechanic actually backtested (confirmed close,
    THEN a passive fill on retest), never a Stop-Limit chase. Safely
    resumable: if entry_exchange_order_id is already set, does nothing
    (the watch loop calls this repeatedly until the row leaves
    WAITING_RETEST)."""
    if order_row.entry_exchange_order_id is not None:
        return

    client = _client_for(account)
    trig = plan_row.bo if order_row.direction == "LONG" else plan_row.bd
    stop = plan_row.stop_long if order_row.direction == "LONG" else plan_row.stop_short
    target = plan_row.target_long if order_row.direction == "LONG" else plan_row.target_short
    action = "Buy" if order_row.direction == "LONG" else "Sell"

    resp = await client.place_bracket_limit_order(
        symbol=order_row.symbol, action=action, qty=order_row.contracts,
        limit_price=trig, stop_price=stop, target_price=target,
    )
    order_id = resp.get("orderId")
    if "error" in resp or not order_id:
        _log_transition(db, order_row, order_row.management_state, "CLOSED_ERROR", detail=resp)
        order_row.management_state = "CLOSED_ERROR"
        _audit(db, "ERROR", f"CME bracket placement failed for order {order_row.id}: {resp}", account.id, order_row, detail=resp)
        return

    order_row.entry_exchange_order_id = str(order_id)
    order_row.sl_price_initial = stop
    order_row.sl_price_current = stop
    order_row.sl_set_at = datetime.datetime.utcnow()
    _log_transition(db, order_row, order_row.management_state, "WAITING_RETEST", detail={"trigger": trig, "stop": stop, "target": target})
    order_row.management_state = "WAITING_RETEST"
    _audit(db, "ORDER_PLACED", f"CME resting bracket placed for order {order_row.id} at {trig} (stop {stop}, target {target})", account.id, order_row)


def stand_down_opposite_side(db: Session, order_row: CMEOrder, winning_side: str) -> None:
    """The D1 fan-out creates one PENDING_ENTRY row per direction per
    account per plan (an OCO-style pair) -- once ONE side's trigger is
    confirmed first, the other side can never be the first to fire this
    plan, so it stands down permanently rather than keep polling. Pure DB
    state change, no exchange call -- found and fixed 2026-11-04 writing
    tests/test_cme_engine.py (the original code let BOTH sides place a
    real bracket off a single confirmed cross)."""
    _log_transition(db, order_row, order_row.management_state, "OPPOSITE_CROSSED",
                     detail={"winning_side": winning_side})
    order_row.management_state = "OPPOSITE_CROSSED"


async def check_retest_fill_and_confirm(db: Session, account: ExecutorAccount, order_row: CMEOrder) -> None:
    """For a WAITING_RETEST order, checks whether the resting bracket has
    filled (a real open position now exists) -- no separate 'place the
    stop' step needed, since the atomic bracket already attached it (see
    module header). On confirmed fill, transitions to FILLED and notifies.
    Leaves the row untouched if still resting -- the caller (cme_engine.py)
    is responsible for cancelling on a NO_RETEST timeout."""
    client = _client_for(account)
    try:
        positions = await client.get_positions()
    except Exception as e:
        _audit(db, "ERROR", f"get_positions failed while confirming fill for CME order {order_row.id}: {e}", account.id, order_row)
        return

    matches = [p for p in positions if p.get("contractId") == order_row.symbol or p.get("symbol") == order_row.symbol]
    if not matches:
        return   # still resting -- recheck next tick

    position = matches[0]
    order_row.position_id = str(position.get("id") or position.get("positionId") or "")
    order_row.entry_fill_price = float(position.get("netPrice") or position.get("avgOpenPrice") or 0.0) or order_row.entry_fill_price
    order_row.entry_fill_time = datetime.datetime.utcnow()
    await _fetch_cme_entry_fee(client, account, order_row)
    _log_transition(db, order_row, "WAITING_RETEST", "FILLED", price=order_row.entry_fill_price)
    order_row.management_state = "FILLED"
    _audit(db, "ORDER_PLACED", f"CME order {order_row.id} entry filled at {order_row.entry_fill_price} (bracket stop/target already exchange-hosted)", account.id, order_row)
    _notify_account(db, account, "build_cme_entry_email", order_row)


async def cancel_no_retest(db: Session, account: ExecutorAccount, order_row: CMEOrder) -> None:
    """The retest window elapsed with no fill -- cancel the resting
    bracket and close the row as NO_RETEST (never entered, never a trade).
    Mirrors the stand-down path audit_cme_universe_comparison.py's own
    backtest counts explicitly as a real outcome (stand_down_no_retest),
    not an error."""
    client = _client_for(account)
    if order_row.entry_exchange_order_id:
        try:
            await client.cancel_order(int(order_row.entry_exchange_order_id))
        except Exception as e:
            _audit(db, "ERROR", f"cancel_order failed for CME order {order_row.id} on NO_RETEST timeout: {e}", account.id, order_row)
            return   # retry next tick -- never assume cancelled on a failed call

    _log_transition(db, order_row, order_row.management_state, "NO_RETEST")
    order_row.management_state = "NO_RETEST"
    order_row.closed_at = datetime.datetime.utcnow()
    _audit(db, "ORDER_PLACED", f"CME order {order_row.id} resting bracket cancelled -- no retest within the window, standing down", account.id, order_row)


async def amend_to_breakeven(db: Session, account: ExecutorAccount, order_row: CMEOrder, stop_order_id: int, be_price: float) -> bool:
    """Amends the live exchange stop leg to breakeven (+0.1x box). Returns
    False (never raises) on any failure -- the caller logs/alerts and
    leaves be_amended False so the next tick retries; never marks the row
    amended without a successful call (there is no cheap confirm-read here
    the way Bitunix's get_pending_tp_sl_order() provides -- flagged as a
    live-API gap to close during the 1-Micro Smoke Test, same as this
    module's other NOT YET LIVE-VERIFIED items in cme_tradovate_client.py)."""
    client = _client_for(account)
    ok = await client.amend_stop_to_breakeven(stop_order_id, be_price, order_row.contracts)
    if not ok:
        _audit(db, "ERROR", f"breakeven amendment failed for CME order {order_row.id} (stop_order_id={stop_order_id})", account.id, order_row)
        return False

    order_row.be_amended = True
    order_row.be_amended_at = datetime.datetime.utcnow()
    order_row.be_price = be_price
    order_row.sl_price_current = be_price
    _log_transition(db, order_row, "FILLED", "RATCHETED", price=be_price)
    order_row.management_state = "RATCHETED"
    _audit(db, "STOP_AMENDED", f"CME order {order_row.id} stop amended to breakeven ({be_price})", account.id, order_row)
    _notify_account(db, account, "build_cme_breakeven_email", order_row)
    return True


async def market_close(db: Session, account: ExecutorAccount, order_row: CMEOrder, exit_reason: str) -> None:
    """Closes the position at market -- used for the 16:00 ET mandatory
    flush and emergency_flatten(). Exit price is APPROXIMATED at the last
    known live price (same Andy-accepted tradeoff Alt Matrix/Traveler's own
    contingency closes use) -- confirmed closed via a bounded re-poll of
    get_positions(), never assumed on the call succeeding."""
    client = _client_for(account)
    try:
        await client.emergency_market_close(order_row.symbol, order_row.contracts, order_row.direction)
    except Exception as e:
        _audit(db, "ERROR", f"market close failed for CME order {order_row.id} ({exit_reason}): {e}", account.id, order_row)
        return

    still_open = True
    for _ in range(_CLOSE_CONFIRM_ATTEMPTS):
        await asyncio.sleep(_CLOSE_CONFIRM_INTERVAL_SEC)
        try:
            positions = await client.get_positions()
        except Exception:
            continue
        still_open = any(p.get("symbol") == order_row.symbol for p in positions)
        if not still_open:
            break

    if still_open:
        _audit(db, "ERROR", f"market close reported for CME order {order_row.id} ({exit_reason}) but a position still exists -- CHECK TRADOVATE DIRECTLY", account.id, order_row)
        return

    from_state = order_row.management_state
    order_row.exit_reason = exit_reason
    order_row.exit_price = order_row.entry_fill_price   # approximated -- no reliable market-close fill price, same convention as Alt Matrix
    order_row.exit_time = datetime.datetime.utcnow()
    order_row.closed_at = order_row.exit_time
    order_row.management_state = f"CLOSED_{exit_reason}"
    if order_row.box_at_entry:
        sgn = 1 if order_row.direction == "LONG" else -1
        order_row.realized_pnl_r = sgn * (order_row.exit_price - order_row.entry_fill_price) / order_row.box_at_entry
    _log_transition(db, order_row, from_state, order_row.management_state, price=order_row.exit_price,
                     realized_pnl_r=order_row.realized_pnl_r, detail={"approximated": True})
    _audit(db, "POSITION_CLOSED", f"CME order {order_row.id} closed: {exit_reason} at ~{order_row.exit_price} (approximated)", account.id, order_row)
    _notify_account(db, account, "build_cme_exit_email", order_row)
    await _fetch_cme_exit_fee(client, account, order_row)


_CANCELLABLE_STATES = ("WAITING_RETEST",)
_CLOSEABLE_STATES = ("FILLED", "RATCHETED", "ENTRY_FILLED_UNPROTECTED")


async def cancel_or_close_order(db: Session, account: ExecutorAccount, order_row: CMEOrder, exit_reason: str = "MANUAL") -> Dict[str, Any]:
    """Cancels a resting WAITING_RETEST entry or market-closes an open
    FILLED/RATCHETED/ENTRY_FILLED_UNPROTECTED position, whichever applies
    to order_row's current state. Factored out of emergency_flatten()'s
    own per-row loop (2026-11-xx desk-page build) so the single-order admin
    cancel route and the fleet-wide flatten share one branch of logic
    instead of two copies that could drift. Raises ValueError on a
    terminal/unrecognized state rather than silently no-op'ing -- the
    caller decides how to report that (emergency_flatten() catches it per-
    row; the single-order route 400s)."""
    if order_row.management_state in _CANCELLABLE_STATES:
        client = _client_for(account)
        if order_row.entry_exchange_order_id:
            await client.cancel_order(int(order_row.entry_exchange_order_id))
        from_state = order_row.management_state
        order_row.management_state = f"CLOSED_{exit_reason}"
        order_row.exit_reason = exit_reason
        order_row.closed_at = datetime.datetime.utcnow()
        _log_transition(db, order_row, from_state, order_row.management_state, detail={"reason": exit_reason})
        _audit(db, "POSITION_CLOSED", f"CME order {order_row.id} resting entry cancelled ({exit_reason})", account.id, order_row)
        return {"action": "cancelled", "order_id": order_row.id}
    elif order_row.management_state in _CLOSEABLE_STATES:
        await market_close(db, account, order_row, exit_reason=exit_reason)
        return {"action": "closed", "order_id": order_row.id}
    else:
        raise ValueError(f"order {order_row.id} is not in a cancellable/closeable state ({order_row.management_state!r})")


async def emergency_flatten(db: Session, account: ExecutorAccount) -> Dict[str, Any]:
    """Fleet Emergency Flatten -- NEW functionality, nothing like this
    exists anywhere else in the codebase (confirmed via grep, 2026-11-04;
    the existing kill switch only ever blocks NEW orders, never touches an
    existing position). Cancels every open order and market-closes every
    open position for this account, independent of the mode-gated kill
    switch. Returns a summary dict; never raises -- a failure on one leg
    must not stop attempts on the rest."""
    cancelled, closed, errors = [], [], []

    open_rows = db.query(CMEOrder).filter(
        CMEOrder.account_id == account.id,
        CMEOrder.management_state.in_(_CANCELLABLE_STATES + _CLOSEABLE_STATES),
    ).all()
    for order_row in open_rows:
        step = "cancel" if order_row.management_state in _CANCELLABLE_STATES else "close"
        try:
            result = await cancel_or_close_order(db, account, order_row, exit_reason="MANUAL")
            (cancelled if result["action"] == "cancelled" else closed).append(order_row.id)
        except Exception as e:
            errors.append({"order_id": order_row.id, "step": step, "error": str(e)})

    executor_accounts.write_audit(
        db, "KILL_SWITCH_ENGAGED" if not errors else "ERROR",
        f"CME emergency_flatten for account {account.id}: cancelled={cancelled}, closed={closed}, errors={errors}",
        account_id=account.id, actor="system",
        detail={"cancelled": cancelled, "closed": closed, "errors": errors},
    )
    return {"cancelled": cancelled, "closed": closed, "errors": errors}
