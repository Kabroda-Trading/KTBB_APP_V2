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

from sqlalchemy import or_
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
# 2026-11-10 (CME_AND_TRAVELER_LIVE_AUDIT_2026_11_10.md Bug 2) -- Python's
# asyncio.sleep() may wake a few milliseconds EARLY, so a boundary-anchored
# sleep to the exact 10:00 ET lock can land at 09:59:59.999 and have
# most_recent_lock_utc() resolve to YESTERDAY's lock instead of today's. A
# small overshoot buffer guarantees the loop wakes strictly AFTER the
# boundary; the top-of-loop catchup-window check remains the backstop for
# genuinely-late wakes (cloud pause/restart). Keeping it 2s (well inside the
# 900s catchup window) so the overshoot is never mistaken for a missed day.
_LOCK_BOUNDARY_BUFFER_SECONDS = 2.0

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


async def _query_equity(account: ExecutorAccount) -> Optional[float]:
    """Real equity for the trailing-DD check. Returns None on a query
    failure -- never raises (one account's broker hiccup must never roll
    back an already-created CMEPlan or other accounts' orders for the same
    symbol), but also never fabricates a number.

    2026-11-11 (real risk finding, external audit): this used to fall back
    to account.high_water_mark_usd on failure. cme_risk.trailing_drawdown_
    usd() computes max(0, hwm - current_equity) -- substituting hwm AS the
    current equity makes that subtraction hwm - hwm = 0 every time, so a
    broker outage was silently indistinguishable from "account sitting
    exactly at its all-time peak, zero drawdown" and would PASS the
    trailing-DD admission check on a day the real equity (unknown, because
    the query failed) could already be in breach of MFFU's rule. The
    caller must treat None as "equity unknown, stand down" -- never
    substitute a value that happens to read as zero drawdown."""
    try:
        client = cme_executor._client_for(account)
        balance = await client.get_balance()
        return balance["total_equity"]
    except Exception as e:
        print(f"|| CME ENGINE || equity query failed for account {account.id}: {e} -- EQUITY_QUERY_FAILED, standing this account down rather than guessing")
        return None


# ------------------------------------------------------------------ session loop (D1, once daily)

async def _process_symbol_lock(db: Session, symbol: str, date_key: str) -> None:
    existing = db.query(CMEPlan).filter_by(symbol=symbol, date_key=date_key).first()
    if existing is not None:
        return   # idempotent -- restart-safe, same convention as TravelerPlan/AltMatrixPlan

    accounts = _enabled_accounts_for_symbol(db, symbol)
    if not accounts:
        return   # nobody has this symbol enabled -- skip the (paid, per-call) market-data fetch entirely

    try:
        bars_1m = await cme_market.fetch_calibration_1m(symbol, date_key)
    except Exception as e:
        # 2026-11-06 -- found live, flagged by DeepSeek, verified directly:
        # returning with NO row here left the desk showing an ambiguous
        # "DATA PENDING / HOLIDAY" for a day that was never actually
        # checked, indistinguishable from "nothing has happened yet
        # today." MISSED_WINDOW is a real, distinct status from
        # STAND_DOWN_UNREACHABLE (cme_signals.py's own, already-correct
        # "checked and refused on purpose" status) -- this one means "the
        # market was never successfully checked at all." Same idempotent-
        # row discipline the happy path already uses one line above.
        # 2026-11-08 (Antigravity ruling AGENT_LOG Session 4 Item 4a): a
        # Databento `data_start_after_available_end` 422 means the market
        # was genuinely CLOSED for this window (a holiday, or a weekend
        # edge that slipped past cme_clock's guard) -- surface that as a
        # clean human-readable reason instead of dumping the raw JSON
        # exception dict into the row and desk. Not conflated with a
        # generic transient fetch failure (which keeps the raw error).
        err_text = str(e)
        if "data_start_after_available_end" in err_text:
            reason = "Market closed (weekend/holiday) -- no bars available for this window"
        else:
            reason = f"calibration fetch failed: {e}"
        print(f"|| CME SESSION LOOP || {symbol} calibration fetch failed: {e}")
        db.add(CMEPlan(
            symbol=symbol, date_key=date_key, lock_time=datetime.datetime.utcnow(),
            status="MISSED_WINDOW", status_reason=reason,
        ))
        db.flush()
        return

    # 2026-11-05 -- found live, same day, flagged by DeepSeek and verified
    # directly against this exact line before fixing: this was hardcoded
    # to None (a stale TODO from before cme_market.py's Databento rebuild),
    # which forces the reachability safety sentinel (999.0 > 0.55) on
    # EVERY lock -- a real trading day silently lost, not cosmetic. Now a
    # real fetch; on failure or <14 real daily bars it still returns None,
    # so the SAME safe stand-down behavior holds -- never a fabricated
    # fallback constant.
    try:
        daily_atr14 = await cme_market.fetch_daily_atr14(symbol, date_key)
    except Exception as e:
        print(f"|| CME SESSION LOOP || {symbol} daily ATR14 fetch failed: {e}")
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
            if equity is None:
                # Real risk finding, 2026-11-11: never call can_open_new_position()
                # with a fabricated equity value just because the broker query
                # failed -- that would let trailing_drawdown_usd() read as zero
                # drawdown and admit a new position on a day the real equity
                # (unknown) could already be in breach. Fail this account closed.
                order = CMEOrder(
                    cme_plan_id=plan.id, account_id=account.id, mode=account.mode,
                    symbol=symbol, direction=direction, box_at_entry=plan.box,
                    contracts=0, point_multiplier=cme_signals.POINT_MULTIPLIER.get(symbol, 2.0),
                    risk_dollars_used=0.0,
                    decision="REJECTED",
                    decision_reason="EQUITY_QUERY_FAILED -- broker balance query failed, cannot verify trailing drawdown",
                    management_state="PENDING_ENTRY",
                )
                db.add(order)
                continue
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
    except asyncio.TimeoutError:
        # 2026-11-10 (CME_AND_TRAVELER_LIVE_AUDIT_2026_11_10.md Bug 1) --
        # the prior blanket except rolled back the transaction and returned,
        # leaving ZERO rows for the day -- not even the fallback
        # MISSED_WINDOW record -- so the desk showed yesterday's stale error
        # as if it were today's. A timeout is a real, distinct "we did not
        # successfully check this market" outcome and MUST be persisted as a
        # MISSED_WINDOW row (same status/idempotency convention as
        # _process_symbol_lock's own fetch-failure path), never silently
        # rolled back into a void. Committed on its own so a timeout on one
        # symbol can never orphan the other's successful lock.
        db.rollback()
        existing = db.query(CMEPlan).filter_by(symbol=symbol, date_key=date_key).first()
        if existing is None:
            db.add(CMEPlan(
                symbol=symbol, date_key=date_key, lock_time=datetime.datetime.utcnow(),
                status="MISSED_WINDOW",
                status_reason=f"lock tick timed out after {_ROW_TIMEOUT_SECONDS * 4}s",
            ))
            db.commit()
            print(f"|| CME SESSION LOOP || {symbol} lock tick timed out -- persisted MISSED_WINDOW row for {date_key}")
        else:
            print(f"|| CME SESSION LOOP || {symbol} lock tick timed out but a row already exists for {date_key}")
    except Exception as e:
        db.rollback()
        print(f"|| CME SESSION LOOP || {symbol} lock tick failed: {e}")
        traceback.print_exc()


def _persist_missed_window_rows(lock_instant: datetime.datetime) -> None:
    """2026-11-06 -- real structural fragility, flagged by DeepSeek,
    verified directly: a process restart/deploy landing more than
    _DEFAULT_CATCHUP_WINDOW_SECONDS after a day's lock instant used to just
    get silently skipped -- that date_key never got a CMEPlan row for ANY
    symbol, indistinguishable from "D1 hasn't run yet." Persists an
    explicit MISSED_WINDOW row for every tracked symbol (same status/
    idempotency convention as _process_symbol_lock's own
    fetch-failure path) so a missed day is a real, visible record instead
    of a silent gap. Pulled out of run_cme_session_loop() as its own
    function specifically so it's unit-testable without driving the whole
    async loop -- same reasoning _process_symbol_lock() itself already
    follows. Own try/except/commit/rollback -- a failure here must never
    crash the loop or block it from sleeping until the next real lock."""
    date_key = lock_instant.astimezone(cme_clock.NY_TZ).strftime("%Y-%m-%d")
    db = None
    try:
        db = SessionLocal()
        for symbol in SYMBOLS:
            if db.query(CMEPlan).filter_by(symbol=symbol, date_key=date_key).first() is None:
                db.add(CMEPlan(
                    symbol=symbol, date_key=date_key, lock_time=lock_instant.replace(tzinfo=None),
                    status="MISSED_WINDOW", status_reason="catchup window elapsed before this process could attempt the lock",
                ))
        db.commit()
    except Exception as e:
        if db is not None:
            db.rollback()
        print(f"|| CME SESSION LOOP || failed to persist MISSED_WINDOW rows for {date_key}: {e}")
    finally:
        if db is not None:
            db.close()


async def run_cme_session_loop() -> None:
    """Background task (registered in main.py's lifespan()) -- the D1 lock
    tick, boundary-anchored to the real 10:00 ET lock (DST-aware, see
    cme_clock.py's own header for the Part-0 bug this fixes).

    2026-11-11 (real incident -- the CME desk showed zero activity all day
    on a confirmed NON-holiday trading day): the catchup-window check and
    boundary sleep below used to sit OUTSIDE any try/except, unlike
    run_cme_watch_loop()'s own fully-wrapped body in this same file.
    main.py registers every background loop as a bare asyncio.create_task()
    with no supervisor -- nothing restarts a dead task. A single uncaught
    exception anywhere in that unwrapped span (cme_clock arithmetic, or
    anything else) would silently end this loop FOREVER, with the desk
    showing a stale "no lock yet" indistinguishable from a lock that
    genuinely hasn't happened yet. The whole iteration is now wrapped
    end-to-end, matching run_cme_watch_loop()'s already-correct pattern --
    one bad tick can never kill the loop again."""
    print(">>> CME SESSION LOOP: Initializing (D1 lock, 10:00 ET-boundary-anchored)...")
    while True:
        try:
            now = datetime.datetime.now(UTC)
            lock_instant = cme_clock.most_recent_lock_utc(now)
            if not cme_clock.is_within_catchup_window(lock_instant, now, _DEFAULT_CATCHUP_WINDOW_SECONDS):
                _persist_missed_window_rows(lock_instant)
                # Overshoot buffer: sleep slightly PAST the boundary so the wake
                # always lands strictly after the lock (see Bug 2 note on
                # _LOCK_BOUNDARY_BUFFER_SECONDS above) -- never a millisecond
                # early, which would re-resolve to yesterday's lock.
                await asyncio.sleep(cme_clock.seconds_until_next_lock(now) + _LOCK_BOUNDARY_BUFFER_SECONDS)
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

            await asyncio.sleep(cme_clock.seconds_until_next_lock(datetime.datetime.now(UTC)) + _LOCK_BOUNDARY_BUFFER_SECONDS)
        except Exception as e:
            # Last-resort catch-all for the span above that used to be
            # unwrapped (clock arithmetic, _persist_missed_window_rows, the
            # catchup sleep). Never let this bare task die silently -- log
            # loudly, back off briefly, then let the next iteration
            # re-resolve the real lock boundary fresh rather than
            # tight-looping on a persistent error.
            print(f"|| CME SESSION LOOP ERROR (outer, loop continues): {e}")
            traceback.print_exc()
            await asyncio.sleep(60)


# ------------------------------------------------------------------ watch loop (D2/D3, 30s)

async def _advance_pending_order(db: Session, order_row: CMEOrder) -> None:
    account = db.query(ExecutorAccount).filter_by(id=order_row.account_id).first()
    if account is None or order_row.decision != "WOULD_PLACE":
        return

    plan = db.query(CMEPlan).filter_by(id=order_row.cme_plan_id).first()
    if plan is None:
        return

    if order_row.management_state == "PENDING_ENTRY":
        bars_5m = await cme_market.fetch_confirmed_5m(order_row.symbol, plan.lock_time)
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

    bars_5m = await cme_market.fetch_confirmed_5m(order_row.symbol, order_row.entry_fill_time or plan.lock_time)

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

            # Fee-capture retry sweep (2026-11-xx) -- reuses this loop's
            # own cadence/per-row commit skeleton, no new background task.
            # Matters MOST here of the three subsystems, given CrossTrade's
            # confirmed fills-schema/lag risk -- this is the real safety
            # net for CME. After 48h with no fee found, a row naturally
            # falls out of this query and stays permanently NULL.
            fee_cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=48)
            fee_retry_rows = db.query(CMEOrder).filter(
                CMEOrder.mode == "LIVE",
                CMEOrder.closed_at.isnot(None),
                CMEOrder.closed_at > fee_cutoff,
                or_(CMEOrder.entry_fee_usd.is_(None), CMEOrder.exit_fee_usd.is_(None)),
            ).all()
            for order_row in fee_retry_rows:
                try:
                    account = db.query(ExecutorAccount).filter_by(id=order_row.account_id).first()
                    if account is None:
                        continue
                    client = cme_executor._client_for(account)
                    if order_row.entry_fee_usd is None and order_row.entry_exchange_order_id:
                        await asyncio.wait_for(
                            cme_executor._fetch_cme_entry_fee(client, account, order_row),
                            timeout=_ROW_TIMEOUT_SECONDS,
                        )
                    if order_row.exit_fee_usd is None and order_row.exit_time:
                        await asyncio.wait_for(
                            cme_executor._fetch_cme_exit_fee(client, account, order_row),
                            timeout=_ROW_TIMEOUT_SECONDS,
                        )
                    db.commit()
                except Exception as e:
                    db.rollback()
                    print(f"|| CME WATCH (FEE RETRY) || order {order_row.id} failed: {e}")
        except Exception as e:
            print(f"|| CME WATCH LOOP ERROR: {e}")
            traceback.print_exc()
        finally:
            if db is not None:
                db.close()

        await asyncio.sleep(_WATCH_POLL_SECONDS)
