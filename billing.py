# billing.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import stripe
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

stripe.api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()

PRICE_TACTICAL = (os.getenv("STRIPE_PRICE_TACTICAL") or "").strip()
PRICE_ELITE = (os.getenv("STRIPE_PRICE_ELITE") or "").strip()
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")


def _require_stripe() -> None:
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured (missing STRIPE_SECRET_KEY).")


def _require_prices() -> None:
    if not PRICE_TACTICAL:
        raise HTTPException(status_code=500, detail="Missing STRIPE_PRICE_TACTICAL.")
    if not PRICE_ELITE:
        raise HTTPException(status_code=500, detail="Missing STRIPE_PRICE_ELITE.")


def price_for_plan(plan: str) -> str:
    _require_prices()
    p = (plan or "").strip().lower()
    if p == "tactical":
        return PRICE_TACTICAL
    if p == "elite":
        return PRICE_ELITE
    raise HTTPException(status_code=400, detail="Invalid plan (must be tactical or elite).")


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


def create_checkout_session(db: Session, user_model, plan: str) -> str:
    """
    Sends user to Stripe-hosted checkout to start a subscription.

    If the user already has an active subscription, we redirect them to Billing Portal instead
    (avoids accidental double subscriptions, and supports upgrades/downgrades cleanly in Stripe).
    """
    _require_stripe()
    customer_id = ensure_customer(db, user_model)

    # If already subscribed and active/trialing, use portal.
    sub_status = (getattr(user_model, "subscription_status", None) or "").strip().lower()
    if sub_status in ("active", "trialing"):
        return create_billing_portal(db, user_model)

    price_id = price_for_plan(plan)

    success = f"{PUBLIC_BASE_URL}/suite?billing=success"
    cancel = f"{PUBLIC_BASE_URL}/pricing?billing=cancel"

    sess = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success,
        cancel_url=cancel,
        allow_promotion_codes=True,
        client_reference_id=str(user_model.id),
        subscription_data={"metadata": {"user_id": str(user_model.id)}},
    )
    return sess["url"]


def create_billing_portal(db: Session, user_model) -> str:
    """
    Stripe Billing Portal handles:
      - upgrade Tactical -> Elite
      - downgrade Elite -> Tactical (if you enable it in portal settings)
      - cancellations, payment method changes
    """
    _require_stripe()
    customer_id = ensure_customer(db, user_model)

    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{PUBLIC_BASE_URL}/suite",
    )
    return portal["url"]


def _extract_subscription_price_id(sub_obj: Dict[str, Any]) -> Optional[str]:
    try:
        items = sub_obj.get("items", {}).get("data", [])
        if items and items[0].get("price"):
            return items[0]["price"].get("id")
    except Exception:
        return None
    return None


def handle_webhook(payload: bytes, sig_header: str, db: Session, UserModel):
    """
    Stripe webhook updates the user record with Stripe-authoritative facts:
      - stripe_subscription_id
      - stripe_price_id
      - subscription_status

    Access gating is based on these fields only.
    """
    _require_stripe()

    secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not secret:
        raise HTTPException(status_code=500, detail="Missing STRIPE_WEBHOOK_SECRET.")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid webhook: {e}")

    etype = event["type"]
    obj = event["data"]["object"]

    # We accept checkout completion as informational; subscription events are authoritative.
    if etype == "checkout.session.completed":
        return {"ok": True}

    if etype in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        customer_id = obj.get("customer")
        if not customer_id:
            return {"ok": True}

        u = db.query(UserModel).filter(UserModel.stripe_customer_id == customer_id).first()
        if not u:
            return {"ok": True}

        sub_id = obj.get("id")
        status = (obj.get("status") or "").strip().lower()
        price_id = _extract_subscription_price_id(obj)

        u.stripe_subscription_id = sub_id
        u.subscription_status = status
        u.stripe_price_id = price_id

        # Optional: keep legacy tier column stable but irrelevant (do not use for gating)
        # This prevents older UI assumptions from crashing while we migrate templates.
        try:
            u.tier = "stripe"
        except Exception:
            pass

        db.commit()
        return {"ok": True}

    return {"ok": True}
