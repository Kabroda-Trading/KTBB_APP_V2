# traveler_plan_engine.py
# ==============================================================================
# TRAVELER PLAN INTRADAY MONITOR -- the async driver for gate_traveler.py's
# pure D1/D2 state-machine functions, same relationship trade_plan_engine.py
# has to trade_plan.py. A SEPARATE loop from trade_plan_engine.py on purpose
# -- see database.py's TravelerPlan docstring for why this can't just be a
# branch inside the existing v2 loop (different fill mechanism, multi-day
# journey window, no session-expiry scoping).
#
# Per-status routing, once per 60s poll cycle:
#   WAITING_CROSS  -> gate_traveler.advance_waiting_cross() (5m candles) --
#                     no cross yet -> silent; cross confirmed -> either
#                     TERCILE_SKIPPED (terminal, no trade) or WAITING_TOUCH.
#                     2026-09-27 (item 3, root cause of that day's incident):
#                     a WAITING_TOUCH transition ALSO fires
#                     executor_engine.process_traveler_cross() immediately
#                     -- this is where a LIVE account's real resting entry
#                     limit gets placed on the exchange, at the trigger,
#                     the moment the cross confirms and the gate passes.
#                     Real fill confirmation for that order is exchange-
#                     polled separately (executor_live_e1_engine.py), not
#                     decided by the candle simulation below.
#   WAITING_TOUCH  -> gate_traveler.advance_waiting_touch() (5m candles since
#                     cross_time) -- a resting limit sits at the trigger;
#                     opposite trigger breaks first -> DONE; a wick touches
#                     the trigger -> FILLED (fires the DRY_RUN-only
#                     executor hook, process_traveler_fill() -- see its own
#                     docstring); 7-day journey cap passes with neither ->
#                     DONE. NOT scoped to "is the session still today" -- a
#                     row can poll across multiple days, unlike TradePlan's
#                     WAITING.
#   FILLED / TERCILE_SKIPPED / DONE -> terminal, not polled (see the query
#                        filter in run_traveler_plan_loop() below).
#
# R1 RE-ARM (2026-10-06, Andy ruling 15:24 CT, TRAVELER_D1_D2_D3_SPEC.md "R1
# RE-ARM AMENDMENT", Arm A only -- same-lock-day): a SEPARATE field,
# TravelerPlan.rearm_status, tracks the re-arm leg's own progression --
# never conflated with the primary's own `status` column above, which stays
# exactly what it was at the primary fill. Set to REARM_WATCH by mgmt_e1_
# stack.start_rearm_watch_if_eligible(), called right after a PRIMARY order
# closes with exit_reason=="C5_EXIT" specifically (not T1/STOP/TIME/
# BBWP_EXIT). Per-rearm_status routing, same 60s poll cycle:
#   REARM_WATCH        -> gate_traveler.advance_rearm_watch() (5m + 1h/4h
#                          candles) -- the window closes at the next lock
#                          (session_manager.next_lock_utc()) with no re-
#                          cross -> REARM_WINDOW_CLOSED (terminal); a
#                          qualifying re-cross -> REARM_TERCILE_SKIPPED
#                          (terminal) or REARM_WAITING_TOUCH, which ALSO
#                          fires executor_engine.process_traveler_rearm_
#                          cross() immediately (LIVE accounts' real re-arm
#                          entry order, same "place at the cross, not after
#                          a later-decided touch" reasoning as the primary's
#                          own 2026-09-27 fix).
#   REARM_WAITING_TOUCH -> gate_traveler.advance_rearm_waiting_touch() (5m
#                          candles since rearm_cross_time) -- bounded by
#                          the 90-bar/7.5h cap (NOT the primary's own 7-day
#                          journey_cap_at), opposite trigger break, or a
#                          wick touch -> REARM_FILLED (fires process_
#                          traveler_rearm_fill(), DRY_RUN bookkeeping only,
#                          same split as the primary's own fill hook).
#   REARM_TERCILE_SKIPPED / REARM_WAITING_TOUCH / REARM_FILLED /
#   REARM_WINDOW_CLOSED -> REARM_FILLED is the only non-terminal one of
#                          these four for THIS field (it then feeds MGMT_
#                          E1_STACK's own D3 walk exactly like a primary
#                          FILLED order does -- see _open_dry_run_e1_
#                          orders() below, which needs NO changes at all
#                          for re-arm orders, since it already selects on
#                          ExecutorOrder fields alone, not TravelerPlan.status).
#
# THIS FILE ALSO drives MGMT_E1_STACK's D3 walk for GATE_TRAVELER's DRY_RUN
# orders (mgmt_e1_stack.py) -- NOT executor_live_engine.py's own poll_open_
# position()/run_executor_position_loop(), which are exchange-POSITION-
# driven (they query real Bitunix positions, and only ever pick up orders
# with a real entry_exchange_order_id, i.e. LIVE fills). GATE_TRAVELER has
# real LIVE accounts as of 2026-09-26 (Andy_Bitunix, dawson_bitu) -- this
# file's own candle-driven D3 walk (mgmt_e1_stack.py) is DRY_RUN-only
# (_open_dry_run_e1_orders()'s own mode filter, unchanged); a LIVE order's
# real management is executor_live_e1_engine.py's job, exchange-polled,
# never this simulation. Kept in this file (rather than executor_live_
# engine.py) to keep that module's real-exchange-call assumptions intact.
# ==============================================================================

import asyncio
from datetime import datetime, timezone
from typing import Optional

from database import SessionLocal, ExecutorAccount, ExecutorOrder, TravelerPlan
import executor_accounts
import gate_traveler
import mgmt_e1_stack
import market_data
import session_manager

# E1 has no partial T1 leg (full exit at whichever trigger fires first) --
# same f"CLOSED_{exit_reason}" naming convention executor_live_engine.py's
# own close_reason -> management_state mapping already uses, just E1's own
# outcome set (STOP/C5_EXIT/BBWP_EXIT/T1/TIME) instead of SPLIT's. P3
# (2026-09-20): promoted to mgmt_e1_stack.MGMT_E1_TERMINAL_STATES, the one
# shared source both this DRY_RUN walk and executor_live_e1_engine.py's
# real one use -- see that constant's own docstring.
_MGMT_E1_TERMINAL_STATES = mgmt_e1_stack.MGMT_E1_TERMINAL_STATES

_POLL_SECONDS = 60
# 2026-09-30 real production incident (DeepSeek's prod-DB find, Kabroda AI
# Brain AGENT_LOG.md 18:26 CT): TravelerPlan id=14's own row (and, per the
# same evidence, the candle_history writes this loop's own fetches would
# have produced) went silent right at the 13:00 UTC lock cycle and never
# resumed for the rest of the day -- a real 5m close beyond BO at 13:05
# UTC was never evaluated, because nothing ever polled this row again.
# Every per-row/per-order call below was ALREADY wrapped in its own try/
# except (so a normal exception on one row never took down the loop or
# blocked other rows) -- but neither that nor this file's own top-level
# try/except can catch a coroutine that never returns at all (a genuine
# network-level hang, not a raised exception) -- and market_data.py's own
# header (see its "EXCHANGE CLIENT" section) already documents one prior,
# real instance of exactly this class of bug for the Kraken/ccxt client:
# "hangs indefinitely: no exception, no timeout, not even cancellable via
# asyncio.wait_for() ... the underlying OS thread stays blocked." Bounding
# every row/order's processing in asyncio.wait_for() below is a defense-
# in-depth fix independent of pinning down today's EXACT stuck call: even
# if some future network path hangs the same uncancellable way, the loop
# itself gives up on that one row after this timeout and moves on to the
# next poll cycle, instead of freezing every future poll forever the way
# today's incident did.
_ROW_TIMEOUT_SECONDS = 45


def _fmt_r(value: Optional[float]) -> str:
    """Same None-safe formatting traveler_plan_notify.py's own _fmt()
    helper uses for a realized-R value -- written locally rather than
    importing that module's helper, matching this file's own established
    self-contained-helper style (see _as_utc() below). 2026-09-23: DRY_RUN's
    own realized_pnl_r CAN be None (when entry_price/stop_price are both
    missing, r_basis never computes) -- unlike LIVE's _r_multiple(), which
    never returns None -- so a bare f"{value:+.4f}" format spec on this
    value would raise TypeError on that edge case."""
    return f"{value:+.4f}" if value is not None else "?"


def _as_utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def _advance_one(db, row: TravelerPlan, now_utc: datetime) -> None:
    symbol = row.symbol
    # 2026-09-27 (Andy ruling 14:55 CT): Bitunix, not Kraken -- the
    # traveler's own decision feed, see market_data.py's fetch_bitunix_*()
    # docstrings for the full ruling.
    candles_5m = market_data.confirmed_5m_closes(await market_data.fetch_bitunix_5m(symbol, target_bars=310))
    if not candles_5m:
        return

    if row.status == "WAITING_CROSS":
        plan_dict = {
            "status": row.status,
            "breakout_trigger": row.breakout_trigger, "breakdown_trigger": row.breakdown_trigger,
            "r30_high": row.r30_high, "r30_low": row.r30_low,
        }
        # D1 RSI-AT-CROSS (2026-09-21): raw (unstripped) 4H candles -- gate_
        # traveler.rsi_at_cross() does its own closed-bar filtering against
        # the cross timestamp it determines internally. target_bars=200 is
        # far more than MIN_RSI_4H_BARS (15) needs; a fetch failure (empty
        # list) is handled by rsi_at_cross() itself (-> None -> not
        # skipped), not a reason to delay cross detection on the already-
        # confirmed 5m data.
        candles_4h = await market_data.fetch_bitunix_4h(symbol, target_bars=200)
        # 2026-10-07 P0 fix (Andy directive 08:28 CT): this plan's own
        # frozen 24h deadline -- see TravelerPlan.session_expires_at's own
        # comment. _as_utc() handles the SQLite-strips-tzinfo round-trip,
        # same convention already used for cross_time/journey_cap_at.
        updates = gate_traveler.advance_waiting_cross(
            plan_dict, candles_5m, now_utc, candles_4h=candles_4h,
            session_expires_at=_as_utc(row.session_expires_at),
        )
        await _apply(db, row, updates, symbol)
        # 2026-09-27 (item 3, root cause of the same-day incident): place
        # LIVE accounts' real resting entry order NOW, at the confirmed
        # cross + gate pass -- not after a later poll decides a touch
        # already happened. Only fires on a genuine WAITING_CROSS ->
        # WAITING_TOUCH transition (gate passed) -- TERCILE_SKIPPED takes
        # no trade, so nothing to arm. See executor_engine.process_
        # traveler_cross()'s own docstring for the full reasoning.
        if updates and updates.get("status") == "WAITING_TOUCH":
            await _notify_executor_cross(db, row, symbol)

    elif row.status == "WAITING_TOUCH":
        # A multi-day window (up to 7 days from the cross) -- target_
        # bars=2016 5m candles (~7 days) may not cover the whole span on a
        # late poll after a gap, but every poll only needs candles since
        # the LAST time this row was checked to find a newly-confirmed
        # bar; a wider fetch is cheap insurance against a missed poll
        # cycle, not required for correctness (a bar this poll misses
        # because the window was too short gets caught on the NEXT poll,
        # same as trade_plan_engine.py's own 60s-cadence tolerance
        # elsewhere).
        candles_wide = market_data.confirmed_5m_closes(await market_data.fetch_bitunix_5m(symbol, target_bars=2016))  # ~7 days of 5m bars
        plan_dict = {
            "status": row.status, "direction": row.direction,
            "breakout_trigger": row.breakout_trigger, "breakdown_trigger": row.breakdown_trigger,
            "opposite_trigger": row.opposite_trigger,
            "cross_time": _as_utc(row.cross_time), "journey_cap_at": _as_utc(row.journey_cap_at),
        }
        updates = gate_traveler.advance_waiting_touch(plan_dict, candles_wide, now_utc)
        if updates and updates.get("status") == "FILLED":
            await _apply(db, row, updates, symbol)
            await _notify_executor(db, row, symbol)
            return
        await _apply(db, row, updates, symbol)


async def _apply(db, row: TravelerPlan, updates, symbol: str) -> None:
    if not updates:
        return
    prev_status = row.status
    for k, v in updates.items():
        setattr(row, k, v)
    if row.status != prev_status:
        print(f"|| TRAVELER PLAN || {symbol} {row.session_id} {row.date_key}: "
              f"{prev_status} -> {row.status} -- {updates.get('last_transition_reason')}")
        _notify_traveler_transition(prev_status, row, symbol)


async def _advance_rearm_one(db, row: TravelerPlan, now_utc: datetime) -> None:
    """R1 re-arm (2026-10-06) -- the rearm_status counterpart to
    _advance_one() above. See this file's own header for the full state
    table; dispatches on rearm_status exactly like _advance_one() dispatches
    on status, reusing the SAME Bitunix fetch calls (this function lives
    inside the same asyncio.wait_for() row-timeout protection as every
    other row in run_traveler_plan_loop() below, so it gets the 2026-09-30
    hang fix automatically, not as a separate change)."""
    symbol = row.symbol
    candles_5m = market_data.confirmed_5m_closes(await market_data.fetch_bitunix_5m(symbol, target_bars=310))
    if not candles_5m:
        return

    if row.rearm_status == "REARM_WATCH":
        # Exhaustion-cleared check -- the SAME mgmt_e1_stack.check_c5_or_
        # bbwp() condition that fired the primary's own C5_EXIT, now
        # evaluated fresh each poll; gate_traveler.advance_rearm_watch()
        # takes the boolean result, not raw candles, so it never needs to
        # import mgmt_e1_stack itself (this file's own header: D1/D2 stays
        # decoupled from D3). 2026-09-27 (Andy ruling 14:55 CT): Bitunix,
        # not Kraken, same migration every other traveler fetch already
        # has -- C5's own 4H leg and BBWP read the SAME fetch.
        candles_1h = await market_data.fetch_bitunix_1h(symbol, target_bars=200)
        candles_4h = await market_data.fetch_bitunix_4h(symbol, target_bars=200)
        if not candles_1h or not candles_4h:
            return  # can't check exhaustion-cleared this poll -- try again next cycle, never guess
        c5_hit, bbwp_hit = mgmt_e1_stack.check_c5_or_bbwp(
            candles_1h, candles_4h, now_ts=now_utc.timestamp(), candles_4h_bbwp=candles_4h)
        exhaustion_cleared = not (c5_hit or bbwp_hit)

        plan_dict = {
            "direction": row.direction,
            "breakout_trigger": row.breakout_trigger, "breakdown_trigger": row.breakdown_trigger,
        }
        rearm_watch_deadline = session_manager.next_lock_utc(now_utc)
        updates = gate_traveler.advance_rearm_watch(
            plan_dict, candles_5m, candles_4h, now_utc,
            exhaustion_cleared=exhaustion_cleared, rearm_watch_deadline=rearm_watch_deadline,
        )
        await _apply_rearm(db, row, updates, symbol)
        # 2026-10-06: same "place the real order AT the confirmed re-cross,
        # not after a later-decided touch" reasoning as the primary's own
        # 2026-09-27 fix (_notify_executor_cross() below) -- only fires on
        # a genuine REARM_WATCH -> REARM_WAITING_TOUCH transition (gate
        # passed); REARM_TERCILE_SKIPPED takes no trade, nothing to arm.
        if updates and updates.get("rearm_status") == "REARM_WAITING_TOUCH":
            await _notify_executor_rearm_cross(db, row, symbol)

    elif row.rearm_status == "REARM_WAITING_TOUCH":
        candles_wide = market_data.confirmed_5m_closes(await market_data.fetch_bitunix_5m(symbol, target_bars=2016))
        plan_dict = {
            "direction": row.direction,
            "breakout_trigger": row.breakout_trigger, "breakdown_trigger": row.breakdown_trigger,
            "opposite_trigger": row.opposite_trigger,
            "rearm_cross_time": _as_utc(row.rearm_cross_time),
            "rearm_entry_expires_at": _as_utc(row.rearm_entry_expires_at),
        }
        updates = gate_traveler.advance_rearm_waiting_touch(plan_dict, candles_wide, now_utc)
        if updates and updates.get("rearm_status") == "REARM_FILLED":
            await _apply_rearm(db, row, updates, symbol)
            await _notify_executor_rearm_fill(db, row, symbol)
            return
        await _apply_rearm(db, row, updates, symbol)


async def _apply_rearm(db, row: TravelerPlan, updates, symbol: str) -> None:
    if not updates:
        return
    prev_rearm_status = row.rearm_status
    for k, v in updates.items():
        setattr(row, k, v)
    if row.rearm_status != prev_rearm_status:
        print(f"|| TRAVELER PLAN (RE-ARM) || {symbol} {row.session_id} {row.date_key}: "
              f"{prev_rearm_status} -> {row.rearm_status} -- {updates.get('rearm_last_transition_reason')}")
        _notify_traveler_rearm_transition(prev_rearm_status, row, symbol)


def _notify_traveler_transition(prev_status: str, row: TravelerPlan, symbol: str) -> None:
    """Ruling C (DeepSeek, relayed by Andy 2026-09-15): ARMED/DONE emails
    for GATE_TRAVELER -- same non-blocking, own-try/except pattern as
    trade_plan_engine.py's own _notify_transition() for v2 (an occasional
    blocking SMTP round-trip inside this 60s-cadence loop is an accepted
    cost, same as that module)."""
    try:
        import notify
        import traveler_plan_notify

        mail = traveler_plan_notify.notification_for_traveler_transition(prev_status, row.__dict__)
        if mail:
            subject, body = mail
            notify.send_admin_email(subject, body)
    except Exception as e:
        print(f"|| TRAVELER PLAN || Notification failed for {symbol}: {e}")


def _notify_traveler_rearm_transition(prev_rearm_status, row: TravelerPlan, symbol: str) -> None:
    """R1 re-arm (2026-10-06) -- the rearm_status counterpart to
    _notify_traveler_transition() above. Plan-level (REARM_WATCH-entered/
    REARM_WINDOW_CLOSED/REARM_TERCILE_SKIPPED), radar-class, same
    send_admin_email() merged-list routing as the primary's own ARMED/DONE
    emails -- not account-specific, so NOT the L4 per-account rule (that
    applies to fills/opens/closes, handled separately by _notify_traveler_
    management_event() below, unchanged for re-arm orders)."""
    try:
        import notify
        import traveler_plan_notify

        mail = traveler_plan_notify.notification_for_traveler_rearm_transition(prev_rearm_status, row.__dict__)
        if mail:
            subject, body = mail
            notify.send_admin_email(subject, body)
    except Exception as e:
        print(f"|| TRAVELER PLAN (RE-ARM) || Notification failed for {symbol}: {e}")


def _notify_traveler_management_event(order: ExecutorOrder, traveler_plan_row: Optional[TravelerPlan] = None) -> None:
    """The one post-fill D3 email MGMT_E1_STACK ever produces -- a single
    full-exit design (no partial T1 leg, no runner), so there is exactly
    one terminal management event per journey, never a sequence (see
    mgmt_e1_stack.py's own header). Same non-blocking, own-try/except
    pattern as _notify_traveler_transition() above -- deliberately NOT the
    style of executor_live_e1_engine.py's own two unguarded `import notify`
    sites (those are early-failure paths where a retry is wanted). A bug
    in this new email code must never roll back the real closure
    bookkeeping (management_state, the audit row, record_trade_result())
    that has already committed for this tick -- see run_traveler_plan_loop()
    below, which commits per-order and would otherwise roll back the whole
    tick on any uncaught exception here.

    traveler_plan_row (2026-10-07, session-date-tag work order): the
    caller already queries this row a few lines above its own call site
    (for journey_cap_at) -- passed through here for the email's own
    [date_key] subject tag rather than a second DB query. Optional and
    defaults to None (tag simply omitted) so this stays callable exactly
    as before anywhere the plan row isn't already in scope."""
    try:
        import notify
        import traveler_plan_notify

        order_dict = {
            "symbol": order.symbol, "direction": order.direction,
            "exit_reason": order.exit_reason, "exit_price": order.exit_price,
            "realized_pnl_r": order.realized_pnl_r, "traveler_plan_id": order.traveler_plan_id,
            "account_id": order.account_id,   # 2026-09-27 item 4 -- which account this closure applies to
            "approximated": False,  # DRY_RUN never approximates -- candle-sourced, deterministic (mgmt_e1_stack.py)
            "is_rearm": order.is_rearm,  # 2026-10-06 -- subject-line clarity only, see build_traveler_management_event_email()
            "date_key": traveler_plan_row.date_key if traveler_plan_row is not None else None,
        }
        subject, body = traveler_plan_notify.build_traveler_management_event_email(order_dict, is_live=False)
        # 2026-09-28 (Andy L4 ruling): a DRY_RUN close is still THIS
        # account's own bookkeeping detail (risk$/R) -- route to its
        # owner only, same as the LIVE version in executor_live_e1_engine.py.
        notify.send_account_email(subject, body, order.account_id)
    except Exception as e:
        print(f"|| MGMT_E1_STACK || Management-event notification failed for order {order.id}: {e}")


async def _notify_executor(db, row: TravelerPlan, symbol: str) -> None:
    """GATE_TRAVELER's executor hook -- fires once, on the candle-simulated
    FILLED transition, DRY_RUN accounts only as of 2026-09-27 (see
    executor_engine.process_traveler_fill()'s own updated docstring; LIVE
    accounts are armed earlier, at the cross -- see _notify_executor_cross()
    below). Same 'bot = hands, brain stays in the plan row' treatment
    trade_plan_engine.py's own _notify_executor() gave v1/v2. Swallows every
    exception (an executor bug must never affect this row's own write)."""
    try:
        import executor_engine
        await executor_engine.process_traveler_fill(db, row)
    except Exception as e:
        print(f"|| EXECUTOR || Traveler hook failed for {symbol}: {e}")


async def _notify_executor_cross(db, row: TravelerPlan, symbol: str) -> None:
    """2026-09-27 (Andy ruling 14:55 CT, item 3): fires once, on the
    confirmed WAITING_CROSS -> WAITING_TOUCH transition (gate passed) --
    this is where a LIVE account's real resting entry order actually gets
    placed on the exchange, at the trigger, per spec §D2. See executor_
    engine.process_traveler_cross()'s own docstring for the full root-
    cause reasoning. Same non-blocking, own-try/except pattern as
    _notify_executor() above."""
    try:
        import executor_engine
        await executor_engine.process_traveler_cross(db, row)
    except Exception as e:
        print(f"|| EXECUTOR || Traveler cross hook failed for {symbol}: {e}")


async def _notify_executor_rearm_fill(db, row: TravelerPlan, symbol: str) -> None:
    """R1 re-arm (2026-10-06) -- the rearm_status counterpart to
    _notify_executor() above: fires once, on the candle-simulated
    REARM_WAITING_TOUCH -> REARM_FILLED transition, DRY_RUN accounts only
    (LIVE accounts are armed earlier, at the re-cross -- see _notify_
    executor_rearm_cross() below). Same swallow-every-exception contract."""
    try:
        import executor_engine
        await executor_engine.process_traveler_rearm_fill(db, row)
    except Exception as e:
        print(f"|| EXECUTOR || Traveler re-arm fill hook failed for {symbol}: {e}")


async def _notify_executor_rearm_cross(db, row: TravelerPlan, symbol: str) -> None:
    """R1 re-arm (2026-10-06) -- the rearm_status counterpart to
    _notify_executor_cross() above: fires once, on the confirmed REARM_
    WATCH -> REARM_WAITING_TOUCH transition (re-cross + tercile-gate
    pass) -- this is where a LIVE account's real re-arm resting entry
    order actually gets placed on the exchange, at the (same) trigger,
    same "place at the cross, not after a later-decided touch" reasoning
    as the primary's own 2026-09-27 fix."""
    try:
        import executor_engine
        await executor_engine.process_traveler_rearm_cross(db, row)
    except Exception as e:
        print(f"|| EXECUTOR || Traveler re-arm cross hook failed for {symbol}: {e}")


def _open_dry_run_e1_orders(db) -> list:
    """The orders this SIMULATED walk drives: DRY_RUN only, same
    `mode == "DRY_RUN"` filter dry_run_split_engine.py's own query carries.
    2026-09-21 audit: this query had no mode filter, so once a LIVE traveler
    order existed (P3) it was selected here too -- and after its real fill
    the live engine sets entry_fill_time, which is all _advance_e1_order()
    needs to start simulating exits on a real position and mark the row
    terminal, at which point the live loop stops managing it. LIVE orders
    belong to executor_live_e1_engine.py alone."""
    return db.query(ExecutorOrder).filter(
        ExecutorOrder.mode == "DRY_RUN",
        ExecutorOrder.traveler_plan_id.isnot(None),
        ExecutorOrder.management_state.isnot(None),
        ~ExecutorOrder.management_state.in_(_MGMT_E1_TERMINAL_STATES),
        ExecutorOrder.decision == "WOULD_PLACE",
    ).all()


async def _advance_e1_order(db, order: ExecutorOrder, now_utc: datetime) -> None:
    symbol = order.symbol
    # 2026-09-27 (Andy ruling 14:55 CT): Bitunix, not Kraken -- see
    # market_data.py's fetch_bitunix_*() docstrings. C5's own 4H leg and
    # BBWP now read the SAME Bitunix feed (candles_4h passed for both
    # parameters below) -- they were only ever split because C5 stayed on
    # Kraken while BBWP moved to Bitunix first (2026-09-22); that split is
    # gone now that both are Bitunix, though check_c5_or_bbwp() still
    # accepts candles_4h/candles_4h_bbwp as two parameters (see market_
    # data.fetch_bitunix_4h()'s own updated docstring on why the
    # signature wasn't changed under this same fix).
    candles_5m = market_data.confirmed_5m_closes(await market_data.fetch_bitunix_5m(symbol, target_bars=2016))  # ~7 days
    if not candles_5m:
        return
    candles_1h = await market_data.fetch_bitunix_1h(symbol, target_bars=200)
    candles_4h = await market_data.fetch_bitunix_4h(symbol, target_bars=200)
    if not candles_1h or not candles_4h:
        return  # can't check C5/BBWP this poll -- try again next cycle, never guess
    candles_4h_bbwp = candles_4h

    traveler_plan = db.query(TravelerPlan).filter_by(id=order.traveler_plan_id).first()
    journey_cap_at = _as_utc(traveler_plan.journey_cap_at) if traveler_plan else None

    order_dict = {
        "direction": order.direction, "entry_price": order.entry_price,
        "stop_price": order.stop_price, "t1_price": order.t1_price,
        "entry_fill_time": _as_utc(order.entry_fill_time),
    }
    result = mgmt_e1_stack.advance(order_dict, candles_5m, candles_1h, candles_4h, now_utc, journey_cap_at,
                                    candles_4h_bbwp=candles_4h_bbwp)
    if result is None:
        return

    order.exit_reason = result["exit_reason"]
    order.exit_price = result["exit_price"]
    order.exit_time = result["exit_time"]
    order.c5_fired = result["c5_fired"]
    order.bbwp_fired = result["bbwp_fired"]
    order.management_state = f"CLOSED_{result['exit_reason']}"
    order.closed_at = now_utc
    order.close_reason = result["exit_reason"]
    # Same R-multiple convention as v1/v2's own realized_pnl_r (executor_
    # live_engine.py::_r_multiple()) -- E1 is a single, full-size exit (no
    # partial leg), so this IS the trade's whole realized R, not a blended one.
    sgn = 1 if order.direction == "LONG" else -1
    r_basis = abs(order.entry_price - order.stop_price) if order.entry_price and order.stop_price else None
    if r_basis:
        order.realized_pnl_r = (result["exit_price"] - order.entry_price) * sgn / r_basis
    print(f"|| MGMT_E1_STACK || order {order.id} ({symbol}): CLOSED_{result['exit_reason']} "
          f"at {result['exit_price']:,.2f}")

    # 2026-09-23 audit-write parity fix: executor_live_e1_engine.py's own
    # _finalize_traveler_close() has always written a real ExecutorAuditLog
    # row on every LIVE close; this DRY_RUN walk never did -- confirmed via
    # grep, zero write_audit() calls anywhere in this file before this fix,
    # meaning every DRY_RUN closure (the only kind that exists today -- no
    # traveler account is currently LIVE) was invisible in the audit trail.
    # Uses order.account_id directly (the raw FK already on the row)
    # rather than querying the ExecutorAccount object -- that object is
    # only actually needed below, for record_trade_result(). This also
    # fires the new management-event email (Andy's own ask: "the same
    # thing on the radar" also communicated by email) -- unconditionally,
    # not gated behind the realized_pnl_r check below, so a missing R
    # doesn't silently suppress either the audit row or the email.
    executor_accounts.write_audit(
        db, "POSITION_CLOSED",
        f"traveler trade closed (bookkeeping): {order.exit_reason}, realized {_fmt_r(order.realized_pnl_r)}R",
        account_id=order.account_id, traveler_plan_id=order.traveler_plan_id,
        executor_order_id=order.id, actor="system")
    _notify_traveler_management_event(order, traveler_plan)

    # R1 re-arm (2026-10-06, Andy ruling 15:24 CT) -- mgmt_e1_stack.start_
    # rearm_watch_if_eligible() is the ONE shared guard (its own docstring
    # has the full "why"): only a PRIMARY order (is_rearm=False) closing
    # via C5_EXIT specifically starts the watch, and only once per plan.
    # traveler_plan is already fetched above, for journey_cap_at -- reused
    # here, not re-queried.
    if mgmt_e1_stack.start_rearm_watch_if_eligible(traveler_plan, order):
        print(f"|| TRAVELER PLAN (RE-ARM) || {symbol} {order.account_id}: "
              f"primary closed via C5_EXIT -- REARM_WATCH entered")
        _notify_traveler_rearm_transition(None, traveler_plan, symbol)

    # Ruling D (DeepSeek, relayed by Andy 2026-09-15): feed the SAME
    # ledger-compounding path a real closed LIVE trade uses (executor_
    # live_engine.py's own poll_open_position() call), symmetrically with
    # dry_run_split_engine.py's own MGMT_SPLIT walk -- a DRY_RUN account's
    # risk_last_usd/consecutive_losses now actually compound instead of
    # sitting flat for the whole evaluation period. is_simulation=True
    # labels the audit row -- see record_trade_result()'s own docstring.
    if order.realized_pnl_r is not None:
        account = db.query(ExecutorAccount).filter_by(id=order.account_id).first()
        if account is not None:
            pnl_usd = order.realized_pnl_r * (order.risk_dollars_used or 0.0)
            executor_accounts.record_trade_result(
                db, account, pnl_usd, trade_plan_id=order.trade_plan_id,
                recorded_by="system_dry_run_traveler", is_simulation=True,
            )


async def run_traveler_plan_loop():
    print(">>> TRAVELER PLAN MONITOR: Initializing (GATE_TRAVELER D1/D2 state machine)...")
    while True:
        try:
            from main import scheduler_health_registry as _thr
            _thr["traveler_plan"]["last_run"] = datetime.now(timezone.utc).isoformat()
            _thr["traveler_plan"]["status"] = "EXECUTING"
        except Exception:
            pass

        now_utc = datetime.now(timezone.utc)
        # 2026-09-30: db used to be opened OUTSIDE this try/except (a bare
        # `db = SessionLocal()` before `try:`) -- if that call itself ever
        # raised (e.g. a genuinely exhausted DB connection pool during the
        # same 13:00 lock cycle's own burst of DB activity), the exception
        # would propagate out of the entire while-loop body, silently
        # killing this whole background task with no restart -- the exact
        # "task just isn't there anymore" shape of today's incident. Now
        # inside the try, with db=None as the guard for the finally below.
        db = None
        try:
            db = SessionLocal()
            rows = db.query(TravelerPlan).filter(
                TravelerPlan.status.in_(["WAITING_CROSS", "WAITING_TOUCH"])
            ).all()
            for row in rows:
                try:
                    await asyncio.wait_for(_advance_one(db, row, now_utc), timeout=_ROW_TIMEOUT_SECONDS)
                    db.commit()
                except Exception as _row_err:
                    db.rollback()
                    print(f"|| TRAVELER PLAN || Row error {row.symbol} {row.session_id} {row.date_key}: {_row_err}")

            # R1 re-arm (2026-10-06) -- a SEPARATE query on rearm_status, not
            # status (see this file's own header for the full field split).
            # A plan that enters REARM_WATCH THIS tick (via the D3 loop
            # below, same cycle) is picked up on the NEXT poll, not this one
            # -- same one-cycle tolerance every other transition in this
            # loop already accepts.
            rearm_rows = db.query(TravelerPlan).filter(
                TravelerPlan.rearm_status.in_(["REARM_WATCH", "REARM_WAITING_TOUCH"])
            ).all()
            for row in rearm_rows:
                try:
                    await asyncio.wait_for(_advance_rearm_one(db, row, now_utc), timeout=_ROW_TIMEOUT_SECONDS)
                    db.commit()
                except Exception as _rearm_err:
                    db.rollback()
                    print(f"|| TRAVELER PLAN (RE-ARM) || Row error {row.symbol} {row.session_id} {row.date_key}: {_rearm_err}")

            # MGMT_E1_STACK's own D3 walk, same 60s cycle -- see this file's
            # own header for why it lives here, not executor_live_engine.py.
            for order in _open_dry_run_e1_orders(db):
                try:
                    await asyncio.wait_for(_advance_e1_order(db, order, now_utc), timeout=_ROW_TIMEOUT_SECONDS)
                    db.commit()
                except Exception as _order_err:
                    db.rollback()
                    print(f"|| MGMT_E1_STACK || order {order.id} poll failed: {_order_err}")

            try:
                from main import scheduler_health_registry as _thr2
                _thr2["traveler_plan"]["status"] = "WAITING"
            except Exception:
                pass
        except Exception as e:
            print(f"|| TRAVELER PLAN MONITOR ERROR: {e}")
            try:
                from main import scheduler_health_registry as _thr3
                _thr3["traveler_plan"]["status"] = "ERROR"
                _thr3["traveler_plan"]["error_count"] += 1
                _thr3["traveler_plan"]["last_error"] = str(e)
            except Exception:
                pass
        finally:
            if db is not None:
                db.close()

        await asyncio.sleep(_POLL_SECONDS)
