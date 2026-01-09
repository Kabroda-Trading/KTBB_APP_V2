# main.py
# ---------------------------------------------------------
# KABRODA UNIFIED SERVER: BATTLEBOX v10.0 (FINAL)
# ---------------------------------------------------------
# Includes:
# 1. Unified Pipeline (No drift)
# 2. Account Profile Updates (Operative ID / Timezone)
# 3. 30-Day Session Persistence
# 4. Correct Billing Routing
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
import battlebox_pipeline  # <--- SINGLE SOURCE OF TRUTH
import research_lab

from database import init_db, get_db, UserModel
from membership import get_membership_state, require_paid_access, ensure_symbol_allowed

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

# --- SESSION CONFIGURATION (30-DAY PERSISTENCE) ---
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=SESSION_HTTPS_ONLY,
    same_site="lax",
    max_age=86400 * 30  # <--- KEEPS USERS LOGGED IN FOR 30 DAYS
)

# --- ADMIN ROUTE ---
@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    
    # SECURITY CHECK: Only allow specific emails
    # REPLACE THIS WITH YOUR REAL ADMIN EMAILS
    ALLOWED_ADMINS = ["grossmonkeytrader@protonmail.com", "spiritmaker79@gmail.com"] 
    
    if u.email not in ALLOWED_ADMINS:
        return RedirectResponse(url="/suite", status_code=303)

    users = db.query(UserModel).order_by(UserModel.created_at.desc()).all()
    return templates.TemplateResponse("admin.html", {"request": request, "users": users})

@app.on_event("startup")
def _startup():
    init_db()
    db = next(get_db()) 
    try:
        try: db.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR")); db.commit()
        except: db.rollback()
        try: db.execute(text("ALTER TABLE users ADD COLUMN tradingview_id VARCHAR")); db.commit()
        except: db.rollback()
        try: db.execute(text("ALTER TABLE users ADD COLUMN operator_flex BOOLEAN")); db.commit()
        except: db.rollback()
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
    return templates.TemplateResponse("home.html", {"request": request, "is_logged_in": _session_user_dict(request) is not None})

@app.get("/analysis", response_class=HTMLResponse)
def analysis(request: Request):
    return templates.TemplateResponse("analysis.html", {"request": request, "is_logged_in": _session_user_dict(request) is not None})

@app.get("/how-it-works", response_class=HTMLResponse)
def how_it_works(request: Request):
    return templates.TemplateResponse("how_it_works.html", {"request": request, "is_logged_in": _session_user_dict(request) is not None})

@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    return templates.TemplateResponse("pricing.html", {"request": request, "is_logged_in": _session_user_dict(request) is not None})

@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request, "is_logged_in": _session_user_dict(request) is not None})

@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request, "is_logged_in": _session_user_dict(request) is not None})

# --- SUITE ROUTES ---
@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request, db: Session = Depends(get_db)):
    sess = _session_user_dict(request)
    if not sess: return RedirectResponse(url="/login", status_code=303)
    u = _db_user_from_session(db, sess)
    try: require_paid_access(u)
    except HTTPException: return RedirectResponse(url="/pricing?paywall=1", status_code=303)
    flags = _plan_flags(u)
    return templates.TemplateResponse("session_control.html", {"request": request, "is_logged_in": True, "user": u, "plan_label": flags.get("plan_label", "")})

@app.get("/suite/battle-control", response_class=HTMLResponse)
def battle_control_page(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    flags = _plan_flags(u)
    return templates.TemplateResponse("battle_control.html", {"request": request, "is_logged_in": True, "user": u, "plan_label": flags.get("plan_label", "")})

@app.get("/suite/research-lab", response_class=HTMLResponse)
def research_lab_page(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    flags = _plan_flags(u)
    return templates.TemplateResponse("research_lab.html", {"request": request, "is_logged_in": True, "user": u, "plan_label": flags.get("plan_label", "")})

@app.get("/indicators", response_class=HTMLResponse)
def indicators(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    flags = _plan_flags(u)
    return templates.TemplateResponse("indicators.html", {"request": request, "is_logged_in": True, "user": u, "plan_label": flags.get("plan_label", "")})

# --- AUTH & ACCOUNT ---
@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request, db: Session = Depends(get_db)):
    # 1. Check if user is already logged in
    sess = _session_user_dict(request)
    if sess:
        return RedirectResponse(url="/suite", status_code=303)
        
    # 2. If not, show login page
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login")
def login_post(request: Request, db: Session = Depends(get_db), email: str = Form(...), password: str = Form(...)):
    u = auth.authenticate_user(db, email=email, password=password)
    if not u: return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"}, status_code=401)
    auth.set_user_session(request, u)
    if not get_membership_state(u).is_paid: return RedirectResponse(url="/pricing?renewal=1", status_code=303)
    return RedirectResponse(url="/suite", status_code=303)

@app.get("/logout")
def logout(request: Request):
    auth.clear_user_session(request)
    return RedirectResponse(url="/", status_code=303)

@app.get("/register", response_class=HTMLResponse)
def register_get(request: Request, plan: str = "monthly"):
    if auth.registration_disabled(): return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("register.html", {"request": request, "error": None, "plan": plan})

@app.post("/register")
def register_post(request: Request, db: Session = Depends(get_db), email: str = Form(...), password: str = Form(...), plan: str = Form("monthly")):
    if auth.registration_disabled(): raise HTTPException(status_code=403, detail="Registration disabled")
    try: u = auth.create_user(db, email=email, password=password)
    except HTTPException as e: return templates.TemplateResponse("register.html", {"request": request, "error": str(e.detail), "plan": plan}, status_code=e.status_code)
    auth.set_user_session(request, u)
    return RedirectResponse(url=billing.create_checkout_session(db=db, user_model=u, plan_key=plan), status_code=303)

@app.get("/account", response_class=HTMLResponse)
def account(request: Request, db: Session = Depends(get_db)):
    sess = _session_user_dict(request)
    if not sess: return RedirectResponse(url="/login", status_code=303)
    u = _db_user_from_session(db, sess)
    return templates.TemplateResponse("account.html", {"request": request, "is_logged_in": True, "user": u, "tier_label": _plan_flags(u)["plan_label"]})

@app.post("/account/settings")
async def account_settings(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    data = await request.json()
    u.operator_flex = bool(data.get("operator_flex", False))
    db.commit()
    return {"status": "ok"}

# --- PROFILE UPDATE (Identity Protocol) ---
@app.post("/account/profile")
async def account_profile_update(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    data = await request.json()
    
    if "username" in data:
        u.username = str(data["username"]).strip()[:50]
    if "tradingview_id" in data:
        u.tradingview_id = str(data["tradingview_id"]).strip()
    if "session_tz" in data:
        u.session_tz = str(data["session_tz"]).strip()
        
    db.commit()
    return {"status": "ok"}

# --- BILLING ---
@app.post("/billing/checkout")
async def billing_checkout(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    payload = await request.json()
    return {"url": billing.create_checkout_session(db=db, user_model=u, plan_key=payload.get("plan", "monthly"))}

@app.post("/billing/portal")
async def billing_portal(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    return {"url": billing.create_billing_portal(db=db, user_model=u)}

@app.post("/billing/webhook")
async def billing_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    return billing.handle_webhook(payload=payload, sig_header=request.headers.get("stripe-signature", ""), db=db, UserModel=UserModel)

# --- UNIFIED API ENDPOINTS (PIPELINE) ---
@app.post("/api/dmr/run-raw")
async def dmr_run_raw(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    requested_tz = payload.get("session_tz") or (u.session_tz or "UTC")
    ensure_symbol_allowed(u, symbol)
    # PIPELINE CALL
    out = await battlebox_pipeline.get_session_review(symbol=symbol, session_tz=requested_tz)
    return JSONResponse(out)

@app.post("/api/dmr/live")
async def dmr_live(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    ensure_symbol_allowed(u, symbol)
    # PIPELINE CALL
    out = await battlebox_pipeline.get_live_battlebox(
        symbol=symbol,
        session_mode=(payload.get("session_mode") or "AUTO").upper(),
        manual_id=payload.get("manual_session_id", None),
        operator_flex=bool(payload.get("operator_flex", False))
    )
    return JSONResponse(out)

@app.post("/api/research/run")
async def research_run(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    payload = await request.json()
    try:
        out = await research_lab.run_research_lab(
            symbol=(payload.get("symbol") or "BTCUSDT").strip().upper(),
            start_date_utc=payload.get("start_date_utc"),
            end_date_utc=payload.get("end_date_utc"),
            session_ids=payload.get("session_ids")
        )
        return JSONResponse(out)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(e)})