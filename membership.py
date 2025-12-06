from enum import Enum
from pydantic import BaseModel
from fastapi import HTTPException, status


class Tier(str, Enum):
    TIER1_MANUAL = "tier1_manual"            # manual only
    TIER2_SINGLE_AUTO = "tier2_single_auto"  # auto BTC only
    TIER3_MULTI_GPT = "tier3_multi_gpt"      # auto multi-symbol + GPT (future)


class User(BaseModel):
    id: int
    email: str
    tier: Tier


def ensure_can_use_auto(user: User) -> None:
    """
    Gate for any auto DMR / auto inputs usage.

    Tier1: blocked
    Tier2: allowed (BTC-only restriction handled separately)
    Tier3: allowed
    """
    if user.tier in (Tier.TIER2_SINGLE_AUTO, Tier.TIER3_MULTI_GPT):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Auto DMR is not available on your membership tier.",
    )


def ensure_can_use_symbol_auto(user: User, symbol_short: str) -> None:
    """
    Symbol-level gating for auto DMR.

    - Tier1: already blocked by ensure_can_use_auto.
    - Tier2: only BTC allowed.
    - Tier3: any supported symbol allowed.
    """
    sym = symbol_short.upper()

    if user.tier == Tier.TIER2_SINGLE_AUTO and sym != "BTC":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your membership includes auto DMR for BTC only.",
        )
    # Tier3 has no extra symbol restriction.
    # Tier1 was already blocked before this is called.


def ensure_can_use_gpt(user: User) -> None:
    """
    Placeholder for GPT access gating.

    - Tier3 allowed
    - Tier1/Tier2 blocked
    """
    if user.tier == Tier.TIER3_MULTI_GPT:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="KTBB GPT assistant is not available on your membership tier.",
    )
