"""
main.py - Clean app entrypoint for Render

Goals:
- Serve the site's HTML pages (no Jinja/templates required)
- Provide the /api/dmr/run-raw endpoint used by session_control.html
- Remove all Black-Ops engine dependencies (endpoint returns 410 Gone)
- Avoid importing missing DB helpers (init_db/UserModel) and avoid broken modules
"""

from __future__ import annotations

import os
import time
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import auth
import battlebox_pipeline

# -----------------------------
# App + logging
# -----------------------------

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("kabroda")

APP_ROOT = Path(__file__).resolve().parent

def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v is not None and v != "" else default

SESSION_SECRET = _env("SESSION_SECRET", "dev-session-secret-change-me")
ENV = _env("ENV", _env("RENDER", "local"))

app = FastAPI(title="Kabroda Site", version="1.0.0")

# Sessions (required for auth router)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=bool(_env("SESSION_HTTPS_ONLY", "1") == "1"),
)

# Auth routes (/auth/login, /auth/logout, /auth/me)
app.include_router(auth.router)

# Serve /static if you have it in your repo
static_dir = APP_ROOT / "static"
if static_dir.exists() and static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# -----------------------------
# Helpers
# -----------------------------

def _require_session_user(request: Request) -> Dict[str, Any]:
    """
    Your auth.py stores the user in request.session["user"].
    This replaces the missing auth.require_session_user().
    """
    sess = request.session.get("user")
    if not sess or not isinstance(sess, dict) or not sess.get("username"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return sess

def _safe_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default

def _page(path: str) -> FileResponse:
    fp = APP_ROOT / path
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"Missing file: {path}")
    return FileResponse(str(fp))


# -----------------------------
# Pages
# -----------------------------

@app.get("/")
def home() -> RedirectResponse:
    # Pick your default landing page
    return RedirectResponse(url="/session-control")

@app.get("/about")
def about_page():
    return _page("about.html")

@app.get("/pricing")
def pricing_page():
    return _page("pricing.html")

@app.get("/privacy")
def privacy_page():
    return _page("privacy.html")

@app.get("/register")
def register_page():
    return _page("register.html")

@app.get("/research")
def research_page():
    return _page("research.html")

@app.get("/research-lab")
def research_lab_page():
    return _page("research_lab.html")

@app.get("/project-omega")
def project_omega_page():
    return _page("project_omega.html")

@app.get("/ui-sandbox")
def ui_sandbox_page():
    return _page("ui_sandbox.html")

@app.get("/session-control")
def session_control_page():
    return _page("session_control.html")


# -----------------------------
# APIs
# -----------------------------

@app.get("/api/health")
def health():
    return {"ok": True, "env": ENV, "ts": int(time.time())}

@app.post("/api/dmr/run-raw")
async def dmr_run_raw(request: Request):
    """
    Called by session_control.html.
    This runs the battlebox pipeline and returns a payload that
    includes:
      - levels
      - range_30m: {high, low}
      - session_packet (raw)
    """
    payload = await request.json()

    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    exchange_id = (payload.get("exchange") or "binance").strip().lower()
    tf = (payload.get("timeframe") or "15m").strip().lower()

    # If not provided, use "now"
    anchor_ts = _safe_int(payload.get("anchor_ts"), int(time.time()))

    # Optional candles_json passthrough
    candles_json = payload.get("candles_json")

    session_packet = await battlebox_pipeline.get_live_battlebox(
        symbol=symbol,
        exchange_id=exchange_id,
        tf=tf,
        anchor_ts=anchor_ts,
        candles_json=candles_json,
    )

    # Normalize to what the UI expects
    levels = session_packet.get("levels", {}) if isinstance(session_packet, dict) else {}
    out = {
        "ok": True,
        "symbol": symbol,
        "exchange": exchange_id,
        "timeframe": tf,
        "anchor_ts": anchor_ts,
        "levels": levels,
        "range_30m": {
            "high": levels.get("range30m_high"),
            "low": levels.get("range30m_low"),
        },
        "session_packet": session_packet,
    }
    return JSONResponse(out)

@app.post("/api/black-ops/status")
async def black_ops_disabled(request: Request):
    """
    You said you are NOT using Black-Ops at all.
    Keep this route to prevent random UI calls from crashing the app,
    but explicitly disable it.
    """
    # If something is calling it from an admin UI, this prevents 500 spam.
    _ = request  # unused
    return JSONResponse(
        {"ok": False, "disabled": True, "detail": "Black-Ops is disabled. Use Project Omega endpoints."},
        status_code=410,
    )
