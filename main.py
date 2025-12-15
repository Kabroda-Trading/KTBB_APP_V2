# main.py
import os
import traceback
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

# -----------------------------
# App
# -----------------------------
app = FastAPI()

DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")

def _json_error(status: int, message: str, exc: Optional[BaseException] = None):
    payload: Dict[str, Any] = {"detail": message}
    if DEBUG and exc is not None:
        payload["traceback"] = traceback.format_exc()
    return JSONResponse(payload, status_code=status)

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
# Auth (safe imports)
# -----------------------------
try:
    from auth import get_current_user, require_user  # type: ignore
except Exception:
    # last-resort fallbacks so main.py never fails to boot
    def get_current_user(request: Request):
        try:
            return request.session.get("user")
        except Exception:
            return None

    def require_user(request: Request):
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Not logged in")
        return user

# -----------------------------
# Optional imports (safe)
# -----------------------------
def _run_dmr_pipeline(symbol: str, user: dict) -> dict:
    """
    Calls into dmr_report.py in a tolerant way.
    Adjust the function name here ONCE if your dmr_report exposes a different entrypoint.
    """
    try:
        import dmr_report  # type: ignore

        # Try a few common function names
        for fn_name in ("run_auto_ai", "run_dmr_auto_ai", "generate_dmr", "run_dmr"):
            fn = getattr(dmr_report, fn_name, None)
            if callable(fn):
                # Some versions accept (symbol, timezone) or (symbol, user_timezone) etc.
                try:
                    return fn(symbol=symbol, user_timezone=user.get("timezone"))  # type: ignore
                except TypeError:
                    try:
                        return fn(symbol, user.get("timezone"))  # type: ignore
                    except TypeError:
                        return fn(symbol)  # type: ignore

        raise RuntimeError(
            "dmr_report.py loaded but no known DMR function found. "
            "Expected one of: run_auto_ai, run_dmr_auto_ai, generate_dmr, run_dmr"
        )
    except Exception as e:
        raise e


def _run_ai_coach(message: str, dmr: dict, user: dict) -> str:
    try:
        import kabroda_ai  # type: ignore

        for fn_name in ("ai_coach", "run_ai_coach", "coach_reply", "chat"):
            fn = getattr(kabroda_ai, fn_name, None)
            if callable(fn):
                try:
                    return fn(user_message=message, dmr_context=dmr, tier=user.get("tier"))  # type: ignore
                except TypeError:
                    try:
                        return fn(message, dmr, user.get("tier"))  # type: ignore
                    except TypeError:
                        return fn(message)  # type: ignore

        raise RuntimeError(
            "kabroda_ai.py loaded but no known AI coach function found. "
            "Expected one of: ai_coach, run_ai_coach, coach_reply, chat"
        )
    except Exception as e:
        raise e


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
def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


# NOTE: keep your existing POST /login behavior if you already had it in a different file.
# This basic version expects form fields `email` and `password`.
@app.post("/login")
async def login_post(request: Request):
    form = await request.form()
    email = (form.get("email") or "").strip()
    password = (form.get("password") or "").strip()

    if not email or not password:
        return _json_error(400, "Email and password required")

    try:
        # If you already have real auth verification in auth.py, use it:
        import auth  # type: ignore
        verify = getattr(auth, "verify_login", None) or getattr(auth, "authenticate", None)
        if callable(verify):
            user = verify(email, password)  # type: ignore
        else:
            # fallback "demo" login
            user = {"email": email, "tier": "tier3_multi_gpt", "timezone": "America/Chicago"}

        if not user:
            return _json_error(401, "Invalid login")

        request.session["user"] = user
        return RedirectResponse(url="/suite", status_code=303)
    except Exception as e:
        return _json_error(500, "Login failed", e)


@app.get("/logout")
def logout(request: Request):
    try:
        request.session.clear()
    except Exception:
        pass
    return RedirectResponse(url="/", status_code=303)


@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request, user=Depends(require_user)):
    return templates.TemplateResponse("app.html", {"request": request, "user": user})


# -----------------------------
# EXACT endpoints your frontend is calling
# -----------------------------
@app.post("/account/session-timezone")
async def account_session_timezone(request: Request, user=Depends(require_user)):
    """
    Frontend is calling this. Store timezone into session user.
    Accepts JSON: { "timezone": "America/Chicago" }
    """
    try:
        data = await request.json()
        tz = (data.get("timezone") or "").strip()
        if not tz:
            raise HTTPException(status_code=400, detail="timezone required")

        # Persist into session user blob
        sess_user = request.session.get("user") or {}
        sess_user["timezone"] = tz
        request.session["user"] = sess_user

        return {"ok": True, "timezone": tz}
    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "Failed to set timezone", e)


@app.post("/api/dmr/run-auto-ai")
async def api_dmr_run_auto_ai(request: Request, user=Depends(require_user)):
    """
    Frontend DMR button calls this.
    Accepts JSON: { "symbol": "BTC" } or { "symbol": "BTCUSDT" }
    """
    try:
        payload = await request.json()
        symbol = (payload.get("symbol") or "").strip()
        if not symbol:
            raise HTTPException(status_code=400, detail="symbol required")

        dmr = _run_dmr_pipeline(symbol=symbol, user=user)
        return {"ok": True, "dmr": dmr}
    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "DMR generation failed", e)


# Optional: keep your newer endpoints too (harmless)
@app.post("/api/run_dmr")
async def api_run_dmr(request: Request, user=Depends(require_user)):
    try:
        payload = await request.json()
        symbol = (payload.get("symbol") or "").strip()
        if not symbol:
            raise HTTPException(status_code=400, detail="symbol required")
        dmr = _run_dmr_pipeline(symbol=symbol, user=user)
        return {"dmr": dmr}
    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "DMR failed", e)


@app.post("/api/ai_coach")
async def api_ai_coach(request: Request, user=Depends(require_user)):
    try:
        payload = await request.json()
        message = (payload.get("message") or "").strip()
        dmr = payload.get("dmr")
        if not message or not dmr:
            raise HTTPException(status_code=400, detail="message and dmr required")
        reply = _run_ai_coach(message=message, dmr=dmr, user=user)
        return {"reply": reply}
    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "AI coach failed", e)


@app.get("/health")
def health():
    return {"status": "ok"}
