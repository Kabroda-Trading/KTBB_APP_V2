# executor_mechanism_test.py
# ==============================================================================
# EXECUTOR MECHANISM TEST -- Stage 2, 2026-09-05. A manually-triggered,
# REAL-MONEY test of the order-placing/closing chain: place the smallest
# possible real order on Bitunix, confirm it fills, set a bracket TP/SL,
# partially close it, move the stop to breakeven, flash-close the
# remainder. Proves the entire mechanism against a real account with a
# few dollars of exposure on purpose, before any of this is ever wired
# into the real TradePlan-driven pipeline.
#
# NOT a trading decision -- always BTCUSDT/LONG, sized at the exchange's
# own real minimum trade volume. Deliberately isolated from TradePlan/
# ExecutorOrder (see database.py's ExecutorMechanismTest docstring): no
# trade_plan_id, no shared unique constraint, structurally impossible to
# alias with a real trade in any existing dashboard/report.
#
# Every action independently re-checks the gates (is_live_orders_enabled
# + is_account_tradeable + credentials) at its OWN start, not just once
# at the top of the ladder -- a kill switch engaged mid-ladder must
# actually halt the next step. Never auto-retries a failed exchange
# call (double-order/double-close risk) -- see the plan's own risk
# notes on why. Always stores the raw exchange response JSON BEFORE
# attempting to parse a specific field out of it, so a KeyError on an
# unexpected shape still leaves the real evidence on the row for manual
# inspection.
# ==============================================================================

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

import executor_accounts
import executor_bitunix_client
import executor_control
import executor_sizing
from database import ExecutorAccount, ExecutorMechanismTest


class MechanismTestBlocked(Exception):
    """Gate failed -- live orders disabled, kill switch engaged, or no
    credentials set. No exchange call was attempted."""


class MechanismTestInvalidState(Exception):
    """The requested action doesn't match the test row's current status."""


_TEST_SYMBOL = "BTCUSDT"
_TEST_DIRECTION = "LONG"   # human-readable / stored in ExecutorMechanismTest.direction
# 2026-09-05, verified against Andy's real account response (test #4):
# Bitunix's real get_position endpoint returns side: "BUY"/"SELL" for
# an open position, NOT "LONG"/"SHORT" as their own docs claim (docs:
# "side (string): Position direction: LONG or SHORT" -- directly
# contradicted by the real, live response). This is the actual root
# cause of all four live failures: the order-fill confirmation
# (get_order_detail) worked correctly every time, but the SUBSEQUENT
# position-list match against "LONG" never matched anything, since
# that literal string never appears in a real response. Real data
# overrides the docs here, not the other way around.
_TEST_POSITION_SIDE = "BUY"
_FILL_POLL_INTERVAL_SEC = 1.0
_FILL_POLL_MAX_ATTEMPTS = 10
_DEFAULT_TP_SL_PCT = 0.01
_DEFAULT_PARTIAL_CLOSE_PCT = 0.50


async def _require_gates_open(db: Session, account: ExecutorAccount) -> Tuple[str, str]:
    """Returns (api_key, api_secret) or raises MechanismTestBlocked. Both
    the persistent global live-orders flag AND is_account_tradeable()
    (kill switch, global kill switch, active) must independently allow
    this -- same real-money gating a real trade would get."""
    if not executor_control.is_live_orders_enabled(db):
        raise MechanismTestBlocked("live orders are not enabled globally")
    tradeable, reason = executor_accounts.is_account_tradeable(db, account)
    if not tradeable:
        raise MechanismTestBlocked(reason)
    api_key, api_secret = executor_accounts.get_decrypted_credentials(account)
    if not api_key or not api_secret:
        raise MechanismTestBlocked("no credentials set on this account")
    return api_key, api_secret


def _extract_pair(pairs_resp: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    """get_trading_pairs()'s data is a LIST -- find the matching entry
    or raise a clear error, never silently guess. Same non-zero-code
    check as every other response parser in this module -- a real API
    error must never look like "symbol not found.\""""
    if pairs_resp.get("code") not in (0, None):
        raise ValueError(f"get_trading_pairs returned a real API error: code={pairs_resp.get('code')} msg={pairs_resp.get('msg')!r}")
    pairs = pairs_resp.get("data") or []
    for pair in pairs:
        if pair.get("symbol") == symbol:
            return pair
    raise ValueError(f"no trading pair entry found for symbol {symbol!r} in get_trading_pairs response")


def _find_open_long_position(pos_resp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """get_position()'s data is a LIST -- filter for an open LONG BTCUSDT
    position (real wire value side=="BUY", see _TEST_POSITION_SIDE's own
    comment for why -- NOT "LONG", contrary to Bitunix's own docs).
    Returns the single match, None if there isn't one, or raises a clear
    error if there's more than one -- an ambiguous state this module
    refuses to guess through rather than silently picking one.

    2026-09-05 fix: a real Bitunix API-level error (non-zero `code`) was
    being silently treated as "zero positions" because `pos_resp.get(
    "data") or []` can't distinguish `data: null` (a real error response)
    from `data: []` (a genuinely empty, successful one) -- this is now
    checked explicitly and raised, never swallowed."""
    if pos_resp.get("code") not in (0, None):
        raise ValueError(f"get_position returned a real API error: code={pos_resp.get('code')} msg={pos_resp.get('msg')!r}")
    positions: List[Dict[str, Any]] = pos_resp.get("data") or []
    matches = [p for p in positions if p.get("symbol") == _TEST_SYMBOL and p.get("side") == _TEST_POSITION_SIDE]
    if len(matches) > 1:
        raise ValueError(
            f"found {len(matches)} open {_TEST_DIRECTION} {_TEST_SYMBOL} positions -- "
            f"ambiguous, refusing to guess which one belongs to this test")
    return matches[0] if matches else None


def _find_pending_tpsl_for_position(tpsl_resp: Dict[str, Any], position_id: str) -> Optional[Dict[str, Any]]:
    """get_pending_tp_sl_order()'s data is a LIST -- find the entry for
    this position_id, confirming a TP/SL mutation actually registered on
    the exchange rather than trusting the mutation call's own response
    (same "REST response success != operation success" caution Bitunix's
    own docs give). Same non-zero-code error handling as
    _find_open_long_position()."""
    if tpsl_resp.get("code") not in (0, None):
        raise ValueError(f"get_pending_tp_sl_order returned a real API error: code={tpsl_resp.get('code')} msg={tpsl_resp.get('msg')!r}")
    entries = tpsl_resp.get("data") or []
    for e in entries:
        if e.get("positionId") == position_id:
            return e
    return None


async def _poll_order_until_filled(client: "executor_bitunix_client.BitunixClient", order_id: str) -> Dict[str, Any]:
    """Polls get_order_detail(order_id) -- the authoritative, ID-based
    status of THIS specific order -- up to _FILL_POLL_MAX_ATTEMPTS times.
    Returns the LAST raw response regardless of outcome (caller decides
    what to do with `status`). This replaces scanning get_position() for
    a symbol+side match as the fill-confirmation gate: a real incident
    (2026-09-05) showed 3 orders that genuinely filled (confirmed on
    Bitunix's own UI) never matched via that scan in 10 attempts each --
    querying the order's own status by ID is direct and can't suffer
    from whatever the positions list's fields actually look like."""
    last_resp: Dict[str, Any] = {}
    for _ in range(_FILL_POLL_MAX_ATTEMPTS):
        await asyncio.sleep(_FILL_POLL_INTERVAL_SEC)
        last_resp = await client.get_order_detail(order_id=order_id)
        if last_resp.get("code") not in (0, None):
            break  # a real API error -- no point retrying the same bad call 10 times
        data = last_resp.get("data") or {}
        status = data.get("status")
        if status == "FILLED":
            break
        if status == "CANCELED":
            break  # definitive terminal state -- no point polling further
    return last_resp


async def place_confirm_and_set_initial_tpsl(
    db: Session, account: ExecutorAccount, actor: str,
    tp_pct: float = _DEFAULT_TP_SL_PCT, sl_pct: float = _DEFAULT_TP_SL_PCT,
) -> ExecutorMechanismTest:
    api_key, api_secret = await _require_gates_open(db, account)
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)

    # Pre-flight: refuse if an open LONG BTCUSDT position already exists
    # -- could be a real concurrent production trade, or a leftover
    # test position; either way this test cannot safely tell which
    # position is "its own" afterward. No DB row created if blocked here.
    existing = await client.get_position(_TEST_SYMBOL)
    if _find_open_long_position(existing) is not None:
        raise MechanismTestBlocked(
            "an open LONG BTCUSDT position already exists on this account -- "
            "refusing to start a tiny test order (cannot disambiguate positions afterward)")

    pairs_resp = await client.get_trading_pairs(_TEST_SYMBOL)
    pair = _extract_pair(pairs_resp, _TEST_SYMBOL)
    min_qty = float(pair["minTradeVolume"])
    base_precision = int(pair["basePrecision"])
    quote_precision = int(pair.get("quotePrecision", 2))
    # 2x the exchange's real minimum, NOT exactly the minimum -- a 50%
    # partial-close (_DEFAULT_PARTIAL_CLOSE_PCT) of the true minimum
    # step floors to exactly 0 at basePrecision (e.g. 0.0001 BTC * 0.5 =
    # 0.00005, which floors to 0.0000 at 4dp) -- unrepresentable, and
    # would send the exchange a zero-qty order. Still the smallest size
    # that can actually complete the full ladder, still a few dollars
    # of exposure at most.
    open_qty = min_qty * 2
    qty_str = executor_sizing.round_qty_to_precision(open_qty, base_precision)

    test_row = ExecutorMechanismTest(
        account_id=account.id, symbol=_TEST_SYMBOL, direction=_TEST_DIRECTION,
        status="STARTED", min_trade_volume=min_qty, base_precision=base_precision,
        quote_precision=quote_precision, qty=float(qty_str), started_by=actor,
    )
    db.add(test_row)
    db.flush()
    executor_accounts.write_audit(
        db, "TEST_MECHANISM_STARTED", f"tiny mechanism test starting for account {account.id}, qty={qty_str}",
        account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)

    try:
        place_resp = await client.place_order(
            symbol=_TEST_SYMBOL, qty=qty_str, side="BUY", trade_side="OPEN", order_type="MARKET")
        test_row.place_order_response_json = json.dumps(place_resp, default=str)
        test_row.exchange_order_id = place_resp["data"]["orderId"]
        test_row.exchange_client_id = place_resp["data"].get("clientId")
        test_row.status = "ORDER_PLACED"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_ORDER_PLACED", f"tiny order placed, exchange orderId={test_row.exchange_order_id}",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=place_resp)

        # Step 1: confirm the ORDER itself filled, by its own ID -- the
        # authoritative check (see _poll_order_until_filled's docstring
        # for why this replaced a positions-list scan).
        order_detail_resp = await _poll_order_until_filled(client, test_row.exchange_order_id)
        test_row.order_detail_response_json = json.dumps(order_detail_resp, default=str)
        db.flush()
        order_status = (order_detail_resp.get("data") or {}).get("status")

        if order_status != "FILLED":
            test_row.status = "FAILED"
            test_row.error_detail = (
                f"order placed (orderId={test_row.exchange_order_id}) but get_order_detail "
                f"reports status={order_status!r} after {_FILL_POLL_MAX_ATTEMPTS} attempts -- "
                f"CHECK THE EXCHANGE DIRECTLY before taking any further action on this account. "
                f"Raw response saved on this row (order_detail_response_json)."
            )
            db.flush()
            executor_accounts.write_audit(
                db, "TEST_MECHANISM_FAILED", test_row.error_detail,
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=order_detail_resp)
            return test_row  # does NOT raise -- "go look at the exchange," not a code bug

        # Step 2: the order is confirmed FILLED -- now find the resulting
        # position (for positionId/avgOpenPrice, needed by every later
        # step). Always save the raw response, filled-match or not.
        pos_resp = await client.get_position(_TEST_SYMBOL)
        test_row.position_check_response_json = json.dumps(pos_resp, default=str)
        db.flush()
        position = _find_open_long_position(pos_resp)

        if position is None:
            test_row.status = "FAILED"
            test_row.error_detail = (
                f"order confirmed FILLED (orderId={test_row.exchange_order_id}) but no matching "
                f"open position found on the very next get_position call -- CHECK THE EXCHANGE "
                f"DIRECTLY. Raw response saved on this row (position_check_response_json)."
            )
            db.flush()
            executor_accounts.write_audit(
                db, "TEST_MECHANISM_FAILED", test_row.error_detail,
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=pos_resp)
            return test_row

        test_row.position_id = position["positionId"]
        test_row.fill_price = float(position["avgOpenPrice"])
        test_row.status = "FILL_CONFIRMED"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_ORDER_FILL_CONFIRMED",
            f"fill confirmed at {test_row.fill_price}, positionId={test_row.position_id}",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=position)

        tp_price = test_row.fill_price * (1 + tp_pct)
        sl_price = test_row.fill_price * (1 - sl_pct)
        tp_str = executor_sizing.round_price_to_precision(tp_price, quote_precision)
        sl_str = executor_sizing.round_price_to_precision(sl_price, quote_precision)

        tpsl_resp = await client.set_position_tpsl(
            symbol=_TEST_SYMBOL, position_id=test_row.position_id,
            tp_price=tp_str, tp_stop_type="LAST_PRICE", sl_price=sl_str, sl_stop_type="LAST_PRICE")
        test_row.tpsl_response_json = json.dumps(tpsl_resp, default=str)
        test_row.initial_tp_price = float(tp_str)
        test_row.initial_sl_price = float(sl_str)
        test_row.tpsl_exchange_order_id = tpsl_resp["data"]["orderId"]
        db.flush()

        # Independent confirmation: verify the TP/SL is ACTUALLY
        # registered on the exchange, not just that the mutation call
        # returned success -- same discipline as the order-fill check
        # above, and for the same reason (a real incident already proved
        # a successful-looking response isn't proof of anything here).
        check_resp = await client.get_pending_tp_sl_order(symbol=_TEST_SYMBOL, position_id=test_row.position_id)
        test_row.tpsl_check_response_json = json.dumps(check_resp, default=str)
        db.flush()
        registered = _find_pending_tpsl_for_position(check_resp, test_row.position_id)

        if registered is None:
            test_row.status = "FAILED"
            test_row.error_detail = (
                f"set_position_tpsl reported success (orderId={test_row.tpsl_exchange_order_id}) but no "
                f"pending TP/SL found for positionId={test_row.position_id} on the very next check -- "
                f"CHECK THE EXCHANGE DIRECTLY. Raw response saved (tpsl_check_response_json)."
            )
            db.flush()
            executor_accounts.write_audit(
                db, "TEST_MECHANISM_FAILED", test_row.error_detail,
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=check_resp)
            return test_row

        test_row.status = "TPSL_SET"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_INITIAL_TPSL_SET", f"initial TP={tp_str} SL={sl_str} set and confirmed registered on positionId={test_row.position_id}",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=tpsl_resp)
        return test_row

    except Exception as e:
        test_row.status = "FAILED"
        test_row.error_detail = str(e)
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_MECHANISM_FAILED", f"mechanism test failed: {e}",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
        raise


async def partial_close(
    db: Session, account: ExecutorAccount, test_row: ExecutorMechanismTest, actor: str,
    pct: float = _DEFAULT_PARTIAL_CLOSE_PCT,
) -> ExecutorMechanismTest:
    if test_row.status != "TPSL_SET":
        raise MechanismTestInvalidState(f"cannot partial-close from status {test_row.status!r} -- expected TPSL_SET")
    api_key, api_secret = await _require_gates_open(db, account)
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)
    qty_str = executor_sizing.round_qty_to_precision(test_row.qty * pct, test_row.base_precision)
    try:
        if float(qty_str) <= 0:
            raise ValueError(
                f"partial close of {pct:.0%} of qty={test_row.qty} floors to {qty_str} at "
                f"{test_row.base_precision} decimals -- unrepresentable, refusing to send a zero-qty order")
        resp = await client.place_order(
            symbol=_TEST_SYMBOL, qty=qty_str, side="SELL", trade_side="CLOSE", order_type="MARKET",
            position_id=test_row.position_id, reduce_only=True)
        test_row.partial_close_response_json = json.dumps(resp, default=str)
        test_row.partial_close_pct = pct
        test_row.partial_close_qty = float(qty_str)
        test_row.partial_close_exchange_order_id = resp["data"]["orderId"]
        db.flush()

        # Independent confirmation: this is a real order too, same as
        # the opening one -- confirm it actually filled by its own ID
        # rather than trusting place_order's response alone.
        order_detail_resp = await _poll_order_until_filled(client, test_row.partial_close_exchange_order_id)
        test_row.order_detail_response_json = json.dumps(order_detail_resp, default=str)
        db.flush()
        order_status = (order_detail_resp.get("data") or {}).get("status")

        if order_status != "FILLED":
            test_row.status = "FAILED"
            test_row.error_detail = (
                f"partial-close order placed (orderId={test_row.partial_close_exchange_order_id}) but "
                f"get_order_detail reports status={order_status!r} after {_FILL_POLL_MAX_ATTEMPTS} "
                f"attempts -- CHECK THE EXCHANGE DIRECTLY. Raw response saved (order_detail_response_json)."
            )
            db.flush()
            executor_accounts.write_audit(
                db, "TEST_MECHANISM_FAILED", test_row.error_detail,
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=order_detail_resp)
            return test_row

        test_row.status = "PARTIAL_CLOSED"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_PARTIAL_CLOSED", f"partial close of {qty_str} executed and confirmed filled",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=resp)
        return test_row
    except Exception as e:
        test_row.status = "FAILED"
        test_row.error_detail = str(e)
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_MECHANISM_FAILED", f"partial close failed: {e}",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
        raise


async def move_sl_to_breakeven(
    db: Session, account: ExecutorAccount, test_row: ExecutorMechanismTest, actor: str,
) -> ExecutorMechanismTest:
    if test_row.status != "PARTIAL_CLOSED":
        raise MechanismTestInvalidState(f"cannot move SL from status {test_row.status!r} -- expected PARTIAL_CLOSED")
    api_key, api_secret = await _require_gates_open(db, account)
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)
    # Deliberately the EXACT fill price, fee-naive -- correct for
    # proving the mechanism, not true PnL-neutral breakeven. Do not
    # carry this simplification into a real future feature without a
    # deliberate decision then.
    sl_str = executor_sizing.round_price_to_precision(test_row.fill_price, test_row.quote_precision)
    # 2026-09-05, found live, real money, on Andy's own account: Bitunix's
    # modify_position_tp_sl_order does NOT behave like a partial update --
    # an omitted field is CLEARED, not left alone. Sending only sl_price
    # silently wiped the existing take-profit entirely (confirmed via the
    # tpsl_check_response_json this same hardening pass added -- without
    # that check this would have been invisible). The existing TP must be
    # re-sent alongside the new SL on every modify call.
    tp_str = executor_sizing.round_price_to_precision(test_row.initial_tp_price, test_row.quote_precision)
    try:
        resp = await client.modify_position_tp_sl_order(
            symbol=_TEST_SYMBOL, position_id=test_row.position_id,
            tp_price=tp_str, tp_stop_type="LAST_PRICE", sl_price=sl_str, sl_stop_type="LAST_PRICE")
        test_row.sl_breakeven_response_json = json.dumps(resp, default=str)
        test_row.breakeven_sl_price = float(sl_str)
        test_row.sl_breakeven_exchange_order_id = resp["data"]["orderId"]
        db.flush()

        # Independent confirmation: verify BOTH the new SL is registered
        # AND the TP is still present at its original price -- not just
        # that modify returned success, and not just that "some" pending
        # TP/SL entry exists (that alone would NOT have caught the real
        # TP-wipe bug this comment is describing).
        check_resp = await client.get_pending_tp_sl_order(symbol=_TEST_SYMBOL, position_id=test_row.position_id)
        test_row.tpsl_check_response_json = json.dumps(check_resp, default=str)
        db.flush()
        registered = _find_pending_tpsl_for_position(check_resp, test_row.position_id)

        def _prices_match(actual: Optional[str], expected: str) -> bool:
            try:
                return actual is not None and abs(float(actual) - float(expected)) < 1e-9
            except (TypeError, ValueError):
                return False

        problem = None
        if registered is None:
            problem = (
                f"modify_position_tp_sl_order reported success (orderId={test_row.sl_breakeven_exchange_order_id}) "
                f"but no pending TP/SL found for positionId={test_row.position_id} on the very next check"
            )
        elif not _prices_match(registered.get("slPrice"), sl_str):
            problem = f"SL registered as {registered.get('slPrice')!r}, expected {sl_str!r}"
        elif not _prices_match(registered.get("tpPrice"), tp_str):
            problem = f"TP registered as {registered.get('tpPrice')!r}, expected {tp_str!r} -- it may have been cleared by the modify call"

        if problem is not None:
            test_row.status = "FAILED"
            test_row.error_detail = f"{problem} -- CHECK THE EXCHANGE DIRECTLY. Raw response saved (tpsl_check_response_json)."
            db.flush()
            executor_accounts.write_audit(
                db, "TEST_MECHANISM_FAILED", test_row.error_detail,
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=check_resp)
            return test_row

        test_row.status = "SL_MOVED_BREAKEVEN"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_SL_MOVED_TO_BREAKEVEN", f"SL moved to breakeven ({sl_str}) and confirmed registered",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=resp)
        return test_row
    except Exception as e:
        test_row.status = "FAILED"
        test_row.error_detail = str(e)
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_MECHANISM_FAILED", f"move-SL-to-breakeven failed: {e}",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
        raise


async def flash_close_remainder(
    db: Session, account: ExecutorAccount, test_row: ExecutorMechanismTest, actor: str,
) -> ExecutorMechanismTest:
    if test_row.status not in ("PARTIAL_CLOSED", "SL_MOVED_BREAKEVEN"):
        raise MechanismTestInvalidState(
            f"cannot flash-close from status {test_row.status!r} -- expected PARTIAL_CLOSED or SL_MOVED_BREAKEVEN")
    api_key, api_secret = await _require_gates_open(db, account)
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)
    try:
        resp = await client.close_position(test_row.position_id)
        test_row.flash_close_response_json = json.dumps(resp, default=str)
        db.flush()

        # Independent confirmation: verify the position is ACTUALLY gone,
        # not just that flash_close_position returned success. Re-checks
        # a few times in case the exchange takes a moment to reflect the
        # close, same pattern as the fill-confirmation polls above.
        pos_resp: Dict[str, Any] = {}
        still_open: Optional[Dict[str, Any]] = None
        for _ in range(_FILL_POLL_MAX_ATTEMPTS):
            await asyncio.sleep(_FILL_POLL_INTERVAL_SEC)
            pos_resp = await client.get_position(_TEST_SYMBOL)
            still_open = _find_open_long_position(pos_resp)
            if still_open is None:
                break
        test_row.position_check_response_json = json.dumps(pos_resp, default=str)
        db.flush()

        if still_open is not None:
            test_row.status = "FAILED"
            test_row.error_detail = (
                f"close_position reported success but a matching open LONG {_TEST_SYMBOL} position "
                f"still exists after {_FILL_POLL_MAX_ATTEMPTS} checks -- CHECK THE EXCHANGE DIRECTLY. "
                f"Raw response saved (position_check_response_json)."
            )
            db.flush()
            executor_accounts.write_audit(
                db, "TEST_MECHANISM_FAILED", test_row.error_detail,
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=pos_resp)
            return test_row

        test_row.status = "FULLY_CLOSED"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_POSITION_FLASH_CLOSED", "remainder flash-closed and confirmed no longer open",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=resp)
        return test_row
    except Exception as e:
        test_row.status = "FAILED"
        test_row.error_detail = str(e)
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_MECHANISM_FAILED", f"flash close failed: {e}",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
        raise
