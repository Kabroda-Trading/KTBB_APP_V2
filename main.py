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
from database import init_db, get_db, UserModel

# CORE MODULES
import project_omega
import battlebox_pipeline
import research_lab

# --- HELPERS ---
def _db_user_from_session(db: Session, user_id: int) -> UserModel:
    return db.query(UserModel).filter(UserModel.id == user_id).first()

def _template_or_fallback(request: Request, templates: Jinja2Templates, name: str, context: Dict[str, Any]):
    try:
        return templates.TemplateResponse(name, context)
    except Exception as e:
        return HTMLResponse(
            f"<h2>System Error</h2><p>Could not load {name}.<br>Error: {str(e)}</p>",
            status_code=500,
        )

# --- APP CONFIG ---
app = FastAPI()

SECRET_KEY = os.getenv("SECRET_KEY") or "dev-secret-change-me"
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=True)

# --- FOLDER CONFIG (CORRECT) ---
# "static" and "templates" (plural) match your screenshots exactly.
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates") 

app.include_router(auth.router)

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/health")
def health():
    return {"ok": True}

# --- PAGE ROUTES ---
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/suite", status_code=302)

@app.get("/suite", response_class=HTMLResponse, include_in_schema=False)
def suite_home(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == user_id).first() if user_id else None
    
    # Fallback user for display if not logged in (Bypasses Login Screen)
    if not user:
        user = UserModel(email="guest@kabroda.com", username="GUEST_COMMAND", is_admin=False)

    # --- THE FIX IS HERE ---
    # Changed "suite_index.html" to "session_control.html"
    return _template_or_fallback(
        request, templates, "session_control.html",
        {
            "request": request, 
            "title": "Session Control",
            "user": user,
            "is_logged_in": True, 
            "is_admin": True
        },
    )

@app.get("/suite/battle-control", response_class=HTMLResponse, include_in_schema=False)
def battle_control_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(auth.SESSION_KEY)
    user = db.query(UserModel).filter(UserModel.id == user_id).first() if user_id else None
    
    if not user:
        user = UserModel(email="guest@kabroda.com", username="GUEST_COMMAND", operator_flex=True)

    return _template_or_fallback(request, templates, "battle_control.html", {"request": request, "user": user})

@app.get("/suite/black-ops", response_class=HTMLResponse, include_in_schema=False)
def omega_page(request: Request):
    return _template_or_fallback(request, templates, "project_omega.html", {"request": request})

@app.get("/suite/omega", response_class=HTMLResponse, include_in_schema=False)
def omega_page_alias(request: Request):
    return _template_or_fallback(request, templates, "project_omega.html", {"request": request})

@app.get("/suite/research-lab", response_class=HTMLResponse, include_in_schema=False)
def research_page(request: Request):
    return _template_or_fallback(request, templates, "research_lab.html", {"request": request})

# --- API: OMEGA (AUTH DISABLED) ---
@app.post("/api/omega/status")
async def omega_status_api(request: Request, db: Session = Depends(get_db)):
    try:
        payload = await request.json()
    except:
        payload = {}
        
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    
    data = await project_omega.get_omega_status(
        symbol=symbol,
        session_id="us_ny_futures",
        ferrari_mode=False
    )
    return JSONResponse(data)

@app.post("/api/black-ops/status")
async def legacy_black_ops_status_alias(request: Request, db: Session = Depends(get_db)):
    return await omega_status_api(request, db)

# --- API: SESSION CONTROL ---
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

# --- API: RESEARCH LAB ---
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
        symbol=symbol,
        raw_5m=raw_5m,
        start_date_utc=start_date,
        end_date_utc=end_date,
        session_ids=session_ids,
        tuning=tuning
    )
    return JSONResponse(data)