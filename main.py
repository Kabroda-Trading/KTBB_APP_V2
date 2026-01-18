# main.py
# ==============================================================================
# KABRODA UNIVERSAL SWITCHBOARD (MAIN DISPATCHER)
# ==============================================================================
# - Architecture: "Gateway Pattern"
# - Function: Receives 'session_id' from ANY page and routes to the correct Engine.
# - Updates: Added AI Analyst Integration for Research Lab.
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
import ai_analyst # <--- NEW MODULE

# --- HELPERS ---
def _template_or_fallback(request: Request, templates: Jinja2Templates, name: str, context: Dict[str, Any]):
    try:
        return templates.TemplateResponse(name, context)
    except Exception as e:
        return HTMLResponse(
            f"<h2>System Error</h2><p>Could not load {name}.<br>Error: {str(e)}</p>",
            status_code=500,
        )

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
def home_page(request: Request):
    user_id = request.session.get(auth.SESSION_KEY)
    return _template_or_fallback(request, templates, "home.html", {"request": request, "is_logged_in": user_id is not None})

@app.get("/pricing", response_class=HTMLResponse)
def pricing_page(request: Request):
    user_id = request.session.get(auth.SESSION_KEY)
    return _template_or_fallback(request, templates, "pricing.html", {"request": request, "is_logged_in": user_id is not None})

@app.get("/how-it-works", response_class=HTMLResponse)
def how_it_works_page(request: Request):
    user_id = request.session.get(auth.SESSION_KEY)
    return _template_or_fallback(request, templates, "how_it_works.html", {"request": request, "is_logged_in": user_id is not None})

@app.get("/analysis", response_class=HTMLResponse)
def analysis_page(request: Request):
    user_id = request.session.get(auth.SESSION_KEY)
    return _template_or_fallback(request, templates, "analysis.html", {"request": request, "is_logged_in": user_id is not None})

@app.get("/indicators", response_class=HTMLResponse)
def indicators_page(request: Request):
    user_id = request.session.get(auth.SESSION_KEY)
    return _template_or_fallback(request, templates, "indicators.html", {"request": request, "is_logged_in": user_id is not None})

@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    return _template_or_fallback(request, templates, "about.html", {"request": request})

@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return _template_or_fallback(request, templates, "privacy.html", {"request": request})

# --- AUTH UI ---
@app.get("/login", response_class=HTMLResponse)
def login_page_ui(request: Request):
    return _template_or_fallback(request, templates, "login.html", {"request": request})

@app.get("/register", response_class=HTMLResponse)
def register_page_ui(request: Request):
    return _template_or_fallback(request, templates, "register.html", {"request": request})

# ==========================================
# MEMBER SUITE
# ==========================================
@app.get("/suite", response_class=HTMLResponse)
def suite_home(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == user_id).first() if user_id else None
    
    is_admin = getattr(user, "is_admin", False) if user else False

    return _template_or_fallback(
        request, templates, "session_control.html",
        {"request": request, "title": "Session Control", "user": user, "is_logged_in": True, "is_admin": is_admin}
    )

@app.get("/suite/battle-control", response_class=HTMLResponse)
def battle_control_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == user_id).first() if user_id else None
    return _template_or_fallback(request, templates, "battle_control.html", {"request": request, "user": user})

@app.get("/suite/omega", response_class=HTMLResponse)
def omega_page(request: Request):
    return _template_or_fallback(request, templates, "project_omega.html", {"request": request})

@app.get("/suite/black-ops", include_in_schema=False)
def omega_page_alias(request: Request):
    return RedirectResponse("/suite/omega")

@app.get("/suite/research-lab", response_class=HTMLResponse)
def research_page(request: Request):
    return _template_or_fallback(request, templates, "research_lab.html", {"request": request})

@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == user_id).first() if user_id else None
    is_admin = getattr(user, "is_admin", False) if user else False

    return _template_or_fallback(request, templates, "account.html", 
                                 {"request": request, "user": user, "is_logged_in": True, "tier_label": "Active", "is_admin": is_admin})

@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    
    if not user or not user.is_admin:
        return RedirectResponse("/account")

    users = db.query(UserModel).all()
    return _template_or_fallback(request, templates, "admin.html", {"request": request, "users": users})

# ==========================================
# SYSTEM MONITOR (ADMIN DASHBOARD)
# ==========================================

@app.get("/suite/system-monitor", response_class=HTMLResponse)
def system_monitor_page(request: Request, db: Session = Depends(get_db)):
    # 1. Security Check
    user_id = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if not user or not user.is_admin:
        return RedirectResponse("/account")

    # 2. Fetch Logs (Last 50)
    logs = db.query(SystemLog).order_by(SystemLog.timestamp.desc()).limit(50).all()

    # 3. Drift Check Data
    now_utc = datetime.now(timezone.utc)
    
    # 4. Render
    return _template_or_fallback(
        request, templates, "system_dashboard.html", 
        {"request": request, "user": user, "logs": logs, "server_time": now_utc}
    )

@app.post("/api/system/log-clear")
async def clear_system_logs(request: Request, db: Session = Depends(get_db)):
    # Simple cleanup
    user_id = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if user and user.is_admin:
        db.query(SystemLog).delete()
        db.commit()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False}, status_code=403)

@app.post("/api/omega/simulate")
async def omega_simulation_api(request: Request):
    """
    The Stress Test Endpoint.
    Accepts fake time/price and returns what Omega WOULD do.
    """
    try:
        payload = await request.json()
        
        # Inputs
        sim_time = payload.get("time")  # "09:30"
        sim_price = float(payload.get("price")) if payload.get("price") else None
        
        # Run Omega with Overrides
        data = await project_omega.get_omega_status(
            symbol="BTCUSDT", # Default for test
            session_id="us_ny_futures",
            force_time_utc=sim_time,
            force_price=sim_price
        )
        return JSONResponse(data)
        
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

# ==========================================
# ACCOUNT ACTIONS (FIXED)
# ==========================================
@app.post("/account/profile")
async def update_profile(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    if not user_id: raise HTTPException(401)
    
    try:
        payload = await request.json()
        user = db.query(UserModel).filter(UserModel.id == user_id).first()
        
        if user:
            # FIX: Only update fields if they are actually sent
            if "username" in payload:
                user.username = payload["username"]
            
            if "tradingview_id" in payload:
                user.tradingview_id = payload["tradingview_id"]
                
            if "session_tz" in payload:
                user.session_tz = payload["session_tz"]

            db.commit()
            return JSONResponse({"ok": True})
            
    except Exception as e:
        print(f"PROFILE UPDATE ERROR: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    
    return JSONResponse({"ok": False}, status_code=400)

@app.post("/account/settings")
async def update_settings(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    if not user_id: raise HTTPException(401)
    payload = await request.json()
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if user:
        user.operator_flex = bool(payload.get("operator_flex", False))
        db.commit()
    return JSONResponse({"ok": True})

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

# ==============================================================================
# UNIVERSAL SWITCHBOARD ROUTES
# ==============================================================================

# 1. OMEGA SWITCHBOARD
@app.post("/api/omega/status")
async def omega_status_api(request: Request, db: Session = Depends(get_db)):
    try: payload = await request.json()
    except: payload = {}
    
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    session_id = payload.get("session_id") or "us_ny_futures" 
    ferrari_mode = bool(payload.get("ferrari_mode", False))
    
    data = await project_omega.get_omega_status(
        symbol=symbol,
        session_id=session_id, 
        ferrari_mode=ferrari_mode
    )
    return JSONResponse(data)

# 2. SESSION CONTROL SWITCHBOARD
@app.post("/api/dmr/run-raw")
async def run_dmr_raw(request: Request):
    try: payload = await request.json()
    except: payload = {}
    
    symbol = payload.get("symbol", "BTCUSDT")
    session_id = payload.get("session_id") or "us_ny_futures"

    data = await battlebox_pipeline.get_session_review(
        symbol=symbol,
        session_id=session_id 
    )
    return JSONResponse(data)

# 3. BATTLEBOX LIVE SWITCHBOARD
@app.post("/api/dmr/live")
async def run_dmr_live(request: Request):
    try: payload = await request.json()
    except: payload = {}
    
    symbol = payload.get("symbol", "BTCUSDT")
    manual_id = payload.get("session_id")
    session_mode = "MANUAL" if manual_id else "AUTO"

    data = await battlebox_pipeline.get_live_battlebox(
        symbol=symbol,
        session_mode=session_mode,
        manual_id=manual_id
    )
    return JSONResponse(data)

# 4. RESEARCH LAB SWITCHBOARD (Fixed Data Connection)
@app.post("/api/research/run")
async def run_research_api(request: Request):
    try: payload = await request.json()
    except: payload = {}
    
    # 1. Parse Parameters
    symbol = payload.get("symbol", "BTCUSDT")
    start_date = payload.get("start_date_utc", "2026-01-01")
    end_date = payload.get("end_date_utc", "2026-01-10")
    session_ids = payload.get("session_ids", ["us_ny_futures"])
    tuning = payload.get("tuning", {})
    
    # NEW: Simulation Inputs
    sim_settings = payload.get("simulation", {})
    use_ai = payload.get("use_ai", False)
    
    # SECURITY LOGIC: 
    # 1. Try to get key from the Frontend Input
    ai_key = payload.get("ai_key", "").strip()
    
    # 2. If Frontend input is empty, use the Render Environment Variable
    if not ai_key:
        ai_key = os.getenv("GEMINI_API_KEY", "")

    # 2. CONVERT DATES TO TIMESTAMPS
    try:
        dt_start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dt_end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        
        # Buffer: Add 24h before/after
        fetch_start_ts = int(dt_start.timestamp()) - 86400
        fetch_end_ts = int(dt_end.timestamp()) + 86400
        
    except ValueError:
        return JSONResponse({"ok": False, "error": "Invalid Date Format"})

    # 3. CALL THE CORPORATE PIPELINE (HISTORICAL)
    print(f">>> [SWITCHBOARD] Ordering Historical Data for: {symbol} ({start_date} to {end_date})")
    
    raw_5m = await battlebox_pipeline.fetch_historical_pagination(
        symbol=symbol,
        start_ts=fetch_start_ts,
        end_ts=fetch_end_ts
    )
    
    print(f">>> [SWITCHBOARD] Pipeline delivered {len(raw_5m)} candles.")

    # 4. HAND OFF TO THE LAB
    data = await research_lab.run_research_lab_from_candles(
        symbol=symbol,
        raw_5m=raw_5m,
        start_date_utc=start_date,
        end_date_utc=end_date,
        session_ids=session_ids,
        tuning=tuning,
        sim_settings=sim_settings # Pass it down
    )
    
    # 5. OPTIONAL: RUN AI ANALYST
    if use_ai and data.get("ok"):
        print(">>> [SWITCHBOARD] Running AI Analysis...")
        # We send a summarized version to save tokens/costs
        ai_payload = {
            "simulation": data["simulation"],
            "stats": data["stats"],
            "session_log": [
                {
                    "date": s["date"], 
                    "kinetic_score": s["kinetic"]["total_score"],
                    "protocol": s["kinetic"]["protocol"],
                    "trade_result": s["strategy"]["outcome"],
                    "pnl_r": s["strategy"]["r_realized"]
                }
                for s in data["sessions"]
            ]
        }
        report = ai_analyst.generate_report(ai_payload, ai_key)
        data["ai_report"] = report
    
    return JSONResponse(data)

# Legacy alias
@app.post("/api/black-ops/status")
async def legacy_black_ops_status_alias(request: Request, db: Session = Depends(get_db)):
    return await omega_status_api(request, db)