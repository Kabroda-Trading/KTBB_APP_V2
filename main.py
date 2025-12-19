# -----------------------------
# DMR: Run Raw (Step 1)
# -----------------------------
from fastapi import HTTPException
from fastapi.responses import JSONResponse
import asyncio
import os

@app.post("/api/dmr/run-raw")
async def dmr_run_raw(request: Request, db: Session = Depends(get_db)):
    # Auth + paywall
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    ensure_symbol_allowed(u, symbol)

    tz = (u.session_tz or "UTC").strip() or "UTC"

    # Run raw (fast)
    raw = dmr_report.run_auto_raw(symbol=symbol, session_tz=tz)

    # IMPORTANT:
    # Do NOT store the full raw dict in the session cookie (can exceed cookie limits).
    # Store only a tiny breadcrumb for UX/debug.
    request.session["last_dmr_meta"] = {
        "symbol": raw.get("symbol", symbol),
        "date": raw.get("date", ""),
    }

    return JSONResponse(raw)


# -----------------------------
# DMR: Generate AI (Step 2)
# -----------------------------
@app.post("/api/dmr/generate-ai")
async def dmr_generate_ai(request: Request, db: Session = Depends(get_db)):
    # Auth + paywall
    sess = _require_session_user(request)
    u = _db_user_from_session(db, sess)
    require_paid_access(u)

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    # REQUIRE raw payload from client (reliable). No session fallback.
    dmr_raw = payload.get("dmr_raw")
    if not isinstance(dmr_raw, dict):
        raise HTTPException(
            status_code=400,
            detail="Run 'Calibrate the Battlefield' first to generate today's levels.",
        )

    symbol = (dmr_raw.get("symbol") or "BTCUSDT").strip().upper()
    ensure_symbol_allowed(u, symbol)

    _apply_doctrine_to_kabroda_prompts()

    try:
        report_text = await asyncio.wait_for(
            asyncio.to_thread(
                kabroda_ai.generate_daily_market_review,
                symbol=symbol,
                date_str=dmr_raw.get("date", ""),
                context=dmr_raw,
            ),
            timeout=int(os.getenv("DMR_AI_TIMEOUT_SECONDS", "75")),
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="AI generation timed out. Try again.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI generation failed: {e}")

    # Store only the final narrative (small) for convenience.
    request.session["last_dmr_meta"] = {
        "symbol": symbol,
        "date": dmr_raw.get("date", ""),
    }

    return JSONResponse({"report_text": report_text})


# -----------------------------
# (Optional) Back-compat endpoint: one-shot raw + ai
# Keep it if you still call /api/dmr/run-auto-ai anywhere.
# -----------------------------
@app.post("/api/dmr/run-auto-ai")
async def dmr_run_auto_ai(request: Request, db: Session = Depends(get_db)):
    # raw first
    raw_resp = await dmr_run_raw(request, db)

    # raw_resp is a JSONResponse; pull body safely
    # Easiest: re-run json parsing from the request payload is messy;
    # Instead just ask client to use step 1 + step 2. But for back-compat:
    try:
        raw = raw_resp.body
        # raw_resp.body is bytes
        import json
        dmr_raw = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        dmr_raw = {}

    if not isinstance(dmr_raw, dict) or not dmr_raw:
        raise HTTPException(status_code=500, detail="Missing DMR context")

    # ai second
    fake_payload = {"dmr_raw": dmr_raw}
    # call generate directly without HTTP roundtrip
    class _TmpReq:
        def __init__(self, req: Request, payload: dict):
            self._req = req
            self.session = req.session
            self.headers = req.headers
            self.state = req.state
            self.scope = req.scope
            self._payload = payload
        async def json(self):
            return self._payload

    tmp_req = _TmpReq(request, fake_payload)
    ai_resp = await dmr_generate_ai(tmp_req, db)

    try:
        import json
        ai_data = json.loads(ai_resp.body.decode("utf-8"))
    except Exception:
        ai_data = {}

    # merge for legacy UI
    dmr_raw["report_text"] = ai_data.get("report_text", "")
    return JSONResponse(dmr_raw)
