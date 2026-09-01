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


async def _enrich_opposite_break_with_full_gate(db, row: TradePlan) -> Optional[dict]:
    """2026-09-01 P0 follow-up (Kabroda AI Brain repo AGENT_LOG.md, 'CC's
    dual-sided question answered from the corpus: track BOTH triggers'):
    advance_waiting_plan()'s interim opposite-break detection only
    reports THAT the untracked trigger broke, not what the validated gate
    actually says about it. DeepSeek's corpus-backed recommendation: track
    both triggers for DETECTION, run the FULL gate on whichever breaks,
    and send the honest verdict -- not just 'wrong side, no plan.' Reuses
    decision_engine.evaluate_15m_decision() directly against the
    SessionLock's own locked levels (the SAME real, unmodified gate,
    never a second implementation) -- exactly how DeepSeek's own
    reconstruction was done and how market_radar._build_dossier()
    already evaluates live crosses elsewhere in this codebase.

    Returns a dict of EXTRA fields (reason/opposite_side/opposite_trigger/
    gate_headline) for the caller to fold into `updates` before _apply()
    -- these are NOT TradePlan columns, so _apply()'s setattr() makes them
    transient, in-memory-only attributes on `row` (never persisted, never
    written by db.commit()), visible to trade_plan_notify.py's builders
    via row.__dict__ for THIS poll's notification only. Best-effort:
    returns None on any failure or missing data, leaving the plain
    fallback text advance_waiting_plan() already set.
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
    headline = decision.get("tactical_brief")
    opposite_side = decision.get("side")
    if not headline or not opposite_side:
        return None

    return {
        "last_transition_reason": f"opposite side crossed -- full gate ran: {headline}",
        "opposite_side": opposite_side,
        "opposite_trigger": levels.get("breakout_trigger") if opposite_side == "LONG" else levels.get("breakdown_trigger"),
        "gate_headline": headline,
    }


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
            try:
                enrichment = await _enrich_opposite_break_with_full_gate(db, row)
                if enrichment:
                    updates.update(enrichment)
            except Exception as _enrich_err:
                print(f"|| TRADE PLAN || Opposite-break enrichment failed for {symbol}: {_enrich_err}")
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
                TradePlan.status.in_(["WAITING", "VETOED", "FILLED", "STOPPED", "REENTRY_ARMED"])
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
