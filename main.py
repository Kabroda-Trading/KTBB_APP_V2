# main.py — production-clean single-file FastAPI app
import os
import traceback
from typing import Dict, Any, Set

from fastapi import FastAPI, Request, Depends, Form, HTTPException, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

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
from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # If a browser is navigating (Accept: text/html) and we hit 401, redirect to login
    accept = (request.headers.get("accept") or "").lower()
    if exc.status_code == 401 and "text/html" in accept:
        next_url = request.url.path
        return RedirectResponse(url=f"/login?next={next_url}", status_code=303)
    # default JSON for API callers
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def _truthy(v: str) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "y", "on")


DEV_MODE = _truthy(os.getenv("DEV_MODE", "0"))
DISABLE_REGISTRATION = _truthy(os.getenv("DISABLE_REGISTRATION", "0"))

PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")

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


# ---------- global errors (so frontend can display real message) ----------
@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    # Never leak secrets; do return a readable message for your own UI.
    # Render logs will still show the stack trace.
    print("[ERROR]", request.method, request.url)
    traceback.print_exc()

    # If it's an HTTPException, let FastAPI handle it normally
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "error": str(exc)},
    )


@app.on_event("startup")
async def _startup():
    init_db()

    # Seed admin user (only if env vars are set)
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
        {"request": request, "user": user, "tier_label": tier_label, "is_elite": is_elite},
    )


# --------------------------
# Auth
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
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid email or password."})

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
# DMR: AI narrative + deterministic fallback
# --------------------------
import traceback

@app.post("/api/dmr/run")
def api_run_dmr(request: Request, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db), user=Depends(get_current_user)):
    try:
        symbol = resolve_symbol(payload.get("symbol") or "BTC")
        inputs = build_auto_inputs(symbol=symbol, session_tz=user.session_tz)
        dmr_payload = compute_dmr(symbol=symbol, inputs=inputs)
        return dmr_payload
    except Exception as e:
        if DEV_MODE:
            return JSONResponse(
                status_code=500,
                content={"detail": f"DMR compute failed: {type(e).__name__}: {e}", "trace": traceback.format_exc()},
            )
        raise HTTPException(status_code=500, detail=f"DMR compute failed: {type(e).__name__}: {e}")


@app.post("/api/dmr/run-auto-ai")
def api_dmr_run_auto_ai(payload: Dict[str, Any], user=Depends(get_current_user)):
    symbol = resolve_symbol(payload.get("symbol") or "BTC")
    session_tz = getattr(user, "session_tz", "UTC") or "UTC"

    # Step 1: build inputs (can fail if exchange unreachable)
    try:
        inputs = build_auto_inputs(symbol=symbol, session_tz=session_tz)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Market data fetch failed: {str(e)}",
        )

    # Step 2: compute deterministic DMR payload
    try:
        dmr = compute_dmr(symbol, inputs)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"DMR compute failed: {str(e)}",
        )

    date_str = dmr.get("date") or ""

    # Step 3: AI narrative (fallback if OpenAI fails)
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
        # keep deterministic report text inside dmr_report.compute_dmr
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

    try:
        url = billing.create_checkout_session(db, u, tier_slug=tier)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Checkout failed: {str(e)}")

    return {"url": url}


@app.get("/billing/portal")
def billing_portal(db: Session = Depends(get_db), user=Depends(get_current_user)):
    u = db.query(UserModel).filter(UserModel.id == user.id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        url = billing.create_billing_portal(db, u)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Billing portal failed: {str(e)}")

    return RedirectResponse(url, status_code=303)


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    return billing.handle_webhook(request, payload, sig, db, UserModel)
