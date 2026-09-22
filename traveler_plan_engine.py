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
#   WAITING_TOUCH  -> gate_traveler.advance_waiting_touch() (5m candles since
#                     cross_time) -- a resting limit sits at the trigger;
#                     opposite trigger breaks first -> DONE; a wick touches
#                     the trigger -> FILLED (fires the executor hook for
#                     GATE_TRAVELER accounts); 7-day journey cap passes with
#                     neither -> DONE. NOT scoped to "is the session still
#                     today" -- a row can poll across multiple days, unlike
#                     TradePlan's WAITING.
#   FILLED / TERCILE_SKIPPED / DONE -> terminal, not polled (see the query
#                        filter in run_traveler_plan_loop() below).
#
# THIS FILE ALSO drives MGMT_E1_STACK's D3 walk for GATE_TRAVELER's DRY_RUN
# orders (mgmt_e1_stack.py) -- NOT executor_live_engine.py's own poll_open_
# position()/run_executor_position_loop(), which are exchange-POSITION-
# driven (they query real Bitunix positions, and only ever pick up orders
# with a real entry_exchange_order_id, i.e. LIVE fills). GATE_TRAVELER is
# DRY_RUN-only for now (executor_engine.py's own comment on this), so its
# management walk is pure candle-driven simulation, same style as gate_
# traveler.py's own D1/D2 -- kept in this file rather than executor_live_
# engine.py to keep that module's real-exchange-call assumptions intact.
# ==============================================================================

import asyncio
from datetime import datetime, timezone
from typing import Optional

from database import SessionLocal, ExecutorAccount, ExecutorOrder, TravelerPlan
import executor_accounts
import gate_traveler
import mgmt_e1_stack
import market_data

# E1 has no partial T1 leg (full exit at whichever trigger fires first) --
# same f"CLOSED_{exit_reason}" naming convention executor_live_engine.py's
# own close_reason -> management_state mapping already uses, just E1's own
# outcome set (STOP/C5_EXIT/BBWP_EXIT/T1/TIME) instead of SPLIT's. P3
# (2026-09-20): promoted to mgmt_e1_stack.MGMT_E1_TERMINAL_STATES, the one
# shared source both this DRY_RUN walk and executor_live_e1_engine.py's
# real one use -- see that constant's own docstring.
_MGMT_E1_TERMINAL_STATES = mgmt_e1_stack.MGMT_E1_TERMINAL_STATES

_POLL_SECONDS = 60


def _as_utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def _advance_one(db, row: TravelerPlan, now_utc: datetime) -> None:
    symbol = row.symbol
    candles_5m = market_data.confirmed_5m_closes(await market_data.fetch_live_5m(symbol, limit=310))
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
        # the cross timestamp it determines internally. limit=200 is far
        # more than MIN_RSI_4H_BARS (15) needs; a fetch failure (empty list)
        # is handled by rsi_at_cross() itself (-> None -> not skipped), not
        # a reason to delay cross detection on the already-confirmed 5m data.
        candles_4h = await market_data.fetch_live_4h(symbol, limit=200)
        updates = gate_traveler.advance_waiting_cross(plan_dict, candles_5m, now_utc, candles_4h=candles_4h)
        await _apply(db, row, updates, symbol)

    elif row.status == "WAITING_TOUCH":
        # A multi-day window (up to 7 days from the cross) -- limit=310 5m
        # candles (~26h) may not cover the whole span on a late poll after a
        # gap, but every poll only needs candles since the LAST time this row
        # was checked to find a newly-confirmed bar; a wider fetch is cheap
        # insurance against a missed poll cycle, not required for correctness
        # (a bar this poll misses because the window was too short gets
        # caught on the NEXT poll, same as trade_plan_engine.py's own 60s-
        # cadence tolerance elsewhere).
        candles_wide = market_data.confirmed_5m_closes(await market_data.fetch_live_5m(symbol, limit=2016))  # ~7 days of 5m bars
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


async def _notify_executor(db, row: TravelerPlan, symbol: str) -> None:
    """GATE_TRAVELER's executor hook -- fires once, on the real FILLED
    transition, same 'bot = hands, brain stays in the plan row' treatment
    trade_plan_engine.py's own _notify_executor() gives v1/v2. Swallows every
    exception (an executor bug must never affect this row's own write)."""
    try:
        import executor_engine
        await executor_engine.process_traveler_fill(db, row)
    except Exception as e:
        print(f"|| EXECUTOR || Traveler hook failed for {symbol}: {e}")


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
    candles_5m = market_data.confirmed_5m_closes(await market_data.fetch_live_5m(symbol, limit=2016))  # ~7 days
    if not candles_5m:
        return
    candles_1h = await market_data.fetch_live_1h(symbol, limit=200)
    candles_4h = await market_data.fetch_live_4h(symbol, limit=200)
    if not candles_1h or not candles_4h:
        return  # can't check C5/BBWP this poll -- try again next cycle, never guess
    # BBWP's own feed (Bitunix, CC_INTERFACE.md item 3) -- deliberately NOT
    # inside the guard above; a bad Bitunix poll must never block the
    # Kraken-fed C5 check (see mgmt_e1_stack.check_c5_or_bbwp()'s docstring).
    candles_4h_bbwp = await market_data.fetch_bitunix_4h(symbol)

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
        db = SessionLocal()
        try:
            rows = db.query(TravelerPlan).filter(
                TravelerPlan.status.in_(["WAITING_CROSS", "WAITING_TOUCH"])
            ).all()
            for row in rows:
                try:
                    await _advance_one(db, row, now_utc)
                    db.commit()
                except Exception as _row_err:
                    db.rollback()
                    print(f"|| TRAVELER PLAN || Row error {row.symbol} {row.session_id} {row.date_key}: {_row_err}")

            # MGMT_E1_STACK's own D3 walk, same 60s cycle -- see this file's
            # own header for why it lives here, not executor_live_engine.py.
            for order in _open_dry_run_e1_orders(db):
                try:
                    await _advance_e1_order(db, order, now_utc)
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
            db.close()

        await asyncio.sleep(_POLL_SECONDS)
