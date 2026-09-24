# main.py
# ---------------------------------------------------------
# KABRODA UNIFIED SERVER: PRIVATE TEAM TERMINAL
# ---------------------------------------------------------
import os
import json
import traceback
import hmac
import csv
import io
from typing import Any, Dict, Optional, Literal
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session
from pydantic import BaseModel

# --- CORE IMPORTS ---
import auth
import battlebox_pipeline
import market_radar
import traveler_radar
import gravity_engine
import gravity_math
import kabroda_mas_flow
import ledger_closing_engine
import trade_plan_engine
import traveler_plan_engine
import dry_run_split_engine
import executor_live_e1_engine
# mtf_confluence_scanner import removed 2026-09-07 (stagnant sweep) -- never
# actually called in this file; the "mtf_confluence_scanner" string at the
# dependency-graph metadata route below is a plain literal, not a reference
# to this module. Real usage lives only in market_radar.py (dossier display,
# not a decision input -- see decision_engine.py's own removal-note comment).
import session_monitor
import agent_core
import session_manager
import lti_engine

from datetime import datetime, timezone, timedelta

from database import init_db, get_db, UserModel, CampaignLog, SessionLock, AgentRunLog, SessionLocal, MacroNarrativeLog, DecisionJournal, SystemAuditLog, InterpreterLog, LtiCheckpoint, LtiProtocol, SystemAnalysisReport, SignalPerformanceLog, GravityMemory, EmailSubscriber

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

scheduler_health_registry = {
    "senior_analyst": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
    "jewel": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
    "weekly": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
    "daily_4h1h_audit": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
    "outcome_tracker": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
    "monthly_lti": {"last_run": None, "next_run": None, "status": "DISABLED", "error_count": 0, "last_error": None},
    "gravity_engine": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
    "ledger_closing": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
    "trade_plan": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
    "traveler_plan": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
    "dry_run_split": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
    "executor_live_e1": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
}


# ==============================================================================
# PHASE 4 — ASYNCIO SCHEDULERS
# No extra dependencies. Each loop calculates sleep duration to next fire time,
# catches all exceptions internally so a crashing agent never kills the server.
# ==============================================================================

# _JEWEL_SCHEDULE removed 2026-08-30 -- see the run_jewel_scheduler()
# removal note below.


def _seconds_until_utc(hour: int, minute: int = 0) -> float:
    """Seconds from now until the next occurrence of hour:minute UTC."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _seconds_until_sunday_2300() -> float:
    """Seconds from now until next Sunday at 23:00 UTC."""
    now = datetime.now(timezone.utc)
    days_ahead = (6 - now.weekday()) % 7   # Monday=0, Sunday=6
    target = now.replace(hour=23, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    if target <= now:
        target += timedelta(weeks=1)
    return (target - now).total_seconds()


def _seconds_until_month_start() -> float:
    """Seconds from now until the first of next calendar month, 00:00 UTC.
    Anchored to the calendar-month boundary (not a rolling 30-day delta) so
    month-length variation (28-31 days) doesn't drift the cadence."""
    now = datetime.now(timezone.utc)
    if now.month == 12:
        target = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        target = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return (target - now).total_seconds()


def _seconds_until_lock_end() -> float:
    """Seconds until the next NY Futures session lock_end (8:30 AM ET + 30min = 9:00 AM ET).

    Uses session_manager's own DST-aware logic so the target shifts correctly between
    EST (lock_end = 14:00 UTC) and EDT (lock_end = 13:00 UTC) without any hardcoded
    UTC hours. If today's lock_end has already passed, targets tomorrow's lock_end.
    """
    now = datetime.now(timezone.utc)
    session = session_manager.resolve_current_session(now, mode="AUTO")
    lock_end_ts = int(session["anchor_time"]) + 1800
    lock_end_utc = datetime.fromtimestamp(lock_end_ts, tz=timezone.utc)
    if lock_end_utc <= now:
        lock_end_utc += timedelta(days=1)
    return (lock_end_utc - now).total_seconds()


async def _fetch_btc_price() -> float:
    """Fetch current BTC price from the last 15M candle close."""
    try:
        candles = await battlebox_pipeline.fetch_live_15m("BTCUSDT", limit=2)
        return float(candles[-1]["close"]) if candles else 0.0
    except Exception as e:
        print(f"[SCHEDULER] BTC price fetch failed: {e}")
        return 0.0


async def _fire_senior_analyst(date_key: str) -> None:
    """
    Fires the Senior Analyst for the given date_key if not already run.

    Two scenarios handled:
    - New lock: get_live_battlebox() creates the lock and fires run_mas_analysis()
      internally via asyncio.create_task(). We detect this via lock_existed_before
      and do NOT fire a second time.
    - Restart recovery: lock already exists but analyst was never triggered.
      We read the locked packet directly and call run_mas_analysis() ourselves.
    """
    db = SessionLocal()
    try:
        # Dedup source switched 2026-08-28 (MacroNarrativeLog -> CampaignLog),
        # then again 2026-09-24 (V2 Crown retirement): CampaignLog.is_canonical
        # is only ever set by decision_engine.py's own V2 write path, which is
        # being retired -- SessionLock.mas_completed_at is the real, canonical
        # "did this already run" signal now. Deliberately NOT a bare
        # SessionLock-existence check -- SessionLock is created BEFORE
        # run_mas_analysis() even runs (battlebox_pipeline.py), so existence
        # alone can't distinguish "locked" from "analysis pipeline finished"
        # (see SessionLock.mas_completed_at's own comment for the full
        # reasoning this replaces).
        existing_brief = db.query(SessionLock).filter(
            SessionLock.symbol == "BTC/USDT",
            SessionLock.date_key == date_key,
            SessionLock.mas_completed_at.isnot(None),
        ).first()
        if existing_brief:
            print(f"[SCHEDULER] Senior Analyst already ran for {date_key} — skipping")
            return

        lock_before = db.query(SessionLock).filter(
            SessionLock.symbol == "BTC/USDT",
            SessionLock.date_key == date_key,
        ).first()
        lock_existed_before = lock_before is not None
    finally:
        db.close()

    print(f"[SCHEDULER] Fetching battlebox for Senior Analyst ({date_key})...")
    try:
        out = await battlebox_pipeline.get_live_battlebox("BTCUSDT", session_mode="AUTO")
    except Exception as e:
        print(f"[SCHEDULER] Battlebox fetch failed: {e}")
        return

    if out.get("status") == "CALIBRATING":
        print("[SCHEDULER] Session CALIBRATING — waiting 2 min and retrying (lock_end / 9:00 AM ET)...")
        await asyncio.sleep(120)
        try:
            out = await battlebox_pipeline.get_live_battlebox("BTCUSDT", session_mode="AUTO")
        except Exception as e:
            print(f"[SCHEDULER] Battlebox retry failed: {e}")
            return

    if out.get("status") == "ERROR":
        print(f"[SCHEDULER] Battlebox error: {out.get('message')}")
        return

    if not lock_existed_before:
        # New lock was created — get_live_battlebox() already fired run_mas_analysis()
        # internally via asyncio.create_task(). No double-fire.
        print(f"[SCHEDULER] New session lock created — Senior Analyst fired via battlebox")
        return

    # Restart recovery: existing lock, analyst not triggered — fire directly
    session_info = out.get("battlebox", {}).get("session", {})
    session_id = session_info.get("id")
    if not session_id:
        print("[SCHEDULER] Could not extract session_id from battlebox response — aborting")
        return

    db = SessionLocal()
    try:
        lock_record = db.query(SessionLock).filter(
            SessionLock.symbol == "BTC/USDT",
            SessionLock.session_id == session_id,
            SessionLock.date_key == date_key,
        ).first()
        if not lock_record:
            print(f"[SCHEDULER] No session lock found for {date_key} — aborting")
            return
        pkt = json.loads(lock_record.packet_data)
    finally:
        db.close()

    print(f"[SCHEDULER] Firing Senior Analyst directly (restart recovery) for {date_key} lock_end (9:00 AM ET)...")
    try:
        await asyncio.to_thread(
            kabroda_mas_flow.run_mas_analysis,
            symbol="BTC/USDT",
            session_id=session_id,
            date_key=date_key,
            battlebox_payload=pkt,
        )
    except Exception as e:
        print(f"[SCHEDULER] Senior Analyst direct fire failed: {e}")


async def run_senior_analyst_scheduler() -> None:
    """
    Daily at 14:00 UTC (9:00 AM ET). Calls _fire_senior_analyst() which handles
    both the normal-operation and restart-recovery paths without double-firing.

    Boot-time logic:
    - If it is past 14:00 UTC and no brief exists for today: fire immediately.
    - If it is before 14:00 UTC: wait for the scheduled time.
    """
    print("[SCHEDULER] Senior Analyst scheduler starting...")

    now = datetime.now(timezone.utc)
    _boot_session = session_manager.resolve_current_session(now, mode="AUTO")
    _boot_lock_end_ts = int(_boot_session["anchor_time"]) + 1800
    if now.timestamp() >= _boot_lock_end_ts:
        date_key = _boot_session["date_key"]
        print(f"[SCHEDULER] Boot check: looking for today's Senior Analyst brief ({date_key})...")
        db = SessionLocal()
        try:
            # Dedup source switched 2026-08-28, then 2026-09-24 -- see
            # _fire_senior_analyst()'s matching comment above for the full
            # reasoning (SessionLock.mas_completed_at, not a bare existence
            # check, not CampaignLog.is_canonical -- that table's only
            # writer is being retired).
            existing = db.query(SessionLock).filter(
                SessionLock.symbol == "BTC/USDT",
                SessionLock.date_key == date_key,
                SessionLock.mas_completed_at.isnot(None),
            ).first()
        finally:
            db.close()

        if existing:
            print(f"[SCHEDULER] Boot: Senior Analyst already ran today ({date_key}) — skipping")
        else:
            print(f"[SCHEDULER] Boot: no brief for today and past lock_end (9:00 AM ET) — firing now...")
            try:
                await _fire_senior_analyst(date_key)
            except Exception as e:
                print(f"[SCHEDULER] Boot-time Senior Analyst failed: {e}")

    while True:
        try:
            seconds = _seconds_until_lock_end()
            next_run_dt = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            scheduler_health_registry["senior_analyst"]["next_run"] = next_run_dt.isoformat()
            scheduler_health_registry["senior_analyst"]["status"] = "WAITING"

            print(f"[SCHEDULER] Senior Analyst: next fire in {seconds / 3600:.1f}h (lock_end / 9:00 AM ET)")
            await asyncio.sleep(seconds)

            scheduler_health_registry["senior_analyst"]["status"] = "EXECUTING"

            _fire_now = datetime.now(timezone.utc)
            _fire_session = session_manager.resolve_current_session(_fire_now, mode="AUTO")
            date_key = _fire_session["date_key"]
            print(f"[SCHEDULER] Senior Analyst scheduled fire — {date_key} lock_end (9:00 AM ET)")
            await _fire_senior_analyst(date_key)

            scheduler_health_registry["senior_analyst"]["last_run"] = datetime.now(timezone.utc).isoformat()
            scheduler_health_registry["senior_analyst"]["status"] = "WAITING"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[SCHEDULER] Senior Analyst scheduler error: {e}")
            scheduler_health_registry["senior_analyst"]["error_count"] += 1
            scheduler_health_registry["senior_analyst"]["last_error"] = str(e)
            scheduler_health_registry["senior_analyst"]["status"] = "ERROR"
            await asyncio.sleep(300)


# run_jewel_scheduler() removed 2026-08-30 -- drove jewel_specialist.py's 6x/
# daily snapshot of the old confluence vote-tally/JEWEL signal, both gone
# (see mtf_confluence_scanner.py's removal note). jewel_specialist.py
# archived, its only purpose was feeding this scheduler.


async def run_weekly_scheduler() -> None:
    """
    Sunday 23:00 UTC: Elliott Wave Specialist runs first, then Performance Auditor.
    Sleeps 1h after firing to avoid re-triggering within the same Sunday window.
    """
    print("[SCHEDULER] Weekly scheduler starting (Elliott Wave + Performance Auditor)...")
    while True:
        try:
            seconds = _seconds_until_sunday_2300()
            next_run_dt = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            scheduler_health_registry["weekly"]["next_run"] = next_run_dt.isoformat()
            scheduler_health_registry["weekly"]["status"] = "WAITING"

            print(f"[SCHEDULER] Weekly: next run in {seconds / 3600:.1f}h (Sunday 23:00 UTC)")
            await asyncio.sleep(seconds)

            scheduler_health_registry["weekly"]["status"] = "EXECUTING"

            date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            since_week = datetime.utcnow() - timedelta(days=7)

            # Elliott Wave Specialist (LLM interpretation layer, elliott_wave_
            # specialist.py) disabled 2026-08-17, same pass and same reason as
            # run_mas_analysis() in kabroda_mas_flow.py -- part of "the agents"
            # costing daily money. NOTE: this is the LLM interpreter only --
            # kabroda_macro_engine.py's actual deterministic ZigZag wave-pivot
            # detection is a separate subprocess on its own 24h schedule,
            # untouched, out of scope for this rebuild (REBUILD_PLAN.md).

            # Performance Auditor + Audit-AI (H1-H6, harness/audit_runner.py)
            # archived 2026-08-17 -- Kabroda Audit AUDIT_FINDINGS.md confirmed
            # both record-only; performance_auditor's one live-reaching path
            # (SystemAuditLog -> Senior Analyst context) was already explicitly
            # non-binding ("do not apply as rules"). Modules moved to _archive/.

            scheduler_health_registry["weekly"]["last_run"] = datetime.now(timezone.utc).isoformat()
            scheduler_health_registry["weekly"]["status"] = "WAITING"

            # Sleep 1h to clear the Sunday 23:00 UTC window before recalculating next fire
            await asyncio.sleep(3600)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[SCHEDULER] Weekly outer error: {e}")
            scheduler_health_registry["weekly"]["error_count"] += 1
            scheduler_health_registry["weekly"]["last_error"] = str(e)
            scheduler_health_registry["weekly"]["status"] = "ERROR"
            await asyncio.sleep(300)


# run_daily_4h1h_audit_scheduler (audit_ai.py, H7-H16) archived 2026-08-17 --
# Kabroda Audit AUDIT_FINDINGS.md confirmed record-only, never fed a live
# decision. Module moved to _archive/. See REBUILD_PLAN.md.


async def run_monthly_lti_scheduler() -> None:
    """
    First of every calendar month, 00:00 UTC: run the KULTI monthly confluence
    audit (lti_engine.run_lti_audit, deterministic, no LLM) and write one
    LtiCheckpoint row. Advisory-only -- never auto-executes anything.

    2026-08-30: the AI interpreter step (lti_interpreter.run_lti_interpretation)
    is removed -- Andy's call, no LLM tied to Kabroda's cost path, and no
    generated publication/brief of any kind. The deterministic audit itself
    stays; it's real numeric data (BBWP/PMARP/RSI/etc.), not a written brief.
    """
    print("[SCHEDULER] Monthly LTI scheduler starting (KULTI confluence audit)...")
    while True:
        try:
            seconds = _seconds_until_month_start()
            next_run_dt = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            scheduler_health_registry["monthly_lti"]["next_run"] = next_run_dt.isoformat()
            scheduler_health_registry["monthly_lti"]["status"] = "WAITING"

            print(f"[SCHEDULER] Monthly LTI: next run in {seconds / 3600:.1f}h (1st of month, 00:00 UTC)")
            await asyncio.sleep(seconds)

            scheduler_health_registry["monthly_lti"]["status"] = "EXECUTING"

            now = datetime.now(timezone.utc)
            date_key = now.strftime("%Y-%m")
            first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)

            _db = SessionLocal()
            try:
                _already_ran = _db.query(LtiCheckpoint).filter(
                    LtiCheckpoint.symbol == "BTC/USDT",
                    LtiCheckpoint.created_at >= first_of_month,
                ).first()
            finally:
                _db.close()

            if _already_ran:
                print(f"[SCHEDULER] Monthly LTI audit already ran this month ({_already_ran.date_key}) — skipping")
            else:
                print(f"[SCHEDULER] Monthly LTI audit firing for {date_key} (1st of month, 00:00 UTC)...")
                try:
                    audit = await asyncio.to_thread(lti_engine.run_lti_audit, symbol="BTC/USDT")

                    _db2 = SessionLocal()
                    try:
                        _db2.add(LtiCheckpoint(symbol=audit["symbol"], date_key=date_key,
                            bbwp=audit["bbwp"], bbwp_state=audit["bbwp_state"],
                            pmarp=audit["pmarp"], pmarp_state=audit["pmarp_state"],
                            rsi_weekly=audit["rsi_weekly"], pct_below_high=audit["pct_below_high"],
                            krown_cross_state=audit["krown_cross_state"], weekly_ema_trend=audit["weekly_ema_trend"],
                            low_month_day_flag=audit["low_month_day_flag"], moon_phase_flag=audit["moon_phase_flag"],
                            moon_phase_label=audit["moon_phase_label"], hash_ribbons_state=audit["hash_ribbons_state"],
                            fear_greed_value=audit["fear_greed_value"], fear_greed_label=audit["fear_greed_label"],
                            accumulation_signals_firing=audit["accumulation_signals_firing"],
                            distribution_signals_firing=audit["distribution_signals_firing"],
                            conviction_label=audit["conviction_label"], wave_label_snapshot=audit["wave_label_snapshot"],
                            gravity_cross_confirm=audit["gravity_cross_confirm"], nearest_macro_level=audit["nearest_macro_level"],
                        ))
                        _db2.commit()
                    finally:
                        _db2.close()
                    print(f"[SCHEDULER] Monthly LTI audit: conviction={audit['conviction_label']}")
                except Exception as e:
                    print(f"[SCHEDULER] Monthly LTI audit failed: {e}")

            scheduler_health_registry["monthly_lti"]["last_run"] = datetime.now(timezone.utc).isoformat()
            scheduler_health_registry["monthly_lti"]["status"] = "WAITING"

            # Sleep 1h to clear the month-start window before recalculating next fire
            await asyncio.sleep(3600)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[SCHEDULER] Monthly LTI outer error: {e}")
            scheduler_health_registry["monthly_lti"]["error_count"] += 1
            scheduler_health_registry["monthly_lti"]["last_error"] = str(e)
            scheduler_health_registry["monthly_lti"]["status"] = "ERROR"
            await asyncio.sleep(300)


# ==============================================================================
# OUTCOME TRACKER — runs every 4 hours
# Fills DecisionJournal outcome fields for rows older than 4h.
# Fills CampaignLog.target_hit for all closed rows.
# ==============================================================================

def _do_outcome_tick(current_price: float) -> None:
    """Core outcome-tracker logic. Extracted for testability."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(hours=4)
    db = SessionLocal()
    try:
        pending = db.query(DecisionJournal).filter(
            DecisionJournal.outcome_direction_correct.is_(None),
            DecisionJournal.timestamp < cutoff,
        ).all()

        filled = 0
        for row in pending:
            if not row.asset_price or row.asset_price == 0:
                continue
            pct_move = (current_price - row.asset_price) / row.asset_price * 100
            bias = row.confluence_direction
            if bias == "LONG":
                correct = pct_move > 0
            elif bias == "SHORT":
                correct = pct_move < 0
            else:
                correct = False
            row.outcome_price_4h = current_price
            row.outcome_pct_move_4h = round(pct_move, 4)
            row.outcome_direction_correct = correct
            filled += 1

        # target_hit: current ledger always closes at T1 or SL — record what happened
        closed_logs = db.query(CampaignLog).filter(
            CampaignLog.status.in_(["CLOSED_WIN", "CLOSED_LOSS"]),
            CampaignLog.target_hit.is_(None),
            CampaignLog.is_canonical == True,
        ).all()
        for log in closed_logs:
            log.target_hit = "T1" if log.status == "CLOSED_WIN" else "STOP"

        db.commit()
        print(f"[OUTCOME TRACKER] Filled {filled} DJ rows | {len(closed_logs)} campaign target_hit rows")
    except Exception as e:
        print(f"[OUTCOME TRACKER] DB error: {e}")
        db.rollback()
    finally:
        db.close()


async def run_outcome_tracker() -> None:
    """Every 4 hours: fills 4H outcome fields on DecisionJournal and target_hit on CampaignLog.
    Runs immediately on boot to backfill any existing unprocessed rows."""
    print("[SCHEDULER] Outcome Tracker starting...")
    while True:
        try:
            scheduler_health_registry["outcome_tracker"]["status"] = "EXECUTING"

            current_price = await _fetch_btc_price()
            if current_price > 0:
                _do_outcome_tick(current_price)
            else:
                print("[OUTCOME TRACKER] Could not fetch BTC price — skipping tick")

            scheduler_health_registry["outcome_tracker"]["last_run"] = datetime.now(timezone.utc).isoformat()

            seconds = 14400
            next_run_dt = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            scheduler_health_registry["outcome_tracker"]["next_run"] = next_run_dt.isoformat()
            scheduler_health_registry["outcome_tracker"]["status"] = "WAITING"

            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[OUTCOME TRACKER] Outer error: {e}")
            scheduler_health_registry["outcome_tracker"]["error_count"] += 1
            scheduler_health_registry["outcome_tracker"]["last_error"] = str(e)
            scheduler_health_registry["outcome_tracker"]["status"] = "ERROR"
            await asyncio.sleep(300)


# _run_analysis_loop_body()/run_analysis_loop_scheduler() removed 2026-09-23
# (V2 Crown retirement, Audit-AI surface retirement, Andy's explicit
# "retire entirely" ruling) -- this was the 12h background scheduler behind
# the dashboard's "Run Analysis" button, writing 30-day CampaignLog win-
# rate/PnL stats into AuditSuggestionLog (both now gone). Its lifespan()
# task wiring is removed in the same commit -- see lifespan()'s own
# removal comment below.


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(">>> BOOTING KABRODA SYSTEM: Initializing Database Schema...")
    init_db()
    try:
        import executor_crypto
        executor_crypto.validate_key_configured()
    except Exception as e:
        # Deliberately NOT a hard crash of the whole app -- EXECUTOR_
        # CREDENTIAL_KEY won't exist on Render until Andy actually sets
        # it, and this is a Stage-1-only feature nobody is using yet;
        # crashing all of kabroda.com over an unconfigured, not-yet-active
        # subsystem would be a far worse outcome than a loud boot warning.
        # The credential-set/decrypt routes themselves still fail cleanly
        # if actually used before the key is configured.
        print(f">>> WARNING: executor credential encryption is not configured -- {e}")
    app.state.gravity_task          = asyncio.create_task(gravity_engine.run_gravity_ingestion_loop())
    app.state.ledger_task           = asyncio.create_task(ledger_closing_engine.run_ledger_audit_loop())
    app.state.trade_plan_task       = asyncio.create_task(trade_plan_engine.run_trade_plan_loop())
    app.state.traveler_plan_task    = asyncio.create_task(traveler_plan_engine.run_traveler_plan_loop())
    app.state.dry_run_split_task    = asyncio.create_task(dry_run_split_engine.run_dry_run_split_loop())
    app.state.executor_live_e1_task = asyncio.create_task(executor_live_e1_engine.run_executor_live_e1_loop())
    import executor_live_engine
    app.state.executor_live_task    = asyncio.create_task(executor_live_engine.run_executor_position_loop())
    app.state.senior_analyst_task   = asyncio.create_task(run_senior_analyst_scheduler())
    # jewel_task (run_jewel_scheduler) removed 2026-08-30 -- see that
    # function's old location for the removal note.
    app.state.weekly_task           = asyncio.create_task(run_weekly_scheduler())
    # KULTI LTI scheduler pulled 2026-07-08 -- see WORK_LOG.md. The design mixed
    # trading-system paradigms (confluence-count tiers, borrowed JEWEL vocabulary,
    # N-based validation thinking) into what should be a from-first-principles
    # long-term investing system. Off until it's rebuilt properly. Function body
    # left in place below, not deleted, in case pieces (real indicator math,
    # Hash Ribbons) are worth reusing in the rebuild.
    # app.state.lti_task            = asyncio.create_task(run_monthly_lti_scheduler())
    app.state.outcome_tracker_task  = asyncio.create_task(run_outcome_tracker())
    # analysis_loop_task removed 2026-09-23 (V2 Crown retirement, Audit-AI
    # surface retirement) -- see run_analysis_loop_scheduler()'s own former
    # definition site removal comment above.
    app.state.monitor_task          = asyncio.create_task(session_monitor.run_session_monitor_loop())
    # signal_accuracy/signal_flagging/accuracy_report schedulers archived
    # 2026-08-17 per Kabroda Audit REBUILD_PLAN.md -- confirmed record-only,
    # never fed a live decision. Modules moved to _archive/.
    yield
    print(">>> SHUTTING DOWN KABRODA SYSTEM...")
    app.state.gravity_task.cancel()
    app.state.ledger_task.cancel()
    app.state.trade_plan_task.cancel()
    app.state.executor_live_task.cancel()
    app.state.senior_analyst_task.cancel()
    app.state.weekly_task.cancel()
    app.state.outcome_tracker_task.cancel()
    app.state.monitor_task.cancel()


# signal_accuracy_tracker / signal_flagging_engine / accuracy_report_generator
# scheduler loops archived 2026-08-17 -- Kabroda Audit AUDIT_FINDINGS.md
# confirmed these three (plus signal_weight_manager, see the API routes
# below) never fed any live decision path; genuinely record-only, read
# only by an admin-only dashboard. See REBUILD_PLAN.md. Modules moved to
# _archive/.

app = FastAPI(title="Kabroda BattleBox", version="12.0", lifespan=lifespan)

SECRET_KEY = os.getenv("SESSION_SECRET", "kabroda_prod_key_999")

def _bool_env(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None: return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
IS_HTTPS = PUBLIC_BASE_URL.startswith("https://")
SESSION_HTTPS_ONLY = _bool_env("SESSION_HTTPS_ONLY", default=IS_HTTPS)

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    https_only=SESSION_HTTPS_ONLY,
    same_site="lax",
    max_age=86400 * 30  
)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

app.include_router(auth.router)

def _template_or_fallback(request: Request, templates: Jinja2Templates, name: str, context: Dict[str, Any]):
    try:
        # Use direct Jinja2 render to avoid Starlette's TemplateResponse cache key bug
        # (Jinja2 3.1.6 uses (name, globals) as cache key, but globals is a dict = unhashable)
        tmpl = templates.env.get_template(name)
        html = tmpl.render(context)
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<h2>System Error: {name}</h2><p>{str(e)}</p>", status_code=500)

def get_user_context(request: Request, db: Session):
    uid = request.session.get(auth.SESSION_KEY)
    base_context = {"request": request}
    
    if not uid: 
        base_context.update({"is_logged_in": False, "is_admin": False})
        return base_context
        
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    base_context.update({
        "is_logged_in": True,
        "is_admin": getattr(user, "is_admin", False) if user else False,
        "username": getattr(user, "username", "Operative") if user else "Operative",
        "email": getattr(user, "email", "") if user else "",
        "user": user
    })
    return base_context

# --- PUBLIC ROUTES (LOCKED DOWN) ---
@app.get("/")
async def home(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if ctx["is_logged_in"]:
        return RedirectResponse(url="/suite/radar", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


# --- HEALTH ENDPOINT ---
@app.get("/health")
async def health():
    """Render health check — no auth required. Returns trimmed scheduler statuses only."""
    trimmed = {
        name: {"last_run": info["last_run"], "status": info["status"]}
        for name, info in scheduler_health_registry.items()
    }
    return {
        "status": "OK",
        "schedulers": trimmed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# --- SUITE ROUTES ---
@app.get("/suite")
async def suite(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "session_control.html", ctx)

@app.get("/suite/battle-control")
async def battle_control_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "suite_home.html", ctx)

# /suite/research-lab removed 2026-08-30 -- research_lab.py archived, real
# backtest validation lives in the Kabroda AI Brain repo now.

@app.get("/suite/radar")
async def radar_page(request: Request, db: Session = Depends(get_db)):
    # Public, 2026-08-27, Andy's direct instruction: "let anyone go to the radar
    # and use it for what it's built for." Safe to open -- the page itself has
    # zero server-rendered template variables (pure static HTML/JS, confirmed
    # by grep before this change) and both APIs it calls
    # (/api/radar/snapshot, /api/radar/scan) were already public/unauthenticated
    # by design (same pattern as /api/gravity/scan, see this file's own header
    # comment on that endpoint). This login gate was never protecting the
    # underlying data -- only the convenience of viewing the page.
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "market_radar.html", ctx)

@app.get("/suite/gravity-map")
async def gravity_map_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "gravity_map.html", ctx)

# /suite/confluence + confluence.html removed 2026-08-30 -- presented the
# old confluence vote-tally/JEWEL signal as if it mattered. Gone, not
# archived-in-place, per Andy's call (see mtf_confluence_scanner.py).

@app.get("/suite/dashboard")
async def suite_dashboard_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "suite_dashboard.html", ctx)

@app.get("/suite/lti")
async def lti_page(request: Request, db: Session = Depends(get_db)):
    # KULTI LTI page pulled 2026-07-08 -- design mixed trading-system paradigms
    # into what should be a from-first-principles investing system. See
    # WORK_LOG.md. Route stays defined so no dangling crash for the URL, but
    # fully inert until rebuilt -- matching the /register closure pattern.
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/suite/dashboard", status_code=303)


@app.post("/api/lti/protocol")
async def save_lti_protocol(request: Request, db: Session = Depends(get_db)):
    # Pulled alongside GET /suite/lti -- see note above.
    return JSONResponse({"ok": False, "error": "KULTI is being rebuilt."}, status_code=410)


# Macro War Room page (GET /suite/macro-war-room) removed 2026-09-23 -- Andy's
# call during the strategic site audit ("there's really no reason to have any
# of that kind of stuff anymore"). Confirmed safe by a dedicated dependency
# check before removal: its two halves were the "CCO Desk" brief panel
# (100% CampaignLog/V2-lineage data, would have gone stale the moment V2's
# decision path stops writing that table) and a client-side Gravity KPI grid
# that already duplicates templates/gravity_map.html (which stays -- Andy's
# own call, "a usable tool on its own"). The run_mas_analysis() trigger this
# route used to fire was a REDUNDANT third call path -- battlebox_pipeline.py
# (on every fresh lock) and the Senior Analyst restart-recovery scheduler
# already fire the same shared function; removing this route's own call site
# does not affect TradePlan or TravelerPlan creation at all. Nothing else
# referenced this route or its template (grepped, including tests/ -- zero
# hits). Full trail: AGENT_LOG.md 2026-09-23, V2_RETIREMENT_MAP.md.

# --- NARRATIVE / JEWEL DATA ENDPOINT ---
@app.get("/api/narrative/latest")
async def api_narrative_latest(symbol: str = "BTC/USDT"):
    """
    Single endpoint serving War Room, Market Radar Panel 00, and Gravity Map sidebar.
    Returns latest tactical brief and JEWEL snapshot. No authentication required
    — data is not sensitive.

    2026-08-28: narrative/wave sourcing changed. MacroNarrativeLog's
    senior_analyst rows stopped being written this session (narrative_text had
    been permanently empty since the Senior Analyst LLM step was removed;
    tactical_text duplicated what CampaignLog already has). elliott_wave_specialist
    rows stopped 2026-08-17 (writer archived) -- the "wave" field had been
    silently serving month-stale data as if current. Andy's call: archive this
    concept, rebuild it properly in Kabroda AI Brain (needs continuous live
    watching, not a once-a-day hardcoded write). tactical_text now reads from
    CampaignLog directly (the real, canonical source); wave is always null so
    the front-end's existing `if (!data.wave)` fallback hides the section
    cleanly instead of showing stale content forever.
    """
    db_sym = symbol.replace("USDT", "/USDT") if "/" not in symbol else symbol

    db = SessionLocal()
    try:
        analyst_row = (
            db.query(CampaignLog)
            .filter(
                CampaignLog.symbol == db_sym,
                CampaignLog.is_canonical == True,
            )
            .order_by(CampaignLog.id.desc())
            .first()
        )

        # jewel_row / JewelSnapshotLog read removed 2026-08-30 -- jewel_specialist.py
        # (the only writer) is archived, nothing populates this table anymore.
        # "jewel": null below so the front-end's existing !data.jewel fallback
        # hides that panel cleanly instead of showing frozen data forever.

        return JSONResponse({
            "ok": True,
            "symbol": db_sym,
            "date_key": analyst_row.date_key if analyst_row else None,
            "narrative": {
                "narrative_text":   None,  # no longer generated -- see docstring
                "tactical_text":    analyst_row.mas_executive_brief if analyst_row else None,
                "performance_note": None,
                "date_key":         analyst_row.date_key if analyst_row else None,
            },
            "wave": None,  # elliott_wave_specialist writer archived 2026-08-17 -- see docstring
            "jewel": None,  # JEWEL system retired 2026-08-30 -- see comment above
        })
    except Exception as e:
        print(f"[NARRATIVE API] Error: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    finally:
        db.close()


# --- GRAVITY API ENDPOINT ---
@app.get("/api/gravity/scan")
async def api_gravity_scan(symbol: str = "BTC/USDT"):
    print(f"[GRAVITY] scan called for {symbol}")
    print("[GRAVITY] calling fetch_live_daily")
    candles_1d = await battlebox_pipeline.fetch_live_daily(symbol, limit=30)
    print(f"[GRAVITY] got {len(candles_1d)} daily candles")
    print("[GRAVITY] calling fetch_live_15m")
    candles_15m = await battlebox_pipeline.fetch_live_15m(symbol, limit=300)
    print(f"[GRAVITY] got {len(candles_15m)} 15m candles")
    kde_data = gravity_math.calculate_gravity_kde(symbol)
    macro_fibs = gravity_math.calculate_macro_fibs(candles_1d, candles_15m)
    print(f"[GRAVITY] chart_data length: {len(macro_fibs.get('chart_data', []))}")
    return JSONResponse({
        "ok": True,
        "symbol": symbol,
        "kde_data": kde_data,
        "macro_fibs": macro_fibs
    })


# /api/confluence removed 2026-08-30 -- exposed the old confluence vote-tally
# plus two different, "un-reconciled" (its own docstring's word) weekly-trend
# definitions side by side. Exactly the kind of silent-conflict surface Andy
# wants zero of anywhere near the 15M decision. Gone, not archived-in-place.


@app.get("/api/radar/snapshot")
async def api_radar_snapshot(db: Session = Depends(get_db)):
    """
    Phase 1 of the two-phase radar render. Pure DB reads — zero exchange I/O.
    Returns: locked session levels + MAS status. Target response time: < 100ms.
    Called before POST /api/radar/scan so the UI can render structural truth
    instantly while live MTF data loads in the background.

    2026-08-30: MtfReading and JewelSnapshotLog reads removed -- both tables'
    only writers (the old confluence vote-tally / JEWEL system) are retired.
    mtf_cached / jewel_gate_open below are always empty now; kept as response
    keys (not removed from the JSON shape) so nothing downstream that reads
    this endpoint breaks on a missing key, but there is nothing left to
    populate them and there won't be again.

    2026-08-30 (later): `today` fixed to use the session's own date_key
    (anchored to the 13:00 UTC lock) instead of raw UTC calendar midnight --
    the two disagree for 13 hours every single day (00:00-13:00 UTC), during
    which this route was missing the still-active SessionLock entirely and
    showing "not locked" for a session that really was. See market_radar.py's
    _current_session_date_key() for the full writeup (found and fixed there
    first, then found here too).
    """
    today = session_manager.resolve_current_session(datetime.now(timezone.utc), "AUTO")["date_key"]
    symbol_norm = "BTC/USDT"
    symbol_raw  = "BTCUSDT"

    # 1. Today's session lock — locked levels are the SSOT
    lock = db.query(SessionLock).filter(
        SessionLock.symbol == symbol_norm,
        SessionLock.session_id == "us_ny_futures",
        SessionLock.date_key == today,
    ).first()

    levels = {}
    price = 0.0
    confluence_scan = {}
    if lock:
        try:
            pkt = json.loads(lock.packet_data)
            levels = pkt.get("levels", {})
            price = float(levels.get("anchor_price") or 0)
            # Already computed once at lock and stored -- no live fetch needed,
            # keeps this endpoint's "Phase 1, zero exchange I/O" contract intact.
            confluence_scan = pkt.get("context", {}).get("confluence_scan", {})
        except Exception:
            pass

    mtf_cached: dict = {}
    jewel_gate_open = None

    # 2. Today's MAS verdict — for the status badge and cockpit pre-population
    campaign = db.query(CampaignLog).filter(
        CampaignLog.symbol == symbol_norm,
        CampaignLog.date_key == today,
        CampaignLog.is_canonical == True,
    ).order_by(CampaignLog.id.desc()).first()

    mas_status = campaign.mas_approval_status if campaign else None
    conviction = campaign.conviction if campaign else None
    plan = None
    if campaign and campaign.entry_price:
        plan = {
            "bias":        campaign.bias,
            "entry_price": campaign.entry_price,
            "stop_loss":   campaign.stop_loss,
            "t1":          campaign.t1,
            "t2":          campaign.t2,
            "t3":          campaign.t3,
        }

    # 5. TF system verdicts (15M only -- 1H/4H retired) and which-TF-today decision
    tf_verdicts = market_radar._get_tf_system_verdicts(symbol_norm)
    tf_today    = market_radar._which_tf_today(tf_verdicts)

    # 6. Daily regime (the real, validated read -- market_regime.py, from the
    # most recent GateLog row) + weekly 200 SMA position (real, separate,
    # live infrastructure -- battlebox_pipeline._fetch_weekly_200sma(), still
    # read from the audit row). The old daily_regime heuristic
    # (_compute_daily_regime(), EMA-slope + 200SMA-position guessing) is
    # removed -- kabroda.com now shows what was actually calibrated and
    # tested, not a separate, never-validated label (Andy's call, 2026-08-30).
    daily_regime = "—"
    weekly_200sma_position = "—"
    from database import SessionAuditLog as _SAL, GateLog as _GL
    audit_row = db.query(_SAL).filter(
        _SAL.symbol == symbol_norm,
    ).order_by(_SAL.id.desc()).first()
    if audit_row:
        weekly_200sma_position = getattr(audit_row, "weekly_200sma_position", None) or "—"
    gate_row = db.query(_GL).filter(
        _GL.symbol == symbol_norm,
        _GL.daily_regime_table.isnot(None),
    ).order_by(_GL.id.desc()).first()
    if gate_row:
        daily_regime = gate_row.daily_regime_table
        if gate_row.daily_regime_quality:
            daily_regime = f"{daily_regime} ({gate_row.daily_regime_quality})"

    return JSONResponse({
        "ok":                    True,
        "locked":                lock is not None,
        "symbol":                symbol_raw,
        "price":                 price,
        "levels":                levels,
        "mtf_cached":            mtf_cached,
        "jewel_gate_open":       jewel_gate_open,
        "mas_status":            mas_status,
        "conviction":            conviction,  # TAKE/PASS (calibrated gate, v2)
        "plan":                  plan,
        "tf_verdicts":           tf_verdicts,
        "tf_today":              tf_today,
        "daily_regime":          daily_regime,
        "weekly_200sma_position": weekly_200sma_position,
        "confluence_scan":       confluence_scan,  # real 21/55 EMA + BBWP/PMARP + divergence per timeframe
        # 2026-09-01 (Kabroda AI Brain AGENT_LOG.md, "deploy verified +
        # backfill row written by the Brain", residual item 1): this
        # endpoint is Phase 1 BY DESIGN -- pure DB reads, zero exchange
        # I/O, <100ms (see its own docstring) -- `price`/`plan`/
        # `tf_verdicts` above are the 8:00 lock snapshot, not live. That
        # was previously silent, which read as staleness rather than
        # design. Made explicit here instead of changing the contract:
        # live price is POST /api/radar/scan; live TradePlan intraday
        # state (the real dual-sided detection + full gate) is GET
        # /api/admin/trade-plan-status (admin session).
        "price_as_of":           "lock",
        "lock_time_utc":         (datetime.fromtimestamp(lock.lock_time, tz=timezone.utc).isoformat() if lock else None),
        "live_price_endpoint":   "/api/radar/scan",
        "live_state_endpoint":   "/api/admin/trade-plan-status",
    })


@app.get("/api/radar/traveler-snapshot")
async def api_radar_traveler_snapshot(db: Session = Depends(get_db)):
    """Step 1 of the V2 Crown retirement + radar rebuild (CLAUDE.md
    "Strategic Direction", V2_RETIREMENT_MAP.md) -- the Traveler-native
    replacement for this file's own /api/radar/snapshot above. Public, no
    login, same as every other /api/radar/* route (main.py:761-771's own
    "let anyone use the radar" precedent) -- pure DB reads via
    traveler_radar.py, zero decision_engine.py/CampaignLog dependency.

    Ships alongside the old /api/radar/snapshot + /api/radar/scan (not a
    replacement of them yet) so the rebuilt radar frontend has a working
    data source to cut over to before V2's routes are deleted in that
    plan's Step 3 -- see traveler_radar.py's own module docstring for the
    full reasoning, including why a pre-cross request gets levels-only
    with `plan: null` rather than a speculative directional dossier."""
    return JSONResponse(traveler_radar.get_public_traveler_snapshot(db))


@app.get("/api/live-price")
async def api_live_price():
    """Lightweight BTC price tick — single candle fetch, no macro math."""
    try:
        candles = await battlebox_pipeline.fetch_live_5m("BTCUSDT", limit=1)
        if not candles:
            return JSONResponse({"ok": False, "price": 0})
        last = candles[-1]
        return JSONResponse({"ok": True, "price": float(last["close"]), "time": int(last["time"])})
    except Exception as e:
        return JSONResponse({"ok": False, "price": 0, "error": str(e)})


# /api/research/chat-mas + MASChatPayload removed 2026-08-30 -- Andy's call:
# no LLM tied to Kabroda's cost path, period. interrogate_cro() was already
# a stub (disabled 2026-08-17, zero live cost), so this was dead weight
# giving the false impression a working chat feature sat behind it. That job
# belongs to Kabroda AI Brain now, a dedicated conversational tool, not a
# second, smaller version of it living inside kabroda.com.

# /api/research/audit-intel (the Intel Auditor) removed 2026-08-30 -- Andy's
# call: gone entirely, one of several LLM-based tools removed this session
# (an agent_core._call_agent() call per use, real cost), and its methodology
# had gone stale under this session's rebuild: it gated on gravity walls as
# BLOCKED/HIGH_RISK/CLEAR (gravity is a decoupled reference page now, not a
# decision input) and recalculated targets with a third, different formula
# from both the old and new measured-move math. See kabroda_mas_flow.py for
# the removed audit_foreign_intel_pipeline()/IntelAuditReport/
# INTEL_AUDITOR_SYSTEM_PROMPT for the removed audit pipeline (the UI panel
# that called this route lived in the Macro War Room page, itself removed
# 2026-09-23 -- see this file's own removal comment further up).

# --- AGENT COST INFRASTRUCTURE (PHASE 1) ---

# /api/admin/run-audit removed 2026-09-23 (V2 Crown retirement, Audit-AI
# surface retirement) -- already effectively dead before this removal: its
# `import harness.audit_runner` target was archived 2026-08-17, so every
# call threw ModuleNotFoundError, caught by its own except and returned as
# a 500. Zero frontend caller either way.

@app.post("/api/admin/test-notify")
async def api_admin_test_notify(request: Request, db: Session = Depends(get_db)):
    """
    Fires one test admin email via notify.send_admin_email(), using the
    real SMTP_* env vars already resolved in this running process (no
    credentials pass through the browser or this endpoint). Admin only.
    Used to confirm the 4H/1H candidate open/close email path is wired
    correctly before relying on it in production.
    """
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)
    import notify
    ok = await asyncio.to_thread(
        notify.send_admin_email,
        "KABRODA NOTIFY TEST",
        "This is a test send from /api/admin/test-notify. If you received "
        "this, the SMTP notification path for 4H/1H candidate open/close "
        "emails is confirmed working end to end.",
    )
    return JSONResponse({
        "ok": ok,
        "smtp_host": notify.SMTP_HOST,
        "smtp_port": notify.SMTP_PORT,
        "smtp_user_configured": bool(notify.SMTP_USER),
        "smtp_dest_configured": bool(notify.SMTP_DEST),
    })


@app.post("/api/admin/test-notify-trade-plan")
async def api_admin_test_notify_trade_plan(
    request: Request, plan_id: int, event: str = "lock", db: Session = Depends(get_db)
):
    """
    Fires one real Trade Plan email (trade_plan_notify.py) built from an
    ACTUAL TradePlan row, so its real formatting can be verified before
    relying on it live -- the plan-specific test fire Andy's build
    request asked for, as an alternative to extending /api/admin/test-
    notify with synthetic content. Admin only.

    event: one of "lock" | "armed" | "vetoed" | "done" -- picks which
    builder in trade_plan_notify.py to use against the real row. "lock"
    now always sends regardless of the row's actual status (2026-09-02 --
    see trade_plan_notify.py's module header for why the old WAITING-only
    gate was reversed).
    """
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)

    from database import TradePlan as _TradePlan
    row = db.query(_TradePlan).filter(_TradePlan.id == plan_id).first()
    if row is None:
        return JSONResponse({"ok": False, "error": f"No TradePlan row with id={plan_id}"}, status_code=404)

    import notify
    import trade_plan_notify
    builders = {
        "lock": trade_plan_notify.build_lock_email,
        "armed": trade_plan_notify.build_armed_email,
        "vetoed": trade_plan_notify.build_vetoed_email,
        "done": trade_plan_notify.build_done_email,
    }
    builder = builders.get(event)
    if builder is None:
        return JSONResponse({"ok": False, "error": f"Unknown event '{event}' -- use one of {list(builders)}"}, status_code=400)

    mail = builder(row.__dict__)
    if mail is None:
        return JSONResponse({"ok": False, "error": f"'{event}' email does not apply to this row's current status ({row.status})"})

    subject, body = mail
    ok = await asyncio.to_thread(notify.send_admin_email, subject, body)
    return JSONResponse({"ok": ok, "subject": subject, "plan_id": plan_id, "event": event})


@app.get("/api/admin/trade-plan-status")
async def api_admin_trade_plan_status(request: Request, db: Session = Depends(get_db)):
    """P0 diagnostic (2026-09-01, Kabroda AI Brain AGENT_LOG.md, 'CONFIRMED
    P0: state machine missed a live cross'): TradePlan's real intraday
    state was invisible everywhere -- /api/radar/snapshot's `plan` field
    reads CampaignLog (lock-time only, never updated intraday), and
    nothing public ever surfaced TradePlan.status/last_transition_reason
    at all. This is read-only visibility into the real row while the
    root cause is investigated -- includes a staleness indicator
    (seconds since the row last changed) per DeepSeek's own remediation
    item #3, so a frozen row is now detectable instead of silently
    assumed current. Admin only.
    """
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)

    from database import TradePlan as _TradePlan
    today = session_manager.resolve_current_session(datetime.now(timezone.utc), "AUTO")["date_key"]
    rows = db.query(_TradePlan).filter(_TradePlan.date_key == today).order_by(_TradePlan.id.desc()).all()

    now_utc = datetime.now(timezone.utc)

    def _seconds_stale(dt):
        if dt is None:
            return None
        d = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        return round((now_utc - d).total_seconds(), 1)

    out = []
    for r in rows:
        out.append({
            "id": r.id, "symbol": r.symbol, "session_id": r.session_id,
            "status": r.status, "direction": r.direction, "tier": r.tier,
            "trigger_price": r.trigger_price, "stop_price": r.stop_price,
            "t1": r.t1, "t2": r.t2, "t3": r.t3,
            "cross_time": r.cross_time.isoformat() if r.cross_time else None,
            "fuel_at_cross": r.fuel_at_cross,
            "fill_time": r.fill_time.isoformat() if r.fill_time else None,
            "last_transition_reason": r.last_transition_reason,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "seconds_since_update": _seconds_stale(r.updated_at),
        })

    return JSONResponse({"ok": True, "date_key": today, "server_time": now_utc.isoformat(), "rows": out})


@app.get("/api/admin/traveler-plan-status")
async def api_admin_traveler_plan_status(request: Request, db: Session = Depends(get_db)):
    """CC_WORK_ORDER_RADAR_PLAN_PANEL.md work item 2 -- the GATE_TRAVELER
    counterpart to /api/admin/trade-plan-status above. Same read-only,
    admin-only, staleness-indicator pattern.

    2026-09-23: the original "Status + levels only -- no management-walk
    detail, D3 internals stay in the executor admin" scope note (this
    docstring's own prior text) is deliberately REVERSED here, not an
    oversight -- Andy's own ask, part of the strategic site audit's radar-
    rebuild-around-Traveler-communication work: the D3 close (the single
    terminal management event MGMT_E1_STACK ever produces -- no partial
    T1 leg, see mgmt_e1_stack.py's own header) now also shows on this same
    panel, via the `mgmt_*` fields below. Extending this one route rather
    than adding a second admin route over the same object: this codebase
    has exactly one admin status route per entity today, and the real
    "stays in the executor admin" destination the old note meant is
    `/api/executor/orders` (a different audience -- login-only, per-
    account), not a second admin-only route for this same panel/poll
    cadence.

    `mgmt_*` fields are sourced from ONE ExecutorOrder linked to this
    TravelerPlan -- preferring a LIVE-mode order if one exists for this
    journey, else the most recent DRY_RUN order, NEVER a blind "last
    inserted" pick. This matters because ExecutorOrder's real unique
    constraint is (traveler_plan_id, account_id), not just traveler_plan_id
    -- multiple accounts can each get their own order against the same
    journey by design (executor_engine.py::process_traveler_fill() loops
    every active account), so a naive order_by(id.desc()).first() could
    silently flip between accounts. Same class of radar-mislead risk this
    route already fixed once (any_account_live, 2026-09-22) -- mirrored
    here, not reinvented. All mgmt_* fields are None when no ExecutorOrder
    exists yet for this journey (graceful degradation, matching this
    route's own existing style elsewhere).

    Deliberately NOT scoped to today's date_key, unlike the TradePlan
    query above -- TravelerPlan's own design allows a WAITING_TOUCH
    journey to span up to 7 days (traveler_plan_engine.py's own header:
    "a row can poll across multiple days, unlike TradePlan's WAITING"). A
    literal date_key == today filter would go blank on day 2+ of an
    active, still-live journey -- a real case in production as of
    2026-09-19 (TravelerPlan id 2, crossed 09-18, still WAITING_TOUCH
    on 09-19). Returns the single most-recently-touched row instead, which
    is always the current journey regardless of which day it started.

    2026-09-22: each row also carries `any_account_live` -- a real,
    freshly-queried check (not a cached/hardcoded assumption) of whether
    any active ExecutorAccount is actually running GATE_TRAVELER in LIVE
    mode right now. Added so the radar panel can show a genuine LIVE
    indicator instead of a fixed "DRY_RUN" label that would have gone
    stale the moment an account was flipped LIVE (audit Finding 2
    follow-up, AGENT_LOG.md same date)."""
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)

    from database import TravelerPlan as _TravelerPlan
    from database import ExecutorAccount as _ExecutorAccount
    from database import ExecutorOrder as _ExecutorOrder
    row = db.query(_TravelerPlan).order_by(_TravelerPlan.id.desc()).first()

    # 2026-09-22 audit Finding 2 follow-up (Andy's own question: will the
    # radar mislead once we go live?): GATE_TRAVELER stopped being
    # structurally DRY_RUN-only 2026-09-20 (c7243ef) -- a LIVE-mode account
    # with this profile places real orders today. The panel used to
    # hardcode "DRY_RUN" in its header with no way to know if that was
    # still true. Real, cheap, read-only check instead of a fixed label:
    # is any active account actually running GATE_TRAVELER in LIVE mode
    # right now? Same thin-surface pattern as the rest of this route --
    # read DB state only, no exchange call.
    any_account_live = db.query(_ExecutorAccount).filter(
        _ExecutorAccount.is_active == True,
        _ExecutorAccount.gate_profile == "GATE_TRAVELER",
        _ExecutorAccount.mode == "LIVE",
    ).first() is not None

    now_utc = datetime.now(timezone.utc)

    def _seconds_stale(dt):
        if dt is None:
            return None
        d = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        return round((now_utc - d).total_seconds(), 1)

    out = []
    if row is not None:
        # 2026-09-23 -- the D3 management detail this route now also
        # surfaces (see this route's own docstring for the full "why").
        # Prefer a LIVE-mode order for this journey; only fall back to the
        # most recent DRY_RUN order if no LIVE one exists. Never a blind
        # order_by(id.desc()).first() across both modes -- see docstring.
        mgmt_order = (
            db.query(_ExecutorOrder)
            .filter(_ExecutorOrder.traveler_plan_id == row.id, _ExecutorOrder.mode == "LIVE")
            .order_by(_ExecutorOrder.id.desc()).first()
            or db.query(_ExecutorOrder)
            .filter(_ExecutorOrder.traveler_plan_id == row.id, _ExecutorOrder.mode == "DRY_RUN")
            .order_by(_ExecutorOrder.id.desc()).first()
        )
        if mgmt_order is not None:
            mgmt_fields = {
                "mgmt_mode": mgmt_order.mode,
                "mgmt_state": mgmt_order.management_state,
                "mgmt_entry_fill_price": mgmt_order.entry_fill_price,
                "mgmt_entry_fill_time": mgmt_order.entry_fill_time.isoformat() if mgmt_order.entry_fill_time else None,
                "mgmt_exit_reason": mgmt_order.exit_reason,
                "mgmt_exit_price": mgmt_order.exit_price,
                "mgmt_exit_time": mgmt_order.exit_time.isoformat() if mgmt_order.exit_time else None,
                "mgmt_realized_pnl_r": mgmt_order.realized_pnl_r,
                "mgmt_c5_fired": mgmt_order.c5_fired,
                "mgmt_bbwp_fired": mgmt_order.bbwp_fired,
                # Computed here, not a stored column -- exit_reason alone
                # fully and permanently determines this (the fixed 5-value
                # MGMT_E1_STACK mapping: STOP/T1 are always real fills on
                # both lineages; C5_EXIT/BBWP_EXIT/TIME are only ever
                # approximated on LIVE, via _market_close_traveler_order()'s
                # own approximated=True -- DRY_RUN's own exit price is
                # always candle-sourced and deterministic, never approximated).
                "mgmt_exit_approximated": (
                    mgmt_order.mode == "LIVE" and mgmt_order.exit_reason in ("C5_EXIT", "BBWP_EXIT", "TIME")
                ),
            }
        else:
            mgmt_fields = {
                "mgmt_mode": None, "mgmt_state": None,
                "mgmt_entry_fill_price": None, "mgmt_entry_fill_time": None,
                "mgmt_exit_reason": None, "mgmt_exit_price": None, "mgmt_exit_time": None,
                "mgmt_realized_pnl_r": None, "mgmt_c5_fired": None, "mgmt_bbwp_fired": None,
                "mgmt_exit_approximated": False,
            }

        out.append({
            "id": row.id, "symbol": row.symbol, "session_id": row.session_id, "date_key": row.date_key,
            "status": row.status, "direction": row.direction,
            "breakout_trigger": row.breakout_trigger, "breakdown_trigger": row.breakdown_trigger,
            "stop_price": row.stop_price, "t1_price": row.t1_price,
            "cross_time": row.cross_time.isoformat() if row.cross_time else None,
            "cross_price": row.cross_price,
            "rsi_4h_at_lock": row.rsi_4h_at_lock,   # v2-style audit/display value only -- NOT what the skip/F_A read
            "rsi_4h_at_cross": row.rsi_4h_at_cross,  # the measured-basis value the skip and F_A actually use
            "tercile_skipped": row.tercile_skipped,
            "fill_time": row.fill_time.isoformat() if row.fill_time else None,
            "fill_price": row.fill_price,
            "journey_cap_at": row.journey_cap_at.isoformat() if row.journey_cap_at else None,
            "last_transition_reason": row.last_transition_reason,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "seconds_since_update": _seconds_stale(row.updated_at),
            "any_account_live": any_account_live,
            **mgmt_fields,
        })

    return JSONResponse({"ok": True, "server_time": now_utc.isoformat(), "rows": out})


# ==============================================================================
# EXECUTOR BOT ADMIN API -- Andy's request, design settled over a multi-day
# conversation with DeepSeek (Kabroda AI Brain repo AGENT_LOG.md,
# 2026-09-04). Stage 1 (DRY-RUN) routes are a thin surface: read DB state,
# write control flags, never import executor_bitunix_client.py, never
# call executor_accounts.get_decrypted_credentials() directly.
#
# Stage 2 (2026-09-05) added the tiny order mechanism test (REAL MONEY,
# routed through executor_mechanism_test.py) -- removed 2026-09-23 (V2
# Crown retirement, Andy's ruling: retire alongside V2). The persistent
# ExecutorGlobalConfig.live_orders_enabled flag + per-call confirmation
# phrase pattern it introduced stays as shared infrastructure below.
# ==============================================================================

from database import (
    ExecutorAccount as _ExecutorAccount, ExecutorOrder as _ExecutorOrder,
    ExecutorAuditLog as _ExecutorAuditLog, ExecutorGlobalConfig as _ExecutorGlobalConfig,
    ExecutorSizingPolicy as _ExecutorSizingPolicy,
)
import executor_accounts as _executor_accounts
import executor_control as _executor_control
import executor_plan_builder as _executor_plan_builder
import executor_sizing as _executor_sizing

# Stage 2 (2026-09-05) real-money confirm phrase -- kept for the shared
# live-orders global gate below. The tiny-test-specific confirm phrases
# (place/partial-close/move-sl/flash-close/resting-t1-limit/concurrent-
# t1-t3-limits) were removed 2026-09-23 along with the routes that used
# them -- see that removal comment further down.
_CONFIRM_ENABLE_LIVE_ORDERS = "CONFIRM ENABLE LIVE ORDERS"


def _executor_owner_or_admin(ctx: Dict[str, Any], account: Optional["_ExecutorAccount"]) -> bool:
    # 2026-09-07, Andy's explicit instruction (stagnant-surface sweep):
    # full mutual isolation between users on the executor domain -- "I
    # don't wanna be able to see his stuff, he doesn't care about mine."
    # The account LIST route already had zero admin bypass (2026-09-05,
    # same reasoning) -- this brought every individual account's detail/
    # action routes (credentials, mode, sizing, kill switch, mechanism
    # test) into agreement with that, rather than leaving an inconsistent
    # back door on one side and not the other. `is_admin` still governs
    # everything ELSE on the site (account creation's user picker, other
    # admin-only pages) -- this function is executor-account-ownership
    # specific, not a general admin check.
    user = ctx.get("user")
    return bool(account and user and account.user_id == getattr(user, "id", None))


class ExecutorCreateAccountRequest(BaseModel):
    # 2026-09-07: user_id now optional. Self-service creators (see the
    # route) never need to send it -- it's forced to their own id
    # server-side. Only an admin using the "create for someone else"
    # picker needs to pass a different id.
    user_id: Optional[int] = None
    label: str
    exchange: str = "bitunix"


class ExecutorSetCredentialsRequest(BaseModel):
    api_key: str
    api_secret: str


class ExecutorKillSwitchRequest(BaseModel):
    reason: Optional[str] = None


class ExecutorModeChangeRequest(BaseModel):
    mode: str
    confirm: Optional[str] = None


class ExecutorProfileChangeRequest(BaseModel):
    """Phase 2 Ruling A (AGENT_LOG.md, Kabroda AI Brain repo, 2026-09-15
    22:10 CT) -- either field omitted leaves it unchanged (independent
    gate/management selection, same as executor_accounts.set_account_
    profile()'s own signature). confirm is only checked when the account
    is already LIVE and this is a real change -- see that function."""
    gate_profile: Optional[str] = None
    mgmt_profile: Optional[str] = None
    confirm: Optional[str] = None


class ExecutorAssumedBalanceRequest(BaseModel):
    """CC_WORK_ORDER_ASSUMED_BALANCE.md (2026-09-16) -- null clears it."""
    assumed_balance_usd: Optional[float] = None


class ExecutorRiskStateUpdateRequest(BaseModel):
    risk_last_usd: Optional[float] = None
    risk_floor_usd: Optional[float] = None
    risk_cap_usd: Optional[float] = None
    compounding_factor: Optional[float] = None


class ExecutorSizingPolicyUpdateRequest(BaseModel):
    """All fields optional -- this is a PARTIAL update. A field must be
    explicitly present in the JSON body (even as `null`, to clear it) to
    take effect; an omitted field is left untouched on the existing row.
    Routes below use body.model_dump(exclude_unset=True) specifically to
    preserve that distinction (an omitted field is NOT the same as an
    explicit null here, unlike ExecutorRiskStateUpdateRequest above)."""
    preset_name: Optional[str] = None
    base_risk_usd: Optional[float] = None
    base_risk_pct: Optional[float] = None
    roll_in_pct: Optional[float] = None
    cap_abs_usd: Optional[float] = None
    cap_pct: Optional[float] = None
    tier_threshold_usd: Optional[float] = None
    tier_flat_usd: Optional[float] = None
    band_step_usd: Optional[float] = None
    band_risk_per_step_usd: Optional[float] = None
    band_below_pct: Optional[float] = None
    band_max_risk_usd: Optional[float] = None
    derisk_n: Optional[int] = None
    derisk_factor: Optional[float] = None


class ExecutorTradeResultRequest(BaseModel):
    pnl_usd: float
    trade_plan_id: Optional[int] = None


class ExecutorLiveOrdersEnableRequest(BaseModel):
    reason: str
    confirm: str


# TinyTestPlaceRequest/TinyTestPartialCloseRequest/TinyTestConfirmOnlyRequest/
# TinyTestPlaceT1LimitRequest/TinyTestPlaceT1T3LimitsRequest removed
# 2026-09-23 along with the tiny-test routes that used them (V2 Crown
# retirement, executor_mechanism_test.py retired alongside V2).


def _serialize_account(account: "_ExecutorAccount", db: Session) -> Dict[str, Any]:
    # Credential fields are NEVER included, on purpose -- not even a
    # masked/truncated form.
    # P0-2 (CC_WORK_ORDER_LIVE_DAY_2026-09-19.md): sizing_confirmed exposes
    # the SAME real gate set_account_mode()'s own DRY_RUN->LIVE check
    # already enforces (policy.preset_name not in (None, "steady_grow",
    # "conservative")) -- account 11 (dawson_bitu) is a real, live example
    # today: still steady_grow since its 09-06 init, silently blocking
    # every real order it would otherwise place, with nothing in the UI
    # ever telling Andy this is why. Read-only flag, no new gate -- the
    # real enforcement already lives in executor_engine.py/set_account_
    # mode(); this just stops it from being invisible.
    policy = _executor_accounts.get_or_init_sizing_policy(db, account)
    return {
        "id": account.id, "user_id": account.user_id, "label": account.label,
        "exchange": account.exchange, "mode": account.mode, "is_active": account.is_active,
        "kill_switch_engaged": account.kill_switch_engaged,
        "kill_switch_reason": account.kill_switch_reason,
        "margin_mode": account.margin_mode, "leverage_baseline": account.leverage_baseline,
        "assumed_balance_usd": account.assumed_balance_usd,
        "has_credentials": bool(account.api_key_encrypted),
        "credential_set_at": account.credential_set_at.isoformat() if account.credential_set_at else None,
        # Phase 2 (2026-09-15): the RESOLVED profile (gate_profile_of()/
        # mgmt_profile_of(), never the raw nullable column) -- a brand-new
        # or never-touched account reads as today's exact v2 behavior.
        "gate_profile": _executor_accounts.gate_profile_of(account),
        "mgmt_profile": _executor_accounts.mgmt_profile_of(account),
        "sizing_confirmed": policy.preset_name not in (None, "steady_grow", "conservative"),
    }


@app.get("/api/executor/accounts")
async def api_executor_list_accounts(request: Request, db: Session = Depends(get_db)):
    # 2026-09-05: deliberately NO admin bypass here, on Andy's own explicit
    # request -- each real user (his own account, "Gross Monkey"'s own
    # separate account) runs an independent real exchange account, and
    # seeing everyone's mixed together by default is a real risk of
    # confusing/misclicking on the wrong one's real money.
    # UPDATE 2026-09-07: the emergency-intervention exception this comment
    # used to describe is GONE -- _executor_owner_or_admin() no longer has
    # an is_admin bypass at all (see that function's own comment). Full
    # mutual isolation now applies at BOTH the list level (here) and every
    # individual account-scoped route. is_admin grants zero visibility or
    # action on another user's executor account, even between two admins.
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Login required."}, status_code=403)
    accounts = db.query(_ExecutorAccount).filter(
        _ExecutorAccount.user_id == ctx["user"].id
    ).order_by(_ExecutorAccount.id).all()
    return JSONResponse({"ok": True, "accounts": [_serialize_account(a, db) for a in accounts]})


@app.post("/api/executor/accounts")
async def api_executor_create_account(request: Request, body: ExecutorCreateAccountRequest, db: Session = Depends(get_db)):
    # 2026-09-07: self-service creation, per Andy's explicit ask -- each
    # user ("Gross Monkey" included) should be able to walk in and link
    # up their OWN exchange account without needing an admin to do it
    # for them, the same way Andy set his own up. Any logged-in user may
    # create an account for THEMSELVES (user_id forced to their own id,
    # ignoring whatever the client sent, if anything). An admin may still
    # create one on behalf of a specific other user (the roster picker in
    # executor_admin.html) by passing a different user_id -- kept for
    # onboarding help, not required for a user to get started.
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Login required."}, status_code=403)
    target_user_id = ctx["user"].id
    if body.user_id is not None and body.user_id != ctx["user"].id:
        if not ctx.get("is_admin"):
            return JSONResponse({"ok": False, "error": "Only an admin can create an account for another user."}, status_code=403)
        target_user_id = body.user_id
    account = _executor_accounts.create_account(db, user_id=target_user_id, label=body.label, exchange=body.exchange, created_by=ctx.get("email"))
    db.commit()
    return JSONResponse({"ok": True, "account": _serialize_account(account, db)})


@app.post("/api/executor/accounts/{account_id}/credentials")
async def api_executor_set_credentials(account_id: int, request: Request, body: ExecutorSetCredentialsRequest, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
    if account is None:
        return JSONResponse({"ok": False, "error": "No such account."}, status_code=404)
    if not _executor_owner_or_admin(ctx, account):
        return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)
    _executor_accounts.set_credentials(db, account, body.api_key, body.api_secret, set_by=ctx.get("email") or "unknown")
    db.commit()
    # Response NEVER echoes the submitted value -- confirmation only.
    return JSONResponse({"ok": True, "credential_set_at": account.credential_set_at.isoformat()})


@app.post("/api/executor/accounts/{account_id}/test-connection")
async def api_executor_test_connection(account_id: int, request: Request, db: Session = Depends(get_db)):
    """"verify-auth" (2026-09-05, Andy + DeepSeek's own framing: this
    needs no live signal, run it on demand, today). Bitunix has no demo/
    paper-trading environment -- verified, see AGENT_LOG.md and
    executor_bitunix_client.py's own header -- so this is the real
    substitute: three independent REAL, READ-ONLY calls that prove the
    signing chain and credentials work, with zero financial risk. No
    order is ever placed by this route. Each call is reported
    independently (one failing doesn't hide whether the others passed) --
    genuinely useful diagnostic if the signing chain is only partially
    right (e.g. a bug specific to one endpoint's query-param shape).
    """
    ctx = get_user_context(request, db)
    account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
    if account is None:
        return JSONResponse({"ok": False, "error": "No such account."}, status_code=404)
    if not _executor_owner_or_admin(ctx, account):
        return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)

    api_key, api_secret = _executor_accounts.get_decrypted_credentials(account)
    if not api_key or not api_secret:
        return JSONResponse({"ok": False, "error": "No credentials set on this account yet."}, status_code=400)

    import executor_bitunix_client
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)

    checks = {}
    for name, coro in (
        ("get_balance", client.get_balance()),
        ("get_leverage_and_margin_mode", client.get_leverage_and_margin_mode("BTCUSDT")),
        ("get_trading_pairs", client.get_trading_pairs("BTCUSDT")),
    ):
        try:
            checks[name] = {"ok": True, "response": await coro}
        except Exception as e:
            checks[name] = {"ok": False, "error": str(e)}

    all_ok = all(c["ok"] for c in checks.values())
    _executor_accounts.write_audit(
        db, "CONNECTION_TESTED" if all_ok else "ERROR",
        f"verify-auth {'succeeded' if all_ok else 'partially or fully failed'} for account {account.id}",
        account_id=account.id, actor=ctx.get("email") or "unknown", detail=checks,
    )
    db.commit()
    return JSONResponse({"ok": all_ok, "checks": checks})


@app.post("/api/executor/accounts/{account_id}/mode")
async def api_executor_set_account_mode(account_id: int, request: Request, body: ExecutorModeChangeRequest, db: Session = Depends(get_db)):
    # 2026-09-07 -- until this route existed, NOTHING could ever set an
    # account's mode to LIVE (see executor_accounts.set_account_mode()'s
    # own docstring). The confirm-phrase check for DRY_RUN->LIVE lives in
    # that function, not duplicated here -- one place owns the phrase.
    ctx = get_user_context(request, db)
    account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
    if account is None:
        return JSONResponse({"ok": False, "error": "No such account."}, status_code=404)
    if not _executor_owner_or_admin(ctx, account):
        return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)
    try:
        _executor_accounts.set_account_mode(db, account, body.mode, by=ctx.get("email") or "unknown", confirm=body.confirm)
    except ValueError as e:
        db.rollback()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    db.commit()
    return JSONResponse({"ok": True, "account": _serialize_account(account, db)})


@app.post("/api/executor/accounts/{account_id}/profile")
async def api_executor_set_account_profile(account_id: int, request: Request, body: ExecutorProfileChangeRequest, db: Session = Depends(get_db)):
    """Phase 2 Ruling A (AGENT_LOG.md, Kabroda AI Brain repo, 2026-09-15
    22:10 CT) -- the activation mechanism for GATE_TRAVELER/MGMT_E1_STACK.
    Without this route, no account could ever be switched onto the new
    profiles at all -- direct DB edits would violate the audited-action
    bar every other account mutation in this file follows. The LIVE-mode
    confirm-phrase check lives in set_account_profile() itself, not
    duplicated here -- same "one place owns the phrase" precedent as the
    mode-change route above."""
    ctx = get_user_context(request, db)
    account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
    if account is None:
        return JSONResponse({"ok": False, "error": "No such account."}, status_code=404)
    if not _executor_owner_or_admin(ctx, account):
        return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)
    try:
        _executor_accounts.set_account_profile(
            db, account, gate_profile=body.gate_profile, mgmt_profile=body.mgmt_profile,
            by=ctx.get("email") or "unknown", confirm=body.confirm,
        )
    except ValueError as e:
        db.rollback()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    db.commit()
    return JSONResponse({"ok": True, "account": _serialize_account(account, db)})


@app.post("/api/executor/accounts/{account_id}/assumed-balance")
async def api_executor_set_assumed_balance(account_id: int, request: Request, body: ExecutorAssumedBalanceRequest, db: Session = Depends(get_db)):
    """CC_WORK_ORDER_ASSUMED_BALANCE.md (2026-09-16) -- the fallback balance
    source executor_plan_builder.py uses for an account with no
    credentials (every DRY_RUN evaluation account) or a failed live
    balance query. Was display-only before this route existed -- direct DB
    edits would violate the audited-action bar every other account
    mutation in this file follows."""
    ctx = get_user_context(request, db)
    account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
    if account is None:
        return JSONResponse({"ok": False, "error": "No such account."}, status_code=404)
    if not _executor_owner_or_admin(ctx, account):
        return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)
    try:
        _executor_accounts.set_assumed_balance(db, account, body.assumed_balance_usd, by=ctx.get("email") or "unknown")
    except ValueError as e:
        db.rollback()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    db.commit()
    return JSONResponse({"ok": True, "account": _serialize_account(account, db)})


@app.post("/api/executor/accounts/{account_id}/kill-switch")
async def api_executor_engage_kill_switch(account_id: int, request: Request, body: ExecutorKillSwitchRequest, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
    if account is None:
        return JSONResponse({"ok": False, "error": "No such account."}, status_code=404)
    if not _executor_owner_or_admin(ctx, account):
        return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)
    _executor_accounts.engage_kill_switch(db, account, reason=body.reason or "manual", by=ctx.get("email") or "unknown")
    db.commit()
    return JSONResponse({"ok": True, "account": _serialize_account(account, db)})


@app.post("/api/executor/accounts/{account_id}/kill-switch/release")
async def api_executor_release_kill_switch(account_id: int, request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
    if account is None:
        return JSONResponse({"ok": False, "error": "No such account."}, status_code=404)
    if not _executor_owner_or_admin(ctx, account):
        return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)
    _executor_accounts.release_kill_switch(db, account, by=ctx.get("email") or "unknown")
    db.commit()
    return JSONResponse({"ok": True, "account": _serialize_account(account, db)})


@app.post("/api/executor/global-kill-switch")
async def api_executor_engage_global_kill_switch(request: Request, body: ExecutorKillSwitchRequest, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)
    _executor_control.engage_global_kill_switch(db, reason=body.reason or "manual", by=ctx.get("email") or "unknown")
    db.commit()
    return JSONResponse({"ok": True})


@app.post("/api/executor/global-kill-switch/release")
async def api_executor_release_global_kill_switch(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)
    _executor_control.release_global_kill_switch(db, by=ctx.get("email") or "unknown")
    db.commit()
    return JSONResponse({"ok": True})


@app.get("/api/executor/accounts/{account_id}/risk-state")
async def api_executor_get_risk_state(account_id: int, request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
    if account is None:
        return JSONResponse({"ok": False, "error": "No such account."}, status_code=404)
    if not _executor_owner_or_admin(ctx, account):
        return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)
    state = _executor_accounts.get_or_init_risk_state(db, account)
    db.commit()
    return JSONResponse({"ok": True, "risk_state": {
        "risk_last_usd": state.risk_last_usd, "risk_floor_usd": state.risk_floor_usd,
        "risk_cap_usd": state.risk_cap_usd, "compounding_factor": state.compounding_factor,
        "last_trade_pnl_usd": state.last_trade_pnl_usd,
    }})


@app.post("/api/executor/accounts/{account_id}/risk-state")
async def api_executor_update_risk_state(account_id: int, request: Request, body: ExecutorRiskStateUpdateRequest, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
    if account is None:
        return JSONResponse({"ok": False, "error": "No such account."}, status_code=404)
    if not _executor_owner_or_admin(ctx, account):
        return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)
    changes = {field: getattr(body, field) for field in
               ("risk_last_usd", "risk_floor_usd", "risk_cap_usd", "compounding_factor")
               if getattr(body, field) is not None}
    state = _executor_accounts.update_risk_state(db, account, changes, updated_by=ctx.get("email") or "unknown")
    db.commit()
    return JSONResponse({"ok": True, "risk_state": {
        "risk_last_usd": state.risk_last_usd, "risk_floor_usd": state.risk_floor_usd,
        "risk_cap_usd": state.risk_cap_usd, "compounding_factor": state.compounding_factor,
    }})


def _serialize_sizing_policy(policy: "_ExecutorSizingPolicy") -> Dict[str, Any]:
    return {
        "preset_name": policy.preset_name,
        "base_risk_usd": policy.base_risk_usd, "base_risk_pct": policy.base_risk_pct,
        "roll_in_pct": policy.roll_in_pct,
        "cap_abs_usd": policy.cap_abs_usd, "cap_pct": policy.cap_pct,
        "tier_threshold_usd": policy.tier_threshold_usd, "tier_flat_usd": policy.tier_flat_usd,
        "band_step_usd": policy.band_step_usd, "band_risk_per_step_usd": policy.band_risk_per_step_usd,
        "band_below_pct": policy.band_below_pct, "band_max_risk_usd": policy.band_max_risk_usd,
        "derisk_n": policy.derisk_n, "derisk_factor": policy.derisk_factor,
    }


@app.get("/api/executor/accounts/{account_id}/sizing-policy")
async def api_executor_get_sizing_policy(account_id: int, request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
    if account is None:
        return JSONResponse({"ok": False, "error": "No such account."}, status_code=404)
    if not _executor_owner_or_admin(ctx, account):
        return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)
    policy = _executor_accounts.get_or_init_sizing_policy(db, account)
    db.commit()
    return JSONResponse({"ok": True, "sizing_policy": _serialize_sizing_policy(policy)})


@app.post("/api/executor/accounts/{account_id}/sizing-policy")
async def api_executor_update_sizing_policy(account_id: int, request: Request, body: ExecutorSizingPolicyUpdateRequest, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
    if account is None:
        return JSONResponse({"ok": False, "error": "No such account."}, status_code=404)
    if not _executor_owner_or_admin(ctx, account):
        return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)
    changes = body.model_dump(exclude_unset=True)
    try:
        policy = _executor_accounts.update_sizing_policy(db, account, changes, updated_by=ctx.get("email") or "unknown")
    except ValueError as e:
        db.rollback()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    db.commit()
    return JSONResponse({"ok": True, "sizing_policy": _serialize_sizing_policy(policy)})


@app.post("/api/executor/accounts/{account_id}/sizing-policy/preview")
async def api_executor_preview_sizing_policy(account_id: int, request: Request, body: ExecutorSizingPolicyUpdateRequest, db: Session = Depends(get_db)):
    """NEVER persists -- computes what compute_stake() would return for
    the candidate policy (existing row overlaid with this request's
    explicitly-set fields, same merge semantics as the real update route)
    under three scenarios: the account's CURRENT state, and what it would
    look like immediately after a +2R win or a -1R loss got rolled in.
    Server-side only, same compute_stake() the real sizing call site
    uses -- this codebase's own precedent (verify-auth, the liquidation
    check, MMR tiers) never duplicates math into JS."""
    ctx = get_user_context(request, db)
    account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
    if account is None:
        return JSONResponse({"ok": False, "error": "No such account."}, status_code=404)
    if not _executor_owner_or_admin(ctx, account):
        return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)

    policy = _executor_accounts.get_or_init_sizing_policy(db, account)
    changes = body.model_dump(exclude_unset=True)
    merged = {
        "base_risk_usd": policy.base_risk_usd, "base_risk_pct": policy.base_risk_pct,
        "roll_in_pct": policy.roll_in_pct, "cap_abs_usd": policy.cap_abs_usd, "cap_pct": policy.cap_pct,
        "tier_threshold_usd": policy.tier_threshold_usd, "tier_flat_usd": policy.tier_flat_usd,
        "band_step_usd": policy.band_step_usd, "band_risk_per_step_usd": policy.band_risk_per_step_usd,
        "band_below_pct": policy.band_below_pct, "band_max_risk_usd": policy.band_max_risk_usd,
        "derisk_n": policy.derisk_n, "derisk_factor": policy.derisk_factor,
    }
    merged.update(changes)
    # Same "setting only one of base_risk_usd/base_risk_pct clears the
    # other" rule update_sizing_policy() applies -- see that function's
    # own comment for why the "both explicitly set" case is left alone.
    switching_base = ("base_risk_usd" in changes) != ("base_risk_pct" in changes)
    if switching_base:
        if changes.get("base_risk_usd") is not None:
            merged["base_risk_pct"] = None
        elif changes.get("base_risk_pct") is not None:
            merged["base_risk_usd"] = None
    # Same "switching TO banded clears the base + tier + standalone caps"
    # rule as update_sizing_policy() -- so the preview matches what saving
    # produces (banded_risk carries its own band_max_risk_usd ceiling).
    if changes.get("band_step_usd") is not None:
        for _f in ("base_risk_usd", "base_risk_pct", "tier_threshold_usd",
                   "tier_flat_usd", "cap_abs_usd", "cap_pct"):
            if _f not in changes:
                merged[_f] = None
    try:
        _executor_accounts._validate_sizing_policy(merged)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    risk_state = _executor_accounts.get_or_init_risk_state(db, account)
    db.commit()

    balance_usd: Optional[float] = None
    balance_source = "not queried -- policy does not use account balance"
    if (merged["base_risk_pct"] is not None or merged["cap_pct"] is not None
            or merged["tier_threshold_usd"] is not None or merged["band_step_usd"] is not None):
        balance_state = await _executor_plan_builder._query_real_balance(account)
        balance_usd = balance_state["balance_usd"]
        balance_source = balance_state["source"]

    # 2026-09-07: mirrors update_sizing_policy()'s own fix -- if this
    # preview is for a NEW base_risk_usd (different from what's already
    # saved), "Current" must show what saving would actually produce
    # (risk_last_usd reset to the new base), not the stale saved value.
    # Never persisted here (this route never commits the reset) -- purely
    # what the number WOULD be.
    new_base_risk_usd = changes.get("base_risk_usd")
    effective_risk_last_usd = (
        new_base_risk_usd if (new_base_risk_usd is not None and new_base_risk_usd != policy.base_risk_usd)
        else risk_state.risk_last_usd
    )

    def _stake(risk_last_usd: float, consecutive_losses: int) -> Dict[str, Any]:
        stake, detail = _executor_sizing.compute_stake(
            risk_last_usd=risk_last_usd, base_risk_pct=merged["base_risk_pct"], account_balance_usd=balance_usd,
            tier_threshold_usd=merged["tier_threshold_usd"], tier_flat_usd=merged["tier_flat_usd"],
            band_step_usd=merged["band_step_usd"], band_risk_per_step_usd=merged["band_risk_per_step_usd"],
            band_below_pct=merged["band_below_pct"], band_max_risk_usd=merged["band_max_risk_usd"],
            cap_abs_usd=merged["cap_abs_usd"], cap_pct=merged["cap_pct"],
            consecutive_losses=consecutive_losses, derisk_n=merged["derisk_n"], derisk_factor=merged["derisk_factor"],
        )
        return {**detail, "stake_usd": stake, "balance_source": balance_source}

    try:
        current = _stake(effective_risk_last_usd, risk_state.consecutive_losses)

        # +2R win: pnl = 2x the CURRENT stake, rolled in via compute_next_risk
        # when roll_in_pct is set; a win always resets the loss streak.
        win_pnl = 2.0 * current["stake_usd"]
        risk_last_after_win = effective_risk_last_usd
        if merged["roll_in_pct"] is not None:
            risk_last_after_win = _executor_sizing.compute_next_risk(
                risk_last=effective_risk_last_usd, last_trade_pnl=win_pnl,
                floor=risk_state.risk_floor_usd, cap=merged["cap_abs_usd"] or risk_state.risk_cap_usd,
                factor=merged["roll_in_pct"],
            )
        after_2r_win = _stake(risk_last_after_win, 0)

        # -1R loss: pnl = -1x the CURRENT stake, rolled in the same way;
        # a loss always increments the streak.
        loss_pnl = -1.0 * current["stake_usd"]
        risk_last_after_loss = effective_risk_last_usd
        if merged["roll_in_pct"] is not None:
            risk_last_after_loss = _executor_sizing.compute_next_risk(
                risk_last=effective_risk_last_usd, last_trade_pnl=loss_pnl,
                floor=risk_state.risk_floor_usd, cap=merged["cap_abs_usd"] or risk_state.risk_cap_usd,
                factor=merged["roll_in_pct"],
            )
        after_1r_loss = _stake(risk_last_after_loss, risk_state.consecutive_losses + 1)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": f"preview calculation failed: {e}"}, status_code=400)

    return JSONResponse({"ok": True, "preview": {
        "current": current, "after_2r_win": after_2r_win, "after_1r_loss": after_1r_loss,
        # 2026-09-07, SIZING_AND_ISOLATION.md Part 1 -- the wizard's "current
        # balance shown at top" requirement needs the actual number, not
        # just the human-readable balance_source string each _stake() dict
        # already carries.
        "balance_usd": balance_usd,
    }})


@app.post("/api/executor/accounts/{account_id}/record-trade-result")
async def api_executor_record_trade_result(account_id: int, request: Request, body: ExecutorTradeResultRequest, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
    if account is None:
        return JSONResponse({"ok": False, "error": "No such account."}, status_code=404)
    if not _executor_owner_or_admin(ctx, account):
        return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)
    state = _executor_accounts.record_trade_result(
        db, account, pnl_usd=body.pnl_usd, trade_plan_id=body.trade_plan_id, recorded_by=ctx.get("email") or "unknown")
    db.commit()
    return JSONResponse({"ok": True, "risk_state": {
        "risk_last_usd": state.risk_last_usd, "consecutive_losses": state.consecutive_losses,
        "last_trade_pnl_usd": state.last_trade_pnl_usd,
    }})


@app.get("/api/executor/orders")
async def api_executor_list_orders(request: Request, db: Session = Depends(get_db), account_id: Optional[int] = None, limit: int = 100):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Login required."}, status_code=403)
    q = db.query(_ExecutorOrder)
    if account_id is not None:
        account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
        if account is None or not _executor_owner_or_admin(ctx, account):
            return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)
        q = q.filter(_ExecutorOrder.account_id == account_id)
    else:
        # No admin bypass here either (2026-09-05, same reasoning as
        # api_executor_list_accounts) -- without an explicit account_id,
        # this always scopes to the logged-in user's own accounts.
        owned_ids = [a.id for a in db.query(_ExecutorAccount).filter_by(user_id=ctx["user"].id).all()]
        q = q.filter(_ExecutorOrder.account_id.in_(owned_ids)) if owned_ids else q.filter(_ExecutorOrder.id.is_(None))
    orders = q.order_by(_ExecutorOrder.id.desc()).limit(min(limit, 500)).all()
    return JSONResponse({"ok": True, "orders": [{
        "id": o.id, "trade_plan_id": o.trade_plan_id, "account_id": o.account_id, "mode": o.mode,
        "symbol": o.symbol, "direction": o.direction, "entry_price": o.entry_price, "stop_price": o.stop_price,
        "t1_price": o.t1_price, "t2_price": o.t2_price, "t3_price": o.t3_price,
        "risk_dollars_used": o.risk_dollars_used, "qty": o.qty, "leverage_used": o.leverage_used,
        "margin_required_usd": o.margin_required_usd, "liquidation_price_estimate": o.liquidation_price_estimate,
        "liquidation_check_passed": o.liquidation_check_passed, "decision": o.decision,
        "decision_reason": o.decision_reason, "created_at": o.created_at.isoformat() if o.created_at else None,
        # 2026-09-07, Domain 2 (executor_live_engine.py) -- real managed-
        # trade fields, populated only for mode=LIVE rows that actually
        # placed a real order. Read-only here; this system runs itself,
        # no admin override controls beyond the existing kill switches.
        "tier": o.tier, "management_state": o.management_state, "position_id": o.position_id,
        "entry_status": o.entry_status, "entry_fill_price": o.entry_fill_price,
        "t1_status": o.t1_status, "t1_fill_price": o.t1_fill_price, "t1_leg_r": o.t1_leg_r,
        "t3_status": o.t3_status, "t3_fill_price": o.t3_fill_price, "runner_r": o.runner_r,
        "sl_price_current": o.sl_price_current, "sl_moved_to_be_at": o.sl_moved_to_be_at.isoformat() if o.sl_moved_to_be_at else None,
        "realized_pnl_r": o.realized_pnl_r, "close_reason": o.close_reason,
        "closed_at": o.closed_at.isoformat() if o.closed_at else None,
    } for o in orders]})


@app.get("/api/executor/audit-log")
async def api_executor_audit_log(request: Request, db: Session = Depends(get_db), account_id: Optional[int] = None, limit: int = 200):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Login required."}, status_code=403)
    q = db.query(_ExecutorAuditLog)
    if account_id is not None:
        account = db.query(_ExecutorAccount).filter_by(id=account_id).first()
        if account is None or not _executor_owner_or_admin(ctx, account):
            return JSONResponse({"ok": False, "error": "Not authorized."}, status_code=403)
        q = q.filter(_ExecutorAuditLog.account_id == account_id)
    else:
        # No per-ACCOUNT admin bypass here (2026-09-05, same reasoning as
        # api_executor_list_accounts) -- without an explicit account_id,
        # this scopes to the logged-in user's own accounts, admin
        # included. Exception, deliberately kept: GLOBAL events
        # (account_id IS NULL -- global kill switch, live-orders enable/
        # disable) are admin-only ACTIONS in the first place, not another
        # user's private trade data, so admin still sees those here.
        owned_ids = [a.id for a in db.query(_ExecutorAccount).filter_by(user_id=ctx["user"].id).all()]
        if ctx.get("is_admin"):
            q = q.filter(or_(_ExecutorAuditLog.account_id.in_(owned_ids), _ExecutorAuditLog.account_id.is_(None)))
        else:
            q = q.filter(_ExecutorAuditLog.account_id.in_(owned_ids)) if owned_ids else q.filter(_ExecutorAuditLog.id.is_(None))
    rows = q.order_by(_ExecutorAuditLog.id.desc()).limit(min(limit, 1000)).all()
    return JSONResponse({"ok": True, "audit_log": [{
        "id": r.id, "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
        "account_id": r.account_id, "trade_plan_id": r.trade_plan_id, "executor_order_id": r.executor_order_id,
        "event_type": r.event_type, "actor": r.actor, "message": r.message,
    } for r in rows]})


# _serialize_mechanism_test() removed 2026-09-23 (V2 Crown retirement,
# Andy's ruling: retire executor_mechanism_test.py alongside V2 -- its
# SPLIT/V2-flavored order shape, concurrent T1+T3 limits + BE-move, has no
# Traveler/E1 analog and was never decision-path-coupled). See the tiny-
# test route block's own removal comment further down for the full list.


# ------------------------------------------------------------------ live orders global gate (Stage 2, 2026-09-05)

@app.get("/api/executor/global-config")
async def api_executor_global_config(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Login required."}, status_code=403)
    # Read-only -- never creates the singleton row on a GET.
    cfg = db.query(_ExecutorGlobalConfig).filter_by(config_key="executor_global").first()
    return JSONResponse({
        "ok": True,
        "global_kill_switch_engaged": bool(cfg.global_kill_switch_engaged) if cfg else False,
        "live_orders_enabled": bool(cfg.live_orders_enabled) if cfg else False,
    })


@app.post("/api/executor/live-orders/enable")
async def api_executor_enable_live_orders(request: Request, body: ExecutorLiveOrdersEnableRequest, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)
    if body.confirm != _CONFIRM_ENABLE_LIVE_ORDERS:
        return JSONResponse({"ok": False, "error": f"confirm phrase must be exactly {_CONFIRM_ENABLE_LIVE_ORDERS!r}"}, status_code=400)
    _executor_control.enable_live_orders(db, reason=body.reason, by=ctx.get("email") or "unknown")
    _executor_accounts.write_audit(db, "LIVE_ORDERS_ENABLED", f"live orders enabled -- {body.reason}", actor=ctx.get("email"))
    db.commit()
    return JSONResponse({"ok": True})


@app.post("/api/executor/live-orders/disable")
async def api_executor_disable_live_orders(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)
    # No confirm phrase needed to turn this OFF -- matches the existing
    # release-kill-switch precedent (disabling never needs extra friction).
    _executor_control.disable_live_orders(db, by=ctx.get("email") or "unknown")
    _executor_accounts.write_audit(db, "LIVE_ORDERS_DISABLED", "live orders disabled", actor=ctx.get("email"))
    db.commit()
    return JSONResponse({"ok": True})


# executor_mechanism_test.py and its 10 tiny-test routes (place,
# partial-close, move-sl-breakeven, flash-close, place/check/cancel-
# resting-t1-limit, place/check/cancel-concurrent-t1-t3-limits, list,
# detail) removed 2026-09-23 (V2 Crown retirement, Andy's explicit
# ruling: retire alongside V2) -- a standalone, real-money manual
# diagnostic never wired into either automated engine, with SPLIT/V2-
# flavored order-shape logic (concurrent T1+T3 limits, breakeven move)
# that has no Traveler/MGMT_E1_STACK analog. `ExecutorMechanismTest` rows
# already written stay in the DB per the "leave the table" convention --
# only the code that could create new ones is gone. The shared live-
# orders global gate below (persistent flag + confirm phrase) is
# untouched -- it's not tiny-test-specific infrastructure.


@app.get("/admin/executor")
async def page_executor_admin(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return RedirectResponse("/login", status_code=303)
    if ctx.get("is_admin"):
        # For the "create account" form's user picker -- account creation
        # itself is admin-only (POST /api/executor/accounts), same as the
        # roster page's own user list (main.py's admin_roster_page()).
        ctx["all_users"] = db.query(UserModel).order_by(UserModel.id).all()
    return _template_or_fallback(request, templates, "executor_admin.html", ctx)


# ==============================================================================
# SIGNAL PERFORMANCE TRACKER — API ENDPOINT
# ==============================================================================

class SignalLogRequest(BaseModel):
    """Validated request body for POST /api/signal/log."""
    source: Literal["meta_signals", "kabroda_radar"]    # validated at parse time
    symbol: str                                         # "BTC/USDT" (will be normalized)
    direction: Literal["LONG", "SHORT"]                 # validated at parse time
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    tp1_price: Optional[float] = None
    tp2_price: Optional[float] = None
    tp3_price: Optional[float] = None
    signal_timeframe: Optional[str] = None               # "15M" | "1H" | "4H" | etc.
    price_action_regime: Optional[str] = None            # TRENDING | RANGING | COMPRESSING | EXPANDING
    indicator_snapshot: Optional[dict] = None            # Full multi-TF indicator state
    confluence_score: Optional[int] = None
    dominant_direction: Optional[str] = None
    conviction: Optional[str] = None
    jewel_gate_open: Optional[bool] = None
    jewel_direction: Optional[str] = None
    jewel_conviction: Optional[str] = None
    jewel_summary: Optional[str] = None
    nearest_support: Optional[float] = None
    nearest_resistance: Optional[float] = None
    kabroda_read: Optional[str] = None
    signal_timestamp: str                               # ISO 8601 datetime string


@app.post("/api/signal/log")
async def log_signal_performance(
    body: SignalLogRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Log a full signal performance snapshot to the database.

    Auth: X-API-Key header must match SIGNAL_API_KEY env variable.
    Idempotency: (source, symbol, direction, signal_timestamp) is unique.
    Symbol is normalized to BTC/USDT format on write.
    """
    # --- Auth ---
    api_key = request.headers.get("X-API-Key", "")
    expected_key = os.getenv("SIGNAL_API_KEY", "")
    if not expected_key or not hmac.compare_digest(api_key, expected_key):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    # --- Normalize symbol ---
    from market_data import _normalize_symbol
    norm_symbol = _normalize_symbol(body.symbol)

    # --- Parse timestamp ---
    try:
        ts = datetime.fromisoformat(body.signal_timestamp)
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid signal_timestamp (use ISO 8601)"}, status_code=400)

    # --- Idempotency check ---
    existing = db.query(SignalPerformanceLog).filter(
        SignalPerformanceLog.source == body.source,
        SignalPerformanceLog.symbol == norm_symbol,
        SignalPerformanceLog.direction == body.direction,
        SignalPerformanceLog.signal_timestamp == ts,
    ).first()
    if existing:
        return JSONResponse({"ok": True, "id": existing.id, "duplicate": True})

    # --- Build row ---
    row = SignalPerformanceLog(
        source=body.source,
        symbol=norm_symbol,
        direction=body.direction,
        entry_price=body.entry_price,
        stop_price=body.stop_price,
        tp1_price=body.tp1_price,
        tp2_price=body.tp2_price,
        tp3_price=body.tp3_price,
        signal_timeframe=body.signal_timeframe,
        price_action_regime=body.price_action_regime,
        indicator_snapshot=json.dumps(body.indicator_snapshot) if body.indicator_snapshot else None,
        confluence_score=body.confluence_score,
        dominant_direction=body.dominant_direction,
        conviction=body.conviction,
        jewel_gate_open=body.jewel_gate_open,
        jewel_direction=body.jewel_direction,
        jewel_conviction=body.jewel_conviction,
        jewel_summary=body.jewel_summary,
        nearest_support=body.nearest_support,
        nearest_resistance=body.nearest_resistance,
        kabroda_read=body.kabroda_read,
        signal_timestamp=ts,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return JSONResponse({"ok": True, "id": row.id, "duplicate": False})


@app.get("/api/export/gate-log.csv")
async def export_gate_log_csv(request: Request, since: Optional[str] = None, symbol: Optional[str] = None, db: Session = Depends(get_db)):
    """KABRODA_COM_TRADE_PLAN_SPEC.md SS9a/SS9c -- the ONE site-side piece
    of the forward-test log division of labor (AGENT_LOG.md, DeepSeek/Andy,
    2026-08-31, Kabroda AI Brain repo commit c5487a6): kabroda.com RECORDS
    (writes GateLog -- locks, plans, transitions, everything the site
    knows, mechanical facts only); the Brain AUDITS (§9b drift check, §9c
    reconciliation, and fills the pressure/would_have_r columns on its own
    closure pass). This export is how the Brain pulls rows without
    touching site internals -- kabroda.com does not run any analytics on
    this data itself, by design.

    Auth: X-API-Key header must match GATE_LOG_EXPORT_API_KEY env var
    (same pattern as /api/signal/log's SIGNAL_API_KEY -- fails closed if
    the env var is unset).
    Params: since=YYYY-MM-DD (optional, date_key >= this -- for
    incremental pulls); symbol=BTC/USDT (optional filter).
    Every GateLog column is included, in declaration order -- this
    endpoint does not pick and choose; the Brain's own tooling decides
    what it needs from the full row, matching §9c's "the log is the
    source of truth, everything else is a view over it."
    """
    api_key = request.headers.get("X-API-Key", "")
    expected_key = os.getenv("GATE_LOG_EXPORT_API_KEY", "")
    if not expected_key or not hmac.compare_digest(api_key, expected_key):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    from database import GateLog as _GateLogExport
    query = db.query(_GateLogExport)
    if since:
        query = query.filter(_GateLogExport.date_key >= since)
    if symbol:
        query = query.filter(_GateLogExport.symbol == symbol)
    rows = query.order_by(_GateLogExport.date_key.asc(), _GateLogExport.id.asc()).all()

    columns = [c.name for c in _GateLogExport.__table__.columns]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([getattr(row, col) for col in columns])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=gate_log_export.csv"},
    )


@app.get("/api/export/traveler-log.csv")
async def export_traveler_log_csv(request: Request, since: Optional[str] = None, symbol: Optional[str] = None, db: Session = Depends(get_db)):
    """The Traveler-native counterpart to /api/export/gate-log.csv above,
    built 2026-09-23 (V2 Crown retirement) -- GateLog becomes fully dead
    once decision_engine.py is gone (it's the ONLY thing that ever wrote
    it), and it was the Kabroda AI Brain's only forward-test pull
    mechanism. Andy's explicit ruling: ship this FIRST, before GateLog is
    actually deleted, so the Brain never loses its forward-test data
    source -- not an after-the-fact backfill.

    Auth: same pattern as gate-log.csv -- X-API-Key header must match
    GATE_LOG_EXPORT_API_KEY. Deliberately reuses that same env var rather
    than adding a new one: the name becomes a minor legacy misnomer once
    GateLog itself is gone, but that's a one-line comment, not worth an
    operational change (a new Render env var) during this transition.
    Params: since=YYYY-MM-DD (date_key >= this), symbol=BTC/USDT -- both
    optional, same as gate-log.csv.

    Shape: one flat row per TravelerPlan, every TravelerPlan column (in
    declaration order) plus mgmt_-prefixed columns from its linked
    ExecutorOrder -- TravelerPlan has no P&L/exit field of its own, that
    lifecycle lives entirely on ExecutorOrder (see traveler_radar.py's own
    module docstring for the same fact). Order selection prefers a
    LIVE-mode order over DRY_RUN per plan, resolved in one batch query
    (not one extra round-trip per row) -- the same rule
    /api/admin/traveler-plan-status and /api/dashboard/mas-history already
    use, never a blind "last inserted" pick.
    """
    api_key = request.headers.get("X-API-Key", "")
    expected_key = os.getenv("GATE_LOG_EXPORT_API_KEY", "")
    if not expected_key or not hmac.compare_digest(api_key, expected_key):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    from database import TravelerPlan as _TravelerPlanExport
    query = db.query(_TravelerPlanExport)
    if since:
        query = query.filter(_TravelerPlanExport.date_key >= since)
    if symbol:
        query = query.filter(_TravelerPlanExport.symbol == symbol)
    plans = query.order_by(_TravelerPlanExport.date_key.asc(), _TravelerPlanExport.id.asc()).all()

    plan_ids = [p.id for p in plans]
    order_by_plan = {}
    for o in db.query(_ExecutorOrder).filter(
        _ExecutorOrder.traveler_plan_id.in_(plan_ids)
    ).order_by(_ExecutorOrder.id.desc()).all():
        existing = order_by_plan.get(o.traveler_plan_id)
        if existing is None or (o.mode == "LIVE" and existing.mode != "LIVE"):
            order_by_plan[o.traveler_plan_id] = o

    plan_columns = [c.name for c in _TravelerPlanExport.__table__.columns]
    mgmt_columns = ["mgmt_mode", "mgmt_management_state", "mgmt_entry_fill_price",
                     "mgmt_entry_fill_time", "mgmt_exit_reason", "mgmt_exit_price",
                     "mgmt_exit_time", "mgmt_realized_pnl_r", "mgmt_c5_fired", "mgmt_bbwp_fired"]
    columns = plan_columns + mgmt_columns

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for plan in plans:
        row = [getattr(plan, col) for col in plan_columns]
        order = order_by_plan.get(plan.id)
        if order is not None:
            row += [order.mode, order.management_state, order.entry_fill_price,
                    order.entry_fill_time, order.exit_reason, order.exit_price,
                    order.exit_time, order.realized_pnl_r, order.c5_fired, order.bbwp_fired]
        else:
            row += [None] * len(mgmt_columns)
        writer.writerow(row)

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=traveler_log_export.csv"},
    )


@app.get("/api/agents/cost")
async def api_agents_cost(request: Request, db: Session = Depends(get_db)):
    """Returns 24h and 7-day agent spend summary. Admin only."""
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)
    summary = await asyncio.to_thread(agent_core.get_cost_summary)
    return JSONResponse(summary)


# /api/agents/test-call removed 2026-08-30 -- fired a real, paid
# agent_core._call_agent() invocation for no operational purpose (an old
# "Phase 1 infrastructure test" button). Andy's call: no LLM tied to
# Kabroda's cost path, period. /api/agents/cost (above) stays -- it only
# reads existing cost-log rows, doesn't generate new spend.


@app.get("/indicators")
async def indicators(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "indicators.html", ctx)

@app.get("/account")
async def account(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "account.html", ctx)

@app.post("/account/profile")
async def update_profile(request: Request, payload: Dict[str, Any], db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    if user:
        if "username" in payload: user.username = str(payload["username"]).strip()[:50]
        if "tradingview_id" in payload: user.tradingview_id = str(payload["tradingview_id"]).strip()
        if "session_tz" in payload: user.session_tz = str(payload["session_tz"]).strip()
        db.commit()
    return {"status": "ok", "ok": True}

@app.post("/account/password")
async def update_password(request: Request, payload: Dict[str, Any], db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    new_pass = payload.get("password")
    if not new_pass: return JSONResponse({"ok": False, "error": "No password"}, status_code=400)
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    if user:
        user.password_hash = auth.hash_password(new_pass)
        db.commit()
    return {"ok": True}

@app.post("/account/settings")
async def account_settings(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    data = await request.json()
    if user:
        user.operator_flex = bool(data.get("operator_flex", False))
        db.commit()
    return {"status": "ok"}

# --- ADMIN ROUTES ---
# /admin/simulator + market_simulator.py archived 2026-08-28 -- Andy's call:
# it was an earlier attempt at what Kabroda AI Brain now does properly, and
# its own logic ("Predator Stop"/"Primal Max"/"Jailbreaks") didn't match the
# real, current methodology anyway (a stale duplicate, same class of problem
# market_radar.py's dead scorer had before this session's Phase 4 rewrite).

# /admin/research removed 2026-08-30 -- see the /suite/research-lab removal
# note above.

# /admin/mission + mission_brief.html removed 2026-08-30 -- a static
# glossary page teaching an older, unrelated framework (MAGNET/SUFFOCATED/
# JAILBREAK templates, hardcoded "2025 DATA" stats) that predates even the
# graded-conviction model, let alone tonight's calibrated-gate rebuild.
# Not linked from anywhere live (grepped every template + main.py) -- only
# reachable by typing the exact URL. Archived, not rewritten: the real
# rules now live in CLAUDE.md and KABRODA_REBUILD_SPEC.md, not a hand-authored
# admin page that would need to be kept in sync by hand every time the
# system changes.

@app.get("/admin")
async def admin_roster_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    users = db.query(UserModel).all()
    ctx["users"] = users
    # latest_daily_digest/recent_suggestions (DailyAuditLog/AuditSuggestionLog)
    # removed 2026-09-23 (V2 Crown retirement, Audit-AI surface retirement)
    # -- confirmed dead even before this removal: templates/admin.html never
    # rendered either context key, so this was a wasted query on every page
    # load, not a real dependency.
    # 2026-09-23 -- the admin-manageable email distribution list
    # (notify.py::send_admin_email() reads these same rows directly).
    ctx["email_subscribers"] = db.query(EmailSubscriber).order_by(EmailSubscriber.id.desc()).all()
    return _template_or_fallback(request, templates, "admin.html", ctx)

@app.post("/admin/add-email-subscriber")
async def admin_add_email_subscriber(request: Request, db: Session = Depends(get_db)):
    """2026-09-23 -- adds a real recipient to notify.py's DB-backed
    distribution list, additive to the existing SMTP_DEST env var (see
    that module's own header). No site login required for someone to be
    on this list -- deliberately no FK to UserModel."""
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"): return JSONResponse({"ok": False, "error": "Unauthorized"})
    payload = await request.json()
    email = (payload.get("email") or "").strip().lower()
    label = (payload.get("label") or "").strip() or None
    if not email or "@" not in email:
        return JSONResponse({"ok": False, "error": "A valid email address is required"})
    existing = db.query(EmailSubscriber).filter(EmailSubscriber.email == email).first()
    if existing:
        if existing.is_active:
            return JSONResponse({"ok": False, "error": "That address is already on the list"})
        # Re-adding a previously-removed address reactivates the same row
        # rather than violating the unique constraint with a duplicate.
        existing.is_active = True
        existing.label = label or existing.label
        existing.added_by = ctx.get("email")
        existing.added_at = datetime.utcnow()
        db.commit()
        return JSONResponse({"ok": True})
    db.add(EmailSubscriber(email=email, label=label, is_active=True, added_by=ctx.get("email")))
    db.commit()
    return JSONResponse({"ok": True})

@app.post("/admin/delete-email-subscriber")
async def admin_delete_email_subscriber(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"): return JSONResponse({"ok": False, "error": "Unauthorized"})
    payload = await request.json()
    sub_id = payload.get("subscriber_id")
    try:
        sub_id_int = int(sub_id) if sub_id is not None else None
    except (TypeError, ValueError):
        # A non-numeric subscriber_id used to raise straight into the
        # catch-all HTML 500 handler -- admin.html's JS does `await
        # res.json()` on the response, which would then throw on the HTML
        # body instead of surfacing this specific message. Caught here
        # 2026-09-23 so a bad id is a clean JSON error like every other
        # validation failure in this route/its sibling add route.
        return JSONResponse({"ok": False, "error": "subscriber_id must be a number"})
    sub = db.query(EmailSubscriber).filter(EmailSubscriber.id == sub_id_int).first() if sub_id_int else None
    if not sub:
        return JSONResponse({"ok": False, "error": "Subscriber not found"})
    db.delete(sub)
    db.commit()
    return JSONResponse({"ok": True})

# /admin/export-audit-ledger removed 2026-09-23 (V2 Crown retirement,
# Andy's ruling: retire the whole Audit-AI dashboard surface entirely,
# not rebuilt against Traveler data) -- it was 100% CampaignLog/
# DailyAuditLog/AuditSuggestionLog/TrialsLog-sourced, all four V2-lineage
# tables. Removed together with its nav.html link, its dashboard button
# (copyAuditExport()), /api/v1/system/audit-suggestions, the Analysis
# tab's "Run Analysis" trigger + 12h background scheduler, and the three
# now-orphaned tables -- see AGENT_LOG.md for the full removal record.

@app.post("/admin/delete-user")
async def admin_delete_user(request: Request, user_id: str = Form(...), db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"): return RedirectResponse("/suite")
    user_to_delete = db.query(UserModel).filter(UserModel.id == int(user_id)).first()
    if user_to_delete:
        db.delete(user_to_delete)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/create-user")
async def admin_create_user(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"): return JSONResponse({"ok": False, "error": "Unauthorized"})
    payload = await request.json()
    email = (payload.get("email") or "").strip().lower()
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    if not email or not username or not password:
        return JSONResponse({"ok": False, "error": "Email, username, and password are all required"})
    if db.query(UserModel).filter(UserModel.email == email).first():
        return JSONResponse({"ok": False, "error": "A user with that email already exists"})
    new_user = UserModel(
        email=email,
        username=username,
        first_name=(payload.get("first_name") or None),
        last_name=(payload.get("last_name") or None),
        password_hash=auth.hash_password(password),
        subscription_status="active",
        tier="basic",
        is_admin=False,
    )
    db.add(new_user)
    db.commit()
    return JSONResponse({"ok": True})

@app.post("/admin/toggle-role")
async def admin_toggle_role(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"): return JSONResponse({"ok": False, "error": "Unauthorized"})
    payload = await request.json()
    target_id = payload.get("user_id")
    user_to_toggle = db.query(UserModel).filter(UserModel.id == int(target_id)).first()
    if user_to_toggle:
        user_to_toggle.is_admin = not user_to_toggle.is_admin
        db.commit()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "User not found"})

@app.post("/admin/reset-password-manual")
async def admin_reset_password(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"): return JSONResponse({"ok": False, "error": "Unauthorized"})
    payload = await request.json()
    target_id = payload.get("user_id")
    new_password = payload.get("new_password")
    if not new_password: return JSONResponse({"ok": False, "error": "No password provided"})
    user = db.query(UserModel).filter(UserModel.id == int(target_id)).first()
    if user:
        user.password_hash = auth.hash_password(new_password)
        db.commit()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "User not found"})

@app.get("/admin/interpreter-log")
async def admin_interpreter_log(request: Request, db: Session = Depends(get_db)):
    """Read-only view of the last 10 sessions of interpreter_log rows (admin only).
    Groups by session_date, shows MTF → gravity → junior_analyst in order.
    Used for weekly JA quality audits and bias_model wiring review."""
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return RedirectResponse("/suite")

    rows = (
        db.query(InterpreterLog)
        .filter(InterpreterLog.symbol == "BTC/USDT")
        .order_by(InterpreterLog.session_date.desc(), InterpreterLog.created_at.asc())
        .limit(30)
        .all()
    )

    # Group by session_date, preserve date order (most-recent first)
    from collections import OrderedDict
    sessions: OrderedDict = OrderedDict()
    for r in rows:
        if r.session_date not in sessions:
            sessions[r.session_date] = []
        sessions[r.session_date].append(r)

    _INTERP_ORDER = ["mtf_interpreter", "gravity_interpreter", "junior_analyst"]
    _INTERP_LABEL = {
        "mtf_interpreter":   "MTF ENERGY",
        "gravity_interpreter": "GRAVITY STRUCTURE",
        "junior_analyst":    "JUNIOR ANALYST (RECONCILIATION)",
    }
    _INTERP_COLOR = {
        "mtf_interpreter":   "#38bdf8",
        "gravity_interpreter": "#a78bfa",
        "junior_analyst":    "#34d399",
    }

    def _render_session(date_key: str, interp_rows: list) -> str:
        by_name = {r.interpreter_name: r for r in interp_rows}
        blocks = []
        for name in _INTERP_ORDER:
            r = by_name.get(name)
            label = _INTERP_LABEL.get(name, name)
            color = _INTERP_COLOR.get(name, "#94a3b8")
            if r is None:
                blocks.append(f"""
                <div style="margin-bottom:18px;">
                  <div style="color:{color};font-weight:bold;font-size:11px;letter-spacing:1px;margin-bottom:4px;">{label}</div>
                  <div style="color:#64748b;font-style:italic;">— not logged this session —</div>
                </div>""")
                continue
            ok_badge = (
                '<span style="background:#166534;color:#86efac;padding:1px 7px;border-radius:4px;font-size:10px;font-weight:bold;">OK</span>'
                if r.ran_successfully else
                '<span style="background:#7f1d1d;color:#fca5a5;padding:1px 7px;border-radius:4px;font-size:10px;font-weight:bold;">FAIL-OPEN</span>'
            )
            ts = r.created_at.strftime("%H:%M:%S UTC") if r.created_at else "?"
            text = r.output_text or "<em style='color:#64748b;'>None — fail-opened, no output</em>"
            safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") if r.output_text else text
            blocks.append(f"""
            <div style="margin-bottom:22px;">
              <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
                <span style="color:{color};font-weight:bold;font-size:11px;letter-spacing:1px;">{label}</span>
                {ok_badge}
                <span style="color:#475569;font-size:10px;">{ts}</span>
              </div>
              <pre style="background:#020617;border:1px solid #1e293b;border-radius:6px;padding:14px;
                          font-family:'JetBrains Mono',monospace;font-size:12px;line-height:1.6;
                          color:#cbd5e1;white-space:pre-wrap;word-break:break-word;margin:0;">{safe_text}</pre>
            </div>""")
        return "".join(blocks)

    session_blocks = []
    for date_key, interp_rows in sessions.items():
        session_id = interp_rows[0].session_id if interp_rows else "?"
        inner = _render_session(date_key, interp_rows)
        session_blocks.append(f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;
                    padding:24px;margin-bottom:28px;">
          <div style="display:flex;align-items:baseline;gap:14px;margin-bottom:18px;
                      border-bottom:1px solid #1e293b;padding-bottom:12px;">
            <span style="color:#f1f5f9;font-weight:bold;font-size:16px;">{date_key}</span>
            <span style="color:#475569;font-size:11px;">{session_id}</span>
          </div>
          {inner}
        </div>""")

    body = "\n".join(session_blocks) if session_blocks else (
        "<p style='color:#64748b;'>No interpreter_log rows found for BTC/USDT. "
        "Table exists but may be empty — check that the JA has run at least one session.</p>"
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Interpreter Log — Kabroda Admin</title>
  <style>
    body {{ background:#020617; color:#cbd5e1; font-family:'JetBrains Mono',monospace;
            margin:0; padding:32px; box-sizing:border-box; }}
    a {{ color:#38bdf8; text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
  </style>
</head>
<body>
  <div style="max-width:900px;margin:0 auto;">
    <div style="margin-bottom:28px;">
      <div style="color:#94a3b8;font-size:11px;letter-spacing:2px;margin-bottom:6px;">KABRODA ADMIN</div>
      <h1 style="color:#f1f5f9;font-size:22px;margin:0 0 6px;">INTERPRETER LOG</h1>
      <div style="color:#475569;font-size:12px;">
        BTC/USDT &nbsp;·&nbsp; last {len(sessions)} sessions &nbsp;·&nbsp;
        MTF → Gravity → Junior Analyst &nbsp;·&nbsp;
        <a href="/admin">← admin</a>
      </div>
    </div>
    {body}
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


# --- API EXECUTION ROUTES ---
@app.post("/api/dmr/run-raw")
async def dmr_run_raw(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    
    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    session_id = payload.get("session_id")
    
    if session_id:
        out = await battlebox_pipeline.get_session_review(symbol=symbol, session_id=session_id)
    else:
        out = await battlebox_pipeline.get_session_review(symbol=symbol)
    return JSONResponse(out)

# /api/dmr/live removed 2026-08-30 -- returned battlebox_pipeline.py's entire
# unfiltered get_live_battlebox() payload, but had zero frontend callers
# (grepped every template/JS, nothing fetches it) and zero test coverage.
# Andy's call: archive it. market_radar.py's own routes (/api/radar/snapshot,
# /api/radar/scan) are the real, live consumers of battlebox_pipeline.py now.

@app.post("/api/radar/scan")
async def run_radar_scan(request: Request):
    print("[RADAR] scan endpoint called")
    results = await market_radar.scan_sector()
    print(f"[RADAR] returning {len(results)} results")
    return {"ok": True, "results": results}

# /api/research/run removed 2026-08-30 -- see the /suite/research-lab
# removal note above.

# /api/simulator/run + market_simulator.py archived 2026-08-28 -- see the
# /admin/simulator removal note above.

# ==============================================================================
# EXECUTIVE DASHBOARD API ROUTES (Phase 6 — read-only DB queries)
# ==============================================================================

@app.get("/api/dashboard/overview")
async def api_dashboard_overview(request: Request, db: Session = Depends(get_db)):
    """2026-09-23 rebuild (V2 Crown retirement, CLAUDE.md "Strategic
    Direction"): total_sessions/fill_rate/win_rate/net_r used to read
    CampaignLog (V2's own session-lock shadow simulation, itself being
    retired). Repointed to TravelerPlan/ExecutorOrder -- the current live
    system -- rather than kept pointed at a table about to stop being
    written. `approved_rate` is renamed `fill_rate`: the Traveler has no
    lock-time gate/verdict at all (gate_traveler.py's own header -- every
    plan starts WAITING_CROSS unconditionally), so "approved" has no
    Traveler meaning; "% of sessions that actually reached a real fill" is
    the honest equivalent. `executor_realized_pnl_r`/`executor_closed_trades`
    below are untouched -- already ExecutorOrder-sourced, already correct,
    kept as the separate "every real order, any lineage" figure."""
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        from sqlalchemy import func
        from database import TravelerPlan as _TravelerPlan

        total = db.query(func.count(_TravelerPlan.id)).filter(
            _TravelerPlan.symbol == "BTC/USDT").scalar() or 0
        filled = db.query(func.count(_TravelerPlan.id)).filter(
            _TravelerPlan.symbol == "BTC/USDT", _TravelerPlan.status == "FILLED").scalar() or 0
        fill_rate = round(filled / total * 100, 1) if total > 0 else 0.0

        # Win rate / net R: TravelerPlan has no P&L field of its own -- the
        # post-fill lifecycle (exit_reason/realized_pnl_r) lives entirely on
        # the linked ExecutorOrder (see traveler_radar.py's own module
        # docstring for the same fact). Real sum of realized_pnl_r, not a
        # win/loss COUNT -- same "stops aren't a clean +-1R" reasoning
        # CLAUDE.md rule 5 already established for the retired CampaignLog
        # version of this KPI.
        resolved_r = [r for (r,) in db.query(_ExecutorOrder.realized_pnl_r).filter(
            _ExecutorOrder.traveler_plan_id.isnot(None),
            _ExecutorOrder.realized_pnl_r.isnot(None),
        ).all()]
        win_rate = round(sum(1 for r in resolved_r if r > 0) / len(resolved_r) * 100, 1) if resolved_r else 0.0
        net_r = round(sum(resolved_r), 4)

        # "No two disagreeing performance numbers visible anywhere" --
        # net_r above (Traveler-linked orders only, DRY_RUN+LIVE combined,
        # the evaluation harness's own running total) and this figure
        # (every real ExecutorOrder, any lineage) are shown separately,
        # never merged, same principle the pre-retirement CampaignLog/
        # executor split already established.
        executor_r_raw = db.query(func.sum(_ExecutorOrder.realized_pnl_r)).filter(
            _ExecutorOrder.realized_pnl_r.isnot(None),
        ).scalar()
        executor_realized_pnl_r = round(float(executor_r_raw or 0.0), 4)
        executor_closed_trades = db.query(func.count(_ExecutorOrder.id)).filter(
            _ExecutorOrder.realized_pnl_r.isnot(None),
        ).scalar() or 0
        since_7d = (datetime.now(timezone.utc) - timedelta(days=7)).replace(tzinfo=None)
        spend_raw = db.query(func.sum(AgentRunLog.estimated_cost_usd)).filter(
            AgentRunLog.created_at >= since_7d).scalar()
        spend_7d = round(spend_raw or 0.0, 4)
        tok = db.query(func.sum(AgentRunLog.input_tokens), func.sum(AgentRunLog.cache_read_tokens)).filter(
            AgentRunLog.created_at >= since_7d).first()
        total_tok = (tok[0] or 0) + (tok[1] or 0)
        cache_hit_rate = round((tok[1] or 0) / total_tok * 100, 1) if total_tok > 0 else 0.0
        return JSONResponse({"ok": True, "total_sessions": total, "fill_rate": fill_rate,
            "win_rate": win_rate, "net_r": net_r, "spend_7d": spend_7d,
            "cache_hit_rate": cache_hit_rate,
            "executor_realized_pnl_r": executor_realized_pnl_r, "executor_closed_trades": executor_closed_trades})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/dashboard/accuracy")
async def api_dashboard_accuracy(request: Request, db: Session = Depends(get_db)):
    """2026-09-23 (V2 Crown retirement): `grade_accuracy` (4H/1H
    CampaignLog.kinematic_grade vs. outcome) removed outright, not given a
    Traveler equivalent -- the 4H/1H independent candidate system it
    measured was already retired under V2 itself (2026-08-30, "clean up
    the radar back to just the fifteen minute"), and the Traveler has no
    "grade" concept at all. Fabricating a replacement for a system that no
    longer exists would be worse than removing the chart -- same call this
    project already made for the Signal Accuracy tab (site commit
    `28821ac`). `confluence_accuracy` (DecisionJournal-sourced) is
    untouched here -- it doesn't read CampaignLog -- but DecisionJournal
    itself is V2-only (V2_RETIREMENT_MAP.md) and will need the same
    treatment once that table is actually deleted in a later step; not
    done here to keep this change scoped to the CampaignLog dependency."""
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        from sqlalchemy import func
        def _build_accuracy(rows):
            acc = {}
            for key, correct, count in rows:
                k = str(key)
                if k not in acc:
                    acc[k] = {"correct": 0, "incorrect": 0}
                if correct:
                    acc[k]["correct"] += count
                else:
                    acc[k]["incorrect"] += count
            result = {}
            for k, c in acc.items():
                total = c["correct"] + c["incorrect"]
                result[k] = {"correct_pct": round(c["correct"]/total*100,1) if total else 0,
                             "incorrect_pct": round(c["incorrect"]/total*100,1) if total else 0,
                             "total": total}
            return result
        conf_rows = db.query(DecisionJournal.confluence_score,
            DecisionJournal.outcome_direction_correct, func.count(DecisionJournal.id)).filter(
            DecisionJournal.symbol == "BTC/USDT",
            DecisionJournal.outcome_direction_correct.isnot(None),
            DecisionJournal.confluence_score.isnot(None)
        ).group_by(DecisionJournal.confluence_score, DecisionJournal.outcome_direction_correct).all()
        return JSONResponse({"ok": True, "confluence_accuracy": _build_accuracy(conf_rows)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/dashboard/costs")
async def api_dashboard_costs(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)
    try:
        from collections import defaultdict
        since_7d = (datetime.now(timezone.utc) - timedelta(days=7)).replace(tzinfo=None)
        rows = db.query(AgentRunLog).filter(
            AgentRunLog.created_at >= since_7d, AgentRunLog.status == "SUCCESS").all()
        daily = defaultdict(lambda: defaultdict(float))
        all_agents = set()
        for row in rows:
            created_at = row.created_at or datetime.utcnow()
            day = created_at.strftime("%m/%d")
            daily[day][row.agent_name] += (row.estimated_cost_usd or 0.0)
            all_agents.add(row.agent_name)
        days_list = [(datetime.utcnow() - timedelta(days=i)).strftime("%m/%d") for i in range(6, -1, -1)]
        agents_sorted = sorted(all_agents)
        return JSONResponse({"ok": True, "days": days_list,
            "agents": [{"name": ag, "values": [round(daily[d][ag], 5) for d in days_list]} for ag in agents_sorted]})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/dashboard/mas-history")
async def api_dashboard_mas_history(request: Request, db: Session = Depends(get_db)):
    """2026-09-23 rebuild (V2 Crown retirement): repointed from CampaignLog
    (mas_approval_status/entry_price/stop_loss/t1/realized_pnl) to
    TravelerPlan/ExecutorOrder. `approval_counts` -> `status_counts`: the
    Traveler has no APPROVED/REJECTED verdict, only its own 5-value status
    enum (WAITING_CROSS/TERCILE_SKIPPED/WAITING_TOUCH/FILLED/DONE, see
    gate_traveler.py). `trades` now carries direction/exit_reason instead
    of bias/mas_approval_status -- each row's mgmt detail comes from its
    linked ExecutorOrder, preferring a LIVE-mode order over DRY_RUN (same
    rule traveler_radar.py/the admin traveler-plan-status route already
    use), resolved in one query rather than one extra round-trip per row."""
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        from sqlalchemy import func
        from database import TravelerPlan as _TravelerPlan

        status_rows = db.query(_TravelerPlan.status, func.count(_TravelerPlan.id)).filter(
            _TravelerPlan.symbol == "BTC/USDT").group_by(_TravelerPlan.status).all()
        status_counts = {row[0]: row[1] for row in status_rows}

        # Cumulative R across every resolved (closed) Traveler-linked
        # order, ordered by exit time -- real realized_pnl_r sum, not a
        # hardcoded +-1.0, same principle the retired CampaignLog version
        # of this KPI already established.
        closed_orders = db.query(_ExecutorOrder.exit_time, _ExecutorOrder.realized_pnl_r).filter(
            _ExecutorOrder.traveler_plan_id.isnot(None),
            _ExecutorOrder.realized_pnl_r.isnot(None),
        ).order_by(_ExecutorOrder.exit_time).all()
        cumulative = 0.0
        pnl_series = []
        for exit_time, r in closed_orders:
            cumulative += r
            pnl_series.append({
                "date": exit_time.strftime("%Y-%m-%d") if exit_time else None,
                "cumulative": round(cumulative, 4),
            })

        plans = db.query(_TravelerPlan).filter(
            _TravelerPlan.symbol == "BTC/USDT").order_by(_TravelerPlan.id.desc()).limit(50).all()
        plan_ids = [p.id for p in plans]
        # One query for every linked order, LIVE-preferred per plan --
        # ordered id-desc so the first order seen per plan_id is already
        # its most recent, then upgraded to a LIVE row if one exists,
        # mirroring the admin traveler-plan-status route's own selection
        # without an extra query per row.
        order_by_plan = {}
        for o in db.query(_ExecutorOrder).filter(
            _ExecutorOrder.traveler_plan_id.in_(plan_ids)
        ).order_by(_ExecutorOrder.id.desc()).all():
            existing = order_by_plan.get(o.traveler_plan_id)
            if existing is None or (o.mode == "LIVE" and existing.mode != "LIVE"):
                order_by_plan[o.traveler_plan_id] = o

        trades_data = []
        for p in plans:
            order = order_by_plan.get(p.id)
            entry_price = None
            if p.direction == "LONG":
                entry_price = p.breakout_trigger
            elif p.direction == "SHORT":
                entry_price = p.breakdown_trigger
            r_pnl = f"{order.realized_pnl_r:+.4f}R" if (order and order.realized_pnl_r is not None) else None
            trades_data.append({
                "date_key": p.date_key, "direction": p.direction, "status": p.status,
                "entry_price": entry_price, "stop_price": p.stop_price, "t1_price": p.t1_price,
                "exit_reason": order.exit_reason if order else None,
                "realized_pnl": r_pnl,
            })
        return JSONResponse({"ok": True, "status_counts": status_counts,
                             "pnl_series": pnl_series, "trades": trades_data})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# /api/dashboard/jewel removed 2026-08-30 -- analyzed the old JEWEL gate vs.
# outcome, both retired (jewel_specialist.py archived, nothing writes new
# JewelSnapshotLog rows). Would only ever show historical (frozen) data.
#
# /api/dashboard/newsletters removed 2026-08-30 -- NewsletterLog has been
# orphaned since publisher_crew.py was archived (2026-08-30, earlier commit);
# nothing writes to it. A newsletter viewer is also exactly what "no
# publication" rules out directly, not just incidentally dead.


@app.get("/api/dashboard/audits")
async def api_dashboard_audits(request: Request, db: Session = Depends(get_db)):
    """Returns the last 5 SystemAuditLog rows for the Dashboard audit viewer."""
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        rows = db.query(SystemAuditLog).order_by(SystemAuditLog.id.desc()).limit(5).all()
        data = [
            {
                "id":         r.id,
                "date_key":   r.date_key,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "audit_md":   r.audit_md or "",
            }
            for r in rows
        ]
        return JSONResponse({"ok": True, "audits": data})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/health/audit-heartbeat")
async def api_audit_heartbeat(request: Request, db: Session = Depends(get_db)):
    """
    Admin-only. Returns WRITING/DARK for session_audit_log and monitor_event_log.
    Polled by the admin page every 60 seconds. Silent failures surface here.
    """
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in") or not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    import datetime as _dt
    yesterday = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=2)).strftime("%Y-%m-%d")
    result: dict = {"ok": True, "session_audit_log": {}, "monitor_event_log": {}}

    try:
        from database import SessionAuditLog as _SAL
        _latest = db.query(_SAL).filter(_SAL.symbol == "BTC/USDT").order_by(_SAL.id.desc()).first()
        _cnt = db.query(_SAL).filter(_SAL.symbol == "BTC/USDT", _SAL.date_key >= yesterday).count()
        result["session_audit_log"] = {
            "status": "WRITING" if _latest else "DARK",
            "recent_count": _cnt,
            "last_date_key": _latest.date_key if _latest else None,
            "last_status": _latest.approval_status if _latest else None,
        }
    except Exception as _e:
        result["session_audit_log"] = {"status": "TABLE_MISSING", "error": str(_e)}

    try:
        from database import MonitorEventLog as _MEL
        _latest_m = db.query(_MEL).filter(_MEL.symbol == "BTC/USDT").order_by(_MEL.id.desc()).first()
        _cnt_m = db.query(_MEL).filter(_MEL.symbol == "BTC/USDT", _MEL.session_date >= yesterday).count()
        result["monitor_event_log"] = {
            "status": "WRITING" if _latest_m else "DARK",
            "recent_count": _cnt_m,
            "last_session_date": _latest_m.session_date if _latest_m else None,
            "last_poll_seq": _latest_m.poll_sequence if _latest_m else None,
        }
    except Exception as _e:
        result["monitor_event_log"] = {"status": "TABLE_MISSING", "error": str(_e)}

    return JSONResponse(result)


# ---------------------------------------------------------
# SYSTEM DIAGNOSTIC API (M2)
# ---------------------------------------------------------

@app.get("/api/v1/system/state")
async def get_system_state(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Forbidden"}, status_code=403)
    
    try:
        # 1. active_sessions: query active SessionLock
        locks = db.query(SessionLock).all()
        active_sessions = [
            {
                "symbol": lock.symbol,
                "session_id": lock.session_id,
                "date_key": lock.date_key,
                "lock_time": lock.lock_time
            }
            for lock in locks
        ]

        # 2. active_runners: active runners list
        # "analysis_loop" removed 2026-09-23 (V2 Crown retirement, Audit-AI
        # surface retirement -- its scheduler is gone). "ledger_closing_
        # engine" stays here until that background task itself is removed
        # later in the same retirement pass (a separate sub-step).
        active_runners = ["gravity_engine", "ledger_closing_engine", "session_monitor"]

        # 3. macro_engine: real freshness check, not a frozen LLM narrative.
        # This used to read MacroNarrativeLog.wave_status with active always
        # hardcoded True -- MacroNarrativeLog has had zero writers since the
        # old LLM Senior Analyst pipeline was retired, so this was showing a
        # permanently frozen (potentially very old) value labeled ACTIVE
        # regardless of whether anything had actually run. The REAL macro
        # engine (kabroda_macro_engine.py, the ZigZag Elliott Wave scanner
        # subprocess) writes to gravity_memory with source=
        # "MACRO_ENGINE_CLASS_0", deleting and reinserting all of a symbol's
        # anchors with one shared timestamp on every run -- so MAX(timestamp)
        # for that source+symbol is exactly "when did the macro engine last
        # successfully complete a run," a real, honest signal.
        _latest_anchor = (
            db.query(GravityMemory)
            .filter(GravityMemory.symbol == "BTCUSDT", GravityMemory.source == "MACRO_ENGINE_CLASS_0")
            .order_by(GravityMemory.timestamp.desc())
            .first()
        )
        _anchor_ts = _latest_anchor.timestamp if _latest_anchor else None
        if _anchor_ts is not None and _anchor_ts.tzinfo is not None:
            _anchor_ts = _anchor_ts.replace(tzinfo=None)
        # Runs every 24h (gravity_engine.py) -- 30h window gives real buffer
        # before flagging stale without being so loose it hides a real outage.
        _macro_fresh = bool(_anchor_ts and (datetime.utcnow() - _anchor_ts) < timedelta(hours=30))
        macro_engine_data = {
            "symbol": "BTC/USDT",
            "latest_anchor": f"{_latest_anchor.level_type} @ ${_latest_anchor.price:,.2f}" if _latest_anchor else None,
            "last_run_at": _anchor_ts.isoformat() if _anchor_ts else None,
            "active": _macro_fresh,
        }

        # 4. recent_errors: system audit logs with ran_successfully == False.
        # SystemAuditLog has had zero writers since the old Performance
        # Auditor was archived, so this is currently always empty in
        # production -- kept anyway (not part of the misleading-macro_engine
        # fix's scope, and genuinely tested -- see test_f1_state_excessive_
        # errors's 50-row truncation check) rather than removed as a drive-by.
        errs = db.query(SystemAuditLog).filter(
            SystemAuditLog.ran_successfully == False
        ).order_by(SystemAuditLog.id.desc()).limit(50).all()

        recent_errors_list = [
            {
                "id": e.id,
                "symbol": e.symbol,
                "date_key": e.date_key,
                "audit_md": e.audit_md,
                "ran_successfully": e.ran_successfully,
                "created_at": e.created_at.isoformat() if e.created_at else None
            }
            for e in errs
        ]

        return JSONResponse({
            "ok": True,
            "active_sessions": active_sessions,
            "active_runners": active_runners,
            "scheduler_health": scheduler_health_registry,
            "macro_engine": macro_engine_data,
            "recent_errors": recent_errors_list
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# /api/v1/system/session-energy (the "Live System" dashboard tab) removed
# 2026-08-30, alongside battlebox_pipeline.py being stripped to only what the
# calibrated gate + forward-audit trail actually need. Andy's call: the tab
# itself was junk, not worth preserving separately. macro_bias/micro_bias
# (battlebox_pipeline.py's _calculate_weekly_force()/_calculate_168h_micro_
# bias()) no longer exist at all; bias_model (daily_lean/permission_state,
# from sse_engine.py) is untouched and still flows through the packet, it
# just has no reader left now that this was its only consumer.
# suite_dashboard.html's Live System tab UI removed alongside it.


# GET /api/v1/system/audit-suggestions and GET /api/v1/system/trades
# removed 2026-09-23 (V2 Crown retirement, Audit-AI surface retirement,
# see /admin/export-audit-ledger's own removal comment above for the full
# batch) -- both were 100% CampaignLog/DailyAuditLog/AuditSuggestionLog-
# sourced with no Traveler equivalent, per Andy's explicit "retire
# entirely" ruling.

# GET /api/v1/system/parameters and GET /api/v1/system/errors removed
# 2026-09-23 -- Andy's call during the strategic site audit. Both were
# already dead in practice, unrelated to V2/Traveler: parameters returned
# hardcoded literals never wired to any real config source; errors read
# SystemAuditLog, which has had zero live writers since the Performance
# Auditor was archived (always reported "all systems operational"
# regardless of real state). Their frontend tabs (Parameters, Errors) and
# every dedicated test in tests/test_e2e.py were removed in the same pass
# -- see suite_dashboard.html's own removal comment and V2_RETIREMENT_MAP.md
# for the full dashboard tab-by-tab audit this followed.


# POST /api/v1/system/analysis (no suffix) removed 2026-09-23 (V2 Crown
# retirement, Audit-AI surface retirement) -- CampaignLog-sourced (30-day
# win rate/PnL) and, separately, confirmed to have zero frontend caller
# anywhere in this repo -- the live "Recent Reports" list below
# (/api/v1/system/analysis/recent) and single-report view read from
# SystemAnalysisReport, but this was the ONLY route that ever wrote to
# that table, so it was already unreachable from any real UI before this
# removal. AnalysisRequest (its now-unused request-body Pydantic model)
# removed with it.

@app.get("/api/v1/system/analysis/recent")
async def get_recent_analysis_reports(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Forbidden"}, status_code=403)

    try:
        reports = db.query(SystemAnalysisReport).order_by(
            SystemAnalysisReport.id.desc()
        ).limit(5).all()

        return JSONResponse({
            "ok": True,
            "reports": [
                {
                    "analysis_id": r.analysis_id,
                    "query": r.query,
                    "status": r.status,
                    "error_message": r.error_message,
                    "created_at": r.created_at.isoformat() if r.created_at else None
                }
                for r in reports
            ]
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/v1/system/analysis/{analysis_id}")
async def get_system_analysis_by_id(analysis_id: str, request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Forbidden"}, status_code=403)
        
    try:
        report = db.query(SystemAnalysisReport).filter(SystemAnalysisReport.analysis_id == analysis_id).first()
        if not report:
            return JSONResponse({"ok": False, "error": "Analysis not found"}, status_code=404)
            
        parsed_report = {}
        if report.report_json:
            parsed_report = json.loads(report.report_json)
            if "findings" not in parsed_report:
                parsed_report["findings"] = parsed_report.get("summary", "System stable.")
                
        return JSONResponse({
            "ok": True,
            "analysis_id": report.analysis_id,
            "query": report.query,
            "status": report.status,
            "error_message": report.error_message,
            "report": parsed_report,
            "created_at": report.created_at.isoformat() if report.created_at else None
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# POST /api/v1/system/analysis/trigger removed 2026-09-23 (V2 Crown
# retirement, Audit-AI surface retirement) -- this was the live dashboard's
# "Run Analysis" button, calling _run_analysis_loop_body() (also removed,
# see its own former definition site) which wrote CampaignLog-derived
# stats into AuditSuggestionLog (also removed). Its 12h background
# scheduler (run_analysis_loop_scheduler(), analysis_loop_task) is removed
# from lifespan() in the same commit -- see that function's own former
# definition site and lifespan()'s removal comment.


# ==============================================================================
# ARCHIVED 2026-08-17 -- Kabroda Audit REBUILD_PLAN.md
# Removed: /api/v1/system/signal-accuracy(/trigger), /api/v1/system/alerts
# (+/{id}/resolve), /api/v1/system/flagging/trigger, /api/v1/system/signal-
# weights, /api/v1/system/accuracy-report(/trigger). All eight routes
# fronted signal_accuracy_tracker.py / signal_flagging_engine.py /
# signal_weight_manager.py / accuracy_report_generator.py -- confirmed
# record-only, never read by any live decision path (AUDIT_FINDINGS.md
# #43-48, #21-22). Modules moved to _archive/. Any admin-dashboard UI
# element that called these will fail client-side (404), not server-side.
# ==============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_trace = traceback.format_exc()
    print(f"CRITICAL CRASH:\n{error_trace}") 
    return HTMLResponse(
        content=f"""
        <div style="background-color: #0f172a; color: #ef4444; padding: 40px; font-family: 'JetBrains Mono', monospace; min-height: 100vh; box-sizing: border-box;">
            <h1 style="border-bottom: 2px solid #ef4444; padding-bottom: 10px; margin-top:0;">🚨 FATAL SYSTEM CRASH 🚨</h1>
            <p style="color: #cbd5e1; font-size: 14px;">The execution sequence failed. Here is the exact internal autopsy of the code:</p>
            <pre style="background: #020617; padding: 20px; border: 1px solid #334155; border-radius: 8px; overflow-x: auto; font-size: 12px; line-height: 1.5;">{error_trace}</pre>
        </div>
        """,
        status_code=500
    )