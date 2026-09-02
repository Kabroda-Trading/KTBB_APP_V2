# trade_plan_engine.py
# ==============================================================================
# TRADE PLAN INTRADAY MONITOR
# KABRODA_COM_TRADE_PLAN_SPEC.md SS5/SS7/SS8 -- the async driver for
# trade_plan.py's pure state-machine functions (advance_waiting_plan,
# advance_reentry_plan, check_wide_stop_or_t1, mirror_campaign_outcome,
# check_reentry_eligibility). Same relationship ledger_closing_engine.py
# has to CampaignLog's Phase 1/2 logic -- kept in its OWN file, not inside
# trade_plan.py, so trade_plan.py stays the pure, dependency-free,
# easily-tested module its own header describes ("no DB/network").
#
# One continuous asyncio task, registered once in main.py's lifespan() and
# alive for the process's whole life -- the same safe pattern
# ledger_closing_engine.py's own module-level ccxt usage relies on.
# Module-level/shared exchange clients are only unsafe when reused ACROSS
# event loops (market_data.py's 2026-08-30 fix, AGENT_LOG.md) -- this loop,
# like ledger_closing_engine's, never crosses loops, so market_data.py's
# fetch_live_5m() (already loop-safe) and ledger_closing_engine.py's own
# _fetch_1m_since() (module-level Kraken client, one continuous loop) are
# both reused directly rather than a third parallel client being stood up.
#
# Per-status routing, once per 60s poll cycle:
#   NO_PLAN           -> promote_no_plan_on_real_cross() (2026-09-02, Andy's
#                        poll-routing decision -- Kabroda AI Brain repo
#                        AGENT_LOG.md). Re-runs the REAL full gate
#                        (decision_engine.evaluate_15m_decision() via
#                        _run_full_gate(), same as the opposite-break/own-
#                        cross paths below -- NOT the pre-cross anticipate_
#                        setup() heuristic build_trade_plan() used at lock).
#                        A genuine later TAKE (fuel/HTF/reachability/hour
#                        all pass, no hard veto incl. counter-trend)
#                        promotes straight to FILLED -- FUELED collapses
#                        ARMED+FILLED the same way it does for the
#                        anticipated-side path, since a TAKE verdict already
#                        implies fuel=FUELED. Session expiry with no
#                        qualifying cross -> DONE, but WITHOUT an email
#                        (trade_plan_notify.py suppresses it -- the STAND
#                        DOWN lock email already told Andy nothing would
#                        follow unless a real cross changed it).
#   WAITING / VETOED -> advance_waiting_plan()   (5m candles + live price)
#   REENTRY_ARMED     -> advance_reentry_plan()   (5m candles)
#   FILLED            -> check_wide_stop_or_t1() first -- TradePlan's OWN
#                        wide stop, scanned against 1m candles since
#                        fill_time. Only a WIDE_STOP_FIRST verdict can move
#                        a FILLED plan to STOPPED (see trade_plan.py's
#                        2026-08-31 CORRECTION -- CampaignLog's own status
#                        cannot answer this, it tracks a different, tighter
#                        stop). Otherwise mirror_campaign_outcome() closes
#                        the plan to DONE once the matching CampaignLog row
#                        resolves on its own (win, its own tighter-stop
#                        loss, or expiry) -- no second T1/runner/T3 scan,
#                        that stays ledger_closing_engine.py's job.
#   STOPPED           -> re-checks fuel at the ORIGINAL trigger. A NO_PUSH
#                        read (price not currently back beyond trigger) is
#                        NOT a "no fuel" verdict -- it means the re-entry
#                        question hasn't been asked yet, so the row is left
#                        untouched to wait for a real cross. Only once
#                        price actually returns to the trigger does
#                        check_reentry_eligibility() get a real verdict.
#                        Session expiry with no qualifying cross -> DONE.
# ==============================================================================

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from database import SessionLocal, TradePlan, CampaignLog, SessionLock
import fuel_gate
import trade_plan as tp
import market_data
from ledger_closing_engine import _fetch_1m_since
from kabroda_mas_flow import _compute_session_expires_at

_POLL_SECONDS = 60


def _as_utc(dt):
    """PostgreSQL/SQLite can return naive UTC on read-back -- same helper
    as ledger_closing_engine.py's own _as_utc()."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def _run_full_gate(db, row: TradePlan) -> Optional[dict]:
    """Runs the REAL, unmodified decision_engine.evaluate_15m_decision()
    against the SessionLock's own locked levels + fresh candles -- exactly
    how DeepSeek's own incident reconstruction was done and how market_
    radar._build_dossier() already evaluates live crosses elsewhere.
    Shared by both call sites that need a full-gate read intraday (the
    opposite-break enrichment, and the anticipated side's own cross) so
    there is one real implementation, not two.

    Returns {"decision": ..., "levels": ...} or None on any failure/
    missing data (no SessionLock, no candles). Never raises.
    """
    lock = (
        db.query(SessionLock)
        .filter(
            SessionLock.symbol == row.symbol,
            SessionLock.session_id == row.session_id,
            SessionLock.date_key == row.date_key,
        )
        .first()
    )
    if lock is None:
        return None
    try:
        levels = dict(json.loads(lock.packet_data).get("levels", {}))
    except Exception:
        return None

    candles_5m, candles_15m, candles_1h, candles_4h, candles_1d = await asyncio.gather(
        market_data.fetch_live_5m(row.symbol, limit=400),
        market_data.fetch_live_15m(row.symbol, limit=300),
        market_data.fetch_live_1h(row.symbol, limit=100),
        market_data.fetch_live_4h(row.symbol, limit=100),
        market_data.fetch_live_daily(row.symbol, limit=60),
    )
    if not candles_5m:
        return None

    levels["daily_atr14"] = market_data._calc_daily_atr14(candles_1d)
    levels["price"] = float(candles_5m[-1]["close"])

    import decision_engine
    decision, _gauges = decision_engine.evaluate_15m_decision(
        levels=levels, confluence_15m=None,
        candles_5m=candles_5m, candles_15m=candles_15m,
        candles_1h=candles_1h, candles_4h=candles_4h, candles_1d=candles_1d,
        session_hour_utc=datetime.now(timezone.utc).hour,
    )
    # candles_5m included for callers that need a real 24h-ish candle
    # window on a real cross without a second fetch (promote_no_plan_on_
    # real_cross()'s stop-planning needs -- stop_planner.py's swing/sweep
    # detection wants the same window this function already pulled).
    return {"decision": decision, "levels": levels, "candles_5m": candles_5m}


async def _enrich_opposite_break_with_full_gate(db, row: TradePlan) -> Optional[dict]:
    """2026-09-01 P0 follow-up (Kabroda AI Brain repo AGENT_LOG.md, 'CC's
    dual-sided question answered from the corpus: track BOTH triggers'):
    advance_waiting_plan()'s interim opposite-break detection only
    reports THAT the untracked trigger broke, not what the validated gate
    actually says about it. DeepSeek's corpus-backed recommendation: track
    both triggers for DETECTION, run the FULL gate on whichever breaks,
    and send the honest verdict -- not just 'wrong side, no plan.'

    Returns a dict of EXTRA fields (reason/opposite_side/opposite_trigger/
    gate_headline) for the caller to fold into `updates` before _apply()
    -- these are NOT TradePlan columns, so _apply()'s setattr() makes them
    transient, in-memory-only attributes on `row` (never persisted, never
    written by db.commit()), visible to trade_plan_notify.py's builders
    via row.__dict__ for THIS poll's notification only. Best-effort:
    returns None on any failure or missing data, leaving the plain
    fallback text advance_waiting_plan() already set. Also persists the
    real verdict to GateLog -- see _persist_verdict_to_gate_log()'s own
    docstring for why.
    """
    result = await _run_full_gate(db, row)
    if result is None:
        return None
    decision, levels = result["decision"], result["levels"]
    headline = decision.get("tactical_brief")
    opposite_side = decision.get("side")
    if not headline or not opposite_side:
        return None

    try:
        _persist_verdict_to_gate_log(db, row, decision)
    except Exception as _persist_err:
        print(f"|| TRADE PLAN || GateLog persist failed for {row.symbol}: {_persist_err}")

    return {
        "last_transition_reason": f"opposite side crossed -- full gate ran: {headline}",
        "opposite_side": opposite_side,
        "opposite_trigger": levels.get("breakout_trigger") if opposite_side == "LONG" else levels.get("breakdown_trigger"),
        "gate_headline": headline,
    }


async def _sync_gate_log_for_own_cross(db, row: TradePlan) -> None:
    """The anticipated side's own cross (WAITING/VETOED -> FILLED/VETOED/
    DONE, advance_waiting_plan()'s normal path) has the SAME gap the
    opposite-break enrichment above fixes: GateLog's row was written once,
    at lock, before any cross -- so even a real, correctly-ARMED trade
    left GateLog frozen at 'PASS, no cross yet' forever. DeepSeek's
    steady-state expectation ('the site's own row IS the verdict row...
    written at each transition: lock -> cross -> full gate -> ARMED/
    VETOED/DONE -> email') covers this path too, not just the opposite-
    side one. Best-effort, non-blocking -- a sync failure here must never
    affect the real TradePlan transition or its email, both already
    applied by the time this runs.
    """
    try:
        result = await _run_full_gate(db, row)
        if result is None:
            return
        _persist_verdict_to_gate_log(db, row, result["decision"])
    except Exception as _sync_err:
        print(f"|| TRADE PLAN || GateLog sync failed for {row.symbol}: {_sync_err}")


def _persist_verdict_to_gate_log(db, row: TradePlan, decision: dict) -> None:
    """Writes the REAL, now-known verdict into today's GateLog row --
    replacing its lock-time PASS placeholder (no cross had happened yet
    at the 8:00 lock) with what actually happened. Without this, the
    dual-sided detection's full-gate call (2026-09-01 P0 follow-up)
    produced an accurate email but no persistent record: the Brain's
    forward-test log (pulled via GET /api/export/gate-log.csv) would
    still show a stale PASS for a session that was actually a real,
    detected, correctly-vetoed cross. Matches exactly what DeepSeek's own
    manual backfill recorded for day 3 (AGENT_LOG.md, 'deploy verified +
    backfill row written by the Brain') -- this makes day 4 onward self-
    sufficient, no manual reconstruction needed ('steady-state row
    ownership clarified for Andy': "the site's own row IS the verdict
    row"). Field-for-field, this mirrors kabroda_mas_flow.py's own
    _inject_gate_log() mapping -- not a second, possibly-drifting
    implementation of what a decision_dict means.
    """
    from database import GateLog
    gate_row = (
        db.query(GateLog)
        .filter(GateLog.symbol == row.symbol, GateLog.date_key == row.date_key)
        .order_by(GateLog.id.desc())
        .first()
    )
    if gate_row is None:
        return

    gate = decision.get("gate") or {}
    plan = decision.get("plan") or {}
    misses = gate.get("misses") or []

    gate_row.state = decision.get("verdict_state")
    gate_row.side = decision.get("side")
    gate_row.headline = decision.get("tactical_brief")
    gate_row.gate_pass = gate.get("pass")
    gate_row.gate_tier = gate.get("tier")
    gate_row.veto = misses[0][:200] if misses else None
    gate_row.push_vol_ratio = decision.get("fuel_push_ratio")
    gate_row.fuel_state = decision.get("fuel_verdict")
    gate_row.trend_1h = decision.get("trend_1h")
    gate_row.trend_4h = decision.get("trend_4h")
    gate_row.htf_aligned = decision.get("htf_aligned")
    gate_row.htf_opposed = decision.get("htf_opposed")
    gate_row.daily_regime_table = decision.get("market_regime_table")
    gate_row.daily_regime_quality = decision.get("market_regime_quality")
    gate_row.micro_regime = decision.get("micro_regime")
    gate_row.entry = plan.get("entry")
    gate_row.stop = plan.get("stop")
    gate_row.t1 = plan.get("t1")
    gate_row.t2 = plan.get("t2")
    gate_row.t3 = plan.get("t3")
    gate_row.subtrig_stop = plan.get("subtrig_stop")
    gate_row.trigger_hour_utc = datetime.now(timezone.utc).hour
    gate_row.gate_detail_json = json.dumps(gate, default=str)
    print(f"|| TRADE PLAN || GateLog row synced with real cross verdict: {row.symbol} {row.date_key} -> {gate_row.state}")


async def _advance_one(db, row: TradePlan, now_utc: datetime) -> None:
    """Applies at most one state transition to `row`, in place. The
    caller's loop body commits (or rolls back on error) per record,
    matching ledger_closing_engine.py's per-record commit pattern."""
    symbol = row.symbol
    side = "LONG" if row.direction == "LONG" else "SHORT"
    session_expires_at = _compute_session_expires_at(row.session_id, row.date_key)

    if row.status in ("WAITING", "VETOED"):
        candles_5m = await market_data.fetch_live_5m(symbol, limit=310)
        if not candles_5m:
            return
        live_price = float(candles_5m[-1]["close"])
        plan_dict = {
            "status": row.status, "direction": row.direction,
            "trigger_price": row.trigger_price, "t2": row.t2,
            "commit_after": _as_utc(row.commit_after),
            "entry_mode": row.entry_mode, "tier": row.tier,
        }
        # Only fetch 1H/4H/daily -- and only when the plan's tier is still
        # None -- for _stamp_tier_at_cross() (2026-08-31 WAITING-visibility
        # fix). A plan generated with a real tier already (the original,
        # already-crossed TAKE path) never needs this extra fetch.
        candles_1h = candles_4h = None
        daily_atr14 = None
        if row.tier is None:
            candles_1h = await market_data.fetch_live_1h(symbol, limit=100)
            candles_4h = await market_data.fetch_live_4h(symbol, limit=100)
            candles_1d = await market_data.fetch_live_daily(symbol, limit=60)
            daily_atr14 = market_data._calc_daily_atr14(candles_1d)
        updates = tp.advance_waiting_plan(
            plan_dict, now_utc, session_expires_at, candles_5m, live_price,
            candles_1h=candles_1h, candles_4h=candles_4h, daily_atr14=daily_atr14,
        )
        if updates and updates.get("status") == "DONE" and "OPPOSITE trigger" in (updates.get("last_transition_reason") or ""):
            # The untracked (opposite) trigger broke -- run the full gate
            # on IT, and persist that verdict to GateLog.
            try:
                enrichment = await _enrich_opposite_break_with_full_gate(db, row)
                if enrichment:
                    updates.update(enrichment)
            except Exception as _enrich_err:
                print(f"|| TRADE PLAN || Opposite-break enrichment failed for {symbol}: {_enrich_err}")
        elif updates and "cross_time" in updates:
            # The ANTICIPATED side crossed for real (FILLED or VETOED) --
            # same GateLog staleness gap, opposite cause. Applied first so
            # row.status/etc already reflect the real transition before
            # this reads them; the sync itself never touches `updates`,
            # so it can't affect the transition or its email.
            _apply(row, updates, symbol)
            await _sync_gate_log_for_own_cross(db, row)
            return
        _apply(row, updates, symbol)

    elif row.status == "REENTRY_ARMED":
        candles_5m = await market_data.fetch_live_5m(symbol, limit=310)
        if not candles_5m:
            return
        plan_dict = {"status": row.status, "direction": row.direction, "trigger_price": row.trigger_price}
        updates = tp.advance_reentry_plan(plan_dict, now_utc, session_expires_at, candles_5m)
        _apply(row, updates, symbol)

    elif row.status == "FILLED":
        if row.fill_time is None or row.stop_price is None or row.t1 is None:
            return  # incomplete row -- nothing safe to check yet
        fill_ms = int(_as_utc(row.fill_time).timestamp() * 1000)
        candles_1m = await _fetch_1m_since(symbol, since_ms=fill_ms)
        if not candles_1m:
            return
        plan_dict = {"status": row.status, "direction": row.direction,
                     "stop_price": row.stop_price, "t1": row.t1,
                     "reentry_used": row.reentry_used}
        verdict = tp.check_wide_stop_or_t1(plan_dict, candles_1m)

        if verdict == "WIDE_STOP_FIRST":
            row.status = "STOPPED"
            row.stopped_time = now_utc
            row.last_transition_reason = "TradePlan's own wide stop wicked before T1"
            print(f"|| TRADE PLAN || {symbol} {row.session_id} {row.date_key}: STOPPED -- {row.last_transition_reason}")
            return

        if row.reentry_used:
            # A re-entry fill has no CampaignLog equivalent to mirror --
            # see mirror_campaign_outcome()'s and resolve_reentry_fill()'s
            # own docstrings.
            updates = tp.resolve_reentry_fill(plan_dict, verdict, now_utc, session_expires_at)
            _apply(row, updates, symbol)
            return

        # T1_FIRST or NEITHER_YET on the ORIGINAL fill: the wide-stop
        # question is settled (or moot) for now -- defer to CampaignLog's
        # own already-verified terminal status for the rest of management.
        campaign = (
            db.query(CampaignLog)
            .filter(
                CampaignLog.symbol == symbol,
                CampaignLog.session_id == row.session_id,
                CampaignLog.date_key == row.date_key,
            )
            .first()
        )
        updates = tp.mirror_campaign_outcome(plan_dict, campaign.status if campaign else None)
        _apply(row, updates, symbol)

    elif row.status == "NO_PLAN":
        # Andy's 2026-09-02 poll-routing decision (Kabroda AI Brain repo
        # AGENT_LOG.md): a NO_PLAN morning is no longer permanently final.
        # Re-run the REAL full gate every poll; a genuine later TAKE
        # (including the counter-trend veto, unlike the pre-cross
        # anticipate_setup() heuristic used at lock) promotes straight to
        # FILLED. Session-expiry check FIRST and cheap (no exchange calls)
        # so old, never-crossed NO_PLAN rows fall out of the polled set
        # instead of being re-fetched from Kraken forever.
        if now_utc >= session_expires_at:
            _apply(row, {"status": "DONE",
                         "last_transition_reason": "session ended, no real cross ever confirmed the gate"},
                   symbol)
            return
        result = await _run_full_gate(db, row)
        if result is None:
            return
        decision, levels = result["decision"], result["levels"]
        updates = tp.promote_no_plan_on_real_cross(
            decision, result["candles_5m"],
            r30_high=levels.get("range30m_high", 0.0), r30_low=levels.get("range30m_low", 0.0),
            f24_vah=levels.get("f24_vah", 0.0), f24_val=levels.get("f24_val", 0.0),
            daily_atr14=levels.get("daily_atr14"), now_utc=now_utc,
        )
        if updates is None:
            return  # still no real, qualifying setup -- keep waiting silently
        _apply(row, updates, symbol)
        try:
            _persist_verdict_to_gate_log(db, row, decision)
        except Exception as _persist_err:
            print(f"|| TRADE PLAN || GateLog persist failed for {row.symbol}: {_persist_err}")

    elif row.status == "STOPPED":
        if now_utc >= session_expires_at:
            # Routed through _apply() (not a direct setattr+print, unlike
            # WIDE_STOP_FIRST's -> STOPPED transition above) specifically
            # so the DONE notification hook fires -- DONE is a required
            # notify event, STOPPED is not.
            _apply(row, {"status": "DONE",
                         "last_transition_reason": "session ended, no qualifying re-entry cross after stop"},
                   symbol)
            return
        candles_5m = await market_data.fetch_live_5m(symbol, limit=310)
        if not candles_5m:
            return
        fuel = fuel_gate.evaluate_fuel_gate(candles_5m, row.trigger_price, side)
        if fuel.get("verdict") == "NO_PUSH":
            return  # price hasn't come back to the trigger yet -- not a verdict, keep waiting
        plan_dict = {"status": row.status, "reentry_used": row.reentry_used}
        updates = tp.check_reentry_eligibility(plan_dict, fuel_still_fueled=(fuel.get("verdict") == "FUELED"))
        _apply(row, updates, symbol)


def _apply(row: TradePlan, updates, symbol: str) -> None:
    if not updates:
        return
    prev_status = row.status
    for k, v in updates.items():
        setattr(row, k, v)
    if row.status != prev_status:
        print(f"|| TRADE PLAN || {symbol} {row.session_id} {row.date_key}: "
              f"{prev_status} -> {row.status} -- {updates.get('last_transition_reason')}")
        _notify_transition(prev_status, row, symbol)


def _notify_transition(prev_status: str, row: TradePlan, symbol: str) -> None:
    """One email per real state transition, only for the required events
    (ARMED/VETOED/DONE) -- trade_plan_notify.py's own docstring has the
    full reasoning. Matches ledger_closing_engine.py's own established
    pattern: notify.send_admin_email() called directly (not asyncio.
    to_thread-wrapped) inside an async loop -- an occasional blocking SMTP
    round-trip is an accepted cost at this loop's 60s cadence."""
    try:
        import notify
        import trade_plan_notify

        mail = trade_plan_notify.notification_for_transition(prev_status, row.__dict__)
        if mail:
            subject, body = mail
            notify.send_admin_email(subject, body)
    except Exception as e:
        print(f"|| TRADE PLAN || Notification failed for {symbol}: {e}")


async def run_trade_plan_loop():
    print(">>> TRADE PLAN MONITOR: Initializing (SS5/SS7/SS8 intraday state machine)...")
    while True:
        try:
            from main import scheduler_health_registry as _thr
            _thr["trade_plan"]["last_run"] = datetime.now(timezone.utc).isoformat()
            _thr["trade_plan"]["status"] = "EXECUTING"
        except Exception:
            pass

        now_utc = datetime.now(timezone.utc)
        db = SessionLocal()
        try:
            rows = db.query(TradePlan).filter(
                TradePlan.status.in_(["WAITING", "VETOED", "FILLED", "STOPPED", "REENTRY_ARMED", "NO_PLAN"])
            ).all()
            for row in rows:
                try:
                    await _advance_one(db, row, now_utc)
                    db.commit()
                except Exception as _row_err:
                    db.rollback()
                    print(f"|| TRADE PLAN || Row error {row.symbol} {row.session_id} {row.date_key}: {_row_err}")

            try:
                from main import scheduler_health_registry as _thr2
                _thr2["trade_plan"]["status"] = "WAITING"
            except Exception:
                pass
        except Exception as e:
            print(f"|| TRADE PLAN MONITOR ERROR: {e}")
            try:
                from main import scheduler_health_registry as _thr3
                _thr3["trade_plan"]["status"] = "ERROR"
                _thr3["trade_plan"]["error_count"] += 1
                _thr3["trade_plan"]["last_error"] = str(e)
            except Exception:
                pass
        finally:
            db.close()

        await asyncio.sleep(_POLL_SECONDS)
