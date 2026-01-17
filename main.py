# main.py
import os
from typing import Any, Dict

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text, inspect

import auth
from database import init_db, get_db, UserModel, engine

# CORE MODULES
import project_omega
import battlebox_pipeline
import research_lab

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
    
    # --- SAFE MIGRATION (DATA SAVER) ---
    # This checks for missing columns and adds them WITHOUT deleting users.
    print(">>> CHECKING DATABASE HEALTH...")
    try:
        with engine.connect() as conn:
            # Get existing columns
            inspector = inspect(engine)
            existing_cols = [c['name'] for c in inspector.get_columns("users")]
            
            # 1. Fix 'is_admin' (Critical for login)
            if "is_admin" not in existing_cols:
                print(">>> MIGRATION: Adding missing 'is_admin' column...")
                conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE"))
                conn.commit()

            # 2. Fix 'operator_flex' (Critical for Battle Control)
            if "operator_flex" not in existing_cols:
                print(">>> MIGRATION: Adding missing 'operator_flex' column...")
                conn.execute(text("ALTER TABLE users ADD COLUMN operator_flex BOOLEAN DEFAULT FALSE"))
                conn.commit()

            # 3. Fix 'tradingview_id' (For Account page)
            if "tradingview_id" not in existing_cols:
                print(">>> MIGRATION: Adding missing 'tradingview_id' column...")
                conn.execute(text("ALTER TABLE users ADD COLUMN tradingview_id VARCHAR"))
                conn.commit()
                
            # 4. Fix 'username' (For Account page)
            if "username" not in existing_cols:
                print(">>> MIGRATION: Adding missing 'username' column...")
                conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR"))
                conn.commit()

            # 5. Fix 'session_tz'
            if "session_tz" not in existing_cols:
                print(">>> MIGRATION: Adding missing 'session_tz' column...")
                conn.execute(text("ALTER TABLE users ADD COLUMN session_tz VARCHAR DEFAULT 'America/New_York'"))
                conn.commit()

            print(">>> DATABASE INTEGRITY SECURED.")
            
    except Exception as e:
        print(f">>> MIGRATION WARNING: {str(e)}")
        # We continue even if this fails, as the DB might be locked or fine.

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
    
    # Debug fallback if needed
    is_admin = False
    if user:
        is_admin = getattr(user, "is_admin", False)

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
    return _template_or_fallback(request, templates, "account.html", {"request": request, "user": user, "is_logged_in": True, "tier_label": "Active"})

@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    users = db.query(UserModel).all()
    return _template_or_fallback(request, templates, "admin.html", {"request": request, "users": users})

# ==========================================
# API ENDPOINTS
# ==========================================
@app.post("/api/omega/status")
async def omega_status_api(request: Request, db: Session = Depends(get_db)):
    try: payload = await request.json()
    except: payload = {}
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    data = await project_omega.get_omega_status(symbol=symbol, session_id="us_ny_futures", ferrari_mode=False)
    return JSONResponse(data)

@app.post("/api/black-ops/status")
async def legacy_black_ops_status_alias(request: Request, db: Session = Depends(get_db)):
    return await omega_status_api(request, db)

@app.post("/api/dmr/run-raw")
async def run_dmr_raw(request: Request):
    payload = await request.json()
    symbol = payload.get("symbol", "BTCUSDT")
    data = await battlebox_pipeline.get_session_review(symbol)
    return JSONResponse(data)

@app.post("/api/dmr/live")
async def run_dmr_live(request: Request):
    payload = await request.json()
    symbol = payload.get("symbol", "BTCUSDT")
    data = await battlebox_pipeline.get_live_battlebox(symbol)
    return JSONResponse(data)

@app.post("/api/research/run")
async def run_research_api(request: Request):
    payload = await request.json()
    symbol = payload.get("symbol", "BTCUSDT")
    start_date = payload.get("start_date_utc", "2026-01-01")
    end_date = payload.get("end_date_utc", "2026-01-10")
    session_ids = payload.get("session_ids", ["us_ny_futures"])
    tuning = payload.get("tuning", {})
    raw_5m = await battlebox_pipeline.fetch_live_5m(symbol, limit=2000) 
    data = await research_lab.run_research_lab_from_candles(
        symbol=symbol, raw_5m=raw_5m, start_date_utc=start_date, end_date_utc=end_date, session_ids=session_ids, tuning=tuning
    )
    return JSONResponse(data)