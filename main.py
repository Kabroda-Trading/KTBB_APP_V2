# main.py
import os
import traceback
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from auth import COOKIE_NAME, get_current_user, require_user, router as auth_router

# Optional imports (don’t hard-crash if you’re still iterating)
try:
    from dmr_report import run_dmr  # type: ignore
except Exception:
    run_dmr = None  # type: ignore

try:
    from kabroda_ai import run_ai_coach  # type: ignore
except Exception:
    run_ai_coach = None  # type: ignore


app = FastAPI()

# -----------------------------
# Sessions
# -----------------------------
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-secret-change-me")

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie=COOKIE_NAME,
    same_site="lax",
    https_only=bool(os.environ.get("SESSION_HTTPS_ONLY", "")),
)

# -----------------------------
# Static/templates
# -----------------------------
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Routers
app.include_router(auth_router)

DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")


def _json_error(status: int, message: str, exc: Optional[BaseException] = None):
    payload: Dict[str, Any] = {"detail": message}
    if DEBUG and exc is not None:
        payload["traceback"] = traceback.format_exc()
    return JSONResponse(payload, status_code=status)


# -----------------------------
# Friendly auth behavior
# -----------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # If a browser hits a protected page, redirect to login instead of showing JSON
    if exc.status_code == 401 and request.url.path.startswith(("/suite",)):
        return RedirectResponse(url="/login", status_code=303)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


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


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request, user=Depends(require_user)):
    return templates.TemplateResponse("app.html", {"request": request, "user": user})


# -----------------------------
# API: DMR (add aliases so frontend never hits Not Found)
# -----------------------------
def _run_dmr_or_fail(symbol: str, user: Dict[str, Any]):
    if run_dmr is None:
        raise HTTPException(status_code=500, detail="DMR engine not available (run_dmr import failed).")
    return run_dmr(symbol=symbol, user_timezone=user.get("timezone"))


@app.post("/api/run_dmr")
@app.post("/api/generate_review")
@app.post("/api/dmr")
def api_run_dmr(request: Request, payload: dict, user=Depends(require_user)):
    symbol = (payload.get("symbol") or "").strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol required")

    try:
        dmr = _run_dmr_or_fail(symbol, user)
        return {"dmr": dmr}
    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "DMR compute failed", e)


# -----------------------------
# API: AI Coach (anchored to DMR)
# -----------------------------
@app.post("/api/ai_coach")
@app.post("/api/coach")
def api_ai_coach(payload: dict, user=Depends(require_user)):
    message = (payload.get("message") or "").strip()
    dmr = payload.get("dmr")

    if not message or not dmr:
        raise HTTPException(status_code=400, detail="Message and DMR required")

    if run_ai_coach is None:
        raise HTTPException(status_code=500, detail="AI coach not available (run_ai_coach import failed).")

    try:
        reply = run_ai_coach(user_message=message, dmr_context=dmr, tier=user.get("tier"))
        return {"reply": reply}
    except Exception as e:
        return _json_error(500, "AI coach failed", e)


@app.get("/health")
def health():
    return {"status": "ok", "user": bool(get_current_user)}
