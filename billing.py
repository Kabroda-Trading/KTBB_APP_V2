# billing.py
import os
from typing import Dict, Any

import stripe
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from membership import Tier

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

PRICE_TACTICAL = os.getenv("STRIPE_PRICE_TACTICAL", "")
PRICE_ELITE = os.getenv("STRIPE_PRICE_ELITE", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def _require_stripe():
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured (missing STRIPE_SECRET_KEY).")


def tier_for_price(price_id: str) -> Tier:
    if price_id == PRICE_ELITE:
        return Tier.TIER3_MULTI_GPT
    if price_id == PRICE_TACTICAL:
        return Tier.TIER2_SINGLE_AUTO
    return Tier.TIER2_SINGLE_AUTO


def price_for_tier(tier: str) -> str:
    if tier in ("elite", "tier3_multi_gpt", "tier3_elite_gpt"):
        if not PRICE_ELITE:
            raise HTTPException(status_code=500, detail="Missing STRIPE_PRICE_ELITE.")
        return PRICE_ELITE
    if tier in ("tactical", "tier2_single_auto"):
        if not PRICE_TACTICAL:
            raise HTTPException(status_code=500, detail="Missing STRIPE_PRICE_TACTICAL.")
        return PRICE_TACTICAL
    raise HTTPException(status_code=400, detail="Invalid tier")


def ensure_customer(db: Session, user_model) -> str:
    _require_stripe()
    if getattr(user_model, "stripe_customer_id", None):
        return user_model.stripe_customer_id

    cust = stripe.Customer.create(email=user_model.email, metadata={"user_id": str(user_model.id)})
    user_model.stripe_customer_id = cust["id"]
    db.commit()
    return cust["id"]


def create_checkout_session(db: Session, user_model, tier_slug: str) -> str:
    _require_stripe()
    customer_id = ensure_customer(db, user_model)
    price_id = price_for_tier(tier_slug)

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
    _require_stripe()
    customer_id = ensure_customer(db, user_model)
    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{PUBLIC_BASE_URL}/suite",
    )
    return portal["url"]


def handle_webhook(request: Request, payload: bytes, sig_header: str, db: Session, UserModel):
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(status_code=500, detail="Missing STRIPE_WEBHOOK_SECRET.")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid webhook: {e}")

    etype = event["type"]
    obj = event["data"]["object"]

    if etype in ("checkout.session.completed",):
        return {"ok": True}

    if etype in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        customer_id = obj.get("customer")
        status = obj.get("status")
        sub_id = obj.get("id")

        items = obj.get("items", {}).get("data", [])
        price_id = None
        if items and items[0].get("price"):
            price_id = items[0]["price"].get("id")

        if not customer_id:
            return {"ok": True}

        u = db.query(UserModel).filter(UserModel.stripe_customer_id == customer_id).first()
        if not u:
            return {"ok": True}

        u.stripe_subscription_id = sub_id
        u.stripe_price_id = price_id
        u.subscription_status = status

        if etype == "customer.subscription.deleted" or status in ("canceled", "unpaid", "incomplete_expired"):
            u.tier = Tier.TIER2_SINGLE_AUTO.value
        else:
            if price_id:
                u.tier = tier_for_price(price_id).value

        db.commit()
        return {"ok": True}

    return {"ok": True}
