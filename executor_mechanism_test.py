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


async def _verify_position_after_reduction(
    client: "executor_bitunix_client.BitunixClient",
    test_row: ExecutorMechanismTest,
    expected_remaining_qty: float,
) -> Optional[str]:
    """Re-queries get_position() after ANY reduction of the test
    position (a MARKET partial-close, or a filled/partially-filled
    resting T1 LIMIT) to verify the remaining position's actual
    positionId and qty match expectations -- never assumed. This is
    exactly the open question Andy/DeepSeek's own ladder checklist
    flagged: does Bitunix keep the SAME positionId for the reduced
    remainder, or assign a new one? Whichever the real answer turns out
    to be, test_row.position_id is kept CURRENT here so every later
    ladder step (move_sl_to_breakeven, flash_close_remainder) always
    targets the exchange's real position, never a potentially-stale
    cached ID -- the same "assumption stood in for verification" class
    of gap that caused the real TP-wipe incident.

    Always writes test_row.position_id_after_partial_close/
    qty_after_partial_close/partial_close_position_check_response_json
    -- a DEDICATED response column, not the shared
    position_check_response_json, which gets overwritten again by
    flash_close_remainder()'s own confirmation check and would
    otherwise erase this specific checkpoint's evidence.

    Returns None on success, or a problem-detail string on a real
    functional break (no matching position at all, or the qty doesn't
    match within a half-unit-at-base_precision tolerance) -- the caller
    decides how to fail the test_row from there, same pattern as every
    other check in this module. A CHANGED positionId is NOT by itself a
    failure -- that's the open question this exists to answer, not a
    break -- it's simply recorded and carried forward."""
    pos_resp = await client.get_position(_TEST_SYMBOL)
    test_row.partial_close_position_check_response_json = json.dumps(pos_resp, default=str)
    remaining = _find_open_long_position(pos_resp)  # raises on a real API error, same as everywhere else

    if remaining is None:
        return (
            f"expected an open {_TEST_SYMBOL} position with ~{expected_remaining_qty} remaining after "
            f"the reduction, but no matching open position was found at all"
        )

    new_position_id = remaining.get("positionId")
    actual_qty = float(remaining.get("qty") or 0)
    test_row.position_id_after_partial_close = new_position_id
    test_row.qty_after_partial_close = actual_qty
    test_row.position_id = new_position_id  # kept current regardless of whether it changed -- see docstring

    tolerance = 0.5 * (10 ** -test_row.base_precision)
    if abs(actual_qty - expected_remaining_qty) > tolerance:
        return (
            f"remaining position qty={actual_qty} does not match the expected remainder "
            f"{expected_remaining_qty} (tolerance {tolerance}) after the reduction"
        )
    return None


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

        # Independent confirmation of what remains, not an assumption:
        # this is the position-lifecycle verification Andy/DeepSeek's
        # own ladder checklist flagged as an open question -- see
        # _verify_position_after_reduction()'s own docstring.
        expected_remaining_qty = test_row.qty - float(qty_str)
        problem = await _verify_position_after_reduction(client, test_row, expected_remaining_qty)
        db.flush()

        if problem is not None:
            test_row.status = "FAILED"
            test_row.error_detail = f"{problem} -- CHECK THE EXCHANGE DIRECTLY. Raw response saved (partial_close_position_check_response_json)."
            db.flush()
            executor_accounts.write_audit(
                db, "TEST_MECHANISM_FAILED", test_row.error_detail,
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
            return test_row

        test_row.status = "PARTIAL_CLOSED"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_PARTIAL_CLOSED",
            f"partial close of {qty_str} executed and confirmed filled, remaining position verified "
            f"(positionId={test_row.position_id}, qty={test_row.qty_after_partial_close})",
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


# 2026-09-06 -- the resting reduce-only LIMIT-at-T1 test. DeepSeek's
# design review flagged this as the one open technical gap before
# Component B: the aligned rerun's new default management policy
# (50/origstop) depends on a plain resting LIMIT order for the T1 leg,
# NOT Bitunix's separate TP/SL trigger-order system (set_position_tpsl/
# modify_position_tp_sl_order, already covered by TPSL_SET above) --
# and that mechanism has never been tested against the real exchange.
# A second, alternate way to reach PARTIAL_CLOSED from TPSL_SET; the
# rest of the ladder (move_sl_to_breakeven, flash_close_remainder)
# doesn't need to know or care which path got there.
_T1_LIMIT_STILL_PENDING_STATUSES = ("NEW", "PART_FILLED", "INIT")


async def place_resting_t1_limit(
    db: Session, account: ExecutorAccount, test_row: ExecutorMechanismTest, actor: str,
    t1_pct: float = _DEFAULT_TP_SL_PCT, qty_pct: float = _DEFAULT_PARTIAL_CLOSE_PCT,
) -> ExecutorMechanismTest:
    """Places a plain resting reduce-only LIMIT order at a target price
    above the fill price. Fire-and-return: unlike every other action in
    this ladder, this does NOT poll for a fill -- the order may sit
    unfilled for a long time (this is deliberately the "unattended"
    test), so the caller checks back later via
    check_resting_t1_limit_status(). Andy's own live-firing note: the
    default t1_pct is small enough to matter as a real percentage move,
    but for an actual test session use something tiny (~0.0005-0.001)
    so the order actually touches during the session -- both callers
    (the route, the UI) accept this per-call, nothing hardcoded here
    forces a slow test."""
    if test_row.status != "TPSL_SET":
        raise MechanismTestInvalidState(f"cannot place a resting T1 limit from status {test_row.status!r} -- expected TPSL_SET")
    api_key, api_secret = await _require_gates_open(db, account)
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)

    target_price = test_row.fill_price * (1 + t1_pct)
    price_str = executor_sizing.round_price_to_precision(target_price, test_row.quote_precision)
    qty_str = executor_sizing.round_qty_to_precision(test_row.qty * qty_pct, test_row.base_precision)
    try:
        if float(qty_str) <= 0:
            raise ValueError(
                f"resting T1 limit qty of {qty_pct:.0%} of qty={test_row.qty} floors to {qty_str} at "
                f"{test_row.base_precision} decimals -- unrepresentable, refusing to send a zero-qty order")
        resp = await client.place_order(
            symbol=_TEST_SYMBOL, qty=qty_str, price=price_str, side="SELL", trade_side="CLOSE",
            order_type="LIMIT", position_id=test_row.position_id, reduce_only=True)
        test_row.t1_limit_place_response_json = json.dumps(resp, default=str)
        test_row.t1_limit_target_price = float(price_str)
        test_row.t1_limit_qty = float(qty_str)
        test_row.t1_limit_exchange_order_id = resp["data"]["orderId"]
        test_row.status = "T1_LIMIT_PLACED"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_T1_LIMIT_PLACED",
            f"resting reduce-only LIMIT placed at {price_str} for qty={qty_str} (orderId={test_row.t1_limit_exchange_order_id})",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=resp)
        return test_row
    except Exception as e:
        test_row.status = "FAILED"
        test_row.error_detail = str(e)
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_MECHANISM_FAILED", f"place resting T1 limit failed: {e}",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
        raise


async def check_resting_t1_limit_status(
    db: Session, account: ExecutorAccount, test_row: ExecutorMechanismTest, actor: str,
) -> ExecutorMechanismTest:
    """ONE on-demand check of the resting T1 limit's real order status --
    never a poll loop, since the order may sit unfilled for a long time
    (see place_resting_t1_limit()'s own docstring). DeepSeek design
    review amendment: fails CLOSED on anything outside the known still-
    pending/FILLED set, rather than enumerating only the 5 documented
    status values -- a CANCELED order nobody asked to cancel (e.g. the
    position closed via its own SL before the limit ever touched), or
    any undocumented status Bitunix's docs don't list (the same class
    of doc-vs-reality gap already found once with the `side` field),
    must never leave this test stuck in an unhandled state."""
    if test_row.status != "T1_LIMIT_PLACED":
        raise MechanismTestInvalidState(f"cannot check resting T1 limit status from status {test_row.status!r} -- expected T1_LIMIT_PLACED")
    api_key, api_secret = await _require_gates_open(db, account)
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)
    try:
        resp = await client.get_order_detail(order_id=test_row.t1_limit_exchange_order_id)
        test_row.t1_limit_check_response_json = json.dumps(resp, default=str)
        db.flush()
        if resp.get("code") not in (0, None):
            raise ValueError(f"get_order_detail returned a real API error: code={resp.get('code')} msg={resp.get('msg')!r}")
        status = (resp.get("data") or {}).get("status")

        if status in _T1_LIMIT_STILL_PENDING_STATUSES:
            executor_accounts.write_audit(
                db, "TEST_T1_LIMIT_STATUS_CHECKED", f"resting T1 limit still {status} -- not yet filled",
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=resp)
            return test_row

        if status == "FILLED":
            expected_remaining_qty = test_row.qty - test_row.t1_limit_qty
            problem = await _verify_position_after_reduction(client, test_row, expected_remaining_qty)
            db.flush()
            if problem is not None:
                test_row.status = "FAILED"
                test_row.error_detail = f"{problem} -- CHECK THE EXCHANGE DIRECTLY. Raw response saved (partial_close_position_check_response_json)."
                db.flush()
                executor_accounts.write_audit(
                    db, "TEST_MECHANISM_FAILED", test_row.error_detail,
                    account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
                return test_row
            test_row.status = "PARTIAL_CLOSED"
            db.flush()
            executor_accounts.write_audit(
                db, "TEST_T1_LIMIT_FILLED",
                f"resting T1 limit filled and confirmed, remaining position verified "
                f"(positionId={test_row.position_id}, qty={test_row.qty_after_partial_close})",
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=resp)
            return test_row

        # Anything else at all -- CANCELED, or any undocumented value --
        # fail closed rather than get stuck in an unhandled UI state.
        test_row.status = "FAILED"
        test_row.error_detail = (
            f"resting T1 limit order (orderId={test_row.t1_limit_exchange_order_id}) reports unexpected "
            f"status={status!r} -- CHECK THE EXCHANGE DIRECTLY. Raw response saved (t1_limit_check_response_json)."
        )
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_MECHANISM_FAILED", test_row.error_detail,
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=resp)
        return test_row
    except Exception as e:
        test_row.status = "FAILED"
        test_row.error_detail = str(e)
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_MECHANISM_FAILED", f"check resting T1 limit status failed: {e}",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
        raise


async def cancel_resting_t1_limit(
    db: Session, account: ExecutorAccount, test_row: ExecutorMechanismTest, actor: str,
) -> ExecutorMechanismTest:
    """Cancels the resting T1 limit order and reverts to TPSL_SET --
    UNLESS the order partially (or fully) filled before the cancel
    request landed (DeepSeek design review amendment): reverting
    straight to TPSL_SET in that case would leave test_row.qty stale
    and make a later MARKET partial-close compute the wrong size, the
    same "assumption stood in for verification" class of bug that
    caused the real TP-wipe incident. Checks the real `tradeQty` field
    (confirmed via Bitunix's own docs: "Fill amount (base coin),
    distinct from the original qty requested") on the post-cancel
    get_order_detail() call -- already needed to confirm the cancel's
    final state -- and if it's meaningfully above zero, treats this
    exactly like a fill: the shared position-lifecycle verification
    runs and the row moves to PARTIAL_CLOSED instead of reverting."""
    if test_row.status != "T1_LIMIT_PLACED":
        raise MechanismTestInvalidState(f"cannot cancel resting T1 limit from status {test_row.status!r} -- expected T1_LIMIT_PLACED")
    api_key, api_secret = await _require_gates_open(db, account)
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)
    try:
        cancel_resp = await client.cancel_orders(_TEST_SYMBOL, [test_row.t1_limit_exchange_order_id])
        test_row.t1_limit_cancel_response_json = json.dumps(cancel_resp, default=str)
        db.flush()

        # Never trust a bare "ok" -- confirm the specific orderId is
        # actually in successList, same discipline as every other
        # mutation in this module.
        success_ids = {e.get("orderId") for e in (cancel_resp.get("data") or {}).get("successList") or []}
        if test_row.t1_limit_exchange_order_id not in success_ids:
            test_row.status = "FAILED"
            test_row.error_detail = (
                f"cancel_orders did not report orderId={test_row.t1_limit_exchange_order_id} in successList "
                f"-- CHECK THE EXCHANGE DIRECTLY. Raw response saved (t1_limit_cancel_response_json)."
            )
            db.flush()
            executor_accounts.write_audit(
                db, "TEST_MECHANISM_FAILED", test_row.error_detail,
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=cancel_resp)
            return test_row

        # Independent confirmation of the ACTUAL final state, including
        # whether anything filled before the cancel landed -- never
        # trust cancel_orders' own successList alone for that.
        detail_resp = await client.get_order_detail(order_id=test_row.t1_limit_exchange_order_id)
        test_row.t1_limit_check_response_json = json.dumps(detail_resp, default=str)
        db.flush()
        if detail_resp.get("code") not in (0, None):
            raise ValueError(f"get_order_detail returned a real API error: code={detail_resp.get('code')} msg={detail_resp.get('msg')!r}")
        traded_qty = float((detail_resp.get("data") or {}).get("tradeQty") or 0)

        tolerance = 0.5 * (10 ** -test_row.base_precision)
        if traded_qty > tolerance:
            test_row.t1_limit_qty = traded_qty   # the ACTUAL filled amount, not the originally requested qty
            expected_remaining_qty = test_row.qty - traded_qty
            problem = await _verify_position_after_reduction(client, test_row, expected_remaining_qty)
            db.flush()
            if problem is not None:
                test_row.status = "FAILED"
                test_row.error_detail = f"{problem} -- CHECK THE EXCHANGE DIRECTLY. Raw response saved (partial_close_position_check_response_json)."
                db.flush()
                executor_accounts.write_audit(
                    db, "TEST_MECHANISM_FAILED", test_row.error_detail,
                    account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
                return test_row
            test_row.status = "PARTIAL_CLOSED"
            db.flush()
            executor_accounts.write_audit(
                db, "TEST_T1_LIMIT_CANCELED_AFTER_PARTIAL_FILL",
                f"resting T1 limit canceled but had already filled {traded_qty} before the cancel landed -- "
                f"treated as a partial close, remaining position verified "
                f"(positionId={test_row.position_id}, qty={test_row.qty_after_partial_close})",
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=detail_resp)
            return test_row

        test_row.status = "TPSL_SET"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_T1_LIMIT_CANCELED", "resting T1 limit canceled, confirmed nothing filled, position unchanged",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=cancel_resp)
        return test_row
    except Exception as e:
        test_row.status = "FAILED"
        test_row.error_detail = str(e)
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_MECHANISM_FAILED", f"cancel resting T1 limit failed: {e}",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
        raise


# 2026-09-07 -- Domain 2 REQUIRED LIVE PRE-FLIGHT (executor_live_engine.py's
# approved plan): the real engine places a protective stop (TP/SL) + a
# resting T1 reduce-only LIMIT + a resting T3 reduce-only LIMIT all
# CONCURRENTLY on the same position, right after entry fill. Only the
# single-resting-T1-limit case (above) has ever been tested live -- this
# is the one open technical question before that engine is trusted with
# a real TradePlan fill: do all three coexist without the exchange
# rejecting anything, and does the TP/SL's effective covered qty auto-
# track the remaining position once one leg fills, or does it silently
# over/under-cover? This step answers that empirically, on a real
# account, with a few dollars of exposure -- same discipline as every
# other step in this ladder. Starts from TPSL_SET, same as
# place_resting_t1_limit() -- a second, alternate way to reach
# PARTIAL_CLOSED, this time via two concurrent resting limits instead of
# one.
_T3_LIMIT_STILL_PENDING_STATUSES = _T1_LIMIT_STILL_PENDING_STATUSES


async def place_concurrent_t1_t3_limits(
    db: Session, account: ExecutorAccount, test_row: ExecutorMechanismTest, actor: str,
    t1_pct: float = _DEFAULT_TP_SL_PCT, t3_pct: float = 0.02, qty_pct: float = _DEFAULT_PARTIAL_CLOSE_PCT,
) -> ExecutorMechanismTest:
    """Places T1 (qty_pct of qty, above fill) AND T3 (the remaining qty,
    further above fill) as two SEPARATE resting reduce-only LIMIT orders,
    both alongside the TP/SL stop already set by
    place_confirm_and_set_initial_tpsl(). Fire-and-return, same as
    place_resting_t1_limit() -- check_concurrent_limits_status() checks
    back later. t3_pct must be strictly greater than t1_pct (T3 sits
    further out) -- checked here, not assumed, since a caller typo could
    otherwise produce two crossed resting limits."""
    if test_row.status != "TPSL_SET":
        raise MechanismTestInvalidState(f"cannot place concurrent T1+T3 limits from status {test_row.status!r} -- expected TPSL_SET")
    if t3_pct <= t1_pct:
        raise ValueError(f"t3_pct ({t3_pct}) must be strictly greater than t1_pct ({t1_pct}) -- T3 sits further from fill than T1")
    api_key, api_secret = await _require_gates_open(db, account)
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)

    t1_target_price = test_row.fill_price * (1 + t1_pct)
    t3_target_price = test_row.fill_price * (1 + t3_pct)
    t1_price_str = executor_sizing.round_price_to_precision(t1_target_price, test_row.quote_precision)
    t3_price_str = executor_sizing.round_price_to_precision(t3_target_price, test_row.quote_precision)
    t1_qty_str = executor_sizing.round_qty_to_precision(test_row.qty * qty_pct, test_row.base_precision)
    t3_qty_str = executor_sizing.round_qty_to_precision(test_row.qty - float(t1_qty_str), test_row.base_precision)
    try:
        if float(t1_qty_str) <= 0 or float(t3_qty_str) <= 0:
            raise ValueError(
                f"T1/T3 qty split of qty={test_row.qty} at qty_pct={qty_pct:.0%} floors to T1={t1_qty_str}/T3={t3_qty_str} "
                f"at {test_row.base_precision} decimals -- unrepresentable, refusing to send a zero-qty order")

        t1_resp = await client.place_order(
            symbol=_TEST_SYMBOL, qty=t1_qty_str, price=t1_price_str, side="SELL", trade_side="CLOSE",
            order_type="LIMIT", position_id=test_row.position_id, reduce_only=True, effect="POST_ONLY")
        test_row.t1_limit_place_response_json = json.dumps(t1_resp, default=str)
        test_row.t1_limit_target_price = float(t1_price_str)
        test_row.t1_limit_qty = float(t1_qty_str)
        test_row.t1_limit_exchange_order_id = t1_resp["data"]["orderId"]
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_T1_LIMIT_PLACED", f"[concurrent pre-flight] T1 resting reduce-only LIMIT placed at {t1_price_str} for qty={t1_qty_str}",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=t1_resp)

        t3_resp = await client.place_order(
            symbol=_TEST_SYMBOL, qty=t3_qty_str, price=t3_price_str, side="SELL", trade_side="CLOSE",
            order_type="LIMIT", position_id=test_row.position_id, reduce_only=True, effect="POST_ONLY")
        test_row.t3_limit_place_response_json = json.dumps(t3_resp, default=str)
        test_row.t3_limit_target_price = float(t3_price_str)
        test_row.t3_limit_qty = float(t3_qty_str)
        test_row.t3_limit_exchange_order_id = t3_resp["data"]["orderId"]
        test_row.status = "T1_AND_T3_LIMITS_PLACED"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_T1_LIMIT_PLACED",
            f"[concurrent pre-flight] T3 resting reduce-only LIMIT ALSO placed at {t3_price_str} for qty={t3_qty_str} -- "
            f"both now resting concurrently alongside the existing TP/SL stop (positionId={test_row.position_id})",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=t3_resp)
        return test_row
    except Exception as e:
        test_row.status = "FAILED"
        test_row.error_detail = str(e)
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_MECHANISM_FAILED", f"place concurrent T1+T3 limits failed: {e}",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
        raise


async def check_concurrent_limits_status(
    db: Session, account: ExecutorAccount, test_row: ExecutorMechanismTest, actor: str,
) -> ExecutorMechanismTest:
    """ONE on-demand check of BOTH resting limits' order status, PLUS an
    unconditional get_pending_tp_sl_order snapshot -- the actual evidence
    for the open question this whole step exists to answer (does the
    stop's effective covered qty auto-track after a leg fills, or does it
    silently over/under-cover). That snapshot is captured every time this
    is called, filled-or-not, and never interpreted/enforced by code --
    it's for Andy/DeepSeek to read directly (tpsl_check_after_leg_fill_
    response_json). Moves to PARTIAL_CLOSED once EITHER leg fills (same
    "fails closed on anything unexpected" discipline as
    check_resting_t1_limit_status())."""
    if test_row.status != "T1_AND_T3_LIMITS_PLACED":
        raise MechanismTestInvalidState(f"cannot check concurrent limits status from status {test_row.status!r} -- expected T1_AND_T3_LIMITS_PLACED")
    api_key, api_secret = await _require_gates_open(db, account)
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)
    try:
        t1_resp = await client.get_order_detail(order_id=test_row.t1_limit_exchange_order_id)
        test_row.t1_limit_check_response_json = json.dumps(t1_resp, default=str)
        t3_resp = await client.get_order_detail(order_id=test_row.t3_limit_exchange_order_id)
        test_row.t3_limit_check_response_json = json.dumps(t3_resp, default=str)
        db.flush()
        if t1_resp.get("code") not in (0, None):
            raise ValueError(f"get_order_detail(T1) returned a real API error: code={t1_resp.get('code')} msg={t1_resp.get('msg')!r}")
        if t3_resp.get("code") not in (0, None):
            raise ValueError(f"get_order_detail(T3) returned a real API error: code={t3_resp.get('code')} msg={t3_resp.get('msg')!r}")
        t1_status = (t1_resp.get("data") or {}).get("status")
        t3_status = (t3_resp.get("data") or {}).get("status")

        # The evidence this step exists to capture -- taken regardless of
        # either leg's status, so a "still pending" check still shows the
        # baseline TP/SL state for comparison against the post-fill one.
        tpsl_snapshot = await client.get_pending_tp_sl_order(symbol=_TEST_SYMBOL, position_id=test_row.position_id)
        test_row.tpsl_check_after_leg_fill_response_json = json.dumps(tpsl_snapshot, default=str)
        db.flush()

        any_filled = t1_status == "FILLED" or t3_status == "FILLED"
        both_pending = t1_status in _T1_LIMIT_STILL_PENDING_STATUSES and t3_status in _T3_LIMIT_STILL_PENDING_STATUSES

        if not any_filled and not both_pending:
            test_row.status = "FAILED"
            test_row.error_detail = (
                f"concurrent T1/T3 limits report unexpected statuses (T1={t1_status!r}, T3={t3_status!r}) -- "
                f"CHECK THE EXCHANGE DIRECTLY. Raw responses saved (t1_limit_check_response_json/t3_limit_check_response_json)."
            )
            db.flush()
            executor_accounts.write_audit(
                db, "TEST_MECHANISM_FAILED", test_row.error_detail,
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail={"t1": t1_resp, "t3": t3_resp})
            return test_row

        if both_pending:
            executor_accounts.write_audit(
                db, "TEST_T1_LIMIT_STATUS_CHECKED",
                f"[concurrent pre-flight] both T1 ({t1_status}) and T3 ({t3_status}) still pending -- TP/SL snapshot captured",
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=tpsl_snapshot)
            return test_row

        filled_leg = "T1" if t1_status == "FILLED" else "T3"
        filled_qty = test_row.t1_limit_qty if filled_leg == "T1" else test_row.t3_limit_qty
        expected_remaining_qty = test_row.qty - filled_qty
        problem = await _verify_position_after_reduction(client, test_row, expected_remaining_qty)
        db.flush()
        if problem is not None:
            test_row.status = "FAILED"
            test_row.error_detail = f"{problem} -- CHECK THE EXCHANGE DIRECTLY. Raw response saved (partial_close_position_check_response_json)."
            db.flush()
            executor_accounts.write_audit(
                db, "TEST_MECHANISM_FAILED", test_row.error_detail,
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
            return test_row

        test_row.status = "PARTIAL_CLOSED"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_T1_LIMIT_FILLED",
            f"[concurrent pre-flight] {filled_leg} leg filled while the other rested unfilled alongside the TP/SL stop -- "
            f"remaining position verified (positionId={test_row.position_id}, qty={test_row.qty_after_partial_close}). "
            f"See tpsl_check_after_leg_fill_response_json for whether the stop's covered qty auto-tracked.",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=tpsl_snapshot)
        return test_row
    except Exception as e:
        test_row.status = "FAILED"
        test_row.error_detail = str(e)
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_MECHANISM_FAILED", f"check concurrent limits status failed: {e}",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
        raise


async def cancel_concurrent_limits(
    db: Session, account: ExecutorAccount, test_row: ExecutorMechanismTest, actor: str,
) -> ExecutorMechanismTest:
    """Cancels BOTH resting limits (whichever are still live) via one
    batch cancel_orders call, then confirms via get_order_detail exactly
    like cancel_resting_t1_limit() -- reverts to TPSL_SET only if NEITHER
    leg filled before the cancel landed; treats any real fill found on
    either leg as a partial close instead, same "never assume, always
    verify" discipline."""
    if test_row.status != "T1_AND_T3_LIMITS_PLACED":
        raise MechanismTestInvalidState(f"cannot cancel concurrent limits from status {test_row.status!r} -- expected T1_AND_T3_LIMITS_PLACED")
    api_key, api_secret = await _require_gates_open(db, account)
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)
    try:
        order_ids = [test_row.t1_limit_exchange_order_id, test_row.t3_limit_exchange_order_id]
        cancel_resp = await client.cancel_orders(_TEST_SYMBOL, order_ids)
        test_row.t1_limit_cancel_response_json = json.dumps(cancel_resp, default=str)
        test_row.t3_limit_cancel_response_json = test_row.t1_limit_cancel_response_json
        db.flush()

        t1_detail = await client.get_order_detail(order_id=test_row.t1_limit_exchange_order_id)
        t3_detail = await client.get_order_detail(order_id=test_row.t3_limit_exchange_order_id)
        test_row.t1_limit_check_response_json = json.dumps(t1_detail, default=str)
        test_row.t3_limit_check_response_json = json.dumps(t3_detail, default=str)
        db.flush()
        if t1_detail.get("code") not in (0, None) or t3_detail.get("code") not in (0, None):
            raise ValueError(f"get_order_detail returned a real API error after cancel: T1 code={t1_detail.get('code')}, T3 code={t3_detail.get('code')}")

        t1_traded = float((t1_detail.get("data") or {}).get("tradeQty") or 0)
        t3_traded = float((t3_detail.get("data") or {}).get("tradeQty") or 0)
        tolerance = 0.5 * (10 ** -test_row.base_precision)

        if t1_traded > tolerance or t3_traded > tolerance:
            filled_qty = t1_traded if t1_traded > tolerance else t3_traded
            expected_remaining_qty = test_row.qty - filled_qty
            problem = await _verify_position_after_reduction(client, test_row, expected_remaining_qty)
            db.flush()
            if problem is not None:
                test_row.status = "FAILED"
                test_row.error_detail = f"{problem} -- CHECK THE EXCHANGE DIRECTLY."
                db.flush()
                executor_accounts.write_audit(
                    db, "TEST_MECHANISM_FAILED", test_row.error_detail,
                    account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
                return test_row
            test_row.status = "PARTIAL_CLOSED"
            db.flush()
            executor_accounts.write_audit(
                db, "TEST_T1_LIMIT_CANCELED_AFTER_PARTIAL_FILL",
                f"[concurrent pre-flight] canceled but a leg had already filled (T1={t1_traded}, T3={t3_traded}) before the cancel landed",
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail={"t1": t1_detail, "t3": t3_detail})
            return test_row

        test_row.status = "TPSL_SET"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_T1_LIMIT_CANCELED", "[concurrent pre-flight] both resting limits canceled, confirmed nothing filled, position unchanged",
            account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor, detail=cancel_resp)
        return test_row
    except Exception as e:
        test_row.status = "FAILED"
        test_row.error_detail = str(e)
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_MECHANISM_FAILED", f"cancel concurrent limits failed: {e}",
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
