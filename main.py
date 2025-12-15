# main.py — CLEAN, COMPLETE VERSION (single source auth cookie)
import os
import traceback
from typing import Dict, Any, Set

from fastapi import FastAPI, Request, Depends, Form, HTTPException
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
    COOKIE_NAME,            # <-- now guaranteed to exist
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

app = FastAPI(title="Kabroda BattleBox Suite")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# --------------------------
# Exception handling
# --------------------------
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
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
            content={"detail": f"{type(exc).__name__}: {str(exc)}", "trace": traceback.format_exc()},
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


# --------------------------
# Auth
# --------------------------
@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request, next: str = "/suite"):
    # This fixes your “Method Not Allowed” when visiting /login in browser
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
    dmr = compute_dmr(inputs)
    return {"dmr": dmr}


# --------------------------
# AI Coach (Elite only)
# --------------------------
@app.post("/api/ai/chat")
def api_ai_chat(payload: Dict[str, Any], user=Depends(get_current_user)):
    ensure_can_use_gpt_chat(user.tier)

    message = (payload.get("message") or "").strip()
    dmr = payload.get("dmr")

    if not message:
        raise HTTPException(status_code=400, detail="Message required.")
    if not dmr:
        raise HTTPException(status_code=400, detail="DMR context required.")

    return {"reply": kabroda_ai.chat(user_message=message, dmr_context=dmr, tier=user.tier)}
