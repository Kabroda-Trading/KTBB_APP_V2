# executor_bitunix_client.py
# ==============================================================================
# BITUNIX REST CLIENT -- Stage 2, PARTIALLY LIVE (read-only), 2026-09-05.
#
# ccxt does not support Bitunix (confirmed directly against ccxt's own
# GitHub source, no bitunix.py exists at any version) -- this is
# necessarily a custom client, not a ccxt wrapper.
#
# IMPORTANT: Bitunix has NO official demo/paper-trading/testnet
# environment. Verified multiple ways before writing any of this: their
# own API docs (bitunix.com/api-docs) list exactly one base URL with no
# separate testnet; their own academy article's "testnet vs mainnet"
# text is a generic industry-concept FAQ answer, not a Bitunix-provided
# feature; an independent full-text search of Bitunix's own help center
# for "demo"/"testnet"/"simulated"/"practice"/"paper trading" found zero
# results. The original 3-stage plan (dry-run -> demo -> tiny live ->
# scale) has no real Stage 2 "demo" to test against -- corrected in
# AGENT_LOG.md, 2026-09-05. This file's read-only methods exist to
# verify the auth/signing chain actually works against a real account
# with ZERO financial risk, BEFORE any order-placing code is written.
#
# Auth scheme -- verified verbatim against bitunix.com/api-docs/futures/
# common/sign.html (the page's own Python sample, quoted in full in the
# AGENT_LOG entry this ships with):
#   digest = SHA256(nonce + timestamp + api_key + query_params + body)
#   sign   = SHA256(digest + secret_key)
# Headers: api-key, nonce, timestamp, sign, Content-Type: application/json.
# nonce: "Random string, 32bits" (interpreted as a 32-character random
# string -- the doc's own worked example uses a short placeholder, not a
# real 32-char value, so this is the standard interpretation, not
# guessed at from nothing). timestamp: current time in MILLISECONDS.
# query_params: keys sorted ascending ASCII, concatenated as key+value
# with no separator (doc's own example: {"id":1,"uid":200} -> "id1uid200").
# body: compact JSON (no whitespace), empty string if no body.
#
# Endpoints verified against bitunix.com/api-docs/futures/account/
# get_single_account.html and .../position/get_pending_positions.html:
#   GET /api/v1/futures/account?marginCoin=USDT
#   GET /api/v1/futures/position/get_pending_positions
# place_order/set_tpsl endpoints are ALSO verified (POST /api/v1/futures/
# trade/place_order, POST /api/v1/futures/tpsl/position/place_order) but
# DELIBERATELY still raise NotImplementedError -- see those methods'
# own docstrings for why real money moves through them and this file's
# read-only methods haven't been proven against a real request yet.
#
# The per-running-event-loop client cache mirrors market_data.py's
# _exchange_live pattern -- that module's own header documents a real
# production hang from sharing an HTTP client across two different
# asyncio event loops.
# ==============================================================================

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import string
import time
import weakref
from typing import Any, Dict, Optional

import aiohttp

BASE_URL = "https://fapi.bitunix.com"

_sessions: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _get_session() -> aiohttp.ClientSession:
    """Per-running-event-loop cached aiohttp session -- never a bare
    module-level global (see module header)."""
    loop = asyncio.get_running_loop()
    session = _sessions.get(loop)
    if session is None or session.closed:
        session = aiohttp.ClientSession()
        _sessions[loop] = session
    return session


def _nonce() -> str:
    """"Random string, 32bits" per the docs -- interpreted as a
    32-character random alphanumeric string (the doc's own worked
    example uses a short numeric placeholder for illustration only)."""
    return "".join(random.choices(string.ascii_letters + string.digits, k=32))


def _sorted_query_string(params: Optional[Dict[str, Any]]) -> str:
    """Keys sorted ascending ASCII, concatenated as key+value with no
    separator -- matches the doc's own example: {"id": 1, "uid": 200}
    -> "id1uid200"."""
    if not params:
        return ""
    return "".join(f"{k}{params[k]}" for k in sorted(params.keys()))


def _compact_body(body: Optional[Dict[str, Any]]) -> str:
    """Compact JSON, no whitespace -- "the request body format must be
    identical to the signature string" per the docs, so this exact
    string (not a re-serialized copy) must be sent as the request body."""
    if not body:
        return ""
    return json.dumps(body, separators=(",", ":"))


def sign_request(nonce: str, timestamp: str, api_key: str, secret: str,
                  query_string: str, body_str: str) -> str:
    """The exact two-stage SHA256 construction from bitunix.com/api-docs/
    futures/common/sign.html's own Python sample -- ported verbatim, not
    reimplemented from a paraphrase. Verified independently (AGENT_LOG.md,
    2026-09-05) against the doc's own worked example inputs before this
    shipped: nonce="123456", timestamp="20241120123045", api_key=
    "yourApiKey", secret="yourSecretKey", query_string="id1uid200",
    body='{"uid":"2899","arr":[{"id":1,"name":"maple"},{"id":2,"name":
    "lily"}]}' -> digest starts "75099831ac...", sign starts
    "00397cd1e5..." (full values in tests/test_executor_bitunix_client.py).
    """
    digest_input = f"{nonce}{timestamp}{api_key}{query_string}{body_str}"
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    return hashlib.sha256((digest + secret).encode("utf-8")).hexdigest()


class BitunixClient:
    """Constructed per-account (api_key/api_secret from executor_accounts.
    get_decrypted_credentials() -- never called from anywhere but
    executor_engine.py's LIVE branch)."""

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret

    async def _request(self, method: str, path: str,
                        query: Optional[Dict[str, Any]] = None,
                        body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        nonce = _nonce()
        timestamp = str(int(time.time() * 1000))
        query_string = _sorted_query_string(query)
        body_str = _compact_body(body)
        sign = sign_request(nonce, timestamp, self.api_key, self.api_secret, query_string, body_str)
        headers = {
            "api-key": self.api_key, "nonce": nonce, "timestamp": timestamp,
            "sign": sign, "Content-Type": "application/json",
        }
        session = _get_session()
        async with session.request(method, BASE_URL + path, params=query,
                                    data=body_str if body else None, headers=headers) as resp:
            return await resp.json()

    # --- READ-ONLY -- zero financial risk, safe to call today ---

    async def get_balance(self, margin_coin: str = "USDT") -> Dict[str, Any]:
        """GET /api/v1/futures/account -- verified against bitunix.com/
        api-docs/futures/account/get_single_account.html. Returns fields
        including `available`, `margin`, `crossUnrealizedPNL` per that
        page's documented response shape."""
        return await self._request("GET", "/api/v1/futures/account", query={"marginCoin": margin_coin})

    async def get_position(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """GET /api/v1/futures/position/get_pending_positions -- verified
        against bitunix.com/api-docs/futures/position/get_pending_positions.html.
        `symbol` is optional there (omit to get all open positions).
        Response includes a real exchange-computed `liqPrice` per
        position -- worth using instead of executor_sizing.py's own
        estimate once this is wired into the plan builder (not done yet
        -- that's a real Stage 2/3 improvement, flagged, not built here)."""
        query = {"symbol": symbol} if symbol else None
        return await self._request("GET", "/api/v1/futures/position/get_pending_positions", query=query)

    # --- ORDER-PLACING -- NOT built yet, deliberately ---

    async def place_order(self, symbol: str, side: str, qty: float, entry_price: float,
                           stop_price: float, tp_price: float, leverage: int) -> Dict[str, Any]:
        """POST /api/v1/futures/trade/place_order -- endpoint and bracket
        params (tpPrice/slPrice/tpStopType/slStopType etc.) ARE verified
        against bitunix.com/api-docs/futures/trade/place_order.html
        (AGENT_LOG.md, 2026-09-05). Still not implemented: this is where
        real money moves, and it hasn't been proven that the signing
        chain above actually works against a real Bitunix request yet
        (no demo environment exists to prove that risk-free -- see
        module header). Andy's own sequencing call: verify get_balance()/
        get_position() against his real account first; only once that's
        confirmed does implementing this become the next real step, not
        a guess stacked on an unverified guess.
        """
        raise NotImplementedError(
            "place_order() is intentionally not implemented yet -- verify get_balance()/"
            "get_position() against a real account first (see this method's own docstring)."
        )

    async def set_tpsl(self, position_id: str, stop_price: Optional[float] = None,
                        tp_price: Optional[float] = None) -> Dict[str, Any]:
        """POST /api/v1/futures/tpsl/position/place_order -- endpoint
        verified against bitunix.com/api-docs, same reasoning as
        place_order() for why it's not implemented yet."""
        raise NotImplementedError(
            "set_tpsl() is intentionally not implemented yet -- verify get_balance()/"
            "get_position() against a real account first (see this method's own docstring)."
        )
