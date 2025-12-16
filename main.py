# main.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

# Local modules (these must exist in your project)
from auth import require_user, set_user_session  # type: ignore


app = FastAPI()

# ---------- Templates / static ----------
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ---------- Sessions ----------
SESSION_SECRET = (os.getenv("SESSION_SECRET") or os.getenv("SECRET_KEY") or "").strip()
if not SESSION_SECRET:
    # Fallback so app boots locally, but you SHOULD set SESSION_SECRET in Render env vars
    SESSION_SECRET = "dev-insecure-session-secret-change-me"

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=bool((os.getenv("HTTPS_ONLY") or "").strip()),
)

# ---------- Small helpers ----------
def _json_error(status: int, message: str, e: Exception | None = None) -> Dict[str, Any]:
    detail = message if e is None else f"{message}: {str(e)}"
    return {"detail": detail, "status": status}

def _normalize_user(user: Any) -> Dict[str, Any]:
    # Your auth layer may return dict already; keep it tolerant.
    if isinstance(user, dict):
        return user
    return {"email": str(user), "tier": "free", "session_tz": "UTC"}

def _is_elite(user: Dict[str, Any]) -> bool:
    return (user.get("tier") or "").lower() == "elite"

def _tier_label(tier: str) -> str:
    t = (tier or "free").lower()
    return "Elite" if t == "elite" else ("Tactical" if t == "tactical" else "Free")

def _coerce_float(x: Any) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

def _build_inputs(symbol: str, session_tz: str) -> Dict[str, Any]:
    """
    Uses data_feed.build_auto_inputs (your actual generator).
    Keeps your existing key names but also maps into SSE expected names.
    """
    import data_feed  # type: ignore

    auto = data_feed.build_auto_inputs(symbol=symbol, session_tz=session_tz)  # type: ignore

    # ---- Map common key mismatches so SSE has what it needs ----
    # data_feed emits range30m_high/low; SSE often expects r30_high/low
    r30_high = _coerce_float(auto.get("range30m_high"))
    r30_low = _coerce_float(auto.get("range30m_low"))
    if r30_high is not None:
        auto["r30_high"] = r30_high
    if r30_low is not None:
        auto["r30_low"] = r30_low

    # Ensure symbol/date normalized
    auto["symbol"] = (auto.get("symbol") or symbol).upper().strip()
    if not auto.get("date"):
        auto["date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return auto

def _compute_sse(auto_inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    If sse_engine.compute_sse_levels exists, call it and merge results into auto_inputs.
    """
    try:
        import sse_engine  # type: ignore
    except Exception:
        return auto_inputs

    if not hasattr(sse_engine, "compute_sse_levels"):
        return auto_inputs

    try:
        sse_out = sse_engine.compute_sse_levels(auto_inputs)  # type: ignore
        if isinstance(sse_out, dict):
            # Expected to include: levels, htf_shelves, intraday_shelves, range_30m (optional)
            auto_inputs.update(sse_out)
    except Exception:
        # Don’t crash DMR if SSE fails; trade logic should scaffold if missing.
        pass

    return auto_inputs

def _compute_trade_logic(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calls dmr_report.compute_dmr(inputs=...) if present.
    """
    import dmr_report  # type: ignore

    if hasattr(dmr_report, "compute_dmr"):
        out = dmr_report.compute_dmr(inputs=inputs)  # type: ignore
        if isinstance(out, dict):
            inputs.update(out)
    return inputs

def _maybe_add_ai_narrative(dmr: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adds report_text using OpenAI only if OPENAI_API_KEY is set.
    """
    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        return dmr

    try:
        from kabroda_ai import generate_daily_market_review  # type: ignore

        symbol = dmr.get("symbol") or "BTCUSDT"
        date_str = dmr.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

        dmr["report_text"] = generate_daily_market_review(
            symbol=str(symbol),
            date_str=str(date_str),
            context=dmr,
        )
    except Exception:
        # Don’t crash endpoint if OpenAI fails
        pass

    return dmr

def _flatten_payload(dmr: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure the response is FLAT (levels/range_30m/etc at top level).
    app.html expects data.levels, data.range_30m, data.report_text, etc.
    """
    # If something returned nested, normalize here (non-destructive).
    if "dmr" in dmr and isinstance(dmr["dmr"], dict):
        inner = dmr["dmr"]
        for k, v in inner.items():
            if k not in dmr:
                dmr[k] = v
        del dmr["dmr"]
    return dmr


# ---------- Pages ----------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    return templates.TemplateResponse("pricing.html", {"request": request})

@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})

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


# ---------- API: timezone ----------
@app.post("/account/session-timezone")
async def set_timezone(request: Request, user=Depends(require_user)):
    try:
        user = _normalize_user(user)
        tz: Optional[str] = None

        # JSON
        try:
            data = await request.json()
            if isinstance(data, dict):
                tz = data.get("tz") or data.get("timezone") or data.get("session_tz")
        except Exception:
            pass

        # Form
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


# ---------- API: DMR ----------
@app.post("/api/dmr/run-auto-ai")
async def run_auto_ai(request: Request, user=Depends(require_user)):
    """
    UI calls POST /api/dmr/run-auto-ai with JSON like: {"symbol":"BTC"}.
    Returns FLAT payload (levels/range_30m/report_text/etc).
    """
    try:
        user = _normalize_user(user)

        payload = await request.json()
        sym = (payload.get("symbol") or "BTC").upper().strip()
        symbol = "BTCUSDT" if sym in ("BTC", "BTCUSDT") else sym

        session_tz = (user.get("session_tz") or user.get("timezone") or "UTC").strip()

        # 1) Build auto inputs (Binance/Coinbase fetch)
        inputs = _build_inputs(symbol=symbol, session_tz=session_tz)

        # 2) Compute SSE levels/shelves if available
        inputs = _compute_sse(inputs)

        # 3) Compute trade logic scaffolding/strategy picks
        inputs = _compute_trade_logic(inputs)

        # 4) Optional OpenAI narrative (DMR)
        inputs = _maybe_add_ai_narrative(inputs)

        # 5) Ensure response shape matches frontend expectations
        return _flatten_payload(inputs)

    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "DMR generation failed", e)


# ---------- API: Assistant Chat (Elite only) ----------
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
        if not question:
            raise HTTPException(status_code=400, detail="question required")
        if not dmr or not isinstance(dmr, dict):
            raise HTTPException(status_code=400, detail="dmr context required")

        from kabroda_ai import answer_coach_question  # type: ignore

        date_str = dmr.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        answer = answer_coach_question(
            symbol=symbol,
            date_str=str(date_str),
            context=dmr,
            question=question,
        )
        return {"answer": answer}

    except HTTPException:
        raise
    except Exception as e:
        return _json_error(500, "Assistant chat failed", e)


# Aliases (so old frontend calls don’t 404)
@app.post("/api/ai_coach")
async def ai_coach_alias(request: Request, user=Depends(require_user)):
    return await assistant_chat(request=request, user=user)

@app.post("/api/ai/chat")
async def ai_chat_alias(request: Request, user=Depends(require_user)):
    return await assistant_chat(request=request, user=user)


@app.get("/health")
def health():
    return {"status": "ok"}
