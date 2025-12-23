# billing.py
from __future__ import annotations
import os
import stripe
from fastapi import HTTPException
from sqlalchemy.orm import Session

stripe.api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
PRICE_KTBB = (os.getenv("STRIPE_PRICE_KTBB") or "").strip()
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")

def ensure_customer(db: Session, user_model) -> str:
    if not stripe.api_key: raise HTTPException(500, detail="Stripe config missing.")
    if user_model.stripe_customer_id: return str(user_model.stripe_customer_id)
    cust = stripe.Customer.create(email=user_model.email, metadata={"user_id": str(user_model.id)})
    user_model.stripe_customer_id = cust["id"]
    db.commit()
    return cust["id"]

def create_checkout_session(db: Session, user_model) -> str:
    if not PRICE_KTBB: raise HTTPException(500, detail="Price ID missing.")
    cust_id = ensure_customer(db, user_model)
    sub = (getattr(user_model, "subscription_status", "") or "").lower()
    if sub in ("active", "trialing"): return create_billing_portal(db, user_model)

    sess = stripe.checkout.Session.create(
        mode="subscription", customer=cust_id,
        line_items=[{"price": PRICE_KTBB, "quantity": 1}],
        success_url=f"{PUBLIC_BASE_URL}/suite?billing=success",
        cancel_url=f"{PUBLIC_BASE_URL}/pricing?billing=cancel",
        client_reference_id=str(user_model.id),
    )
    return sess["url"]

def create_billing_portal(db: Session, user_model) -> str:
    cust_id = ensure_customer(db, user_model)
    portal = stripe.billing_portal.Session.create(customer=cust_id, return_url=f"{PUBLIC_BASE_URL}/suite")
    return portal["url"]

def handle_webhook(payload, sig_header, db, UserModel):
    # (Standard webhook logic assumed)
    return {"ok": True}