# main.py
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates

import auth
import billing
import dmr_report
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
    if v is None:
        return default
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

# -------------------------------------------------------------------
# STARTUP EVENT (AUTO-FIX DATABASE)
# -------------------------------------------------------------------
@app.on_event("startup")
def _startup():
    init_db()
    
    print("--- CHECKING DATABASE SCHEMA ---")
    db = next(get_db()) 
    try:
        # 1. Fix Username Column
        try:
            db.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR"))
            db.commit()
            print("--- ADDED 'username' COLUMN ---")
        except Exception:
            db.rollback()

        # 2. Fix TradingView ID Column (NEW)
        try:
            db.execute(text("ALTER TABLE users ADD COLUMN tradingview_id VARCHAR"))
            db.commit()
            print("--- ADDED 'tradingview_id' COLUMN ---")
        except Exception:
            db.rollback()
            
    except Exception as e:
        print(f"--- SCHEMA CHECK NOTE: {e}")
    finally:
        db.close()

# -------------------------------------------------------------------
# Session helpers
# -------------------------------------------------------------------
def _session_user_dict(request: Request) -> Optional[Dict[str, Any]]:
    u = request.session.get("user")
    return u if isinstance(u, dict) else None

def _require_session_user(request: Request) -> Dict[str, Any]:
    return auth.require_session_user(request)

def _db_user_from_session(db: Session, sess: Dict[str, Any]) -> UserModel:
    uid = sess.get("id")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid session")
    u = db.query(UserModel).filter(UserModel.id == uid).first()
    if not u:
        raise HTTPException(status_code=401, detail="User not found")
    return u

def _plan_flags(u: UserModel) -> Dict[str, Any]:
    ms = get_membership_state(u)
    return {
        "is_paid": ms.is_paid,
        "plan": ms.plan,
        "plan_label": ms.label,
    }

# -------------------------------------------------------------------
# Public routes
# -------------------------------------------------------------------
@app.head("/")
def head_root():
    return {"ok": True}

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request, "is_logged_in": False, "force_public_nav": True})

@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request, "is_logged_in": False, "force_public_nav": True})

@app.get("/how-it-works", response_class=HTMLResponse)
def how_it_works(request: Request):
    return templates.TemplateResponse("how_it_works.html", {"request": request, "is_logged_in": False, "force_public_nav": True})

@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    return templates.TemplateResponse("pricing.html", {"request": request, "is_logged_in": False, "force_public_nav": True})

# -------------------------------------------------------------------
# Suite (paywalled)
# -------------------------------------------------------------------
@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request, db: Session = Depends(get_db)):
    sess = _session_user_dict(request)
    if not sess: return RedirectResponse(url="/login", status_code=303)
    u = _db_user_from_session(db, sess)
    try: require_paid_access(u)
    except HTTPException: return RedirectResponse(url="/pricing?paywall=1", status_code=303)
    flags = _plan_flags(u)
    return templates.TemplateResponse("app.html", {
        "request": request, "is_logged_in": True,
        "user": {"email": u.email, "username": u.username, "session_tz": (u.session_tz or "UTC"), "plan_label": flags.get("plan_label", ""), "plan": flags.get("plan") or ""}
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

@app.get("/account", response_class=HTMLResponse)
def account(request: Request, db: Session = Depends(get_db)):
    sess = _session_user_dict(request)
    if not sess: return RedirectResponse(url="/login", status_code=303)
    u = _db_user_from_session(db, sess)
    flags = _plan_flags(u)
    return templates.TemplateResponse("account.html", {
        "request": request, "is_logged_in": True,
        "user": {"email": u.email, "username": u.username, "tradingview_id": u.tradingview_id, "session_tz": (u.session_tz or "UTC")},
        "tier_label": flags["plan_label"],
    })

@app.post("/account/profile")
async def account_update_profile(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    try: payload = await request.json()
    except: payload = {}
    new_name = payload.get("username", "").strip()
    u.username = new_name
    db.commit()
    if "user" in request.session: request.session["user"]["username"] = new_name
    return {"ok": True, "username": new_name}

@app.post("/account/session-timezone")
async def account_set_timezone(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    try: payload = await request.json()
    except: payload = {}
    tz = (payload.get("timezone") or "").strip()
    if not tz: raise HTTPException(status_code=400, detail="Missing timezone")
    u.session_tz = tz
    db.commit()
    return {"ok": True, "timezone": tz}

# -------------------------------------------------------------------
# NEW: TradingView ID Save Route (With Alert)
# -------------------------------------------------------------------
@app.post("/account/tradingview-id")
async def account_save_tvid(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    
    try: payload = await request.json()
    except: payload = {}
    
    tv_id = (payload.get("tradingview_id") or "").strip()
    u.tradingview_id = tv_id
    db.commit()
    
    # 🚨 NOTIFICATION LOG
    print(f"🚨 ACTIVATION REQUEST: User {u.email} -> TradingView ID: {tv_id}")
    
    return {"ok": True}

# -------------------------------------------------------------------
# Auth & Billing Routes
# -------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login")
def login_post(request: Request, db: Session = Depends(get_db), email: str = Form(...), password: str = Form(...)):
    u = auth.authenticate_user(db, email=email, password=password)
    if not u: return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"}, status_code=401)
    auth.set_user_session(request, u)
    return RedirectResponse(url="/suite", status_code=303)

@app.get("/logout")
def logout_get(request: Request):
    auth.clear_user_session(request)
    return RedirectResponse(url="/", status_code=303)

@app.post("/logout")
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
    try:
        u = auth.create_user(db, email=email, password=password)
    except HTTPException as e:
        return templates.TemplateResponse("register.html", {"request": request, "error": str(e.detail)}, status_code=e.status_code)
    auth.set_user_session(request, u)
    try: return RedirectResponse(url=billing.create_checkout_session(db=db, user_model=u), status_code=303)
    except: return RedirectResponse(url="/pricing?new=1", status_code=303)

# -------------------------------------------------------------------
# API & Billing
# -------------------------------------------------------------------
@app.post("/api/dmr/run-raw")
async def dmr_run_raw(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    ensure_symbol_allowed(u, symbol)
    tz = (u.session_tz or "UTC").strip() or "UTC"
    raw = await asyncio.to_thread(dmr_report.run_auto_raw, symbol=symbol, session_tz=tz)
    request.session["last_dmr_meta"] = {"symbol": raw.get("symbol", symbol), "date": raw.get("date", "")}
    return JSONResponse(raw)

@app.post("/billing/checkout")
async def billing_checkout(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    return {"url": billing.create_checkout_session(db=db, user_model=u)}

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