# membership.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, status

# Single-plan Stripe price ID (source of truth)
PRICE_KTBB = (os.getenv("STRIPE_PRICE_KTBB") or "").strip()

ACTIVE_STATUSES = {"active", "trialing"}


@dataclass(frozen=True)
class MembershipState:
    """
    Stripe-authoritative membership state.

    - Paid access is derived exclusively from subscription_status.
    - Plan/tier is intentionally collapsed into ONE product.
    """
    is_paid: bool
    plan: Optional[str]  # "ktbb" | None
    label: str


def get_membership_state(user_model) -> MembershipState:
    sub_status = (getattr(user_model, "subscription_status", None) or "").strip().lower()
    price_id = (getattr(user_model, "stripe_price_id", None) or "").strip()

    is_paid = sub_status in ACTIVE_STATUSES
    if not is_paid:
        return MembershipState(is_paid=False, plan=None, label="No active subscription")

    # If you want to strictly enforce the exact Stripe price ID, keep this check.
    # If you want to allow any active subscription, remove the PRICE_KTBB check.
    if PRICE_KTBB and price_id != PRICE_KTBB:
        return MembershipState(is_paid=True, plan=None, label="Active subscription (Unrecognized plan)")

    return MembershipState(is_paid=True, plan="ktbb", label="Kabroda Trading BattleBox")


def require_paid_access(user_model) -> MembershipState:
    ms = get_membership_state(user_model)
    if not ms.is_paid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Subscription required to unlock the Suite.",
        )
    # If strict plan enforcement is enabled and plan is unknown, block suite.
    if ms.plan is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Subscription not recognized. Please contact support.",
        )
    return ms


def ensure_symbol_allowed(user_model, symbol: str) -> None:
    """
    Single plan: allow whichever symbols your engine supports.
    Keep this as a simple guard (still requires paid access).
    """
    require_paid_access(user_model)

    sym = (symbol or "").upper().strip()
    if not sym:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing symbol")
