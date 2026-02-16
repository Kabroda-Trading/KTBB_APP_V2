# main.py
import os
from typing import Any, Dict
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates
from sqlalchemy.orm import Session

import auth
import billing
from database import init_db, get_db, UserModel
import battlebox_pipeline
import market_radar
import research_lab

app = FastAPI(title="Kabroda BattleBox", version="10.2")

SECRET_KEY = os.getenv("SESSION_SECRET", "kabroda_prod_key_999")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=86400*30)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Register the standalone router logic
app.include_router(auth.router)
app.include_router(billing.router, prefix="/billing")

@app.on_event("startup")
def on_startup():
    init_db()

def _template_or_fallback(request: Request, templates: Jinja2Templates, name: str, context: Dict[str, Any]):
    try: 
        return templates.TemplateResponse(name, context)
    except Exception as e: 
        return HTMLResponse(f"<h2>System Error: {name}</h2><p>{str(e)}</p>", status_code=500)

def get_user_context(request: Request, db: Session):
    uid = request.session.get(auth.SESSION_KEY)
    
    # CRITICAL FIX: The Request object is now strictly injected into the base context
    base_context = {"request": request}
    
    if not uid: 
        base_context.update({"is_logged_in": False, "is_admin": False})
        return base_context
        
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    
    base_context.update({
        "is_logged_in": True,
        "is_admin": user.is_admin if user else False,
        "username": user.username if user else "Operative",
        "email": user.email if user else "",
        "sub_status": user.subscription_status if user else "inactive",
        "user": user
    })
    return base_context

# --- PUBLIC ROUTES ---
@app.get("/")
async def homepage(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "home.html", ctx)

@app.get("/how-it-works")
async def how_it_works(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "how_it_works.html", ctx)

@app.get("/analysis")
async def analysis_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "analysis.html", ctx)

@app.get("/pricing")
async def pricing_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "pricing.html", ctx)

@app.get("/indicators")
async def indicators_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    return _template_or_fallback(request, templates, "indicators.html", ctx)

# --- PROTECTED SUITE ROUTES ---
@app.get("/suite")
async def suite_home(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse("/login")
    return _template_or_fallback(request, templates, "session_control.html", ctx)

@app.get("/suite/battle-control")
async def battle_control(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse("/login")
    return _template_or_fallback(request, templates, "suite_home.html", ctx)

@app.get("/account")
async def account_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse("/login")
    return _template_or_fallback(request, templates, "account.html", ctx)

# --- USER PROFILE & ACCOUNT API ---
@app.post("/account/profile")
async def update_profile(request: Request, payload: Dict[str, Any], db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    if user:
        user.username = payload.get("username", user.username)
        user.tradingview_id = payload.get("tradingview_id", user.tradingview_id)
        db.commit()
    return {"ok": True}

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
async def research_lab_page(request: Request, db: Session = Depends(get_db)):
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

# --- SYSTEM & PIPELINE API ENDPOINTS ---
@app.post("/api/dmr/live")
async def get_live_dmr(request: Request, payload: Dict[str, Any]):
    symbol = payload.get("symbol", "BTCUSDT")
    session_id = payload.get("session_id", "us_ny_futures")
    data = await battlebox_pipeline.get_live_battlebox(symbol=symbol, session_mode="MANUAL", manual_id=session_id)
    return data

@app.get("/api/radar/scan")
async def run_radar_scan(request: Request):
    results = await market_radar.scan_market()
    return {"ok": True, "results": results}