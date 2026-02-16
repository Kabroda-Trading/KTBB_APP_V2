# membership.py
# ==============================================================================
# KABRODA MEMBERSHIP LOGIC (WHOP EDITION)
# ==============================================================================
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from fastapi import HTTPException, status

# Valid statuses that grant access
# We added 'lifetime' just in case you manually grant that in the future.
ACTIVE_STATUSES = {"active", "trialing", "lifetime"}

@dataclass(frozen=True)
class MembershipState:
    is_paid: bool
    plan: Optional[str]
    label: str

def get_membership_state(user_model) -> MembershipState:
    """
    Checks if the user has a valid subscription status in the DB.
    """
    # Safe check: ensures we don't crash if user_model is None
    sub_status = (getattr(user_model, "subscription_status", None) or "").strip().lower()
    
    if sub_status not in ACTIVE_STATUSES:
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