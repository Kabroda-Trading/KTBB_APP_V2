# main.py
# ---------------------------------------------------------
# KABRODA UNIFIED SERVER: PRIVATE TEAM TERMINAL
# ---------------------------------------------------------
import os
import json 
import traceback
import re
from typing import Any, Dict, Optional
import asyncio
from contextlib import asynccontextmanager 

from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel

# --- CORE IMPORTS ---
import auth
import battlebox_pipeline
import market_radar
import research_lab
import market_simulator
import gravity_engine
import gravity_math
import kabroda_mas_flow
import ledger_closing_engine
import mtf_confluence_scanner
import session_monitor
import agent_core
import session_manager
import lti_engine
import lti_interpreter

from datetime import datetime, timezone, timedelta
from jewel_specialist import run_jewel_snapshot
from elliott_wave_specialist import run_elliott_wave_analysis
from performance_auditor import run_performance_audit

from database import init_db, get_db, UserModel, CampaignLog, SessionLock, AgentRunLog, SessionLocal, MacroNarrativeLog, JewelSnapshotLog, DecisionJournal, NewsletterLog, MtfReading, SystemAuditLog, InterpreterLog, LtiCheckpoint, LtiProtocol

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==============================================================================
# PHASE 4 — ASYNCIO SCHEDULERS
# No extra dependencies. Each loop calculates sleep duration to next fire time,
# catches all exceptions internally so a crashing agent never kills the server.
# ==============================================================================

# JEWEL session transitions sorted by UTC hour (ET label → UTC time)
_JEWEL_SCHEDULE = [
    ( 1, 0, "ASIA_OPEN"),    # 8:00 PM ET  → 01:00 UTC
    ( 5, 0, "ASIA_MIDDAY"),  # 12:00 AM ET → 05:00 UTC
    ( 9, 0, "LONDON_OPEN"),  # 4:00 AM ET  → 09:00 UTC
    (14, 0, "NY_OPEN"),      # 9:00 AM ET  → 14:00 UTC
    (18, 0, "NY_MIDDAY"),    # 1:00 PM ET  → 18:00 UTC
    (21, 0, "NY_CLOSE"),     # 4:00 PM ET  → 21:00 UTC
]


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


def _next_jewel_slot():
    """Returns (seconds_to_wait, session_label) for the next JEWEL snapshot."""
    now = datetime.now(timezone.utc)
    today = now.date()
    candidates = []
    for hour, minute, label in _JEWEL_SCHEDULE:
        t = datetime(today.year, today.month, today.day, hour, minute, 0, tzinfo=timezone.utc)
        if t <= now:
            t += timedelta(days=1)
        candidates.append((t, label))
    candidates.sort(key=lambda x: x[0])
    next_time, next_label = candidates[0]
    return (next_time - now).total_seconds(), next_label


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
        existing_brief = db.query(MacroNarrativeLog).filter(
            MacroNarrativeLog.symbol == "BTC/USDT",
            MacroNarrativeLog.authored_by == "senior_analyst",
            MacroNarrativeLog.date_key == date_key,
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
            existing = db.query(MacroNarrativeLog).filter(
                MacroNarrativeLog.symbol == "BTC/USDT",
                MacroNarrativeLog.authored_by == "senior_analyst",
                MacroNarrativeLog.date_key == date_key,
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
            print(f"[SCHEDULER] Senior Analyst: next fire in {seconds / 3600:.1f}h (lock_end / 9:00 AM ET)")
            await asyncio.sleep(seconds)
            _fire_now = datetime.now(timezone.utc)
            _fire_session = session_manager.resolve_current_session(_fire_now, mode="AUTO")
            date_key = _fire_session["date_key"]
            print(f"[SCHEDULER] Senior Analyst scheduled fire — {date_key} lock_end (9:00 AM ET)")
            await _fire_senior_analyst(date_key)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[SCHEDULER] Senior Analyst scheduler error: {e}")
            await asyncio.sleep(300)


async def run_jewel_scheduler() -> None:
    """6x daily JEWEL snapshots at each session transition."""
    print("[SCHEDULER] JEWEL Specialist scheduler starting...")
    while True:
        try:
            seconds, session_label = _next_jewel_slot()
            print(f"[SCHEDULER] JEWEL: next snapshot is {session_label} in {seconds / 3600:.1f}h")
            await asyncio.sleep(seconds)

            current_price = await _fetch_btc_price()
            date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            print(f"[SCHEDULER] JEWEL snapshot: {session_label} | ${current_price:,.2f}")

            try:
                result = await run_jewel_snapshot(
                    symbol="BTC/USDT",
                    session_label=session_label,
                    current_price=current_price,
                    date_key=date_key,
                )
                print(f"[SCHEDULER] JEWEL {session_label}: {result.get('status')}")
            except Exception as e:
                print(f"[SCHEDULER] JEWEL {session_label} failed: {e}")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[SCHEDULER] JEWEL outer error: {e}")
            await asyncio.sleep(60)


async def run_weekly_scheduler() -> None:
    """
    Sunday 23:00 UTC: Elliott Wave Specialist runs first, then Performance Auditor.
    Sleeps 1h after firing to avoid re-triggering within the same Sunday window.
    """
    print("[SCHEDULER] Weekly scheduler starting (Elliott Wave + Performance Auditor)...")
    while True:
        try:
            seconds = _seconds_until_sunday_2300()
            print(f"[SCHEDULER] Weekly: next run in {seconds / 3600:.1f}h (Sunday 23:00 UTC)")
            await asyncio.sleep(seconds)

            date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            current_price = await _fetch_btc_price()

            since_week = datetime.utcnow() - timedelta(days=7)

            # Elliott Wave Specialist — dedup: skip if already ran this week
            _ew_db = SessionLocal()
            try:
                _ew_ran = _ew_db.query(MacroNarrativeLog).filter(
                    MacroNarrativeLog.symbol == "BTC/USDT",
                    MacroNarrativeLog.authored_by == "elliott_wave_specialist",
                    MacroNarrativeLog.created_at >= since_week,
                ).first()
            finally:
                _ew_db.close()

            if _ew_ran:
                print(f"[SCHEDULER] Elliott Wave Specialist already ran this week ({_ew_ran.date_key}) — skipping")
            else:
                print(f"[SCHEDULER] Elliott Wave Specialist firing for {date_key} (Sunday 23:00 UTC)...")
                try:
                    result = await asyncio.to_thread(
                        run_elliott_wave_analysis,
                        symbol="BTC/USDT",
                        current_price=current_price,
                        date_key=date_key,
                    )
                    print(f"[SCHEDULER] Elliott Wave: {result.get('status')}")
                except Exception as e:
                    print(f"[SCHEDULER] Elliott Wave failed: {e}")

            # Performance Auditor — dedup: skip if a SystemAuditLog row already
            # exists for this date_key. Checks the vault directly — immune to
            # the race condition that previously caused double-fires when two
            # instances both found performance_note IS NULL before either committed.
            _pa_db = SessionLocal()
            try:
                _pa_ran = _pa_db.query(SystemAuditLog).filter(
                    SystemAuditLog.symbol == "BTC/USDT",
                    SystemAuditLog.date_key == date_key,
                ).first()
            finally:
                _pa_db.close()

            if _pa_ran:
                print(f"[SCHEDULER] Performance Auditor: audit already in SystemAuditLog for {date_key} — skipping")
            else:
                print(f"[SCHEDULER] Performance Auditor firing for {date_key} (Sunday 23:00 UTC)...")
                try:
                    result = await asyncio.to_thread(
                        run_performance_audit,
                        symbol="BTC/USDT",
                        date_key=date_key,
                    )
                    print(f"[SCHEDULER] Performance Auditor: {result.get('status')}")
                except Exception as e:
                    print(f"[SCHEDULER] Performance Auditor failed: {e}")

            # Sleep 1h to clear the Sunday 23:00 UTC window before recalculating next fire
            await asyncio.sleep(3600)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[SCHEDULER] Weekly outer error: {e}")
            await asyncio.sleep(300)


async def run_monthly_lti_scheduler() -> None:
    """
    First of every calendar month, 00:00 UTC: run the KULTI monthly confluence
    audit (lti_engine.run_lti_audit) + AI interpreter (lti_interpreter.run_
    lti_interpretation), write one LtiCheckpoint row + one InterpreterLog row.
    Advisory-only -- never auto-executes anything.
    """
    print("[SCHEDULER] Monthly LTI scheduler starting (KULTI confluence audit)...")
    while True:
        try:
            seconds = _seconds_until_month_start()
            print(f"[SCHEDULER] Monthly LTI: next run in {seconds / 3600:.1f}h (1st of month, 00:00 UTC)")
            await asyncio.sleep(seconds)

            now = datetime.now(timezone.utc)
            date_key = now.strftime("%Y-%m")
            first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

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
                    interpretation = await asyncio.to_thread(lti_interpreter.run_lti_interpretation, audit)

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
                        _db2.add(InterpreterLog(symbol=audit["symbol"], session_date=date_key, session_id="monthly_lti_audit",
                            interpreter_name="lti_interpreter", output_text=interpretation,
                            ran_successfully=interpretation is not None,
                        ))
                        _db2.commit()
                    finally:
                        _db2.close()
                    print(f"[SCHEDULER] Monthly LTI audit: conviction={audit['conviction_label']}, interpreter={'OK' if interpretation else 'FAILED'}")
                except Exception as e:
                    print(f"[SCHEDULER] Monthly LTI audit failed: {e}")

            # Sleep 1h to clear the month-start window before recalculating next fire
            await asyncio.sleep(3600)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[SCHEDULER] Monthly LTI outer error: {e}")
            await asyncio.sleep(300)


# ==============================================================================
# OUTCOME TRACKER — runs every 4 hours
# Fills DecisionJournal outcome fields for rows older than 4h.
# Fills CampaignLog.target_hit for all closed rows.
# ==============================================================================

def _do_outcome_tick(current_price: float) -> None:
    """Core outcome-tracker logic. Extracted for testability."""
    now = datetime.now(timezone.utc)
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
            current_price = await _fetch_btc_price()
            if current_price > 0:
                _do_outcome_tick(current_price)
            else:
                print("[OUTCOME TRACKER] Could not fetch BTC price — skipping tick")
            await asyncio.sleep(14400)  # 4 hours
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[OUTCOME TRACKER] Outer error: {e}")
            await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(">>> BOOTING KABRODA SYSTEM: Initializing Database Schema...")
    init_db()
    app.state.gravity_task          = asyncio.create_task(gravity_engine.run_gravity_ingestion_loop())
    app.state.ledger_task           = asyncio.create_task(ledger_closing_engine.run_ledger_audit_loop())
    app.state.senior_analyst_task   = asyncio.create_task(run_senior_analyst_scheduler())
    app.state.jewel_task            = asyncio.create_task(run_jewel_scheduler())
    app.state.weekly_task           = asyncio.create_task(run_weekly_scheduler())
    app.state.lti_task              = asyncio.create_task(run_monthly_lti_scheduler())
    app.state.outcome_tracker_task  = asyncio.create_task(run_outcome_tracker())
    app.state.monitor_task          = asyncio.create_task(session_monitor.run_session_monitor_loop())
    yield
    print(">>> SHUTTING DOWN KABRODA SYSTEM...")
    app.state.gravity_task.cancel()
    app.state.ledger_task.cancel()
    app.state.senior_analyst_task.cancel()
    app.state.jewel_task.cancel()
    app.state.weekly_task.cancel()
    app.state.lti_task.cancel()
    app.state.outcome_tracker_task.cancel()
    app.state.monitor_task.cancel()

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
        return templates.TemplateResponse(name, context)
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

@app.get("/suite/research-lab")
async def suite_research_lab_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "research_lab.html", ctx)

@app.get("/suite/radar")
async def radar_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "market_radar.html", ctx)

@app.get("/suite/gravity-map")
async def gravity_map_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "gravity_map.html", ctx)

@app.get("/suite/dashboard")
async def suite_dashboard_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "suite_dashboard.html", ctx)

@app.get("/suite/lti")
async def lti_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)

    latest = db.query(LtiCheckpoint).filter(LtiCheckpoint.symbol == "BTC/USDT").order_by(LtiCheckpoint.id.desc()).first()
    history = db.query(LtiCheckpoint).filter(LtiCheckpoint.symbol == "BTC/USDT").order_by(LtiCheckpoint.id.desc()).limit(12).all()
    protocol = db.query(LtiProtocol).first()

    latest_interpretation = None
    if latest:
        interp_row = db.query(InterpreterLog).filter(
            InterpreterLog.interpreter_name == "lti_interpreter",
            InterpreterLog.session_date == latest.date_key,
        ).order_by(InterpreterLog.id.desc()).first()
        latest_interpretation = interp_row.output_text if interp_row else None

    ctx.update({
        "latest_checkpoint": latest,
        "checkpoint_history": history,
        "protocol": protocol,
        "latest_interpretation": latest_interpretation,
    })
    return _template_or_fallback(request, templates, "lti.html", ctx)


@app.post("/api/lti/protocol")
async def save_lti_protocol(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]:
        raise HTTPException(status_code=401)

    data = await request.json()
    protocol = db.query(LtiProtocol).first()
    if not protocol:
        protocol = LtiProtocol()
        db.add(protocol)

    protocol.universe = data.get("universe") or protocol.universe or "BTC"
    protocol.conviction_threshold = int(data.get("conviction_threshold") or protocol.conviction_threshold or 4)
    protocol.drawdown_protocol = data.get("drawdown_protocol", protocol.drawdown_protocol)
    protocol.cash_floor_pct = float(data.get("cash_floor_pct") or protocol.cash_floor_pct or 5.0)
    protocol.residual_trim_pct = float(data.get("residual_trim_pct") or protocol.residual_trim_pct or 15.0)
    db.commit()
    return {"status": "ok"}


@app.get("/suite/macro-war-room")
async def macro_war_room_page(request: Request, symbol: str = "BTC/USDT", db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    
    db_sym = symbol.replace("USDT", "/USDT") if "/" not in symbol else symbol
    latest_log = db.query(CampaignLog).filter(CampaignLog.symbol == db_sym, CampaignLog.is_canonical == True).order_by(CampaignLog.id.desc()).first()
    
    if latest_log and not latest_log.mas_executive_brief and latest_log.mas_approval_status == 'PENDING':
        # Dedup: if MacroNarrativeLog already has a senior_analyst row for this date,
        # the brief is written or in-flight — do not fire a second run_mas_analysis().
        existing_narrative = db.query(MacroNarrativeLog).filter(
            MacroNarrativeLog.symbol == db_sym,
            MacroNarrativeLog.authored_by == "senior_analyst",
            MacroNarrativeLog.date_key == latest_log.date_key,
        ).first()

        if not existing_narrative:
            lock_record = db.query(SessionLock).filter(
                SessionLock.symbol == db_sym,
                SessionLock.session_id == latest_log.session_id,
                SessionLock.date_key == latest_log.date_key
            ).first()

            if lock_record:
                pkt = json.loads(lock_record.packet_data)
                asyncio.create_task(
                    asyncio.to_thread(
                        kabroda_mas_flow.run_mas_analysis,
                        symbol=db_sym,
                        session_id=latest_log.session_id,
                        date_key=latest_log.date_key,
                        battlebox_payload=pkt
                    )
                )
    
    ctx["mas_log"] = latest_log
    return _template_or_fallback(request, templates, "macro_war_room.html", ctx)

# --- NARRATIVE / JEWEL DATA ENDPOINT ---
@app.get("/api/narrative/latest")
async def api_narrative_latest(symbol: str = "BTC/USDT"):
    """
    Single endpoint serving War Room, Market Radar Panel 00, and Gravity Map sidebar.
    Returns latest Senior Analyst narrative, Elliott Wave state, and JEWEL snapshot.
    No authentication required — data is not sensitive.
    """
    db_sym = symbol.replace("USDT", "/USDT") if "/" not in symbol else symbol

    db = SessionLocal()
    try:
        analyst_row = (
            db.query(MacroNarrativeLog)
            .filter(
                MacroNarrativeLog.symbol == db_sym,
                MacroNarrativeLog.authored_by == "senior_analyst",
            )
            .order_by(MacroNarrativeLog.id.desc())
            .first()
        )

        wave_row = (
            db.query(MacroNarrativeLog)
            .filter(
                MacroNarrativeLog.symbol == db_sym,
                MacroNarrativeLog.authored_by == "elliott_wave_specialist",
            )
            .order_by(MacroNarrativeLog.id.desc())
            .first()
        )

        jewel_row = (
            db.query(JewelSnapshotLog)
            .filter(JewelSnapshotLog.symbol == db_sym)
            .order_by(JewelSnapshotLog.id.desc())
            .first()
        )

        return JSONResponse({
            "ok": True,
            "symbol": db_sym,
            "date_key": analyst_row.date_key if analyst_row else None,
            "narrative": {
                "narrative_text":   analyst_row.narrative_text   if analyst_row else None,
                "tactical_text":    analyst_row.tactical_text    if analyst_row else None,
                "performance_note": analyst_row.performance_note if analyst_row else None,
                "date_key":         analyst_row.date_key         if analyst_row else None,
            },
            "wave": {
                "wave_label":            wave_row.wave_label            if wave_row else None,
                "wave_status":           wave_row.wave_status           if wave_row else None,
                "completion_pct":        wave_row.completion_pct        if wave_row else None,
                "wave_origin_price":     wave_row.wave_origin_price     if wave_row else None,
                "wave_target_price":     wave_row.wave_target_price     if wave_row else None,
                "invalidation_price":    wave_row.invalidation_price    if wave_row else None,
                "confirmation_condition":wave_row.confirmation_condition if wave_row else None,
                "wave_reasoning":        wave_row.wave_reasoning        if wave_row else None,
                "date_key":              wave_row.date_key              if wave_row else None,
            } if wave_row else None,
            "jewel": {
                "jewel_gate_open":         jewel_row.jewel_gate_open         if jewel_row else None,
                "jewel_conviction":        jewel_row.jewel_conviction        if jewel_row else None,
                "jewel_exit_warning":      jewel_row.jewel_exit_warning      if jewel_row else None,
                "jewel_divergence_warning":jewel_row.jewel_divergence_warning if jewel_row else None,
                "jewel_signal_summary":    jewel_row.jewel_signal_summary    if jewel_row else None,
                "dominant_direction":      jewel_row.dominant_direction      if jewel_row else None,
                "session_label":           jewel_row.session_label           if jewel_row else None,
                "timestamp":               jewel_row.timestamp.isoformat()   if jewel_row and jewel_row.timestamp else None,
            } if jewel_row else None,
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


@app.get("/api/radar/snapshot")
async def api_radar_snapshot(db: Session = Depends(get_db)):
    """
    Phase 1 of the two-phase radar render. Pure DB reads — zero exchange I/O.
    Returns: locked session levels + most recent MtfReading + JEWEL gate + MAS status.
    Target response time: < 100ms. Called before POST /api/radar/scan so the UI
    can render structural truth instantly while live MTF data loads in the background.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
    if lock:
        try:
            pkt = json.loads(lock.packet_data)
            levels = pkt.get("levels", {})
            price = float(levels.get("anchor_price") or 0)
        except Exception:
            pass

    # 2. Latest MtfReading — written on every scan_sector() call and every gravity loop tick
    mtf_row = db.query(MtfReading).filter(
        MtfReading.symbol == symbol_norm
    ).order_by(MtfReading.id.desc()).first()

    mtf_cached: dict = {}
    if mtf_row:
        mtf_cached = {
            "confluence_direction": mtf_row.confluence_direction,
            "confluence_score":     mtf_row.confluence_score,
            "energy_status":        mtf_row.energy_status,
            "bo_price":             mtf_row.bo_price,
            "bd_price":             mtf_row.bd_price,
            "asset_price":          mtf_row.asset_price,
            "scanned_at":           mtf_row.timestamp.isoformat() if mtf_row.timestamp else None,
        }
        # Prefer MtfReading asset_price — more recent than the locked anchor
        if mtf_row.asset_price and mtf_row.asset_price > 0:
            price = mtf_row.asset_price

    # 3. Latest JEWEL snapshot — for the gate dot
    jewel_row = db.query(JewelSnapshotLog).filter(
        JewelSnapshotLog.symbol == symbol_norm
    ).order_by(JewelSnapshotLog.id.desc()).first()

    jewel_gate_open = jewel_row.jewel_gate_open if jewel_row else None

    # 4. Today's MAS verdict — for the status badge and cockpit pre-population
    campaign = db.query(CampaignLog).filter(
        CampaignLog.symbol == symbol_norm,
        CampaignLog.date_key == today,
        CampaignLog.is_canonical == True,
    ).order_by(CampaignLog.id.desc()).first()

    mas_status = campaign.mas_approval_status if campaign else None
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

    # 5. TF system verdicts (4H + 1H + 15M) and which-TF-today decision
    tf_verdicts = market_radar._get_tf_system_verdicts(symbol_norm)
    tf_today    = market_radar._which_tf_today(tf_verdicts)

    # 6. Daily regime + weekly 200 SMA position from most recent audit row
    daily_regime = "—"
    weekly_200sma_position = "—"
    from database import SessionAuditLog as _SAL
    audit_row = db.query(_SAL).filter(
        _SAL.symbol == symbol_norm,
    ).order_by(_SAL.id.desc()).first()
    if audit_row:
        daily_regime = market_radar._compute_daily_regime({
            "daily_21ema_direction": audit_row.daily_21ema_direction,
            "daily_200sma_position": getattr(audit_row, "daily_200sma_position", None),
        })
        weekly_200sma_position = getattr(audit_row, "weekly_200sma_position", None) or "—"

    return JSONResponse({
        "ok":                    True,
        "locked":                lock is not None,
        "symbol":                symbol_raw,
        "price":                 price,
        "levels":                levels,
        "mtf_cached":            mtf_cached,
        "jewel_gate_open":       jewel_gate_open,
        "mas_status":            mas_status,
        "plan":                  plan,
        "tf_verdicts":           tf_verdicts,
        "tf_today":              tf_today,
        "daily_regime":          daily_regime,
        "weekly_200sma_position": weekly_200sma_position,
    })


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


# --- KABRODA ARCHITECTURE: FOREIGN INTEL PARSER & MAS ROUTING ---
class ForeignIntelPayload(BaseModel):
    raw_text: str

class MASChatPayload(BaseModel):
    symbol: str
    message: str

@app.post("/api/research/chat-mas")
async def chat_with_mas(payload: MASChatPayload, request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"): 
        return JSONResponse({"ok": False, "error": "Unauthorized"})
    
    db_sym = payload.symbol.replace("USDT", "/USDT") if "/" not in payload.symbol else payload.symbol
    response_text = await asyncio.to_thread(kabroda_mas_flow.interrogate_cro, db_sym, payload.message)
    
    return JSONResponse({"ok": True, "reply": response_text})

@app.post("/api/research/audit-intel")
async def audit_foreign_intel(payload: ForeignIntelPayload, request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"): 
        return JSONResponse({"ok": False, "error": "Unauthorized"})

    text = payload.raw_text
    try:
        header_match = re.search(r'([A-Z]+)\s*\|\s*([A-Z]+)\s*@\s*\$([\d,.]+)', text)
        asset = f"{header_match.group(1)}/{header_match.group(2)}"
        entry_price = float(header_match.group(3).replace(',', ''))
        
        t1 = float(re.search(r'Target 1:\s*([\d,.]+)', text).group(1).replace(',', ''))
        t2 = float(re.search(r'Target 2:\s*([\d,.]+)', text).group(1).replace(',', ''))
        t3 = float(re.search(r'Target 3:\s*([\d,.]+)', text).group(1).replace(',', ''))
        sl = float(re.search(r'SL Close Below:\s*([\d,.]+)', text).group(1).replace(',', ''))

        bias = "LONG" if t1 > entry_price else "SHORT"

        # Timeframe lives in the MetaSignals header, e.g. "BTC | USDT @ $76,821.20 - 1H - 1.1 G1"
        tf_match = re.search(r'@\s*\$[\d,.]+\s*-\s*(\d+\s*[HMDWhmdw])', text)
        timeframe = tf_match.group(1).replace(' ', '').upper() if tf_match else "UNKNOWN"

        parsed_packet = {
            "source": "MetaSignals",
            "symbol": asset,
            "bias": bias,
            "timeframe": timeframe,
            "entry_price": entry_price,
            "targets": [t1, t2, t3],
            "stop_loss": sl
        }

        db_sym = asset.replace("USDT", "/USDT") if "/" not in asset else asset
        
        lock_record = db.query(SessionLock).filter(
            SessionLock.symbol == db_sym
        ).order_by(SessionLock.id.desc()).first()
                
        if not lock_record:
            return JSONResponse({"ok": False, "error": f"No active Kabroda session locked for {asset} in DB. Cannot perform audit."})

        current_ssot = json.loads(lock_record.packet_data)

        # Third data source: live multi-timeframe confluence for the momentum audit.
        try:
            mtf_context = await mtf_confluence_scanner.run_mtf_confluence_scan(db_sym)
        except Exception as mtf_err:
            print(f"[AUDIT MTF ERROR] {db_sym}: {mtf_err}")
            mtf_context = {"error": str(mtf_err)}

        audit_result = await asyncio.to_thread(
            kabroda_mas_flow.audit_foreign_intel_pipeline,
            parsed_packet, current_ssot, mtf_context
        )
        
        if audit_result["status"] == "SUCCESS":
            return JSONResponse({
                "ok": True, 
                "message": "Intel audited successfully.", 
                "data": parsed_packet,
                "audit": audit_result["report"]
            })
        else:
            return JSONResponse({"ok": False, "error": "Agent failed to analyze intel. See server logs."})

    except Exception as e:
        return JSONResponse({
            "ok": False, 
            "error": "Failed to parse intel. Ensure the text perfectly matches the MetaSignals format."
        })

# --- AGENT COST INFRASTRUCTURE (PHASE 1) ---

@app.post("/api/admin/run-audit")
async def api_run_audit(request: Request, db: Session = Depends(get_db)):
    """
    Trigger the Audit-AI weekly ledger run on demand. Admin only.
    Runs all 6 pre-defined hypotheses against session_audit_log, writes
    suggestions to audit_suggestion_log (N>=30 only), and appends a
    Markdown brief to system_audit_log.
    """
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)
    try:
        import harness.audit_runner as _audit
        brief = await asyncio.to_thread(_audit.main)
        return JSONResponse({"ok": True, "brief": brief})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


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


@app.get("/api/agents/cost")
async def api_agents_cost(request: Request, db: Session = Depends(get_db)):
    """Returns 24h and 7-day agent spend summary. Admin only."""
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)
    summary = await asyncio.to_thread(agent_core.get_cost_summary)
    return JSONResponse(summary)


@app.post("/api/agents/test-call")
async def api_agents_test_call(request: Request, db: Session = Depends(get_db)):
    """
    Phase 1 success test. Fires one minimal _call_agent() invocation,
    writes a row to agent_run_log, and returns the response + cost.
    Admin only.
    """
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)

    system_prompt = (
        "You are a cost-tracking verification agent for the Kabroda trading "
        "intelligence system. Your only function is to confirm that the Phase 1 "
        "cost infrastructure is operational."
    )
    context_text = (
        "Confirm system status. "
        "Respond with exactly one line: PHASE_1_COST_INFRASTRUCTURE_ONLINE"
    )

    try:
        result = await asyncio.to_thread(
            agent_core._call_agent,
            "infrastructure_test",
            system_prompt,
            context_text,
            "admin_test",
        )
        summary = await asyncio.to_thread(agent_core.get_cost_summary)
        last_call = summary.get("last_10_calls", [{}])[0]
        return JSONResponse({
            "ok": True,
            "agent_response": result,
            "logged_row": last_call,
            "next_step": "Visit /api/agents/cost to see full summary.",
        })
    except RuntimeError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=402)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


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
@app.get("/admin/simulator")
async def admin_simulator_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    return _template_or_fallback(request, templates, "market_simulator.html", ctx)

@app.get("/admin/research")
async def admin_research_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    return _template_or_fallback(request, templates, "research_lab.html", ctx)

@app.get("/admin/mission")
async def mission_brief(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    return _template_or_fallback(request, templates, "mission_brief.html", ctx)

@app.get("/admin")
async def admin_roster_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    users = db.query(UserModel).all()
    ctx["users"] = users
    return _template_or_fallback(request, templates, "admin.html", ctx)

@app.get("/admin/export-audit-ledger")
async def export_audit_ledger(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"): 
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=403)
        
    logs = db.query(CampaignLog).order_by(CampaignLog.created_at.desc()).all()
    audit_data = []
    
    for l in logs:
        try:
            diagnostics = json.loads(l.diagnostic_data) if l.diagnostic_data else {}
        except Exception:
            diagnostics = {}
            
        audit_data.append({
            "trade_id": l.id,
            "symbol": l.symbol,
            "date": l.date_key,
            "bias": l.bias,
            "status": l.status,
            "realized_pnl": l.realized_pnl,
            "diagnostics": diagnostics
        })
        
    return JSONResponse({"ok": True, "total_records": len(audit_data), "ledger": audit_data})

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

@app.post("/api/dmr/live")
async def dmr_live(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    
    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    
    out = await battlebox_pipeline.get_live_battlebox(
        symbol=symbol,
        session_mode=(payload.get("session_mode") or "AUTO").upper(),
        manual_id=payload.get("manual_session_id") or payload.get("session_id"),
        operator_flex=getattr(user, "operator_flex", False)
    )
    return JSONResponse(out)

@app.post("/api/radar/scan")
async def run_radar_scan(request: Request):
    print("[RADAR] scan endpoint called")
    results = await market_radar.scan_sector()
    print(f"[RADAR] returning {len(results)} results")
    return {"ok": True, "results": results}

@app.post("/api/research/run")
async def research_run(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    
    payload = await request.json()
    try:
        out = await research_lab.run_research_lab(payload)
        return JSONResponse(out)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(e)})

@app.post("/api/simulator/run")
async def simulator_run(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    
    if not getattr(user, "is_admin", False): 
        return JSONResponse({"ok": False, "error": "Admin access required for heavy backtesting computations."}, status_code=403)
    
    payload = await request.json()
    try:
        out = await market_simulator.run_simulation(payload)
        return JSONResponse(out)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(e)})

# ==============================================================================
# EXECUTIVE DASHBOARD API ROUTES (Phase 6 — read-only DB queries)
# ==============================================================================

@app.get("/api/dashboard/overview")
async def api_dashboard_overview(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        from sqlalchemy import func
        total      = db.query(func.count(CampaignLog.id)).filter(CampaignLog.symbol == "BTC/USDT", CampaignLog.is_canonical == True).scalar() or 0
        approved   = db.query(func.count(CampaignLog.id)).filter(CampaignLog.symbol == "BTC/USDT", CampaignLog.mas_approval_status == "APPROVED", CampaignLog.is_canonical == True).scalar() or 0
        approved_rate = round(approved / total * 100, 1) if total > 0 else 0.0
        wins   = db.query(func.count(CampaignLog.id)).filter(CampaignLog.symbol == "BTC/USDT", CampaignLog.status == "CLOSED_WIN", CampaignLog.is_canonical == True).scalar() or 0
        losses = db.query(func.count(CampaignLog.id)).filter(CampaignLog.symbol == "BTC/USDT", CampaignLog.status == "CLOSED_LOSS", CampaignLog.is_canonical == True).scalar() or 0
        win_rate = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0.0
        net_r = round(float(wins - losses), 2)
        since_7d = datetime.now(timezone.utc) - timedelta(days=7)
        spend_raw = db.query(func.sum(AgentRunLog.estimated_cost_usd)).filter(
            AgentRunLog.created_at >= since_7d).scalar()
        spend_7d = round(spend_raw or 0.0, 4)
        tok = db.query(func.sum(AgentRunLog.input_tokens), func.sum(AgentRunLog.cache_read_tokens)).filter(
            AgentRunLog.created_at >= since_7d).first()
        total_tok = (tok[0] or 0) + (tok[1] or 0)
        cache_hit_rate = round((tok[1] or 0) / total_tok * 100, 1) if total_tok > 0 else 0.0
        newsletter_count = db.query(func.count(NewsletterLog.id)).scalar() or 0
        return JSONResponse({"ok": True, "total_sessions": total, "approved_rate": approved_rate,
            "win_rate": win_rate, "net_r": net_r, "spend_7d": spend_7d,
            "cache_hit_rate": cache_hit_rate, "newsletter_count": newsletter_count})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/dashboard/accuracy")
async def api_dashboard_accuracy(request: Request, db: Session = Depends(get_db)):
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
        grade_rows = db.query(DecisionJournal.kinematic_grade,
            DecisionJournal.outcome_direction_correct, func.count(DecisionJournal.id)).filter(
            DecisionJournal.symbol == "BTC/USDT",
            DecisionJournal.outcome_direction_correct.isnot(None),
            DecisionJournal.kinematic_grade.isnot(None)
        ).group_by(DecisionJournal.kinematic_grade, DecisionJournal.outcome_direction_correct).all()
        conf_rows = db.query(DecisionJournal.confluence_score,
            DecisionJournal.outcome_direction_correct, func.count(DecisionJournal.id)).filter(
            DecisionJournal.symbol == "BTC/USDT",
            DecisionJournal.outcome_direction_correct.isnot(None),
            DecisionJournal.confluence_score.isnot(None)
        ).group_by(DecisionJournal.confluence_score, DecisionJournal.outcome_direction_correct).all()
        return JSONResponse({"ok": True, "grade_accuracy": _build_accuracy(grade_rows),
                             "confluence_accuracy": _build_accuracy(conf_rows)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/dashboard/costs")
async def api_dashboard_costs(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)
    try:
        from collections import defaultdict
        since_7d = datetime.now(timezone.utc) - timedelta(days=7)
        rows = db.query(AgentRunLog).filter(
            AgentRunLog.created_at >= since_7d, AgentRunLog.status == "SUCCESS").all()
        daily = defaultdict(lambda: defaultdict(float))
        all_agents = set()
        for row in rows:
            day = row.created_at.strftime("%m/%d")
            daily[day][row.agent_name] += row.estimated_cost_usd
            all_agents.add(row.agent_name)
        days_list = [(datetime.now(timezone.utc) - timedelta(days=i)).strftime("%m/%d") for i in range(6, -1, -1)]
        agents_sorted = sorted(all_agents)
        return JSONResponse({"ok": True, "days": days_list,
            "agents": [{"name": ag, "values": [round(daily[d][ag], 5) for d in days_list]} for ag in agents_sorted]})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/dashboard/mas-history")
async def api_dashboard_mas_history(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        from sqlalchemy import func
        approval_rows = db.query(CampaignLog.mas_approval_status,
            func.count(CampaignLog.id)).filter(CampaignLog.symbol == "BTC/USDT", CampaignLog.is_canonical == True).group_by(CampaignLog.mas_approval_status).all()
        approval_counts = {row[0]: row[1] for row in approval_rows}
        pnl_rows = db.query(CampaignLog.closed_at, CampaignLog.date_key,
            CampaignLog.status).filter(
            CampaignLog.symbol == "BTC/USDT",
            CampaignLog.closed_at.isnot(None),
            CampaignLog.is_canonical == True,
        ).order_by(CampaignLog.closed_at).all()
        cumulative = 0.0
        pnl_series = []
        for row in pnl_rows:
            if row.status not in ("CLOSED_WIN", "CLOSED_LOSS"):
                continue
            cumulative += 1.0 if row.status == "CLOSED_WIN" else -1.0
            pnl_series.append({"date": row.date_key, "cumulative": round(cumulative, 2)})
        trades = db.query(CampaignLog).filter(CampaignLog.symbol == "BTC/USDT", CampaignLog.is_canonical == True).order_by(CampaignLog.id.desc()).limit(50).all()
        trades_data = []
        for t in trades:
            if t.status == "CLOSED_WIN":
                r_pnl = "+1.0R"
            elif t.status == "CLOSED_LOSS":
                r_pnl = "-1.0R"
            else:
                r_pnl = None
            trades_data.append({
                "date_key": t.date_key, "bias": t.bias, "mas_approval_status": t.mas_approval_status,
                "status": t.status, "entry_price": t.entry_price, "stop_loss": t.stop_loss,
                "t1": t.t1, "realized_pnl": r_pnl
            })
        return JSONResponse({"ok": True, "approval_counts": approval_counts,
                             "pnl_series": pnl_series, "trades": trades_data})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/dashboard/jewel")
async def api_dashboard_jewel(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        snapshots = db.query(JewelSnapshotLog).filter(
            JewelSnapshotLog.session_label == "NY_OPEN").all()
        open_win = open_loss = closed_win = closed_loss = 0
        for snap in snapshots:
            if not snap.timestamp:
                continue
            date_key = snap.timestamp.strftime("%Y-%m-%d")
            trade = db.query(CampaignLog).filter(
                CampaignLog.symbol == "BTC/USDT",
                CampaignLog.date_key == date_key,
                CampaignLog.status.in_(["CLOSED_WIN", "CLOSED_LOSS"]),
                CampaignLog.is_canonical == True).first()
            if not trade:
                continue
            is_win = trade.status == "CLOSED_WIN"
            if snap.jewel_gate_open:
                open_win  += (1 if is_win else 0)
                open_loss += (0 if is_win else 1)
            else:
                closed_win  += (1 if is_win else 0)
                closed_loss += (0 if is_win else 1)
        return JSONResponse({"ok": True, "open_win": open_win, "open_loss": open_loss,
                             "closed_win": closed_win, "closed_loss": closed_loss})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/dashboard/newsletters")
async def api_dashboard_newsletters(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        rows = db.query(NewsletterLog).order_by(NewsletterLog.id.desc()).limit(30).all()
        data = [{"id": r.id, "date_key": r.date_key, "headline": r.headline,
                 "approval_status": r.approval_status, "publish_status": r.publish_status,
                 "newsletter_md": r.newsletter_md or ""} for r in rows]
        return JSONResponse({"ok": True, "newsletters": data})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


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