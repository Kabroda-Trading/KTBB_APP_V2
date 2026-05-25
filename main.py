# main.py
# ---------------------------------------------------------
# KABRODA UNIFIED SERVER: PRIVATE TEAM TERMINAL
# ---------------------------------------------------------
import os
import json 
import traceback
import re
from typing import Any, Dict, Optional
import asyncio
from contextlib import asynccontextmanager 

from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel

# --- CORE IMPORTS ---
import auth
import battlebox_pipeline
import market_radar
import research_lab
import market_simulator  
import gravity_engine  
import gravity_math
import kabroda_mas_flow
import ledger_closing_engine
import mtf_confluence_scanner
import agent_core

from database import init_db, get_db, UserModel, CampaignLog, SessionLock, AgentRunLog

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(">>> BOOTING KABRODA SYSTEM: Initializing Database Schema...")
    init_db()
    app.state.gravity_task = asyncio.create_task(gravity_engine.run_gravity_ingestion_loop())
    app.state.ledger_task = asyncio.create_task(ledger_closing_engine.run_ledger_audit_loop())
    yield
    print(">>> SHUTTING DOWN KABRODA SYSTEM...")
    app.state.gravity_task.cancel()
    app.state.ledger_task.cancel()

app = FastAPI(title="Kabroda BattleBox", version="12.0", lifespan=lifespan)

SECRET_KEY = os.getenv("SESSION_SECRET", "kabroda_prod_key_999")

def _bool_env(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None: return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
IS_HTTPS = PUBLIC_BASE_URL.startswith("https://")
SESSION_HTTPS_ONLY = _bool_env("SESSION_HTTPS_ONLY", default=IS_HTTPS)

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    https_only=SESSION_HTTPS_ONLY,
    same_site="lax",
    max_age=86400 * 30  
)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

app.include_router(auth.router)

def _template_or_fallback(request: Request, templates: Jinja2Templates, name: str, context: Dict[str, Any]):
    try: 
        return templates.TemplateResponse(name, context)
    except Exception as e: 
        return HTMLResponse(f"<h2>System Error: {name}</h2><p>{str(e)}</p>", status_code=500)

def get_user_context(request: Request, db: Session):
    uid = request.session.get(auth.SESSION_KEY)
    base_context = {"request": request}
    
    if not uid: 
        base_context.update({"is_logged_in": False, "is_admin": False})
        return base_context
        
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    base_context.update({
        "is_logged_in": True,
        "is_admin": getattr(user, "is_admin", False) if user else False,
        "username": getattr(user, "username", "Operative") if user else "Operative",
        "email": getattr(user, "email", "") if user else "",
        "user": user
    })
    return base_context

# --- PUBLIC ROUTES (LOCKED DOWN) ---
@app.get("/")
async def home(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if ctx["is_logged_in"]:
        return RedirectResponse(url="/suite", status_code=303)
    return RedirectResponse(url="/login", status_code=303)

# --- SUITE ROUTES ---
@app.get("/suite")
async def suite(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "session_control.html", ctx)

@app.get("/suite/battle-control")
async def battle_control_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "suite_home.html", ctx)

@app.get("/suite/research-lab")
async def suite_research_lab_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "research_lab.html", ctx)

@app.get("/suite/radar")
async def radar_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "market_radar.html", ctx)

@app.get("/suite/gravity-map")
async def gravity_map_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "gravity_map.html", ctx)

@app.get("/suite/macro-war-room")
async def macro_war_room_page(request: Request, symbol: str = "BTC/USDT", db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    
    db_sym = symbol.replace("USDT", "/USDT") if "/" not in symbol else symbol
    latest_log = db.query(CampaignLog).filter(CampaignLog.symbol == db_sym).order_by(CampaignLog.id.desc()).first()
    
    if latest_log and not latest_log.mas_executive_brief and latest_log.mas_approval_status == 'PENDING':
        lock_record = db.query(SessionLock).filter(
            SessionLock.symbol == db_sym,
            SessionLock.session_id == latest_log.session_id,
            SessionLock.date_key == latest_log.date_key
        ).first()

        if lock_record:
            pkt = json.loads(lock_record.packet_data)
            asyncio.create_task(
                asyncio.to_thread(
                    kabroda_mas_flow.run_mas_analysis,
                    symbol=db_sym,
                    session_id=latest_log.session_id,
                    date_key=latest_log.date_key,
                    battlebox_payload=pkt
                )
            )
    
    ctx["mas_log"] = latest_log
    return _template_or_fallback(request, templates, "macro_war_room.html", ctx)

# --- GRAVITY API ENDPOINT ---
@app.get("/api/gravity/scan")
async def api_gravity_scan(symbol: str = "BTC/USDT"):
    candles_1d = await battlebox_pipeline.fetch_live_daily(symbol, limit=30)
    candles_15m = await battlebox_pipeline.fetch_live_15m(symbol, limit=300)
    
    kde_data = gravity_math.calculate_gravity_kde(symbol)
    macro_fibs = gravity_math.calculate_macro_fibs(candles_1d, candles_15m)
    
    return JSONResponse({
        "ok": True, 
        "symbol": symbol, 
        "kde_data": kde_data, 
        "macro_fibs": macro_fibs
    })

# --- KABRODA ARCHITECTURE: FOREIGN INTEL PARSER & MAS ROUTING ---
class ForeignIntelPayload(BaseModel):
    raw_text: str

class MASChatPayload(BaseModel):
    symbol: str
    message: str

@app.post("/api/research/chat-mas")
async def chat_with_mas(payload: MASChatPayload, request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"): 
        return JSONResponse({"ok": False, "error": "Unauthorized"})
    
    db_sym = payload.symbol.replace("USDT", "/USDT") if "/" not in payload.symbol else payload.symbol
    response_text = await asyncio.to_thread(kabroda_mas_flow.interrogate_cro, db_sym, payload.message)
    
    return JSONResponse({"ok": True, "reply": response_text})

@app.post("/api/research/audit-intel")
async def audit_foreign_intel(payload: ForeignIntelPayload, request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_logged_in"): 
        return JSONResponse({"ok": False, "error": "Unauthorized"})

    text = payload.raw_text
    try:
        header_match = re.search(r'([A-Z]+)\s*\|\s*([A-Z]+)\s*@\s*\$([\d,.]+)', text)
        asset = f"{header_match.group(1)}/{header_match.group(2)}"
        entry_price = float(header_match.group(3).replace(',', ''))
        
        t1 = float(re.search(r'Target 1:\s*([\d,.]+)', text).group(1).replace(',', ''))
        t2 = float(re.search(r'Target 2:\s*([\d,.]+)', text).group(1).replace(',', ''))
        t3 = float(re.search(r'Target 3:\s*([\d,.]+)', text).group(1).replace(',', ''))
        sl = float(re.search(r'SL Close Below:\s*([\d,.]+)', text).group(1).replace(',', ''))

        bias = "LONG" if t1 > entry_price else "SHORT"

        # Timeframe lives in the MetaSignals header, e.g. "BTC | USDT @ $76,821.20 - 1H - 1.1 G1"
        tf_match = re.search(r'@\s*\$[\d,.]+\s*-\s*(\d+\s*[HMDWhmdw])', text)
        timeframe = tf_match.group(1).replace(' ', '').upper() if tf_match else "UNKNOWN"

        parsed_packet = {
            "source": "MetaSignals",
            "symbol": asset,
            "bias": bias,
            "timeframe": timeframe,
            "entry_price": entry_price,
            "targets": [t1, t2, t3],
            "stop_loss": sl
        }

        db_sym = asset.replace("USDT", "/USDT") if "/" not in asset else asset
        
        lock_record = db.query(SessionLock).filter(
            SessionLock.symbol == db_sym
        ).order_by(SessionLock.id.desc()).first()
                
        if not lock_record:
            return JSONResponse({"ok": False, "error": f"No active Kabroda session locked for {asset} in DB. Cannot perform audit."})

        current_ssot = json.loads(lock_record.packet_data)

        # Third data source: live multi-timeframe confluence for the momentum audit.
        try:
            mtf_context = await mtf_confluence_scanner.run_mtf_confluence_scan(db_sym)
        except Exception as mtf_err:
            print(f"[AUDIT MTF ERROR] {db_sym}: {mtf_err}")
            mtf_context = {"error": str(mtf_err)}

        audit_result = await asyncio.to_thread(
            kabroda_mas_flow.audit_foreign_intel_pipeline,
            parsed_packet, current_ssot, mtf_context
        )
        
        if audit_result["status"] == "SUCCESS":
            return JSONResponse({
                "ok": True, 
                "message": "Intel audited successfully.", 
                "data": parsed_packet,
                "audit": audit_result["report"]
            })
        else:
            return JSONResponse({"ok": False, "error": "Agent failed to analyze intel. See server logs."})

    except Exception as e:
        return JSONResponse({
            "ok": False, 
            "error": "Failed to parse intel. Ensure the text perfectly matches the MetaSignals format."
        })

# --- AGENT COST INFRASTRUCTURE (PHASE 1) ---

@app.get("/api/agents/cost")
async def api_agents_cost(request: Request, db: Session = Depends(get_db)):
    """Returns 24h and 7-day agent spend summary. Admin only."""
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)
    summary = await asyncio.to_thread(agent_core.get_cost_summary)
    return JSONResponse(summary)


@app.post("/api/agents/test-call")
async def api_agents_test_call(request: Request, db: Session = Depends(get_db)):
    """
    Phase 1 success test. Fires one minimal _call_agent() invocation,
    writes a row to agent_run_log, and returns the response + cost.
    Admin only.
    """
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"):
        return JSONResponse({"ok": False, "error": "Admin only."}, status_code=403)

    system_prompt = (
        "You are a cost-tracking verification agent for the Kabroda trading "
        "intelligence system. Your only function is to confirm that the Phase 1 "
        "cost infrastructure is operational."
    )
    context_text = (
        "Confirm system status. "
        "Respond with exactly one line: PHASE_1_COST_INFRASTRUCTURE_ONLINE"
    )

    try:
        result = await asyncio.to_thread(
            agent_core._call_agent,
            "infrastructure_test",
            system_prompt,
            context_text,
            "admin_test",
        )
        summary = await asyncio.to_thread(agent_core.get_cost_summary)
        last_call = summary.get("last_10_calls", [{}])[0]
        return JSONResponse({
            "ok": True,
            "agent_response": result,
            "logged_row": last_call,
            "next_step": "Visit /api/agents/cost to see full summary.",
        })
    except RuntimeError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=402)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/indicators")
async def indicators(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "indicators.html", ctx)

@app.get("/account")
async def account(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_logged_in"]: return RedirectResponse(url="/login", status_code=303)
    return _template_or_fallback(request, templates, "account.html", ctx)

@app.post("/account/profile")
async def update_profile(request: Request, payload: Dict[str, Any], db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    if user:
        if "username" in payload: user.username = str(payload["username"]).strip()[:50]
        if "tradingview_id" in payload: user.tradingview_id = str(payload["tradingview_id"]).strip()
        if "session_tz" in payload: user.session_tz = str(payload["session_tz"]).strip()
        db.commit()
    return {"status": "ok", "ok": True}

@app.post("/account/password")
async def update_password(request: Request, payload: Dict[str, Any], db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    new_pass = payload.get("password")
    if not new_pass: return JSONResponse({"ok": False, "error": "No password"}, status_code=400)
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    if user:
        user.password_hash = auth.hash_password(new_pass)
        db.commit()
    return {"ok": True}

@app.post("/account/settings")
async def account_settings(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    data = await request.json()
    if user:
        user.operator_flex = bool(data.get("operator_flex", False))
        db.commit()
    return {"status": "ok"}

# --- ADMIN ROUTES ---
@app.get("/admin/simulator")
async def admin_simulator_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    return _template_or_fallback(request, templates, "market_simulator.html", ctx)

@app.get("/admin/research")
async def admin_research_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    return _template_or_fallback(request, templates, "research_lab.html", ctx)

@app.get("/admin/mission")
async def mission_brief(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    return _template_or_fallback(request, templates, "mission_brief.html", ctx)

@app.get("/admin")
async def admin_roster_page(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx["is_admin"]: return RedirectResponse("/suite")
    users = db.query(UserModel).all()
    ctx["users"] = users
    return _template_or_fallback(request, templates, "admin.html", ctx)

@app.get("/admin/export-audit-ledger")
async def export_audit_ledger(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"): 
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=403)
        
    logs = db.query(CampaignLog).order_by(CampaignLog.created_at.desc()).all()
    audit_data = []
    
    for l in logs:
        try:
            diagnostics = json.loads(l.diagnostic_data) if l.diagnostic_data else {}
        except Exception:
            diagnostics = {}
            
        audit_data.append({
            "trade_id": l.id,
            "symbol": l.symbol,
            "date": l.date_key,
            "bias": l.bias,
            "status": l.status,
            "realized_pnl": l.realized_pnl,
            "diagnostics": diagnostics
        })
        
    return JSONResponse({"ok": True, "total_records": len(audit_data), "ledger": audit_data})

@app.post("/admin/delete-user")
async def admin_delete_user(request: Request, user_id: str = Form(...), db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"): return RedirectResponse("/suite")
    user_to_delete = db.query(UserModel).filter(UserModel.id == int(user_id)).first()
    if user_to_delete:
        db.delete(user_to_delete)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/toggle-role")
async def admin_toggle_role(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"): return JSONResponse({"ok": False, "error": "Unauthorized"})
    payload = await request.json()
    target_id = payload.get("user_id")
    user_to_toggle = db.query(UserModel).filter(UserModel.id == int(target_id)).first()
    if user_to_toggle:
        user_to_toggle.is_admin = not user_to_toggle.is_admin
        db.commit()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "User not found"})

@app.post("/admin/reset-password-manual")
async def admin_reset_password(request: Request, db: Session = Depends(get_db)):
    ctx = get_user_context(request, db)
    if not ctx.get("is_admin"): return JSONResponse({"ok": False, "error": "Unauthorized"})
    payload = await request.json()
    target_id = payload.get("user_id")
    new_password = payload.get("new_password")
    if not new_password: return JSONResponse({"ok": False, "error": "No password provided"})
    user = db.query(UserModel).filter(UserModel.id == int(target_id)).first()
    if user:
        user.password_hash = auth.hash_password(new_password)
        db.commit()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "User not found"})

# --- API EXECUTION ROUTES ---
@app.post("/api/dmr/run-raw")
async def dmr_run_raw(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    
    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    session_id = payload.get("session_id")
    
    if session_id:
        out = await battlebox_pipeline.get_session_review(symbol=symbol, session_id=session_id)
    else:
        out = await battlebox_pipeline.get_session_review(symbol=symbol)
    return JSONResponse(out)

@app.post("/api/dmr/live")
async def dmr_live(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    
    payload = await request.json()
    symbol = (payload.get("symbol") or "BTCUSDT").strip().upper()
    
    out = await battlebox_pipeline.get_live_battlebox(
        symbol=symbol,
        session_mode=(payload.get("session_mode") or "AUTO").upper(),
        manual_id=payload.get("manual_session_id") or payload.get("session_id"),
        operator_flex=getattr(user, "operator_flex", False)
    )
    return JSONResponse(out)

@app.post("/api/radar/scan")
async def run_radar_scan(request: Request):
    results = await market_radar.scan_sector()
    return {"ok": True, "results": results}

@app.post("/api/research/run")
async def research_run(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    
    payload = await request.json()
    try:
        out = await research_lab.run_research_lab(payload)
        return JSONResponse(out)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(e)})

@app.post("/api/simulator/run")
async def simulator_run(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get(auth.SESSION_KEY)
    if not uid: raise HTTPException(status_code=401)
    
    user = db.query(UserModel).filter(UserModel.id == uid).first()
    
    if not getattr(user, "is_admin", False): 
        return JSONResponse({"ok": False, "error": "Admin access required for heavy backtesting computations."}, status_code=403)
    
    payload = await request.json()
    try:
        out = await market_simulator.run_simulation(payload)
        return JSONResponse(out)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(e)})

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_trace = traceback.format_exc()
    print(f"CRITICAL CRASH:\n{error_trace}") 
    return HTMLResponse(
        content=f"""
        <div style="background-color: #0f172a; color: #ef4444; padding: 40px; font-family: 'JetBrains Mono', monospace; min-height: 100vh; box-sizing: border-box;">
            <h1 style="border-bottom: 2px solid #ef4444; padding-bottom: 10px; margin-top:0;">🚨 FATAL SYSTEM CRASH 🚨</h1>
            <p style="color: #cbd5e1; font-size: 14px;">The execution sequence failed. Here is the exact internal autopsy of the code:</p>
            <pre style="background: #020617; padding: 20px; border: 1px solid #334155; border-radius: 8px; overflow-x: auto; font-size: 12px; line-height: 1.5;">{error_trace}</pre>
        </div>
        """,
        status_code=500
    )