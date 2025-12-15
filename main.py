# main.py
import os
from typing import Any, Dict

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

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

app = FastAPI()

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")
DISABLE_REGISTRATION = os.environ.get("DISABLE_REGISTRATION", "").lower() in ("1", "true", "yes")


@app.on_event("startup")
def _startup():
    init_db()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(url=path, status_code=302)


def require_user_redirect(request: Request, db: Session = Depends(get_db)):
    """
    Used for PAGE routes where we want redirect-to-login instead of raw 401.
    """
    try:
        return get_current_user(request, db)
    except HTTPException:
        # 303 makes browsers do a GET to /login
        raise HTTPException(status_code=303, headers={"Location": f"/login?next={request.url.path}"})


# --------------------------
# Pages
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
def suite_page(request: Request, user=Depends(require_user_redirect)):
    tier_value = getattr(user.tier, "value", str(user.tier))
    is_elite = tier_value == Tier.TIER3_MULTI_GPT.value
    tier_label = tier_marketing_label(user.tier)

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
def account_page(request: Request, user=Depends(require_user_redirect)):
    tier_label = tier_marketing_label(user.tier)
    is_elite = (user.tier == Tier.TIER3_MULTI_GPT)

    return templates.TemplateResponse(
        "account.html",
        {"request": request, "user": user, "tier_label": tier_label, "is_elite": is_elite},
    )


@app.post("/account/session-timezone")
def set_session_timezone(
    tz: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    tz = (tz or "").strip()[:64] or "UTC"
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


# --------------------------
# AI Coach (Elite only; anchored)
# --------------------------
@app.post("/api/ai/chat")
def api_ai_chat(payload: Dict[str, Any], user=Depends(get_current_user)):
    tier_value = getattr(user.tier, "value", str(user.tier))
    if tier_value != Tier.TIER3_MULTI_GPT.value:
        raise HTTPException(status_code=403, detail="Elite required for AI Coach.")

    message = (payload.get("message") or "").strip()
    dmr = payload.get("dmr")
    if not message:
        raise HTTPException(status_code=400, detail="Message required")
    if not dmr:
        raise HTTPException(status_code=400, detail="DMR context required")

    reply = kabroda_ai.chat_with_kabroda(user_message=message, dmr_context=dmr)
    return {"reply": reply}


@app.get("/health")
def health():
    return {"status": "ok"}
