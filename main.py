# main.py — CLEAN, COMPLETE VERSION
import os
import traceback
from typing import Dict, Any, Set

from fastapi import FastAPI, Request, Depends, Form, HTTPException, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

from starlette.exceptions import HTTPException as StarletteHTTPException

import billing
import kabroda_ai

from database import init_db, get_db, SessionLocal, UserModel
from auth import (
    authenticate_user,
    create_session_token,
    create_user,
    delete_session,
    get_current_user,
)
from membership import Tier, tier_marketing_label, ensure_can_use_gpt_chat
from data_feed import build_auto_inputs, resolve_symbol
from dmr_report import compute_dmr


def _truthy(v: str) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "y", "on")


DEV_MODE = _truthy(os.getenv("DEV_MODE", "0"))
DISABLE_REGISTRATION = _truthy(os.getenv("DISABLE_REGISTRATION", "0"))

SEED_ADMIN_EMAIL = (os.getenv("SEED_ADMIN_EMAIL") or "").strip().lower()
SEED_ADMIN_PASSWORD = (os.getenv("SEED_ADMIN_PASSWORD") or "").strip()
SEED_ADMIN_TIER = (os.getenv("SEED_ADMIN_TIER") or "tier3_multi_gpt").strip()

ADMIN_EMAILS_RAW = os.getenv("ADMIN_EMAILS", "")
ADMIN_EMAILS: Set[str] = {e.strip().lower() for e in ADMIN_EMAILS_RAW.split(",") if e.strip()}
if SEED_ADMIN_EMAIL:
    ADMIN_EMAILS.add(SEED_ADMIN_EMAIL)

COOKIE_NAME = "ktbb_session"

app = FastAPI(title="Kabroda BattleBox Suite")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# --------------------------
# Exception handling
# --------------------------
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """
    Browser page navigation should redirect to login on 401.
    API fetch calls should still receive JSON.
    """
    accept = (request.headers.get("accept") or "").lower()
    wants_html = "text/html" in accept

    if exc.status_code == 401 and wants_html:
        next_url = request.url.path
        return RedirectResponse(url=f"/login?next={next_url}", status_code=303)

    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    print("[UNHANDLED ERROR]", request.method, request.url)
    traceback.print_exc()

    if DEV_MODE:
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"{type(exc).__name__}: {str(exc)}",
                "trace": traceback.format_exc(),
            },
        )
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


# --------------------------
# Startup
# --------------------------
@app.on_event("startup")
async def _startup():
    init_db()

    if SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD:
        db = SessionLocal()
        try:
            existing = db.query(UserModel).filter(UserModel.email == SEED_ADMIN_EMAIL).first()
            if not existing:
                try:
                    tier_enum = Tier(SEED_ADMIN_TIER)
                except Exception:
                    tier_enum = Tier.TIER3_MULTI_GPT

                create_user(db, SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD, tier=tier_enum, session_tz="UTC")
                print(f"[BOOTSTRAP] Seeded admin user: {SEED_ADMIN_EMAIL} ({tier_enum.value})")
        finally:
            db.close()


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _validate_tz(tz: str) -> str:
    tz = (tz or "").strip()
    if not tz:
        raise HTTPException(status_code=400, detail="Missing timezone.")
    try:
        ZoneInfo(tz)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid timezone. Use e.g. America/Chicago.")
    return tz


def _dmr_model_payload(dmr: Dict[str, Any]) -> Dict[str, Any]:
    """
    Single canonical payload for AI DMR + Coach.
    Keep this centralized so the contract never drifts.
    """
    inputs = dmr.get("inputs") or {}
    return {
        "symbol": dmr.get("symbol"),
        "date": dmr.get("date"),
        "bias_label": inputs.get("bias_label"),
        "levels": dmr.get("levels"),
        "range_30m": dmr.get("range_30m"),
        "htf_shelves": dmr.get("htf_shelves"),
        "intraday_shelves": inputs.get("intraday_shelves"),
        "trade_logic": dmr.get("trade_logic"),
        "inputs": inputs,
        "news": inputs.get("news") or [],
    }


# --------------------------
# Pages
# --------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    return templates.TemplateResponse("pricing.html", {"request": request})


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})


@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request, user=Depends(get_current_user)):
    tier_label = tier_marketing_label(user.tier)
    is_elite = (user.tier == Tier.TIER3_MULTI_GPT)
    return templates.TemplateResponse(
        "app.html",
        {
            "request": request,
            "user": user,
            "tier_label": tier_label,
            "is_elite": is_elite,
            "dev_mode": DEV_MODE,
        },
    )


@app.get("/account", response_class=HTMLResponse)
def account(request: Request, user=Depends(get_current_user)):
    tier_label = tier_marketing_label(user.tier)
    is_elite = (user.tier == Tier.TIER3_MULTI_GPT)
    return templates.TemplateResponse(
        "account.html",
        {
            "request": request,
            "user": user,
            "tier_label": tier_label,
            "is_elite": is_elite,
        },
    )


# --------------------------
# Auth
# --------------------------
@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request, next: str = "/suite"):
    return templates.TemplateResponse("login.html", {"request": request, "error": None, "next": next})


@app.post("/login")
def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/suite"),
    db: Session = Depends(get_db),
):
    u = authenticate_user(db, email, password)
    if not u:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid email or password.", "next": next},
        )

    token = create_session_token(db, u.id)
    resp = _redirect(next or "/suite")
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax")
    return resp


@app.get("/register", response_class=HTMLResponse)
def register_get(request: Request):
    if DISABLE_REGISTRATION and not DEV_MODE:
        return _redirect("/login")
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@app.post("/register")
def register_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if DISABLE_REGISTRATION and not DEV_MODE:
        return _redirect("/login")

    try:
        # default new users to Tactical
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
# Account: timezone
# --------------------------
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
@app.post("/api/dmr/run-auto-ai")
def api_dmr_run_auto_ai(payload: Dict[str, Any], user=Depends(get_current_user)):
    symbol = resolve_symbol(payload.get("symbol") or "BTC")
    session_tz = getattr(user, "session_tz", "UTC") or "UTC"

    # 1) market inputs (your build_auto_inputs should attach SSE outputs now)
    inputs = build_auto_inputs(symbol=symbol, session_tz=session_tz)

    # 2) deterministic compute
    dmr = compute_dmr(symbol=symbol, inputs=inputs)

    # 3) AI narrative (fallback to deterministic if AI fails)
    date_str = dmr.get("date") or ""
    model_payload = _dmr_model_payload(dmr)

    try:
        ai_text = kabroda_ai.generate_daily_market_review(symbol, date_str, model_payload)
        dmr["report_text"] = ai_text
        dmr["report"] = ai_text
        dmr["ai_used"] = True
    except Exception as e:
        dmr["ai_used"] = False
        dmr["ai_error"] = str(e)

    return JSONResponse(dmr)


# --------------------------
# Elite-only Coach
# --------------------------
@app.post("/api/assistant/chat")
def api_assistant_chat(payload: Dict[str, Any], user=Depends(get_current_user)):
    ensure_can_use_gpt_chat(user)

    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Missing question.")

    symbol = resolve_symbol(payload.get("symbol") or "BTC")
    session_tz = getattr(user, "session_tz", "UTC") or "UTC"

    inputs = build_auto_inputs(symbol=symbol, session_tz=session_tz)
    dmr = compute_dmr(symbol=symbol, inputs=inputs)

    date_str = dmr.get("date") or ""
    model_payload = _dmr_model_payload(dmr)

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


@app.get("/billing/portal")
def billing_portal(db: Session = Depends(get_db), user=Depends(get_current_user)):
    u = db.query(UserModel).filter(UserModel.id == user.id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    url = billing.create_billing_portal(db, u)
    return RedirectResponse(url, status_code=303)


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    return billing.handle_webhook(request, payload, sig, db, UserModel)
