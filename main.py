# main.py
import os
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates
from sqlalchemy.orm import Session

import auth
from database import init_db, get_db, UserModel

# Use Project Omega ONLY
import project_omega


def _is_admin(u: UserModel) -> bool:
    return bool(u and u.is_admin)


def _db_user_from_session(db: Session, user_id: int) -> UserModel:
    u = db.query(UserModel).filter(UserModel.id == user_id).first()
    if not u:
        raise HTTPException(status_code=401, detail="Invalid session")
    return u


def _require_session_user_id(request: Request) -> int:
    return auth.require_session_user(request)


def _template_or_fallback(request: Request, templates: Jinja2Templates, name: str, context: Dict[str, Any]):
    """
    If a template is missing in production, don't 500 — show a helpful placeholder.
    """
    try:
        return templates.TemplateResponse(name, context)
    except Exception:
        # Keeps the site alive even if templates are missing on deploy
        return HTMLResponse(
            f"<h2>Missing template: {name}</h2><p>Deploy includes /templates?</p>",
            status_code=200,
        )


# --- App ---
app = FastAPI()

# Sessions
SECRET_KEY = os.getenv("SECRET_KEY") or "dev-secret-change-me"
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=True)

# Static + Templates
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Auth router
app.include_router(auth.router)


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return {"ok": True}


# --- Root / Suite Index ---
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/suite", status_code=302)


@app.get("/suite", response_class=HTMLResponse, include_in_schema=False)
def suite_home(request: Request):
    # You can point this at your real index template
    return _template_or_fallback(
        request,
        templates,
        "suite_index.html",
        {"request": request, "title": "Kabroda Suite"},
    )


# --- Suite Pages (these were 404 for you) ---
@app.get("/suite/research-lab", response_class=HTMLResponse, include_in_schema=False)
def research_lab_page(request: Request):
    return _template_or_fallback(request, templates, "research_lab.html", {"request": request})


@app.get("/suite/battle-control", response_class=HTMLResponse, include_in_schema=False)
def battle_control_page(request: Request):
    return _template_or_fallback(request, templates, "battle_control.html", {"request": request})


# NEW: Omega page (replace black-ops)
@app.get("/suite/omega", response_class=HTMLResponse, include_in_schema=False)
def omega_page(request: Request):
    # Reuse an existing template if you already have one.
    # If you had "black_ops.html" before, you can rename it or keep it and just render it here.
    return _template_or_fallback(request, templates, "omega.html", {"request": request})


# Backward compat: if something still hits /suite/black-ops, redirect it.
@app.get("/suite/black-ops", include_in_schema=False)
def legacy_black_ops_ui():
    return RedirectResponse("/suite/omega", status_code=302)


# --- API: Omega ---
@app.post("/api/omega/status")
async def omega_status_api(request: Request, db: Session = Depends(get_db)):
    user_id = _require_session_user_id(request)
    u = _db_user_from_session(db, user_id)
    if not _is_admin(u):
        raise HTTPException(status_code=403, detail="Unauthorized")

    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    session_mode = (payload.get("session_mode") or "AUTO").strip().upper()
    manual_id = payload.get("manual_id")
    operator_flex = bool(payload.get("operator_flex") or False)

    data = await project_omega.get_live_battlebox(
        symbol=symbol,
        session_mode=session_mode,
        manual_id=manual_id,
        operator_flex=operator_flex,
    )
    return JSONResponse(data)


@app.get("/api/omega/review")
async def omega_review_api(symbol: str = "BTCUSDT", session_tz: str = "UTC"):
    data = await project_omega.get_session_review(symbol.strip().upper(), session_tz)
    return JSONResponse(data)


# Backward compat: if frontend still calls black-ops API, route it to omega so you stop crashing.
@app.post("/api/black-ops/status")
async def legacy_black_ops_status_alias(request: Request, db: Session = Depends(get_db)):
    return await omega_status_api(request, db)
