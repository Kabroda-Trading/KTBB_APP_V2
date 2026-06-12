# ledger_closing_engine.py
# ==============================================================================
# KABRODA TRADE-LIFECYCLE MONITOR  (W-9 replacement — 2026-06-11)
#
# Three-phase state machine. Replaces the original ledger engine that had no
# entry-fill check and hardcoded ±1R PnL — causing phantom losses on untriggered
# setups (confirmed Jun-7 and Jun-10).
#
# PHASE 1 — Pre-entry
#   Watches APPROVED records where entry_filled_at IS NULL.
#   Entry trigger not crossed before session_expires_at → EXPIRED / pnl=null.
#   CRITICAL: stop hit while entry_filled_at IS NULL is NOT a loss — it is still
#   EXPIRED. The phantom-loss trap required price to cross entry FIRST.
#
# PHASE 2 — In-trade
#   Runs only after entry_filled_at is set. Watches stop + T1 (exit), T2/T3
#   (observation only — V1 exits at T1). True R recorded (1.0 / -1.0), not
#   hardcoded ±1 on a record that may never have been entered.
#
#   KNOWN LIMITATION — intra-interval ordering: if stop and T1 are both touched
#   within a single 60s poll, stop is assumed (conservative). True ordering
#   requires OHLC candle inspection. Acceptable for V1; flag for future fix.
#
# PHASE 3 — Post-exit observation
#   After a T1 close, keeps observing until session_expires_at. Logs whether
#   price subsequently reached T2/T3 via max_target_reached / t2_reached /
#   t3_reached. Does NOT reopen the record or change status/pnl.
#   Data foundation for future target-optimisation ("called T1, hit T3 on 80%").
#
# KNOWN LIMITATION — poll-based entry detection: a fast wick through the entry
# trigger between two 60s polls is not observed, and the setup expires as if
# unfilled. Acceptable for V1. Future fix: supplement with OHLC lookback on each
# poll to catch wicks the live-price snapshot missed.
#
# Legacy-row safety: all existing rows have session_expires_at = NULL. Every
# phase query filters session_expires_at IS NOT NULL (Phase 1) or entry_filled_at
# IS NOT NULL (Phase 2), so no pre-monitor row is touched until Step 5 backfill.
# ==============================================================================

import asyncio
from datetime import datetime, timezone
import traceback
from typing import Optional

import ccxt.async_support as ccxt

from database import CampaignLog, SessionLocal

_ticker_exchange = ccxt.mexc({"enableRateLimit": True})

_TARGET_RANK = {"T1": 1, "T2": 2, "T3": 3}


async def _get_live_price(symbol: str) -> float:
    try:
        fmt = symbol if "/" in symbol else symbol.replace("USDT", "/USDT")
        ticker = await _ticker_exchange.fetch_ticker(fmt)
        return float(ticker["last"])
    except Exception as e:
        print(f"|| LIFECYCLE || Price fetch error {symbol}: {e}")
        return 0.0


def _as_utc(dt: datetime) -> datetime:
    """Ensure datetime is UTC-aware. PostgreSQL returns naive UTC on read-back."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _advance_target(current: Optional[str], candidate: str) -> str:
    """Return the further target. T1 < T2 < T3. Never regresses."""
    if _TARGET_RANK.get(candidate, 0) > _TARGET_RANK.get(current or "", 0):
        return candidate
    return current or candidate


def _observe_targets(c: CampaignLog, live: float) -> bool:
    """
    Update t2_reached, t3_reached, max_target_reached based on live price.
    Returns True if any column changed (caller should commit).
    Does NOT close the record — observation only.

    Null t2/t3 values: skipped (guard for records where MAS output was partial).
    Null t2_reached/t3_reached: treated as False (nullable columns from production).
    """
    changed = False
    is_long = c.bias == "LONG"

    if c.t3 is not None:
        t3_hit = live >= c.t3 if is_long else live <= c.t3
        if t3_hit and not (c.t3_reached or False):
            c.t3_reached = True
            c.t2_reached = True  # T3 implies T2 was cleared
            c.max_target_reached = _advance_target(c.max_target_reached, "T3")
            changed = True

    if c.t2 is not None:
        t2_hit = live >= c.t2 if is_long else live <= c.t2
        if t2_hit and not (c.t2_reached or False):
            c.t2_reached = True
            c.max_target_reached = _advance_target(c.max_target_reached, "T2")
            changed = True

    return changed


async def run_ledger_audit_loop():
    print(">>> TRADE-LIFECYCLE MONITOR: Initializing (W-9 three-phase engine)...")

    while True:
        now_utc = datetime.now(timezone.utc)
        # Per-cycle price cache — avoids redundant API calls for same symbol
        price_cache: dict = {}
        db = SessionLocal()

        try:
            # ── PHASE 1: Pre-entry ────────────────────────────────────────────
            # Records that are APPROVED, open, and not yet filled.
            # session_expires_at IS NOT NULL guard: legacy rows (null expiry)
            # are skipped entirely until the Step 5 historical backfill.
            pending = db.query(CampaignLog).filter(
                CampaignLog.mas_approval_status == "APPROVED",
                CampaignLog.closed_at.is_(None),
                CampaignLog.entry_filled_at.is_(None),
                CampaignLog.session_expires_at.isnot(None),
                CampaignLog.is_canonical == True,
            ).all()

            for c in pending:
                expires = _as_utc(c.session_expires_at)

                if now_utc >= expires:
                    # Session over — entry never triggered. EXPIRED, not a loss.
                    # This fires whether or not price hit the stop: entry_filled_at
                    # IS NULL means we were never in the trade.
                    c.status = "EXPIRED"
                    c.closed_at = now_utc
                    c.realized_pnl = None
                    db.commit()
                    print(f"|| LIFECYCLE P1 || {c.symbol} EXPIRED — session closed, entry never triggered.")
                    continue

                if c.symbol not in price_cache:
                    price_cache[c.symbol] = await _get_live_price(c.symbol)
                live = price_cache[c.symbol]
                if live == 0.0:
                    continue

                filled = (
                    (c.bias == "LONG" and live >= c.entry_price)
                    or (c.bias == "SHORT" and live <= c.entry_price)
                )
                if filled:
                    c.entry_filled_at = now_utc
                    db.commit()
                    print(f"|| LIFECYCLE P1 || {c.symbol} ENTRY FILL observed at {live:.2f}. Entering Phase 2.")

            # ── PHASE 2: In-trade ─────────────────────────────────────────────
            # Only records where entry has been confirmed.
            active = db.query(CampaignLog).filter(
                CampaignLog.mas_approval_status == "APPROVED",
                CampaignLog.closed_at.is_(None),
                CampaignLog.entry_filled_at.isnot(None),
                CampaignLog.is_canonical == True,
            ).all()

            for c in active:
                # Session expiry while in-trade (entered but no outcome before close)
                if c.session_expires_at is not None:
                    if now_utc >= _as_utc(c.session_expires_at):
                        c.status = "EXPIRED"
                        c.closed_at = now_utc
                        c.realized_pnl = None
                        db.commit()
                        print(f"|| LIFECYCLE P2 || {c.symbol} EXPIRED — session closed while in-trade.")
                        continue

                if c.symbol not in price_cache:
                    price_cache[c.symbol] = await _get_live_price(c.symbol)
                live = price_cache[c.symbol]
                if live == 0.0:
                    continue

                # Observe T2/T3 high-water mark (non-closing)
                obs_changed = _observe_targets(c, live)
                closed = False

                # Stop check runs before T1 check (conservative).
                # If both stop and T1 are touched in the same 60s interval,
                # stop wins. True ordering requires OHLC — V1 known limitation.
                if c.bias == "LONG":
                    if live <= c.stop_loss:
                        c.status = "CLOSED_LOSS"
                        c.realized_pnl = -1.0
                        c.target_hit = "STOP"
                        c.closed_at = now_utc
                        closed = True
                        print(f"|| LIFECYCLE P2 || {c.symbol} LONG STOP at {live:.2f}. −1R.")
                    elif c.t1 is not None and live >= c.t1:
                        c.status = "CLOSED_WIN"
                        c.realized_pnl = 1.0
                        c.target_hit = "T1"
                        c.max_target_reached = _advance_target(c.max_target_reached, "T1")
                        c.closed_at = now_utc
                        closed = True
                        print(f"|| LIFECYCLE P2 || {c.symbol} LONG T1 at {live:.2f}. +1R.")

                elif c.bias == "SHORT":
                    if live >= c.stop_loss:
                        c.status = "CLOSED_LOSS"
                        c.realized_pnl = -1.0
                        c.target_hit = "STOP"
                        c.closed_at = now_utc
                        closed = True
                        print(f"|| LIFECYCLE P2 || {c.symbol} SHORT STOP at {live:.2f}. −1R.")
                    elif c.t1 is not None and live <= c.t1:
                        c.status = "CLOSED_WIN"
                        c.realized_pnl = 1.0
                        c.target_hit = "T1"
                        c.max_target_reached = _advance_target(c.max_target_reached, "T1")
                        c.closed_at = now_utc
                        closed = True
                        print(f"|| LIFECYCLE P2 || {c.symbol} SHORT T1 at {live:.2f}. +1R.")

                if closed or obs_changed:
                    db.commit()

            # ── PHASE 3: Post-exit observation ────────────────────────────────
            # T1-closed records whose session is still running. Keep watching
            # T2/T3 to build target-optimisation data. No status/pnl changes.
            post_exit = db.query(CampaignLog).filter(
                CampaignLog.mas_approval_status == "APPROVED",
                CampaignLog.closed_at.isnot(None),
                CampaignLog.target_hit == "T1",
                CampaignLog.session_expires_at.isnot(None),
                CampaignLog.is_canonical == True,
            ).all()

            for c in post_exit:
                if now_utc >= _as_utc(c.session_expires_at):
                    continue  # Session over, nothing more to observe

                if c.symbol not in price_cache:
                    price_cache[c.symbol] = await _get_live_price(c.symbol)
                live = price_cache[c.symbol]
                if live == 0.0:
                    continue

                if _observe_targets(c, live):
                    db.commit()
                    print(f"|| LIFECYCLE P3 || {c.symbol} post-T1 target observation updated.")

        except Exception as e:
            print(f"|| LIFECYCLE MONITOR ERROR || {e}")
            traceback.print_exc()
        finally:
            db.close()

        await asyncio.sleep(60)
