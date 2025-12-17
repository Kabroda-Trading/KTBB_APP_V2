from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

import auth
import billing
import dmr_report
import kabroda_ai
from database import init_db, get_db, UserModel
from membership import Tier, User as MembershipUser, ensure_can_use_auto, ensure_can_use_symbol_auto, ensure_can_use_gpt_chat, tier_marketing_label


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


def _bool_env(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


SESSION_SECRET = os.getenv("SESSION_SECRET") or os.getenv("SECRET_KEY") or "dev-session-secret-change-me"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
IS_HTTPS = PUBLIC_BASE_URL.startswith("https://")
SESSION_HTTPS_ONLY = _bool_env("SESSION_HTTPS_ONLY", default=IS_HTTPS)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=SESSION_HTTPS_ONLY,
    same_site="lax",
)


@app.on_event("startup")
def _startup():
    init_db()


def _session_user_dict(request: Request) -> Optional[Dict[str, Any]]:
    u = request.session.get("user")
    return u if isinstance(u, dict) else None


def _require_session_user(request: Request) -> Dict[str, Any]:
    return auth.require_session_user(request)


def _db_user_from_session(db: Session, sess: Dict[str, Any]) -> UserModel:
    uid = sess.get("id")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid session")
    u = db.query(UserModel).filter(UserModel.id == int(uid)).first()
    if not u:
        raise HTTPException(status_code=401, detail="User not found")
    return u


def _membership_user(u: UserModel) -> MembershipUser:
    return MembershipUser(id=int(u.id), email=u.email, tier=Tier(u.tier), session_tz=u.session_tz)


# -----------------------------
# Health
# -----------------------------
@app.get("/health")
def health():
    return {"status": "ok"}


# -----------------------------
# Pages
# -----------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})

@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    return templates.TemplateResponse("pricing.html", {"request": request})

@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request, db: Session = Depends(get_db)):
    sess = _session_user_dict(request)
    if not sess:
        return RedirectResponse(url="/login", status_code=303)

    u = _db_user_from_session(db, sess)
    mu = _membership_user(u)

    return templates.TemplateResponse(
        "app.html",
        {
            "request": request,
            "user": {"email": mu.email, "tier": mu.tier.value, "session_tz": mu.session_tz},
            "is_elite": (mu.tier == Tier.TIER3_MULTI_GPT),
        },
    )

@app.get("/account", response_class=HTMLResponse)
def account(request: Request, db: Session = Depends(get_db)):
    sess = _session_user_dict(request)
    if not sess:
        return RedirectResponse(url="/login", status_code=303)

    u = _db_user_from_session(db, sess)
    mu = _membership_user(u)

    return templates.TemplateResponse(
        "account.html",
        {
            "request": request,
            "user": {"email": mu.email, "tier": mu.tier.value, "session_tz": mu.session_tz},
            "tier_label": tier_marketing_label(mu.tier),
        },
    )


# -----------------------------
# Auth
# -----------------------------
@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login")
def login_post(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
):
    # 1) DB auth
    u = auth.authenticate_user(db, email=email, password=password)

    # 2) Seed admin fallback
    if not u:
        seed_email = (os.getenv("SEED_ADMIN_EMAIL") or "").strip().lower()
        seed_pw = (os.getenv("SEED_ADMIN_PASSWORD") or "").strip()
        if seed_email and seed_pw and email.strip().lower() == seed_email and password == seed_pw:
            u = auth.get_user_by_email(db, seed_email)
            if not u:
                # create seed admin user if missing
                u = auth.create_user(db, seed_email, seed_pw)
                u.tier = os.getenv("SEED_ADMIN_TIER", "tier3_multi_gpt")
                u.session_tz = "UTC"
                db.commit()
                db.refresh(u)

    if not u:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"}, status_code=401)

    auth.set_user_session(request, u, is_admin=False)
    return RedirectResponse(url="/suite", status_code=303)

@app.post("/logout")
def logout(request: Request):
    auth.clear_user_session(request)
    return RedirectResponse(url="/", status_code=303)

@app.get("/register", response_class=HTMLResponse)
def register_get(request: Request):
    if auth.registration_disabled():
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("register.html", {"request": request, "error": None})

@app.post("/register")
def register_post(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
):
    if auth.registration_disabled():
        raise HTTPException(status_code=403, detail="Registration disabled")

    u = auth.create_user(db, email=email, password=password)
    auth.set_user_session(request, u, is_admin=False)
    return RedirectResponse(url="/suite", status_code=303)


# -----------------------------
# Account timezone (accept JSON or form)
# -----------------------------
@app.post("/account/session-timezone")
async def set_session_timezone(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)

    tz = None
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        payload = await request.json()
        tz = payload.get("tz") or payload.get("timezone")
    else:
        form = await request.form()
        tz = form.get("tz")

    tz = (tz or "UTC").strip() or "UTC"
    u.session_tz = tz
    db.commit()
    db.refresh(u)

    # refresh session copy
    auth.set_user_session(request, u, is_admin=bool(sess.get("is_admin")))
    return {"ok": True, "tz": tz}


# -----------------------------
# Billing / Stripe
# -----------------------------
@app.post("/billing/checkout")
async def billing_checkout(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)

    payload = await request.json()
    tier = (payload.get("tier") or "").strip().lower()
    if tier not in ("tactical", "elite"):
        raise HTTPException(status_code=400, detail="Invalid tier")

    url = billing.create_checkout_session(db=db, user_model=u, tier_slug=tier)
    return {"url": url}

@app.post("/billing/portal")
async def billing_portal(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    url = billing.create_billing_portal(db=db, user_model=u)
    return {"url": url}

@app.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    return billing.handle_webhook(request=request, payload=payload, sig_header=sig, db=db, UserModel=UserModel)


# -----------------------------
# DMR APIs (locked)
# -----------------------------
@app.post("/api/dmr/run-raw")
async def dmr_run_raw(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    mu = _membership_user(u)

    ensure_can_use_auto(mu)

    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    short = symbol.replace("USDT", "")
    ensure_can_use_symbol_auto(mu, short)

    raw = dmr_report.run_auto_raw(symbol=symbol, session_tz=mu.session_tz)
    request.session["last_dmr_raw"] = raw
    return JSONResponse(raw)

@app.post("/api/dmr/run-narrative")
async def dmr_run_narrative(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    mu = _membership_user(u)

    ensure_can_use_auto(mu)

    payload = await request.json()
    raw = payload.get("dmr") or request.session.get("last_dmr_raw")
    if not isinstance(raw, dict):
        # build from symbol if provided
        symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
        raw = dmr_report.run_auto_raw(symbol=symbol, session_tz=mu.session_tz)

    text = kabroda_ai.generate_daily_market_review(
        symbol=raw.get("symbol", "BTCUSDT"),
        date_str=raw.get("date", ""),
        context=raw,
    )
    return {"report_text": text}


# Legacy endpoint your current UI already calls
@app.post("/api/dmr/run-auto-ai")
async def dmr_run_auto_ai(request: Request, db: Session = Depends(get_db)):
    raw = await dmr_run_raw(request, db)  # type: ignore
    if isinstance(raw, JSONResponse):
        raw_payload = raw.body
    # easier: just recompute narrative from session raw
    sess_raw = request.session.get("last_dmr_raw")
    if not isinstance(sess_raw, dict):
        raise HTTPException(status_code=500, detail="Missing DMR context")
    report_text = kabroda_ai.generate_daily_market_review(
        symbol=sess_raw.get("symbol", "BTCUSDT"),
        date_str=sess_raw.get("date", ""),
        context=sess_raw,
    )
    sess_raw["report_text"] = report_text
    request.session["last_dmr_full"] = sess_raw
    return JSONResponse(sess_raw)


# -----------------------------
# AI Coach (Elite-only) — endpoint + contract the UI expects
# -----------------------------
@app.post("/api/ai_coach")
async def ai_coach(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    mu = _membership_user(u)

    ensure_can_use_gpt_chat(mu)

    payload = await request.json()
    message = (payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Missing message")

    # Prefer dmr payload from request, else session
    dmr_ctx = payload.get("dmr") or request.session.get("last_dmr_full") or request.session.get("last_dmr_raw")
    if not isinstance(dmr_ctx, dict):
        raise HTTPException(status_code=400, detail="Run the DMR first so coach has today’s context.")

    reply = kabroda_ai.run_ai_coach(user_message=message, dmr_context=dmr_ctx, tier=mu.tier.value)
    return {"reply": reply}
