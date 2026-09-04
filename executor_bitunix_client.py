# executor_bitunix_client.py
# ==============================================================================
# BITUNIX REST CLIENT -- Stage 2/3 shell, DELIBERATELY UNCALLED in Stage 1.
#
# ccxt does not support Bitunix (confirmed directly against ccxt's own
# GitHub source, no bitunix.py exists at any version) -- this is
# necessarily a custom client, not a ccxt wrapper.
#
# Every method here raises NotImplementedError. Bitunix's documented
# shape (openapidoc.bitunix.com, from the design conversation -- Kabroda
# AI Brain repo AGENT_LOG.md, 2026-09-04): POST /api/v1/futures/trade/
# place_order (symbol, qty, side, tradeSide, orderType, effect, with
# native bracket support via tpPrice/slPrice + tpStopType/slStopType =
# MARK_PRICE, chosen deliberately since mark-price triggers are
# wick-resistant, matching this codebase's own hardened close-
# confirmation discipline) and a separate /api/v1/futures/tpsl/position/
# place_order endpoint to modify TP/SL on an open position. Auth is
# api-key + nonce + timestamp + HMAC sign headers -- the EXACT header
# names and signing-string construction are NOT yet confirmed against
# Bitunix's real, current docs. Do not guess them -- writing a plausible-
# looking but unverified signing implementation would be exactly the
# kind of confident-but-wrong code this project's own discipline exists
# to prevent (see CLAUDE.md's cross-project "no fabrication" rule).
# Verify against the live docs before filling in any of these bodies.
#
# The per-running-event-loop client cache below mirrors market_data.py's
# _exchange_live pattern DEFENSIVELY, even though no code calls this
# module yet -- market_data.py's own header documents a real production
# hang from sharing an HTTP client across two different asyncio event
# loops. Building this shell the safe way now means Stage 2 fills in
# method bodies, it doesn't redesign the client's lifecycle.
# ==============================================================================

from __future__ import annotations

import weakref
from typing import Any, Dict, Optional

import aiohttp

BASE_URL = "https://fapi.bitunix.com"  # unverified against live docs -- confirm before Stage 2

_sessions: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _get_session() -> aiohttp.ClientSession:
    """Per-running-event-loop cached aiohttp session -- never a bare
    module-level global. Unused until a Stage 2 method body actually
    calls it; present now so the lifecycle pattern is right from the
    start (see module header)."""
    import asyncio
    loop = asyncio.get_running_loop()
    session = _sessions.get(loop)
    if session is None or session.closed:
        session = aiohttp.ClientSession()
        _sessions[loop] = session
    return session


class BitunixClient:
    """Constructed per-account (api_key/api_secret from executor_accounts.
    get_decrypted_credentials() -- never called from anywhere but
    executor_engine.py's PAPER/LIVE branch)."""

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret

    async def place_order(self, symbol: str, side: str, qty: float, entry_price: float,
                           stop_price: float, tp_price: float, leverage: int) -> Dict[str, Any]:
        raise NotImplementedError(
            "Stage 2 -- verify Bitunix's exact auth header format and "
            "place_order payload shape against openapidoc.bitunix.com before implementing."
        )

    async def set_tpsl(self, position_id: str, stop_price: Optional[float] = None,
                        tp_price: Optional[float] = None) -> Dict[str, Any]:
        raise NotImplementedError("Stage 2 -- verify /api/v1/futures/tpsl/position/place_order against live docs.")

    async def get_balance(self) -> Dict[str, Any]:
        raise NotImplementedError("Stage 2 -- verify Bitunix's balance endpoint against live docs.")

    async def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError("Stage 2 -- verify Bitunix's position-query endpoint against live docs.")
