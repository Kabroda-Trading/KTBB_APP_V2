# main.py
from fastapi import FastAPI, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import List
import uvicorn
import os
import asyncio

# INTERNAL MODULES
from database import get_db, init_db, UserModel
import data_feed
import dmr_report
import wealth_os
import research_lab  # <--- ENSURE THIS IS IMPORTED

app = FastAPI()

# 1. MOUNT STATIC FILES & TEMPLATES
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# 2. INIT DB
@app.on_event("startup")
def on_startup():
    init_db()

# ---------------------------------------------------------
# AUTH & USER HELPER FUNCTIONS
# ---------------------------------------------------------
def _db_user_from_session(db: Session, request_session: dict):
    if not request_session or "user_id" not in request_session:
        return None
    return db.query(UserModel).filter(UserModel.id == request_session["user_id"]).first()

def _require_session_user(request: Request):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return request.session

def require_paid_access(u: UserModel):
    if not u: raise HTTPException(status_code=401)
    if u.tier not in ["tier1_manual", "tier2_stripe"]:
        # In a real app, you might redirect to billing
        pass 
    return True

def _plan_flags(u: UserModel):
    return {
        "is_paid": u.tier in ["tier1_manual", "tier2_stripe"],
        "plan_label": "PRO MEMBER" if u.tier != "free" else "FREE ACCOUNT"
    }

# ---------------------------------------------------------
# HTML ROUTES (Frontend)
# ---------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    u = _db_user_from_session(db, request.session)
    if u:
        return RedirectResponse("/suite", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    flags = _plan_flags(u)
    return templates.TemplateResponse("suite.html", {
        "request": request, 
        "is_logged_in": True,
        "user": {"email": u.email, "username": u.username, "plan_label": flags.get("plan_label", "")}
    })

@app.get("/wealth", response_class=HTMLResponse)
def wealth_page(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    flags = _plan_flags(u)
    return templates.TemplateResponse("wealth.html", {
        "request": request, "is_logged_in": True,
        "user": {"email": u.email, "username": u.username, "plan_label": flags.get("plan_label", "")}
    })

@app.get("/indicators", response_class=HTMLResponse)
def indicators_page(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    flags = _plan_flags(u)
    return templates.TemplateResponse("indicators.html", {
        "request": request, "is_logged_in": True,
        "user": {"email": u.email, "username": u.username, "plan_label": flags.get("plan_label", "")}
    })

@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    flags = _plan_flags(u)
    return templates.TemplateResponse("account.html", {
        "request": request, "is_logged_in": True,
        "user": {"email": u.email, "username": u.username, "plan_label": flags.get("plan_label", "")}
    })

# --- RESEARCH LAB PAGE ---
@app.get("/research", response_class=HTMLResponse)
def research_page(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    flags = _plan_flags(u)
    return templates.TemplateResponse("research.html", {
        "request": request, "is_logged_in": True,
        "user": {"email": u.email, "username": u.username, "plan_label": flags.get("plan_label", "")}
    })

# ---------------------------------------------------------
# AUTH ROUTES
# ---------------------------------------------------------
@app.post("/login")
async def login_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    email = form.get("email")
    password = form.get("password")
    
    # Simple hardcoded check for MVP or DB check
    user = db.query(UserModel).filter(UserModel.email == email).first()
    if not user:
        # Auto-create for demo if you want, OR reject
        # For now, let's reject if not found, or auto-create test user
        if email == "demo@kabroda.com" and password == "future":
            # ensure demo user exists
            return RedirectResponse("/suite", status_code=303)
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})
    
    # In real app: verify_password(password, user.password_hash)
    # Here we assume simple pass for demo or verify hash
    if user.password_hash == password: # Plaintext for MVP, hash in prod
        request.session["user_id"] = user.id
        return RedirectResponse("/suite", status_code=303)
    
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)

# ---------------------------------------------------------
# API ROUTES (THE ENGINE)
# ---------------------------------------------------------
@app.post("/api/dmr/run-raw")
async def run_dmr_raw(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)

    payload = await request.json()
    symbol = payload.get("symbol", "BTCUSDT")
    session_tz = payload.get("session_tz", "America/New_York_Early") # Default
    
    # 1. Fetch Data
    inputs = await data_feed.get_inputs(symbol, session_tz=session_tz)
    
    # 2. Run Report Logic
    report = dmr_report.generate_report_from_inputs(inputs, session_tz)
    
    return JSONResponse(report)

@app.post("/api/wealth/run")
async def run_wealth_os(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)

    payload = await request.json()
    symbol = payload.get("symbol", "BTCUSDT")
    
    # 1. Fetch
    inputs = await data_feed.get_investing_inputs(symbol)
    
    # 2. Run Brain
    report = wealth_os.run_wealth_brain(inputs)
    
    return JSONResponse(report)

# --- RESEARCH LAB API (FIXED) ---
@app.post("/api/dmr/history")
async def dmr_history(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)
    
    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    session_list = payload.get("sessions") or ["America/New_York"]
    leverage = float(payload.get("leverage") or 1.0)
    capital = float(payload.get("capital") or 1000.0)
    
    # 1. Fetch Data
    inputs = await data_feed.get_inputs(symbol=symbol)
    
    # 2. Run the Research Lab
    # FIX: We DIRECTLY await the function because it is now defined as 'async def'
    history = await research_lab.run_historical_analysis(
        inputs, session_list, leverage, capital
    )
    
    return JSONResponse({"history": history})