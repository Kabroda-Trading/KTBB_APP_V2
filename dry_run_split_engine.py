# dry_run_split_engine.py
# ==============================================================================
# MGMT_SPLIT DRY_RUN MONITOR -- the async driver for mgmt_split_dry_run.py's
# pure candle-walk function, the same relationship traveler_plan_engine.py
# has to mgmt_e1_stack.py. A SEPARATE loop from executor_live_engine.py on
# purpose -- that module's whole design assumes a REAL exchange position to
# poll (get_position/get_order_detail calls); a DRY_RUN v1/v2 order has no
# such thing to poll, ever. Kept as its own file rather than a branch inside
# executor_live_engine.py's run_executor_position_loop() so that module's
# real-exchange-call assumptions stay completely intact, unmodified --
# Ruling B's explicit "do NOT touch the LIVE code paths" instruction.
#
# Scope: v1/v2 (GATE_V2/MGMT_SPLIT) orders ONLY, in DRY_RUN mode. GATE_
# TRAVELER's own DRY_RUN walk (MGMT_E1_STACK) is traveler_plan_engine.py's
# job, not this file's -- kept separate since the two management rules
# (single full exit vs 50/50 split) share nothing but the "candle wick
# instead of a real position" translation trick.
#
# Ruling B (DeepSeek, relayed by Andy 2026-09-15, AGENT_LOG.md both repos):
# v1/v2's own DRY_RUN orders sat at management_state="PENDING_ENTRY"
# forever with no exit ever recorded -- run_executor_position_loop() only
# ever queries orders with a real entry_exchange_order_id (a LIVE fill),
# so the ingestion spec's "every exit" requirement was unmet for v1/v2's
# own DRY_RUN population. The apples-to-apples forward comparison (v2 vs
# traveler, both DRY_RUN, same feed) needed v2's DRY_RUN orders to
# actually produce exits. Additive only, own tests, LIVE untouched.
#
# Same "console print, no separate audit row for the print itself"
# convention traveler_plan_engine.py's own _advance_e1_order() already
# established for candle-only DRY_RUN walks. Ruling D (DeepSeek, relayed
# by Andy 2026-09-15): this loop now DOES call executor_accounts.
# record_trade_result() on every terminal closure -- symmetrically with
# traveler_plan_engine.py's own MGMT_E1_STACK walk -- so a DRY_RUN
# account's dollar-ledger (consecutive_losses/risk_last_usd) actually
# compounds during the evaluation period instead of sitting flat (the
# stagnant-surface bug class this codebase has hit before). Tagged
# is_simulation=True on every call -- see that function's own docstring.
# ==============================================================================

import asyncio
from datetime import datetime, timezone

from database import SessionLocal, ExecutorAccount, ExecutorOrder
import executor_accounts
import mgmt_split_dry_run
import market_data

# Same vocabulary as executor_live_engine.py's own _TERMINAL_STATES for the
# SPLIT management_state values -- duplicated rather than imported (same
# cross-module convention traveler_plan_engine.py already uses for its own
# terminal-state tuple) so this file's only real dependency stays the
# vocabulary itself, never a live import of executor_live_engine.py's
# real-exchange code.
_SPLIT_TERMINAL_STATES = ("CLOSED_STOP_BEFORE_T1", "CLOSED_RUNNER_STOP", "CLOSED_T3", "CLOSED_ERROR", "ENTRY_FILLED_UNPROTECTED")

_POLL_SECONDS = 60


def _as_utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def _advance_one(db, order: ExecutorOrder, now_utc: datetime) -> None:
    symbol = order.symbol
    candles_5m = market_data.confirmed_5m_closes(await market_data.fetch_live_5m(symbol, limit=2016))  # ~7 days, same window mgmt_e1_stack.py's own caller uses
    if not candles_5m:
        return

    order_dict = {
        "direction": order.direction, "entry_price": order.entry_fill_price,
        "stop_price": order.stop_price, "t1_price": order.t1_price, "t3_price": order.t3_price,
        "entry_fill_time": _as_utc(order.entry_fill_time),
        "t1_status": order.t1_status, "t1_fill_price": order.t1_fill_price,
        "t1_fill_time": _as_utc(order.t1_fill_time), "t1_leg_r": order.t1_leg_r,
    }
    updates = mgmt_split_dry_run.advance(order_dict, candles_5m)
    if not updates:
        return

    for k, v in updates.items():
        setattr(order, k, v)

    if order.management_state in _SPLIT_TERMINAL_STATES:
        order.closed_at = now_utc
        print(f"|| MGMT_SPLIT DRY_RUN || order {order.id} ({symbol}): {order.management_state} "
              f"at {order.exit_price}, realized {order.realized_pnl_r:+.4f}R")
        # Ruling D (DeepSeek, relayed by Andy 2026-09-15): feed the SAME
        # ledger-compounding path a real closed LIVE trade uses
        # (executor_live_engine.py's own poll_open_position() call), so a
        # DRY_RUN account's risk_last_usd/consecutive_losses actually move
        # instead of sitting flat for the whole evaluation period.
        # is_simulation=True labels the audit row -- see record_trade_
        # result()'s own docstring for why no new column was needed.
        if order.realized_pnl_r is not None:
            account = db.query(ExecutorAccount).filter_by(id=order.account_id).first()
            if account is not None:
                pnl_usd = order.realized_pnl_r * (order.risk_dollars_used or 0.0)
                executor_accounts.record_trade_result(
                    db, account, pnl_usd, trade_plan_id=order.trade_plan_id,
                    recorded_by="system_dry_run_split", is_simulation=True,
                )
    elif order.management_state == "T1_FILLED":
        print(f"|| MGMT_SPLIT DRY_RUN || order {order.id} ({symbol}): T1 filled at "
              f"{order.t1_fill_price}, locked {order.t1_leg_r:+.4f}R")


async def run_dry_run_split_loop():
    print(">>> MGMT_SPLIT DRY_RUN MONITOR: Initializing (v1/v2 candle-only DRY_RUN walk)...")
    while True:
        try:
            from main import scheduler_health_registry as _thr
            _thr["dry_run_split"]["last_run"] = datetime.now(timezone.utc).isoformat()
            _thr["dry_run_split"]["status"] = "EXECUTING"
        except Exception:
            pass

        now_utc = datetime.now(timezone.utc)
        db = SessionLocal()
        try:
            open_orders = db.query(ExecutorOrder).filter(
                ExecutorOrder.mode == "DRY_RUN",
                ExecutorOrder.traveler_plan_id.is_(None),
                ExecutorOrder.mgmt_profile_used == "MGMT_SPLIT",
                ExecutorOrder.management_state.isnot(None),
                ~ExecutorOrder.management_state.in_(_SPLIT_TERMINAL_STATES),
                ExecutorOrder.decision == "WOULD_PLACE",
            ).all()
            for order in open_orders:
                try:
                    await _advance_one(db, order, now_utc)
                    db.commit()
                except Exception as _order_err:
                    db.rollback()
                    print(f"|| MGMT_SPLIT DRY_RUN || order {order.id} poll failed: {_order_err}")

            try:
                from main import scheduler_health_registry as _thr2
                _thr2["dry_run_split"]["status"] = "WAITING"
            except Exception:
                pass
        except Exception as e:
            print(f"|| MGMT_SPLIT DRY_RUN MONITOR ERROR: {e}")
            try:
                from main import scheduler_health_registry as _thr3
                _thr3["dry_run_split"]["status"] = "ERROR"
                _thr3["dry_run_split"]["error_count"] += 1
                _thr3["dry_run_split"]["last_error"] = str(e)
            except Exception:
                pass
        finally:
            db.close()

        await asyncio.sleep(_POLL_SECONDS)
