# main.py
# Kabroda BattleBox Suite - FastAPI entrypoint
# Clean + consistent with auth.py exports (NO require_user import)

import os
import traceback
from typing import Any, Dict, Optional

from fastapi import Body, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import billing
import kabroda_ai
from auth import (
    COOKIE_NAME,
    authenticate_user,
    create_session_token,
    create_user,
    delete_session,
    get_current_user,
)
from data_feed import build_auto_inputs
from database import SessionLocal, UserModel, init_db
from dmr_report import compute_dmr, resolve_symbol
from membership import Tier, tier_marketing_label

# --------------------------
# App + config
# --------------------------
app = FastAPI()

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")
DISABLE_REGISTRATION = os.environ.get("DISABLE_REGISTRATION", "").lower() in ("1", "true", "yes")

# --------------------------
# DB dependency
# --------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --------------------------
# Helpers
# --------------------------
def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(url=path, status_code=302)

def _validate_tz(tz: str) -> str:
    tz = (tz or "").strip()
    if not tz:
        return "UTC"
    # Keep it permissive; just cap length / obvious garbage
    if len(tz) > 64:
        return "UTC"
    return tz

def ensure_can_use_gpt_chat(user: Any) -> None:
    tier_value = getattr(user.tier, "value", str(user.tier))
    if tier_value != Tier.TIER3_MULTI_GPT.value:
        raise HTTPException(status_code=403, detail="Elite required for AI Coach.")

# --------------------------
# Startup
# --------------------------
@app.on_event("startup")
def _startup():
    init_db()

# --------------------------
# Basic pages
# --------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request, "dev_mode": DEV_MODE})

@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request, "dev_mode": DEV_MODE})

@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    return templates.TemplateResponse("pricing.html", {"request": request, "dev_mode": DEV_MODE})

# --------------------------
# Auth pages
# --------------------------
@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login", response_class=HTMLResponse)
def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    u = authenticate_user(db, email, password)
    if not u:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid email or password."},
        )

    token = create_session_token(db, u.id)
    resp = _redirect("/suite")
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax")
    return resp

@app.get("/register", response_class=HTMLResponse)
def register_get(request: Request):
    if DISABLE_REGISTRATION and not DEV_MODE:
        return _redirect("/login")
    return templates.TemplateResponse("register.html", {"request": request, "error": None})

@app.post("/register", response_class=HTMLResponse)
def register_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if DISABLE_REGISTRATION and not DEV_MODE:
        return _redirect("/login")

    try:
        # Default new users to Tactical
        u = create_user(db, email, password, tier=Tier.TIER2_SINGLE_AUTO, session_tz="UTC")
    except HTTPException as e:
        return templates.TemplateResponse("register.html", {"request": request, "error": e.detail})

    token = create_session_token(db, u.id)
    resp = _redirect("/suite")
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax")
    return resp

@app.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        delete_session(db, token)
    resp = _redirect("/login")
    resp.delete_cookie(COOKIE_NAME)
    return resp

# --------------------------
# Suite + Account
# --------------------------
@app.get("/suite", response_class=HTMLResponse)
def suite_page(request: Request, user=Depends(get_current_user)):
    tier_value = getattr(user.tier, "value", str(user.tier))
    is_elite = tier_value == Tier.TIER3_MULTI_GPT.value
    tier_label = tier_marketing_label(user.tier)

    return templates.TemplateResponse(
        "app.html",
        {
            "request": request,
            "user": user,
            "tier_label": tier_label,
            "dev_mode": DEV_MODE,
            "tier_value": tier_value,
            "is_elite": is_elite,
        },
    )

@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, user=Depends(get_current_user)):
    tier_label = tier_marketing_label(user.tier)
    return templates.TemplateResponse(
        "account.html",
        {"request": request, "user": user, "tier_label": tier_label, "dev_mode": DEV_MODE},
    )

@app.post("/account/session-timezone")
def set_session_timezone(
    tz: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    tz = _validate_tz(tz)
    u = db.query(UserModel).filter(UserModel.id == user.id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    u.session_tz = tz
    db.commit()
    return _redirect("/account")

# --------------------------
# DMR endpoints
# --------------------------
@app.post("/api/dmr/run-auto")
def api_run_auto(payload: Dict[str, Any], user=Depends(get_current_user)):
    symbol = resolve_symbol(payload.get("symbol") or "BTC")
    session_tz = getattr(user, "session_tz", "UTC") or "UTC"
    inputs = build_auto_inputs(symbol=symbol, session_tz=session_tz)
    return JSONResponse(compute_dmr(symbol, inputs))

@app.post("/api/dmr/run-auto-ai")
def api_dmr_run_auto_ai(payload: Dict[str, Any], user=Depends(get_current_user)):
    """
    AI-written narrative for BOTH Tactical + Elite.
    Falls back to deterministic output if OpenAI is unavailable.
    """
    symbol = resolve_symbol(payload.get("symbol") or "BTC")
    session_tz = getattr(user, "session_tz", "UTC") or "UTC"

    # Step 1: build inputs
    try:
        inputs = build_auto_inputs(symbol=symbol, session_tz=session_tz)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Market data fetch failed: {str(e)}")

    # Step 2: deterministic DMR
    try:
        dmr = compute_dmr(symbol, inputs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DMR compute failed: {str(e)}")

    # Step 3: AI narrative (optional)
    date_str = dmr.get("date") or ""
    try:
        model_payload = {
            "symbol": dmr.get("symbol"),
            "date": dmr.get("date"),
            "levels": dmr.get("levels"),
            "range_30m": dmr.get("range_30m"),
            "htf_shelves": dmr.get("htf_shelves"),
            "inputs": dmr.get("inputs"),
        }
        ai_text = kabroda_ai.generate_daily_market_review(symbol, date_str, model_payload)
        dmr["report_text"] = ai_text
        dmr["report"] = ai_text
        dmr["ai_used"] = True
    except Exception as e:
        dmr["ai_used"] = False
        dmr["ai_error"] = str(e)

    return JSONResponse(dmr)

# --------------------------
# Elite-only Coach (frontend uses /api/assistant/chat)
# --------------------------
@app.post("/api/assistant/chat")
def api_assistant_chat(payload: Dict[str, Any], user=Depends(get_current_user)):
    ensure_can_use_gpt_chat(user)

    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Missing question.")

    symbol = resolve_symbol(payload.get("symbol") or "BTC")
    session_tz = getattr(user, "session_tz", "UTC") or "UTC"

    try:
        inputs = build_auto_inputs(symbol=symbol, session_tz=session_tz)
        dmr = compute_dmr(symbol, inputs)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Market/DMR failed: {str(e)}")

    date_str = dmr.get("date") or ""
    model_payload = {
        "symbol": dmr.get("symbol"),
        "date": dmr.get("date"),
        "levels": dmr.get("levels"),
        "range_30m": dmr.get("range_30m"),
        "htf_shelves": dmr.get("htf_shelves"),
        "inputs": dmr.get("inputs"),
    }

    answer = kabroda_ai.answer_coach_question(symbol, date_str, model_payload, question)
    return {"answer": answer}

# --------------------------
# Billing
# --------------------------
@app.post("/billing/checkout")
def billing_checkout(
    tier: str = Body(embed=True),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    u = db.query(UserModel).filter(UserModel.id == user.id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    url = billing.create_checkout_session(db, u, tier_slug=tier)
    return {"url": url}

@app.post("/billing/portal")
def billing_portal(
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    u = db.query(UserModel).filter(UserModel.id == user.id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    url = billing.create_billing_portal_session(db, u)
    return {"url": url}

# --------------------------
# Health
# --------------------------
@app.get("/health")
def health():
    return {"status": "ok", "dev_mode": DEV_MODE}
