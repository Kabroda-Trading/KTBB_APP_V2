# cme_engine.py
# ==============================================================================
# CME ENGINE -- the two background loops. Per the approved plan, built
# DRY_RUN-capable from day one (same convention as every other subsystem
# here), even though Andy's own explicit go-ahead has the first real test
# (the 1-Micro Smoke Test) skip straight to the live Apex account -- DRY_RUN
# still matters for the test suite and any future second CME account.
#
# TWO LOOPS, DELIBERATELY DIFFERENT SPLIT THAN ALT MATRIX'S -- flagged
# explicitly, not a silent deviation: Alt Matrix's signal loop owns BOTH
# plan creation AND the full D3 exit lifecycle, because Alt Matrix's D3 was
# backtested on the SAME 4H cadence as its own D1 signal. CME's D1 (the
# 10:00 ET lock) is a once-daily event, but its D2/D3 (cross confirmation,
# retest fill, breakeven ratchet, exit detection) were backtested on
# 5-MINUTE bars -- a once-daily signal-loop wake is the wrong cadence for
# that. So here:
#   - run_cme_session_loop() (once daily, boundary-anchored to the 10:00 ET
#     lock) owns ONLY CMEPlan creation (D1) plus the initial per-account
#     CMEOrder row creation (sizing + admission, still at lock time -- the
#     box is already known then).
#   - run_cme_watch_loop() (30s) owns EVERYTHING ELSE: cross detection,
#     resting-bracket placement, retest-fill/NO_RETEST-timeout, breakeven
#     ratchet, exit detection (stop/target/16:00 flush).
# The two loops' write-sets still never overlap (session loop writes only
# CMEPlan + brand-new CMEOrder rows; watch loop only ever updates an
# existing CMEOrder) -- same race-avoidance invariant Alt Matrix's own
# header describes, just drawn along a different boundary.
#
# KNOWN GAP: cme_market.py's fetch functions raise NotImplementedError
# until Tradovate's real Market Data API is confirmed live (see that
# module's own header) -- this loop catches that per-tick, logs it, and
# keeps running harmlessly rather than crashing. Nothing here will advance
# past CMEPlan creation until that gap is closed.
# ==============================================================================

import asyncio
import datetime
import traceback
from typing import List, Optional

from sqlalchemy.orm import Session

import cme_clock
import cme_executor
import cme_market
import cme_risk
import cme_signals
import executor_accounts
from database import SessionLocal, CMEPlan, CMEOrder, CMEConfig, ExecutorAccount

UTC = datetime.timezone.utc
SYMBOLS = ["MNQ", "MES"]
_ROW_TIMEOUT_SECONDS = 25
_DEFAULT_CATCHUP_WINDOW_SECONDS = 900
_WATCH_POLL_SECONDS = 30

_PENDING_RETEST_STATES = ("WAITING_RETEST",)
_OPEN_POSITION_STATES = ("FILLED", "RATCHETED", "ENTRY_FILLED_UNPROTECTED")


def _enabled_accounts_for_symbol(db: Session, symbol: str) -> List[ExecutorAccount]:
    is_mnq = symbol.upper() == "MNQ"
    configs = db.query(CMEConfig).filter(
        CMEConfig.mnq_enabled.is_(True) if is_mnq else CMEConfig.mes_enabled.is_(True)
    ).all()
    accounts = []
    for cfg in configs:
        account = db.query(ExecutorAccount).filter_by(id=cfg.account_id).first()
        if account is not None:
            accounts.append((account, cfg))
    accounts.sort(key=lambda pair: pair[0].id)
    return accounts


async def _query_equity(account: ExecutorAccount) -> float:
    """Real equity for the trailing-DD check. Falls back to the account's
    own high_water_mark_usd (last known value) on a query failure -- never
    crashes the whole tick over one failed balance call, but also never
    silently treats a failure as "equity is fine" (the caller's own
    trailing-DD check still runs against whatever value this returns)."""
    try:
        client = cme_executor._client_for(account)
        balance = await client.get_balance()
        return balance["total_equity"]
    except Exception as e:
        print(f"|| CME ENGINE || equity query failed for account {account.id}: {e} -- using last known high-water mark")
        return account.high_water_mark_usd or 0.0


# ------------------------------------------------------------------ session loop (D1, once daily)

async def _process_symbol_lock(db: Session, symbol: str, date_key: str) -> None:
    existing = db.query(CMEPlan).filter_by(symbol=symbol, date_key=date_key).first()
    if existing is not None:
        return   # idempotent -- restart-safe, same convention as TravelerPlan/AltMatrixPlan

    accounts = _enabled_accounts_for_symbol(db, symbol)
    try:
        client = cme_executor._client_for(accounts[0][0]) if accounts else None
        bars_1m = await cme_market.fetch_calibration_1m(client, symbol) if client else []
    except Exception as e:
        print(f"|| CME SESSION LOOP || {symbol} calibration fetch failed: {e}")
        return   # no row at all for today -- safe, matches "no lock row for a closed/unfetchable day"

    # daily ATR14 would come from the same market-data layer -- left as a
    # TODO alongside cme_market.py's own NotImplementedError gap; using a
    # conservative always-fails-reachability sentinel until that's wired
    # in means this never silently arms a trade on an unverified ATR.
    daily_atr14 = None
    plan_data = cme_signals.calculate_session_lock(symbol, bars_1m, daily_atr14)
    if plan_data is None:
        return

    plan = CMEPlan(symbol=symbol, date_key=date_key, lock_time=datetime.datetime.utcnow(), **{
        k: v for k, v in plan_data.items() if k != "symbol"
    })
    db.add(plan)
    db.flush()

    try:
        import notify
        import cme_notify
        email = cme_notify.notification_for_cme_plan({**plan_data, "id": plan.id, "date_key": date_key})
        if email:
            notify.send_admin_email(*email)
    except Exception as e:
        print(f"|| CME SESSION LOOP || lock notification failed for {symbol}: {e}")

    if plan.status != "ARMED":
        return

    for account, cfg in accounts:
        for direction, trig, stop in (("LONG", plan.bo, plan.stop_long), ("SHORT", plan.bd, plan.stop_short)):
            equity = await _query_equity(account)
            admission = cme_risk.can_open_new_position(
                db, account, symbol, date_key, equity,
                cfg.daily_risk_budget_usd, cfg.contracts_cap, plan.box,
            )
            order = CMEOrder(
                cme_plan_id=plan.id, account_id=account.id, mode=account.mode,
                symbol=symbol, direction=direction, box_at_entry=plan.box,
                contracts=admission.get("contracts") or 0,
                point_multiplier=cme_signals.POINT_MULTIPLIER.get(symbol, 2.0),
                risk_dollars_used=admission.get("risk_budget_usd") or 0.0,
                decision="WOULD_PLACE" if admission["admitted"] else "REJECTED",
                decision_reason=admission.get("reason"),
                management_state="PENDING_ENTRY",
            )
            db.add(order)
    db.flush()


async def _process_one_symbol_lock_tick(db: Session, symbol: str, date_key: str) -> None:
    try:
        await asyncio.wait_for(_process_symbol_lock(db, symbol, date_key), timeout=_ROW_TIMEOUT_SECONDS * 4)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"|| CME SESSION LOOP || {symbol} lock tick failed: {e}")
        traceback.print_exc()


async def run_cme_session_loop() -> None:
    """Background task (registered in main.py's lifespan()) -- the D1 lock
    tick, boundary-anchored to the real 10:00 ET lock (DST-aware, see
    cme_clock.py's own header for the Part-0 bug this fixes)."""
    print(">>> CME SESSION LOOP: Initializing (D1 lock, 10:00 ET-boundary-anchored)...")
    while True:
        now = datetime.datetime.now(UTC)
        lock_instant = cme_clock.most_recent_lock_utc(now)
        if not cme_clock.is_within_catchup_window(lock_instant, now, _DEFAULT_CATCHUP_WINDOW_SECONDS):
            await asyncio.sleep(cme_clock.seconds_until_next_lock(now))
            now = datetime.datetime.now(UTC)
            lock_instant = cme_clock.most_recent_lock_utc(now)

        date_key = lock_instant.astimezone(cme_clock.NY_TZ).strftime("%Y-%m-%d")
        db = None
        try:
            db = SessionLocal()
            for symbol in SYMBOLS:
                await _process_one_symbol_lock_tick(db, symbol, date_key)
        except Exception as e:
            print(f"|| CME SESSION LOOP ERROR: {e}")
            traceback.print_exc()
        finally:
            if db is not None:
                db.close()

        await asyncio.sleep(cme_clock.seconds_until_next_lock(datetime.datetime.now(UTC)))


# ------------------------------------------------------------------ watch loop (D2/D3, 30s)

async def _advance_pending_order(db: Session, order_row: CMEOrder) -> None:
    account = db.query(ExecutorAccount).filter_by(id=order_row.account_id).first()
    if account is None or order_row.decision != "WOULD_PLACE":
        return

    plan = db.query(CMEPlan).filter_by(id=order_row.cme_plan_id).first()
    if plan is None:
        return

    if order_row.management_state == "PENDING_ENTRY":
        client = cme_executor._client_for(account)
        bars_5m = await cme_market.fetch_confirmed_5m(client, order_row.symbol, plan.lock_time)
        cross = cme_signals.check_cross(bars_5m, plan.bo, plan.bd)
        if cross is None:
            return   # still waiting within the cross window -- caller enforces the window via plan age, not here
        if cross["side"] != order_row.direction:
            # The OTHER side crossed first (the fan-out creates one
            # PENDING_ENTRY row per direction, an OCO-style pair) -- this
            # row can never be the first to fire this plan. Found
            # 2026-11-04: without this check, a single confirmed cross
            # placed a REAL bracket on BOTH rows, opening an unbacktested
            # straddle. Stand this side down permanently instead.
            cme_executor.stand_down_opposite_side(db, order_row, winning_side=cross["side"])
            return
        order_row.cross_confirmed_at = datetime.datetime.utcnow()
        await cme_executor.place_resting_bracket(db, account, plan, order_row)
        return

    if order_row.management_state == "WAITING_RETEST":
        window_elapsed = (
            datetime.datetime.utcnow() - order_row.cross_confirmed_at
        ).total_seconds() > cme_signals.RETEST_WINDOW_BARS * 300 if order_row.cross_confirmed_at else False
        if window_elapsed:
            await cme_executor.cancel_no_retest(db, account, order_row)
            return
        await cme_executor.check_retest_fill_and_confirm(db, account, order_row)
        return


async def _advance_open_position(db: Session, order_row: CMEOrder) -> None:
    account = db.query(ExecutorAccount).filter_by(id=order_row.account_id).first()
    if account is None:
        return
    plan = db.query(CMEPlan).filter_by(id=order_row.cme_plan_id).first()
    if plan is None:
        return

    now_utc = datetime.datetime.now(UTC)
    if now_utc >= cme_clock.today_flush_utc(now_utc):
        await cme_executor.market_close(db, account, order_row, exit_reason="TIME_FLUSH")
        return

    client = cme_executor._client_for(account)
    bars_5m = await cme_market.fetch_confirmed_5m(client, order_row.symbol, order_row.entry_fill_time or plan.lock_time)

    be_trigger = plan.be_trigger_long if order_row.direction == "LONG" else plan.be_trigger_short
    target = plan.target_long if order_row.direction == "LONG" else plan.target_short
    if not order_row.be_amended and bars_5m:
        last_price = bars_5m[-1]["close"]
        if cme_signals.check_breakeven_ratchet(last_price, order_row.direction, be_trigger):
            be_price = plan.be_stop_long if order_row.direction == "LONG" else plan.be_stop_short
            if order_row.sl_exchange_order_id:
                await cme_executor.amend_to_breakeven(db, account, order_row, int(order_row.sl_exchange_order_id), be_price)
            return

    stop = order_row.sl_price_current
    exit_result = cme_signals.check_exit(
        bars_5m, order_row.direction, stop, target,
        be_trigger=be_trigger if not order_row.be_amended else None,
        be_stop_price=(plan.be_stop_long if order_row.direction == "LONG" else plan.be_stop_short) if not order_row.be_amended else None,
    )
    if exit_result is not None:
        await cme_executor.market_close(db, account, order_row, exit_reason=exit_result["exit_reason"].replace("CLOSED_", ""))


async def _advance_one_row(db: Session, order_row: CMEOrder) -> None:
    try:
        if order_row.management_state in _PENDING_RETEST_STATES or order_row.management_state == "PENDING_ENTRY":
            await asyncio.wait_for(_advance_pending_order(db, order_row), timeout=_ROW_TIMEOUT_SECONDS)
        elif order_row.management_state in _OPEN_POSITION_STATES:
            await asyncio.wait_for(_advance_open_position(db, order_row), timeout=_ROW_TIMEOUT_SECONDS)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"|| CME WATCH || order {order_row.id} advance failed: {e}")
        traceback.print_exc()


async def run_cme_watch_loop() -> None:
    """Background task (registered in main.py's lifespan()) -- owns cross
    detection, resting-bracket placement, retest/NO_RETEST, breakeven
    ratchet, and exit detection. See module header for why this loop (not
    the session loop) owns the full D2/D3 lifecycle here, unlike Alt
    Matrix's own split."""
    print(">>> CME WATCH LOOP: Initializing (D2/D3 advancement, 30s cadence)...")
    while True:
        db = None
        try:
            db = SessionLocal()
            rows = db.query(CMEOrder).filter(
                CMEOrder.mode == "LIVE",
                CMEOrder.management_state.in_(("PENDING_ENTRY",) + _PENDING_RETEST_STATES + _OPEN_POSITION_STATES),
            ).all()
            for order_row in rows:
                await _advance_one_row(db, order_row)
        except Exception as e:
            print(f"|| CME WATCH LOOP ERROR: {e}")
            traceback.print_exc()
        finally:
            if db is not None:
                db.close()

        await asyncio.sleep(_WATCH_POLL_SECONDS)
