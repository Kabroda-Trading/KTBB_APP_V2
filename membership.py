# membership.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, status

# Stripe price IDs (source of truth for plan)
PRICE_TACTICAL = (os.getenv("STRIPE_PRICE_TACTICAL") or "").strip()
PRICE_ELITE = (os.getenv("STRIPE_PRICE_ELITE") or "").strip()

ACTIVE_STATUSES = {"active", "trialing"}


@dataclass(frozen=True)
class MembershipState:
    """
    Stripe-authoritative membership state.

    We do NOT use any internal tiering model for gating.
    Plan is derived exclusively from stripe_price_id.
    Paid access is derived exclusively from subscription_status.
    """
    is_paid: bool
    plan: Optional[str]  # "tactical" | "elite" | None
    label: str


def _plan_from_price_id(price_id: Optional[str]) -> Optional[str]:
    if not price_id:
        return None
    pid = price_id.strip()
    if PRICE_ELITE and pid == PRICE_ELITE:
        return "elite"
    if PRICE_TACTICAL and pid == PRICE_TACTICAL:
        return "tactical"
    return None


def get_membership_state(user_model) -> MembershipState:
    """
    Accepts the SQLAlchemy UserModel.
    """
    sub_status = (getattr(user_model, "subscription_status", None) or "").strip().lower()
    price_id = (getattr(user_model, "stripe_price_id", None) or "").strip()

    is_paid = sub_status in ACTIVE_STATUSES
    plan = _plan_from_price_id(price_id)

    if not is_paid:
        return MembershipState(is_paid=False, plan=None, label="No active subscription")

    # Paid but unknown price -> treat as paid but unknown plan (safe default = no elite features)
    if plan == "elite":
        return MembershipState(is_paid=True, plan="elite", label="KABRODA BattleBox Elite")
    if plan == "tactical":
        return MembershipState(is_paid=True, plan="tactical", label="KABRODA BattleBox Tactical")

    return MembershipState(is_paid=True, plan=None, label="Active subscription (Unknown plan)")


def require_paid_access(user_model) -> MembershipState:
    ms = get_membership_state(user_model)
    if not ms.is_paid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Subscription required. Please choose Tactical or Elite to unlock the Suite.",
        )
    return ms


def is_elite(user_model) -> bool:
    ms = get_membership_state(user_model)
    return bool(ms.is_paid and ms.plan == "elite")


def ensure_symbol_allowed(user_model, symbol: str) -> None:
    """
    Tactical: BTC only.
    Elite: multi-symbol.
    Symbol canonical format for this app is like BTCUSDT / ETHUSDT.
    """
    ms = require_paid_access(user_model)

    # Unknown plan while paid: safest behavior is restrict to BTC only.
    if ms.plan not in ("tactical", "elite"):
        sym = (symbol or "").upper()
        if sym != "BTCUSDT":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Plan not recognized. Please contact support or re-check subscription.",
            )
        return

    if ms.plan == "tactical":
        sym = (symbol or "").upper()
        if sym != "BTCUSDT":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tactical includes Auto DMR for BTC only. Upgrade to Elite for multi-symbol.",
            )


def ensure_coach_allowed(user_model) -> None:
    ms = require_paid_access(user_model)
    if ms.plan != "elite":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Kabroda AI Coach is Elite-only. Upgrade to unlock.",
        )
