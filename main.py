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
    Tier,
    User as MembershipUser,
    ensure_can_use_auto,
    ensure_can_use_symbol_auto,
    ensure_can_use_gpt_chat,
    tier_marketing_label,
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

_DOCTRINE_APPLIED = False


def _load_doctrine_markdown() -> str:
    """
    Loads all doctrine markdown under ./doctrine/**.md and returns a single string.
    Safe: if folder missing/empty, returns "".
    """
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
    """
    Minimal, non-invasive: append doctrine text to kabroda_ai's system prompts.
    Does not touch any numbers pipeline code.
    """
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
            "Do NOT invent numbers. Do NOT override computed levels.\n"
            "====================\n\n"
            f"{doctrine}\n"
        )
        # Append (don’t replace) so your existing constraints still apply.
        kabroda_ai.DMR_SYSTEM = (kabroda_ai.DMR_SYSTEM or "") + appendix
        kabroda_ai.COACH_SYSTEM = (kabroda_ai.COACH_SYSTEM or "") + appendix

    _DOCTRINE_APPLIED = True


@app.on_event("startup")
def _startup():
    init_db()
    _apply_doctrine_to_kabroda_prompts()


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
    return {"status": "ok", "openai_configured": bool(os.getenv("OPENAI_API_KEY"))}


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
            "tier_label": tier_marketing_label(mu.tier.value),
            "is_elite": mu.tier.value.lower() == "elite",
        },
    )


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    # Use your existing auth module
    user = auth.authenticate_user(email=email, password=password, db=db, UserModel=UserModel)
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid credentials"},
            status_code=401,
        )

    request.session["user"] = {"id": int(user.id), "email": user.email}
    return RedirectResponse(url="/suite", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


# -----------------------------
# Billing
# -----------------------------
@app.post("/billing/portal")
def billing_portal(request: Request, db: Session = Depends(get_db)):
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
# DMR APIs (numbers locked)
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
        symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
        raw = dmr_report.run_auto_raw(symbol=symbol, session_tz=mu.session_tz)

    # Ensure doctrine is applied even if Render worker reused old state
    _apply_doctrine_to_kabroda_prompts()

    text = kabroda_ai.generate_daily_market_review(
        symbol=raw.get("symbol", "BTCUSDT"),
        date_str=raw.get("date", ""),
        context=raw,
    )
    return {"report_text": text}


# Legacy endpoint your UI calls
@app.post("/api/dmr/run-auto-ai")
async def dmr_run_auto_ai(request: Request, db: Session = Depends(get_db)):
    # Step 1: compute raw (numbers)
    raw_resp = await dmr_run_raw(request, db)  # type: ignore
    _ = raw_resp  # keep for clarity

    sess_raw = request.session.get("last_dmr_raw")
    if not isinstance(sess_raw, dict):
        raise HTTPException(status_code=500, detail="Missing DMR context")

    # Step 2: narrative (OpenAI)
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
    mu = _membership_user(u)

    ensure_can_use_gpt_chat(mu)

    payload = await request.json()
    message = (payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Missing message")

    dmr_ctx = payload.get("dmr") or request.session.get("last_dmr_full") or request.session.get("last_dmr_raw")
    if not isinstance(dmr_ctx, dict):
        raise HTTPException(status_code=400, detail="Run the DMR first so coach has today’s context.")

    _apply_doctrine_to_kabroda_prompts()

    reply = kabroda_ai.run_ai_coach(user_message=message, dmr_context=dmr_ctx, tier=mu.tier.value)
    return {"reply": reply}
