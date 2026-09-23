# executor_live_e1_engine.py
# ==============================================================================
# EXECUTOR LIVE E1 ENGINE -- P3 (CC_WORK_ORDER_LIVE_DAY_2026-09-19.md ->
# CC_INTERFACE.md's HARD PRE-LIVE BLOCKER section, 2026-09-20). Places and
# manages a REAL Bitunix order for the GATE_TRAVELER/MGMT_E1_STACK lineage.
#
# THIS FILE IS WHY executor_engine.py::_process_traveler_account() can ever
# lift its own real-order-placement refusal. Before this file existed, that
# refusal was the ONLY thing preventing a real fill from reaching executor_
# live_engine.py's own check_entry_fill_and_place_exits() -- which is
# hard-coded to MGMT_SPLIT's shape (half-qty T1, a required t3_price) and
# would place a wrong-sized T1 then throw placing T3 (order_row.t3_price is
# always None for a traveler order). Traced and confirmed 2026-09-20, see
# the Kabroda AI Brain repo's CC_INTERFACE.md for the full incident record.
#
# Same "one dedicated engine per management style" convention this codebase
# already uses for the DRY_RUN split (dry_run_split_engine.py for MGMT_SPLIT,
# traveler_plan_engine.py's own E1 walk for MGMT_E1_STACK) -- kept as its own
# file, own loop, rather than a branch inside executor_live_engine.py, so
# that proven v2 module's own real-exchange-call assumptions and MGMT_SPLIT-
# specific shape stay completely untouched.
#
# THE MANAGEMENT RULE, per CC_INTERFACE.md's BRAIN SPEC section (Andy-
# confirmed 2026-09-20 14:40 CT, both items):
#   Entry: resting POST_ONLY LIMIT at the trigger (unchanged mechanism).
#   On fill: place ONLY the exchange-side stop (TP/SL trigger, full qty) +
#     a resting reduce-only LIMIT at T1 for the FULL qty (maker) -- NO T3,
#     no half-qty split. "T1 = resting full-qty limit, 100% off -- that is
#     the traveler rule set" (Andy).
#   All other exits are DYNAMIC, driven by this file's own poll loop:
#     priority STOP (exchange-side) -> C5-or-BBWP (market close) -> T1
#     (resting, passive) -> TIME (journey_cap_at, market close). A C5/BBWP/
#     TIME fire calls close_position() (flash-close, market -- the SAME
#     call already live-proven in executor_mechanism_test.py's own ladder
#     test) then immediately cancels the now-orphaned T1 limit. A stop fire
#     is exchange-side and also orphan-cancels the T1 limit once observed.
#   Exit price for a market close is APPROXIMATED at the last known live
#     price (close_position()'s own response carries no reliable fill
#     price -- confirmed against executor_mechanism_test.py::
#     flash_close_remainder(), the only other proven caller of this
#     endpoint, which does not try to extract one either). "It does not
#     matter about the slippage nuances... we are managing the trade"
#     (Andy, accepting this for contingency exits specifically).
#
# CANCEL-ON-EXPIRY FOR THE ENTRY (added here, not explicitly itemized in the
# Brain's own a-j test list, but already a standing binding requirement --
# CC_INTERFACE.md audit item 3: "any new order type added later must inherit
# this guarantee"): the resting entry limit gets the SAME P0-1 protection
# v2's own entry got, using TravelerPlan.status in (DONE, TERCILE_SKIPPED)
# or journey_cap_at as the equivalent expiry signal (TravelerPlan has no
# session_expires_at concept). Flagged explicitly in the AGENT_LOG report,
# not silently folded in.
#
# Race safety throughout matches P0-1's own standard: a real fill or a real
# closure discovered mid-action is never discarded or double-processed --
# every cancel is successList-verified, every market-close is confirmed via
# a re-poll before this module commits to a terminal state, and a failure
# anywhere just retries next tick rather than assuming success.
# ==============================================================================

from __future__ import annotations

import asyncio
import datetime
import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

import executor_accounts
import executor_bitunix_client
import executor_sizing
import market_data
import mgmt_e1_stack
from database import ExecutorAccount, ExecutorOrder, TravelerPlan

_LONG, _SHORT = "LONG", "SHORT"
# Same doc-vs-reality correction executor_live_engine.py's own header notes
# (Bitunix's real get_position side field is "BUY"/"SELL", not "LONG"/"SHORT").
_POSITION_SIDE_FOR_DIRECTION = {_LONG: "BUY", _SHORT: "SELL"}
_ENTRY_SIDE_FOR_DIRECTION = {_LONG: "BUY", _SHORT: "SELL"}
_EXIT_SIDE_FOR_DIRECTION = {_LONG: "SELL", _SHORT: "BUY"}   # closing a LONG sells, closing a SHORT buys

# This lineage's own terminal vocabulary (mgmt_e1_stack.MGMT_E1_TERMINAL_STATES,
# the ONE shared source with the DRY_RUN walk -- CC_INTERFACE.md audit item 6)
# plus the two extra states this LIVE engine can also reach, mirroring
# executor_live_engine.py's own _TERMINAL_STATES shape exactly.
_E1_LIVE_TERMINAL_STATES = mgmt_e1_stack.MGMT_E1_TERMINAL_STATES + ("ENTRY_FILLED_UNPROTECTED", "CLOSED_EXPIRED")

_CLOSE_CONFIRM_INTERVAL_SEC = 1.0
_CLOSE_CONFIRM_ATTEMPTS = 10


def _client_for(account: ExecutorAccount) -> "executor_bitunix_client.BitunixClient":
    api_key, api_secret = executor_accounts.get_decrypted_credentials(account)
    if not api_key or not api_secret:
        raise ValueError(f"account {account.id} has no credentials set -- cannot place real orders")
    return executor_bitunix_client.BitunixClient(api_key, api_secret)


async def _get_pair_precision(client: "executor_bitunix_client.BitunixClient", symbol: str) -> Dict[str, Any]:
    """Duplicated from executor_live_engine.py on purpose -- same tiny,
    stable-lookup convention that module's own docstring on this exact
    function already states ("duplicated rather than imported... not
    worth coupling the two for")."""
    resp = await client.get_trading_pairs(symbol)
    if resp.get("code") not in (0, None):
        raise ValueError(f"get_trading_pairs returned a real API error: code={resp.get('code')} msg={resp.get('msg')!r}")
    for pair in resp.get("data") or []:
        if pair.get("symbol") == symbol:
            return {
                "base_precision": int(pair["basePrecision"]),
                "quote_precision": int(pair.get("quotePrecision", 2)),
                "min_trade_volume": float(pair["minTradeVolume"]),
            }
    raise ValueError(f"no trading pair entry found for symbol {symbol!r} in get_trading_pairs response")


def _find_open_position(pos_resp: Dict[str, Any], symbol: str, direction: str) -> Optional[Dict[str, Any]]:
    if pos_resp.get("code") not in (0, None):
        raise ValueError(f"get_position returned a real API error: code={pos_resp.get('code')} msg={pos_resp.get('msg')!r}")
    side = _POSITION_SIDE_FOR_DIRECTION[direction]
    positions: List[Dict[str, Any]] = pos_resp.get("data") or []
    matches = [p for p in positions if p.get("symbol") == symbol and p.get("side") == side]
    if len(matches) > 1:
        raise ValueError(f"found {len(matches)} open {direction} {symbol} positions for order {matches} -- ambiguous, refusing to guess")
    return matches[0] if matches else None


def _r_multiple(price: float, entry: float, stop: float) -> float:
    """Signed R-multiple of `price` relative to entry -- identical formula
    to executor_live_engine.py's own _r_multiple(); duplicated per this
    codebase's own established cross-module convention (mgmt_split_dry_run.py
    duplicates this exact same formula for the same isolation reason)."""
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    return (price - entry) / risk if entry >= stop else (entry - price) / risk


async def _current_live_price(symbol: str) -> Optional[float]:
    """Same live-price source as executor_live_engine.py's own identical
    helper -- market_data's own 5m candle feed, not Bitunix tick data."""
    candles = await market_data.fetch_live_5m(symbol, limit=2)
    if not candles:
        return None
    return float(candles[-1]["close"])


def _plan_has_expired(traveler_plan_row: TravelerPlan, now_utc: datetime.datetime) -> bool:
    """P3 addition (not itemized in the Brain's a-j scenario list, but
    already required by CC_INTERFACE.md audit item 3: "any new order type
    added later must inherit this guarantee"). TravelerPlan has no
    session_expires_at concept; journey_cap_at (cross + 7 days) is its real
    analog. Deliberately does NOT treat status=="FILLED" as expired -- FILLED
    is the very transition that caused this order to be created in the
    first place."""
    if traveler_plan_row.status in ("DONE", "TERCILE_SKIPPED"):
        return True
    cap = traveler_plan_row.journey_cap_at
    if cap is not None:
        cap = cap if cap.tzinfo is not None else cap.replace(tzinfo=datetime.timezone.utc)
        return now_utc >= cap
    return False


async def place_traveler_entry_order(db: Session, account: ExecutorAccount, traveler_plan_row: TravelerPlan, order_row: ExecutorOrder) -> None:
    """Places the real resting POST_ONLY LIMIT entry order -- same mechanism
    as executor_live_engine.place_entry_order() (unchanged per the P3
    spec's own item 1), duplicated here per this codebase's established
    "small stable helpers get duplicated, not cross-imported" convention.
    Called once, right after build_hypothetical_traveler_order() has
    already computed and validated qty/leverage/liquidation safety."""
    client = _client_for(account)
    symbol = order_row.symbol.replace("/", "")
    pair = await _get_pair_precision(client, symbol)
    price_str = executor_sizing.round_price_to_precision(order_row.entry_price, pair["quote_precision"])
    qty_str = executor_sizing.round_qty_to_precision(order_row.qty, pair["base_precision"])

    if float(qty_str) <= 0:
        order_row.management_state = "CLOSED_ERROR"
        executor_accounts.write_audit(
            db, "ERROR", f"entry qty {order_row.qty} floors to {qty_str} at {pair['base_precision']} decimals -- refusing to place a zero-qty order",
            account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system")
        return

    resp = await client.place_order(
        symbol=symbol, qty=qty_str, price=price_str,
        side=_ENTRY_SIDE_FOR_DIRECTION[order_row.direction], trade_side="OPEN",
        order_type="LIMIT", effect="POST_ONLY",
    )
    order_row.exchange_response_json = json.dumps(resp, default=str)
    order_id = (resp.get("data") or {}).get("orderId")
    if resp.get("code") not in (0, None) or not order_id:
        order_row.management_state = "CLOSED_ERROR"
        executor_accounts.write_audit(
            db, "ERROR",
            f"entry order placement FAILED (code={resp.get('code')} msg={resp.get('msg')!r}) -- "
            f"no real order exists on the exchange for this traveler_plan",
            account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system", detail=resp)
        import notify
        notify.send_admin_email(
            f"KABRODA EXECUTOR ERROR -- traveler entry placement failed ({order_row.symbol})",
            f"Real entry order placement failed for traveler_plan_id={traveler_plan_row.id}, account={account.id}.\n"
            f"Exchange response: code={resp.get('code')} msg={resp.get('msg')!r}\n\n"
            f"No real order exists for this trade -- likely a POST_ONLY rejection (price moved past "
            f"the entry level before the order reached the exchange). No automatic retry.",
        )
        return
    order_row.entry_exchange_order_id = order_id
    order_row.entry_status = "NEW"
    order_row.management_state = "PENDING_ENTRY"
    executor_accounts.write_audit(
        db, "ORDER_PLACED", f"real traveler entry LIMIT placed at {price_str} qty={qty_str} (orderId={order_row.entry_exchange_order_id})",
        account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system", detail=resp)


async def _cancel_expired_traveler_entry_order(
    db: Session, account: ExecutorAccount, client: "executor_bitunix_client.BitunixClient",
    symbol: str, traveler_plan_row: TravelerPlan, order_row: ExecutorOrder,
) -> None:
    """P3/P0-1 parity: cancels a real resting entry LIMIT whose parent
    journey has already expired (see _plan_has_expired()). Race-safe,
    identical discipline to executor_live_engine.py's own
    _cancel_expired_entry_order(): a fresh get_order_detail() after the
    cancel confirms the real final state before committing to
    CLOSED_EXPIRED; a real fill found there is handed back to the normal
    fill path untouched; a cancel-call failure or an unconfirmed
    successList never assumes success, both just retry next tick."""
    try:
        cancel_resp = await client.cancel_orders(symbol, [order_row.entry_exchange_order_id])
    except Exception as e:
        executor_accounts.write_audit(
            db, "ERROR", f"cancel_orders call failed for expired traveler entry order {order_row.id}: {e}",
            account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system")
        return

    detail_resp = await client.get_order_detail(order_id=order_row.entry_exchange_order_id)
    if detail_resp.get("code") not in (0, None):
        executor_accounts.write_audit(
            db, "ERROR",
            f"get_order_detail returned a real API error confirming the cancel for order {order_row.id}: "
            f"code={detail_resp.get('code')} msg={detail_resp.get('msg')!r} -- CHECK THE EXCHANGE DIRECTLY",
            account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system", detail=detail_resp)
        return
    real_status = (detail_resp.get("data") or {}).get("status")

    if real_status == "FILLED":
        executor_accounts.write_audit(
            db, "ERROR",
            f"cancel-on-expiry raced with a real fill on order {order_row.id} -- cancel not applied, "
            f"handing off to the normal fill-confirmation path instead",
            account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system", detail=detail_resp)
        return

    success_ids = {e.get("orderId") for e in (cancel_resp.get("data") or {}).get("successList") or []}
    if order_row.entry_exchange_order_id not in success_ids and real_status != "CANCELED":
        executor_accounts.write_audit(
            db, "ERROR",
            f"cancel_orders did not report orderId={order_row.entry_exchange_order_id} in successList for "
            f"expired traveler order {order_row.id}, and get_order_detail shows status={real_status!r} -- "
            f"CHECK THE EXCHANGE DIRECTLY, will retry next tick",
            account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system", detail=cancel_resp)
        return

    order_row.management_state = "CLOSED_EXPIRED"
    order_row.entry_status = real_status
    order_row.close_reason = "EXPIRED"
    order_row.closed_at = datetime.datetime.utcnow()
    executor_accounts.write_audit(
        db, "ORDER_CANCELLED_ON_EXPIRY",
        f"real traveler resting entry order {order_row.entry_exchange_order_id} cancelled -- the parent "
        f"journey (traveler_plan_id={traveler_plan_row.id}) expired before the entry ever filled",
        account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system",
        detail={"cancel_response": cancel_resp, "order_detail": detail_resp})


async def check_traveler_entry_fill_and_protect(db: Session, account: ExecutorAccount, traveler_plan_row: TravelerPlan, order_row: ExecutorOrder) -> None:
    """ONE on-demand check per tick, mirrors executor_live_engine.py's own
    check_entry_fill_and_place_exits() -- but places ONLY the exchange stop
    + a FULL-qty resting reduce-only T1 LIMIT (P3 spec item 2), never the
    half-qty T1 + T3 split MGMT_SPLIT uses. Both legs are attempted
    independently (one failing must never block the other), same loud
    "no AI improvisation" alert-and-halt discipline as that function."""
    client = _client_for(account)
    symbol = order_row.symbol.replace("/", "")
    resp = await client.get_order_detail(order_id=order_row.entry_exchange_order_id)
    if resp.get("code") not in (0, None):
        raise ValueError(f"get_order_detail returned a real API error: code={resp.get('code')} msg={resp.get('msg')!r}")
    data = resp.get("data") or {}
    status = data.get("status")
    order_row.entry_status = status
    if status != "FILLED":
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        if _plan_has_expired(traveler_plan_row, now_utc):
            await _cancel_expired_traveler_entry_order(db, account, client, symbol, traveler_plan_row, order_row)
        return   # still resting (or just cancelled above) -- re-checked next tick

    pos_resp = await client.get_position(symbol)
    position = _find_open_position(pos_resp, symbol, order_row.direction)
    if position is None:
        order_row.management_state = "CLOSED_ERROR"
        executor_accounts.write_audit(
            db, "ERROR", f"entry order {order_row.entry_exchange_order_id} confirmed FILLED but no matching open position found -- CHECK THE EXCHANGE DIRECTLY",
            account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system", detail=pos_resp)
        return

    order_row.position_id = position["positionId"]
    order_row.entry_fill_price = float(position["avgOpenPrice"])
    order_row.entry_fill_time = datetime.datetime.utcnow()

    pair = await _get_pair_precision(client, symbol)
    qty_str = executor_sizing.round_qty_to_precision(order_row.qty, pair["base_precision"])
    exit_side = _EXIT_SIDE_FOR_DIRECTION[order_row.direction]

    failures: List[str] = []

    sl_str = executor_sizing.round_price_to_precision(order_row.stop_price, pair["quote_precision"])
    try:
        sl_resp = await client.set_position_tpsl(
            symbol=symbol, position_id=order_row.position_id, sl_price=sl_str, sl_stop_type="LAST_PRICE")
        sl_order_id = (sl_resp.get("data") or {}).get("orderId")
        if sl_resp.get("code") not in (0, None) or not sl_order_id:
            failures.append(f"STOP at {sl_str}: FAILED (code={sl_resp.get('code')} msg={sl_resp.get('msg')!r})")
        else:
            order_row.sl_exchange_order_id = sl_order_id
            order_row.sl_price_current = float(sl_str)
            order_row.sl_set_at = datetime.datetime.utcnow()
    except Exception as e:
        failures.append(f"STOP at {sl_str}: FAILED (exception: {e})")

    t1_price_str = executor_sizing.round_price_to_precision(order_row.t1_price, pair["quote_precision"])
    try:
        t1_resp = await client.place_order(
            symbol=symbol, qty=qty_str, price=t1_price_str, side=exit_side, trade_side="CLOSE",
            order_type="LIMIT", position_id=order_row.position_id, reduce_only=True, effect="POST_ONLY",
        )
        t1_order_id = (t1_resp.get("data") or {}).get("orderId")
        if t1_resp.get("code") not in (0, None) or not t1_order_id:
            failures.append(f"T1 at {t1_price_str} qty={qty_str}: FAILED (code={t1_resp.get('code')} msg={t1_resp.get('msg')!r})")
        else:
            order_row.t1_exchange_order_id = t1_order_id
            order_row.t1_status = "NEW"
    except Exception as e:
        failures.append(f"T1 at {t1_price_str} qty={qty_str}: FAILED (exception: {e})")

    if failures:
        order_row.management_state = "ENTRY_FILLED_UNPROTECTED"
        detail_msg = (
            f"REAL OPEN TRAVELER POSITION (positionId={order_row.position_id}) with INCOMPLETE protection -- "
            f"{'; '.join(failures)}. Manual intervention required, no automatic retry."
        )
        executor_accounts.write_audit(
            db, "ERROR", detail_msg,
            account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system")
        import notify
        notify.send_admin_email(
            f"KABRODA EXECUTOR ALERT -- unprotected open traveler position ({order_row.symbol})",
            f"traveler_plan_id={traveler_plan_row.id}, account={account.id}, positionId={order_row.position_id}\n\n"
            f"{detail_msg}\n\nCHECK THE EXCHANGE DIRECTLY NOW and manually place whatever's missing "
            f"or close the position -- this bot will not act on it further.",
        )
        return

    order_row.management_state = "ENTRY_FILLED_ORDERS_PLACED"
    executor_accounts.write_audit(
        db, "ORDER_PLACED",
        f"traveler entry filled at {order_row.entry_fill_price} (positionId={order_row.position_id}) -- "
        f"stop {sl_str}, T1 {t1_price_str} ({qty_str}, full qty) placed",
        account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system")


async def _cancel_orphaned_t1(db: Session, account: ExecutorAccount, client: "executor_bitunix_client.BitunixClient",
                               symbol: str, traveler_plan_row: TravelerPlan, order_row: ExecutorOrder) -> None:
    """Cancels the now-orphaned T1 resting limit after the position closed
    some other way (a contingency market close, or the exchange-side
    stop) -- the no-floating-orders rule (CC_INTERFACE.md audit item 5)
    applies to exit legs too. A failure here is logged loudly but does not
    block finalizing the trade's own closure -- an orphaned reduce-only
    limit on an already-flat position creates no new exposure, so it is a
    cleanup item, not a safety blocker, but it must never be silently left."""
    if not order_row.t1_exchange_order_id:
        return
    try:
        cancel_resp = await client.cancel_orders(symbol, [order_row.t1_exchange_order_id])
    except Exception as e:
        executor_accounts.write_audit(
            db, "ERROR", f"cancel_orders call failed for orphaned T1 limit on order {order_row.id}: {e}",
            account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system")
        return
    success_ids = {e.get("orderId") for e in (cancel_resp.get("data") or {}).get("successList") or []}
    if order_row.t1_exchange_order_id not in success_ids:
        executor_accounts.write_audit(
            db, "ERROR",
            f"cancel_orders did not report T1 orderId={order_row.t1_exchange_order_id} in successList "
            f"for order {order_row.id} -- likely already filled/cancelled by the exchange when the "
            f"position closed; CHECK THE EXCHANGE if this recurs",
            account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system", detail=cancel_resp)


async def _finalize_traveler_close(
    db: Session, account: ExecutorAccount, traveler_plan_row: TravelerPlan, order_row: ExecutorOrder,
    exit_reason: str, exit_price: float, exit_time: datetime.datetime,
    c5_fired: bool, bbwp_fired: bool, approximated: bool = False,
) -> None:
    """Common closure bookkeeping for every exit path (T1/STOP/C5/BBWP/TIME)
    -- a single full-size exit, no blended-leg math (unlike v2's own 50/50
    split), matching MGMT_E1_STACK's own single-exit design. Feeds the SAME
    ledger-compounding path v2's own poll_open_position() uses
    (record_trade_result(), is_simulation defaults False -- this is real
    money, unlike Ruling D's DRY_RUN-only callers)."""
    order_row.exit_reason = exit_reason
    order_row.exit_price = exit_price
    order_row.exit_time = exit_time
    order_row.c5_fired = c5_fired
    order_row.bbwp_fired = bbwp_fired
    order_row.close_reason = exit_reason
    order_row.management_state = f"CLOSED_{exit_reason}"
    order_row.closed_at = exit_time
    order_row.realized_pnl_r = _r_multiple(exit_price, order_row.entry_fill_price, order_row.stop_price)

    approx_note = " (exit price approximated at last known live price -- close_position() carries no reliable fill price; see this module's own header)" if approximated else ""
    executor_accounts.write_audit(
        db, "POSITION_CLOSED",
        f"traveler trade closed: {exit_reason}, realized {order_row.realized_pnl_r:+.4f}R{approx_note}",
        account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system")

    pnl_usd = order_row.realized_pnl_r * (order_row.risk_dollars_used or 0.0)
    executor_accounts.record_trade_result(db, account, pnl_usd, trade_plan_id=order_row.trade_plan_id, recorded_by="system")

    # 2026-09-23 -- the new management-event email (Andy's own ask: "the
    # same thing on the radar" also communicated by email). Wrapped in its
    # OWN try/except, deliberately NOT this file's own two unguarded
    # `import notify` sites above (those are early-failure paths where a
    # retry is wanted) -- a bug in this new email code must never roll
    # back the real closure bookkeeping (management_state, the audit row
    # above, record_trade_result()) that has already committed for this
    # tick; run_executor_live_e1_loop() commits once per order per tick and
    # rolls back the WHOLE tick on any uncaught exception.
    try:
        import notify
        import traveler_plan_notify

        # Reuses this function's OWN `approximated` parameter directly --
        # an earlier draft recomputed it from exit_reason here, which was
        # redundant (this function already receives the authoritative
        # value from its callers, per-exit-reason, one line up in every
        # real call site) and risked a second source of truth drifting
        # from the first. main.py's own read-side derivation for the
        # radar (`/api/admin/traveler-plan-status`) still computes this
        # the same way from exit_reason, since it has no `order_row` this
        # function's caller already resolved it from -- that one IS the
        # right place for it, this one is not.
        order_dict = {
            "symbol": order_row.symbol, "direction": order_row.direction,
            "exit_reason": exit_reason, "exit_price": exit_price,
            "realized_pnl_r": order_row.realized_pnl_r, "traveler_plan_id": traveler_plan_row.id,
            "approximated": approximated,
        }
        subject, body = traveler_plan_notify.build_traveler_management_event_email(order_dict, is_live=True)
        notify.send_admin_email(subject, body)
    except Exception as e:
        print(f"|| EXECUTOR LIVE E1 || Management-event notification failed for order {order_row.id}: {e}")


async def _market_close_traveler_order(
    db: Session, account: ExecutorAccount, client: "executor_bitunix_client.BitunixClient",
    symbol: str, traveler_plan_row: TravelerPlan, order_row: ExecutorOrder,
    exit_reason: str, c5_fired: bool, bbwp_fired: bool,
) -> None:
    """Market-closes the position for a contingency exit (C5/BBWP fire or
    TIME), per Andy's ruling: contingency exits get out now, timing risk
    dominates fee cost. close_position() (flash_close_position) is the SAME
    call already live-proven in executor_mechanism_test.py's own ladder
    test. Confirms the close via a re-poll on get_position(), mirroring
    that proven function's own confirmation loop.

    Race guard (scenario f): if T1 actually filled in the window between
    poll_traveler_position()'s own "still open" check and this call
    landing, close_position() closes nothing new -- checked via T1's own
    status after the attempt, and the closure is correctly attributed to
    T1, not double-booked as this contingency exit."""
    try:
        await client.close_position(order_row.position_id)
    except Exception as e:
        executor_accounts.write_audit(
            db, "ERROR", f"close_position call failed for order {order_row.id} ({exit_reason}): {e}",
            account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system")
        return   # retry next tick -- never assume closed on a call failure

    still_open = None
    pos_resp: Dict[str, Any] = {}
    for _ in range(_CLOSE_CONFIRM_ATTEMPTS):
        await asyncio.sleep(_CLOSE_CONFIRM_INTERVAL_SEC)
        pos_resp = await client.get_position(symbol)
        still_open = _find_open_position(pos_resp, symbol, order_row.direction)
        if still_open is None:
            break

    if still_open is not None:
        executor_accounts.write_audit(
            db, "ERROR",
            f"close_position reported for order {order_row.id} ({exit_reason}) but a matching open position "
            f"still exists after {_CLOSE_CONFIRM_ATTEMPTS} checks -- CHECK THE EXCHANGE DIRECTLY, will retry next tick",
            account_id=account.id, traveler_plan_id=traveler_plan_row.id, executor_order_id=order_row.id, actor="system", detail=pos_resp)
        return

    t1_resp = await client.get_order_detail(order_id=order_row.t1_exchange_order_id)
    t1_data = t1_resp.get("data") or {}
    if t1_data.get("status") == "FILLED":
        order_row.t1_status = "FILLED"
        order_row.t1_fill_price = order_row.t1_price
        order_row.t1_fill_time = datetime.datetime.utcnow()
        await _finalize_traveler_close(
            db, account, traveler_plan_row, order_row, exit_reason="T1",
            exit_price=order_row.t1_fill_price, exit_time=order_row.t1_fill_time,
            c5_fired=False, bbwp_fired=False,
        )
        return

    await _cancel_orphaned_t1(db, account, client, symbol, traveler_plan_row, order_row)
    exit_price = await _current_live_price(symbol)
    if exit_price is None:
        exit_price = order_row.entry_fill_price   # last resort -- never leave exit_price None
    await _finalize_traveler_close(
        db, account, traveler_plan_row, order_row, exit_reason=exit_reason, exit_price=exit_price,
        exit_time=datetime.datetime.utcnow(), c5_fired=c5_fired, bbwp_fired=bbwp_fired, approximated=True,
    )


async def poll_traveler_position(db: Session, account: ExecutorAccount, traveler_plan_row: TravelerPlan, order_row: ExecutorOrder) -> None:
    """The per-tick watcher for one real MGMT_E1_STACK trade. Dispatches on
    management_state; priority STOP (exchange-side) > C5-or-BBWP (market
    close) > T1 (resting, passive) > TIME, per CC_INTERFACE.md's BRAIN SPEC.
    Closure detection runs FIRST each tick (same convention as executor_
    live_engine.py's own poll_open_position()): if the exchange already
    shows no open position, T1's own status disambiguates whether T1 or the
    exchange-side stop closed it -- this naturally respects "STOP checked
    first" without a separate exchange call, since a fired stop is already
    a fait accompli by the time this poll runs."""
    if order_row.management_state in _E1_LIVE_TERMINAL_STATES:
        return

    if order_row.management_state == "PENDING_ENTRY":
        await check_traveler_entry_fill_and_protect(db, account, traveler_plan_row, order_row)
        return

    client = _client_for(account)
    symbol = order_row.symbol.replace("/", "")

    pos_resp = await client.get_position(symbol)
    still_open = _find_open_position(pos_resp, symbol, order_row.direction)
    if still_open is None:
        t1_resp = await client.get_order_detail(order_id=order_row.t1_exchange_order_id)
        if t1_resp.get("code") not in (0, None):
            raise ValueError(f"get_order_detail(T1) returned a real API error: code={t1_resp.get('code')} msg={t1_resp.get('msg')!r}")
        t1_data = t1_resp.get("data") or {}
        if t1_data.get("status") == "FILLED":
            order_row.t1_status = "FILLED"
            order_row.t1_fill_price = order_row.t1_price
            order_row.t1_fill_time = datetime.datetime.utcnow()
            await _finalize_traveler_close(
                db, account, traveler_plan_row, order_row, exit_reason="T1",
                exit_price=order_row.t1_fill_price, exit_time=order_row.t1_fill_time,
                c5_fired=False, bbwp_fired=False,
            )
        else:
            # T1 never filled but the position is gone -- the exchange-side
            # stop fired. Cancel the now-orphaned T1 limit first (no-
            # floating-orders rule applies to exits too), then close out at
            # the order's own recorded stop price -- same "not independently
            # confirmed against the stop's own fill, approximated" honesty
            # v2's own poll_open_position() RUNNER_STOP branch already uses
            # (a TP/SL trigger order fills AT its own trigger by the
            # exchange's own design, so this is not flagged "approximated"
            # the way a market close is -- same non-flagged convention v2
            # uses for its own STOP_BEFORE_T1 case).
            await _cancel_orphaned_t1(db, account, client, symbol, traveler_plan_row, order_row)
            await _finalize_traveler_close(
                db, account, traveler_plan_row, order_row, exit_reason="STOP",
                exit_price=order_row.stop_price, exit_time=datetime.datetime.utcnow(),
                c5_fired=False, bbwp_fired=False,
            )
        return

    # Still open -- check the dynamic exits, priority C5-or-BBWP then TIME.
    # T1 stays passive; it is caught in the closure branch above the moment
    # it fills, no separate check needed here.
    candles_1h = await market_data.fetch_live_1h(symbol, limit=200)
    candles_4h = await market_data.fetch_live_4h(symbol, limit=200)
    # BBWP's own feed (Bitunix, CC_INTERFACE.md item 3) -- fetched
    # regardless of the Kraken candles above; a bad Bitunix poll must never
    # block the Kraken-fed C5 check (see check_c5_or_bbwp()'s own docstring).
    candles_4h_bbwp = await market_data.fetch_bitunix_4h(symbol)
    if candles_1h and candles_4h:
        c5_hit, bbwp_hit = mgmt_e1_stack.check_c5_or_bbwp(candles_1h, candles_4h, candles_4h_bbwp=candles_4h_bbwp)
        if c5_hit or bbwp_hit:
            await _market_close_traveler_order(
                db, account, client, symbol, traveler_plan_row, order_row,
                exit_reason=("C5_EXIT" if c5_hit else "BBWP_EXIT"), c5_fired=c5_hit, bbwp_fired=bbwp_hit,
            )
            return

    if traveler_plan_row.journey_cap_at is not None:
        cap = traveler_plan_row.journey_cap_at
        cap = cap if cap.tzinfo is not None else cap.replace(tzinfo=datetime.timezone.utc)
        if datetime.datetime.now(datetime.timezone.utc) >= cap:
            await _market_close_traveler_order(
                db, account, client, symbol, traveler_plan_row, order_row,
                exit_reason="TIME", c5_fired=False, bbwp_fired=False,
            )


async def run_executor_live_e1_loop() -> None:
    """Background task (registered in main.py's lifespan()) -- watches every
    real GATE_TRAVELER/MGMT_E1_STACK ExecutorOrder row in a non-terminal
    management_state and calls poll_traveler_position() for each,
    independently try/excepted per row so one account/trade's failure never
    blocks another's. 30s cadence, matching executor_live_engine.py's own
    real-money loop (not traveler_plan_engine.py's 60s DRY_RUN cadence for
    this same lineage) -- real money gets the tighter poll."""
    import traceback
    from database import SessionLocal

    print(">>> EXECUTOR LIVE E1 ENGINE: Initializing traveler position-watch loop...")
    while True:
        db = SessionLocal()
        try:
            open_orders = db.query(ExecutorOrder).filter(
                ExecutorOrder.management_state.isnot(None),
                ~ExecutorOrder.management_state.in_(_E1_LIVE_TERMINAL_STATES),
                ExecutorOrder.entry_exchange_order_id.isnot(None),
                ExecutorOrder.traveler_plan_id.isnot(None),
            ).all()
            for order_row in open_orders:
                try:
                    account = db.query(ExecutorAccount).filter_by(id=order_row.account_id).first()
                    traveler_plan_row = db.query(TravelerPlan).filter_by(id=order_row.traveler_plan_id).first()
                    if account is None or traveler_plan_row is None:
                        continue
                    await poll_traveler_position(db, account, traveler_plan_row, order_row)
                    db.commit()
                except Exception as e:
                    db.rollback()
                    print(f"|| EXECUTOR LIVE E1 || order {order_row.id} (account {order_row.account_id}) poll failed: {e}")
                    traceback.print_exc()
        except Exception as e:
            print(f"|| EXECUTOR LIVE E1 ENGINE ERROR || {e}")
            traceback.print_exc()
        finally:
            db.close()
        await asyncio.sleep(30)
