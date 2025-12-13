# main.py
import os
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import billing
from fastapi import Body


from database import init_db, get_db, SessionLocal, UserModel
from auth import (
    authenticate_user,
    create_session_token,
    create_user,
    delete_session,
    get_current_user,
)
from membership import Tier, tier_marketing_label
from data_feed import build_auto_inputs, resolve_symbol
from dmr_report import compute_dmr
import kabroda_ai


# --------------------------
# Config
# --------------------------
def _truthy(v: str) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "y", "on")

DEV_MODE = _truthy(os.getenv("DEV_MODE", "0"))
DISABLE_REGISTRATION = _truthy(os.getenv("DISABLE_REGISTRATION", "0"))

SEED_ADMIN_EMAIL = (os.getenv("SEED_ADMIN_EMAIL") or "").strip().lower()
SEED_ADMIN_PASSWORD = (os.getenv("SEED_ADMIN_PASSWORD") or "").strip()
SEED_ADMIN_TIER = (os.getenv("SEED_ADMIN_TIER") or "tier3_multi_gpt").strip()

COOKIE_NAME = "ktbb_session"


# --------------------------
# App setup
# --------------------------
app = FastAPI(title="Kabroda BattleBox Suite")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
async def _startup():
    init_db()

    # Create a master/demo user automatically if env vars are set.
    if SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD:
        db = SessionLocal()
        try:
            existing = db.query(UserModel).filter(UserModel.email == SEED_ADMIN_EMAIL).first()
            if not existing:
                # Create as Elite by default for demos/testing
                try:
                    tier_enum = Tier(SEED_ADMIN_TIER)
                except Exception:
                    tier_enum = Tier.TIER3_MULTI_GPT
                create_user(db, SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD, tier=tier_enum)
                print(f"[BOOTSTRAP] Seeded admin user: {SEED_ADMIN_EMAIL} ({tier_enum.value})")
        finally:
            db.close()


# --------------------------
# Helpers
# --------------------------
def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


# --------------------------
# Marketing pages
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
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid email or password."})

    token = create_session_token(db, u.id)
    resp = _redirect("/suite")
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax")
    return resp


@app.get("/register", response_class=HTMLResponse)
def register_get(request: Request):
    if DISABLE_REGISTRATION and not DEV_MODE:
        # Live demo mode: no public signups
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

    # Default tier for new registrations (Tactical)
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
# App (Suite)
# --------------------------
@app.get("/suite", response_class=HTMLResponse)
def suite_page(
    request: Request,
    user=Depends(get_current_user),
):
    tier_label = tier_marketing_label(user.tier)
    return templates.TemplateResponse(
        "app.html",
        {"request": request, "user": user, "tier_label": tier_label, "dev_mode": DEV_MODE},
    )


# --------------------------
# API: DMR
# --------------------------
@app.post("/api/dmr/run-auto")
def api_run_auto(
    payload: Dict[str, Any],
    user=Depends(get_current_user),
):
    symbol = resolve_symbol(payload.get("symbol") or "BTC")
    inputs = build_auto_inputs(symbol=symbol, session_tz=getattr(user, "session_tz", "UTC"))
    result = compute_dmr(symbol, inputs)
    return JSONResponse(result)


@app.post("/api/coach/ask")
def api_coach_ask(
    payload: Dict[str, Any],
    user=Depends(get_current_user),
):
    # Elite only
    if getattr(user.tier, "value", str(user.tier)) not in ("tier3_multi_gpt", "tier3_elite_gpt"):
        raise HTTPException(status_code=403, detail="Elite required.")

    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Missing question.")

    # Run DMR first (keeps coach anchored to numbers)
    symbol = resolve_symbol(payload.get("symbol") or "BTC")
    inputs = build_auto_inputs(symbol=symbol, session_tz=getattr(user, "session_tz", "UTC"))
    dmr = compute_dmr(symbol, inputs)

    answer = kabroda_ai.ask_coach(question=question, dmr=dmr)
    return {"answer": answer}


# --------------------------
# DEV: Set tier (only if DEV_MODE=1)
# --------------------------
@app.post("/dev/set-tier")
def dev_set_tier(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    if not DEV_MODE:
        raise HTTPException(status_code=404, detail="Not found")

    new_tier = (payload.get("tier") or "").strip()
    if not new_tier:
        raise HTTPException(status_code=400, detail="Missing tier")

    u = db.query(UserModel).filter(UserModel.id == user.id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User missing")

    u.tier = new_tier
    db.commit()
    return {"ok": True, "tier": u.tier}

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
def billing_portal(
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
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
