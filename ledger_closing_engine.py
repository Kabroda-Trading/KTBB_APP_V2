# ledger_closing_engine.py
# ==============================================================================
# KABRODA TRADE-LIFECYCLE MONITOR  (W-9 replacement — 2026-06-11)
# OHLC detection upgrade — 2026-06-16
# Phase 4 candidate monitoring — 2026-06-30
# Phase 3B shadow runner tracking (15M, EMA-based) — 2026-07-06, SUPERSEDED
# Phase 4B shadow runner tracking (4H/1H, zone-based) — 2026-07-07
# Real 30/70 runner mechanic (15M, fixed runner-stop) — 2026-08-30, LIVE
#
# PHASE 1 — Pre-entry
#   Watches APPROVED records where entry_filled_at IS NULL.
#   Entry trigger not crossed before session_expires_at → EXPIRED / pnl=null.
#   CRITICAL: stop hit while entry_filled_at IS NULL is NOT a loss — it is still
#   EXPIRED. The phantom-loss trap required price to cross entry FIRST.
#
# PHASE 2 — In-trade (OHLC-based, bounded by next session open), two legs
#   Runs only after entry_filled_at is set. Watches 1m Kraken OHLCV candles —
#   NOT ticker snapshots. Filled trades are NOT clock-expired at
#   session_expires_at (3 PM ET) — they run until fully resolved or until the
#   NEXT session open (next day 8:30 AM ET) without resolution. The 3 PM
#   session_expires_at is the Phase 1 entry-window boundary only.
#
#   LEG 1 (100% of position, until T1): original stop vs T1.
#     Stop hit first → CLOSED_LOSS, -1R, terminal.
#     T1 hit first → NOT terminal (2026-08-30 rebuild). 30% of the position is
#       realized at T1's R (t1_leg_r = 0.30 * that R); the stop for the
#       remaining 70% moves to a FIXED runner-stop = entry -+ 0.15*box
#       (KABRODA_REBUILD_SPEC.md SS6, box derived exactly from t2 = trigger +
#       1.0*box). runner_active flips True and LEG 2 begins, in the same
#       candle-scan pass if the batch has more candles left.
#     Legacy/partial rows missing t2 or t3 (nothing to derive a runner-stop
#     from) fall back to the pre-2026-08-30 terminal-at-T1 close instead of
#     crashing the poll loop.
#
#   LEG 2 (runner, 70% of position, only after T1): fixed runner-stop vs T3.
#     Runner-stop hit → CLOSED_LOSS, target_hit="RUNNER_STOP", blended R =
#       t1_leg_r + 0.70 * (runner-stop's R). Usually a small net loss or
#       near-breakeven, not the old flat -1.0 -- 30% already banked at T1.
#     T3 hit → CLOSED_WIN, target_hit="T3", blended R = t1_leg_r + 0.70 *
#       (T3's R). This IS the validated management rule -- 30% off at T1,
#       stop to the runner-stop level, 70% rides to T3 -- tested against
#       both 50/50 and 100%-at-T1 alternatives and beat both. See the
#       CampaignLog model's "RUNNER MECHANIC (LIVE)" comment in database.py.
#
#   Stop-first rule on same-candle ambiguity (conservative), on BOTH legs.
#   At 1m granularity this requires a large intrabar range — rare.
#
#   Genuinely-unresolved case (neither leg's exit hit by next session open):
#   CLOSED_AT_EXPIRY / fractional R (blended with t1_leg_r if the runner leg
#   was the one still open) / target_hit="EXPIRY".
#
#   Cross-poll continuity: each poll re-fetches candles from entry_filled_at
#   (capped to a rolling 710min window), so a batch can re-include candles
#   from BEFORE T1 touched even after runner_active is already True. Leg 2's
#   scan filters to candles at/after runner_started_at so a stale early
#   candle can never spuriously match runner_stop/T3 against irrelevant
#   pre-T1 history.
#
#   KNOWN LIMITATION R1 (minor, accounting): a trade that hits stop between
#   midnight UTC and next session open has closed_at on the following calendar
#   date. Grouping by campaign date_key (session label) is accurate; grouping
#   by closed_at::date will shift that outcome to the next day's audit bucket.
#
# PHASE 3 — Post-exit observation (now effectively legacy-only)
#   Watches rows with closed_at set AND target_hit=="T1" for continued T2/T3
#   observation. Under the pre-2026-08-30 model T1 was always terminal, so
#   this had live rows to watch; under the current model T1 is never terminal
#   for a row with t2/t3 populated (it becomes a runner instead), so this
#   phase naturally stops finding new matches going forward. Left as-is —
#   still correct for any pre-existing T1-closed legacy rows still open.
#
# PHASE 3B — Shadow runner tracking (2026-07-06, 15M only, RECORD-ONLY,
# SUPERSEDED 2026-08-30)
#   Modeled "close 50% at T1, run the rest" (a 15m-EMA-trailing stop) as a
#   record-only comparison against a ledger that closed 100% at T1 — an
#   exploration of a DIFFERENT split and a DIFFERENT stop style than what
#   KABRODA_REBUILD_SPEC.md SS6 later validated (30/70, fixed runner-stop).
#   No longer seeded at new T1 touches (Phase 2's real runner mechanic now
#   owns that event) — this phase's own scan/resolve logic is untouched and
#   still correctly finishes off any rows already shadow-active from before
#   the cutover, then goes dormant for good.
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
#   cap measured from the real T1 close. Untouched by the 2026-08-30 change
#   (4H/1H candidate creation is retired; only legacy rows still resolve here).
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

from database import CampaignLog, GravityMemory, SessionLocal, GateLog, TradePlan
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


def _backfill_gate_log(db, now_utc: datetime) -> None:
    """KABRODA_REBUILD_SPEC.md §9 -- fills in what actually happened for each
    TAKE_PREMIUM/TAKE_STANDARD gate_log row once its matching CampaignLog
    record resolves. Deliberately reuses CampaignLog's already-verified
    close-detection instead of re-scanning candles from scratch -- every
    15M decision (TAKE or PASS) already upserts a CampaignLog row
    (_inject_brief_to_database, unconditional), so matching by
    (symbol, date_key) is reliable, not a guess.

    Honest scope: this fills first_target_hit / stopped_first / r_t1only /
    mgmt_label, all directly available from CampaignLog's own resolved
    fields, plus faked_first (2026-08-31 -- pulled from the matching
    TradePlan row now that trade_plan.py's advance_waiting_plan() actually
    computes it; previously hardcoded None here). bars_to_t1/t2/t3,
    r_runner, and mfe_r are still NOT computed here -- they need a real
    candle-level reconstruction pass this function doesn't do. Left null
    rather than faked; a genuine gap, not a guessed value with a caveat
    stapled on. TradePlan's OWN execution-layer fields (entry_mode,
    fill_time/price, execution stop, re-entry) are backfilled separately
    by _backfill_gate_log_execution() below, gated on TradePlan reaching
    DONE rather than on CampaignLog -- the two records don't always
    resolve on the same timeline (a re-entry can still be open after
    CampaignLog has long since closed, or vice versa).
    """
    pending = db.query(GateLog).filter(
        GateLog.state.in_(["TAKE_PREMIUM", "TAKE_STANDARD"]),
        GateLog.backfilled_at.is_(None),
    ).all()
    if not pending:
        return

    for row in pending:
        campaign = db.query(CampaignLog).filter(
            CampaignLog.symbol == row.symbol,
            CampaignLog.date_key == row.date_key,
            CampaignLog.is_canonical == True,
        ).order_by(CampaignLog.id.desc()).first()

        if not campaign:
            continue  # no matching record yet -- try again next tick

        if campaign.closed_at is None:
            # Still open. Only give up (and record a null/unknown outcome)
            # once the session itself is long past due -- don't backfill a
            # live trade as if it were resolved.
            expires = getattr(campaign, "session_expires_at", None)
            if expires is None or _as_utc(expires) > now_utc:
                continue
            if (now_utc - _as_utc(expires)) < timedelta(hours=6):
                continue  # give the lifecycle monitor a little more room first

        row.first_target_hit = {"T1": "T1", "T2": "T2", "T3": "T3"}.get(campaign.target_hit)
        row.stopped_first = campaign.status == "CLOSED_LOSS"
        # faked_first is stable from the moment of first fill onward (it
        # never changes after), so it's safe to pull here even though this
        # backfill is gated on CampaignLog, not TradePlan, resolving.
        plan_row = db.query(TradePlan).filter(
            TradePlan.symbol == row.symbol, TradePlan.date_key == row.date_key,
        ).order_by(TradePlan.id.desc()).first()
        row.faked_first = plan_row.faked_first if plan_row else None
        row.r_t1only = campaign.realized_pnl
        row.mgmt_label = campaign.status or "UNRESOLVED"
        row.backfilled_at = now_utc
        print(f"|| GATE LOG BACKFILL || {row.symbol} {row.date_key} -> {row.mgmt_label} "
              f"(R={row.r_t1only})" if row.r_t1only is not None else
              f"|| GATE LOG BACKFILL || {row.symbol} {row.date_key} -> {row.mgmt_label}")

    db.commit()


def _backfill_gate_log_execution(db, now_utc: datetime) -> None:
    """KABRODA_COM_TRADE_PLAN_SPEC.md SS9a -- fills GateLog's execution_*
    columns from the matching TradePlan row once THAT row itself reaches a
    terminal state (DONE, or NO_PLAN which is already terminal at
    creation). Deliberately a separate pass with its own
    execution_backfilled_at flag, not folded into _backfill_gate_log()
    above: TradePlan and CampaignLog do not always resolve on the same
    timeline (a re-entry can still be open well after CampaignLog has
    closed on its own tighter stop, or vice versa), so gating this on
    CampaignLog's resolution could permanently strand these columns at
    None for any row where TradePlan finishes later.

    reentry_R is deliberately not filled -- see trade_plan.py's
    resolve_reentry_fill(): a re-entry's own runner/T3 outcome isn't
    tracked anywhere (CampaignLog has no re-entry concept, and TradePlan
    doesn't re-scan for it either). A genuine, documented gap.
    """
    pending = db.query(GateLog).filter(
        GateLog.execution_backfilled_at.is_(None),
    ).all()
    if not pending:
        return

    for row in pending:
        plan_row = (
            db.query(TradePlan)
            .filter(TradePlan.symbol == row.symbol, TradePlan.date_key == row.date_key)
            .order_by(TradePlan.id.desc())
            .first()
        )
        if not plan_row:
            continue  # no matching TradePlan row (yet, or ever) -- try again next tick

        if plan_row.status not in ("NO_PLAN", "DONE"):
            continue  # still in flight -- don't capture a mid-flight snapshot

        row.execution_entry_mode = plan_row.entry_mode
        row.execution_fill_time = plan_row.fill_time
        row.execution_fill_price = plan_row.fill_price
        row.execution_stop_price = plan_row.stop_price
        row.execution_stop_basis = plan_row.stop_basis
        row.execution_stop_dist_atr = plan_row.stop_dist_atr
        row.reentry_used = plan_row.reentry_used
        row.execution_backfilled_at = now_utc
        print(f"|| GATE LOG EXECUTION BACKFILL || {row.symbol} {row.date_key} -> "
              f"TradePlan {plan_row.status} (fill={row.execution_fill_price}, "
              f"reentry={row.reentry_used})")

    db.commit()


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
                    try:
                        from harness.unified_audit_writer import backfill_decision_outcome as _dl_backfill
                        _dl_backfill(campaign_log_id=c.id, outcome_status="NO_TRIGGER", realized_r=None)
                    except Exception as _ae:
                        print(f"[UNIFIED AUDIT BACKFILL] Non-critical failure: {_ae}")
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

                # If the runner leg (below) is already active from a prior
                # poll, this batch was re-fetched from entry_filled_at (or the
                # rolling 710min window) and can include the SAME candles
                # already scanned before T1 touched -- filter to candles at or
                # after the T1 touch so a stale early candle can never
                # spuriously match runner_stop/T3 against irrelevant history.
                if c.runner_active and c.runner_started_at is not None:
                    _runner_start_ms = int(_as_utc(c.runner_started_at).timestamp() * 1000)
                    candles_to_scan = [cd for cd in candles if cd["ts"] >= _runner_start_ms]
                    if not candles_to_scan:
                        continue
                else:
                    candles_to_scan = candles

                closed = False
                is_long = c.bias == "LONG"

                # Scan chronologically. Stop-first on same-candle (conservative,
                # applies to both legs: original stop vs T1, and runner-stop vs T3).
                for candle in candles_to_scan:
                    candle_ts = datetime.fromtimestamp(candle["ts"] / 1000, tz=timezone.utc)

                    if not c.runner_active:
                        # ── LEG 1 (100% of position): original stop vs T1 ──
                        hit_stop = candle["l"] <= c.stop_loss if is_long else candle["h"] >= c.stop_loss
                        hit_t1   = c.t1 is not None and (candle["h"] >= c.t1 if is_long else candle["l"] <= c.t1)

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
                            c.max_target_reached = _advance_target(c.max_target_reached, "T1")
                            t1_r = _frac_r(c.entry_price, c.stop_loss, c.t1, is_long)

                            if c.t2 is None or c.t3 is None:
                                # Legacy/partial row -- no t2/t3 to derive a
                                # runner-stop from (box = |t2 - entry|).
                                # Fall back to the pre-2026-08-30 terminal-at-
                                # T1 behavior rather than crash on one bad row.
                                c.status       = "CLOSED_WIN"
                                c.realized_pnl = t1_r
                                c.target_hit   = "T1"
                                c.closed_at    = candle_ts
                                closed = True
                                print(f"|| LIFECYCLE P2 || {c.symbol} {c.bias} T1 {candle_ts}. {t1_r:+.4f}R (no t2/t3 -- legacy row, terminal close).")
                                break

                            # 30% off at T1, stop moves to the runner-stop
                            # level, 70% rides to T3 -- KABRODA_REBUILD_SPEC.md
                            # SS6, the validated management rule (beat both
                            # 50/50 and 100%-at-T1 in the calibration
                            # backtest). box is exact from t2 = trigger + 1.0*box.
                            box = abs(c.t2 - c.entry_price)
                            c.t1_leg_r          = 0.30 * t1_r
                            c.runner_active     = True
                            c.runner_stop       = c.entry_price - 0.15 * box if is_long else c.entry_price + 0.15 * box
                            c.runner_started_at = candle_ts
                            print(f"|| LIFECYCLE P2 || {c.symbol} {c.bias} T1 {candle_ts}. 30% @ {t1_r:+.4f}R (locked {c.t1_leg_r:+.4f}R). Runner stop {c.runner_stop:.2f}, riding to T3 {c.t3:.2f}.")
                            # Fall through in the SAME loop pass, on the SAME
                            # candles_to_scan list -- if the runner leg also
                            # resolves within this batch (a fast-moving day),
                            # catch it now instead of waiting a poll cycle.
                            continue

                    else:
                        # ── LEG 2 (runner, 70% of position): fixed runner-stop vs T3 ──
                        hit_runner_stop = candle["l"] <= c.runner_stop if is_long else candle["h"] >= c.runner_stop
                        hit_t3 = c.t3 is not None and (candle["h"] >= c.t3 if is_long else candle["l"] <= c.t3)

                        if hit_runner_stop:
                            runner_r = _frac_r(c.entry_price, c.stop_loss, c.runner_stop, is_long)
                            c.status       = "CLOSED_LOSS"
                            c.realized_pnl = c.t1_leg_r + 0.70 * runner_r
                            c.target_hit   = "RUNNER_STOP"
                            c.closed_at    = candle_ts
                            closed = True
                            tag = " (same-candle, runner-stop wins)" if hit_t3 else ""
                            print(f"|| LIFECYCLE P2 || {c.symbol} {c.bias} RUNNER STOP{tag} {candle_ts}. Blended {c.realized_pnl:+.4f}R (30%@{c.t1_leg_r:+.4f}R + 70%@{runner_r:+.4f}R).")
                            break

                        if hit_t3:
                            t3_r = _frac_r(c.entry_price, c.stop_loss, c.t3, is_long)
                            c.status       = "CLOSED_WIN"
                            c.realized_pnl = c.t1_leg_r + 0.70 * t3_r
                            c.target_hit   = "T3"
                            c.max_target_reached = _advance_target(c.max_target_reached, "T3")
                            c.closed_at    = candle_ts
                            closed = True
                            print(f"|| LIFECYCLE P2 || {c.symbol} {c.bias} T3 {candle_ts}. Blended {c.realized_pnl:+.4f}R (30%@{c.t1_leg_r:+.4f}R + 70%@{t3_r:+.4f}R).")
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
                    try:
                        from harness.unified_audit_writer import backfill_decision_outcome as _dl_backfill
                        _dl_backfill(campaign_log_id=c.id, outcome_status=c.status, realized_r=c.realized_pnl)
                    except Exception as _ae:
                        print(f"[UNIFIED AUDIT BACKFILL] Non-critical failure: {_ae}")
                    continue

                # Neither leg resolved yet — update T2/T3 high-water marks
                # from period extremes of the candles just scanned.
                obs_changed = False
                if is_long:
                    obs_changed = _observe_targets(c, max(cd["h"] for cd in candles_to_scan))
                else:
                    obs_changed = _observe_targets(c, min(cd["l"] for cd in candles_to_scan))

                # Genuinely-unresolved boundary: next session open reached
                # with neither leg's exit condition hit. Record fractional R
                # from the final close, blended with the T1 leg's already-
                # locked-in 30% if the runner leg was the one still open.
                next_open = _next_session_open_utc(_as_utc(c.session_expires_at))
                if now_utc >= next_open:
                    final_close = candles_to_scan[-1]["c"]
                    if c.runner_active:
                        runner_frac_r = _frac_r(c.entry_price, c.stop_loss, final_close, is_long)
                        frac_r = c.t1_leg_r + 0.70 * runner_frac_r
                    else:
                        frac_r = _frac_r(c.entry_price, c.stop_loss, final_close, is_long)
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
                    try:
                        from harness.unified_audit_writer import backfill_decision_outcome as _dl_backfill
                        _dl_backfill(campaign_log_id=c.id, outcome_status="CLOSED_AT_EXPIRY", realized_r=frac_r)
                    except Exception as _ae:
                        print(f"[UNIFIED AUDIT BACKFILL] Non-critical failure: {_ae}")
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
                        try:
                            from harness.unified_audit_writer import backfill_decision_outcome as _dl_backfill
                            _dl_backfill(campaign_log_id=c.id, outcome_status="NO_TRIGGER", realized_r=None)
                        except Exception as _ae:
                            print(f"[UNIFIED AUDIT BACKFILL] Non-critical failure: {_ae}")
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
                    try:
                        from harness.unified_audit_writer import backfill_decision_outcome as _dl_backfill
                        _dl_backfill(campaign_log_id=c.id, outcome_status=c.status, realized_r=c.realized_pnl)
                    except Exception as _ae:
                        print(f"[UNIFIED AUDIT BACKFILL] Non-critical failure: {_ae}")
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
                    try:
                        from harness.unified_audit_writer import backfill_decision_outcome as _dl_backfill
                        _dl_backfill(campaign_log_id=c.id, outcome_status="CLOSED_AT_EXPIRY", realized_r=frac_r)
                    except Exception as _ae:
                        print(f"[UNIFIED AUDIT BACKFILL] Non-critical failure: {_ae}")
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

            # ── PHASE 5: gate_log backfill (non-blocking) ────────────────
            # KABRODA_REBUILD_SPEC.md §9. Kept isolated with its own
            # try/except so a failure here never breaks the CampaignLog
            # lifecycle phases above it.
            try:
                _backfill_gate_log(db, now_utc)
            except Exception as _ge:
                print(f"[GATE LOG BACKFILL] Non-critical failure: {_ge}")

            try:
                _backfill_gate_log_execution(db, now_utc)
            except Exception as _gee:
                print(f"[GATE LOG EXECUTION BACKFILL] Non-critical failure: {_gee}")

        except Exception as e:
            print(f"|| LIFECYCLE MONITOR ERROR || {e}")
            traceback.print_exc()
        finally:
            db.close()

        await asyncio.sleep(60)
