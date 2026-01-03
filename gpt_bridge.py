# gpt_bridge.py
# Handles OAuth2 Handshake and Multi-Session Data Delivery
from __future__ import annotations
import time
import secrets
from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime

# Import core logic
import dmr_report
import data_feed
from database import get_db, UserModel
from membership import require_paid_access, get_membership_state

# --- CONFIGURATION ---
GPT_CLIENT_ID = "kabroda-battlebox-gpt" 
GPT_CLIENT_SECRET = "kb-secret-change-this-in-prod" 
router = APIRouter(prefix="/api/v1")

# --- 1. OAUTH2 HANDSHAKE ---
@router.get("/oauth/authorize")
async def oauth_authorize(request: Request, client_id: str, redirect_uri: str, state: str):
    if client_id != GPT_CLIENT_ID:
        return JSONResponse({"error": "invalid_client"}, status_code=400)

    user = request.session.get("user")
    if not user:
        return RedirectResponse(f"/login?next={request.url}", status_code=303)
    
    auth_code = f"{user['id']}:{int(time.time())}:{secrets.token_hex(4)}"
    return RedirectResponse(f"{redirect_uri}?code={auth_code}&state={state}", status_code=303)

@router.post("/oauth/token")
async def oauth_token(code: str = Form(...), client_id: str = Form(...), client_secret: str = Form(...)):
    if client_id != GPT_CLIENT_ID or client_secret != GPT_CLIENT_SECRET:
        raise HTTPException(status_code=401, detail="Invalid Credentials")

    try:
        user_id_str, timestamp_str, _ = code.split(":")
        if time.time() - int(timestamp_str) > 600:
            raise HTTPException(status_code=400, detail="Code expired")
        user_id = int(user_id_str)
    except:
        raise HTTPException(status_code=400, detail="Invalid code")

    access_token = f"kb-token-{user_id}-{secrets.token_hex(16)}"
    return {"access_token": access_token, "token_type": "bearer", "expires_in": 2592000}

# --- 2. AUTH HELPER ---
def get_current_user_from_token(request: Request, db: Session = Depends(get_db)):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer kb-token-"):
        raise HTTPException(status_code=401, detail="Invalid Token")
    
    try:
        token = auth_header.split(" ")[1]
        user_id = int(token.split("-")[2])
        user = db.query(UserModel).filter(UserModel.id == user_id).first()
        if not user: raise Exception()
        require_paid_access(user)
        return user
    except:
        raise HTTPException(status_code=403, detail="Unauthorized")

# --- 3. DATA ENDPOINTS (With Session Switching) ---
@router.get("/payload/daily")
async def get_daily_payload(
    symbol: str = "BTCUSDT", 
    session_tz: Optional[str] = None, # <--- NEW: Allows GPT to ask for specific sessions
    user: UserModel = Depends(get_current_user_from_token)
):
    # Priority: GPT Request > User Preference > Default NY
    target_tz = session_tz or user.session_tz or "America/New_York"
    
    inputs = await data_feed.get_inputs(symbol=symbol)
    raw_report = dmr_report.generate_report_from_inputs(inputs, target_tz)
    
    return {
        "contract_version": "1.2",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "user_tier": get_membership_state(user).plan,
        "symbol": raw_report["symbol"],
        "price": raw_report["last_price"],
        "session_tz": raw_report["session_tz"], # Confirming back to GPT which session ran
        "levels": raw_report["levels"],
        "range_30m": raw_report["range_30m"],
        "trade_logic": raw_report["trade_logic"],
        "context_hint": f"Analyzing {target_tz} session. If user asked for a different session, use the session_tz parameter."
    }

@router.get("/telemetry/daily")
async def get_telemetry(symbol: str = "BTCUSDT", user: UserModel = Depends(get_current_user_from_token)):
    inputs = await data_feed.get_inputs(symbol=symbol)
    user_tz = user.session_tz or "America/New_York"
    raw = dmr_report.generate_report_from_inputs(inputs, user_tz)
    return {"symbol": symbol, "levels": raw["levels"]}

@router.get("/me")
async def get_me(user: UserModel = Depends(get_current_user_from_token)):
    return {"username": user.username, "status": "active"}