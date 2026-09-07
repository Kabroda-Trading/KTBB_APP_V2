# executor_live_engine.py
# ==============================================================================
# EXECUTOR LIVE ENGINE -- Domain 2, Stage 3. Places and manages a REAL
# Bitunix order for a real, TradePlan-driven trade. Built 2026-09-07 per
# GATE_REBUILD_SPEC.md §3/§5 (Kabroda AI Brain repo), following the
# shipped Three-Outcome Gate Rebuild (decision_engine.py).
#
# This generalizes executor_mechanism_test.py's proven, live-verified
# order mechanics (Andy's real ladder test, AGENT_LOG.md MILESTONE entry)
# into TradePlan-linked, tier-aware, symbol/side-agnostic functions. It
# does not invent new exchange mechanics -- resting LIMIT entry, resting
# reduce-only LIMIT exits, and Bitunix's TP/SL trigger-order system for
# the protective stop are all identical in kind to what the mechanism
# test already proved live; this module just makes them TradePlan-linked
# and runs them unattended via a background loop instead of one manual
# click at a time.
#
# THE AUDITED MANAGEMENT RULE (CLEAN_REPORT.md §2, verified against the
# real 128-trade corpus, NOT the stale 30/70 rule in ledger_closing_
# engine.py -- see that module's own deprecated-marker comment):
#   Entry: resting LIMIT at the trigger, 50% now / 50% runner.
#   T1 (0.618x box): take 50%, stop stays at the original level.
#   T2 (1.0x box, decision_engine.py's own box multiple -- see that
#       file's header for the T1/T2/T3=0.618/1.0/1.618 formula, CLAUDE.md
#       rule #1, must-never-change): PREMIUM moves the stop to breakeven,
#       mechanically, unconditionally -- verified against the real
#       31-trade premium corpus: 13 touched T2, 10 rode on to T3 at zero
#       cost, 3 stopped after T2 for +1.50R saved. STANDARD's stop never
#       moves at T2.
#   T3 (1.618x box) or the runner's stop: the runner exits, both tiers.
#
# MAKER-ONLY IS MECHANICALLY ENFORCED HERE, not just placement discipline
# (CLEAN_REPORT.md §4: taker fees turn +42R into -0.5R). Every LIMIT order
# this module places -- entry, T1, T3 -- passes effect="POST_ONLY"
# (verified live against bitunix.com/api-docs, 2026-09-07: IOC/FOK/GTC/
# POST_ONLY are the real enum values). No caller in this codebase used
# this before; every resting limit placed so far (including the proven
# T1 ladder test) defaulted to GTC.
#
# POST-T2 "SMART" RE-EVALUATION (pull the runner early if the move looks
# dead) is explicitly NOT built here -- GATE_REBUILD_SPEC.md's own
# language for it has no validated criteria behind it (the +5.32R premium
# number is the UNCONDITIONAL mechanical BE move, not a reval). This
# module only LOGS the reval inputs (t2_reval_fuel_verdict/
# t2_reval_micro_regime) at the real T2 touch, as a live-audit-loop
# observation for the Brain repo to eventually validate a real rule
# against -- it never reads them back to change the mechanical action.
#
# "NO AI IMPROVISATION MID-TRADE": the full order layout (stop + T1 + T3)
# is placed ONCE, atomically, right after entry fill confirmation. The
# background loop (run_executor_position_loop(), registered in main.py's
# lifespan()) only WATCHES status and reacts per the fixed rule above --
# it never re-decides direction, size, or targets after that point.
#
# REQUIRED LIVE PRE-FLIGHT VERIFICATION, per the approved plan: placing a
# stop (set_position_tpsl) + a resting T1 reduce-only LIMIT + a resting T3
# reduce-only LIMIT CONCURRENTLY on the same position has never been
# tested live -- only the single-resting-T1-limit case has (the proven
# ladder test). Andy must run the extended mechanism-test pre-flight step
# (executor_mechanism_test.py) live and confirm it passes before this
# module is wired to a real TradePlan fill in production.
# ==============================================================================

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

import executor_accounts
import executor_bitunix_client
import executor_sizing
import market_data
from database import ExecutorAccount, ExecutorOrder, TradePlan

_LONG, _SHORT = "LONG", "SHORT"
_TERMINAL_STATES = ("CLOSED_STOP_BEFORE_T1", "CLOSED_RUNNER_STOP", "CLOSED_T3", "CLOSED_ERROR")
# 2026-09-05 real doc-vs-reality gap (executor_mechanism_test.py's own
# comment): get_position's real `side` field is "BUY"/"SELL", not
# "LONG"/"SHORT" as Bitunix's docs claim.
_POSITION_SIDE_FOR_DIRECTION = {_LONG: "BUY", _SHORT: "SELL"}
_ENTRY_SIDE_FOR_DIRECTION = {_LONG: "BUY", _SHORT: "SELL"}
_EXIT_SIDE_FOR_DIRECTION = {_LONG: "SELL", _SHORT: "BUY"}   # closing a LONG sells, closing a SHORT buys


async def _get_pair_precision(client: "executor_bitunix_client.BitunixClient", symbol: str) -> Dict[str, Any]:
    """Same shape as executor_mechanism_test.py's _extract_pair() --
    duplicated rather than imported from that module on purpose: that
    module is deliberately isolated from TradePlan/ExecutorOrder (see its
    own header), and this is a tiny, stable lookup, not worth coupling
    the two for."""
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


def _client_for(account: ExecutorAccount) -> "executor_bitunix_client.BitunixClient":
    api_key, api_secret = executor_accounts.get_decrypted_credentials(account)
    if not api_key or not api_secret:
        raise ValueError(f"account {account.id} has no credentials set -- cannot place real orders")
    return executor_bitunix_client.BitunixClient(api_key, api_secret)


async def place_entry_order(db: Session, account: ExecutorAccount, trade_plan_row: TradePlan, order_row: ExecutorOrder) -> None:
    """Places the real resting LIMIT entry order. Called once, right after
    build_hypothetical_order() has already computed and validated qty/
    leverage/liquidation safety (order_row's fields are already filled in
    -- this function never re-decides sizing, only places the order)."""
    client = _client_for(account)
    symbol = order_row.symbol.replace("/", "")
    pair = await _get_pair_precision(client, symbol)
    price_str = executor_sizing.round_price_to_precision(order_row.entry_price, pair["quote_precision"])
    qty_str = executor_sizing.round_qty_to_precision(order_row.qty, pair["base_precision"])

    if float(qty_str) <= 0:
        order_row.management_state = "CLOSED_ERROR"
        executor_accounts.write_audit(
            db, "ERROR", f"entry qty {order_row.qty} floors to {qty_str} at {pair['base_precision']} decimals -- refusing to place a zero-qty order",
            account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system")
        return

    resp = await client.place_order(
        symbol=symbol, qty=qty_str, price=price_str,
        side=_ENTRY_SIDE_FOR_DIRECTION[order_row.direction], trade_side="OPEN",
        order_type="LIMIT", effect="POST_ONLY",
    )
    order_row.exchange_response_json = json.dumps(resp, default=str)
    order_row.entry_exchange_order_id = resp["data"]["orderId"]
    order_row.entry_status = "NEW"
    order_row.management_state = "PENDING_ENTRY"
    executor_accounts.write_audit(
        db, "ORDER_PLACED", f"real entry LIMIT placed at {price_str} qty={qty_str} (orderId={order_row.entry_exchange_order_id})",
        account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system", detail=resp)


def _find_open_position(pos_resp: Dict[str, Any], symbol: str, direction: str) -> Optional[Dict[str, Any]]:
    if pos_resp.get("code") not in (0, None):
        raise ValueError(f"get_position returned a real API error: code={pos_resp.get('code')} msg={pos_resp.get('msg')!r}")
    side = _POSITION_SIDE_FOR_DIRECTION[direction]
    positions: List[Dict[str, Any]] = pos_resp.get("data") or []
    matches = [p for p in positions if p.get("symbol") == symbol and p.get("side") == side]
    if len(matches) > 1:
        raise ValueError(f"found {len(matches)} open {direction} {symbol} positions for order {matches} -- ambiguous, refusing to guess")
    return matches[0] if matches else None


async def check_entry_fill_and_place_exits(db: Session, account: ExecutorAccount, trade_plan_row: TradePlan, order_row: ExecutorOrder) -> None:
    """ONE on-demand check per tick (never a blocking poll loop -- this
    runs from the shared background loop, see run_executor_position_loop
    below). On FILLED, places the protective stop + T1 + T3 exits all in
    one precommitted batch -- "no AI improvisation mid-trade": the full
    layout is set once, atomically, right here."""
    client = _client_for(account)
    symbol = order_row.symbol.replace("/", "")
    resp = await client.get_order_detail(order_id=order_row.entry_exchange_order_id)
    if resp.get("code") not in (0, None):
        raise ValueError(f"get_order_detail returned a real API error: code={resp.get('code')} msg={resp.get('msg')!r}")
    data = resp.get("data") or {}
    status = data.get("status")
    order_row.entry_status = status
    if status != "FILLED":
        return   # still resting -- re-checked next tick, no state change

    pos_resp = await client.get_position(symbol)
    position = _find_open_position(pos_resp, symbol, order_row.direction)
    if position is None:
        order_row.management_state = "CLOSED_ERROR"
        executor_accounts.write_audit(
            db, "ERROR", f"entry order {order_row.entry_exchange_order_id} confirmed FILLED but no matching open position found -- CHECK THE EXCHANGE DIRECTLY",
            account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system", detail=pos_resp)
        return

    order_row.position_id = position["positionId"]
    order_row.entry_fill_price = float(position["avgOpenPrice"])
    order_row.entry_fill_time = datetime.datetime.utcnow()

    pair = await _get_pair_precision(client, symbol)
    half_qty_str = executor_sizing.round_qty_to_precision(order_row.qty * 0.5, pair["base_precision"])
    exit_side = _EXIT_SIDE_FOR_DIRECTION[order_row.direction]

    # (a) protective stop, exactly as the mechanism test proved live.
    sl_str = executor_sizing.round_price_to_precision(order_row.stop_price, pair["quote_precision"])
    sl_resp = await client.set_position_tpsl(
        symbol=symbol, position_id=order_row.position_id, sl_price=sl_str, sl_stop_type="LAST_PRICE")
    order_row.sl_exchange_order_id = sl_resp["data"]["orderId"]
    order_row.sl_price_current = float(sl_str)
    order_row.sl_set_at = datetime.datetime.utcnow()

    # (b) T1 resting reduce-only LIMIT, 50% qty, POST_ONLY.
    t1_price_str = executor_sizing.round_price_to_precision(order_row.t1_price, pair["quote_precision"])
    t1_resp = await client.place_order(
        symbol=symbol, qty=half_qty_str, price=t1_price_str, side=exit_side, trade_side="CLOSE",
        order_type="LIMIT", position_id=order_row.position_id, reduce_only=True, effect="POST_ONLY",
    )
    order_row.t1_exchange_order_id = t1_resp["data"]["orderId"]
    order_row.t1_status = "NEW"

    # (c) T3 resting reduce-only LIMIT, remaining 50% qty, POST_ONLY.
    remaining_qty_str = executor_sizing.round_qty_to_precision(order_row.qty - float(half_qty_str), pair["base_precision"])
    t3_price_str = executor_sizing.round_price_to_precision(order_row.t3_price, pair["quote_precision"])
    t3_resp = await client.place_order(
        symbol=symbol, qty=remaining_qty_str, price=t3_price_str, side=exit_side, trade_side="CLOSE",
        order_type="LIMIT", position_id=order_row.position_id, reduce_only=True, effect="POST_ONLY",
    )
    order_row.t3_exchange_order_id = t3_resp["data"]["orderId"]
    order_row.t3_status = "NEW"

    order_row.management_state = "ENTRY_FILLED_ORDERS_PLACED"
    executor_accounts.write_audit(
        db, "ORDER_PLACED",
        f"entry filled at {order_row.entry_fill_price} (positionId={order_row.position_id}) -- "
        f"stop {sl_str}, T1 {t1_price_str} ({half_qty_str}), T3 {t3_price_str} ({remaining_qty_str}) all placed",
        account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system",
        detail={"sl": sl_resp, "t1": t1_resp, "t3": t3_resp})


def _r_multiple(price: float, entry: float, stop: float) -> float:
    """Signed R-multiple of `price` relative to entry, in units of the
    original stop distance -- the same convention audit_engine.py/
    CampaignLog use."""
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    return (price - entry) / risk if entry >= stop else (entry - price) / risk


async def _current_live_price(symbol: str) -> Optional[float]:
    """Same live-price source decision_engine.py's callers already use
    for "has price crossed a level" (market_data's own 5m candle feed) --
    deliberately NOT Bitunix's own tick data, so the T2-touch check agrees
    with the same feed the whole gate/TradePlan system is anchored to."""
    candles = await market_data.fetch_live_5m(symbol, limit=2)
    if not candles:
        return None
    return float(candles[-1]["close"])


async def poll_open_position(db: Session, account: ExecutorAccount, trade_plan_row: TradePlan, order_row: ExecutorOrder) -> None:
    """The per-tick watcher for one real managed trade. Dispatches on
    management_state; never re-decides direction/size/targets, only
    watches order/position status and applies the fixed, audited rule."""
    if order_row.management_state in _TERMINAL_STATES:
        return

    if order_row.management_state == "PENDING_ENTRY":
        await check_entry_fill_and_place_exits(db, account, trade_plan_row, order_row)
        return

    client = _client_for(account)
    symbol = order_row.symbol.replace("/", "")

    # T1 fill check -- identical for both tiers (T1 management doesn't
    # differ by tier; only the T2 breakeven move does).
    if order_row.t1_status != "FILLED":
        t1_resp = await client.get_order_detail(order_id=order_row.t1_exchange_order_id)
        if t1_resp.get("code") not in (0, None):
            raise ValueError(f"get_order_detail(T1) returned a real API error: code={t1_resp.get('code')} msg={t1_resp.get('msg')!r}")
        t1_data = t1_resp.get("data") or {}
        if t1_data.get("status") == "FILLED":
            order_row.t1_status = "FILLED"
            # get_order_detail has no avg-fill-price field at all (verified
            # against bitunix.com/api-docs, 2026-09-07 -- just `price`, the
            # order's own requested price, and `tradeQty`) -- but this is a
            # resting LIMIT order, so a real fill happens AT that resting
            # price by construction. Using order_row.t1_price directly,
            # not a fabricated field name.
            order_row.t1_fill_price = order_row.t1_price
            order_row.t1_fill_time = datetime.datetime.utcnow()
            order_row.t1_leg_r = 0.5 * _r_multiple(order_row.t1_fill_price, order_row.entry_fill_price, order_row.stop_price)
            order_row.management_state = "T1_FILLED_BE_PENDING" if order_row.tier == "PREMIUM" else "T1_FILLED"
            executor_accounts.write_audit(
                db, "T1_PARTIAL_DETECTED", f"T1 filled at {order_row.t1_fill_price}, locked {order_row.t1_leg_r:+.4f}R",
                account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system", detail=t1_resp)

    # PREMIUM-only: mechanical breakeven move at the real T2 touch.
    # Observation-only reval inputs logged alongside -- never read back
    # to change this unconditional action (see module header).
    if order_row.tier == "PREMIUM" and order_row.management_state == "T1_FILLED_BE_PENDING":
        live_price = await _current_live_price(order_row.symbol)
        touched_t2 = live_price is not None and (
            live_price >= order_row.t2_price if order_row.direction == _LONG else live_price <= order_row.t2_price
        )
        if touched_t2:
            order_row.t2_touch_time = datetime.datetime.utcnow()
            try:
                import fuel_gate, micro_regime
                candles_5m = await market_data.fetch_live_5m(order_row.symbol, limit=300)
                candles_15m = await market_data.fetch_live_15m(order_row.symbol, limit=300)
                fuel = fuel_gate.evaluate_fuel_gate(candles_5m, order_row.entry_price, order_row.direction)
                order_row.t2_reval_fuel_verdict = fuel.get("verdict")
                order_row.t2_reval_micro_regime = micro_regime.classify_regime(candles_15m).get("regime")
            except Exception as e:
                print(f"|| EXECUTOR LIVE || T2 reval observation failed (non-critical, does not affect the mechanical BE move): {e}")

            pair = await _get_pair_precision(client, symbol)
            be_str = executor_sizing.round_price_to_precision(order_row.entry_fill_price, pair["quote_precision"])
            resp = await client.modify_position_tp_sl_order(
                symbol=symbol, position_id=order_row.position_id, sl_price=be_str, sl_stop_type="LAST_PRICE")
            order_row.sl_exchange_order_id = resp["data"]["orderId"]
            order_row.sl_price_current = float(be_str)
            order_row.sl_moved_to_be_at = datetime.datetime.utcnow()
            order_row.management_state = "BE_MOVED"
            executor_accounts.write_audit(
                db, "SL_MOVED_TO_BREAKEVEN", f"PREMIUM T2 touched at {live_price} -- stop moved to breakeven {be_str} (mechanical, unconditional)",
                account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system", detail=resp)

    # Closure detection: position gone = the trade is over. Classify the
    # cause from T1/T3 fill evidence, not from inspecting the stop's own
    # order state directly (see module header on why -- unverified how
    # Bitunix's TP/SL system reports itself via get_order_detail; the
    # live pre-flight test resolves this before this is trusted for real).
    pos_resp = await client.get_position(symbol)
    still_open = _find_open_position(pos_resp, symbol, order_row.direction)
    if still_open is not None:
        return   # still open, nothing more to do this tick

    if order_row.t1_status != "FILLED":
        order_row.realized_pnl_r = -1.0
        order_row.close_reason = "STOP_BEFORE_T1"
    else:
        t3_resp = await client.get_order_detail(order_id=order_row.t3_exchange_order_id)
        t3_data = t3_resp.get("data") or {}
        if t3_data.get("status") == "FILLED":
            order_row.t3_status = "FILLED"
            order_row.t3_fill_price = order_row.t3_price   # resting LIMIT -- fills at that price, see T1's comment above
            order_row.t3_fill_time = datetime.datetime.utcnow()
            order_row.runner_r = 0.5 * _r_multiple(order_row.t3_fill_price, order_row.entry_fill_price, order_row.stop_price)
            order_row.close_reason = "T3"
        else:
            # Position closed, T1 filled, T3 did not -- the runner's stop
            # (original or breakeven) took it. Exact fill price of the
            # stop's own trigger order is not independently confirmed
            # here (see header) -- approximated at the last known stop
            # level, flagged as such in the audit row rather than assumed
            # silently correct.
            exit_price = order_row.sl_price_current if order_row.sl_price_current is not None else order_row.stop_price
            order_row.runner_r = 0.5 * _r_multiple(exit_price, order_row.entry_fill_price, order_row.stop_price)
            order_row.close_reason = "RUNNER_STOP"
            executor_accounts.write_audit(
                db, "ERROR",
                f"runner exit price approximated at last known stop level {exit_price} -- "
                f"not independently confirmed against the stop's own fill (see executor_live_engine.py header)",
                account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system")
        order_row.realized_pnl_r = (order_row.t1_leg_r or 0.0) + (order_row.runner_r or 0.0)

    order_row.management_state = f"CLOSED_{order_row.close_reason}"
    order_row.closed_at = datetime.datetime.utcnow()
    executor_accounts.write_audit(
        db, "POSITION_CLOSED", f"trade closed: {order_row.close_reason}, realized {order_row.realized_pnl_r:+.4f}R",
        account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system")

    # Real R -> real dollars, so record_trade_result()'s compounding/
    # derisk math (which is USD-denominated) gets a real number, not a
    # manual stopgap entry -- this is the one automatic caller of that
    # function, for real Executor-managed trades specifically (out-of-
    # band trades still require the existing manual admin call).
    pnl_usd = order_row.realized_pnl_r * (order_row.risk_dollars_used or 0.0)
    executor_accounts.record_trade_result(db, account, pnl_usd, trade_plan_id=trade_plan_row.id, recorded_by="system")


async def run_executor_position_loop() -> None:
    """Background task (registered in main.py's lifespan(), 30s cadence,
    matching the Gravity/Ledger loop pattern) -- watches every real
    ExecutorOrder row in a non-terminal management_state and calls
    poll_open_position() for each, independently try/excepted per row so
    one account/trade's failure never blocks another's."""
    import asyncio
    import traceback
    from database import SessionLocal

    print(">>> EXECUTOR LIVE ENGINE: Initializing position-watch loop...")
    while True:
        db = SessionLocal()
        try:
            open_orders = db.query(ExecutorOrder).filter(
                ExecutorOrder.management_state.isnot(None),
                ~ExecutorOrder.management_state.in_(_TERMINAL_STATES),
                ExecutorOrder.entry_exchange_order_id.isnot(None),
            ).all()
            for order_row in open_orders:
                try:
                    account = db.query(ExecutorAccount).filter_by(id=order_row.account_id).first()
                    trade_plan_row = db.query(TradePlan).filter_by(id=order_row.trade_plan_id).first()
                    if account is None or trade_plan_row is None:
                        continue
                    await poll_open_position(db, account, trade_plan_row, order_row)
                    db.commit()
                except Exception as e:
                    db.rollback()
                    print(f"|| EXECUTOR LIVE || order {order_row.id} (account {order_row.account_id}) poll failed: {e}")
                    traceback.print_exc()
        except Exception as e:
            print(f"|| EXECUTOR LIVE ENGINE ERROR || {e}")
            traceback.print_exc()
        finally:
            db.close()
        await asyncio.sleep(30)
