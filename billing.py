# billing.py
from __future__ import annotations

import os
from typing import Optional

import stripe
from fastapi import HTTPException
from sqlalchemy.orm import Session

stripe.api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()

PRICE_KTBB = (os.getenv("STRIPE_PRICE_KTBB") or "").strip()
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")


def _require_stripe() -> None:
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured (missing STRIPE_SECRET_KEY).")


def _require_price() -> None:
    if not PRICE_KTBB:
        raise HTTPException(status_code=500, detail="Missing STRIPE_PRICE_KTBB.")


def ensure_customer(db: Session, user_model) -> str:
    _require_stripe()

    existing = getattr(user_model, "stripe_customer_id", None)
    if existing:
        return str(existing)

    cust = stripe.Customer.create(
        email=user_model.email,
        metadata={"user_id": str(user_model.id)},
    )
    user_model.stripe_customer_id = cust["id"]
    db.commit()
    return cust["id"]


def create_checkout_session(db: Session, user_model) -> str:
    """
    Single-plan subscription checkout.
    If user is already active/trialing, send to Billing Portal instead.
    """
    _require_stripe()
    _require_price()

    customer_id = ensure_customer(db, user_model)

    sub_status = (getattr(user_model, "subscription_status", None) or "").strip().lower()
    if sub_status in ("active", "trialing"):
        return create_billing_portal(db, user_model)

    success = f"{PUBLIC_BASE_URL}/suite?billing=success"
    cancel = f"{PUBLIC_BASE_URL}/pricing?billing=cancel"

    sess = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": PRICE_KTBB, "quantity": 1}],
        success_url=success,
        cancel_url=cancel,
        allow_promotion_codes=True,
        client_reference_id=str(user_model.id),
        subscription_data={"metadata": {"user_id": str(user_model.id)}},
    )
    return sess["url"]


def create_billing_portal(db: Session, user_model) -> str:
    _require_stripe()
    customer_id = ensure_customer(db, user_model)

    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{PUBLIC_BASE_URL}/suite",
    )
    return portal["url"]
