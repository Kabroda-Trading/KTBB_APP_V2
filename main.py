# main.py
# ==============================================================================
# KABRODA UNIVERSAL SWITCHBOARD (MAIN DISPATCHER)
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
import market_radar # <--- NEW RADAR ENGINE

# --- HELPERS ---
def _template_or_fallback(request: Request, templates: Jinja2Templates, name: str, context: Dict[str, Any]):
    try: return templates.TemplateResponse(name, context)
    except Exception as e: return HTMLResponse(f"<h2>System Error</h2><p>{str(e)}</p>", status_code=500)

def get_user_context(request: Request, db: Session):
    user_id = request.session.get(auth.SESSION_KEY)
    if not user_id: return {"is_logged_in": False, "is_admin": False, "user": None}
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if not user: return {"is_logged_in": False, "is_admin": False, "user": None}
    return {"is_logged_in": True, "is_admin": getattr(user, "is_admin", False), "user": user}

app = FastAPI()
SECRET_KEY = os.getenv("SECRET_KEY") or "dev-secret-change-me"
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates") 
app.include_router(auth.router)

@app.on_event("startup")
def on_startup():
    init_db()
    try:
        with engine.connect() as conn:
            inspector = inspect(engine)
            cols = [c['name'] for c in inspector.get_columns("users")]
            req = {"is_admin":"BOOLEAN DEFAULT FALSE", "operator_flex":"BOOLEAN DEFAULT FALSE",
                   "tradingview_id":"VARCHAR", "username":"VARCHAR", "first_name":"VARCHAR", 
                   "last_name":"VARCHAR", "session_tz":"VARCHAR DEFAULT 'America/New_York'"}
            for c, t in req.items():
                if c not in cols:
                    try: conn.execute(text(f"ALTER TABLE users ADD COLUMN {c} {t}")); conn.commit()
                    except: pass
    except: pass

@app.get("/health")
def health(): return {"ok": True}

# PUBLIC ROUTES
@app.get("/", response_class=HTMLResponse)
def home_page(request: Request, db: Session = Depends(get_db)):
    return _template_or_fallback(request, templates, "home.html", {"request": request, **get_user_context(request, db)})

@app.get("/pricing", response_class=HTMLResponse)
def pricing_page(request: Request, db: Session = Depends(get_db)):
    return _template_or_fallback(request, templates, "pricing.html", {"request": request, **get_user_context(request, db)})

@app.get("/how-it-works", response_class=HTMLResponse)
def how_it_works_page(request: Request, db: Session = Depends(get_db)):
    return _template_or_fallback(request, templates, "how_it_works.html", {"request": request, **get_user_context(request, db)})

@app.get("/analysis", response_class=HTMLResponse)
def analysis_page(request: Request, db: Session = Depends(get_db)):
    return _template_or_fallback(request, templates, "analysis.html", {"request": request, **get_user_context(request, db)})

@app.get("/indicators", response_class=HTMLResponse)
def indicators_page(request: Request, db: Session = Depends(get_db)):
    return _template_or_fallback(request, templates, "indicators.html", {"request": request, **get_user_context(request, db)})

@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request, db: Session = Depends(get_db)):
    return _template_or_fallback(request, templates, "about.html", {"request": request, **get_user_context(request, db)})

@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request, db: Session = Depends(get_db)):
    return _template_or_fallback(request, templates, "privacy.html", {"request": request, **get_user_context(request, db)})

# AUTH
@app.get("/login", response_class=HTMLResponse)
def login_page_ui(request: Request): return _template_or_fallback(request, templates, "login.html", {"request": request})

@app.get("/register", response_class=HTMLResponse)
def register_page_ui(request: Request): return _template_or_fallback(request, templates, "register.html", {"request": request})

# MEMBER SUITE
@app.get("/suite", response_class=HTMLResponse)
def suite_home(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse("/login")
    return _template_or_fallback(request, templates, "session_control.html", {"request": request, **ctx})

@app.get("/suite/battle-control", response_class=HTMLResponse)
def battle_control_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse("/login")
    return _template_or_fallback(request, templates, "battle_control.html", {"request": request, **ctx})

@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse("/login")
    return _template_or_fallback(request, templates, "account.html", {"request": request, **ctx, "tier_label": "Active"})

# GOD MODE PAGES
@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/account")
    users = db.query(UserModel).all()
    return _template_or_fallback(request, templates, "admin.html", {"request": request, "users": users, **ctx})

@app.get("/suite/omega", response_class=HTMLResponse)
def omega_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    return _template_or_fallback(request, templates, "project_omega.html", {"request": request})

@app.get("/suite/research-lab", response_class=HTMLResponse)
def research_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    return _template_or_fallback(request, templates, "research_lab.html", {"request": request})

@app.get("/suite/system-monitor", response_class=HTMLResponse)
def system_monitor_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/account")
    logs = db.query(SystemLog).order_by(SystemLog.timestamp.desc()).limit(50).all()
    return _template_or_fallback(request, templates, "system_dashboard.html", {"request": request, "logs": logs, "server_time": datetime.now(timezone.utc), **ctx})

# --- NEW: MARKET RADAR PAGE ---
@app.get("/suite/market-radar", response_class=HTMLResponse)
def market_radar_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    return _template_or_fallback(request, templates, "market_radar.html", {"request": request})

# ADMIN ACTIONS (FIXED INTEGER CASTING)
@app.post("/admin/toggle-role")
async def toggle_role(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    admin = db.query(UserModel).filter(UserModel.id == uid).first()
    if not admin or not admin.is_admin: raise HTTPException(403)
    try:
        pl = await request.json()
        target_id = int(pl.get("user_id")) # Force Int
        target = db.query(UserModel).filter(UserModel.id == target_id).first()
        if target:
            if target.id == admin.id: return JSONResponse({"ok": False, "error": "Cannot demote self."})
            target.is_admin = not target.is_admin
            db.commit()
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "error": "User not found"})
    except Exception as e: return JSONResponse({"ok": False, "error": str(e)})

@app.post("/admin/reset-password-manual")
async def reset_pass(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    admin = db.query(UserModel).filter(UserModel.id == uid).first()
    if not admin or not admin.is_admin: raise HTTPException(403)
    try:
        pl = await request.json()
        target_id = int(pl.get("user_id")) # Force Int
        target = db.query(UserModel).filter(UserModel.id == target_id).first()
        if target:
            target.password_hash = auth.hash_password(pl["new_password"])
            db.commit()
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "error": "User not found"})
    except Exception as e: return JSONResponse({"ok": False, "error": str(e)})

@app.post("/admin/delete-user")
async def delete_user(request: Request, user_id: int = Form(...), db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    admin = db.query(UserModel).filter(UserModel.id == uid).first()
    if not admin or not admin.is_admin: raise HTTPException(403)
    db.query(UserModel).filter(UserModel.id == user_id).delete()
    db.commit()
    return RedirectResponse("/admin", status_code=303)

# API ENDPOINTS
@app.post("/api/omega/status")
async def omega_api(request: Request):
    try: pl = await request.json()
    except: pl = {}
    return JSONResponse(await project_omega.get_omega_status(pl.get("symbol", "BTCUSDT"), "us_ny_futures", force_time_utc=pl.get("force_time_utc"), force_price=pl.get("force_price")))

@app.post("/api/omega/simulate")
async def omega_sim(request: Request):
    try: pl = await request.json()
    except: pl = {}
    return JSONResponse(await project_omega.get_omega_status("BTCUSDT", "us_ny_futures", force_time_utc=pl.get("time"), force_price=float(pl.get("price")) if pl.get("price") else None))

@app.post("/api/dmr/run-raw")
async def dmr_api(request: Request):
    try: pl = await request.json()
    except: pl = {}
    return JSONResponse(await battlebox_pipeline.get_session_review(pl.get("symbol", "BTCUSDT"), "us_ny_futures"))

@app.post("/api/dmr/live")
async def live_api(request: Request):
    try: pl = await request.json()
    except: pl = {}
    return JSONResponse(await battlebox_pipeline.get_live_battlebox(pl.get("symbol", "BTCUSDT"), "MANUAL" if pl.get("session_id") else "AUTO", manual_id=pl.get("session_id")))

@app.post("/api/research/run")
async def res_api(request: Request):
    try: pl = await request.json()
    except: pl = {}
    try:
        s_ts = int(datetime.strptime(pl.get("start_date_utc", "2026-01-01"), "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) - 86400
        e_ts = int(datetime.strptime(pl.get("end_date_utc", "2026-01-10"), "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 86400
    except: return JSONResponse({"ok": False, "error": "Bad Date"})
    raw = await battlebox_pipeline.fetch_historical_pagination(pl.get("symbol", "BTCUSDT"), s_ts, e_ts)
    return JSONResponse(await research_lab.run_hybrid_analysis(pl.get("symbol", "BTCUSDT"), raw, pl.get("start_date_utc"), pl.get("end_date_utc"), pl.get("session_ids", ["us_ny_futures"]), pl.get("tuning", {}), pl.get("sensors", {}), pl.get("min_score", 70)))

# --- NEW: RADAR API ---
@app.post("/api/radar/scan")
async def radar_scan_api(request: Request):
    # Only admins reach here via UI check, but extra check if needed
    data = await market_radar.scan_sector()
    return JSONResponse({"ok": True, "results": data})

@app.post("/api/system/log-clear")
async def clear_logs(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    if user and user.is_admin:
        db.query(SystemLog).delete()
        db.commit()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False}, status_code=403)

@app.post("/account/profile")
async def update_profile(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(401)
    try:
        pl = await request.json()
        u = db.query(UserModel).filter(UserModel.id == uid).first()
        if u:
            if "username" in pl: u.username = pl["username"]
            if "tradingview_id" in pl: u.tradingview_id = pl["tradingview_id"]
            if "session_tz" in pl: u.session_tz = pl["session_tz"]
            db.commit()
            return JSONResponse({"ok": True})
    except Exception as e: return JSONResponse({"ok": False, "error": str(e)})
    return JSONResponse({"ok": False})

@app.post("/account/password")
async def update_pw(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(401)
    pl = await request.json()
    u = db.query(UserModel).filter(UserModel.id == uid).first()
    if u and pl.get("password"):
        u.password_hash = auth.hash_password(pl["password"])
        db.commit()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False})