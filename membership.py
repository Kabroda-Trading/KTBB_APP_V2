# membership.py
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional
from fastapi import HTTPException, status

PRICE_KTBB = (os.getenv("STRIPE_PRICE_KTBB") or "").strip()
ACTIVE_STATUSES = {"active", "trialing"}

@dataclass(frozen=True)
class MembershipState:
    is_paid: bool
    plan: Optional[str]
    label: str

def get_membership_state(user_model) -> MembershipState:
    sub_status = (getattr(user_model, "subscription_status", None) or "").strip().lower()
    if sub_status not in ACTIVE_STATUSES:
        return MembershipState(is_paid=False, plan=None, label="No active subscription")
    return MembershipState(is_paid=True, plan="ktbb", label="Kabroda BattleBox (Full Access)")

def require_paid_access(user_model) -> MembershipState:
    ms = get_membership_state(user_model)
    if not ms.is_paid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Subscription required.")
    return ms

def ensure_symbol_allowed(user_model, symbol: str) -> None:
    require_paid_access(user_model)
    if not (symbol or "").strip():
        raise HTTPException(status_code=400, detail="Missing symbol")