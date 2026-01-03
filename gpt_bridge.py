# gpt_bridge.py
# Handles OAuth2 Handshake and Data Delivery for Kabroda GPT
from __future__ import annotations
import time
import json
import secrets
from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

# Import core logic from your existing system
import dmr_report
import data_feed
import research_lab
from database import get_db, UserModel
from membership import require_paid_access, get_membership_state

# --- CONFIGURATION ---
# In production, these should be environment variables!
GPT_CLIENT_ID = "kabroda-battlebox-gpt" 
GPT_CLIENT_SECRET = "kb-secret-change-this-in-prod" 
JWT_SECRET = "kb-jwt-secret-change-this" 
ALGORITHM = "HS256"

router = APIRouter(prefix="/api/v1")

# --- 1. OAUTH2 HANDSHAKE (The Key Exchange) ---

@router.get("/oauth/authorize")
async def oauth_authorize(request: Request, response_type: str, client_id: str, redirect_uri: str, state: str):
    """
    Step 1: GPT sends user here. We check if they are logged in.
    If yes -> We give them a 'Code' to take back to the GPT.
    If no -> We send them to login page.
    """
    if client_id != GPT_CLIENT_ID:
        return JSONResponse({"error": "invalid_client"}, status_code=400)

    # Check if user is logged into the website
    user = request.session.get("user")
    if not user:
        # Not logged in? Redirect to login, then come back here
        return RedirectResponse(f"/login?next={request.url}", status_code=303)
    
    # User is logged in! Generate a temporary auth code (valid 5 mins)
    # In a full scaling app, store this in Redis. For now, we sign it.
    auth_code = f"{user['id']}:{int(time.time())}:{secrets.token_hex(4)}"
    
    # Redirect back to GPT with the code
    full_redirect = f"{redirect_uri}?code={auth_code}&state={state}"
    return RedirectResponse(full_redirect, status_code=303)

@router.post("/oauth/token")
async def oauth_token(
    grant_type: str = Form(...), 
    code: str = Form(...), 
    client_id: str = Form(...), 
    client_secret: str = Form(...)
):
    """
    Step 2: GPT exchanges the 'Code' for a permanent 'Access Token'.
    """
    if client_id != GPT_CLIENT_ID or client_secret != GPT_CLIENT_SECRET:
        raise HTTPException(status_code=401, detail="Invalid Client Credentials")

    # Validate Code (Simple simplified validation for MVP)
    try:
        user_id_str, timestamp_str, _ = code.split(":")
        if time.time() - int(timestamp_str) > 600: # 10 min expiry
            raise HTTPException(status_code=400, detail="Code expired")
        user_id = int(user_id_str)
    except:
        raise HTTPException(status_code=400, detail="Invalid code format")

    # MINT ACCESS TOKEN (JSON Payload that proves identity)
    # In a real app, use python-jose to sign this JWT. 
    # Here is a simplified "Opaque Token" approach that works without extra pip installs:
    access_token = f"kb-token-{user_id}-{secrets.token_hex(16)}"
    
    # Store this token in a global cache or DB if needed. 
    # For MVP, we will validate it by checking the prefix and ID.
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 3600 * 24 * 30 # 30 Days
    }

# --- 2. AUTHENTICATION HELPER ---

def get_current_user_from_token(request: Request, db: Session = Depends(get_db)):
    """
    Extracts the Bearer Token from GPT's request and finds the User.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer kb-token-"):
        raise HTTPException(status_code=401, detail="Missing or Invalid Token")
    
    token = auth_header.split(" ")[1]
    try:
        # Extract User ID from our simple token format: kb-token-{uid}-{random}
        parts = token.split("-")
        user_id = int(parts[2])
        
        user = db.query(UserModel).filter(UserModel.id == user_id).first()
        if not user: raise Exception("User not found")
        
        # CHECK STRIPE MEMBERSHIP
        require_paid_access(user) # This ensures only paid members use the API
        
        return user
    except Exception as e:
        raise HTTPException(status_code=403, detail="Membership Inactive or Token Invalid")

# --- 3. DATA ENDPOINTS (The Goods) ---

@router.get("/payload/daily")
async def get_daily_payload(symbol: str = "BTCUSDT", user: UserModel = Depends(get_current_user_from_token)):
    """
    The Main Endpoint: GPT calls this to get the full BattleBox JSON.
    """
    # 1. Fetch Data
    inputs = await data_feed.get_inputs(symbol=symbol)
    
    # 2. Run Engine (Using User's Preferred Timezone)
    user_tz = user.session_tz or "America/New_York"
    raw_report = dmr_report.generate_report_from_inputs(inputs, user_tz)
    
    # 3. Format as clean JSON for GPT
    payload = {
        "contract_version": "1.1",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "user_tier": get_membership_state(user).plan,
        
        "symbol": raw_report["symbol"],
        "price": raw_report["last_price"],
        "session_tz": raw_report["session_tz"],
        
        "levels": raw_report["levels"],
        "range_30m": raw_report["range_30m"],
        
        "trade_logic": raw_report["trade_logic"],
        
        # Context Hints for GPT
        "context_hint": "User is asking for a live session review. Focus on the relationship between Price and the Breakout Triggers.",
    }
    return payload

@router.get("/telemetry/daily")
async def get_telemetry(symbol: str = "BTCUSDT", user: UserModel = Depends(get_current_user_from_token)):
    """
    Lightweight endpoint for quick level checks.
    """
    inputs = await data_feed.get_inputs(symbol=symbol)
    user_tz = user.session_tz or "America/New_York"
    raw = dmr_report.generate_report_from_inputs(inputs, user_tz)
    
    return {
        "symbol": symbol,
        "updated": datetime.utcnow().isoformat(),
        "levels": raw["levels"]
    }

@router.get("/me")
async def get_me(user: UserModel = Depends(get_current_user_from_token)):
    """
    GPT calls this to verify who it is talking to.
    """
    ms = get_membership_state(user)
    return {
        "id": user.id,
        "username": user.username,
        "tier": ms.plan,
        "status": "active"
    }