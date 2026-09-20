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
# THE MANAGEMENT RULE, v2 (2026-09-15 resolution -- CC_QUESTION_T2_
# BREAKEVEN.md, Kabroda AI Brain repo, DeepSeek's ruling "delete outright"):
#   Entry: resting LIMIT at the trigger, 50% now / 50% runner.
#   T1 (1.0x box, v2's box multiple -- decision_engine.py's own header):
#       take 50%, stop stays at the original level.
#   T3 (1.618x box) or the runner's stop: the runner exits.
#   THE STOP NEVER MOVES, for anyone, at any point (CC_PACKAGE.md §1,
#   locked 2026-09-11: "stop never moves... NO BE stop (measured harmful)").
#
# v1's PREMIUM-only mechanical breakeven-move at T2 (CLEAN_REPORT.md §2,
# verified against a real 31-trade premium corpus: 13 touched T2, 10 rode
# on to T3 at zero cost, 3 stopped after T2 for +1.50R saved) was already
# confirmed dead code before this resolution -- order_row.tier can never
# be "PREMIUM" under the v2 gate (decision_engine.py retired the tier
# split entirely, 2026-09-11) -- and is now deleted outright per Andy/
# DeepSeek's explicit call, not left dormant: v2's own locked spec already
# states "no BE move, ever" as a measured result, and the traveler
# candidate's E1 exit is a full exit at T1 with no partial/runner leg for
# a BE move to apply to at all -- neither lineage has a use for this
# mechanism, so there's nothing to keep dormant "in case." The T1_FILLED_
# BE_PENDING/BE_MOVED management_state values and the t2_reval_fuel_
# verdict/t2_reval_micro_regime observation-only columns went with it (per
# DeepSeek's ruling: they were only ever written inside this same dead
# branch). A future T2-reval-observation feature, if wanted, is a fresh
# design decision through the study chain, not a resurrection of this.
#
# MAKER-ONLY IS MECHANICALLY ENFORCED HERE, not just placement discipline
# (CLEAN_REPORT.md §4: taker fees turn +42R into -0.5R). Every LIMIT order
# this module places -- entry, T1, T3 -- passes effect="POST_ONLY"
# (verified live against bitunix.com/api-docs, 2026-09-07: IOC/FOK/GTC/
# POST_ONLY are the real enum values). No caller in this codebase used
# this before; every resting limit placed so far (including the proven
# T1 ladder test) defaulted to GTC.
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
# ENTRY_FILLED_UNPROTECTED is deliberately terminal (loop-side) even
# though the real position is still open -- "no AI improvisation" means
# a partial-protection failure gets a loud alert and stops here, not a
# repeated automatic retry every 30s. See check_entry_fill_and_place_
# exits()'s own comment on this.
_TERMINAL_STATES = ("CLOSED_STOP_BEFORE_T1", "CLOSED_RUNNER_STOP", "CLOSED_T3", "CLOSED_ERROR", "ENTRY_FILLED_UNPROTECTED", "CLOSED_EXPIRED")
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
    order_id = (resp.get("data") or {}).get("orderId")
    if resp.get("code") not in (0, None) or not order_id:
        # 2026-09-07 END_TO_END_AUDIT.md fix: a real, expected outcome --
        # POST_ONLY's whole mechanism is "reject rather than cross the
        # spread" (verified live against bitunix.com/api-docs). Before
        # this fix, resp["data"]["orderId"] on a rejection response
        # (data often null) raised an unhandled TypeError, caught only by
        # process_fill()'s outer try/except as a console print -- the
        # ExecutorOrder row silently stuck at management_state's DB
        # default (PENDING_ENTRY) with no exchange_order_id, invisible to
        # the background loop's own query filter, no audit row, no alert.
        # No real money is at risk here specifically (nothing filled),
        # but Andy would never have found out a live entry silently
        # failed to place at all.
        order_row.management_state = "CLOSED_ERROR"
        executor_accounts.write_audit(
            db, "ERROR",
            f"entry order placement FAILED (code={resp.get('code')} msg={resp.get('msg')!r}) -- "
            f"no real order exists on the exchange for this trade_plan",
            account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system", detail=resp)
        import notify
        notify.send_admin_email(
            f"KABRODA EXECUTOR ERROR -- entry placement failed ({order_row.symbol})",
            f"Real entry order placement failed for trade_plan_id={trade_plan_row.id}, account={account.id}.\n"
            f"Exchange response: code={resp.get('code')} msg={resp.get('msg')!r}\n\n"
            f"No real order exists for this trade -- likely a POST_ONLY rejection (price moved past "
            f"the entry level before the order reached the exchange). No automatic retry is attempted; "
            f"nothing to undo since nothing filled.",
        )
        return
    order_row.entry_exchange_order_id = order_id
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


def _plan_has_expired(trade_plan_row: TradePlan) -> bool:
    """P0-1 (CC_WORK_ORDER_LIVE_DAY_2026-09-19.md): two independent real
    signals the site already computes, OR'd together -- either is
    sufficient reason a real resting entry order should never keep
    sitting on the exchange:
      (a) the plan's own status already reached DONE (e.g. an early
          WIDE_STOP_FIRST invalidation -- trade_plan_engine.py's FILLED
          branch, check_wide_stop_or_t1() -- or CampaignLog's own eventual
          resolution via mirror_campaign_outcome()).
      (b) the trading session itself has closed (_compute_session_
          expires_at) -- a real, deterministic backstop, independent of
          (a).

    A real discrepancy found auditing this work order, flagged here and
    in AGENT_LOG.md rather than silently reconciled: the work order's own
    prose says a FILLED plan "goes DONE site-side" at session close.
    Traced directly in trade_plan_engine.py -- that is NOT what happens.
    The FILLED branch never checks session_expires_at at all; (a) above
    is bounded only by CampaignLog's shadow simulation, which can run
    until the NEXT session's 8:30 AM ET open (~17.5h after session close,
    ledger_closing_engine.py's own _next_session_open_utc()) before
    CLOSED_AT_EXPIRY resolves it. Relying on (a) alone would let a real
    resting order sit for most of a day after the session it belonged to
    already closed -- the exact outcome Andy explicitly does not want
    ("a random limit order floating around out there is a big no-no").
    (b) is the real, session-bound cutoff this fix actually needs; (a) is
    still checked too since it is a real, often-earlier signal."""
    if trade_plan_row.status == "DONE":
        return True
    from kabroda_mas_flow import _compute_session_expires_at
    session_expires_at = _compute_session_expires_at(trade_plan_row.session_id, trade_plan_row.date_key)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    return now_utc >= session_expires_at


async def _cancel_expired_entry_order(
    db: Session, account: ExecutorAccount, client: "executor_bitunix_client.BitunixClient",
    symbol: str, trade_plan_row: TradePlan, order_row: ExecutorOrder,
) -> None:
    """P0-1 (CC_WORK_ORDER_LIVE_DAY_2026-09-19.md): cancels a real resting
    entry LIMIT order whose parent plan has already expired (see
    _plan_has_expired() above) -- the entry never filled, and the setup
    it was created for is over. Race-safe: the caller already just
    confirmed via get_order_detail() that this order was NOT FILLED as of
    that check, but a real fill could still land in the moments between
    that check and this cancel call landing on the exchange -- so
    cancel_orders' own successList/failureList is checked (never trust a
    bare top-level "ok", same discipline as executor_mechanism_test.py's
    own cancel calls, its docstring's own explicit warning), and a FRESH
    get_order_detail() confirms the actual final state before this
    function commits to CLOSED_EXPIRED. A real fill found here is handed
    back to the normal fill path -- management_state is left untouched,
    so the very next tick's check_entry_fill_and_place_exits() call picks
    it up and protects the position exactly as it always would have. A
    filled, unprotected real position is far more dangerous than a stray
    resting order, so this never discards a fill just to force a clean
    cancel."""
    try:
        cancel_resp = await client.cancel_orders(symbol, [order_row.entry_exchange_order_id])
    except Exception as e:
        executor_accounts.write_audit(
            db, "ERROR", f"cancel_orders call failed for expired entry order {order_row.id}: {e}",
            account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system")
        return   # try again next tick -- never assume cancelled on a call failure

    detail_resp = await client.get_order_detail(order_id=order_row.entry_exchange_order_id)
    if detail_resp.get("code") not in (0, None):
        executor_accounts.write_audit(
            db, "ERROR",
            f"get_order_detail returned a real API error confirming the cancel for order {order_row.id}: "
            f"code={detail_resp.get('code')} msg={detail_resp.get('msg')!r} -- CHECK THE EXCHANGE DIRECTLY",
            account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system", detail=detail_resp)
        return
    real_status = (detail_resp.get("data") or {}).get("status")

    if real_status == "FILLED":
        executor_accounts.write_audit(
            db, "ERROR",
            f"cancel-on-expiry raced with a real fill on order {order_row.id} -- cancel not applied, "
            f"handing off to the normal fill-confirmation path instead",
            account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system", detail=detail_resp)
        return

    success_ids = {e.get("orderId") for e in (cancel_resp.get("data") or {}).get("successList") or []}
    if order_row.entry_exchange_order_id not in success_ids and real_status != "CANCELED":
        executor_accounts.write_audit(
            db, "ERROR",
            f"cancel_orders did not report orderId={order_row.entry_exchange_order_id} in successList for "
            f"expired order {order_row.id}, and get_order_detail shows status={real_status!r} -- "
            f"CHECK THE EXCHANGE DIRECTLY, will retry next tick",
            account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system", detail=cancel_resp)
        return

    order_row.management_state = "CLOSED_EXPIRED"
    order_row.entry_status = real_status
    order_row.close_reason = "EXPIRED"
    order_row.closed_at = datetime.datetime.utcnow()
    executor_accounts.write_audit(
        db, "ORDER_CANCELLED_ON_EXPIRY",
        f"real resting entry order {order_row.entry_exchange_order_id} cancelled -- the parent plan "
        f"(trade_plan_id={trade_plan_row.id}) expired before the entry ever filled",
        account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system",
        detail={"cancel_response": cancel_resp, "order_detail": detail_resp})


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
        # P0-1 (CC_WORK_ORDER_LIVE_DAY_2026-09-19.md): still resting -- but
        # if the parent plan has already expired, a real order must never
        # just keep sitting on the exchange waiting for a retest that no
        # longer matters to the site. See _plan_has_expired()'s own header
        # for the real trigger conditions and the discrepancy found
        # auditing the work order's original framing.
        if _plan_has_expired(trade_plan_row):
            await _cancel_expired_entry_order(db, account, client, symbol, trade_plan_row, order_row)
        return   # still resting (or just cancelled above) -- re-checked next tick

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

    # 2026-09-07 END_TO_END_AUDIT.md fix: a REAL position now exists (real
    # money, unprotected until all three of the below succeed). Before
    # this fix, any one of these three calls returning an error response
    # (order_id missing/null -- the same POST_ONLY-rejection class fixed
    # in place_entry_order() above, but here AFTER a real fill) raised an
    # unhandled exception straight into process_fill()'s outer try/except
    # -- a console print, nothing else. management_state never advanced
    # past PENDING_ENTRY even though a real, open position existed, so
    # the next tick would try this whole function again from scratch
    # (re-reading get_order_detail/get_position, harmless) rather than
    # ever surfacing that some real position sits there with only
    # PARTIAL or NO protection. Each leg is now attempted independently
    # (one failing must never prevent the others from at least trying to
    # protect the position) and any failure is loud: a distinct
    # management_state that the background loop stops touching (no
    # further automatic action on a state this failure-prone), a full
    # audit row naming exactly what succeeded and what didn't, and an
    # immediate email -- this is exactly the situation "no AI
    # improvisation" means Andy decides the recovery, not the bot.
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
            symbol=symbol, qty=half_qty_str, price=t1_price_str, side=exit_side, trade_side="CLOSE",
            order_type="LIMIT", position_id=order_row.position_id, reduce_only=True, effect="POST_ONLY",
        )
        t1_order_id = (t1_resp.get("data") or {}).get("orderId")
        if t1_resp.get("code") not in (0, None) or not t1_order_id:
            failures.append(f"T1 at {t1_price_str} qty={half_qty_str}: FAILED (code={t1_resp.get('code')} msg={t1_resp.get('msg')!r})")
        else:
            order_row.t1_exchange_order_id = t1_order_id
            order_row.t1_status = "NEW"
    except Exception as e:
        failures.append(f"T1 at {t1_price_str} qty={half_qty_str}: FAILED (exception: {e})")

    remaining_qty_str = executor_sizing.round_qty_to_precision(order_row.qty - float(half_qty_str), pair["base_precision"])
    t3_price_str = executor_sizing.round_price_to_precision(order_row.t3_price, pair["quote_precision"])
    try:
        t3_resp = await client.place_order(
            symbol=symbol, qty=remaining_qty_str, price=t3_price_str, side=exit_side, trade_side="CLOSE",
            order_type="LIMIT", position_id=order_row.position_id, reduce_only=True, effect="POST_ONLY",
        )
        t3_order_id = (t3_resp.get("data") or {}).get("orderId")
        if t3_resp.get("code") not in (0, None) or not t3_order_id:
            failures.append(f"T3 at {t3_price_str} qty={remaining_qty_str}: FAILED (code={t3_resp.get('code')} msg={t3_resp.get('msg')!r})")
        else:
            order_row.t3_exchange_order_id = t3_order_id
            order_row.t3_status = "NEW"
    except Exception as e:
        failures.append(f"T3 at {t3_price_str} qty={remaining_qty_str}: FAILED (exception: {e})")

    if failures:
        order_row.management_state = "ENTRY_FILLED_UNPROTECTED"
        detail_msg = (
            f"REAL OPEN POSITION (positionId={order_row.position_id}) with INCOMPLETE protection -- "
            f"{'; '.join(failures)}. Manual intervention required, no automatic retry."
        )
        executor_accounts.write_audit(
            db, "ERROR", detail_msg,
            account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system")
        import notify
        notify.send_admin_email(
            f"KABRODA EXECUTOR ALERT -- unprotected open position ({order_row.symbol})",
            f"trade_plan_id={trade_plan_row.id}, account={account.id}, positionId={order_row.position_id}\n\n"
            f"{detail_msg}\n\nCHECK THE EXCHANGE DIRECTLY NOW and manually place whatever's missing "
            f"or close the position -- this bot will not act on it further.",
        )
        return

    order_row.management_state = "ENTRY_FILLED_ORDERS_PLACED"
    executor_accounts.write_audit(
        db, "ORDER_PLACED",
        f"entry filled at {order_row.entry_fill_price} (positionId={order_row.position_id}) -- "
        f"stop {sl_str}, T1 {t1_price_str} ({half_qty_str}), T3 {t3_price_str} ({remaining_qty_str}) all placed",
        account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system")


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
            order_row.management_state = "T1_FILLED"
            executor_accounts.write_audit(
                db, "T1_PARTIAL_DETECTED", f"T1 filled at {order_row.t1_fill_price}, locked {order_row.t1_leg_r:+.4f}R",
                account_id=account.id, trade_plan_id=trade_plan_row.id, executor_order_id=order_row.id, actor="system", detail=t1_resp)

    # PREMIUM-only mechanical breakeven-move-at-T2 block (T1_FILLED_BE_
    # PENDING/BE_MOVED states, the t2_reval_fuel_verdict/t2_reval_micro_
    # regime observation logging) DELETED 2026-09-15 -- see the module
    # header's "THE MANAGEMENT RULE, v2" note. Andy/DeepSeek's explicit
    # "delete outright" ruling (CC_QUESTION_T2_BREAKEVEN.md, Kabroda AI
    # Brain repo): the stop never moves for anyone now, full stop, so
    # there's no dormant-but-real mechanism left to keep. Full text
    # preserved in git history.

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
                # P3 (2026-09-20): defensive exclusion -- this module's own
                # management code (check_entry_fill_and_place_exits() etc.)
                # is hard-coded to MGMT_SPLIT's shape (half-qty T1, t3_price
                # required) and must NEVER run for an E1/traveler-linked
                # order (executor_live_e1_engine.py owns those). Mirrors
                # dry_run_split_engine.py's own identical exclusion for the
                # DRY_RUN side of this same split.
                ExecutorOrder.traveler_plan_id.is_(None),
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
