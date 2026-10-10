# alt_matrix_engine.py
# ==============================================================================
# ALT MATRIX ENGINE -- the two background loops that drive the whole
# system. Per the approved plan, built DRY_RUN-first: LIVE code paths
# exist and are exercised against a faked client in tests, but nothing
# here should be registered to run against a real account until Andy's
# Step 6 live-API checks are done.
#
# TWO LOOPS, DISJOINT OWNERSHIP OF AltMatrixOrder.management_state --
# this is deliberate, not an oversight, and avoids a real race that an
# earlier draft of this file almost introduced (a second, independently-
# scheduled loop passively polling get_position() to detect a stop-
# closure could read a FILLED/TRAILING row, then lose a race against
# THIS loop actively closing that same row for a trail-exit reason,
# clobbering the correct exit record with a wrong one):
#   - run_alt_matrix_signal_loop() (every 4H close) owns PENDING_ENTRY
#     creation (new orders) AND the entire FILLED/TRAILING -> CLOSED_*
#     lifecycle (via alt_matrix_management.advance(), called once per
#     confirmed 4H bar -- the SAME cadence the backtest itself measured
#     the stop/trail checks against; polling a live ticker more often
#     than that would be running behavior nothing actually backtested).
#   - run_alt_matrix_watch_loop() (30-60s) owns ONLY the PENDING_ENTRY /
#     ENTRY_FILLED_UNPROTECTED -> FILLED transition (confirming/
#     protecting a freshly placed real entry quickly, not waiting up to
#     4h for the next signal tick) -- it NEVER touches a FILLED/TRAILING
#     row. The two loops' write-sets never overlap.
#
# DRY_RUN vs LIVE: for a DRY_RUN account, every state transition is
# simulated directly (no Bitunix client call at all) using the exact
# same alt_matrix_signals/alt_matrix_management pure-function outputs a
# LIVE account would act on -- same decision, different execution path,
# matching traveler_plan_engine.py's own DRY_RUN-vs-executor_live_
# e1_engine.py split.
# ==============================================================================

import asyncio
import datetime
import json
import traceback
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

import alt_matrix_clock
import alt_matrix_executor
import alt_matrix_management
import alt_matrix_market
import alt_matrix_portfolio
import alt_matrix_signals
import executor_accounts
from database import SessionLocal, AltMatrixPlan, AltMatrixOrder, AltMatrixConfig, ExecutorAccount, AltMatrixTransition

UTC = datetime.timezone.utc
SYMBOLS = ["SOL/USDT", "ETH/USDT"]
_ROW_TIMEOUT_SECONDS = 25           # matches executor_live_e1_engine.py's own per-row hang-defense bound
_DEFAULT_CATCHUP_WINDOW_SECONDS = 900
_WATCH_POLL_SECONDS = 45
_STOP_ATR_MULTIPLE = 1.5            # the backtest's own stop formula: entry - 1.5*ATR14 (walkforward_htf_sol.py:56)

_OPEN_MGMT_STATES = ("FILLED", "TRAILING")
_PENDING_MGMT_STATES = ("PENDING_ENTRY", "ENTRY_FILLED_UNPROTECTED")


# ------------------------------------------------------------------ shared helpers

def _enabled_accounts_for_symbol(db: Session, symbol: str) -> List[ExecutorAccount]:
    """Every ExecutorAccount with an AltMatrixConfig row enabling this
    symbol, deterministic order (by account id). Andy's Option A ruling
    means there's realistically one such account in production, but this
    stays correct if more than one is ever configured."""
    is_sol = symbol.startswith("SOL")
    configs = db.query(AltMatrixConfig).filter(
        AltMatrixConfig.sol_enabled.is_(True) if is_sol else AltMatrixConfig.eth_enabled.is_(True)
    ).all()
    accounts = []
    for cfg in configs:
        account = db.query(ExecutorAccount).filter_by(id=cfg.account_id).first()
        if account is not None:
            accounts.append(account)
    accounts.sort(key=lambda a: a.id)
    return accounts


def _any_credentialed_account(db: Session) -> Optional[ExecutorAccount]:
    """Picks any one Alt-Matrix-configured account to use for the shared,
    account-agnostic funding-rate read -- every Bitunix call in this
    codebase is signed (no unauthenticated path exists in BitunixClient
    at all), so this is still needed even though funding rate itself
    doesn't depend on which account asks. See alt_matrix_market.
    fetch_funding_rate()'s own docstring."""
    for cfg in db.query(AltMatrixConfig).all():
        account = db.query(ExecutorAccount).filter_by(id=cfg.account_id).first()
        if account is not None and account.api_key_encrypted:
            return account
    return None


def _record_transition(
    db: Session, order_row: AltMatrixOrder, from_state: Optional[str], to_state: str,
    price: Optional[float] = None, realized_pnl_r: Optional[float] = None,
) -> None:
    """DRY_RUN's own transition logger -- same AltMatrixTransition table
    alt_matrix_executor.py's LIVE path writes to, kept as a small local
    helper here rather than importing that module's private _log_
    transition() (module-private by convention, same as every other
    leading-underscore helper in this codebase)."""
    db.add(AltMatrixTransition(
        alt_matrix_plan_id=order_row.alt_matrix_plan_id, alt_matrix_order_id=order_row.id,
        from_state=from_state, to_state=to_state, price=price, realized_pnl_r=realized_pnl_r,
    ))


def _close_any_plan_fully_resolved(db: Session, plan_id: int) -> None:
    """If every AltMatrixOrder tied to this plan has reached a terminal
    state, marks the plan DONE -- purely a display/audit convenience,
    never read by any decision logic. Flushes first: SessionLocal is
    autoflush=False project-wide, so the caller's own just-mutated
    management_state (set moments ago on an already-persistent row, not
    a fresh db.add()) would not otherwise be visible to this query."""
    db.flush()
    open_count = db.query(AltMatrixOrder).filter(
        AltMatrixOrder.alt_matrix_plan_id == plan_id,
        AltMatrixOrder.management_state.in_(list(_PENDING_MGMT_STATES) + list(_OPEN_MGMT_STATES)),
    ).count()
    if open_count == 0:
        plan = db.query(AltMatrixPlan).filter_by(id=plan_id).first()
        if plan is not None and plan.status == "ARMED":
            plan.status = "DONE"


# ------------------------------------------------------------------ D3 management (signal-loop-owned)

async def _advance_one_order_management(
    db: Session, account: ExecutorAccount, order_row: AltMatrixOrder,
    candles_4h_confirmed: List[Dict[str, Any]], now_utc: datetime.datetime,
) -> None:
    order_dict = {
        "entry_price": order_row.entry_fill_price,
        "stop_price": order_row.sl_price_current,
        "r_distance": order_row.r_distance,
        "entry_fill_time": order_row.entry_fill_time,
        "be_amended": order_row.be_amended,
    }
    result = alt_matrix_management.advance(order_dict, candles_4h_confirmed, now_utc)
    if result is None:
        return

    if result["action"] == "AMEND_TO_BE":
        if account.mode == "LIVE":
            await alt_matrix_executor.amend_to_breakeven(db, account, order_row, result["be_price"])
        else:
            order_row.be_amended = True
            order_row.be_amended_at = result["at_time"]
            order_row.be_price = result["be_price"]
            order_row.sl_price_current = result["be_price"]
            _record_transition(db, order_row, "FILLED", "TRAILING", price=result["be_price"])
            order_row.management_state = "TRAILING"
        return

    if result["action"] == "EXIT":
        exit_reason = result["exit_reason"]
        if account.mode == "LIVE":
            await alt_matrix_executor.market_close(db, account, order_row, exit_reason)
        else:
            from_state = order_row.management_state
            exit_price = result["exit_price"]
            order_row.exit_reason = exit_reason
            order_row.exit_price = exit_price
            order_row.exit_time = result["exit_time"]
            order_row.closed_at = result["exit_time"]
            order_row.management_state = f"CLOSED_{exit_reason}"
            if order_row.r_distance:
                order_row.realized_pnl_r = (exit_price - order_row.entry_fill_price) / order_row.r_distance
            _record_transition(db, order_row, from_state, order_row.management_state, price=exit_price, realized_pnl_r=order_row.realized_pnl_r)
        _close_any_plan_fully_resolved(db, order_row.alt_matrix_plan_id)


async def _process_symbol_management(db: Session, symbol: str, candles_4h_confirmed: List[Dict[str, Any]], now_utc: datetime.datetime) -> None:
    open_orders = db.query(AltMatrixOrder).filter(
        AltMatrixOrder.symbol == symbol,
        AltMatrixOrder.management_state.in_(_OPEN_MGMT_STATES),
    ).all()
    for order_row in open_orders:
        try:
            account = db.query(ExecutorAccount).filter_by(id=order_row.account_id).first()
            if account is None:
                continue
            await asyncio.wait_for(
                _advance_one_order_management(db, account, order_row, candles_4h_confirmed, now_utc),
                timeout=_ROW_TIMEOUT_SECONDS,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"|| ALT MATRIX MGMT || order {order_row.id} ({symbol}) advance failed: {e}")
            traceback.print_exc()


# ------------------------------------------------------------------ D1 signal + entry fan-out (signal-loop-owned)

async def _try_enter_for_account(
    db: Session, account: ExecutorAccount, plan: AltMatrixPlan, symbol: str,
    reference_price: float, stop_price: float, atr14: float,
) -> str:
    """Returns the resulting AltMatrixOrder.decision value (always
    creates a row, even on a refusal -- same audit-visible convention
    executor_plan_builder.py uses for Traveler's own order table, never
    silently skipping a row just because the answer was no)."""
    tradeable, reason = executor_accounts.is_account_tradeable(db, account)
    r_distance = reference_price - stop_price

    order_row = AltMatrixOrder(
        alt_matrix_plan_id=plan.id, account_id=account.id, mode=account.mode,
        symbol=symbol, direction="LONG",
        atr14_at_signal=atr14, r_distance=r_distance, reference_price=reference_price,
        sl_price_initial=stop_price, sl_price_current=stop_price,
        management_state="PENDING_ENTRY",
    )

    if not tradeable:
        decision = "SKIPPED_KILL_SWITCH" if "kill switch" in reason else "SKIPPED_ACCOUNT_INACTIVE"
        order_row.decision = decision
        order_row.decision_reason = reason
        order_row.management_state = "NOT_ENTERED"
        db.add(order_row)
        db.flush()
        return decision

    already_open = db.query(AltMatrixOrder).filter(
        AltMatrixOrder.account_id == account.id, AltMatrixOrder.symbol == symbol,
        AltMatrixOrder.management_state.in_(list(_PENDING_MGMT_STATES) + list(_OPEN_MGMT_STATES)),
    ).first()
    if already_open is not None:
        order_row.decision = "SKIPPED_IN_TRADE"
        order_row.decision_reason = f"account {account.id} already has an open {symbol} order (id={already_open.id})"
        order_row.management_state = "NOT_ENTERED"
        db.add(order_row)
        db.flush()
        return "SKIPPED_IN_TRADE"

    try:
        exch = await alt_matrix_portfolio.exchange_account_state(account)
    except Exception as e:
        order_row.decision = "REJECTED"
        order_row.decision_reason = f"exchange_state_query_failed: {e}"
        order_row.management_state = "NOT_ENTERED"
        db.add(order_row)
        db.flush()
        return "REJECTED"

    sizing = await alt_matrix_executor.size_entry(db, account, symbol.replace("/", ""), exch["equity"], reference_price, stop_price)
    if sizing["decision"] != "WOULD_PLACE":
        order_row.decision = "REJECTED"
        order_row.decision_reason = sizing["decision_reason"]
        order_row.management_state = "NOT_ENTERED"
        db.add(order_row)
        db.flush()
        return "REJECTED"

    order_row.qty = sizing["qty"]
    order_row.risk_dollars_used = sizing["risk_dollars_used"]
    order_row.leverage_used = sizing["leverage"]
    order_row.margin_required_usd = sizing["margin_required_usd"]
    order_row.liquidation_price_estimate = sizing["liquidation_price_estimate"]
    order_row.liquidation_check_passed = sizing["liquidation_check_passed"]
    order_row.liquidation_check_detail = sizing["liquidation_check_detail"]

    admission = await alt_matrix_portfolio.check_admission(
        db, account, symbol, sizing["risk_dollars_used"], sizing["margin_required_usd"],
    )
    order_row.admission_snapshot_json = json.dumps(admission["snapshot"], default=str)
    if not admission["admitted"]:
        order_row.decision = "CONCURRENCY_SKIPPED"
        order_row.decision_reason = admission["reason"]
        order_row.management_state = "NOT_ENTERED"
        db.add(order_row)
        db.flush()
        return "CONCURRENCY_SKIPPED"

    order_row.decision = "WOULD_PLACE"
    db.add(order_row)
    db.flush()

    if account.mode == "LIVE":
        await alt_matrix_executor.place_entry_and_protect(db, account, order_row)
    else:
        order_row.entry_fill_price = reference_price
        order_row.entry_fill_time = datetime.datetime.utcnow()
        order_row.management_state = "FILLED"
        _record_transition(db, order_row, "PENDING_ENTRY", "FILLED", price=reference_price)
    return "WOULD_PLACE"


async def _process_symbol_signal(db: Session, symbol: str, eval_instant: datetime.datetime) -> None:
    expected_open = alt_matrix_clock.expected_closed_bar_open(eval_instant)
    candles_4h = await alt_matrix_market.fetch_confirmed_4h(symbol)
    candles_1d = await alt_matrix_market.fetch_confirmed_daily(symbol)
    if not candles_4h or not candles_1d:
        return   # retry next cycle -- never guess at a missing fetch

    last_bar_open = datetime.datetime.utcfromtimestamp(int(candles_4h[-1]["time"]))
    if last_bar_open != expected_open:
        return   # stale fetch, or the exchange is running behind -- skip this cycle, never guess

    # D3 management runs BEFORE evaluating a new D1 signal, on the SAME
    # just-fetched candles, so an exit this same tick frees a concurrency
    # slot a brand-new entry below can actually use (the approved plan's
    # own "exits before entries" requirement).
    await _process_symbol_management(db, symbol, candles_4h, datetime.datetime.utcnow())

    signal_bar_time = last_bar_open + datetime.timedelta(seconds=alt_matrix_clock.BAR_SECONDS)
    existing = db.query(AltMatrixPlan).filter_by(symbol=symbol, signal_bar_time=signal_bar_time).first()
    if existing is not None:
        return   # already processed this boundary -- idempotent restart-safety

    funding_account = _any_credentialed_account(db)
    funding_rate = await alt_matrix_market.fetch_funding_rate(symbol, funding_account)

    verdict = alt_matrix_signals.evaluate_d1(candles_4h, candles_1d, funding_rate)
    if not verdict.get("cross_pass"):
        return   # routine -- no Silver Cross this bar; AltMatrixPlan's own
                 # docstring: no row at all for "no signal yet"

    if verdict["signal"]:
        status = "ARMED"
    elif not verdict["macro_pass"]:
        status = "SKIPPED_MACRO"
    else:
        status = "SKIPPED_FUNDING"

    plan = AltMatrixPlan(
        symbol=symbol, signal_bar_time=signal_bar_time, date_key=last_bar_open.strftime("%Y-%m-%d"),
        daily_close=verdict.get("daily_close"), sma200=verdict.get("sma200"),
        ema21=verdict.get("ema21"), ema21_prev=verdict.get("ema21_prev"),
        ema55=verdict.get("ema55"), ema55_prev=verdict.get("ema55_prev"),
        atr14=verdict.get("atr14"), funding_rate=verdict.get("funding_rate"),
        status=status, status_reason=verdict.get("reason"),
    )
    db.add(plan)
    db.flush()

    if status != "ARMED":
        return

    reference_price = float(candles_4h[-1]["close"])
    atr14 = verdict["atr14"]
    stop_price = reference_price - _STOP_ATR_MULTIPLE * atr14   # the backtest's own formula (walkforward_htf_sol.py:56)

    entered_any = False
    last_blocking_decision = None
    for account in _enabled_accounts_for_symbol(db, symbol):
        try:
            decision = await asyncio.wait_for(
                _try_enter_for_account(db, account, plan, symbol, reference_price, stop_price, atr14),
                timeout=_ROW_TIMEOUT_SECONDS,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"|| ALT MATRIX SIGNAL || entry attempt failed for account {account.id} ({symbol}): {e}")
            traceback.print_exc()
            continue
        if decision == "WOULD_PLACE":
            entered_any = True
        else:
            last_blocking_decision = decision

    # last_blocking_decision is last-account-wins across the fan-out loop
    # -- a deliberate simplification, not a bug: Andy's Option A ruling
    # means there's realistically ONE enabled account in production, so
    # this always reflects that account's own real reason. With more
    # than one configured, only the LAST account's blocking reason gets
    # reflected on the plan (each account's own real reason still lives
    # on its own AltMatrixOrder.decision regardless -- this is a display
    # convenience, never read by any decision logic).
    if not entered_any and last_blocking_decision in ("SKIPPED_IN_TRADE", "CONCURRENCY_SKIPPED"):
        plan.status = last_blocking_decision
        db.commit()


async def _process_one_symbol_tick(db: Session, symbol: str, eval_instant: datetime.datetime) -> None:
    try:
        await asyncio.wait_for(_process_symbol_signal(db, symbol, eval_instant), timeout=_ROW_TIMEOUT_SECONDS * 4)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"|| ALT MATRIX SIGNAL || {symbol} tick failed: {e}")
        traceback.print_exc()


# ------------------------------------------------------------------ the two loops

async def run_alt_matrix_signal_loop() -> None:
    """Background task (registered in main.py's lifespan()) -- the D1
    gate + D3 management tick, boundary-anchored to the real 4H close
    (never a fixed-duration sleep that drifts, same discipline as
    main.py's own run_monthly_lti_scheduler() and this module's own
    alt_matrix_clock.py header). On every wake (including right after a
    restart), checks whether the most recent boundary is still within
    its catch-up window before processing it -- past the window, that
    boundary is simply skipped (left as a permanent gap, matching the
    approved plan's own "record MISSED rather than guess" intent; a
    dedicated MISSED row is not written here, since nothing was ever
    fetched for that boundary to attach one to)."""
    print(">>> ALT MATRIX SIGNAL LOOP: Initializing (D1 gate + D3 management, 4H-boundary-anchored)...")
    while True:
        now = datetime.datetime.now(UTC)
        eval_instant = alt_matrix_clock.most_recent_eval_utc(now)
        if not alt_matrix_clock.is_within_catchup_window(eval_instant, now, _DEFAULT_CATCHUP_WINDOW_SECONDS):
            await asyncio.sleep(alt_matrix_clock.seconds_until_next_eval(now))
            now = datetime.datetime.now(UTC)
            eval_instant = alt_matrix_clock.most_recent_eval_utc(now)

        eval_instant_naive = eval_instant.replace(tzinfo=None)
        db = None
        try:
            db = SessionLocal()
            for symbol in SYMBOLS:
                await _process_one_symbol_tick(db, symbol, eval_instant_naive)
        except Exception as e:
            print(f"|| ALT MATRIX SIGNAL LOOP ERROR: {e}")
            traceback.print_exc()
        finally:
            if db is not None:
                db.close()

        await asyncio.sleep(alt_matrix_clock.seconds_until_next_eval(datetime.datetime.now(UTC)))


async def run_alt_matrix_watch_loop() -> None:
    """Background task (registered in main.py's lifespan()) -- confirms
    fills/stop-placement for freshly placed REAL entries only (LIVE
    accounts, PENDING_ENTRY/ENTRY_FILLED_UNPROTECTED rows). Never touches
    a FILLED/TRAILING row -- that lifecycle belongs exclusively to the
    signal loop's D3 step (see this module's own header for why the two
    loops deliberately never share write-ownership of the same rows).
    30-60s cadence, matching executor_live_e1_engine.py's own real-money
    polling philosophy -- a market entry should fill almost immediately,
    worth polling for quickly rather than waiting up to 4h."""
    print(">>> ALT MATRIX WATCH LOOP: Initializing (LIVE entry fill/stop confirmation)...")
    while True:
        db = None
        try:
            db = SessionLocal()
            pending = db.query(AltMatrixOrder).filter(
                AltMatrixOrder.mode == "LIVE",
                AltMatrixOrder.management_state.in_(_PENDING_MGMT_STATES),
            ).all()
            for order_row in pending:
                try:
                    account = db.query(ExecutorAccount).filter_by(id=order_row.account_id).first()
                    if account is None:
                        continue
                    await asyncio.wait_for(
                        alt_matrix_executor.place_entry_and_protect(db, account, order_row),
                        timeout=_ROW_TIMEOUT_SECONDS,
                    )
                    db.commit()
                except Exception as e:
                    db.rollback()
                    print(f"|| ALT MATRIX WATCH || order {order_row.id} confirm failed: {e}")
                    traceback.print_exc()
        except Exception as e:
            print(f"|| ALT MATRIX WATCH LOOP ERROR: {e}")
            traceback.print_exc()
        finally:
            if db is not None:
                db.close()

        await asyncio.sleep(_WATCH_POLL_SECONDS)
