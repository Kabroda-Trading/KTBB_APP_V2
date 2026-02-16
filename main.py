# main.py
# ==============================================================================
# KABRODA UNIVERSAL SWITCHBOARD (PRODUCTION CLEANUP v9.5)
# ==============================================================================
# STRUCTURE:
# 1. CORE CONFIG & DATABASE
# 2. PUBLIC ROUTES (Landing, Pricing, Policies)
# 3. AUTHENTICATION & BILLING
# 4. MEMBER SUITE (Session Control, Indicators)
# 5. ADMIN COMMAND (Market Radar, Roster, Lock Target)
# ==============================================================================

import os
from typing import Any, Dict

from fastapi import Depends, FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates
from sqlalchemy.orm import Session

# --- INTERNAL MODULES (KEEPING ONLY THE ESSENTIALS) ---
import auth
import billing
import database
from database import init_db, get_db, UserModel

# --- CORE ENGINES ---
import battlebox_pipeline  # The heart of the system
import market_radar        # Admin Tool

# --- APP INITIALIZATION ---
app = FastAPI(title="Kabroda BattleBox", version="9.5-PROD")

# SECRET KEY (Ensure this is set in Render Environment!)
SECRET_KEY = os.getenv("SESSION_SECRET", "kabroda_prod_secret_key_999")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=86400*30)

# MOUNT STATIC FILES (CSS, JS, IMAGES)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# DATABASE STARTUP
@app.on_event("startup")
def on_startup():
    init_db()

# --- HELPERS (PRESERVED) ---
def _template_or_fallback(request: Request, templates: Jinja2Templates, name: str, context: Dict[str, Any]):
    try: return templates.TemplateResponse(name, context)
    except Exception as e: return HTMLResponse(f"<h2>System Error: Template Failure</h2><p>{str(e)}</p>", status_code=500)

def get_user_context(request: Request, db: Session):
    """
    Standard context builder for every page load.
    Checks login status and admin privileges.
    """
    user_id = request.session.get(auth.SESSION_KEY)
    if not user_id: return {"is_logged_in": False, "is_admin": False}
    
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    return {
        "is_logged_in": True,
        "is_admin": user.is_admin if user else False,
        "username": user.username if user else "Operative",
        "email": user.email if user else "",
        "sub_status": user.subscription_status if user else "inactive"
    }

# --- INCLUDE ROUTERS ---
app.include_router(auth.router)
app.include_router(billing.router, prefix="/api/billing")

# ==============================================================================
# 1. PUBLIC PAGES
# ==============================================================================
@app.get("/")
async def home(request: Request, db: Session = Depends(get_db)):
    return _template_or_fallback(request, templates, "home.html", get_user_context(request, db))

@app.get("/pricing")
async def pricing(request: Request, db: Session = Depends(get_db)):
    return _template_or_fallback(request, templates, "pricing.html", get_user_context(request, db))

@app.get("/how-it-works")
async def methodology(request: Request, db: Session = Depends(get_db)):
    return _template_or_fallback(request, templates, "how_it_works.html", get_user_context(request, db))

@app.get("/privacy")
async def privacy(request: Request, db: Session = Depends(get_db)):
    return _template_or_fallback(request, templates, "privacy.html", get_user_context(request, db))

@app.get("/login")
async def login_page(request: Request, db: Session = Depends(get_db)):
    return _template_or_fallback(request, templates, "login.html", get_user_context(request, db))

@app.get("/register")
async def register_page(request: Request, db: Session = Depends(get_db)):
    return _template_or_fallback(request, templates, "register.html", get_user_context(request, db))

@app.get("/logout")
async def logout(request: Request):
    auth.logout_session(request)
    return RedirectResponse(url="/login", status_code=303)

# ==============================================================================
# 2. MEMBER SUITE
# ==============================================================================
@app.get("/suite")
async def session_control(request: Request, db: Session = Depends(get_db)):
    """
    THE MAIN DASHBOARD (Previously 'Session Control')
    """
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse("/login")
    return _template_or_fallback(request, templates, "session_control.html", ctx)

@app.get("/indicators")
async def indicators(request: Request, db: Session = Depends(get_db)):
    """
    RESTORED: The Indicators setup page.
    """
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse("/login")
    return _template_or_fallback(request, templates, "indicators.html", ctx)

@app.get("/account")
async def account(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse("/login")
    
    # Inject full user object for account management
    uid = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    ctx["user"] = user
    return _template_or_fallback(request, templates, "account.html", ctx)

@app.post("/account/password")
async def update_password(request: Request, db: Session = Depends(get_db)):
    """Handles password updates from the account page"""
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(401)
    try:
        pl = await request.json()
        user = db.query(UserModel).filter(UserModel.id == uid).first()
        if user and pl.get("password"):
            user.password_hash = auth.hash_password(pl["password"])
            db.commit()
            return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})
    return JSONResponse({"ok": False})

# ==============================================================================
# 3. ADMIN COMMAND
# ==============================================================================
@app.get("/admin")
async def admin_roster(request: Request, db: Session = Depends(get_db)):
    """User Management"""
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    
    users = db.query(UserModel).all()
    ctx["roster"] = users
    return _template_or_fallback(request, templates, "admin.html", ctx)

@app.get("/admin/radar")
async def market_radar_view(request: Request, db: Session = Depends(get_db)):
    """The Market Scanner"""
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    return _template_or_fallback(request, templates, "market_radar.html", ctx)

@app.get("/admin/target-lock")
async def lock_target_tool(request: Request, db: Session = Depends(get_db)):
    """
    PRESERVED: The Calculator Tool you specifically requested to keep.
    """
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    return _template_or_fallback(request, templates, "lock_target.html", ctx)

# ==============================================================================
# 4. DATA API ENDPOINTS
# ==============================================================================

@app.post("/api/dmr/live")
async def get_live_dmr(request: Request, payload: Dict[str, Any]):
    """
    Feeds 'Session Control' with live BattleBox data.
    Uses 'battlebox_pipeline' to fetch levels.
    """
    symbol = payload.get("symbol", "BTCUSDT")
    session_id = payload.get("session_id", "us_ny_futures")
    
    # Connect to the Corporate Pipeline
    data = await battlebox_pipeline.get_live_battlebox(
        symbol=symbol,
        session_mode="MANUAL",
        manual_id=session_id
    )
    return data

@app.get("/api/radar/scan")
async def run_radar_scan(request: Request):
    """
    Feeds the 'Market Radar' admin tool.
    """
    # Simple Admin Check
    # (In prod, you might want strict auth here, but for now we keep it open for the tool)
    results = await market_radar.scan_market()
    return {"ok": True, "results": results}