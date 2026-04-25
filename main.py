# main.py
# ---------------------------------------------------------
# KABRODA UNIFIED SERVER: BATTLEBOX v10.3 (RADAR PROMOTION)
# ---------------------------------------------------------
import os
import traceback
from typing import Any, Dict, Optional
import asyncio
from contextlib import asynccontextmanager 

from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates
from sqlalchemy.orm import Session

# --- CORE IMPORTS ---
import auth
import billing
import battlebox_pipeline
import market_radar
import research_lab
import market_simulator  
import gravity_engine  
import gravity_math    

from database import init_db, get_db, UserModel
from membership import get_membership_state, require_paid_access, ensure_symbol_allowed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(">>> BOOTING KABRODA SYSTEM: Initializing Database Schema...")
    init_db()
    gravity_task = asyncio.create_task(gravity_engine.run_gravity_ingestion_loop())
    yield
    print(">>> SHUTTING DOWN KABRODA SYSTEM...")
    gravity_task.cancel()

app = FastAPI(title="Kabroda BattleBox", version="10.3", lifespan=lifespan)

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
app.include_router(billing.router, prefix="/billing")

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
        "sub_status": getattr(user, "subscription_status", "inactive") if user else "inactive",
        "user": user
    })
    
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

@app.get("/suite/radar")
async def radar_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    require_paid_access(ctx["user"])
    return _template_or_fallback(request, templates, "market_radar.html", ctx)

@app.get("/suite/gravity-map")
async def gravity_map_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    require_paid_access(ctx["user"])
    return _template_or_fallback(request, templates, "gravity_map.html", ctx)

# --- UPDATED: Automated Fib Execution & Single Source Routing ---
@app.get("/api/gravity/scan")
async def api_gravity_scan(symbol: str = "BTC/USDT"):
    # 1. Fetch from the Single Source of Truth
    candles_1d = await battlebox_pipeline.fetch_live_daily(symbol, limit=30)
    candles_15m = await battlebox_pipeline.fetch_live_15m(symbol, limit=300)
    
    # 2. Compute logic without triggering external API calls
    heatmap = gravity_math.calculate_gravity_heatmap(symbol)
    macro_fibs = gravity_math.calculate_macro_fibs(candles_1d, candles_15m)
    
    return JSONResponse({"ok": True, "symbol": symbol, "heatmap": heatmap, "macro_fibs": macro_fibs})

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
    ctx["tier_label"] = ctx.get("plan_label", "")
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

@app.post("/admin/delete-user")
async def admin_delete_user(request: Request, user_id: str = Form(...), db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"): return RedirectResponse("/suite")
    user_to_delete = db.query(UserModel).filter(UserModel.id == int(user_id)).first()
    if user_to_delete:
        db.delete(user_to_delete)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)

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

@app.post("/api/dmr/run-raw")
async def dmr_run_raw(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    require_paid_access(user)
    
    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
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
        return JSONResponse({"ok": False, "error": "Admin access required."}, status_code=403)
    
    payload = await request.json()
    try:
        out = await market_simulator.run_simulation(payload)
        return JSONResponse(out)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(e)})
    
@app.get("/processing")
async def processing_route(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "processing.html", ctx)

from fastapi.responses import HTMLResponse
import traceback

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_trace = traceback.format_exc()
    print(f"CRITICAL CRASH:\n{error_trace}")
    return HTMLResponse(
        content=f"""
        <div style="background-color: #0f172a; color: #ef4444; padding: 40px; font-family: monospace;">
            <h1>🚨 FATAL SYSTEM CRASH 🚨</h1>
            <pre>{error_trace}</pre>
        </div>
        """,
        status_code=500
    )