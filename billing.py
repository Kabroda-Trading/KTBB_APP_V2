# billing.py
# ==============================================================================
# KABRODA BILLING ENGINE (WHOP LISTENER)
# ==============================================================================
# Replaces Stripe logic with Whop Webhook logic.
# Database interaction remains identical to preserve system stability.
# ==============================================================================

import os
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from database import get_db, UserModel

router = APIRouter()

# SECURITY: Matches the key you put in Render Environment
WHOP_SECRET = os.getenv("WHOP_WEBHOOK_SECRET")

@router.post("/whop-webhook")
async def whop_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receives secure signals from Whop when a user pays or cancels.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # 1. EXTRACT DATA
    action = payload.get("action")
    data = payload.get("data", {})
    
    # Whop sends the user object inside the data payload
    user_info = data.get("user", {})
    user_email = user_info.get("email")

    # Safety check: If Whop sends a ping without an email, ignore it.
    if not user_email:
        return {"status": "ignored", "reason": "no_email_provided"}

    print(f">>> WHOP SIGNAL RECEIVED: {action} | User: {user_email}")

    # 2. LOCATE USER IN KABRODA DB
    user = db.query(UserModel).filter(UserModel.email == user_email).first()

    if not user:
        print(f"⚠️  USER NOT FOUND: {user_email}")
        # They paid but haven't registered yet. 
        # The system will simply wait for them to register with this email.
        return {"status": "user_not_found"}

    # 3. APPLY LOGIC (GRANT/REVOKE ACCESS)
    
    # CASE A: ACCESS GRANTED (New Sub, Renewal, Upgrade)
    if action in ["membership.went_active", "payment.succeeded"]:
        user.subscription_status = "active"
        db.commit()
        print(f"✅  ACCESS GRANTED: {user.email} is now ACTIVE.")

    # CASE B: ACCESS REVOKED (Cancel, Expire, Payment Fail)
    elif action in ["membership.went_cancelled", "membership.expired"]:
        user.subscription_status = "inactive"
        db.commit()
        print(f"❌  ACCESS REVOKED: {user.email} is now INACTIVE.")

    return {"status": "processed"}