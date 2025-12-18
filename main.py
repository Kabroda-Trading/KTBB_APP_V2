# main.py
from __future__ import annotations

import os
from pathlib import Path
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
from membership import (
    get_membership_state,
    require_paid_access,
    ensure_symbol_allowed,
    ensure_coach_allowed,
    PRICE_TACTICAL,
    PRICE_ELITE,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOCTRINE_DIR = Path(BASE_DIR) / "doctrine"

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

# main.py

def _slim_dmr(raw: dict) -> dict:
    # Keep only what the UI + AI actually needs.
    keep = [
        "symbol", "date",
        "levels",
        "htf_shelves", "intraday_shelves",
        "tf_facts", "momentum_summary",
        "trade_logic", "execution_rules",
    ]
    return {k: raw.get(k) for k in keep if k in raw}

@app.post("/api/dmr/run-raw")
async def dmr_run_raw(request: Request, db: Session = Depends(get_db)):
    ...
    raw = dmr_report.run_auto_raw(symbol=symbol, session_tz=tz)
    slim = _slim_dmr(raw)
    request.session["last_dmr_raw"] = slim
    return JSONResponse(slim)

@app.post("/api/dmr/run-auto-ai")
async def dmr_run_auto_ai(request: Request, db: Session = Depends(get_db)):
    ...
    raw = dmr_report.run_auto_raw(symbol=symbol, session_tz=tz)
    slim = _slim_dmr(raw)

    _apply_doctrine_to_kabroda_prompts()

    report_text = kabroda_ai.generate_daily_market_review(
        symbol=slim.get("symbol", "BTCUSDT"),
        date_str=slim.get("date", ""),
        context=slim,
    )
    slim["report_text"] = report_text

    # If you want, store only the slim version:
    request.session["last_dmr_full"] = slim

    return JSONResponse(slim)


# -------------------------------------------------------------------
# Doctrine injection (non-invasive, does NOT touch numbers pipeline)
# -------------------------------------------------------------------
_DOCTRINE_APPLIED = False


def _load_doctrine_markdown() -> str:
    if not DOCTRINE_DIR.exists():
        return ""
    md_files = sorted(DOCTRINE_DIR.rglob("*.md"))
    if not md_files:
        return ""

    chunks: list[str] = []
    for p in md_files:
        try:
            rel = p.relative_to(DOCTRINE_DIR)
            text = p.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                continue
            chunks.append(f"\n\n---\n\n# DOCTRINE FILE: {rel.as_posix()}\n\n{text}\n")
        except Exception:
            continue

    return "".join(chunks).strip()


def _apply_doctrine_to_kabroda_prompts() -> None:
    global _DOCTRINE_APPLIED
    if _DOCTRINE_APPLIED:
        return

    doctrine = _load_doctrine_markdown()
    if doctrine:
        appendix = (
            "\n\n"
            "====================\n"
            "KABRODA DOCTRINE (AUTHORITATIVE)\n"
            "Use this doctrine to choose wording, structure, and coaching behavior.\n"
            "Do NOT invent numbers.\n"
            "Do NOT override computed levels.\n"
            "====================\n\n"
            f"{doctrine}\n"
        )
        kabroda_ai.DMR_SYSTEM = (getattr(kabroda_ai, "DMR_SYSTEM", "") or "") + appendix
        kabroda_ai.COACH_SYSTEM = (getattr(kabroda_ai, "COACH_SYSTEM", "") or "") + appendix

    _DOCTRINE_APPLIED = True


@app.on_event("startup")
def _startup():
    init_db()
    _apply_doctrine_to_kabroda_prompts()


# -------------------------------------------------------------------
# Session helpers
# -------------------------------------------------------------------
def _session_user_dict(request: Request) -> Optional[Dict[str, Any]]:
    u = request.session.get("user")
    return u if isinstance(u, dict) else None


def _require_session_user(request: Request) -> Dict[str, Any]:
    return auth.require_session_user(request)


def _db_user_from_session(db: Session, sess: Dict[str, Any]) -> UserModel:
    uid = sess.get("id")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid session")
    u = db.query(UserModel).filter(UserModel.id == uid).first()
    if not u:
        raise HTTPException(status_code=401, detail="User not found")
    return u


def _plan_flags(u: UserModel) -> Dict[str, Any]:
    ms = get_membership_state(u)
    return {
        "is_paid": ms.is_paid,
        "plan": ms.plan,
        "plan_label": ms.label,
        "is_elite": bool(ms.is_paid and ms.plan == "elite"),
        "is_tactical": bool(ms.is_paid and ms.plan == "tactical"),
    }


# -------------------------------------------------------------------
# Public routes
# -------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})


@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    sess = _session_user_dict(request)
    return templates.TemplateResponse(
        "pricing.html",
        {
            "request": request,
            "is_logged_in": bool(sess),
        },
    )


# -------------------------------------------------------------------
# Suite (paywalled)
# -------------------------------------------------------------------
@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request, db: Session = Depends(get_db)):
    sess = _session_user_dict(request)
    if not sess:
        return RedirectResponse(url="/login", status_code=303)

    u = _db_user_from_session(db, sess)

    # Paywall: must have active/trialing subscription
    try:
        require_paid_access(u)
    except HTTPException:
        return RedirectResponse(url="/pricing?paywall=1", status_code=303)

    flags = _plan_flags(u)

    return templates.TemplateResponse(
        "app.html",
        {
            "request": request,
            "user": {
                "email": u.email,
                "session_tz": (u.session_tz or "UTC"),
                "plan_label": flags["plan_label"],
                "plan": flags["plan"] or "",
            },
            "is_elite": flags["is_elite"],
        },
    )


@app.get("/account", response_class=HTMLResponse)
def account(request: Request, db: Session = Depends(get_db)):
    sess = _session_user_dict(request)
    if not sess:
        return RedirectResponse(url="/login", status_code=303)

    u = _db_user_from_session(db, sess)
    flags = _plan_flags(u)

    return templates.TemplateResponse(
        "account.html",
        {
            "request": request,
            "user": {"email": u.email, "session_tz": (u.session_tz or "UTC")},
            "tier_label": flags["plan_label"],  # template expects tier_label; we supply plan label
        },
    )


@app.post("/account/session-timezone")
async def account_set_timezone(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)

    tz = None
    # Support JSON {"timezone": "..."} and form field "tz"
    try:
        payload = await request.json()
        tz = (payload.get("timezone") or payload.get("tz") or "").strip()
    except Exception:
        form = await request.form()
        tz = (form.get("timezone") or form.get("tz") or "").strip()

    if not tz:
        raise HTTPException(status_code=400, detail="Missing timezone")

    u.session_tz = tz
    db.commit()
    return {"ok": True, "timezone": tz}


# -------------------------------------------------------------------
# Auth
# -------------------------------------------------------------------
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
    u = auth.authenticate_user(db, email=email, password=password)
    if not u:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid credentials"},
            status_code=401,
        )

    auth.set_user_session(request, u)
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

    try:
        u = auth.create_user(db, email=email, password=password)
    except HTTPException as e:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": str(e.detail)},
            status_code=e.status_code,
        )

    auth.set_user_session(request, u)
    # Account created, but Suite is paywalled until subscription is active
    return RedirectResponse(url="/pricing?new=1", status_code=303)


# -------------------------------------------------------------------
# DMR APIs (paywalled)
# -------------------------------------------------------------------
@app.post("/api/dmr/run-raw")
async def dmr_run_raw(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)

    # Must be paid
    require_paid_access(u)

    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()

    # Plan-based symbol gating (Tactical BTC only)
    ensure_symbol_allowed(u, symbol)

    tz = (u.session_tz or "UTC").strip() or "UTC"
    raw = dmr_report.run_auto_raw(symbol=symbol, session_tz=tz)

    request.session["last_dmr_raw"] = raw
    return JSONResponse(raw)


@app.post("/api/dmr/run-auto-ai")
async def dmr_run_auto_ai(request: Request, db: Session = Depends(get_db)):
    # Step 1: compute raw (numbers) - unchanged
    await dmr_run_raw(request, db)  # type: ignore

    sess_raw = request.session.get("last_dmr_raw")
    if not isinstance(sess_raw, dict):
        raise HTTPException(status_code=500, detail="Missing DMR context")

    # Step 2: narrative (OpenAI) - doctrine applied
    _apply_doctrine_to_kabroda_prompts()

    report_text = kabroda_ai.generate_daily_market_review(
        symbol=sess_raw.get("symbol", "BTCUSDT"),
        date_str=sess_raw.get("date", ""),
        context=sess_raw,
    )
    sess_raw["report_text"] = report_text
    request.session["last_dmr_full"] = sess_raw
    return JSONResponse(sess_raw)


# -----------------------------
# AI Coach (Elite-only)
# -----------------------------
@app.post("/api/ai_coach")
async def ai_coach(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)

    # Must be paid + elite
    ensure_coach_allowed(u)

    payload = await request.json()
    message = (payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Missing message")

    dmr_ctx = payload.get("dmr") or request.session.get("last_dmr_full") or request.session.get("last_dmr_raw")
    if not isinstance(dmr_ctx, dict):
        raise HTTPException(status_code=400, detail="Run the DMR first so coach has today’s context.")

    _apply_doctrine_to_kabroda_prompts()

    reply = kabroda_ai.run_ai_coach(user_message=message, dmr_context=dmr_ctx, tier="elite")
    return {"reply": reply}


# -----------------------------
# Billing / Stripe
# -----------------------------
@app.post("/billing/checkout")
async def billing_checkout(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)

    payload = await request.json()
    plan = (payload.get("tier") or payload.get("plan") or "").strip().lower()
    if plan not in ("tactical", "elite"):
        raise HTTPException(status_code=400, detail="Invalid plan")

    url = billing.create_checkout_session(db=db, user_model=u, plan=plan)
    return {"url": url}


@app.post("/billing/portal")
async def billing_portal(request: Request, db: Session = Depends(get_db)):
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)

    url = billing.create_billing_portal(db=db, user_model=u)
    return {"url": url}


@app.post("/billing/webhook")
async def billing_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    return billing.handle_webhook(payload=payload, sig_header=sig, db=db, UserModel=UserModel)
