# ledger_closing_engine.py
# ==============================================================================
# KABRODA TRADE-LIFECYCLE MONITOR  (W-9 replacement — 2026-06-11)
# OHLC detection upgrade — 2026-06-16
#
# Three-phase state machine.
#
# PHASE 1 — Pre-entry
#   Watches APPROVED records where entry_filled_at IS NULL.
#   Entry trigger not crossed before session_expires_at → EXPIRED / pnl=null.
#   CRITICAL: stop hit while entry_filled_at IS NULL is NOT a loss — it is still
#   EXPIRED. The phantom-loss trap required price to cross entry FIRST.
#
# PHASE 2 — In-trade (OHLC-based, bounded by next session open)
#   Runs only after entry_filled_at is set. Watches stop + T1 via 1m Kraken
#   OHLCV candles — NOT ticker snapshots. Filled trades are NOT clock-expired
#   at session_expires_at (3 PM ET). They run until stop or T1 is hit, or until
#   the NEXT session open (next day 8:30 AM ET) without resolution. The 3 PM
#   session_expires_at is the Phase 1 entry-window boundary only.
#
#   Stop-first rule on same-candle ambiguity (conservative). At 1m granularity
#   this requires a ~$1,690 intrabar range for BTC at current levels — rare.
#
#   Genuinely-unresolved case (neither stop nor T1 hit by next session open):
#   CLOSED_AT_EXPIRY / fractional R / target_hit="EXPIRY".
#
#   KNOWN LIMITATION R1 (minor, accounting): a trade that hits stop between
#   midnight UTC and next session open has closed_at on the following calendar
#   date. Grouping by campaign date_key (session label) is accurate; grouping
#   by closed_at::date will shift that outcome to the next day's audit bucket.
#
# PHASE 3 — Post-exit observation
#   After a T1 close, keeps observing until session_expires_at. Logs whether
#   price subsequently reached T2/T3 via max_target_reached / t2_reached /
#   t3_reached. Does NOT reopen the record or change status/pnl.
#   Uses MEXC live-price snapshot (acceptable for non-closing observation).
#
# Legacy-row safety: all existing rows have session_expires_at = NULL. Every
# phase query filters session_expires_at IS NOT NULL (Phase 1) or entry_filled_at
# IS NOT NULL (Phase 2). Phase 2 skips rows with session_expires_at=NULL (guard
# at top of loop) to avoid indefinite OHLC scanning of legacy data.
# ==============================================================================

import asyncio
from datetime import datetime, timedelta, timezone
import traceback
from typing import Optional

import ccxt.async_support as ccxt

from database import CampaignLog, SessionLocal
from session_manager import anchor_ts_for_utc_date, get_session_config

_ticker_exchange = ccxt.mexc({"enableRateLimit": True})
_ohlc_exchange   = ccxt.kraken({"enableRateLimit": True})

_TARGET_RANK = {"T1": 1, "T2": 2, "T3": 3}


async def _get_live_price(symbol: str) -> float:
    """MEXC snapshot — Phase 1 entry detection and Phase 3 T2/T3 observation only."""
    try:
        fmt = symbol if "/" in symbol else symbol.replace("USDT", "/USDT")
        ticker = await _ticker_exchange.fetch_ticker(fmt)
        return float(ticker["last"])
    except Exception as e:
        print(f"|| LIFECYCLE || Price fetch error {symbol}: {e}")
        return 0.0


async def _fetch_1m_since(symbol: str, since_ms: int, limit: int = 720) -> list:
    """
    Fetch 1m Kraken OHLCV candles from since_ms forward.
    720 candles = 12 hours; covers a full session plus overnight gap to next open.
    Returns list of dicts: ts (epoch ms), o, h, l, c.
    """
    try:
        fmt = symbol if "/" in symbol else symbol.replace("USDT", "/USDT")
        rows = await _ohlc_exchange.fetch_ohlcv(fmt, "1m", since=since_ms, limit=limit)
        return [
            {"ts": int(r[0]), "o": float(r[1]), "h": float(r[2]),
             "l": float(r[3]), "c": float(r[4])}
            for r in rows
        ]
    except Exception as e:
        print(f"|| LIFECYCLE || OHLC fetch error {symbol}: {e}")
        return []


def _next_session_open_utc(session_expires_at_utc: datetime) -> datetime:
    """
    Compute the next session open (8:30 AM ET) after session_expires_at.
    Adds 18h to session_expires_at (3 PM ET → 9 AM next day ET), then lets
    anchor_ts_for_utc_date snap to that day's 8:30 AM ET anchor.
    Example: 19:00 UTC (3 PM ET) + 18h = 13:00 UTC next day (9 AM ET).
    9 AM > 8:30 AM open → anchor returns that day's 8:30 AM ET. Correct.
    """
    config = get_session_config("us_ny_futures")
    probe = session_expires_at_utc + timedelta(hours=18)
    ts = anchor_ts_for_utc_date(config, probe)
    return datetime.fromtimestamp(ts, tz=timezone.utc)


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
    print(">>> TRADE-LIFECYCLE MONITOR: Initializing (W-9 engine, OHLC detection)...")

    while True:
        now_utc = datetime.now(timezone.utc)
        # Per-cycle price cache — avoids redundant API calls for same symbol (Phase 1/3)
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

            # ── PHASE 2: In-trade (OHLC-based detection) ─────────────────────
            # Filled trades run until stop/T1 is hit via 1m Kraken candle scan,
            # or until the next session open without resolution. The 3 PM ET
            # session_expires_at does NOT close filled trades — it is the Phase 1
            # entry-window boundary only. No more EXPIRED/null for filled rows.
            active = db.query(CampaignLog).filter(
                CampaignLog.mas_approval_status == "APPROVED",
                CampaignLog.closed_at.is_(None),
                CampaignLog.entry_filled_at.isnot(None),
                CampaignLog.is_canonical == True,
            ).all()

            for c in active:
                # Legacy-row guard: rows without session_expires_at have no
                # next-session-open anchor; skip to prevent indefinite scanning.
                if c.session_expires_at is None:
                    continue

                fill_ts_ms = max(
                    int(_as_utc(c.entry_filled_at).timestamp() * 1000),
                    int((now_utc - timedelta(minutes=710)).timestamp() * 1000),
                )
                candles = await _fetch_1m_since(c.symbol, since_ms=fill_ts_ms)

                if not candles:
                    continue

                closed = False

                # Scan chronologically. Stop-first on same-candle (conservative).
                for candle in candles:
                    if c.bias == "LONG":
                        hit_stop = candle["l"] <= c.stop_loss
                        hit_t1   = c.t1 is not None and candle["h"] >= c.t1
                    else:
                        hit_stop = candle["h"] >= c.stop_loss
                        hit_t1   = c.t1 is not None and candle["l"] <= c.t1

                    candle_ts = datetime.fromtimestamp(candle["ts"] / 1000, tz=timezone.utc)

                    if hit_stop:
                        c.status       = "CLOSED_LOSS"
                        c.realized_pnl = -1.0
                        c.target_hit   = "STOP"
                        c.closed_at    = candle_ts
                        closed = True
                        tag = " (same-candle, stop wins)" if hit_t1 else ""
                        print(f"|| LIFECYCLE P2 || {c.symbol} {c.bias} STOP{tag} {candle_ts}. -1R.")
                        break

                    if hit_t1:
                        c.status       = "CLOSED_WIN"
                        c.realized_pnl = 1.0
                        c.target_hit   = "T1"
                        c.max_target_reached = _advance_target(c.max_target_reached, "T1")
                        c.closed_at    = candle_ts
                        closed = True
                        print(f"|| LIFECYCLE P2 || {c.symbol} {c.bias} T1 {candle_ts}. +1R.")
                        break

                if closed:
                    db.commit()
                    continue

                # No stop/T1 hit yet — update T2/T3 high-water marks from
                # period extremes of all scanned candles.
                obs_changed = False
                if c.bias == "LONG":
                    obs_changed = _observe_targets(c, max(can["h"] for can in candles))
                elif c.bias == "SHORT":
                    obs_changed = _observe_targets(c, min(can["l"] for can in candles))

                # Genuinely-unresolved boundary: next session open reached with
                # no stop or T1 hit. Record fractional R from final candle close.
                next_open = _next_session_open_utc(_as_utc(c.session_expires_at))
                if now_utc >= next_open:
                    final_close = candles[-1]["c"]
                    if c.bias == "LONG":
                        frac_r = round(
                            (final_close - c.entry_price) / (c.entry_price - c.stop_loss), 4
                        )
                    else:
                        frac_r = round(
                            (c.entry_price - final_close) / (c.stop_loss - c.entry_price), 4
                        )
                    c.status       = "CLOSED_AT_EXPIRY"
                    c.realized_pnl = frac_r
                    c.target_hit   = "EXPIRY"
                    c.closed_at    = now_utc
                    db.commit()
                    print(f"|| LIFECYCLE P2 || {c.symbol} CLOSED_AT_EXPIRY (next session open). R={frac_r:+.4f}.")
                    continue

                if obs_changed:
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
