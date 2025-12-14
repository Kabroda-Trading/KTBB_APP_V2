# membership.py

from enum import Enum
from pydantic import BaseModel
from fastapi import HTTPException, status


class Tier(str, Enum):
    TIER1_MANUAL = "tier1_manual"
    TIER2_SINGLE_AUTO = "tier2_single_auto"   # Tactical
    TIER3_MULTI_GPT = "tier3_multi_gpt"       # Elite


class User(BaseModel):
    id: int
    email: str
    tier: Tier
    session_tz: str = "America/New_York"


def tier_marketing_label(tier: Tier) -> str:
    if tier == Tier.TIER1_MANUAL:
        return "KABRODA Battle Suite – Manual"
    if tier == Tier.TIER2_SINGLE_AUTO:
        return "KABRODA BattleBox Tactical"
    if tier == Tier.TIER3_MULTI_GPT:
        return "KABRODA BattleBox Elite"
    return str(tier)


def ensure_can_use_auto(user: User) -> None:
    if user.tier in (Tier.TIER2_SINGLE_AUTO, Tier.TIER3_MULTI_GPT):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Auto DMR requires Tactical or Elite. Upgrade to unlock.",
    )


def ensure_can_use_symbol_auto(user: User, symbol_short: str) -> None:
    sym = symbol_short.upper()
    if user.tier == Tier.TIER2_SINGLE_AUTO and sym != "BTC":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tactical includes Auto DMR for BTC only. Upgrade to Elite for multi-symbol.",
        )


def ensure_can_use_gpt_chat(user: User) -> None:
    if user.tier == Tier.TIER3_MULTI_GPT:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="GPT chat coaching is Elite-only. Upgrade to unlock.",
    )


def can_use_gpt_report(user: User) -> bool:
    return user.tier in (Tier.TIER2_SINGLE_AUTO, Tier.TIER3_MULTI_GPT)
