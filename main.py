# main.py
# ---------------------------------------------------------
# KABRODA UNIFIED SERVER: BATTLEBOX + WEALTH OS v6.9
# (Research Lab Integrated)
# ---------------------------------------------------------
from __future__ import annotations

import asyncio
import os
import traceback
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates

# --- CORE IMPORTS ---
import auth
import billing
import dmr_report
import data_feed
import sse_engine

# --- WEALTH IMPORTS ---
import sjan_brain
import wealth_allocator

# --- BATTLE CONTROL IMPORT ---
import battle_control

# --- RESEARCH LAB IMPORT (NEW) ---
import research_lab

from database import init_db, get_db, UserModel
from membership import (
    get_membership_state,
    require_paid_access,
    ensure_symbol_allowed,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

def _bool_env(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None: return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")

SESSION_SECRET = os.getenv("SESSION_SECRET") or os.getenv("SECRET_KEY") or "dev-session-secret"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
IS_HTTPS = PUBLIC_BASE_URL.startswith("https://")
SESSION_HTTPS_ONLY = _bool_env("SESSION_HTTPS_ONLY", default=IS_HTTPS)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=SESSION_HTTPS_ONLY,
    same_site="lax",
)

@app.on_event("startup")
def _startup():
    init_db()
    db = next(get_db()) 
    try:
        # Auto-migrate columns if missing
        try:
            db.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR"))
            db.commit()
        except Exception: db.rollback()
        try:
            db.execute(text("ALTER TABLE users ADD COLUMN tradingview_id VARCHAR"))
            db.commit()
        except Exception: db.rollback()
        try:
            db.execute(text("ALTER TABLE users ADD COLUMN operator_flex BOOLEAN"))
            db.commit()
        except Exception: db.rollback()
    except Exception as e:
        print(f"--- STARTUP SCHEMA LOG: {e}")
    finally:
        db.close()

# --- HELPERS ---
def _session_user_dict(request: Request) -> Optional[Dict[str, Any]]:
    u = request.session.get("user")
    return u if isinstance(u, dict) else None

def _require_session_user(request: Request) -> Dict[str, Any]:
    return auth.require_session_user(request)

def _db_user_from_session(db: Session, sess: Dict[str, Any]) -> UserModel:
    uid = sess.get("id")
    if not uid: raise HTTPException(status_code=401, detail="Invalid session")
    u = db.query(UserModel).filter(UserModel.id == uid).first()
    if not u: raise HTTPException(status_code=401, detail="User not found")
    return u

def _plan_flags(u: UserModel) -> Dict[str, Any]:
    ms = get_membership_state(u)
    return {"is_paid": ms.is_paid, "plan": ms.plan, "plan_label": ms.label}

# --- PUBLIC ROUTES ---
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request, "is_logged_in": _session_user_dict(request) is not None, "force_public_nav": True})

@app.get("/analysis", response_class=HTMLResponse)
def analysis(request: Request):
    return templates.TemplateResponse("analysis.html", {"request": request, "is_logged_in": _session_user_dict(request) is not None, "force_public_nav": True})

@app.get("/how-it-works", response_class=HTMLResponse)
def how_it_works(request: Request):
    return templates.TemplateResponse("how_it_works.html", {"request": request, "is_logged_in": _session_user_dict(request) is not None, "force_public_nav": True})

@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    return templates.TemplateResponse("pricing.html", {"request": request, "is_logged_in": _session_user_dict(request) is not None, "force_public_nav": True})

@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request, "is_logged_in": _session_user_dict(request) is not None, "force_public_nav": True})

@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request, "is_logged_in": _session_user_dict(request) is not None, "force_public_nav": True})

# --- SUITE ROUTES ---
@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request, db: Session = Depends(get_db)):
    sess = _session_user_dict(request)
    if not sess: return RedirectResponse(url="/login", status_code=303)
    u = _db_user_from_session(db, sess)
    try: require_paid_access(u)
    except HTTPException: return RedirectResponse(url="/pricing?paywall=1", status_code=303)
    flags = _plan_flags(u)
    
    return templates.TemplateResponse("session_control.html", {
        "request": request, "is_logged_in": True,
        "user": {"email": u.email, "username": u.username, "session_tz": (u.session_tz or "UTC"), "plan_label": flags.get("plan_label", ""), "plan": flags.get("plan") or ""}
    })

@app.get("/suite/battle-control", response_class=HTMLResponse)
def battle_control_page(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    flags = _plan_flags(u)
    return templates.TemplateResponse("battle_control.html", {
        "request": request, "is_logged_in": True,
        "user": u, 
        "plan_label": flags.get("plan_label", "")
    })

# --- RESEARCH LAB (NEW PAGE) ---
@app.get("/suite/research-lab", response_class=HTMLResponse)
def research_lab_page(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    flags = _plan_flags(u)
    return templates.TemplateResponse("research_lab.html", {
        "request": request,
        "is_logged_in": True,
        "user": u,
        "plan_label": flags.get("plan_label", "")
    })

@app.get("/indicators", response_class=HTMLResponse)
def indicators(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    flags = _plan_flags(u)
    return templates.TemplateResponse("indicators.html", {
        "request": request, "is_logged_in": True,
        "user": {"email": u.email, "username": u.username, "session_tz": (u.session_tz or "UTC"), "plan_label": flags.get("plan_label", ""), "plan": flags.get("plan") or ""}
    })

# --- AUTH & ACCOUNT ---
@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login")
def login_post(request: Request, db: Session = Depends(get_db), email: str = Form(...), password: str = Form(...)):
    u = auth.authenticate_user(db, email=email, password=password)
    if not u: return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"}, status_code=401)
    auth.set_user_session(request, u)
    # Check if paid, otherwise send to pricing
    ms = get_membership_state(u)
    if not ms.is_paid:
        return RedirectResponse(url="/pricing?renewal=1", status_code=303)
    return RedirectResponse(url="/suite", status_code=303)

@app.get("/logout")
def logout(request: Request):
    auth.clear_user_session(request)
    return RedirectResponse(url="/", status_code=303)

@app.get("/register", response_class=HTMLResponse)
def register_get(request: Request):
    if auth.registration_disabled(): return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("register.html", {"request": request, "error": None})

@app.post("/register")
def register_post(request: Request, db: Session = Depends(get_db), email: str = Form(...), password: str = Form(...)):
    if auth.registration_disabled(): raise HTTPException(status_code=403, detail="Registration disabled")
    
    # 1. Create User
    try: 
        u = auth.create_user(db, email=email, password=password)
    except HTTPException as e: 
        return templates.TemplateResponse("register.html", {"request": request, "error": str(e.detail)}, status_code=e.status_code)
    
    # 2. Login immediately
    auth.set_user_session(request, u)
    
    # 3. Redirect to Stripe (Errors here will now show up in logs, not silent redirect)
    # Defaulting to monthly plan for initial flow
    checkout_url = billing.create_checkout_session(db=db, user_model=u, plan_key="monthly")
    return RedirectResponse(url=checkout_url, status_code=303)

@app.get("/account", response_class=HTMLResponse)
def account(request: Request, db: Session = Depends(get_db)):
    sess = _session_user_dict(request)
    if not sess: return RedirectResponse(url="/login", status_code=303)
    u = _db_user_from_session(db, sess)
    flags = _plan_flags(u)
    return templates.TemplateResponse("account.html", {
        "request": request, "is_logged_in": True,
        "user": u,
        "tier_label": flags["plan_label"],
    })

@app.post("/account/settings")
async def account_settings(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    data = await request.json()
    u.operator_flex = bool(data.get("operator_flex", False))
    db.commit()
    return {"status": "ok"}

# --- BILLING & API ---
@app.post("/billing/checkout")
async def billing_checkout(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    try: payload = await request.json()
    except: payload = {}
    plan_key = payload.get("plan", "monthly")
    return {"url": billing.create_checkout_session(db=db, user_model=u, plan_key=plan_key)}

@app.post("/billing/portal")
async def billing_portal(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    return {"url": billing.create_billing_portal(db=db, user_model=u)}

@app.post("/billing/webhook")
async def billing_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    return billing.handle_webhook(payload=payload, sig_header=sig, db=db, UserModel=UserModel)

@app.post("/api/dmr/run-raw")
async def dmr_run_raw(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    requested_tz = payload.get("session_tz") or (u.session_tz or "UTC")
    ensure_symbol_allowed(u, symbol)
    inputs = await data_feed.get_inputs(symbol=symbol, session_tz=requested_tz)
    raw = await asyncio.to_thread(dmr_report.generate_report_from_inputs, inputs, requested_tz)
    return JSONResponse(raw)

@app.post("/api/dmr/live")
async def dmr_live(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    ensure_symbol_allowed(u, symbol)
    session_mode = (payload.get("session_mode") or "AUTO").upper()
    manual_id = payload.get("manual_session_id", None)
    operator_flex = bool(payload.get("operator_flex", False))
    live_data = await battle_control.run_live_pulse(symbol, session_mode=session_mode, manual_id=manual_id, operator_flex=operator_flex)
    return JSONResponse(live_data)

# --- RESEARCH LAB (API) ---
@app.post("/api/research/run")
async def research_run(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)

    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    ensure_symbol_allowed(u, symbol)

    start_date_utc = payload.get("start_date_utc")
    end_date_utc = payload.get("end_date_utc")
    session_ids = payload.get("session_ids") or None

    if not start_date_utc or not end_date_utc:
        return JSONResponse({"ok": False, "error": "start_date_utc and end_date_utc are required."})

    try:
        out = await research_lab.run_research_lab(
            symbol=symbol,
            start_date_utc=start_date_utc,
            end_date_utc=end_date_utc,
            session_ids=session_ids,
        )
        return JSONResponse(out)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(e)})