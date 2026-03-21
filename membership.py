# membership.py
# ==============================================================================
# KABRODA MEMBERSHIP LOGIC (GOD-MODE BOUNCER)
# ==============================================================================
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from fastapi import HTTPException, status

@dataclass(frozen=True)
class MembershipState:
    is_paid: bool
    plan: Optional[str]
    label: str

def get_membership_state(user_model) -> MembershipState:
    # 1. Safe check: If no user exists, deny access
    if not user_model:
        return MembershipState(is_paid=False, plan=None, label="Inactive")

    # 2. Safety Override: Admins always get in instantly
    if getattr(user_model, "is_admin", False):
        return MembershipState(is_paid=True, plan="admin", label="Commander")

    # 3. Extract the exact words from the database, force lowercase, and strip hidden spaces
    sub_status = str(getattr(user_model, "subscription_status", "")).strip().lower()
    tier = str(getattr(user_model, "tier", "")).strip().lower()

    # 4. The "Fuzzy Match" Safety Net
    # If ANY of these words exist ANYWHERE in their status, let them in.
    approved_keywords = ["active", "pro", "premium", "paid", "valid", "lifetime"]

    # This checks if the word 'active' is inside 'whop_active', for example
    is_valid = any(kw in sub_status for kw in approved_keywords) or any(kw in tier for kw in approved_keywords)

    if is_valid:
        return MembershipState(is_paid=True, plan="pro", label="Kabroda Operator")

    return MembershipState(is_paid=False, plan=None, label="Inactive")

def require_paid_access(user_model) -> MembershipState:
    ms = get_membership_state(user_model)
    
    # If the bouncer decides to block them, force it to confess EXACTLY why in the Render logs!
    if not ms.is_paid:
        email = getattr(user_model, 'email', 'Unknown')
        db_sub = getattr(user_model, 'subscription_status', 'Empty')
        db_tier = getattr(user_model, 'tier', 'Empty')
        
        print(f"🚨 BOUNCER BLOCKED USER: {email}")
        print(f"🚨 EXACT DB RECORD -> Status: '{db_sub}' | Tier: '{db_tier}'")
        
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Active subscription required."
        )
    return ms

def ensure_symbol_allowed(user_model, symbol: str) -> None:
    require_paid_access(user_model)
    if not (symbol or "").strip():
        raise HTTPException(status_code=400, detail="Missing symbol")