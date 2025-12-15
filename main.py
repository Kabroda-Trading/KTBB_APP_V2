# main.py
import os
import traceback
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.status import HTTP_303_SEE_OTHER


# -----------------------------
# App / middleware
# -----------------------------
app = FastAPI()

DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-secret-change-me")

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=bool(os.environ.get("SESSION_HTTPS_ONLY", "")),
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# -----------------------------
# Auth (single source of truth)
# -----------------------------
from auth import COOKIE_NAME, get_current_user, require_user  # noqa: F401


# -----------------------------
# Helpers
# -----------------------------
def _wants_html(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    return "text/html" in accept

def _json_error(status: int, message: str, exc: Optional[BaseException] = None):
    payload: Dict[str, Any] = {"detail": message}
    if DEBUG and exc is not None:
        payload["traceback"] = traceback.format_exc()
    return JSONResponse(payload, status_code=status)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Redirect browsers to login on 401 for page navigation
    if exc.status_code == 401 and _wants_html(request):
        return RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def _call_first_available(module_name: str, candidates: Tuple[str, ...], *args, **kwargs):
    """
    Tries module.<candidate>(*args, **kwargs) for the first function that exists.
    Allows your dmr_report.py / kabroda_ai.py to evolve without breaking main.py.
    """
    try:
        mod = __import__(module_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed importing {module_name}: {e}")

    last_err: Optional[BaseException] = None
    for fname in candidates:
        fn = getattr(mod, fname, None)
        if callable(fn):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                last_err = e
                continue

    if last_err:
        raise HTTPException(
            status_code=500,
            detail=f"{module_name} callable found but failed: {last_err}",
        )
    raise HTTPException(
        status_code=500,
        detail=f"No callable found in {module_name}. Tried: {', '.join(candidates)}",
    )


# -----------------------------
# Pages
# -----------------------------
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})

@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    return templates.TemplateResponse("pricing.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login_post(request: Request):
    """
    Minimal login handler:
    - expects form fields: email, password
    - you can swap this to your DB auth later, but it unblocks the flow now
    """
    form = await request.form()
    email = (form.get("email") or "").strip()
    password = (form.get("password") or "").strip()

    if not email or not password:
        return RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)

    # If you have real auth in database.py, plug it in here.
    # For now, accept the seeded admin (or any) and store session.
    # Tier defaults to Elite to match your screenshots.
    request.session["user"] = {  # type: ignore[index]
        "email": email,
        "tier": "elite",
        "timezone": request.session.get("timezone"),  # type: ignore[attr-defined]
    }
    return RedirectResponse("/suite", status_code=HTTP_303_SEE_OTHER)

@app.post("/logout")
def logout(request: Request):
    try:
        request.session.clear()  # type: ignore[attr-defined]
    except Exception:
        pass
    return RedirectResponse("/", status_code=HTTP_303_SEE_OTHER)

@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request, user=Depends(require_user)):
    return templates.TemplateResponse("app.html", {"request": request, "user": user})


# -----------------------------
# Account utilities expected by frontend
# -----------------------------
@app.post("/account/session-timezone")
async def set_session_timezone(request: Request, user=Depends(require_user)):
    """
    Frontend calls this (your logs show 404 previously).
    Accepts JSON like {"timezone": "America/Chicago"}.
    Stores it in session and in the user dict.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    tz = payload.get("timezone") or payload.get("tz")
    if not tz:
        raise HTTPException(status_code=400, detail="timezone required")

    request.session["timezone"] = tz  # type: ignore[index]
    # also mirror into user
    user["timezone"] = tz
    request.session["user"] = user  # type: ignore[index]
    return {"ok": True, "timezone": tz}


# -----------------------------
# DMR API (match frontend route names)
# -----------------------------
@app.post("/api/dmr/run-auto-ai")
async def api_dmr_run_auto_ai(request: Request, user=Depends(require_user)):
    """
    This is the endpoint your UI is calling.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    symbol = payload.get("symbol") or payload.get("ticker")
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol required")

    tz = user.get("timezone") or request.session.get("timezone")  # type: ignore[attr-defined]

    try:
        result = _call_first_available(
            "dmr_report",
            ("run_dmr", "generate_dmr", "build_dmr", "dmr_for_symbol", "compute_dmr"),
            symbol,
            tz,
        )
    except TypeError:
        # if your function only accepts (symbol) not (symbol, tz)
        result = _call_first_available(
            "dmr_report",
            ("run_dmr", "generate_dmr", "build_dmr", "dmr_for_symbol", "compute_dmr"),
            symbol,
        )
    except Exception as e:
        return _json_error(500, "DMR generation failed", e)

    # Normalize output so frontend always has something stable
    if isinstance(result, str):
        return {"ok": True, "symbol": symbol, "dmr": {"report": result}}
    if isinstance(result, dict):
        return {"ok": True, "symbol": symbol, "dmr": result}
    return {"ok": True, "symbol": symbol, "dmr": {"data": result}}


# Backward-compatible alias (if older JS hits this)
@app.post("/api/run_dmr")
async def api_run_dmr_alias(request: Request, user=Depends(require_user)):
    return await api_dmr_run_auto_ai(request, user)


# -----------------------------
# AI Coach API
# -----------------------------
@app.post("/api/ai_coach")
async def api_ai_coach(request: Request, user=Depends(require_user)):
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    message = payload.get("message") or payload.get("text")
    dmr = payload.get("dmr") or payload.get("context")

    if not message:
        raise HTTPException(status_code=400, detail="message required")
    if not dmr:
        raise HTTPException(status_code=400, detail="dmr required")

    try:
        reply = _call_first_available(
            "kabroda_ai",
            ("run_ai_coach", "ai_coach_reply", "chat_with_kabroda", "coach_chat"),
            message,
            dmr,
            user.get("tier"),
        )
    except TypeError:
        # try simpler signatures
        reply = _call_first_available(
            "kabroda_ai",
            ("run_ai_coach", "ai_coach_reply", "chat_with_kabroda", "coach_chat"),
            message,
            dmr,
        )
    except Exception as e:
        return _json_error(500, "AI coach failed", e)

    if isinstance(reply, dict):
        return {"ok": True, "reply": reply.get("reply") or reply}
    return {"ok": True, "reply": reply}


@app.get("/health")
def health():
    return {"status": "ok"}
