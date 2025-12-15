# main.py
import os
import traceback
from typing import Any, Dict

from fastapi import Body, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

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

DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")
DISABLE_REGISTRATION = os.environ.get("DISABLE_REGISTRATION", "").lower() in ("1", "true", "yes")

app = FastAPI(title="Kabroda BattleBox Suite")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


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
def _startup():
    init_db()


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(url=path, status_code=303)


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


@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request, user=Depends(get_current_user)):
    tier_label = tier_marketing_label(user.tier)
    is_elite = (getattr(user.tier, "value", str(user.tier)) == Tier.TIER3_MULTI_GPT.value)
    return templates.TemplateResponse(
        "app.html",
        {"request": request, "user": user, "tier_label": tier_label, "is_elite": is_elite, "dev_mode": DEV_MODE},
    )


@app.get("/account", response_class=HTMLResponse)
def account(request: Request, user=Depends(get_current_user)):
    tier_label = tier_marketing_label(user.tier)
    is_elite = (getattr(user.tier, "value", str(user.tier)) == Tier.TIER3_MULTI_GPT.value)
    return templates.TemplateResponse(
        "account.html",
        {"request": request, "user": user, "tier_label": tier_label, "is_elite": is_elite},
    )


# --------------------------
# Auth
# --------------------------
@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request, next: str = "/suite"):
    return templates.TemplateResponse("login.html", {"request": request, "error": None, "next": next})


@app.post("/login", response_class=HTMLResponse)
def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/suite"),
    db: Session = Depends(get_db),
):
    u = authenticate_user(db, email, password)
    if not u:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid email or password.", "next": next})

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


# --------------------------
# DMR
# --------------------------
@app.post("/api/dmr/run-auto")
def api_run_auto(payload: Dict[str, Any] = Body(...), user=Depends(get_current_user)):
    symbol = resolve_symbol(payload.get("symbol") or "BTC")
    inputs = build_auto_inputs(symbol=symbol, session_tz=getattr(user, "session_tz", "UTC"))
    return JSONResponse(compute_dmr(symbol, inputs))


# --------------------------
# AI Coach
# --------------------------
@app.post("/api/ai/coach")
def api_ai_coach(payload: Dict[str, Any] = Body(...), user=Depends(get_current_user)):
    tier_value = getattr(user.tier, "value", str(user.tier))
    if tier_value != Tier.TIER3_MULTI_GPT.value:
        raise HTTPException(status_code=403, detail="Elite required for AI Coach.")

    message = (payload.get("message") or "").strip()
    dmr = payload.get("dmr")

    if not message:
        raise HTTPException(status_code=400, detail="Missing message.")
    if not dmr:
        raise HTTPException(status_code=400, detail="Missing DMR context.")

    reply = kabroda_ai.coach_reply(user_message=message, dmr_context=dmr)
    return {"reply": reply}


@app.get("/health")
def health():
    return {"status": "ok"}
