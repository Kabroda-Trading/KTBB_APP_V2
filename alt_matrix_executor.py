# alt_matrix_executor.py
# ==============================================================================
# ALT MATRIX EXECUTOR -- real Bitunix order placement for LIVE accounts
# only. Copies (never imports) the proven patterns from executor_live_
# e1_engine.py (entry/stop sequencing, hang-safe polling discipline) and
# executor_plan_builder.py (real leverage/MMR/balance queries, the
# liquidation-safety check) -- per the BTC Iron Wall (ALT_MATRIX_D1_D2_
# D3_SPEC.md acceptance criterion 5), nothing in this module imports
# gate_traveler.py, traveler_plan_engine.py, executor_live_e1_engine.py,
# or executor_plan_builder.py. executor_sizing.py and executor_accounts.py
# ARE imported directly -- both are generic, symbol-agnostic utility
# modules with zero Traveler-specific coupling (confirmed by direct
# read during the Step 1 audit), the same two files alt_matrix_
# portfolio.py already reuses the same way.
#
# D2 ENTRY SHAPE (Andy's ruling on the Part-0 "entry order shape"
# question, see the approved plan): SEQUENTIAL placement -- market entry
# first, confirm the fill, THEN place the exchange stop -- matching
# executor_live_e1_engine.py's own already-live, already-accepted
# pattern (including its ENTRY_FILLED_UNPROTECTED state for the brief
# gap between fill and stop), not a new atomic attached-stop order shape
# unverified against the live Bitunix API.
#
# AUDIT TRAIL -- two tables, two purposes, never conflated: every actual
# state change on an AltMatrixOrder (entry fill, stop set, BE amendment,
# exit) is logged to AltMatrixTransition (satisfies the spec's own
# acceptance criterion 6 -- exact fill price/fee/realized R per
# transition). write_audit()/ExecutorAuditLog is ALSO called for
# anything a human operator needs to see in the general admin feed
# (errors above all), but deliberately NEVER with executor_order_id=
# an AltMatrixOrder's id -- that column is documented in database.py as
# `executor_orders.id`, a different id sequence (Traveler's own table)
# that can collide. The AltMatrixOrder id goes in `detail` instead.
# ==============================================================================

import asyncio
import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

import executor_accounts
import executor_sizing
import market_data
from database import ExecutorAccount, AltMatrixOrder, AltMatrixTransition

_FALLBACK_MMR_UNVERIFIED = 0.01   # same conservative fallback value executor_plan_builder.py uses
_CLOSE_CONFIRM_INTERVAL_SEC = 1.0   # duplicated from executor_live_e1_engine.py's own proven confirm loop
_CLOSE_CONFIRM_ATTEMPTS = 10


async def _current_live_price(symbol: str) -> Optional[float]:
    """Duplicated verbatim from executor_live_e1_engine.py::
    _current_live_price() (Andy ruling 2026-09-27 14:55 CT) -- Bitunix,
    not Kraken, since this approximates a REAL market-close fill against
    Bitunix, the actual execution venue."""
    candles = await market_data.fetch_bitunix_5m(symbol, target_bars=2)
    if not candles:
        return None
    return float(candles[-1]["close"])


def _find_open_position(pos_resp: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    """Duplicated from executor_live_e1_engine.py's own _find_open_position()
    -- Alt Matrix is LONG-only (side="BUY"), so no direction param needed."""
    if pos_resp.get("code") not in (0, None):
        raise ValueError(f"get_position returned a real API error: code={pos_resp.get('code')} msg={pos_resp.get('msg')!r}")
    positions = pos_resp.get("data") or []
    matches = [p for p in positions if p.get("symbol") == symbol and p.get("side") == "BUY"]
    if len(matches) > 1:
        raise ValueError(f"found {len(matches)} open LONG {symbol} positions -- ambiguous, refusing to guess")
    return matches[0] if matches else None


def _log_transition(
    db: Session, order_row: AltMatrixOrder, from_state: Optional[str], to_state: str,
    price: Optional[float] = None, fee: Optional[float] = None,
    realized_pnl_r: Optional[float] = None, detail: Optional[Dict[str, Any]] = None,
) -> None:
    import json
    db.add(AltMatrixTransition(
        alt_matrix_plan_id=order_row.alt_matrix_plan_id, alt_matrix_order_id=order_row.id,
        from_state=from_state, to_state=to_state, price=price, fee=fee,
        realized_pnl_r=realized_pnl_r,
        detail_json=json.dumps(detail, default=str) if detail else None,
    ))


def _audit(db: Session, event_type: str, message: str, account_id: int, order_row: AltMatrixOrder, detail: Optional[Dict[str, Any]] = None) -> None:
    """Thin wrapper around executor_accounts.write_audit() that always
    folds the AltMatrixOrder id into `detail` rather than the mismatched
    executor_order_id column -- see this module's header."""
    merged_detail = dict(detail or {})
    merged_detail["alt_matrix_order_id"] = order_row.id
    executor_accounts.write_audit(db, event_type, message, account_id=account_id, actor="system", detail=merged_detail)


def _client_for(account: ExecutorAccount) -> "executor_bitunix_client.BitunixClient":
    import executor_bitunix_client
    api_key, api_secret = executor_accounts.get_decrypted_credentials(account)
    if not api_key or not api_secret:
        raise ValueError(f"account {account.id} has no credentials set -- cannot place real orders")
    return executor_bitunix_client.BitunixClient(api_key, api_secret)


async def _get_pair_precision(client, symbol: str) -> Dict[str, Any]:
    """Duplicated from executor_live_e1_engine.py on purpose -- same tiny,
    stable-lookup convention that module's own docstring already states
    for its own duplication of this exact function from executor_live_
    engine.py."""
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


async def query_real_leverage(account: ExecutorAccount, symbol: str) -> Dict[str, Any]:
    """Returns {"leverage": int, "source": str}. Same never-crash-on-
    network-hiccup fallback pattern as executor_plan_builder.py's own
    _query_real_leverage_and_margin_mode() -- duplicated, not imported."""
    api_key, api_secret = executor_accounts.get_decrypted_credentials(account)
    if not api_key or not api_secret:
        return {"leverage": account.leverage_baseline, "source": "no credentials set yet -- using configured baseline, NOT verified"}
    import executor_bitunix_client
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)
    try:
        resp = await client.get_leverage_and_margin_mode(symbol)
        data = resp["data"]
        return {"leverage": int(data["leverage"]), "source": "verified against the real exchange account"}
    except Exception as e:
        return {"leverage": account.leverage_baseline, "source": f"exchange query failed ({e}) -- using configured baseline, NOT verified"}


async def query_real_mmr(account: ExecutorAccount, symbol: str, notional_value: float) -> Dict[str, Any]:
    """Returns {"mmr": float, "source": str}. Duplicated from executor_
    plan_builder.py's own _query_real_maintenance_margin_rate() for the
    same Iron Wall reason."""
    api_key, api_secret = executor_accounts.get_decrypted_credentials(account)
    if not api_key or not api_secret:
        return {"mmr": 0.0, "source": "no credentials set yet -- using unverified 0.0 mmr"}
    import executor_bitunix_client
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)
    try:
        resp = await client.get_position_tiers(symbol)
        tiers = resp["data"]
        tier = next((t for t in tiers if float(t["startValue"]) <= notional_value < float(t["endValue"])), None)
        if tier is None:
            return {"mmr": _FALLBACK_MMR_UNVERIFIED, "source": "no matching notional tier -- using conservative fallback, NOT verified"}
        return {"mmr": float(tier["maintenanceMarginRate"]), "source": "verified against the real exchange position tiers"}
    except Exception as e:
        return {"mmr": _FALLBACK_MMR_UNVERIFIED, "source": f"tiers query failed ({e}) -- using conservative fallback, NOT verified"}


async def size_entry(
    db: Session, account: ExecutorAccount, symbol: str, equity: float, entry_price: float, stop_price: float,
) -> Dict[str, Any]:
    """Sizing + the hard liquidation-vs-stop safety check, mirroring
    executor_plan_builder.py::_size_and_check_order()'s own generic
    shape -- fails closed (decision="REJECTED") on any missing policy
    field or failed liquidation check, never guesses a default band.
    Takes `db` from the caller (same convention as get_or_init_sizing_
    policy() itself) rather than opening a second, separate session --
    a second session reading/creating ExecutorSizingPolicy out-of-band
    from the caller's own open transaction could race or duplicate the
    row. Returns {"decision": "WOULD_PLACE"|"REJECTED", "decision_reason":
    Optional[str], "qty": ..., "risk_dollars_used": ..., "leverage": ...,
    "margin_required_usd": ..., "liquidation_price_estimate": ...,
    "liquidation_check_passed": ..., "liquidation_check_detail": ...}."""
    policy = executor_accounts.get_or_init_sizing_policy(db, account)

    if policy.band_step_usd is None or policy.band_risk_per_step_usd is None:
        return {"decision": "REJECTED", "decision_reason": "no banded sizing policy configured for this account -- refusing to guess a default"}

    risk_dollars = executor_sizing.banded_risk(
        equity, step_usd=policy.band_step_usd, risk_per_step_usd=policy.band_risk_per_step_usd,
        below_pct=policy.band_below_pct if policy.band_below_pct is not None else 0.10,
        max_risk_usd=policy.band_max_risk_usd,
    )
    if risk_dollars <= 0:
        return {"decision": "REJECTED", "decision_reason": "computed risk_dollars <= 0"}

    qty = executor_sizing.compute_qty(risk_dollars, entry_price, stop_price)
    leverage_state = await query_real_leverage(account, symbol)
    leverage = leverage_state["leverage"]
    notional = qty * entry_price
    mmr_state = await query_real_mmr(account, symbol, notional)
    mmr = mmr_state["mmr"]

    safe, detail, liq_price = executor_sizing.check_leverage_is_safe(entry_price, stop_price, "LONG", leverage, mmr)
    margin_required = notional / leverage if leverage else notional

    result = {
        "qty": qty, "risk_dollars_used": risk_dollars, "leverage": leverage,
        "margin_required_usd": margin_required, "liquidation_price_estimate": liq_price,
        "liquidation_check_passed": safe, "liquidation_check_detail": detail,
    }
    if not safe:
        result["decision"] = "REJECTED"
        result["decision_reason"] = detail
    else:
        result["decision"] = "WOULD_PLACE"
        result["decision_reason"] = None
    return result


async def place_entry_and_protect(db: Session, account: ExecutorAccount, order_row: AltMatrixOrder) -> None:
    """Places the real MARKET entry order, confirms the fill, then places
    the exchange stop -- sequential, per Andy's ruling (see this module's
    own header). order_row must already have qty/risk_dollars_used/
    r_distance/sl_price_initial set by the caller's own size_entry()
    call. Mirrors executor_live_e1_engine.py's check_traveler_entry_fill_
    and_protect()'s own stop-placement sequence exactly, minus the T1 leg
    (Alt Matrix has none -- trailing/breakeven management only)."""
    client = _client_for(account)
    symbol = order_row.symbol.replace("/", "")
    pair = await _get_pair_precision(client, symbol)
    qty_str = executor_sizing.round_qty_to_precision(order_row.qty, pair["base_precision"])

    entry_resp = await client.place_order(symbol=symbol, qty=qty_str, side="BUY", trade_side="OPEN", order_type="MARKET")
    entry_order_id = (entry_resp.get("data") or {}).get("orderId")
    if entry_resp.get("code") not in (0, None) or not entry_order_id:
        order_row.decision_reason = f"entry order placement failed: code={entry_resp.get('code')} msg={entry_resp.get('msg')!r}"
        _log_transition(db, order_row, order_row.management_state, "CLOSED_ERROR", detail=entry_resp)
        order_row.management_state = "CLOSED_ERROR"
        _audit(db, "ERROR", order_row.decision_reason, account.id, order_row, detail=entry_resp)
        return
    order_row.entry_exchange_order_id = entry_order_id

    detail_resp = await client.get_order_detail(order_id=entry_order_id)
    if detail_resp.get("code") not in (0, None):
        raise ValueError(f"get_order_detail returned a real API error: code={detail_resp.get('code')} msg={detail_resp.get('msg')!r}")
    data = detail_resp.get("data") or {}
    order_row.entry_status = data.get("status")
    if order_row.entry_status != "FILLED":
        # A market order not yet showing FILLED on the very next read is
        # unusual but not impossible -- leave PENDING_ENTRY, the watch
        # loop re-checks next tick (same "never assume, recheck" rule as
        # Traveler's own resting-limit poll).
        return

    pos_resp = await client.get_position(symbol)
    if pos_resp.get("code") not in (0, None):
        raise ValueError(f"get_position returned a real API error: code={pos_resp.get('code')} msg={pos_resp.get('msg')!r}")
    positions = [p for p in (pos_resp.get("data") or []) if p.get("symbol") == symbol and p.get("side") == "BUY"]
    if len(positions) != 1:
        _log_transition(db, order_row, order_row.management_state, "CLOSED_ERROR", detail=pos_resp)
        order_row.management_state = "CLOSED_ERROR"
        _audit(db, "ERROR", f"entry order {entry_order_id} confirmed FILLED but found {len(positions)} matching open positions, expected 1 -- CHECK THE EXCHANGE DIRECTLY", account.id, order_row, detail=pos_resp)
        return
    position = positions[0]
    order_row.position_id = position["positionId"]
    order_row.entry_fill_price = float(position["avgOpenPrice"])
    order_row.entry_fill_time = datetime.datetime.utcnow()

    sl_str = executor_sizing.round_price_to_precision(order_row.sl_price_initial, pair["quote_precision"])
    try:
        sl_resp = await client.set_position_tpsl(symbol=symbol, position_id=order_row.position_id, sl_price=sl_str, sl_stop_type="LAST_PRICE")
        sl_order_id = (sl_resp.get("data") or {}).get("orderId")
        if sl_resp.get("code") not in (0, None) or not sl_order_id:
            _log_transition(db, order_row, "PENDING_ENTRY", "ENTRY_FILLED_UNPROTECTED", price=order_row.entry_fill_price, detail=sl_resp)
            order_row.management_state = "ENTRY_FILLED_UNPROTECTED"
            _audit(
                db, "ERROR", f"REAL OPEN ALT MATRIX POSITION (positionId={order_row.position_id}) with NO STOP PLACED -- "
                f"code={sl_resp.get('code')} msg={sl_resp.get('msg')!r}. Manual intervention required.",
                account.id, order_row, detail=sl_resp)
            return
        order_row.sl_exchange_order_id = sl_order_id
        order_row.sl_price_current = float(sl_str)
        order_row.sl_set_at = datetime.datetime.utcnow()
    except Exception as e:
        _log_transition(db, order_row, "PENDING_ENTRY", "ENTRY_FILLED_UNPROTECTED", price=order_row.entry_fill_price)
        order_row.management_state = "ENTRY_FILLED_UNPROTECTED"
        _audit(db, "ERROR", f"REAL OPEN ALT MATRIX POSITION (positionId={order_row.position_id}) with NO STOP PLACED -- exception: {e}. Manual intervention required.", account.id, order_row)
        return

    _log_transition(db, order_row, "PENDING_ENTRY", "FILLED", price=order_row.entry_fill_price)
    order_row.management_state = "FILLED"
    _audit(db, "ORDER_PLACED", f"Alt Matrix entry filled at {order_row.entry_fill_price} (positionId={order_row.position_id}), stop {sl_str} placed", account.id, order_row)


async def amend_to_breakeven(db: Session, account: ExecutorAccount, order_row: AltMatrixOrder, be_price: float) -> bool:
    """Amends the live exchange stop to breakeven. Bitunix's modify_
    position_tp_sl_order() is NOT a partial update -- any field left out
    gets CLEARED (confirmed live, 2026-09-05, commit 4405571) -- Alt
    Matrix never sets a take-profit at all, so only sl_price is ever sent
    here, which is safe (there is no tp_price to accidentally clear).
    Confirms the amendment actually took via get_pending_tp_sl_order()
    before updating the row, per that same incident's own lesson: a
    successful REST response does not guarantee the operation succeeded.
    On confirmed success, moves management_state FILLED -> TRAILING (the
    bar-by-bar trail-exit stage, matching alt_matrix_management.py's own
    EMA21-once-amended/EMA55-until-then branch). Returns True on
    confirmed success."""
    client = _client_for(account)
    symbol = order_row.symbol.replace("/", "")
    pair = await _get_pair_precision(client, symbol)
    be_str = executor_sizing.round_price_to_precision(be_price, pair["quote_precision"])

    try:
        resp = await client.modify_position_tp_sl_order(symbol=symbol, position_id=order_row.position_id, sl_price=be_str, sl_stop_type="LAST_PRICE")
    except Exception as e:
        _audit(db, "ERROR", f"breakeven amendment call failed for order {order_row.id}: {e}", account.id, order_row)
        return False
    if resp.get("code") not in (0, None):
        _audit(db, "ERROR", f"breakeven amendment returned a real API error for order {order_row.id}: code={resp.get('code')} msg={resp.get('msg')!r}", account.id, order_row, detail=resp)
        return False

    confirm_resp = await client.get_pending_tp_sl_order(position_id=order_row.position_id)
    confirmed_sl = None
    if confirm_resp.get("code") in (0, None):
        for row in (confirm_resp.get("data") or []):
            sl = row.get("slPrice")
            if sl not in (None, "", "0"):
                try:
                    confirmed_sl = float(sl)
                except (TypeError, ValueError):
                    confirmed_sl = None
                break
    if confirmed_sl is None or abs(confirmed_sl - float(be_str)) > 1e-6:
        _audit(db, "ERROR", f"breakeven amendment for order {order_row.id} returned success but get_pending_tp_sl_order shows sl={confirmed_sl}, expected {be_str} -- CHECK THE EXCHANGE DIRECTLY", account.id, order_row, detail=confirm_resp)
        return False

    order_row.be_amended = True
    order_row.be_amended_at = datetime.datetime.utcnow()
    order_row.be_price = float(be_str)
    order_row.sl_price_current = float(be_str)
    _log_transition(db, order_row, "FILLED", "TRAILING", price=float(be_str))
    order_row.management_state = "TRAILING"
    _audit(db, "STOP_AMENDED", f"Alt Matrix order {order_row.id} stop amended to breakeven ({be_str})", account.id, order_row)
    return True


async def market_close(db: Session, account: ExecutorAccount, order_row: AltMatrixOrder, exit_reason: str) -> None:
    """Closes the position via close_position() (flash_close_position) --
    the SAME endpoint executor_live_e1_engine.py's own contingency exits
    (C5/BBWP/TIME) already use, proven live. That module's own header is
    explicit that this endpoint's response carries NO reliable fill
    price (confirmed against executor_mechanism_test.py's own
    flash_close_remainder(), which doesn't try to extract one either) --
    an earlier draft of this function assumed place_order(CLOSE)+
    get_order_detail() would yield an exact fill price, which is an
    UNVERIFIED claim with no real precedent anywhere in this codebase;
    corrected to match the one actually-proven pattern instead. Exit
    price is APPROXIMATED at the last known live Bitunix price, same
    Andy-accepted tradeoff ("it does not matter about the slippage
    nuances... we are managing the trade", 2026-09-27) -- confirmed via
    a bounded re-poll of get_position(), not assumed on the call
    succeeding."""
    client = _client_for(account)
    symbol = order_row.symbol.replace("/", "")

    try:
        await client.close_position(order_row.position_id)
    except Exception as e:
        _audit(db, "ERROR", f"close_position call failed for order {order_row.id} ({exit_reason}): {e}", account.id, order_row)
        return   # retry next tick -- never assume closed on a call failure

    still_open = None
    pos_resp: Dict[str, Any] = {}
    for _ in range(_CLOSE_CONFIRM_ATTEMPTS):
        await asyncio.sleep(_CLOSE_CONFIRM_INTERVAL_SEC)
        pos_resp = await client.get_position(symbol)
        still_open = _find_open_position(pos_resp, symbol)
        if still_open is None:
            break

    if still_open is not None:
        _audit(
            db, "ERROR",
            f"close_position reported for order {order_row.id} ({exit_reason}) but a matching open position "
            f"still exists after {_CLOSE_CONFIRM_ATTEMPTS} checks -- CHECK THE EXCHANGE DIRECTLY, will retry next tick",
            account.id, order_row, detail=pos_resp)
        return

    exit_price = await _current_live_price(symbol)
    if exit_price is None:
        exit_price = order_row.entry_fill_price   # last resort -- never leave exit_price None, matching Traveler's own convention

    from_state = order_row.management_state
    order_row.exit_reason = exit_reason
    order_row.exit_price = exit_price
    order_row.exit_time = datetime.datetime.utcnow()
    order_row.closed_at = order_row.exit_time
    order_row.management_state = f"CLOSED_{exit_reason}"
    if order_row.r_distance:
        order_row.realized_pnl_r = (exit_price - order_row.entry_fill_price) / order_row.r_distance   # LONG-only, no sign flip needed
    _log_transition(db, order_row, from_state, order_row.management_state, price=exit_price, realized_pnl_r=order_row.realized_pnl_r,
                     detail={"approximated": True})
    _audit(db, "POSITION_CLOSED", f"Alt Matrix order {order_row.id} closed: {exit_reason} at {exit_price} (approximated at last known live price -- close_position() carries no reliable fill price)", account.id, order_row)
