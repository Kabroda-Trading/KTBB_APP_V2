# main.py
# ==============================================================================
# KABRODA UNIVERSAL SWITCHBOARD (MAIN DISPATCHER)
# ==============================================================================
# - Architecture: "Gateway Pattern"
# - Function: Receives 'session_id' from ANY page and routes to the correct Engine.
# - Updates: 
#    1. Removed Paywall (Session/Battle Control are Free).
#    2. Gated High-Value Tools (Omega/Research) to Admins.
#    3. Fixed Simulation Mode wiring.
#    4. Added manual password reset tool.
# ==============================================================================

import os
from datetime import datetime, timezone 
from typing import Any, Dict

from fastapi import Depends, FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text, inspect

import auth
from database import init_db, get_db, UserModel, SystemLog, engine

# CORE ENGINES
import project_omega
import battlebox_pipeline
import research_lab
import ai_analyst

# --- HELPERS ---
def _template_or_fallback(request: Request, templates: Jinja2Templates, name: str, context: Dict[str, Any]):
    try:
        return templates.TemplateResponse(name, context)
    except Exception as e:
        return HTMLResponse(
            f"<h2>System Error</h2><p>Could not load {name}.<br>Error: {str(e)}</p>",
            status_code=500,
        )

# --- USER CONTEXT HELPER (For Nav Bar) ---
def get_user_context(request: Request, db: Session):
    user_id = request.session.get(auth.SESSION_KEY)
    if not user_id:
        return {"is_logged_in": False, "is_admin": False, "user": None}
    
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if not user:
        return {"is_logged_in": False, "is_admin": False, "user": None}
        
    return {
        "is_logged_in": True,
        "is_admin": getattr(user, "is_admin", False),
        "user": user
    }

app = FastAPI()

SECRET_KEY = os.getenv("SECRET_KEY") or "dev-secret-change-me"
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=True)

# --- FOLDER CONFIG ---
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates") 

app.include_router(auth.router)

@app.on_event("startup")
def on_startup():
    init_db()
    # --- DB REPAIR (SAFE) ---
    print(">>> SYSTEM CHECK: Verifying Database Schema...")
    try:
        with engine.connect() as conn:
            inspector = inspect(engine)
            existing_cols = [c['name'] for c in inspector.get_columns("users")]
            
            required = {
                "is_admin": "BOOLEAN DEFAULT FALSE",
                "operator_flex": "BOOLEAN DEFAULT FALSE",
                "tradingview_id": "VARCHAR",
                "username": "VARCHAR",
                "first_name": "VARCHAR",  # Added for registration
                "last_name": "VARCHAR",   # Added for registration
                "session_tz": "VARCHAR DEFAULT 'America/New_York'"
            }

            for col, dtype in required.items():
                if col not in existing_cols:
                    print(f">>> ADDING COLUMN: {col}")
                    try:
                        conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {dtype}"))
                        conn.commit()
                    except Exception as e:
                        print(f"Failed to add {col}: {e}")
            print(">>> DATABASE INTEGRITY: VERIFIED.")
    except Exception as e:
        print(f">>> DB WARNING: {str(e)}")

@app.get("/health")
def health():
    return {"ok": True}

# ==========================================
# PUBLIC PAGES
# ==========================================
@app.get("/", response_class=HTMLResponse)
def home_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "home.html", {"request": request, **ctx})

@app.get("/pricing", response_class=HTMLResponse)
def pricing_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "pricing.html", {"request": request, **ctx})

@app.get("/how-it-works", response_class=HTMLResponse)
def how_it_works_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "how_it_works.html", {"request": request, **ctx})

@app.get("/analysis", response_class=HTMLResponse)
def analysis_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "analysis.html", {"request": request, **ctx})

@app.get("/indicators", response_class=HTMLResponse)
def indicators_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "indicators.html", {"request": request, **ctx})

@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "about.html", {"request": request, **ctx})

@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "privacy.html", {"request": request, **ctx})

# --- AUTH UI ---
@app.get("/login", response_class=HTMLResponse)
def login_page_ui(request: Request):
    return _template_or_fallback(request, templates, "login.html", {"request": request})

@app.get("/register", response_class=HTMLResponse)
def register_page_ui(request: Request):
    return _template_or_fallback(request, templates, "register.html", {"request": request})

# ==========================================
# MEMBER SUITE (HIERARCHY ENFORCED)
# ==========================================

# 1. SESSION CONTROL (FREE ACCESS)
@app.get("/suite", response_class=HTMLResponse)
def suite_home(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    if not user_id: return RedirectResponse("/login") 

    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    is_admin = getattr(user, "is_admin", False) if user else False

    return _template_or_fallback(
        request, templates, "session_control.html",
        {"request": request, "user": user, "is_logged_in": True, "is_admin": is_admin}
    )

# 2. BATTLE CONTROL (FREE ACCESS)
@app.get("/suite/battle-control", response_class=HTMLResponse)
def battle_control_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    if not user_id: return RedirectResponse("/login")
    
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    is_admin = getattr(user, "is_admin", False) if user else False
    
    return _template_or_fallback(request, templates, "battle_control.html", 
                                 {"request": request, "user": user, "is_admin": is_admin})

# 3. PROJECT OMEGA (GOD MODE / ADMIN ONLY)
@app.get("/suite/omega", response_class=HTMLResponse)
def omega_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    
    # GATEKEEPER
    if not user or not user.is_admin:
        return RedirectResponse("/suite")

    return _template_or_fallback(request, templates, "project_omega.html", {"request": request})

# 4. RESEARCH LAB (GOD MODE / ADMIN ONLY)
@app.get("/suite/research-lab", response_class=HTMLResponse)
def research_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    
    # GATEKEEPER
    if not user or not user.is_admin:
        return RedirectResponse("/suite")

    return _template_or_fallback(request, templates, "research_lab.html", {"request": request})

# 5. ACCOUNT PAGE (FREE)
@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == user_id).first() if user_id else None
    is_admin = getattr(user, "is_admin", False) if user else False

    return _template_or_fallback(request, templates, "account.html", 
                                 {"request": request, "user": user, "is_logged_in": True, "tier_label": "Early Access", "is_admin": is_admin})

# 6. ADMIN PANEL (GOD MODE ONLY)
@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    
    if not user or not user.is_admin:
        return RedirectResponse("/account")

    users = db.query(UserModel).all()
    return _template_or_fallback(request, templates, "admin.html", {"request": request, "users": users})

# ==========================================
# SYSTEM MONITOR & ADMIN API
# ==========================================

@app.get("/suite/system-monitor", response_class=HTMLResponse)
def system_monitor_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if not user or not user.is_admin:
        return RedirectResponse("/account")

    logs = db.query(SystemLog).order_by(SystemLog.timestamp.desc()).limit(50).all()
    now_utc = datetime.now(timezone.utc)
    
    return _template_or_fallback(
        request, templates, "system_dashboard.html", 
        {"request": request, "user": user, "logs": logs, "server_time": now_utc}
    )

@app.post("/api/system/log-clear")
async def clear_system_logs(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if user and user.is_admin:
        db.query(SystemLog).delete()
        db.commit()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False}, status_code=403)

@app.post("/admin/reset-password-manual")
async def admin_reset_password(request: Request, db: Session = Depends(get_db)):
    admin_id = request.session.get(auth.SESSION_KEY)
    admin = db.query(UserModel).filter(UserModel.id == admin_id).first()
    if not admin or not admin.is_admin:
        raise HTTPException(403, detail="Unauthorized")

    try:
        payload = await request.json()
        target_id = payload.get("user_id")
        new_pass = payload.get("new_password")
        
        target_user = db.query(UserModel).filter(UserModel.id == target_id).first()
        if target_user:
            target_user.password_hash = auth.hash_password(new_pass)
            db.commit()
            return JSONResponse({"ok": True})
        else:
            return JSONResponse({"ok": False, "error": "User not found"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

@app.post("/admin/delete-user")
async def delete_user(request: Request, user_id: int = Form(...), db: Session = Depends(get_db)):
    admin_id = request.session.get(auth.SESSION_KEY)
    admin = db.query(UserModel).filter(UserModel.id == admin_id).first()
    if not admin or not admin.is_admin:
        raise HTTPException(403)
    target = db.query(UserModel).filter(UserModel.id == user_id).first()
    if target:
        db.delete(target)
        db.commit()
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/toggle-role")
async def toggle_user_role(request: Request, db: Session = Depends(get_db)):
    # 1. Security Check (Only Admins can do this)
    admin_id = request.session.get(auth.SESSION_KEY)
    admin = db.query(UserModel).filter(UserModel.id == admin_id).first()
    if not admin or not admin.is_admin:
        raise HTTPException(403)

    try:
        # 2. Get Target User
        payload = await request.json()
        target_id = payload.get("user_id")
        target = db.query(UserModel).filter(UserModel.id == target_id).first()
        
        if target:
            # 3. Flip the Switch
            # Prevent admin from demoting themselves to avoid lockout
            if target.id == admin.id:
                return JSONResponse({"ok": False, "error": "Cannot demote self."})
                
            target.is_admin = not target.is_admin
            db.commit()
            return JSONResponse({"ok": True, "new_status": target.is_admin})
            
        return JSONResponse({"ok": False, "error": "User not found"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})
    
# ==========================================
# OMEGA SIMULATION & API
# ==========================================
@app.post("/api/omega/simulate")
async def omega_simulation_api(request: Request):
    """
    The Stress Test Endpoint (Simulation Mode)
    """
    try:
        payload = await request.json()
        sim_time = payload.get("time")  # "09:30"
        sim_price = float(payload.get("price")) if payload.get("price") else None
        
        # Calls Omega with overrides
        data = await project_omega.get_omega_status(
            symbol="BTCUSDT",
            session_id="us_ny_futures",
            force_time_utc=sim_time,
            force_price=sim_price
        )
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

@app.post("/api/omega/status")
async def omega_status_api(request: Request, db: Session = Depends(get_db)):
    try: payload = await request.json()
    except: payload = {}
    
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    session_id = payload.get("session_id") or "us_ny_futures" 
    ferrari_mode = bool(payload.get("ferrari_mode", False))
    
    # Captures simulation params if sent from front-end
    force_time = payload.get("force_time_utc")
    force_price = payload.get("force_price")

    data = await project_omega.get_omega_status(
        symbol=symbol,
        session_id=session_id, 
        ferrari_mode=ferrari_mode,
        force_time_utc=force_time,
        force_price=force_price
    )
    return JSONResponse(data)

# ==========================================
# OTHER API ROUTES
# ==========================================
@app.post("/account/profile")
async def update_profile(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    if not user_id: raise HTTPException(401)
    
    try:
        payload = await request.json()
        user = db.query(UserModel).filter(UserModel.id == user_id).first()
        if user:
            if "username" in payload: user.username = payload["username"]
            if "tradingview_id" in payload: user.tradingview_id = payload["tradingview_id"]
            if "session_tz" in payload: user.session_tz = payload["session_tz"]
            db.commit()
            return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": False}, status_code=400)

@app.post("/account/password")
async def update_password(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    if not user_id: raise HTTPException(401)
    payload = await request.json()
    new_pass = payload.get("password")
    if new_pass:
        user = db.query(UserModel).filter(UserModel.id == user_id).first()
        if user:
            user.password_hash = auth.hash_password(new_pass)
            db.commit()
            return JSONResponse({"ok": True})
    return JSONResponse({"ok": False}, status_code=400)

@app.post("/api/dmr/run-raw")
async def run_dmr_raw(request: Request):
    try: payload = await request.json()
    except: payload = {}
    data = await battlebox_pipeline.get_session_review(
        symbol=payload.get("symbol", "BTCUSDT"),
        session_id=payload.get("session_id", "us_ny_futures")
    )
    return JSONResponse(data)

@app.post("/api/dmr/live")
async def run_dmr_live(request: Request):
    try: payload = await request.json()
    except: payload = {}
    manual_id = payload.get("session_id")
    data = await battlebox_pipeline.get_live_battlebox(
        symbol=payload.get("symbol", "BTCUSDT"),
        session_mode="MANUAL" if manual_id else "AUTO",
        manual_id=manual_id
    )
    return JSONResponse(data)

@app.post("/api/research/run")
async def run_res(request: Request):
    try: pl = await request.json()
    except: pl = {}
    
    try:
        s_ts = int(datetime.strptime(pl.get("start_date_utc", "2026-01-01"), "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) - 86400
        e_ts = int(datetime.strptime(pl.get("end_date_utc", "2026-01-10"), "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 86400
    except: return JSONResponse({"ok": False, "error": "Bad Date"})
    
    # 1. Fetch Step 1 Data
    raw = await battlebox_pipeline.fetch_historical_pagination(pl.get("symbol", "BTCUSDT"), s_ts, e_ts)
    
    # 2. Run Hybrid Engine (Passing everything: Tuning + Sensors)
    data = await research_lab.run_hybrid_analysis(
        symbol=pl.get("symbol", "BTCUSDT"),
        raw_5m=raw,
        start_date=pl.get("start_date_utc"),
        end_date=pl.get("end_date_utc"),
        session_ids=pl.get("session_ids", ["us_ny_futures"]),
        tuning=pl.get("tuning", {}),
        sensors=pl.get("sensors", {}),
        min_score=pl.get("min_score", 70)
    )
    return JSONResponse(data)