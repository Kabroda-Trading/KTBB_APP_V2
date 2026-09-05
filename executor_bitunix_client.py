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
# Read-only endpoints verified against bitunix.com/api-docs (each page
# named in its own method's docstring below):
#   GET /api/v1/futures/account?marginCoin=USDT
#   GET /api/v1/futures/position/get_pending_positions
#   GET /api/v1/futures/account/get_leverage_margin_mode
#   GET /api/v1/futures/market/trading_pairs
# 2026-09-05 (Stage 2): place_order/close_position/set_position_tpsl/
# modify_position_tp_sl_order/get_position_tiers are now REAL,
# implemented, and were independently re-verified against
# bitunix.com/api-docs the same day this shipped (not carried forward
# from an earlier guess): POST /api/v1/futures/trade/place_order, POST
# /api/v1/futures/trade/flash_close_position, POST /api/v1/futures/
# tpsl/position/place_order, POST /api/v1/futures/tpsl/position/
# modify_order, GET /api/v1/futures/position/get_position_tiers. These
# are exercised for real, deliberately, by a manually-triggered tiny
# mechanism test (executor_mechanism_test.py) gated behind a persistent
# ExecutorGlobalConfig.live_orders_enabled flag (default OFF) plus a
# per-call confirmation phrase -- never called automatically from the
# real TradePlan-driven pipeline. change_leverage() is intentionally
# NOT implemented and never will be here -- the bot only ever READS
# leverage/margin-mode config, never mutates it (Andy's explicit call,
# default OFF, do not add this without re-litigating that decision).
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

    async def get_order_detail(self, order_id: Optional[str] = None, client_id: Optional[str] = None) -> Dict[str, Any]:
        """GET /api/v1/futures/trade/get_order_detail -- verified against
        bitunix.com/api-docs, 2026-09-05. At least one of order_id/client_id
        required. Response `status` values: INIT (prepare) | NEW (pending)
        | PART_FILLED | CANCELED | FILLED. THIS, not a positions-list scan,
        is the authoritative way to confirm a specific order actually
        filled -- added after a real incident (2026-09-05) where polling
        get_position() and matching on symbol+side never found real,
        confirmed-filled positions in 10 attempts; querying the order's
        own status by orderId is direct and unambiguous, immune to
        whatever the positions list's side/symbol fields actually look
        like in practice."""
        if not order_id and not client_id:
            raise ValueError("get_order_detail requires order_id or client_id")
        query: Dict[str, Any] = {}
        if order_id:
            query["orderId"] = order_id
        if client_id:
            query["clientId"] = client_id
        return await self._request("GET", "/api/v1/futures/trade/get_order_detail", query=query)

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

    async def get_leverage_and_margin_mode(self, symbol: str, margin_coin: str = "USDT") -> Dict[str, Any]:
        """GET /api/v1/futures/account/get_leverage_margin_mode -- verified
        against bitunix.com/api-docs/futures/account/get_leverage_and_margin_mode.html
        (2026-09-05, DeepSeek's own flagged addition to the read-only
        verification set). Returns `leverage` (int) and `marginMode`
        ("ISOLATION"|"CROSS") for the real account -- lets the bot verify
        isolated+10x is ACTUALLY set on the exchange rather than trust
        `ExecutorAccount.margin_mode`/`leverage_baseline` as configured."""
        return await self._request("GET", "/api/v1/futures/account/get_leverage_margin_mode",
                                    query={"symbol": symbol, "marginCoin": margin_coin})

    async def get_trading_pairs(self, symbols: Optional[str] = None) -> Dict[str, Any]:
        """GET /api/v1/futures/market/trading_pairs -- verified against
        bitunix.com/api-docs/futures/market/get_trading_pairs.html.
        `symbols` is a comma-separated string (e.g. "BTCUSDT"), optional
        (omit for all pairs). Response includes `minTradeVolume` (the
        real minimum order size) and `basePrecision`/`quotePrecision` --
        needed before any real order can be sized correctly; a synthetic
        assumption here would be exactly the kind of guess this project's
        discipline exists to avoid."""
        query = {"symbols": symbols} if symbols else None
        return await self._request("GET", "/api/v1/futures/market/trading_pairs", query=query)

    # --- ORDER-PLACING -- REAL, Stage 2 (2026-09-05) -- see module header ---
    # NOTE: change_leverage() does not exist and never will here -- the
    # bot only ever reads leverage/margin-mode, never mutates it.

    async def place_order(
        self, symbol: str, qty: str, side: str, trade_side: str, order_type: str,
        price: Optional[str] = None, position_id: Optional[str] = None,
        effect: Optional[str] = None, client_id: Optional[str] = None,
        reduce_only: Optional[bool] = None,
        tp_price: Optional[str] = None, tp_stop_type: Optional[str] = None,
        tp_order_type: Optional[str] = None, tp_order_price: Optional[str] = None,
        sl_price: Optional[str] = None, sl_stop_type: Optional[str] = None,
        sl_order_type: Optional[str] = None, sl_order_price: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /api/v1/futures/trade/place_order -- verified against
        bitunix.com/api-docs/futures/trade/place_order.html, 2026-09-05.
        qty/price/tpPrice/slPrice etc. are all STRING types on the wire --
        callers must pre-format with executor_sizing.round_qty_to_precision()/
        round_price_to_precision() BEFORE calling this; this method never
        rounds or reformats a numeric value itself, it only forwards
        exactly the string it was given. side: "BUY"|"SELL". trade_side:
        "OPEN"|"CLOSE" (HEDGE-mode position direction -- Andy's real
        account is HEDGE mode, confirmed live). order_type: "LIMIT"|
        "MARKET". Only non-None optional fields are included in the body.
        """
        body: Dict[str, Any] = {
            "symbol": symbol, "qty": qty, "side": side,
            "tradeSide": trade_side, "orderType": order_type,
        }
        for key, val in (
            ("price", price), ("positionId", position_id), ("effect", effect),
            ("clientId", client_id), ("reduceOnly", reduce_only),
            ("tpPrice", tp_price), ("tpStopType", tp_stop_type),
            ("tpOrderType", tp_order_type), ("tpOrderPrice", tp_order_price),
            ("slPrice", sl_price), ("slStopType", sl_stop_type),
            ("slOrderType", sl_order_type), ("slOrderPrice", sl_order_price),
        ):
            if val is not None:
                body[key] = val
        return await self._request("POST", "/api/v1/futures/trade/place_order", body=body)

    async def close_position(self, position_id: str) -> Dict[str, Any]:
        """POST /api/v1/futures/trade/flash_close_position -- verified
        against bitunix.com/api-docs, 2026-09-05. Closes the ENTIRE
        position instantly at market (no partial-qty param exists on
        this endpoint -- for a partial close, use place_order with
        tradeSide="CLOSE"/reduceOnly=True instead). Rate limit 5 req/sec/
        uid -- a non-issue for this build's single human-clicked call per
        test run; never wrap this in an automatic retry loop (double-
        close risk)."""
        return await self._request("POST", "/api/v1/futures/trade/flash_close_position",
                                    body={"positionId": position_id})

    async def set_position_tpsl(
        self, symbol: str, position_id: str,
        tp_price: Optional[str] = None, tp_stop_type: Optional[str] = None,
        sl_price: Optional[str] = None, sl_stop_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /api/v1/futures/tpsl/position/place_order -- verified
        against bitunix.com/api-docs, 2026-09-05. Sets TP/SL on an
        EXISTING position (separate from place_order's own bracket
        params). At least one of tp_price/sl_price is required by the
        exchange -- enforced here so a caller bug never sends a no-op
        request that could be mistaken for a real one."""
        if tp_price is None and sl_price is None:
            raise ValueError("set_position_tpsl requires at least one of tp_price or sl_price")
        body: Dict[str, Any] = {"symbol": symbol, "positionId": position_id}
        for key, val in (("tpPrice", tp_price), ("tpStopType", tp_stop_type),
                         ("slPrice", sl_price), ("slStopType", sl_stop_type)):
            if val is not None:
                body[key] = val
        return await self._request("POST", "/api/v1/futures/tpsl/position/place_order", body=body)

    async def modify_position_tp_sl_order(
        self, symbol: str, position_id: str,
        tp_price: Optional[str] = None, tp_stop_type: Optional[str] = None,
        sl_price: Optional[str] = None, sl_stop_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /api/v1/futures/tpsl/position/modify_order -- verified
        against bitunix.com/api-docs, 2026-09-05. MODIFIES an existing
        position TP/SL (this is how a stop gets moved to breakeven).
        Same param shape/validation as set_position_tpsl(). Rate limit
        10 req/sec/UID -- non-issue for this build's usage pattern."""
        if tp_price is None and sl_price is None:
            raise ValueError("modify_position_tp_sl_order requires at least one of tp_price or sl_price")
        body: Dict[str, Any] = {"symbol": symbol, "positionId": position_id}
        for key, val in (("tpPrice", tp_price), ("tpStopType", tp_stop_type),
                         ("slPrice", sl_price), ("slStopType", sl_stop_type)):
            if val is not None:
                body[key] = val
        return await self._request("POST", "/api/v1/futures/tpsl/position/modify_order", body=body)

    async def get_pending_tp_sl_order(self, symbol: Optional[str] = None, position_id: Optional[str] = None) -> Dict[str, Any]:
        """GET /api/v1/futures/tpsl/get_pending_orders -- verified against
        bitunix.com/api-docs, 2026-09-05. Returns the position-level TP/SL
        orders ACTUALLY REGISTERED on the exchange (tpPrice/slPrice
        fields) -- used to independently confirm a set_position_tpsl()/
        modify_position_tp_sl_order() call really took effect, per
        Bitunix's own guidance that a successful REST response doesn't
        guarantee the operation succeeded."""
        query: Dict[str, Any] = {}
        if symbol:
            query["symbol"] = symbol
        if position_id:
            query["positionId"] = position_id
        return await self._request("GET", "/api/v1/futures/tpsl/get_pending_orders", query=query or None)

    async def get_position_tiers(self, symbol: str) -> Dict[str, Any]:
        """GET /api/v1/futures/position/get_position_tiers -- verified
        against bitunix.com/api-docs, 2026-09-05. Returns tiered notional
        brackets with the real, live maintenance margin rate per tier
        (`maintenanceMarginRate`) -- NEVER cache/hardcode a tier table,
        Bitunix can change tiers at any time, same philosophy as never
        trusting a stored leverage baseline. See executor_plan_builder.
        py's _query_real_maintenance_margin_rate() for how this feeds
        the liquidation safety check."""
        return await self._request("GET", "/api/v1/futures/position/get_position_tiers",
                                    query={"symbol": symbol})
