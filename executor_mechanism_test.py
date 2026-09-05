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
_TEST_DIRECTION = "LONG"
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
    or raise a clear error, never silently guess."""
    pairs = pairs_resp.get("data") or []
    for pair in pairs:
        if pair.get("symbol") == symbol:
            return pair
    raise ValueError(f"no trading pair entry found for symbol {symbol!r} in get_trading_pairs response")


def _find_open_long_position(pos_resp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """get_position()'s data is a LIST -- filter for an open LONG BTCUSDT
    position. Returns the single match, None if there isn't one, or
    raises a clear error if there's more than one -- an ambiguous state
    this module refuses to guess through rather than silently picking one."""
    positions: List[Dict[str, Any]] = pos_resp.get("data") or []
    matches = [p for p in positions if p.get("symbol") == _TEST_SYMBOL and p.get("side") == _TEST_DIRECTION]
    if len(matches) > 1:
        raise ValueError(
            f"found {len(matches)} open {_TEST_DIRECTION} {_TEST_SYMBOL} positions -- "
            f"ambiguous, refusing to guess which one belongs to this test")
    return matches[0] if matches else None


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

        position = None
        for _ in range(_FILL_POLL_MAX_ATTEMPTS):
            await asyncio.sleep(_FILL_POLL_INTERVAL_SEC)
            pos_resp = await client.get_position(_TEST_SYMBOL)
            position = _find_open_long_position(pos_resp)
            if position is not None:
                break

        if position is None:
            test_row.status = "FAILED"
            test_row.error_detail = (
                f"order placed (orderId={test_row.exchange_order_id}) but no matching open "
                f"position found after {_FILL_POLL_MAX_ATTEMPTS} attempts -- CHECK THE EXCHANGE "
                f"DIRECTLY before taking any further action on this account")
            db.flush()
            executor_accounts.write_audit(
                db, "TEST_MECHANISM_FAILED", test_row.error_detail,
                account_id=account.id, executor_mechanism_test_id=test_row.id, actor=actor)
            return test_row  # does NOT raise -- "go look at the exchange," not a code bug

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
        test_row.status = "TPSL_SET"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_INITIAL_TPSL_SET", f"initial TP={tp_str} SL={sl_str} set on positionId={test_row.position_id}",
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
        test_row.status = "PARTIAL_CLOSED"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_PARTIAL_CLOSED", f"partial close of {qty_str} executed",
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
    try:
        resp = await client.modify_position_tp_sl_order(
            symbol=_TEST_SYMBOL, position_id=test_row.position_id, sl_price=sl_str, sl_stop_type="LAST_PRICE")
        test_row.sl_breakeven_response_json = json.dumps(resp, default=str)
        test_row.breakeven_sl_price = float(sl_str)
        test_row.sl_breakeven_exchange_order_id = resp["data"]["orderId"]
        test_row.status = "SL_MOVED_BREAKEVEN"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_SL_MOVED_TO_BREAKEVEN", f"SL moved to breakeven ({sl_str})",
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
        test_row.status = "FULLY_CLOSED"
        db.flush()
        executor_accounts.write_audit(
            db, "TEST_POSITION_FLASH_CLOSED", "remainder flash-closed",
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
