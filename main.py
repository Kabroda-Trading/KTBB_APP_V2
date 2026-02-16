# main.py
# ---------------------------------------------------------
# KABRODA UNIFIED SERVER: BATTLEBOX v10.2 (WHOP & PIPELINE RESTORED)
# ---------------------------------------------------------
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
import battlebox_pipeline
import market_radar
import research_lab

from database import init_db, get_db, UserModel
from membership import get_membership_state, require_paid_access, ensure_symbol_allowed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Kabroda BattleBox", version="10.2")
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
    max_age=86400 * 30  # Keeps users logged in for 30 days
)

# Register our standalone routers for Auth and Billing
app.include_router(auth.router)
app.include_router(billing.router, prefix="/billing")

@app.on_event("startup")
def _startup():
    init_db()

# --- HELPERS ---
def _template_or_fallback(request: Request, templates: Jinja2Templates, name: str, context: Dict[str, Any]):
    try: 
        return templates.TemplateResponse(name, context)
    except Exception as e: 
        return HTMLResponse(f"<h2>System Error: {name}</h2><p>{str(e)}</p>", status_code=500)

def get_user_context(request: Request, db: Session):
    uid = request.session.get(auth.SESSION_KEY)
    
    # CRITICAL FIX: The Request object is strictly injected into the base context
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
        "sub_status": getattr(user, "subscription_status", "inactive") if user else "inactive",
        "user": user
    })
    
    # Add plan flags for older template compatibility
    if user:
        ms = get_membership_state(user)
        base_context.update({"plan_label": ms.label})
        
    return base_context

# --- PUBLIC ROUTES ---
@app.get("/")
async def home(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "home.html", ctx)

@app.get("/analysis")
async def analysis(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "analysis.html", ctx)

@app.get("/how-it-works")
async def how_it_works(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "how_it_works.html", ctx)

@app.get("/pricing")
async def pricing(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "pricing.html", ctx)

@app.get("/about")
async def about(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "about.html", ctx)

@app.get("/privacy")
async def privacy_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "privacy.html", ctx)

# --- SUITE ROUTES ---
@app.get("/suite")
async def suite(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    require_paid_access(ctx["user"])
    return _template_or_fallback(request, templates, "session_control.html", ctx)

@app.get("/suite/battle-control")
async def battle_control_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    require_paid_access(ctx["user"])
    return _template_or_fallback(request, templates, "suite_home.html", ctx)

@app.get("/suite/research-lab")
async def suite_research_lab_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    require_paid_access(ctx["user"])
    return _template_or_fallback(request, templates, "research_lab.html", ctx)

@app.get("/indicators")
async def indicators(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    require_paid_access(ctx["user"])
    return _template_or_fallback(request, templates, "indicators.html", ctx)

@app.get("/account")
async def account(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    # Adding tier_label specifically for account.html compatibility
    ctx["tier_label"] = ctx.get("plan_label", "")
    return _template_or_fallback(request, templates, "account.html", ctx)

# --- PROFILE UPDATE API ---
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
@app.get("/admin/radar")
async def radar_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    return _template_or_fallback(request, templates, "market_radar.html", ctx)

@app.get("/admin/target-lock")
async def lock_target_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    return _template_or_fallback(request, templates, "lock_target.html", ctx)

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

# --- UNIFIED API ENDPOINTS (PIPELINE) ---
@app.post("/api/dmr/run-raw")
async def dmr_run_raw(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    require_paid_access(user)
    
    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    
    # Check if frontend sent session_id, else fallback to tz logic
    session_id = payload.get("session_id")
    requested_tz = payload.get("session_tz") or (getattr(user, "session_tz", None) or "UTC")
    
    ensure_symbol_allowed(user, symbol)
    
    if session_id:
        out = await battlebox_pipeline.get_session_review(symbol=symbol, session_id=session_id)
    else:
        out = await battlebox_pipeline.get_session_review(symbol=symbol, session_tz=requested_tz)
    return JSONResponse(out)

@app.post("/api/dmr/live")
async def dmr_live(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    require_paid_access(user)
    
    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    ensure_symbol_allowed(user, symbol)
    
    out = await battlebox_pipeline.get_live_battlebox(
        symbol=symbol,
        session_mode=(payload.get("session_mode") or "AUTO").upper(),
        manual_id=payload.get("manual_session_id") or payload.get("session_id"),
        operator_flex=getattr(user, "operator_flex", False)
    )
    return JSONResponse(out)

@app.post("/api/radar/scan")
async def run_radar_scan(request: Request):
    results = await market_radar.scan_sector()
    return {"ok": True, "results": results}

@app.post("/api/research/run")
async def research_run(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    require_paid_access(user)
    
    payload = await request.json()
    try:
        # Connect to the research_lab data collector
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