# account_balance_snapshot.py
# ==============================================================================
# DESK-PAGE BALANCE/P&L REPORTING BUILD (2026-11-xx) -- a single background
# loop that periodically writes a real balance/equity reading for EVERY
# active ExecutorAccount (Bitunix-based Traveler/Alt Matrix accounts and
# Tradovate/CrossTrade-based CME accounts alike) into
# ExecutorAccountBalanceSnapshot, so the desk pages have a real dated
# history to chart instead of only ever showing a live-queried "right now"
# number.
#
# Deliberately NOT piggybacked onto any of the existing per-subsystem watch
# loops (executor_live_e1_engine.run_executor_live_e1_loop(),
# alt_matrix_engine.run_alt_matrix_watch_loop(), cme_engine.run_cme_watch_loop())
# -- every one of those iterates OPEN ORDERS, not accounts, so they all go
# silent exactly when an account is flat, which is precisely when a balance
# curve still needs a data point. This loop iterates accounts directly and
# never looks at order state at all.
#
# Reuses each subsystem's own already-existing balance-query function --
# executor_plan_builder._query_real_balance() for Bitunix accounts (covers
# Traveler and Alt Matrix in one call, since they share the same physical
# account), cme_executor._client_for(account).get_balance() for Tradovate/
# CrossTrade accounts. No new exchange-call code anywhere in this file.
#
# Per-account try/except, same discipline as every other loop in this
# codebase (alt_matrix_engine.py's watch loop, etc.): one account's failure
# must never skip the rest of the tick.
# ==============================================================================

from __future__ import annotations

import asyncio
import traceback

from database import SessionLocal, ExecutorAccount, ExecutorAccountBalanceSnapshot

_SNAPSHOT_INTERVAL_SECONDS = 900  # 15 min -- cheap enough for a background poll, frequent enough for a usable daily curve


async def _snapshot_one_account(db, account: ExecutorAccount) -> None:
    if (account.exchange or "").strip().lower() == "tradovate":
        import cme_executor
        client = cme_executor._client_for(account)
        bal = await client.get_balance()
        db.add(ExecutorAccountBalanceSnapshot(
            account_id=account.id,
            balance_usd=bal.get("cash_balance"),
            equity_usd=bal.get("total_equity"),
            source="SNAPSHOT_LOOP",
        ))
    else:
        import executor_plan_builder
        bal = await executor_plan_builder._query_real_balance(account)
        db.add(ExecutorAccountBalanceSnapshot(
            account_id=account.id,
            balance_usd=bal.get("balance_usd"),
            equity_usd=bal.get("balance_usd"),
            source=bal.get("source", "SNAPSHOT_LOOP"),
        ))


async def run_one_snapshot_tick(db) -> None:
    """One full tick's worth of work, extracted from run_balance_snapshot_
    loop() below so it's directly unit-testable without needing to drive
    the infinite loop. Iterates every is_active ExecutorAccount, writes one
    ExecutorAccountBalanceSnapshot row per account. Never raises -- a bad
    account or a down exchange must never stop the other accounts' writes
    in the same tick."""
    accounts = db.query(ExecutorAccount).filter_by(is_active=True).all()
    for account in accounts:
        try:
            await _snapshot_one_account(db, account)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"|| BALANCE SNAPSHOT || account {account.id} failed: {e}")


async def run_balance_snapshot_loop() -> None:
    """Background task (registered in main.py's lifespan())."""
    print(">>> ACCOUNT BALANCE SNAPSHOT LOOP: Initializing...")
    while True:
        db = None
        try:
            db = SessionLocal()
            await run_one_snapshot_tick(db)
        except Exception as e:
            print(f"|| BALANCE SNAPSHOT LOOP ERROR: {e}")
            traceback.print_exc()
        finally:
            if db is not None:
                db.close()

        await asyncio.sleep(_SNAPSHOT_INTERVAL_SECONDS)
