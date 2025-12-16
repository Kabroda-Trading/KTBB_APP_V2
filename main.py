# main.py
import os
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from auth import (
    COOKIE_NAME,
    require_user,
    set_user_session,
    clear_user_session,
    make_legacy_cookie_value,
)

app = FastAPI()

# -----------------------------
# Settings / flags
# -----------------------------
DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
DEBUG_ERRORS = os.environ.get("DEBUG_ERRORS", "").lower() in ("1", "true", "yes")

SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-secret-change-me")

# Render is always HTTPS at the edge; cookies can still be secure.
HTTPS_ONLY = bool(os.environ.get("SESSION_HTTPS_ONLY", "")) or bool(os.environ.get("RENDER", ""))

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=HTTPS_ONLY,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# -----------------------------
# Helpers
# -----------------------------
def _json_error(status: int, message: str, exc: Optional[BaseException] = None) -> JSONResponse:
    payload: Dict[str, Any] = {"detail": message}

    # Always print traceback to server logs (Render log is your source of truth)
    if exc is not None:
        print("ERROR:", repr(exc))
        traceback.print_exc()

    # Optionally expose trace to client
    if (DEBUG or DEBUG_ERRORS) and exc is not None:
        payload["trace"] = traceback.format_exc()
        payload["error"] = str(exc)

    return JSONResponse(payload, status_code=status)


async def _safe_json(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalize_user(user: Dict[str, Any]) -> Dict[str, Any]:
    """
    Templates expect:
      - user.email
      - user.tier
      - user.session_tz
    We also keep user.timezone as legacy alias.
    """
    if "tier" not in user:
        user["tier"] = "free"

    tz = user.get("session_tz") or user.get("timezone") or "UTC"
    if not isinstance(tz, str) or not tz.strip():
        tz = "UTC"

    tz = tz.strip()
    user["session_tz"] = tz
    user["timezone"] = tz
    return user


def _tier_label(tier: str) -> str:
    t = (tier or "free").strip().lower()
    if t == "elite":
        return "Elite"
    if t == "tactical":
        return "Tactical"
    return "Free"


def _is_elite(user: Dict[str, Any]) -> bool:
    return (user.get("tier") or "").strip().lower() == "elite"


def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if s in ("BTC", "BTCUSDT"):
        return "BTCUSDT"
    if s in ("ETH", "ETHUSDT"):
        return "ETHUSDT"
    return s or "BTCUSDT"


# -----------------------------
# Render healthchecks
# -----------------------------
@app.head("/")
def head_root():
    return Response(status_code=200)


@app.head("/suite")
def head_suite():
    return Response(status_code=200)


@app.get("/health")
def health():
    return {"status": "ok"}


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
    try:
        form = await request.form()
        email = (form.get("email") or "").strip().lower()
        password = (form.get("password") or "").strip()

        if not email or not password:
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Email and password required"},
                status_code=400,
            )

        user = None
        try:
            import database  # type: ignore

            if hasattr(database, "authenticate_user"):
                user = database.authenticate_user(email, password)  # type: ignore
            elif hasattr(database, "verify_user"):
                user = database.verify_user(email, password)  # type: ignore
        except Exception:
            user = None

        # Bootstrap admin (optional)
        if not user:
            if email == "you@yourdomain.com":
                user = {"email": email, "tier": "elite", "session_tz": "UTC"}
            else:
                return templates.TemplateResponse(
                    "login.html",
                    {"request": request, "error": "Login failed"},
                    status_code=401,
                )

        user = _normalize_user(user)
        set_user_session(request, user)

        resp = RedirectResponse(url="/suite", status_code=303)
        resp.set_cookie(
            COOKIE_NAME,
            make_legacy_cookie_value(user),
            max_age=60 * 60 * 24 * 14,
            httponly=True,
            samesite="lax",
            secure=bool(os.environ.get("COOKIE_SECURE", "")) or bool(os.environ.get("RENDER", "")),
            path="/",
        )
        return resp

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
    user = _normalize_user(user)
    ctx = {
        "request": request,
        "user": user,
        "is_elite": _is_elite(user),
        "tier_label": _tier_label(user.get("tier", "free")),
    }
    return templates.TemplateResponse("app.html", ctx)


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, user=Depends(require_user)):
    user = _normalize_user(user)
    ctx = {
        "request": request,
        "user": user,
        "is_elite": _is_elite(user),
        "tier_label": _tier_label(user.get("tier", "free")),
    }
    return templates.TemplateResponse("account.html", ctx)


# -----------------------------
# API: session timezone
# Accepts JSON {timezone: "..."} as primary, plus legacy keys.
# -----------------------------
@app.post("/account/session-timezone")
async def set_timezone(request: Request, user=Depends(require_user)):
    try:
        user = _normalize_user(user)

        tz: Optional[str] = None

        data = await _safe_json(request)
        tz = data.get("timezone") or data.get("session_tz") or data.get("tz")

        if not tz:
            try:
                form = await request.form()
                tz = form.get("timezone") or form.get("session_tz") or form.get("tz")
            except Exception:
                tz = None

        tz = (tz or "").strip()
        if not tz:
            raise HTTPException(status_code=400, detail="timezone required")

        user["session_tz"] = tz
        user["timezone"] = tz
        set_user_session(request, user)

        return {"ok": True, "session_tz": tz}

    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "Failed to set timezone", e)


# -----------------------------
# API: DMR
# Returns BOTH:
#   - nested {ok:true, dmr:{...}}
#   - AND flat top-level keys (compat for older app.html renderers)
# -----------------------------
@app.post("/api/dmr/run-auto-ai")
async def run_auto_ai(request: Request, user=Depends(require_user)):
    try:
        user = _normalize_user(user)
        payload = await _safe_json(request)

        symbol = _normalize_symbol(payload.get("symbol") or "BTCUSDT")

        import dmr_report  # type: ignore

        dmr = dmr_report.run_auto_ai(symbol=symbol, user_timezone=user["session_tz"])  # type: ignore
        if not isinstance(dmr, dict):
            raise RuntimeError("DMR generator returned non-dict")

        # Optional AI narrative: never allow OpenAI to crash DMR
        try:
            if (os.getenv("OPENAI_API_KEY") or "").strip():
                from kabroda_ai import generate_daily_market_review  # type: ignore

                dmr["report_text"] = generate_daily_market_review(
                    symbol=dmr.get("symbol") or symbol,
                    date_str=dmr.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    context=dmr,
                )
        except Exception as ai_exc:
            # keep deterministic output even if AI fails
            print("AI narrative failed:", repr(ai_exc))
            traceback.print_exc()

        # Return both shapes for max frontend compatibility
        return {"ok": True, "dmr": dmr, **dmr}

    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "DMR generation failed", e)


# -----------------------------
# Chat core (so aliases don't re-read the body)
# -----------------------------
async def _assistant_chat_core(payload: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    user = _normalize_user(user)
    if not _is_elite(user):
        raise HTTPException(status_code=403, detail="Elite required")

    symbol = _normalize_symbol(payload.get("symbol") or "BTCUSDT")
    question = (payload.get("question") or payload.get("message") or "").strip()
    dmr = payload.get("dmr")

    if not question:
        raise HTTPException(status_code=400, detail="question required")
    if not dmr or not isinstance(dmr, dict):
        raise HTTPException(status_code=400, detail="dmr context required")

    import kabroda_ai  # type: ignore
    answer = kabroda_ai.run_ai_coach(user_message=question, dmr_context=dmr, tier=user.get("tier"))  # type: ignore
    return {"answer": answer}


@app.post("/api/assistant/chat")
async def assistant_chat(request: Request, user=Depends(require_user)):
    try:
        payload = await _safe_json(request)
        return await _assistant_chat_core(payload, user)
    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "Assistant chat failed", e)


# Aliases (accept {message, dmr} or {question, dmr})
@app.post("/api/ai_coach")
async def ai_coach_alias(request: Request, user=Depends(require_user)):
    try:
        payload = await _safe_json(request)
        return await _assistant_chat_core(payload, user)
    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "Assistant chat failed", e)


@app.post("/api/ai/chat")
async def ai_chat_alias(request: Request, user=Depends(require_user)):
    try:
        payload = await _safe_json(request)
        return await _assistant_chat_core(payload, user)
    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "Assistant chat failed", e)
