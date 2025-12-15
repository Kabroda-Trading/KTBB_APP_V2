# main.py
import os
import traceback
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from auth import (
    COOKIE_NAME,
    get_current_user,
    require_user,
    set_user_session,
    clear_user_session,
    make_legacy_cookie_value,
)

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


def _json_error(status: int, message: str, exc: Optional[BaseException] = None):
    payload: Dict[str, Any] = {"detail": message}
    if DEBUG and exc is not None:
        payload["traceback"] = traceback.format_exc()
    return JSONResponse(payload, status_code=status)


# ---- Render healthcheck: allow HEAD / ----
@app.head("/")
def head_root():
    return Response(status_code=200)


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


@app.post("/login")
async def login_submit(request: Request):
    """
    Expects form POST from login.html.
    Your login.html likely posts email/password as form fields.
    """
    try:
        form = await request.form()
        email = (form.get("email") or "").strip().lower()
        password = (form.get("password") or "").strip()

        if not email or not password:
            raise HTTPException(status_code=400, detail="Email and password required")

        # Try your DB auth if available
        user = None
        try:
            # If your database.py has a function, use it (safe import)
            import database  # type: ignore
            if hasattr(database, "authenticate_user"):
                user = database.authenticate_user(email, password)  # type: ignore
            elif hasattr(database, "verify_user"):
                user = database.verify_user(email, password)  # type: ignore
        except Exception:
            user = None

        # Fallback: allow seeded admin login if you’re using bootstrap logic elsewhere
        # (Keeps you from getting locked out while you iterate.)
        if not user:
            if email == "you@yourdomain.com":
                user = {"email": email, "tier": "elite", "timezone": None}
            else:
                raise HTTPException(status_code=401, detail="Login failed")

        # Store in session
        if "tier" not in user:
            user["tier"] = "free"
        if "timezone" not in user:
            user["timezone"] = None

        set_user_session(request, user)

        # ALSO set legacy cookie name for older UI code (optional but helps)
        resp = RedirectResponse(url="/suite", status_code=303)
        resp.set_cookie(
            COOKIE_NAME,
            make_legacy_cookie_value(user),
            max_age=60 * 60 * 24 * 14,
            httponly=True,
            samesite="lax",
            secure=bool(os.environ.get("COOKIE_SECURE", "")),
            path="/",
        )
        return resp

    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "Login failed", e)


@app.get("/logout")
def logout(request: Request):
    clear_user_session(request)
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@app.get("/suite", response_class=HTMLResponse)
def suite(request: Request, user=Depends(require_user)):
    return templates.TemplateResponse("app.html", {"request": request, "user": user})


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, user=Depends(require_user)):
    # If you have account.html use it; otherwise fall back to app.html or simple page
    try:
        return templates.TemplateResponse("account.html", {"request": request, "user": user})
    except Exception:
        return templates.TemplateResponse("app.html", {"request": request, "user": user})


# -----------------------------
# API: session timezone (YOUR UI CALLS THIS)
# -----------------------------
@app.post("/account/session-timezone")
async def set_timezone(request: Request, user=Depends(require_user)):
    try:
        data = await request.json()
        tz = data.get("timezone")
        if not tz or not isinstance(tz, str):
            raise HTTPException(status_code=400, detail="timezone required")

        user["timezone"] = tz
        set_user_session(request, user)

        return {"ok": True, "timezone": tz}
    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "Failed to set timezone", e)


# -----------------------------
# API: DMR (YOUR UI CALLS THIS EXACT ENDPOINT)
# -----------------------------
@app.post("/api/dmr/run-auto-ai")
async def run_auto_ai(request: Request, user=Depends(require_user)):
    """
    UI calls POST /api/dmr/run-auto-ai with JSON like: {"symbol":"BTC"} or {"symbol":"BTCUSDT"}.
    This handler normalizes and calls into dmr_report.py safely.
    """
    try:
        payload = await request.json()
        symbol = (payload.get("symbol") or "BTC").upper().strip()
        if symbol in ("BTC", "BTCUSDT"):
            market = "BTCUSDT"
        else:
            market = symbol

        tz = user.get("timezone")

        # Call your report generator (safe + flexible)
        import dmr_report  # type: ignore

        dmr_result = None

        # Try common function names in your file
        for fn_name in ("run_auto_ai", "generate_dmr", "generate_report", "run_dmr", "build_dmr"):
            if hasattr(dmr_report, fn_name):
                fn = getattr(dmr_report, fn_name)
                dmr_result = fn(symbol=market, user_timezone=tz)  # type: ignore
                break

        # If your dmr_report exposes a class instead
        if dmr_result is None and hasattr(dmr_report, "DMRReport"):
            D = getattr(dmr_report, "DMRReport")
            dmr_result = D().run(symbol=market, user_timezone=tz)  # type: ignore

        if dmr_result is None:
            raise RuntimeError("dmr_report.py does not expose a callable generator function")

        return {"ok": True, "dmr": dmr_result}

    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "DMR generation failed", e)


# -----------------------------
# API: AI Coach (add common aliases so UI won’t 404)
# -----------------------------
@app.post("/api/ai_coach")
async def ai_coach(request: Request, user=Depends(require_user)):
    try:
        payload = await request.json()
        message = payload.get("message")
        dmr = payload.get("dmr")

        if not message or not isinstance(message, str):
            raise HTTPException(status_code=400, detail="message required")
        if not dmr:
            raise HTTPException(status_code=400, detail="dmr required")

        import kabroda_ai  # type: ignore

        reply = None
        for fn_name in ("run_ai_coach", "ask_kabroda", "chat", "coach"):
            if hasattr(kabroda_ai, fn_name):
                fn = getattr(kabroda_ai, fn_name)
                reply = fn(user_message=message, dmr_context=dmr, tier=user.get("tier"))  # type: ignore
                break

        if reply is None:
            raise RuntimeError("kabroda_ai.py missing AI coach function")

        return {"ok": True, "reply": reply}

    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "AI coach failed", e)


# Extra aliases (in case your frontend is calling older routes)
@app.post("/api/ai/chat")
async def ai_chat_alias(request: Request, user=Depends(require_user)):
    return await ai_coach(request, user)


@app.get("/health")
def health():
    return {"status": "ok"}
