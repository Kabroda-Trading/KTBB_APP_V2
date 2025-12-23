# billing.py
from __future__ import annotations
import os
import stripe
from fastapi import HTTPException
from sqlalchemy.orm import Session

# Load keys
stripe.api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
PRICE_KTBB = (os.getenv("STRIPE_PRICE_KTBB") or "").strip()
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")

def ensure_customer(db: Session, user_model) -> str:
    """
    Ensures the user has a Stripe Customer ID.
    """
    if not stripe.api_key: 
        raise HTTPException(500, detail="Stripe config missing.")
    
    if user_model.stripe_customer_id: 
        return str(user_model.stripe_customer_id)
    
    # Create new customer in Stripe
    cust = stripe.Customer.create(
        email=user_model.email, 
        metadata={"user_id": str(user_model.id)}
    )
    user_model.stripe_customer_id = cust["id"]
    db.commit()
    return cust["id"]

def create_checkout_session(db: Session, user_model) -> str:
    """
    Creates a Stripe Checkout Session.
    
    INSTITUTIONAL LOGIC:
    1. Look up the Price.
    2. Expand the 'Product' data to find your 'trial_days' tag.
    3. Apply the trial if the tag exists.
    """
    if not PRICE_KTBB: 
        raise HTTPException(500, detail="Price ID missing.")
    
    cust_id = ensure_customer(db, user_model)
    
    # Check subscription status
    sub = (getattr(user_model, "subscription_status", "") or "").lower()
    if sub in ("active", "trialing"): 
        return create_billing_portal(db, user_model)

    # --- DYNAMIC TRIAL LOGIC (PRODUCT LEVEL) ---
    trial_days = 0
    try:
        # We ask Stripe for the Price AND the Product details in one call
        price_obj = stripe.Price.retrieve(PRICE_KTBB, expand=['product'])
        
        # We check the PRODUCT metadata for the rule
        # This is robust: change the price later, and the trial rule persists.
        product_meta = price_obj.product.metadata or {}
        trial_days_str = product_meta.get("trial_days", "0")
        trial_days = int(trial_days_str)
    except Exception:
        trial_days = 0
    
    checkout_args = {
        "mode": "subscription",
        "customer": cust_id,
        "line_items": [{"price": PRICE_KTBB, "quantity": 1}],
        "success_url": f"{PUBLIC_BASE_URL}/suite?billing=success",
        "cancel_url": f"{PUBLIC_BASE_URL}/pricing?billing=cancel",
        "client_reference_id": str(user_model.id),
        "allow_promotion_codes": True,
    }

    # Only apply trial if Metadata > 0
    if trial_days > 0:
        checkout_args["subscription_data"] = {"trial_period_days": trial_days}

    sess = stripe.checkout.Session.create(**checkout_args)
    return sess["url"]

def create_billing_portal(db: Session, user_model) -> str:
    """
    Creates a link to the self-serve Billing Portal.
    """
    cust_id = ensure_customer(db, user_model)
    portal = stripe.billing_portal.Session.create(
        customer=cust_id, 
        return_url=f"{PUBLIC_BASE_URL}/suite"
    )
    return portal["url"]

def handle_webhook(payload, sig_header, db, UserModel):
    """
    Processes signals from Stripe.
    """
    event = None
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, os.getenv("STRIPE_WEBHOOK_SECRET") or ""
        )
    except ValueError:
        return {} 
    except stripe.error.SignatureVerificationError:
        return {} 

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