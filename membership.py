# membership.py
# ==============================================================================
# KABRODA MEMBERSHIP LOGIC (WHOP EDITION)
# ==============================================================================
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from fastapi import HTTPException, status

# AUDIT FIX: Expanded the valid statuses to catch ALL possible Whop webhook signals
ACTIVE_STATUSES = {
    "active", 
    "trialing", 
    "lifetime", 
    "valid", 
    "paid", 
    "approved",
    "membership.active",
    "pro"
}

@dataclass(frozen=True)
class MembershipState:
    is_paid: bool
    plan: Optional[str]
    label: str

def get_membership_state(user_model) -> MembershipState:
    """
    Checks if the user has a valid subscription status in the DB.
    """
    if not user_model:
        return MembershipState(is_paid=False, plan=None, label="Inactive")

    # Safely extract whatever words Whop wrote into the database
    sub_status = (getattr(user_model, "subscription_status", None) or "").strip().lower()
    tier = (getattr(user_model, "tier", None) or "").strip().lower()
    
    # AUDIT FIX: We check BOTH subscription_status and tier. 
    # If the webhook updated either one of these to a positive status, we let them in.
    is_valid_sub = sub_status in ACTIVE_STATUSES
    is_valid_tier = tier in {"pro", "premium", "lifetime", "active"}

    if not (is_valid_sub or is_valid_tier):
        return MembershipState(is_paid=False, plan=None, label="Inactive")
    
    return MembershipState(is_paid=True, plan="pro", label="Kabroda Operator")

def require_paid_access(user_model) -> MembershipState:
    """
    Dependency for routes that require a paid sub.
    """
    ms = get_membership_state(user_model)
    if not ms.is_paid:
        # If user is admin, they always get a pass (Safety Override)
        if getattr(user_model, "is_admin", False):
            return MembershipState(is_paid=True, plan="admin", label="Commander")
            
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Active subscription required."
        )
    return ms

def ensure_symbol_allowed(user_model, symbol: str) -> None:
    """
    Legacy helper: Ensures user is paid before letting them touch a symbol.
    Preserved to prevent import errors in older modules.
    """
    require_paid_access(user_model)
    if not (symbol or "").strip():
        raise HTTPException(status_code=400, detail="Missing symbol")