# main.py
import os
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

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


def _normalize_user(user: Dict[str, Any]) -> Dict[str, Any]:
    """
    Your templates expect:
      - user.email
      - user.tier
      - user.session_tz
    Some older code used 'timezone'. We keep both for compatibility.
    """
    if "tier" not in user:
        user["tier"] = "free"

    # Accept either key, but ensure session_tz exists.
    tz = user.get("session_tz") or user.get("timezone") or "UTC"
    if not isinstance(tz, str) or not tz.strip():
        tz = "UTC"
    user["session_tz"] = tz
    user["timezone"] = tz  # legacy alias

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


# ---- Render healthchecks ----
@app.head("/")
def head_root():
    return Response(status_code=200)


@app.head("/suite")
def head_suite():
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

        if not user:
            # If you want to keep a bootstrap admin while iterating:
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
            secure=bool(os.environ.get("COOKIE_SECURE", "")),
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
# API: session timezone (YOUR UI POSTS form tz=...)
# -----------------------------
@app.post("/account/session-timezone")
async def set_timezone(request: Request, user=Depends(require_user)):
    try:
        user = _normalize_user(user)
        tz: Optional[str] = None

        # 1) Try JSON
        try:
            data = await request.json()
            if isinstance(data, dict):
                tz = data.get("tz") or data.get("timezone") or data.get("session_tz")
        except Exception:
            pass

        # 2) Try form
        if not tz:
            try:
                form = await request.form()
                tz = form.get("tz") or form.get("timezone") or form.get("session_tz")
            except Exception:
                pass

        tz = (tz or "").strip()
        if not tz:
            raise HTTPException(status_code=400, detail="timezone required")

        user["session_tz"] = tz
        user["timezone"] = tz  # legacy alias
        set_user_session(request, user)

        return {"ok": True, "session_tz": tz}

    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "Failed to set timezone", e)


# -----------------------------
# API: DMR (UI calls this exact endpoint)
# IMPORTANT: return FLAT payload (levels/range_30m/etc at top level),
# because app.html renderDMR() reads data.levels directly. :contentReference[oaicite:8]{index=8}
# -----------------------------
@app.post("/api/dmr/run-auto-ai")
async def run_auto_ai(request: Request, user=Depends(require_user)):
    try:
        user = _normalize_user(user)

        payload = await request.json()
        symbol = (payload.get("symbol") or "BTC").upper().strip()
        market = "BTCUSDT" if symbol in ("BTC", "BTCUSDT") else symbol

        import dmr_report  # type: ignore

        # This function is now guaranteed by our rewritten dmr_report.py
        dmr = dmr_report.run_auto_ai(symbol=market, user_timezone=user["session_tz"])  # type: ignore

        # Optional AI narrative: only if key is set (never crash the endpoint)
        try:
            if (os.getenv("OPENAI_API_KEY") or "").strip():
                from kabroda_ai import generate_daily_market_review  # type: ignore

                dmr["report_text"] = generate_daily_market_review(
                    symbol=dmr.get("symbol") or market,
                    date_str=dmr.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    context=dmr,
                )
        except Exception:
            pass

        return dmr

    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "DMR generation failed", e)


# -----------------------------
# API: Assistant chat (YOUR UI calls /api/assistant/chat with {symbol, question})
# :contentReference[oaicite:9]{index=9}
# -----------------------------
@app.post("/api/assistant/chat")
async def assistant_chat(request: Request, user=Depends(require_user)):
    try:
        user = _normalize_user(user)
        if not _is_elite(user):
            raise HTTPException(status_code=403, detail="Elite required")

        payload = await request.json()
        symbol = (payload.get("symbol") or "BTCUSDT").upper().strip()
        question = (payload.get("question") or payload.get("message") or "").strip()
        dmr = payload.get("dmr")

        # If UI didn’t send DMR context, we can’t anchor answers.
        if not question:
            raise HTTPException(status_code=400, detail="question required")
        if not dmr or not isinstance(dmr, dict):
            raise HTTPException(status_code=400, detail="dmr context required")

        import kabroda_ai  # type: ignore

        # Use new wrapper (guaranteed by rewritten kabroda_ai.py)
        answer = kabroda_ai.run_ai_coach(user_message=question, dmr_context=dmr, tier=user.get("tier"))  # type: ignore

        return {"answer": answer}

    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "Assistant chat failed", e)


# Aliases (helps if you have older frontend calls)
@app.post("/api/ai_coach")
async def ai_coach_alias(request: Request, user=Depends(require_user)):
    # Expect {message, dmr} but also allow {question, dmr}
    payload = await request.json()
    return await assistant_chat(
        request=request,
        user=user,
    )


@app.post("/api/ai/chat")
async def ai_chat_alias(request: Request, user=Depends(require_user)):
    return await assistant_chat(request=request, user=user)


@app.get("/health")
def health():
    return {"status": "ok"}
