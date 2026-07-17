# ledger_closing_engine.py
# ==============================================================================
# KABRODA TRADE-LIFECYCLE MONITOR  (W-9 replacement — 2026-06-11)
# OHLC detection upgrade — 2026-06-16
# Phase 4 candidate monitoring — 2026-06-30
# Phase 3B shadow runner tracking (15M, EMA-based) — 2026-07-06
# Phase 4B shadow runner tracking (4H/1H, zone-based) — 2026-07-07
#
# Six-phase state machine (four real phases + two shadow/record-only phases).
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
# PHASE 3B — Shadow runner tracking (2026-07-06, 15M only, RECORD-ONLY)
#   Seeded by Phase 2's T1-hit branch (shadow_runner_active=True, shadow_runner_
#   stop=entry_price). Independently walks 1m candles forward (own last-scan
#   watermark, own query, decoupled from real closed_at/status) trailing a
#   stop toward a 15m EMA21 -- ratcheted only in the favorable direction, never
#   below breakeven -- and resolving at a stop touch, a T3 touch, or a 5-day
#   time cap. Models "close 50% at T1, run the rest" as a blended-R value
#   (shadow_runner_blended_r) for comparison against the real, already-
#   recorded realized_pnl. Never touches status/realized_pnl/closed_at.
#
# PHASE 4B -- Shadow runner tracking (2026-07-07, 4H/1H, RECORD-ONLY)
#   The 4H/1H counterpart to Phase 3B. Seeded by Phase 4's T1-hit branch.
#   Trails the shadow stop using gravity_memory structural zones (nearest
#   DEMAND zone below price for LONG / nearest SUPPLY zone above price for
#   SHORT, ratcheted only favorably) instead of an EMA -- 4H/1H moves too
#   slowly for a 15m-style EMA trail to be the right tool, per the original
#   master plan's own Component 4 spec. Reuses the same shadow_runner_*
#   columns as Phase 3B (mutually exclusive populations by query filter, so
#   no schema conflict); T3 stays the fixed v4 Fibonacci target -- only the
#   stop trails. Resolves at stop touch, T3 touch, or a 5d(4H)/2d(1H) time
#   cap measured from the real T1 close.
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

from database import CampaignLog, GravityMemory, SessionLocal
from session_manager import anchor_ts_for_utc_date, get_session_config
import notify

# Exhaustion monitor (IMP-006) — in-trade runner exhaustion detection
from monitoring.exhaustion_monitor import check_exhaustion

# 5m candle cache for exhaustion monitor — refreshed every 5 min per symbol
# to avoid redundant Kraken calls while giving PMARP/BBWP enough history.
import market_data
_exhaustion_5m_cache: dict = {}  # symbol -> {"candles": [...], "refreshed_at": float}
_EXHAUSTION_CACHE_TTL = 300.0  # 5 minutes

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


def _frac_r(entry_price: float, stop_loss: float, exit_price: float, is_long: bool) -> float:
    """
    True fractional R at close: (actual move) / (actual risk), direction-aware.

    Not always 1.0 on a T1 hit: the 15M system's actual stop (r30_low/high
    ATR/wall-adjusted, per trade_structure_analyst.py) is a different value
    than the raw trigger distance T1/T2/T3 are staged from, so entry-to-stop
    and entry-to-target distances are not guaranteed equal. A stop hit is
    still always exactly -1R by definition (R is defined relative to your
    own actual stop) -- only T1/T2/T3 hits need this computed, never assumed.

    risk floor matches the existing convention already used in the
    CLOSED_AT_EXPIRY branches (a zero-risk row is a data anomaly, not a
    real trading state -- this just prevents a crash, not a correct answer).
    """
    risk = max(abs(entry_price - stop_loss), 0.01)
    if risk <= 0.01:
        print(f"|| LIFECYCLE || _frac_r: near-zero risk (entry={entry_price}, stop={stop_loss}) -- data anomaly, R floored.")
    move = (exit_price - entry_price) if is_long else (entry_price - exit_price)
    return round(move / risk, 4)


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


def _floor_to_15m(ts_ms: int) -> int:
    """Floor an epoch-ms timestamp to the start of its containing 15-minute bucket."""
    bucket_ms = 15 * 60 * 1000
    return (ts_ms // bucket_ms) * bucket_ms


def _update_ema21(prev_ema: Optional[float], new_close: float, period: int = 21) -> float:
    """
    Incremental EMA update, one completed 15m bar at a time -- no look-ahead,
    since each call only ever uses a bar that has already fully closed.

    Seeded with the first bar's close rather than a full N-bar SMA warm-up
    (an acknowledged simplification for this shadow/trial feature -- the seed
    bias decays within roughly `period` bars, and this avoids needing to
    buffer 21 bars of history before the runner's trail can start moving).
    """
    if prev_ema is None:
        return new_close
    k = 2.0 / (period + 1)
    return prev_ema + k * (new_close - prev_ema)


def _nearest_zone_by_price(db, db_sym: str, source: str, level_type: str, price_filter, as_of_ts: datetime, ascending: bool):
    """
    Nearest active gravity_memory zone by PRICE (not recency) satisfying
    price_filter, as of a given moment in time (as_of_ts) -- deliberately
    different from _nearest_pivot_in_window() in gravity_engine.py, whose
    recency-first ordering is correct for stop SELECTION at trade
    construction (see item #1's grid-test finding) but wrong for trailing,
    where the closest-by-price zone in the direction of travel is what
    "structural support/resistance" actually means.

    timestamp <= as_of_ts is the no-look-ahead guard: a zone detected after
    the candle it's being tested against must not count, mirroring the same
    chronological discipline _update_ema21()'s caller applies for the 15M
    EMA trail.
    """
    q = db.query(GravityMemory).filter(
        GravityMemory.symbol == db_sym,
        GravityMemory.source == source,
        GravityMemory.level_type == level_type,
        GravityMemory.active == True,
        GravityMemory.timestamp <= as_of_ts,
        price_filter,
    )
    q = q.order_by(GravityMemory.price.asc() if ascending else GravityMemory.price.desc())
    return q.first()


def _notify_candidate_closed(c: CampaignLog) -> None:
    """
    Fires the admin close-email for a resolved 4H/1H candidate. Called at each
    Phase 4 resolution branch (CLOSED_WIN, CLOSED_LOSS, CLOSED_AT_EXPIRY,
    EXPIRED). Non-blocking — a failure here must never interrupt the caller's
    db.commit() or the lifecycle loop.
    """
    try:
        tf = c.session_timeframe or "?"
        duration = "unknown"
        if c.entry_filled_at and c.closed_at:
            delta = _as_utc(c.closed_at) - _as_utc(c.entry_filled_at)
            hours = delta.total_seconds() / 3600.0
            duration = f"{hours:.1f}h"
        pnl_str = f"{c.realized_pnl:+.4f}R" if c.realized_pnl is not None else "N/A"
        notify.send_admin_email(
            subject=f"KABRODA {tf} CANDIDATE CLOSED — {c.symbol} {c.status}",
            body=(
                f"Symbol: {c.symbol}\nTimeframe: {tf}\nBias: {c.bias}\n"
                f"Outcome: {c.status}\nRealized PnL: {pnl_str}\n"
                f"Time to resolve: {duration}\n"
                f"Entry: ${c.entry_price:.2f}\nStop: ${c.stop_loss:.2f}\nTarget: ${c.t1:.2f}"
            ),
        )
    except Exception as e:
        print(f"[NOTIFY ERROR] Close email failed for {c.symbol}: {e}")


async def run_ledger_audit_loop():
    print(">>> TRADE-LIFECYCLE MONITOR: Initializing (W-9 engine, OHLC detection, Phase 4 candidates, Phase 3B shadow runner)...")

    while True:
        # Health monitoring
        try:
            from main import scheduler_health_registry as _lhr
            _lhr["ledger_closing"]["last_run"] = datetime.now(timezone.utc).isoformat()
            _lhr["ledger_closing"]["status"] = "EXECUTING"
        except Exception:
            pass
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
                    # Forward-audit back-fill (Adj. 3: non-blocking)
                    try:
                        from harness.audit_writer import backfill_outcome as _backfill
                        _backfill(
                            symbol=c.symbol,
                            date_key=c.date_key,
                            session_id=c.session_id,
                            outcome_type="NO_TRIGGER",
                            realized_pnl_r=None,
                            resolution_notes="Session expired; entry price never triggered.",
                        )
                    except Exception as _ae:
                        print(f"[AUDIT BACKFILL] Non-critical failure: {_ae}")
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
                        r = _frac_r(c.entry_price, c.stop_loss, c.t1, c.bias == "LONG")
                        c.status       = "CLOSED_WIN"
                        c.realized_pnl = r
                        c.target_hit   = "T1"
                        c.max_target_reached = _advance_target(c.max_target_reached, "T1")
                        c.closed_at    = candle_ts
                        # Shadow-mode runner tracking (2026-07-06, 15M only, record-only) —
                        # seeds Phase 3B below. Real status/realized_pnl/closed_at above are
                        # completely unaffected by this.
                        c.shadow_runner_active = True
                        c.shadow_runner_stop = c.entry_price
                        c.shadow_runner_last_scan_ts = candle_ts
                        closed = True
                        print(f"|| LIFECYCLE P2 || {c.symbol} {c.bias} T1 {candle_ts}. {r:+.4f}R.")
                        break

                if closed:
                    db.commit()
                    # Forward-audit back-fill (Adj. 3: non-blocking — close path continues on any error)
                    try:
                        from harness.audit_writer import backfill_outcome as _backfill
                        _backfill(
                            symbol=c.symbol,
                            date_key=c.date_key,
                            session_id=c.session_id,
                            outcome_type=c.status,          # CLOSED_WIN or CLOSED_LOSS
                            realized_pnl_r=c.realized_pnl,
                        )
                    except Exception as _ae:
                        print(f"[AUDIT BACKFILL] Non-critical failure: {_ae}")
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
                    frac_r = _frac_r(c.entry_price, c.stop_loss, final_close, c.bias == "LONG")
                    c.status       = "CLOSED_AT_EXPIRY"
                    c.realized_pnl = frac_r
                    c.target_hit   = "EXPIRY"
                    c.closed_at    = now_utc
                    db.commit()
                    # Forward-audit back-fill (Adj. 3: non-blocking)
                    try:
                        from harness.audit_writer import backfill_outcome as _backfill
                        _backfill(
                            symbol=c.symbol,
                            date_key=c.date_key,
                            session_id=c.session_id,
                            outcome_type="CLOSED_AT_EXPIRY",
                            realized_pnl_r=frac_r,
                            resolution_notes="Reached next session open without hitting stop or T1.",
                        )
                    except Exception as _ae:
                        print(f"[AUDIT BACKFILL] Non-critical failure: {_ae}")
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

            # ── PHASE 3B: Shadow runner tracking (2026-07-06, 15M only) ──────
            # Seeded by Phase 2's T1-hit branch above (shadow_runner_active=True,
            # shadow_runner_stop=entry_price). Independent of Phase 3 -- Phase 3 is
            # ticker-only and time-boxed to the same session; this needs candle
            # precision over a multi-day window, and is decoupled from the real
            # closed_at/status exactly like Phase 3 already is.
            #
            # Walks 1m candles strictly chronologically so the trailing stop is
            # ratcheted using only 15m bars that have already closed as of the
            # candle being tested -- never a bar from that candle's own future
            # (the look-ahead trap: naively recomputing "the current EMA21" once
            # per tick and applying it retroactively across a re-scanned window
            # would test old candles against a stop level the EMA hadn't reached
            # yet at that historical moment).
            #
            # Real status/realized_pnl/closed_at are never touched here -- this
            # only fills shadow_runner_* columns, modeling what "close 50% at T1,
            # run the rest" would have produced, for review before ever
            # considering flipping this live.
            shadow_active = db.query(CampaignLog).filter(
                CampaignLog.session_timeframe == "15M",
                CampaignLog.is_canonical == True,
                CampaignLog.shadow_runner_active == True,
                CampaignLog.shadow_runner_closed_at.is_(None),
            ).all()

            for c in shadow_active:
                since_ms = int(_as_utc(c.shadow_runner_last_scan_ts or c.closed_at).timestamp() * 1000)
                candles = await _fetch_1m_since(c.symbol, since_ms=since_ms)
                if not candles:
                    continue

                # ── Exhaustion check (IMP-006) ─────────────────────────────
                # Fetch 5m candles with a 5-minute TTL cache to give PMARP/BBWP
                # enough history (272+ candles) without hammering Kraken.
                now_ts = datetime.now(timezone.utc).timestamp()
                cached = _exhaustion_5m_cache.get(c.symbol)
                if cached and (now_ts - cached["refreshed_at"]) < _EXHAUSTION_CACHE_TTL:
                    candles_5m = cached["candles"]
                else:
                    candles_5m = await market_data.fetch_live_5m(c.symbol, limit=300)
                    _exhaustion_5m_cache[c.symbol] = {"candles": candles_5m, "refreshed_at": now_ts}
                exhaustion = check_exhaustion(
                    candles_5m, candles_5m,
                    {"entry_price": c.entry_price, "current_stop": c.shadow_runner_stop, "direction": c.bias},
                )
                if exhaustion["level"] in ("WARNING", "EXIT"):
                    print(
                        f"|| LIFECYCLE P3B || {c.symbol} exhaustion: {exhaustion['level']} "
                        f"signals={[s['type'] for s in exhaustion['signals']]}"
                    )

                is_long = c.bias == "LONG"

                for candle in candles:
                    candle_ts = datetime.fromtimestamp(candle["ts"] / 1000, tz=timezone.utc)
                    bucket_ts_ms = _floor_to_15m(candle["ts"])
                    prev_bucket_ms = (
                        int(_as_utc(c.shadow_runner_bucket_ts).timestamp() * 1000)
                        if c.shadow_runner_bucket_ts is not None else None
                    )

                    # A new 15m bucket has started -- the previous one just closed.
                    # Finalize it into the running EMA and ratchet the stop BEFORE
                    # testing this candle for a touch (chronologically honest).
                    if prev_bucket_ms is not None and bucket_ts_ms > prev_bucket_ms:
                        c.shadow_runner_ema21 = _update_ema21(c.shadow_runner_ema21, c.shadow_runner_bucket_close)
                        if is_long:
                            c.shadow_runner_stop = max(c.shadow_runner_stop, c.shadow_runner_ema21)
                        else:
                            c.shadow_runner_stop = min(c.shadow_runner_stop, c.shadow_runner_ema21)

                    c.shadow_runner_bucket_ts = datetime.fromtimestamp(bucket_ts_ms / 1000, tz=timezone.utc)
                    c.shadow_runner_bucket_close = candle["c"]

                    if is_long:
                        hit_stop = candle["l"] <= c.shadow_runner_stop
                        hit_t3   = c.t3 is not None and candle["h"] >= c.t3
                    else:
                        hit_stop = candle["h"] >= c.shadow_runner_stop
                        hit_t3   = c.t3 is not None and candle["l"] <= c.t3

                    if hit_stop or hit_t3:
                        exit_price = c.shadow_runner_stop if hit_stop else c.t3
                        reason = "STOP" if hit_stop else "T3"
                        leg2_r = _frac_r(c.entry_price, c.stop_loss, exit_price, is_long)
                        c.shadow_runner_leg2_r = leg2_r
                        c.shadow_runner_blended_r = round(0.5 * c.realized_pnl + 0.5 * leg2_r, 4)
                        c.shadow_runner_exit_reason = reason
                        c.shadow_runner_closed_at = candle_ts
                        print(f"|| LIFECYCLE P3B || {c.symbol} shadow runner {reason} {candle_ts}. leg2={leg2_r:+.4f}R blended={c.shadow_runner_blended_r:+.4f}R.")
                        break

                    if (candle_ts - _as_utc(c.closed_at)) > timedelta(days=5):
                        leg2_r = _frac_r(c.entry_price, c.stop_loss, candle["c"], is_long)
                        c.shadow_runner_leg2_r = leg2_r
                        c.shadow_runner_blended_r = round(0.5 * c.realized_pnl + 0.5 * leg2_r, 4)
                        c.shadow_runner_exit_reason = "TIME_CAP"
                        c.shadow_runner_closed_at = candle_ts
                        print(f"|| LIFECYCLE P3B || {c.symbol} shadow runner TIME_CAP {candle_ts}. leg2={leg2_r:+.4f}R blended={c.shadow_runner_blended_r:+.4f}R.")
                        break

                    c.shadow_runner_last_scan_ts = candle_ts

                db.commit()

            # ── PHASE 4: Candidate monitoring (4H / 1H BOS candidates) ──────
            # CANDIDATE rows are written by gravity_engine._detect_4h/1h_bos()
            # with entry_filled_at=detection_time and session_expires_at=+5d/+2d.
            # They are never APPROVED so Phases 1-3 skip them. Phase 4 closes
            # them on stop/T1 hit (via OHLC) or time cap, recording outcomes so
            # the 4H/1H candidates are auditable in campaign_logs.
            candidates = db.query(CampaignLog).filter(
                CampaignLog.mas_approval_status.in_(["4H_CANDIDATE", "1H_CANDIDATE"]),
                CampaignLog.closed_at.is_(None),
                CampaignLog.entry_filled_at.isnot(None),
            ).all()

            for c in candidates:
                fill_ts_ms = max(
                    int(_as_utc(c.entry_filled_at).timestamp() * 1000),
                    int((now_utc - timedelta(minutes=710)).timestamp() * 1000),
                )
                candles = await _fetch_1m_since(c.symbol, since_ms=fill_ts_ms)

                if not candles:
                    if c.session_expires_at and now_utc >= _as_utc(c.session_expires_at):
                        c.status = "EXPIRED"
                        c.closed_at = now_utc
                        c.realized_pnl = None
                        db.commit()
                        print(f"|| LIFECYCLE P4 || {c.symbol} {c.mas_approval_status} EXPIRED (no candles).")
                        _notify_candidate_closed(c)
                    continue

                closed = False
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
                        print(f"|| LIFECYCLE P4 || {c.symbol} {c.mas_approval_status} STOP{tag} {candle_ts}. -1R.")
                        break

                    if hit_t1:
                        r = _frac_r(c.entry_price, c.stop_loss, c.t1, c.bias == "LONG")
                        c.status       = "CLOSED_WIN"
                        c.realized_pnl = r
                        c.target_hit   = "T1"
                        c.max_target_reached = _advance_target(c.max_target_reached, "T1")
                        c.closed_at    = candle_ts
                        # Shadow-mode runner tracking (2026-07-07, 4H/1H, record-only) —
                        # seeds Phase 4B below. Real status/realized_pnl/closed_at above
                        # are completely unaffected by this.
                        c.shadow_runner_active = True
                        c.shadow_runner_stop = c.entry_price
                        c.shadow_runner_last_scan_ts = candle_ts
                        closed = True
                        print(f"|| LIFECYCLE P4 || {c.symbol} {c.mas_approval_status} T1 {candle_ts}. {r:+.4f}R.")
                        break

                if closed:
                    db.commit()
                    _notify_candidate_closed(c)
                    continue

                # T2/T3 high-water mark update (non-closing observation)
                obs_changed = False
                if c.bias == "LONG":
                    obs_changed = _observe_targets(c, max(can["h"] for can in candles))
                elif c.bias == "SHORT":
                    obs_changed = _observe_targets(c, min(can["l"] for can in candles))

                # Time-cap expiry (5d for 4H, 2d for 1H — set at write time)
                if c.session_expires_at and now_utc >= _as_utc(c.session_expires_at):
                    final_close = candles[-1]["c"]
                    frac_r = _frac_r(c.entry_price, c.stop_loss, final_close, c.bias == "LONG")
                    c.status       = "CLOSED_AT_EXPIRY"
                    c.realized_pnl = frac_r
                    c.target_hit   = "EXPIRY"
                    c.closed_at    = now_utc
                    db.commit()
                    print(f"|| LIFECYCLE P4 || {c.symbol} {c.mas_approval_status} CLOSED_AT_EXPIRY. R={frac_r:+.4f}.")
                    _notify_candidate_closed(c)
                    continue

                if obs_changed:
                    db.commit()

            # ── PHASE 4B: Shadow runner tracking (2026-07-07, 4H/1H) ─────────
            # Seeded by Phase 4's T1-hit branch above (shadow_runner_active=True,
            # shadow_runner_stop=entry_price). Trails the shadow stop using
            # gravity_memory structural zones instead of an EMA -- 4H/1H moves
            # too slowly for a 15m-style EMA trail to be the right tool, per
            # the original master plan's own Component 4 spec. Reuses the same
            # shadow_runner_* columns as Phase 3B -- mutually exclusive
            # populations (this query filters mas_approval_status, Phase 3B
            # filters session_timeframe=="15M"), so no schema conflict.
            #
            # T3 stays the fixed v4 Fibonacci target -- only the stop trails,
            # same design choice as the 15M version. Real status/realized_pnl/
            # closed_at are never touched here.
            shadow_active_tf = db.query(CampaignLog).filter(
                CampaignLog.mas_approval_status.in_(["4H_CANDIDATE", "1H_CANDIDATE"]),
                CampaignLog.shadow_runner_active == True,
                CampaignLog.shadow_runner_closed_at.is_(None),
            ).all()

            for c in shadow_active_tf:
                since_ms = int(_as_utc(c.shadow_runner_last_scan_ts or c.closed_at).timestamp() * 1000)
                candles = await _fetch_1m_since(c.symbol, since_ms=since_ms)
                if not candles:
                    continue

                # ── Exhaustion check (IMP-006) ─────────────────────────────
                # Fetch 5m candles with a 5-minute TTL cache to give PMARP/BBWP
                # enough history (272+ candles) without hammering Kraken.
                now_ts = datetime.now(timezone.utc).timestamp()
                cached = _exhaustion_5m_cache.get(c.symbol)
                if cached and (now_ts - cached["refreshed_at"]) < _EXHAUSTION_CACHE_TTL:
                    candles_5m = cached["candles"]
                else:
                    candles_5m = await market_data.fetch_live_5m(c.symbol, limit=300)
                    _exhaustion_5m_cache[c.symbol] = {"candles": candles_5m, "refreshed_at": now_ts}
                exhaustion = check_exhaustion(
                    candles_5m, candles_5m,
                    {"entry_price": c.entry_price, "current_stop": c.shadow_runner_stop, "direction": c.bias},
                )
                if exhaustion["level"] in ("WARNING", "EXIT"):
                    print(
                        f"|| LIFECYCLE P4B || {c.symbol} {c.mas_approval_status} exhaustion: {exhaustion['level']} "
                        f"signals={[s['type'] for s in exhaustion['signals']]}"
                    )

                is_long = c.bias == "LONG"
                db_sym = c.symbol.replace("/", "")
                source = "4H_PIVOT" if c.session_timeframe == "4H" else "1H_PIVOT"
                time_cap = timedelta(days=5) if c.session_timeframe == "4H" else timedelta(days=2)

                for candle in candles:
                    candle_ts = datetime.fromtimestamp(candle["ts"] / 1000, tz=timezone.utc)

                    # Ratchet the shadow stop toward the nearest qualifying
                    # structural zone, only if it's strictly better than the
                    # current stop (the price_filter below enforces that) --
                    # never loosened. No qualifying zone this candle just
                    # means the stop is left exactly where it was.
                    if is_long:
                        zone = _nearest_zone_by_price(
                            db, db_sym, source, "DEMAND",
                            (GravityMemory.price < candle["c"]) & (GravityMemory.price > c.shadow_runner_stop),
                            candle_ts, ascending=False,
                        )
                    else:
                        zone = _nearest_zone_by_price(
                            db, db_sym, source, "SUPPLY",
                            (GravityMemory.price > candle["c"]) & (GravityMemory.price < c.shadow_runner_stop),
                            candle_ts, ascending=True,
                        )
                    if zone is not None:
                        c.shadow_runner_stop = zone.price

                    if is_long:
                        hit_stop = candle["l"] <= c.shadow_runner_stop
                        hit_t3   = c.t3 is not None and candle["h"] >= c.t3
                    else:
                        hit_stop = candle["h"] >= c.shadow_runner_stop
                        hit_t3   = c.t3 is not None and candle["l"] <= c.t3

                    if hit_stop or hit_t3:
                        exit_price = c.shadow_runner_stop if hit_stop else c.t3
                        reason = "STOP" if hit_stop else "T3"
                        leg2_r = _frac_r(c.entry_price, c.stop_loss, exit_price, is_long)
                        c.shadow_runner_leg2_r = leg2_r
                        c.shadow_runner_blended_r = round(0.5 * c.realized_pnl + 0.5 * leg2_r, 4)
                        c.shadow_runner_exit_reason = reason
                        c.shadow_runner_closed_at = candle_ts
                        print(f"|| LIFECYCLE P4B || {c.symbol} {c.mas_approval_status} shadow runner {reason} {candle_ts}. leg2={leg2_r:+.4f}R blended={c.shadow_runner_blended_r:+.4f}R.")
                        break

                    if (candle_ts - _as_utc(c.closed_at)) > time_cap:
                        leg2_r = _frac_r(c.entry_price, c.stop_loss, candle["c"], is_long)
                        c.shadow_runner_leg2_r = leg2_r
                        c.shadow_runner_blended_r = round(0.5 * c.realized_pnl + 0.5 * leg2_r, 4)
                        c.shadow_runner_exit_reason = "TIME_CAP"
                        c.shadow_runner_closed_at = candle_ts
                        print(f"|| LIFECYCLE P4B || {c.symbol} {c.mas_approval_status} shadow runner TIME_CAP {candle_ts}. leg2={leg2_r:+.4f}R blended={c.shadow_runner_blended_r:+.4f}R.")
                        break

                    c.shadow_runner_last_scan_ts = candle_ts

                db.commit()

        except Exception as e:
            print(f"|| LIFECYCLE MONITOR ERROR || {e}")
            traceback.print_exc()
        finally:
            db.close()

        await asyncio.sleep(60)
