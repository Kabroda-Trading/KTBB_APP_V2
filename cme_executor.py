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
import executor_control
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
        "exit_price_source": order_row.exit_price_source,
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
            reason_label = order_row.exit_reason
            if (order_row.exit_price_source or "BROKER_FILL") != "BROKER_FILL":
                reason_label = f"{reason_label} (approx. price)"   # Lane H C2.5 labelling
            text = notify._telegram_exit_text("CME", order_row.symbol, reason_label,
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


def _notify_owner_problem(account: ExecutorAccount, order_row: CMEOrder, subject_tail: str, body: str) -> None:
    """LIVE-only owner alert for a refusal/cancel decided by this module.
    Never raises (same pattern as _notify_unprotected)."""
    if (account.mode or "").upper() != "LIVE":
        return
    try:
        import notify
        notify.send_account_email(
            f"KABRODA CME ALERT -- {order_row.symbol} {order_row.direction} {subject_tail} ({account.label or account.id})",
            body, account.id, category="problem",
        )
        notify.send_account_telegram("problem", notify._telegram_problem_text(
            "CME", order_row.symbol, f"{order_row.direction} {subject_tail}"), account.id)
    except Exception as e:
        print(f"|| CME EXECUTOR || owner notification failed for order {order_row.id}: {e}")


def refuse_before_placement(db: Session, account: ExecutorAccount, order_row: CMEOrder, terminal_state: str, reason: str) -> None:
    """Lane H (SPEC-H-20261114 C7.3 step 2 / C9.3 step 2): a confirmed
    cross that may NOT be turned into a real order. No exchange call. The
    row goes terminal (never deferred: releasing a switch later in the
    day must not resurrect a stale entry -- spec N-5)."""
    from_state = order_row.management_state
    order_row.management_state = terminal_state
    order_row.decision_reason = reason
    order_row.closed_at = datetime.datetime.utcnow()
    _log_transition(db, order_row, from_state, terminal_state, detail={"reason": reason})
    _audit(db, "ERROR", f"LIVE-mode CME order {order_row.id} skipped real order placement -- {reason}", account.id, order_row)
    _notify_owner_problem(account, order_row, "entry NOT placed",
                          f"A confirmed {order_row.symbol} {order_row.direction} cross was not turned into an order.\n\n"
                          f"Reason: {reason}\n\nNo order was sent. No automatic retry.")


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

    # Lane H C9 (SPEC-H-20261114, AA-20261114-01): defense in depth at the
    # ONLY call that opens new CME exchange exposure. Mirrors the Traveler
    # LIVE gate (executor_engine.py). is_live_orders_enabled fails closed.
    # Never applied to fills/cancels/BE/closes -- existing exposure is
    # always managed (C9.3 step 3).
    if (account.mode or "").upper() == "LIVE" and not executor_control.is_live_orders_enabled(db):
        refuse_before_placement(db, account, order_row, "SKIPPED_LIVE_ORDERS_OFF", "global Live Orders switch is OFF")
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
    # Lane H C1.3 step 1: store the bracket leg ids if the placement
    # response carries them. Key names are CANDIDATES, not verified; the raw
    # response is kept in the transition so the first live placement shows
    # the real names.
    sl_leg = _first_key(resp, _STOP_LEG_KEYS)
    tp_leg = _first_key(resp, _TARGET_LEG_KEYS)
    if sl_leg:
        order_row.sl_exchange_order_id = sl_leg
    if tp_leg:
        order_row.tp_exchange_order_id = tp_leg
    _log_transition(db, order_row, order_row.management_state, "WAITING_RETEST",
                    detail={"trigger": trig, "stop": stop, "target": target, "raw_placement_response": resp})
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

    matches = [p for p in positions if _position_matches(p, order_row.symbol)]
    if not matches:
        return   # still resting -- recheck next tick

    position = matches[0]
    order_row.position_id = str(position.get("id") or position.get("positionId") or "")
    order_row.entry_fill_price = float(position.get("netPrice") or position.get("avgOpenPrice") or 0.0) or order_row.entry_fill_price
    order_row.entry_fill_time = datetime.datetime.utcnow()
    order_row.entry_price_source = "BROKER_POSITION_AVG"   # Lane H C2.5: position avg, not a fill record
    await _fetch_cme_entry_fee(client, account, order_row)
    from_state = order_row.management_state
    _log_transition(db, order_row, from_state, "FILLED", price=order_row.entry_fill_price)
    order_row.management_state = "FILLED"
    _audit(db, "ORDER_PLACED", f"CME order {order_row.id} entry filled at {order_row.entry_fill_price} (bracket stop/target already exchange-hosted)", account.id, order_row)
    _notify_account(db, account, "build_cme_entry_email", order_row)
    # Lane H C1.3 step 2-3: prove the stop leg exists at the broker.
    if not order_row.sl_exchange_order_id:
        await ensure_bracket_legs(db, account, order_row, client=client)


# ------------------------------------------------------------------ Lane H C1: bracket legs

# Candidate response keys for the OSO legs. NOT LIVE-VERIFIED (spec C1.3):
# Tradovate maps bracket1 = target, bracket2 = stop (cme_tradovate_client).
_STOP_LEG_KEYS = ("oso2Id", "bracket2Id", "stopLossOrderId")
_TARGET_LEG_KEYS = ("oso1Id", "bracket1Id", "takeProfitOrderId")


def _first_key(d: Any, keys) -> Optional[str]:
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return str(v)
    return None


def _num(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _field(o: Dict[str, Any], keys) -> Any:
    for k in keys:
        if o.get(k) is not None:
            return o.get(k)
    return None


def _order_id_of(o: Dict[str, Any]) -> Optional[str]:
    v = _field(o, ("id", "orderId"))
    return None if v is None else str(v)


def _order_type_of(o: Dict[str, Any]) -> str:
    return str(_field(o, ("orderType", "ordType", "type")) or "").lower()


def _order_action_of(o: Dict[str, Any]) -> str:
    return str(_field(o, ("action", "side")) or "").lower()


def _order_stop_price_of(o: Dict[str, Any]) -> Optional[float]:
    return _num(_field(o, ("stopPrice", "stop_price", "triggerPrice")))


def _order_limit_price_of(o: Dict[str, Any]) -> Optional[float]:
    return _num(_field(o, ("price", "limitPrice")))


def _same_price(a: Optional[float], b: Optional[float]) -> bool:
    return a is not None and b is not None and abs(round(a, 2) - round(b, 2)) < 1e-9


def _is_working_order(o: Dict[str, Any]) -> bool:
    # An unreadable status counts as possibly working; a known terminal
    # status (filled/cancelled/expired/rejected) does not.
    status = _order_status_of(o)
    return status is None or status not in (_ORDER_GONE_STATUSES | {"filled"})


def _plan_target(db: Session, order_row: CMEOrder) -> Optional[float]:
    plan = db.query(CMEPlan).filter_by(id=order_row.cme_plan_id).first()
    if plan is None:
        return None
    return plan.target_long if order_row.direction == "LONG" else plan.target_short


async def ensure_bracket_legs(db: Session, account: ExecutorAccount, order_row: CMEOrder, client=None) -> str:
    """Lane H C1.3 steps 2-4 (SPEC-H-20261114): find the bracket's stop (and
    target) leg among the broker's working orders. Returns:
      "found"   -- exactly one stop leg matched; id stored. An
                   ENTRY_FILLED_UNPROTECTED row is promoted back to FILLED.
      "none"    -- the order list was read and holds NO stop leg -> the row
                   goes ENTRY_FILLED_UNPROTECTED and the owner is alerted
                   (once, on the transition).
      "unknown" -- the query failed, or several candidates match: never
                   guessed, never called unprotected; BE runs in the
                   software fallback; retried on later ticks."""
    client = client or _client_for(account)
    try:
        orders = await client.get_orders()
    except Exception as e:
        _audit(db, "ERROR", f"CME order {order_row.id}: stop leg id unknown (get_orders failed: {e}) -- BE will run in software fallback",
               account.id, order_row)
        return "unknown"
    print(f"[CME EXECUTOR] RAW get_orders for leg discovery, order {order_row.id} (verify field names against this): {orders}")
    exit_action = "sell" if order_row.direction == "LONG" else "buy"
    target = _plan_target(db, order_row)

    def _qty_ok(o):
        q = _num(_field(o, ("orderQty", "qty", "quantity")))
        return q is None or int(q) == int(order_row.contracts or 0)   # absent qty field: not used to reject

    candidates = [o for o in (orders or []) if isinstance(o, dict) and _is_working_order(o)
                  and _order_action_of(o) == exit_action and _qty_ok(o)]
    stops = [o for o in candidates if "stop" in _order_type_of(o) and _same_price(_order_stop_price_of(o), order_row.sl_price_initial)]
    targets = [o for o in candidates if "limit" in _order_type_of(o) and "stop" not in _order_type_of(o)
               and _same_price(_order_limit_price_of(o), target)]
    if len(targets) == 1 and not order_row.tp_exchange_order_id:
        order_row.tp_exchange_order_id = _order_id_of(targets[0])

    if len(stops) == 1:
        order_row.sl_exchange_order_id = _order_id_of(stops[0])
        if order_row.management_state == "ENTRY_FILLED_UNPROTECTED":
            _log_transition(db, order_row, "ENTRY_FILLED_UNPROTECTED", "FILLED", detail={"stop_leg_found": order_row.sl_exchange_order_id})
            order_row.management_state = "FILLED"
            _audit(db, "ORDER_PLACED", f"CME order {order_row.id} stop leg now found ({order_row.sl_exchange_order_id}) -- protected again",
                   account.id, order_row)
        return "found"
    if not stops:
        if order_row.management_state != "ENTRY_FILLED_UNPROTECTED":
            from_state = order_row.management_state
            _log_transition(db, order_row, from_state, "ENTRY_FILLED_UNPROTECTED", detail={"reason": "stop leg not found among working orders"})
            order_row.management_state = "ENTRY_FILLED_UNPROTECTED"
            _audit(db, "ERROR", f"CME order {order_row.id} UNPROTECTED: stop leg not found among working orders after fill",
                   account.id, order_row)
            _notify_unprotected(db, account, order_row, "stop leg not found among working orders after fill")
        return "none"
    _audit(db, "ERROR", f"CME order {order_row.id}: stop leg id ambiguous ({len(stops)} candidates) -- BE will run in software fallback",
           account.id, order_row)
    return "unknown"


def _flagged_once(db: Session, account: ExecutorAccount, order_row: CMEOrder, tag: str) -> bool:
    """True if an audit row carrying `tag` already exists for this order
    (used to alert once per order, not once per 30 s tick)."""
    from database import ExecutorAuditLog
    db.flush()
    marker = f"[{tag} #{order_row.id}]"
    return db.query(ExecutorAuditLog).filter(
        ExecutorAuditLog.account_id == account.id, ExecutorAuditLog.message.like(f"%{marker}%"),
    ).first() is not None


def _position_matches(position: Dict[str, Any], symbol: str) -> bool:
    return position.get("contractId") == symbol or position.get("symbol") == symbol


# Lane H C5: broker order states. Field names/values are NOT YET
# LIVE-VERIFIED for either client (spec gap G-ORD) -- an unrecognized
# status is treated as "unreadable", never as "still working" or "gone".
_ORDER_WORKING_STATUSES = {"working", "pendingnew", "pending_new", "accepted", "suspended",
                           "pendingreplace", "pendingcancel", "pending_cancel", "filled", "partiallyfilled"}
_ORDER_GONE_STATUSES = {"canceled", "cancelled", "expired", "rejected"}


def _order_status_of(detail: Dict[str, Any]) -> Optional[str]:
    for key in ("ordStatus", "status", "orderStatus", "state"):
        val = detail.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().lower().replace(" ", "")
    return None


async def cancel_resting_entry(db: Session, account: ExecutorAccount, order_row: CMEOrder,
                               terminal_state: str, reason: str) -> str:
    """Lane H C5.3 steps 2-4 (SPEC-H-20261114): cancel a resting entry and
    only book it terminal once the cancel is actually confirmed.
    Returns:
      "cancelled" -- confirmed gone, row booked terminal_state;
      "filled"    -- the entry filled before the cancel landed; the row was
                     handed to check_retest_fill_and_confirm (FILLED path);
      "failed"    -- not confirmed (cancel returned False/raised, positions
                     unreadable, or the order still reads as working). State
                     untouched, ERROR audited, caller retries next tick.
    Both clients' cancel_order RETURN False on a rejected cancel rather
    than raising -- the old code only treated an exception as a failure
    and booked the row closed with the order still resting (a floating
    order)."""
    client = _client_for(account)
    entry_id = order_row.entry_exchange_order_id
    cancel_ok, cancel_err = True, None
    if entry_id:
        try:
            cancel_ok = bool(await client.cancel_order(int(entry_id)))
            if not cancel_ok:
                cancel_err = getattr(client, "last_cancel_error", None) or "cancel_order returned False"
        except Exception as e:
            cancel_ok, cancel_err = False, f"cancel_order raised: {e}"

    # Step 3 -- fill race: a position for this symbol means the entry filled first.
    try:
        positions = await client.get_positions()
    except Exception as e:
        _audit(db, "ERROR", f"CME order {order_row.id} cancel ({reason}): position check failed, state unchanged, retrying: {e}"
               + (f" | cancel error: {cancel_err}" if cancel_err else ""), account.id, order_row)
        return "failed"
    if any(_position_matches(p, order_row.symbol) for p in positions):
        _audit(db, "ERROR", f"CME order {order_row.id} entry FILLED before the cancel ({reason}) landed -- treating as a fill, not {terminal_state}",
               account.id, order_row, detail={"cancel_ok": cancel_ok, "cancel_error": cancel_err})
        await check_retest_fill_and_confirm(db, account, order_row)
        return "filled"
    # Step 4 -- read the entry order's status (also after a REJECTED cancel:
    # H5 finding -- an order the broker already expired, e.g. a CrossTrade
    # day order, can reject the cancel; it is gone, so the row must close).
    verified: Optional[bool] = None
    extra: Dict[str, Any] = {}
    if entry_id:
        try:
            detail = await client.get_order_detail(int(entry_id))
        except Exception:
            detail = {}
        status = None if detail is None else (_order_status_of(detail) if isinstance(detail, dict) else None)
        if status == "filled":
            # H5 finding #9: the entry filled, yet no position exists -- it
            # opened and closed at the broker between two polls. Never book
            # it as a non-trade; alert once and leave it for a human.
            tag = "entry-filled-no-position"
            if not _flagged_once(db, account, order_row, tag):
                _audit(db, "ERROR", f"[{tag} #{order_row.id}] entry order reads FILLED but no position exists -- the trade may have "
                       "opened and closed at the broker between polls. NOT booked. CHECK TRADOVATE and record it by hand.",
                       account.id, order_row)
                _notify_owner_problem(account, order_row, "entry filled but no position found",
                                      "The entry order reads FILLED at the broker but no open position exists. The trade may have "
                                      "opened and closed between checks. Check Tradovate; the system has not booked it.")
            return "failed"
        if not cancel_ok:
            if detail is None or status in _ORDER_GONE_STATUSES:
                verified, extra = True, {"cancel_rejected_but_order_gone": cancel_err}
            else:
                _audit(db, "ERROR", f"CME order {order_row.id} cancel failed ({reason}) -- order may still be resting, state unchanged, retrying: {cancel_err}",
                       account.id, order_row, detail={"cancel_error": cancel_err})
                return "failed"
        elif detail is None:
            verified = True
        else:
            if status in _ORDER_WORKING_STATUSES:
                _audit(db, "ERROR", f"CME order {order_row.id} cancel reported OK but the order still reads {status!r} ({reason}) -- not booked, retrying",
                       account.id, order_row)
                return "failed"
            # unreadable -> False, accepted with the empty-position check (spec C5.3 step 4)
            verified = status in _ORDER_GONE_STATUSES
    elif not cancel_ok:
        return "failed"

    # Step 5 -- spec C5.4: with the leg ids known (batch 2 C1), make sure no
    # bracket child is still working. A stop leg resting with no position
    # could OPEN a new position if touched, so the row stays open (and the
    # cancel retries) until every known leg is confirmed gone.
    still_working = await _cancel_working_legs(client, order_row)
    if still_working:
        _audit(db, "ERROR", f"CME order {order_row.id} entry cancelled but bracket leg(s) {still_working} still working ({reason}) -- not booked, retrying",
               account.id, order_row)
        return "failed"

    from_state = order_row.management_state
    order_row.management_state = terminal_state
    order_row.closed_at = datetime.datetime.utcnow()
    _log_transition(db, order_row, from_state, terminal_state, detail={"reason": reason, "cancel_verified": verified, **extra})
    _audit(db, "ORDER_PLACED", f"CME order {order_row.id} resting entry cancelled ({reason}) -> {terminal_state}", account.id, order_row,
           detail={"cancel_verified": verified})
    return "cancelled"


async def _cancel_working_legs(client, order_row: CMEOrder) -> List[str]:
    """Spec C5.4: cancel any known bracket leg that still reads as working.
    Returns the ids still working (or unconfirmed) afterwards; [] = clear.
    An unknown leg id is skipped. A leg whose status reads nothing (gone) or
    cannot be read is treated as clear: unverified, listed as a GAP."""
    remaining = []
    for leg_id in (order_row.sl_exchange_order_id, order_row.tp_exchange_order_id):
        if not leg_id:
            continue
        status = await _leg_status(client, leg_id)
        if status is None or status in _ORDER_GONE_STATUSES or status == "filled":
            continue
        try:
            ok = bool(await client.cancel_order(int(leg_id)))
        except Exception:
            ok = False
        after = await _leg_status(client, leg_id) if ok else status
        if not ok or (after is not None and after not in _ORDER_GONE_STATUSES):
            remaining.append(str(leg_id))
    return remaining


async def cancel_no_retest(db: Session, account: ExecutorAccount, order_row: CMEOrder) -> str:
    """The retest window elapsed with no fill -- cancel the resting
    bracket and close the row as NO_RETEST (never entered, never a trade).
    Mirrors the stand-down path audit_cme_universe_comparison.py's own
    backtest counts explicitly as a real outcome (stand_down_no_retest),
    not an error. Lane H C5: the cancel is now verified (see
    cancel_resting_entry) -- a rejected cancel or a fill race never books
    NO_RETEST."""
    return await cancel_resting_entry(db, account, order_row, "NO_RETEST",
                                      "no retest within the window, standing down")


async def amend_to_breakeven(db: Session, account: ExecutorAccount, order_row: CMEOrder, stop_order_id: int, be_price: float) -> bool:
    """Amends the live exchange stop leg to breakeven (+0.1x box). Returns
    False (never raises) on any failure -- the caller then lets the
    software BE replay run and retries next tick; never marks the row
    amended without a successful call.
    Lane H C1.4: after a True return the stop is read back. A read-back
    showing a DIFFERENT price is a failure (not amended). A missing or
    unreadable price is accepted (the API said OK) but recorded as
    unconfirmed. The owner is alerted once per order on a failed amend."""
    client = _client_for(account)
    try:
        ok = await client.amend_stop_to_breakeven(stop_order_id, be_price, order_row.contracts)
    except Exception as e:
        ok = False
        print(f"|| CME EXECUTOR || amend raised for order {order_row.id}: {e}")
    if not ok:
        _audit(db, "ERROR", f"breakeven amendment failed for CME order {order_row.id} (stop_order_id={stop_order_id}) -- software BE fallback active",
               account.id, order_row)
        _alert_be_amend_failed_once(db, account, order_row)
        return False

    confirmed, raw = False, None
    try:
        raw = await client.get_order_detail(int(stop_order_id))
    except Exception as e:
        raw = {"readback_error": str(e)}
    seen = _order_stop_price_of(raw) if isinstance(raw, dict) else None
    if seen is not None and not _same_price(seen, be_price):
        _audit(db, "ERROR", f"breakeven amendment for CME order {order_row.id} read back stop {seen}, expected {round(be_price, 2)} -- NOT marked amended, retrying",
               account.id, order_row, detail={"raw": raw})
        _alert_be_amend_failed_once(db, account, order_row)
        return False
    confirmed = seen is not None

    from_state = order_row.management_state
    order_row.be_amended = True
    order_row.be_amended_at = datetime.datetime.utcnow()
    order_row.be_price = be_price
    order_row.sl_price_current = be_price
    _log_transition(db, order_row, from_state, "RATCHETED", price=be_price,
                    detail={"confirmed": confirmed} if confirmed else {"confirmed": False, "raw": raw})
    order_row.management_state = "RATCHETED"
    _audit(db, "STOP_AMENDED", f"CME order {order_row.id} stop amended to breakeven ({be_price})", account.id, order_row)
    if not confirmed:
        _audit(db, "WARNING", f"CME order {order_row.id} breakeven amend accepted but the new stop price could not be read back (unconfirmed)",
               account.id, order_row, detail={"raw": raw})
    _notify_account(db, account, "build_cme_breakeven_email", order_row)
    return True


def _alert_be_amend_failed_once(db: Session, account: ExecutorAccount, order_row: CMEOrder) -> None:
    tag = "be-amend-alert"
    if _flagged_once(db, account, order_row, tag):
        return
    _audit(db, "ERROR", f"[{tag} #{order_row.id}] owner alerted: breakeven stop could not be moved at the broker", account.id, order_row)
    _notify_owner_problem(account, order_row, "breakeven stop NOT moved at the broker",
                          f"The exchange stop for {order_row.symbol} {order_row.direction} could not be moved to breakeven.\n\n"
                          "The system is enforcing breakeven in software (it will market-close if price returns to the "
                          "breakeven level) and keeps retrying the exchange amendment every 30 seconds.\n\n"
                          "CHECK TRADOVATE if you want the exchange stop moved by hand.")


# ------------------------------------------------------------------ Lane H C2: exit price, reconcile, guarded close

_EXIT_SOURCES_APPROX = ("APPROX_BRACKET_ORDER_PRICE", "APPROX_LAST_5M_CLOSE")


def _fill_price_qty(fills: List[Dict[str, Any]]):
    """Quantity-weighted mean fill price. Field names are NOT LIVE-VERIFIED
    (same caveat as sum_fill_fees). Returns (price or None, total_qty)."""
    num, qty_total = 0.0, 0.0
    for f in fills or []:
        if not isinstance(f, dict):
            continue
        price = _num(_field(f, ("price", "fillPrice", "avgPrice")))
        qty = _num(_field(f, ("qty", "quantity", "fillQty")))
        if price is None or qty is None:
            continue
        num += price * qty
        qty_total += qty
    return (num / qty_total if qty_total > 0 else None), qty_total


async def _broker_exit_fill(client, account: ExecutorAccount, order_row: CMEOrder, leg_id: Optional[str]):
    """Step 1 of the exit-price hierarchy (BROKER_FILL), CrossTrade only.
    Returns (price or None, detail dict)."""
    if (account.execution_provider or "tradovate").strip().lower() != "crosstrade":
        return None, {}
    try:
        if leg_id:
            fills = await client.get_fills_for_order(int(leg_id))
        else:
            exit_time = order_row.exit_time or datetime.datetime.utcnow()
            lookback = datetime.timedelta(seconds=_CLOSE_CONFIRM_ATTEMPTS * _CLOSE_CONFIRM_INTERVAL_SEC + 5)
            from_ts = (exit_time - lookback).replace(tzinfo=datetime.timezone.utc).isoformat()
            to_ts = (exit_time + datetime.timedelta(seconds=5)).replace(tzinfo=datetime.timezone.utc).isoformat()
            fills = await client.get_fills_history(from_ts, to_ts, is_demo=bool(account.tradovate_is_demo))
            exclude = {str(x) for x in (order_row.entry_exchange_order_id, order_row.sl_exchange_order_id,
                                         order_row.tp_exchange_order_id) if x}
            exit_action = "sell" if order_row.direction == "LONG" else "buy"

            def _keep(f):
                oid = _field(f, ("orderId", "order_id"))
                if oid is not None and str(oid) in exclude:
                    return False
                act = str(_field(f, ("action", "side")) or "").lower()
                if act and act != exit_action:
                    return False
                inst = str(_field(f, ("instrument", "symbol", "contract")) or "")
                if inst and order_row.symbol not in inst:
                    return False
                return True
            fills = [f for f in (fills or []) if isinstance(f, dict) and _keep(f)]
    except Exception as e:
        return None, {"fill_lookup_error": str(e)}
    price, qty = _fill_price_qty(fills)
    if price is None:
        return None, {}
    if int(round(qty)) != int(order_row.contracts or 0):
        return None, {"fill_qty_mismatch": qty}
    return price, {}


async def _last_5m_close(order_row: CMEOrder) -> Optional[float]:
    try:
        import cme_market
        bars = await cme_market.fetch_confirmed_5m(order_row.symbol, order_row.entry_fill_time or datetime.datetime.utcnow())
        return float(bars[-1]["close"]) if bars else None
    except Exception:
        return None


async def _resolve_exit_price(client, account: ExecutorAccount, order_row: CMEOrder,
                              leg_id: Optional[str] = None, leg_price: Optional[float] = None):
    """Lane H C2.5 hierarchy, first available wins, never invented:
    BROKER_FILL -> APPROX_BRACKET_ORDER_PRICE (exchange leg exits) ->
    APPROX_LAST_5M_CLOSE (commanded closes) -> UNKNOWN (NULL price/R/$)."""
    price, detail = await _broker_exit_fill(client, account, order_row, leg_id)
    if price is not None:
        return price, "BROKER_FILL", detail
    if leg_price is not None:
        return float(leg_price), "APPROX_BRACKET_ORDER_PRICE", detail
    last = await _last_5m_close(order_row)
    if last is not None:
        return last, "APPROX_LAST_5M_CLOSE", detail
    return None, "UNKNOWN", detail


def _apply_exit_price(order_row: CMEOrder, price: Optional[float], source: str) -> None:
    order_row.exit_price = price
    order_row.exit_price_source = source
    if price is None or order_row.entry_fill_price is None:
        order_row.realized_pnl_r = None
        order_row.dollar_pnl = None
        return
    sgn = 1 if order_row.direction == "LONG" else -1
    move = price - order_row.entry_fill_price
    order_row.realized_pnl_r = sgn * move / order_row.box_at_entry if order_row.box_at_entry else None
    order_row.dollar_pnl = sgn * move * (order_row.contracts or 0) * (order_row.point_multiplier or 0.0)


async def _book_exit(db: Session, account: ExecutorAccount, order_row: CMEOrder, client, exit_reason: str,
                     leg_id: Optional[str] = None, leg_price: Optional[float] = None,
                     extra: Optional[Dict[str, Any]] = None) -> None:
    from_state = order_row.management_state
    order_row.exit_reason = exit_reason
    order_row.exit_time = datetime.datetime.utcnow()
    order_row.closed_at = order_row.exit_time
    price, source, pdetail = await _resolve_exit_price(client, account, order_row, leg_id=leg_id, leg_price=leg_price)
    _apply_exit_price(order_row, price, source)
    order_row.management_state = f"CLOSED_{exit_reason}"
    detail = {"exit_price_source": source, "approximated": source != "BROKER_FILL"}
    detail.update(pdetail)
    detail.update(extra or {})
    _log_transition(db, order_row, from_state, order_row.management_state, price=order_row.exit_price,
                    realized_pnl_r=order_row.realized_pnl_r, detail=detail)
    _audit(db, "POSITION_CLOSED", f"CME order {order_row.id} closed: {exit_reason} at {order_row.exit_price} ({source})", account.id, order_row)
    _notify_account(db, account, "build_cme_exit_email", order_row)
    await _fetch_cme_exit_fee(client, account, order_row)


async def _leg_status(client, leg_id: Optional[str]) -> Optional[str]:
    if not leg_id:
        return None
    try:
        detail = await client.get_order_detail(int(leg_id))
    except Exception:
        return None
    return _order_status_of(detail) if isinstance(detail, dict) else None


async def reconcile_open_position(db: Session, account: ExecutorAccount, order_row: CMEOrder,
                                  positions: Optional[List[Dict[str, Any]]] = None, client=None) -> bool:
    """Lane H C2.3: before acting on an open row, look at the broker. Returns
    True when this tick must send NO order (an exchange-side exit was booked,
    or the account reads flat with no confirmed bracket fill); False when the
    position is still open or the position query failed (normal management
    continues, and market_close re-checks before sending anything)."""
    client = client or _client_for(account)
    if positions is None:
        try:
            positions = await client.get_positions()
        except Exception:
            return False
    if any(_position_matches(p, order_row.symbol) for p in positions):
        return False

    sl_status = await _leg_status(client, order_row.sl_exchange_order_id)
    tp_status = await _leg_status(client, order_row.tp_exchange_order_id)
    if sl_status == "filled":
        reason = "BE_STOP" if order_row.be_amended else "STOP"
        await _book_exit(db, account, order_row, client, reason, leg_id=order_row.sl_exchange_order_id,
                         leg_price=order_row.sl_price_current, extra={"reconciled": True})
        return True
    if tp_status == "filled":
        await _book_exit(db, account, order_row, client, "TARGET", leg_id=order_row.tp_exchange_order_id,
                         leg_price=_plan_target(db, order_row), extra={"reconciled": True})
        return True
    if (order_row.sl_exchange_order_id and order_row.tp_exchange_order_id
            and sl_status in _ORDER_GONE_STATUSES and tp_status in _ORDER_GONE_STATUSES):
        await _book_exit(db, account, order_row, client, "EXTERNAL", extra={"reconciled": True})
        return True

    tag = "recon-flat-unconfirmed"
    if not _flagged_once(db, account, order_row, tag):
        _audit(db, "ERROR", f"[{tag} #{order_row.id}] no matching position but no bracket fill confirmed -- CHECK BROKER "
               f"(stop leg status={sl_status!r}, target leg status={tp_status!r}); no order sent", account.id, order_row)
    return True


async def market_close(db: Session, account: ExecutorAccount, order_row: CMEOrder, exit_reason: str) -> None:
    """Closes the position at market -- used for the 16:00 ET mandatory
    flush, the news window, the software exit replay and emergency_flatten().
    Lane H C2.4: the position is checked FIRST -- a market order into a flat
    account would open a NEW unprotected position on Tradovate-direct. A
    flat account goes to reconcile (exchange-side exit) instead; an
    unreadable account sends nothing. The close call's False return is a
    failure. Lane H C2.5: the exit price comes from the broker fill when
    available, otherwise a labelled approximation, otherwise NULL --
    never the entry price."""
    client = _client_for(account)
    try:
        positions = await client.get_positions()
    except Exception as e:
        _audit(db, "ERROR", f"market close for CME order {order_row.id} ({exit_reason}) NOT sent: position check failed ({e}), retrying next tick",
               account.id, order_row)
        return
    if not any(_position_matches(p, order_row.symbol) for p in positions):
        await reconcile_open_position(db, account, order_row, positions=positions, client=client)
        return

    try:
        sent = await client.emergency_market_close(order_row.symbol, order_row.contracts, order_row.direction)
    except Exception as e:
        _audit(db, "ERROR", f"market close failed for CME order {order_row.id} ({exit_reason}): {e}", account.id, order_row)
        return
    if not sent:
        _audit(db, "ERROR", f"market close for CME order {order_row.id} ({exit_reason}) was rejected by the broker (returned False) -- retrying next tick",
               account.id, order_row)
        return

    still_open = True
    for _ in range(_CLOSE_CONFIRM_ATTEMPTS):
        await asyncio.sleep(_CLOSE_CONFIRM_INTERVAL_SEC)
        try:
            positions = await client.get_positions()
        except Exception:
            continue
        still_open = any(_position_matches(p, order_row.symbol) for p in positions)
        if not still_open:
            break

    if still_open:
        _audit(db, "ERROR", f"market close reported for CME order {order_row.id} ({exit_reason}) but a position still exists -- CHECK TRADOVATE DIRECTLY", account.id, order_row)
        return

    await _book_exit(db, account, order_row, client, exit_reason)


async def upgrade_approx_exit_price(db: Session, account: ExecutorAccount, order_row: CMEOrder) -> bool:
    """Lane H C2.5 late upgrade (called from the 48 h fee sweep): if a broker
    fill price is now available for an APPROX_* exit, replace it, recompute
    R/$ and log the upgrade. CrossTrade only. Returns True if upgraded."""
    if (order_row.exit_price_source or "") not in _EXIT_SOURCES_APPROX:
        return False
    client = _client_for(account)
    leg_id = None
    if order_row.exit_reason in ("STOP", "BE_STOP"):
        leg_id = order_row.sl_exchange_order_id
    elif order_row.exit_reason == "TARGET":
        leg_id = order_row.tp_exchange_order_id
    price, _detail = await _broker_exit_fill(client, account, order_row, leg_id)
    if price is None:
        return False
    old_price, old_source = order_row.exit_price, order_row.exit_price_source
    _apply_exit_price(order_row, price, "BROKER_FILL")
    _log_transition(db, order_row, order_row.management_state, order_row.management_state, price=price,
                    realized_pnl_r=order_row.realized_pnl_r,
                    detail={"exit_price_upgraded_from": old_price, "old_source": old_source, "exit_price_source": "BROKER_FILL"})
    return True


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
        # Lane H C5.3 step 5: the cancel return value used to be ignored, so
        # emergency flatten could report "cancelled" for an order still
        # resting at the broker. Now verified; a failure raises so the
        # caller reports it (flatten collects it into `errors`).
        result = await cancel_resting_entry(db, account, order_row, f"CLOSED_{exit_reason}", f"manual/flatten ({exit_reason})")
        if result == "failed":
            raise RuntimeError(f"cancel of resting entry for order {order_row.id} could not be confirmed -- check the broker")
        if result == "filled":
            # Filled before the cancel landed: it is now an open position,
            # and the caller asked for it gone -- close it.
            await market_close(db, account, order_row, exit_reason=exit_reason)
            return {"action": "closed", "order_id": order_row.id}
        order_row.exit_reason = exit_reason
        return {"action": "cancelled", "order_id": order_row.id}
    elif order_row.management_state in _CLOSEABLE_STATES:
        await market_close(db, account, order_row, exit_reason=exit_reason)
        if order_row.management_state in _CLOSEABLE_STATES:
            # Lane H C2.4: market_close no longer books a close it couldn't
            # confirm (position unreadable, broker rejected, still open, or
            # flat without a confirmed bracket fill). Report it, don't claim it.
            raise RuntimeError(f"close of order {order_row.id} could not be confirmed -- check the broker")
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
