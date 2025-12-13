# main.py (PATCH: add is_elite + AI DMR endpoint + assistant chat endpoint)
import os
from typing import Dict, Any, Set

from fastapi import FastAPI, Request, Depends, Form, HTTPException, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

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


@app.on_event("startup")
async def _startup():
    init_db()
    # Seed admin user if env vars set
    if SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD:
        db = SessionLocal()
        try:
            existing = db.query(UserModel).filter(UserModel.email == SEED_ADMIN_EMAIL).first()
            if not existing:
                try:
                    tier_enum = Tier(SEED_ADMIN_TIER)
                except Exception:
                    tier_enum = Tier.Tier.TIER3_MULTI_GPT  # safety, but not expected
                create_user(db, SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD, tier=tier_enum)
                print(f"[BOOTSTRAP] Seeded admin user: {SEED_ADMIN_EMAIL} ({tier_enum.value})")
        finally:
            db.close()


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _require_admin(user) -> None:
    email = (getattr(user, "email", "") or "").strip().lower()
    if email and email in ADMIN_EMAILS:
        return
    raise HTTPException(status_code=403, detail="Admin access required.")


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

    u = create_user(db, email, password, tier=Tier.TIER2_SINGLE_AUTO)
    token = create_session_token(db, u.id)
    resp = _redirect("/suite")
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        delete_session(db, token)
    resp = _redirect("/")
    resp.delete_cookie(COOKIE_NAME)
    return resp


# --------------------------
# Suite (FIX: pass is_elite)
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


# --------------------------
# DMR endpoints
# --------------------------
@app.post("/api/dmr/run-auto")
def api_run_auto(payload: Dict[str, Any], user=Depends(get_current_user)):
    symbol = resolve_symbol(payload.get("symbol") or "BTC")
    inputs = build_auto_inputs(symbol=symbol, session_tz=getattr(user, "session_tz", "UTC"))
    return JSONResponse(compute_dmr(symbol, inputs))


@app.post("/api/dmr/run-auto-ai")
def api_run_auto_ai(payload: Dict[str, Any], user=Depends(get_current_user)):
    """
    AI-written narrative for BOTH Tactical + Elite (your requirement).
    Falls back to deterministic narrative if OpenAI is unavailable.
    """
    symbol = resolve_symbol(payload.get("symbol") or "BTC")
    inputs = build_auto_inputs(symbol=symbol, session_tz=getattr(user, "session_tz", "UTC"))
    dmr = compute_dmr(symbol, inputs)

    date_str = dmr.get("date") or ""
    try:
        # Avoid sending the giant report text back into the model
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
        # Keep deterministic output if OpenAI fails
        dmr["ai_used"] = False
        dmr["ai_error"] = str(e)

    return JSONResponse(dmr)


# --------------------------
# Elite-only Coach (matches app.html JS: /api/assistant/chat)
# --------------------------
@app.post("/api/assistant/chat")
def api_assistant_chat(payload: Dict[str, Any], user=Depends(get_current_user)):
    ensure_can_use_gpt_chat(user)  # Elite-only gate

    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Missing question.")

    symbol = resolve_symbol(payload.get("symbol") or "BTC")
    inputs = build_auto_inputs(symbol=symbol, session_tz=getattr(user, "session_tz", "UTC"))
    dmr = compute_dmr(symbol, inputs)

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
# Admin quick checks (keep)
# --------------------------
@app.get("/admin/whoami")
def admin_whoami(user=Depends(get_current_user)):
    _require_admin(user)
    return {"email": user.email, "tier": getattr(user.tier, "value", str(user.tier))}


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
