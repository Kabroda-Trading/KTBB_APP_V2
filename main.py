# main.py
# Clean FastAPI entrypoint for Kabroda BattleBox
# - /suite redirects to /login when not logged in
# - API routes return JSON 401/500 (no HTML redirects)
# - Imports run_dmr + run_ai_coach safely with clear errors

import os
import traceback
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware


# -----------------------------
# Config
# -----------------------------
DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")

def _json_error(status: int, message: str, exc: Optional[BaseException] = None):
    payload: Dict[str, Any] = {"detail": message}
    if DEBUG and exc is not None:
        payload["traceback"] = traceback.format_exc()
    return JSONResponse(payload, status_code=status)


# -----------------------------
# App
# -----------------------------
app = FastAPI()

SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-secret-change-me")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=_env_bool("SESSION_HTTPS_ONLY", False),
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# -----------------------------
# Imports (safe)
# -----------------------------
# auth.py must provide get_current_user(request)
try:
    from auth import get_current_user  # type: ignore
except Exception as e:
    raise RuntimeError("auth.py must export get_current_user(request)") from e

# DMR + AI coach entrypoints
# Adjust these if your function names differ.
try:
    from dmr_report import run_dmr  # type: ignore
except Exception:
    run_dmr = None  # type: ignore

try:
    from kabroda_ai import run_ai_coach  # type: ignore
except Exception:
    run_ai_coach = None  # type: ignore


# -----------------------------
# Auth dependencies
# -----------------------------
def require_user_api(request: Request):
    """For JSON API routes: raise a proper 401 JSON error."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in.")
    return user


# -----------------------------
# Page routes
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

@app.get("/login", response_class=HTMLResponse)
def login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request):
    # IMPORTANT: page route redirects instead of returning JSON 401
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse(
        "app.html",
        {
            "request": request,
            "user": user,
        },
    )


# -----------------------------
# API: DMR generation
# -----------------------------
@app.post("/api/run_dmr")
def api_run_dmr(payload: dict, request: Request, user=Depends(require_user_api)):
    symbol = payload.get("symbol")
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol required")

    if run_dmr is None:
        return _json_error(
            500,
            "DMR engine not available: could not import run_dmr from dmr_report.py",
        )

    try:
        dmr = run_dmr(
            symbol=symbol,
            user_timezone=user.get("timezone"),
        )
        return {"dmr": dmr}
    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "DMR compute failed", e)


# -----------------------------
# API: AI Coach (anchored to DMR)
# -----------------------------
@app.post("/api/ai_coach")
def api_ai_coach(payload: dict, request: Request, user=Depends(require_user_api)):
    message = payload.get("message")
    dmr = payload.get("dmr")

    if not message or not dmr:
        raise HTTPException(status_code=400, detail="Message and DMR required")

    if run_ai_coach is None:
        return _json_error(
            500,
            "AI coach not available: could not import run_ai_coach from kabroda_ai.py",
        )

    try:
        reply = run_ai_coach(
            user_message=message,
            dmr_context=dmr,
            tier=user.get("tier"),
        )
        return {"reply": reply}
    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "AI coach failed", e)


# -----------------------------
# Health
# -----------------------------
@app.get("/health")
def health():
    return {"status": "ok"}
