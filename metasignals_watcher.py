# metasignals_watcher.py
# ==============================================================================
# METASIGNALS PAPER DESK WATCHER -- the one background loop this subsystem
# needs. Andy directive 2026-10-13, METASIGNALS_TRIAGE_AND_EXECUTION_SPEC.md
# section 4A.4: every 30 seconds, poll the live Bitunix mark price for
# every symbol with an open paper position, advance each order's state
# (fill a resting limit, take TP1 and ratchet to breakeven, hit the hard
# stop, take TP2), and refresh the paper account's live equity so Andy can
# watch it move in real time.
#
# Deliberately the ONLY loop this subsystem registers (unlike Alt Matrix's
# two-loop split) -- there is no separate "signal" cadence to protect: new
# plans/orders are created synchronously, inline, by the POST /api/
# metasignals/alert route the instant an alert arrives, not by a polling
# tick. This loop's only job is advancing positions that already exist.
#
# Iron Wall: zero import of/call into gate_traveler.py, traveler_plan_
# engine.py, battlebox_pipeline.py, executor_live_e1_engine.py,
# executor_plan_builder.py, executor_sizing.py, or any alt_matrix_*.py
# module. executor_bitunix_client.py is explicitly NOT forbidden (a
# generic, already-public exchange client Alt Matrix itself reuses
# directly) -- this is the one real network call this loop makes.
# ==============================================================================

import asyncio
import traceback
from typing import Dict, Optional

from sqlalchemy.orm import Session

from database import SessionLocal, MetaSignalsPaperOrder
import metasignals_paper_engine

_POLL_SECONDS = 30
_ROW_TIMEOUT_SECONDS = 15
_OPEN_STATES = ("PENDING_LIMIT", "OPEN", "HALF_CLOSED_BE")


async def _fetch_mark_price(symbol: str) -> Optional[float]:
    import executor_bitunix_client
    try:
        resp = await executor_bitunix_client.fetch_public_funding_rate(symbol.replace("/", ""))
    except Exception:
        return None
    if resp.get("code") not in (0, None):
        return None
    data = resp.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None
    try:
        price = data.get("markPrice")
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


async def run_tick(db: Session) -> None:
    """The actual tick logic, accepting `db` as a parameter -- same
    split as alt_matrix_engine.py's own inner helpers vs. its outer
    self-managing loop, so tests can exercise this directly against a
    real fixture session without needing to monkeypatch SessionLocal or
    fight the outer wrapper's own session lifecycle."""
    open_orders = db.query(MetaSignalsPaperOrder).filter(
        MetaSignalsPaperOrder.state.in_(_OPEN_STATES),
    ).all()
    if not open_orders:
        return

    symbols = sorted({o.symbol for o in open_orders})
    mark_price_by_symbol: Dict[str, float] = {}
    for symbol in symbols:
        price = await asyncio.wait_for(_fetch_mark_price(symbol), timeout=_ROW_TIMEOUT_SECONDS)
        if price is not None:
            mark_price_by_symbol[symbol] = price

    for order_row in open_orders:
        price = mark_price_by_symbol.get(order_row.symbol)
        if price is None:
            continue   # fetch failed this tick -- retry next tick, never guess
        try:
            order_row.last_mark_price = price
            order_row.unrealized_pnl = metasignals_paper_engine.mark_unrealized(order_row, price)
            metasignals_paper_engine.advance_order(db, order_row, price)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"|| METASIGNALS WATCHER || order {order_row.id} ({order_row.symbol}) advance failed: {e}")
            traceback.print_exc()

    account = metasignals_paper_engine.get_or_init_paper_account(db)
    metasignals_paper_engine.refresh_equity(db, account, mark_price_by_symbol)
    db.commit()


async def run_one_tick() -> None:
    """Self-managing wrapper (own SessionLocal(), own commit/rollback/
    close) -- what run_metasignals_watch_loop() actually calls every
    30s. Not unit-tested directly, matching alt_matrix_engine.py's own
    convention of only testing the inner, db-accepting helpers; tests
    exercise run_tick(db) instead."""
    db = None
    try:
        db = SessionLocal()
        await run_tick(db)
    except Exception as e:
        if db is not None:
            db.rollback()
        print(f"|| METASIGNALS WATCHER TICK ERROR: {e}")
        traceback.print_exc()
    finally:
        if db is not None:
            db.close()


async def run_metasignals_watch_loop() -> None:
    """Background task -- registered in main.py's lifespan()."""
    print(">>> METASIGNALS WATCHER: Initializing (30s paper-position poll)...")
    while True:
        await run_one_tick()
        await asyncio.sleep(_POLL_SECONDS)
