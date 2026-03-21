# billing.py
# ==============================================================================
# KABRODA BILLING ENGINE (WHOP LISTENER)
# ==============================================================================
import os
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from database import get_db, UserModel

router = APIRouter()

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
    action = payload.get("action", "")
    data = payload.get("data", {})
    
    # Whop sends the user object inside the data payload
    user_info = data.get("user", {})
    raw_email = user_info.get("email")

    if not raw_email:
        print("⚠️ WHOP WEBHOOK IGNORED: No email found in payload.")
        return {"status": "ignored", "reason": "no_email_provided"}

    # AUDIT FIX: Strictly lowercase and strip the email so it perfectly matches the database
    user_email = raw_email.strip().lower()

    print(f">>> WHOP SIGNAL RECEIVED: {action} | Target Email: {user_email}")

    # 2. LOCATE USER IN KABRODA DB
    user = db.query(UserModel).filter(UserModel.email == user_email).first()

    if not user:
        print(f"❌ WHOP WEBHOOK FAILED: User {user_email} not found in Kabroda database.")
        return {"status": "user_not_found"}

    # 3. APPLY LOGIC (GRANT/REVOKE ACCESS)
    
    # CASE A: ACCESS GRANTED (New Sub, Renewal, Upgrade)
    if action in ["membership.went_active", "payment.succeeded"]:
        user.subscription_status = "active"
        user.tier = "pro"  # Redundancy upgrade
        db.commit()
        print(f"✅ ACCESS GRANTED: {user.email} is now ACTIVE and upgraded to PRO.")

    # CASE B: ACCESS REVOKED (Cancel, Expire, Payment Fail)
    elif action in ["membership.went_cancelled", "membership.expired"]:
        user.subscription_status = "inactive"
        user.tier = "basic"
        db.commit()
        print(f"🛑 ACCESS REVOKED: {user.email} is now INACTIVE.")

    return {"status": "processed"}