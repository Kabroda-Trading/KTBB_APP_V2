# billing.py
from __future__ import annotations
import os
import stripe
from fastapi import HTTPException
from sqlalchemy.orm import Session

stripe.api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")

# Load Price IDs from Env
PRICES = {
    "monthly": os.getenv("STRIPE_PRICE_MONTHLY", ""),
    "semi":    os.getenv("STRIPE_PRICE_SEMI", ""),
    "annual":  os.getenv("STRIPE_PRICE_ANNUAL", "")
}

def ensure_customer(db: Session, user_model) -> str:
    if not stripe.api_key: raise HTTPException(500, detail="Stripe config missing.")
    if user_model.stripe_customer_id: return str(user_model.stripe_customer_id)
    
    cust = stripe.Customer.create(email=user_model.email, metadata={"user_id": str(user_model.id)})
    user_model.stripe_customer_id = cust["id"]
    db.commit()
    return cust["id"]

def create_checkout_session(db: Session, user_model, plan_key: str = "monthly") -> str:
    """
    Creates a Checkout Session for the selected plan.
    Default trial is ALWAYS 7 days for new users.
    """
    price_id = PRICES.get(plan_key)
    if not price_id:
        # Fallback to monthly if key is wrong, or error out
        price_id = PRICES.get("monthly")
    
    if not price_id:
        raise HTTPException(500, detail=f"Price Configuration Missing for {plan_key}.")

    cust_id = ensure_customer(db, user_model)
    
    # If already active, send to portal
    sub = (getattr(user_model, "subscription_status", "") or "").lower()
    if sub in ("active", "trialing"): 
        return create_billing_portal(db, user_model)

    checkout_args = {
        "mode": "subscription",
        "customer": cust_id,
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": f"{PUBLIC_BASE_URL}/suite?billing=success",
        # FIXED: Cancel goes to Homepage as requested
        "cancel_url": f"{PUBLIC_BASE_URL}/", 
        "client_reference_id": str(user_model.id),
        "allow_promotion_codes": True,
        "subscription_data": {"trial_period_days": 7} # GLOBAL 7-DAY TRIAL
    }

    sess = stripe.checkout.Session.create(**checkout_args)
    return sess["url"]

def create_billing_portal(db: Session, user_model) -> str:
    cust_id = ensure_customer(db, user_model)
    # FIXED: Return to Account page (safer UX)
    portal = stripe.billing_portal.Session.create(customer=cust_id, return_url=f"{PUBLIC_BASE_URL}/account")
    return portal["url"]

def handle_webhook(payload, sig_header, db, UserModel):
    event = None
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, os.getenv("STRIPE_WEBHOOK_SECRET") or "")
    except: return {} 

    data = event["data"]["object"]
    event_type = event["type"]

    if event_type == "checkout.session.completed":
        uid = data.get("client_reference_id")
        if uid:
            u = db.query(UserModel).filter(UserModel.id == uid).first()
            if u:
                u.stripe_customer_id = data.get("customer")
                db.commit()

    elif event_type in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        cust_id = data.get("customer")
        u = db.query(UserModel).filter(UserModel.stripe_customer_id == cust_id).first()
        if u:
            u.subscription_status = data.get("status") 
            db.commit()

    return {"ok": True}