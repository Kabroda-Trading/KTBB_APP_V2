# main.py
import os
import traceback
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from auth import (
    COOKIE_NAME,
    authenticate_email_password,
    clear_session,
    get_current_user,
    require_user,
    set_session_user,
)

app = FastAPI()

DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")

SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-secret-change-me")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie=COOKIE_NAME,  # <-- this is why COOKIE_NAME must exist
    same_site="lax",
    https_only=bool(os.environ.get("SESSION_HTTPS_ONLY", "")),
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _json_error(status: int, message: str, exc: Optional[BaseException] = None):
    payload: Dict[str, Any] = {"detail": message}
    if DEBUG and exc is not None:
        payload["traceback"] = traceback.format_exc()
    return JSONResponse(payload, status_code=status)


# -----------------------------
# Pages
# -----------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request, "user": get_current_user(request)})


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request, "user": get_current_user(request)})


@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    return templates.TemplateResponse("pricing.html", {"request": request, "user": get_current_user(request)})


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "user": get_current_user(request)})


@app.post("/login")
async def login_post(request: Request):
    """
    Supports either form-post or JSON.
    Your login.html likely POSTs form fields.
    """
    try:
        ctype = request.headers.get("content-type", "")
        if "application/json" in ctype:
            data = await request.json()
        else:
            form = await request.form()
            data = dict(form)

        email = (data.get("email") or "").strip()
        password = data.get("password") or ""

        user = authenticate_email_password(email, password)
        if not user:
            # back to login with a simple flag
            return RedirectResponse(url="/login?error=1", status_code=303)

        set_session_user(request, user)
        return RedirectResponse(url="/suite", status_code=303)

    except Exception as e:
        return _json_error(500, "Login failed", e)


@app.post("/logout")
def logout(request: Request):
    clear_session(request)
    return RedirectResponse(url="/", status_code=303)


@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request, user=Depends(require_user)):
    return templates.TemplateResponse("app.html", {"request": request, "user": user})


# -----------------------------
# Account helpers expected by the UI
# -----------------------------
@app.post("/account/session-timezone")
async def set_session_timezone(request: Request, user=Depends(require_user)):
    """
    UI is POSTing here and expects 200.
    Accepts JSON: { "timezone": "America/Chicago" } (or similar).
    """
    try:
        data = await request.json()
        tz = (data.get("timezone") or "").strip()
        if not tz:
            raise HTTPException(status_code=400, detail="timezone required")

        # store on session user
        user["timezone"] = tz
        request.session["user"] = user
        return {"ok": True, "timezone": tz}
    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "Failed to set timezone", e)


# -----------------------------
# DMR endpoint expected by your frontend (matches your Render logs)
# -----------------------------
@app.post("/api/dmr/run-auto-ai")
async def run_dmr_auto_ai(request: Request, user=Depends(require_user)):
    """
    This matches the frontend call that was 404'ing:
      POST /api/dmr/run-auto-ai
    """
    try:
        payload = await request.json()
        symbol = (payload.get("symbol") or "").strip()
        if not symbol:
            raise HTTPException(status_code=400, detail="symbol required")

        # Try your project's dmr_report module in a few common shapes
        import dmr_report  # type: ignore

        tz = user.get("timezone")

        if hasattr(dmr_report, "run_auto_ai"):
            dmr = dmr_report.run_auto_ai(symbol=symbol, user_timezone=tz)  # type: ignore
        elif hasattr(dmr_report, "generate_dmr"):
            dmr = dmr_report.generate_dmr(symbol=symbol, user_timezone=tz)  # type: ignore
        elif hasattr(dmr_report, "run_dmr"):
            dmr = dmr_report.run_dmr(symbol=symbol, user_timezone=tz)  # type: ignore
        else:
            raise RuntimeError("dmr_report is missing run_auto_ai/generate_dmr/run_dmr")

        return {"ok": True, "dmr": dmr}

    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "DMR generation failed", e)


# -----------------------------
# AI Coach endpoint (optional, but keeps UI from breaking if it calls it)
# -----------------------------
@app.post("/api/ai/chat")
async def ai_chat(request: Request, user=Depends(require_user)):
    """
    Safe default: expects { message, dmr } and returns { reply }.
    """
    try:
        payload = await request.json()
        msg = payload.get("message")
        dmr = payload.get("dmr")
        if not msg:
            raise HTTPException(status_code=400, detail="message required")

        import kabroda_ai  # type: ignore

        if hasattr(kabroda_ai, "chat"):
            reply = kabroda_ai.chat(message=msg, dmr=dmr, user=user)  # type: ignore
        elif hasattr(kabroda_ai, "run_ai_coach"):
            reply = kabroda_ai.run_ai_coach(user_message=msg, dmr_context=dmr, tier=user.get("tier"))  # type: ignore
        else:
            # fallback “normal assistant” response if the module doesn’t match
            reply = "Hi — ask me about today’s DMR and I’ll help you build a plan."

        return {"reply": reply}

    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "AI chat failed", e)


@app.get("/health")
def health():
    return {"status": "ok"}
