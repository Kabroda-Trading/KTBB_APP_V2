# cme_crosstrade_client.py
# ==============================================================================
# CROSSTRADE REST CLIENT -- an alternative execution path for CME Micro
# Futures (MNQ/MES) when an account's own direct Tradovate API access is
# gated off (2026-11-04: confirmed live and independently via Tradovate's
# own community forum that unfunded/evaluation Tradovate accounts cannot
# obtain cid/sec API credentials at all -- /auth/accesstokenrequest returns
# HTTP 200 with neither accessToken nor errorText for such an account, see
# cme_tradovate_client.py's last_auth_error handling). CrossTrade
# (crosstrade.io) is a third-party, Tradovate-sanctioned broker-integration
# partner that authenticates to Tradovate server-side on the user's behalf
# ("CrossTrade calls Tradovate's cloud API over HTTPS and places the order
# on your linked account" -- crosstrade.io/docs/tradovate/your-first-
# automated-trade) using its OWN partner-level credentials, not the user's.
# This is NOT a workaround/hack like DeepSeek's rejected "Mode A" session-
# token proposal (AGENT_LOG.md, Kabroda AI Brain repo, tenth/eleventh
# entries, 2026-11-04) -- it's CrossTrade's actual, documented, supported
# product, explicitly marketed for "prop trading evaluation and funded
# accounts" (crosstrade.io/pricing FAQ).
#
# This file exposes the EXACT SAME method surface as cme_tradovate_client.
# TradovateClient (get_balance/get_positions/get_orders/get_order_detail/
# place_bracket_limit_order/amend_stop_to_breakeven/cancel_order/
# emergency_market_close), normalized to the same return shapes, so
# cme_executor.py's _client_for() is the ONLY place that needs to branch on
# which provider an account uses -- no other file changes.
#
# DELIBERATE EXCEPTION (fee-capture build, 2026-11-xx): get_fills_for_order()/
# get_fills_history() are added ONLY here, not on TradovateClient -- direct-
# Tradovate fee capture is explicitly deferred (same funded-account gate
# that blocks direct Tradovate trading/market-data blocks any direct
# Fill/FillFee lookup too). cme_executor.py only calls these two when
# account.execution_provider == "crosstrade".
#
# Auth: a single CrossTrade Bearer token (generated on CrossTrade's own My
# Account page, requires their Pro plan or above) -- NOT the webhook Secret
# Key, a materially different credential (crosstrade.io/docs/api/overview:
# "use your CrossTrade Bearer token, not the Secret Key from a webhook
# alert"). Reuses ExecutorAccount.api_key_encrypted for the bearer token;
# api_secret_encrypted is unused for this provider (CrossTrade needs only
# one secret) -- set it to any non-empty placeholder so executor_accounts.
# get_decrypted_credentials() (which requires BOTH fields present) still
# reports credentials as set. Documented in the admin UI, not a silent trap.
#
# CONFIRMED against CrossTrade's own live OpenAPI 3.1 spec
# (https://app.crosstrade.io/v1/api/openapi.json, fetched and parsed
# directly 2026-11-04 -- exact field names and endpoints, not a guess):
#   POST /accounts/{account}/orders/place (instrument/action/qty/orderType/
#     limitPrice/stopPrice/tif/takeProfit/stopLoss -- takeProfit/stopLoss
#     are a server-side OSO bracket in the SAME call, per their docs: "A
#     bracketed placement (takeProfit and/or stopLoss) is one Tradovate
#     placeOSO call"),
#   PUT /accounts/{account}/orders/{id}/change (qty/orderType/limitPrice/
#     stopPrice/tif/...),
#   POST /accounts/{account}/orders/{id}/cancel,
#   POST /accounts/{account}/positions/flatten (instrument/marketPosition --
#     flattens one side of one instrument on one account; the OTHER flatten
#     endpoint, POST /positions/flatten with no account/instrument, is
#     "FLATTENEVERYTHING" across ALL linked identities -- deliberately never
#     used here, would affect accounts beyond this one),
#   GET /cashbalances, GET /accounts/{account}/positions,
#   GET /accounts/{account}/orders, GET /accounts/{account}/orders/{id}.
#
# CONFIRMED LIVE 2026-11-04 (real response, not a guess -- Andy ran it
# through CrossTrade's own API Playground against the real MFFU account):
#   GET /accounts/{account} -- {data: {accountId, environment, userId,
#   name, balance: {totalCashValue, totalPnL, netLiq, openPnL, realizedPnL,
#   cashUSD, ...}}}. get_balance() is built against this exact shape.
#   GET /accounts -- {data: [{accountId, environment, userId, name,
#   accountType, active, archived, ...}]} -- confirmed accountId "72422359"
#   for the real MFFU account matches the Brokers page's own "ID" column.
#
# **NOT YET LIVE-VERIFIED -- flagged, not presented as certain**: the spec
# types the remaining response bodies as a bare `{success: bool, data:
# object|array}` with no per-field schema (CrossTrade proxies Tradovate's
# own data through unmodified -- "Positions, orders, and fills are shared
# REST resources on both platforms" -- but the exact field names inside
# `data` for positions/orders/a placed order are the live-Tradovate-native
# ones CrossTrade doesn't redocument, and no position/order has existed yet
# to confirm against). The field names assumed below (contractId/symbol/
# id/positionId/netPrice/avgOpenPrice for positions; orderId for a placed
# order) are the SAME ones cme_tradovate_client.py already uses against raw
# Tradovate -- a reasonable assumption given CrossTrade's own "shared
# resources" claim and balance's confirmed accuracy above, but confirm on
# the first real position/order, same as every other NOT YET LIVE-VERIFIED
# item in this build.
#
# FILLS ENDPOINTS (fee-capture build, 2026-11-xx) -- CONFIRMED to exist live
# against CrossTrade's own endpoint catalog (app.crosstrade.io/v1/api/
# _endpoints) and OpenAPI spec, 2026-11-05: GET /fills/order/{orderId}
# ("Fills for one order (NT8 executions-by-order parity)") and GET
# /fills/history (from/to ISO-8601 UTC, environment demo|live, account,
# cursor -- cursor listed as REQUIRED with no doc explaining a first-page
# value; limit optional, max 1000/default 500). **SCHEMA NOT LIVE-VERIFIED**:
# the fill object itself is typed in the OpenAPI spec as a bare `{"type":
# "object"}` with ZERO documented properties -- neither the human docs nor
# the llms-full.txt reference give an example response. sum_fill_fees()'s
# field-name guesses (commission/fees, falling back to fee/commissionFee)
# are UNVERIFIED -- both new methods log their raw response on every call
# specifically so this can be confirmed/corrected after the first real CME
# fill under CrossTrade. Do not treat the guessed names as fact.
# ==============================================================================

from __future__ import annotations

import asyncio
import weakref
from typing import Any, Dict, List, Optional

import aiohttp

BASE_URL = "https://app.crosstrade.io/v1/api/tv"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10.0)

_sessions: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _safe_float(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def sum_fill_fees(fills: List[Dict[str, Any]]) -> float:
    """Defensive candidate-field parsing -- CrossTrade's own OpenAPI spec
    gives the fill object zero documented properties (see module header,
    "FILLS ENDPOINTS"). "commission" and "fees" are presumed to be TWO
    DISTINCT real components (broker commission + exchange/clearing fee)
    and are SUMMED when either is present; "fee"/"commissionFee" are
    alternate-spelling FALLBACKS used only when NEITHER presumed-real name
    is present on a given fill, to avoid double-counting one field under
    two guessed names. CORRECT THIS against the first real logged
    response -- these names are a guess, not fact."""
    total = 0.0
    for fill in fills:
        commission, fees = fill.get("commission"), fill.get("fees")
        if commission is not None or fees is not None:
            total += _safe_float(commission) + _safe_float(fees)
        else:
            for key in ("fee", "commissionFee"):
                if fill.get(key) is not None:
                    total += _safe_float(fill[key])
                    break
    return total


def _get_session() -> aiohttp.ClientSession:
    """Per-running-event-loop cached aiohttp session -- same rationale as
    cme_tradovate_client.py's own _get_session()."""
    loop = asyncio.get_running_loop()
    session = _sessions.get(loop)
    if session is None or session.closed:
        session = aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT)
        _sessions[loop] = session
    return session


class CrossTradeClient:
    """One instance per ExecutorAccount using execution_provider=
    'crosstrade'. Holds no secrets beyond what the caller passes in --
    same convention as TradovateClient."""

    def __init__(self, bearer_token: str, crosstrade_account_id: str):
        self.bearer_token = bearer_token
        self.crosstrade_account_id = crosstrade_account_id
        # 2026-11-04 -- found diagnosing a real "Cancelled: false" with no
        # context: cancel_order() must never raise (same contract as
        # TradovateClient's own), but a bare False gives zero information
        # about why. Same last_*_error pattern cme_tradovate_client.py's
        # authenticate() already uses.
        self.last_cancel_error: Optional[str] = None

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def get_balance(self) -> Dict[str, Any]:
        """GET /accounts/{account} -- CONFIRMED LIVE 2026-11-04 against the
        real MFFU account via CrossTrade's own API Playground (not a
        guess): returns `{data: {..., balance: {totalCashValue, netLiq,
        realizedPnL, openPnL, cashUSD, ...}}}`, already scoped to exactly
        this account. Deliberately NOT /cashbalances -- that endpoint
        nests balance rows two levels deep (one array per linked Tradovate
        identity/login, each with its OWN nested array of account rows),
        confirmed live the same session; this endpoint is flat and simpler.
        Raises on any unexpected response shape rather than returning a
        partial/zeroed dict -- cme_risk.py's trailing-drawdown check must
        never silently treat a failed query as zero equity."""
        url = f"{BASE_URL}/accounts/{self.crosstrade_account_id}"
        session = _get_session()
        async with session.get(url, headers=self._headers()) as resp:
            if resp.status != 200:
                raise RuntimeError(f"CrossTrade balance query failed ({resp.status}): {await resp.text()}")
            body = await resp.json()
        if not body.get("success", True):
            raise RuntimeError(f"CrossTrade balance query reported failure: {body}")
        balance = (body.get("data") or {}).get("balance") or {}
        if not balance:
            raise RuntimeError(f"CrossTrade account query returned no balance for account {self.crosstrade_account_id}: {body}")
        return {
            "cash_balance": float(balance.get("cashUSD", balance.get("totalCashValue", 0.0))),
            "total_equity": float(balance.get("netLiq", balance.get("totalCashValue", 0.0))),
            "realized_pnl": float(balance.get("realizedPnL", 0.0)),
            "open_pnl": float(balance.get("openPnL", 0.0)),
        }

    async def get_positions(self) -> List[Dict[str, Any]]:
        """GET /accounts/{account}/positions -- already scoped to this
        account server-side, unlike TradovateClient.get_positions() which
        must filter client-side across all accounts on the login."""
        url = f"{BASE_URL}/accounts/{self.crosstrade_account_id}/positions"
        session = _get_session()
        async with session.get(url, headers=self._headers()) as resp:
            if resp.status != 200:
                raise RuntimeError(f"CrossTrade position query failed ({resp.status}): {await resp.text()}")
            body = await resp.json()
        if not body.get("success", True):
            raise RuntimeError(f"CrossTrade position query reported failure: {body}")
        return body.get("data") or []

    async def get_orders(self) -> List[Dict[str, Any]]:
        """GET /accounts/{account}/orders."""
        url = f"{BASE_URL}/accounts/{self.crosstrade_account_id}/orders"
        session = _get_session()
        async with session.get(url, headers=self._headers()) as resp:
            if resp.status != 200:
                raise RuntimeError(f"CrossTrade order query failed ({resp.status}): {await resp.text()}")
            body = await resp.json()
        if not body.get("success", True):
            raise RuntimeError(f"CrossTrade order query reported failure: {body}")
        return body.get("data") or []

    async def get_order_detail(self, order_id: int) -> Optional[Dict[str, Any]]:
        """GET /accounts/{account}/orders/{id} -- a real single-order
        lookup endpoint (unlike TradovateClient's own scan-over-get_orders()
        fallback, which exists only because raw Tradovate has no equivalent
        at the level this codebase calls it)."""
        url = f"{BASE_URL}/accounts/{self.crosstrade_account_id}/orders/{order_id}"
        session = _get_session()
        async with session.get(url, headers=self._headers()) as resp:
            if resp.status == 404:
                return None
            if resp.status != 200:
                raise RuntimeError(f"CrossTrade order detail query failed ({resp.status}): {await resp.text()}")
            body = await resp.json()
        return body.get("data")

    async def get_fills_for_order(self, order_id: int) -> List[Dict[str, Any]]:
        """GET /fills/order/{orderId} -- CONFIRMED LIVE against CrossTrade's
        own endpoint catalog, 2026-11-05 (see module header, "FILLS
        ENDPOINTS"). Deliberately NOT /accounts/{account}/... like every
        other method in this file -- this one endpoint isn't account-scoped
        the same way. **SCHEMA NOT LIVE-VERIFIED** -- logs the raw response
        on every call so the field names assumed by sum_fill_fees() can be
        confirmed/corrected against a real fill."""
        url = f"{BASE_URL}/fills/order/{order_id}"
        session = _get_session()
        async with session.get(url, headers=self._headers()) as resp:
            if resp.status != 200:
                raise RuntimeError(f"CrossTrade fills-by-order query failed ({resp.status}): {await resp.text()}")
            body = await resp.json()
        if not body.get("success", True):
            raise RuntimeError(f"CrossTrade fills-by-order query reported failure: {body}")
        fills = body.get("data") or []
        print(f"[CROSSTRADE] RAW fills/order/{order_id} response (verify field names against this): {fills}")
        return fills

    async def get_fills_history(self, from_ts: str, to_ts: str, is_demo: bool = False) -> List[Dict[str, Any]]:
        """GET /fills/history -- windowed fill history, used ONLY as the
        exit-fee fallback for a flatten-based close (positions/flatten
        produces no distinct order id to query get_fills_for_order with).
        from_ts/to_ts: ISO-8601 UTC strings (e.g. dt.replace(tzinfo=
        datetime.timezone.utc).isoformat()). `environment` is a REQUIRED
        query param per CrossTrade's own endpoint catalog -- derived from
        is_demo, don't omit it. `cursor` is also listed as required with no
        documented first-page value; sent as an empty string here --
        UNCONFIRMED, flagged (see module header). Pagination beyond one
        page is NOT handled -- only safe for a narrow time window on one
        account. **SCHEMA NOT LIVE-VERIFIED** -- logs the raw response on
        every call, same as get_fills_for_order()."""
        url = f"{BASE_URL}/fills/history"
        params = {
            "from": from_ts, "to": to_ts, "account": self.crosstrade_account_id,
            "environment": "demo" if is_demo else "live", "cursor": "",
        }
        session = _get_session()
        async with session.get(url, headers=self._headers(), params=params) as resp:
            if resp.status != 200:
                raise RuntimeError(f"CrossTrade fills/history query failed ({resp.status}): {await resp.text()}")
            body = await resp.json()
        if not body.get("success", True):
            raise RuntimeError(f"CrossTrade fills/history query reported failure: {body}")
        fills = body.get("data") or []
        print(f"[CROSSTRADE] RAW fills/history response (verify field names against this): {fills}")
        return fills

    async def place_bracket_limit_order(
        self, symbol: str, action: str, qty: int,
        limit_price: float, stop_price: float, target_price: float,
    ) -> Dict[str, Any]:
        """POST /accounts/{account}/orders/place -- entry as a resting
        LIMIT with takeProfit/stopLoss attached as a server-side OSO
        bracket in the SAME call (CrossTrade's docs: "A bracketed
        placement (takeProfit and/or stopLoss) is one Tradovate placeOSO
        call"). Same mechanic cme_tradovate_client.py places directly --
        confirmed close, then a bounded passive retest fill at the
        trigger (see cme_signals.py's own header), never a Stop-Limit
        chase. Returns the raw `data` object on success (expected to
        carry an `orderId`, same contract as TradovateClient's own
        return shape) or {"error": ..., "status_code": ...} on failure --
        cme_executor.py's place_resting_bracket() checks `"error" in resp`
        and `resp.get("orderId")` identically regardless of provider.

        Instrument format: CONFIRMED LIVE 2026-11-04 (Andy's own test,
        not a guess) -- a bare root symbol was never actually tested;
        the continuous-contract alias ("ES1!") auto-resolved correctly to
        the real live front-month contract (ESZ6) for order placement.
        Uses that same proven format (f"{symbol}1!") rather than a bare
        root or a quarterly-expiring dated code that would need ongoing
        maintenance."""
        url = f"{BASE_URL}/accounts/{self.crosstrade_account_id}/orders/place"
        payload = {
            "instrument": f"{symbol}1!", "action": action.lower(), "qty": qty,
            "orderType": "limit", "limitPrice": round(limit_price, 2),
            "tif": "day",
            "takeProfit": round(target_price, 2), "stopLoss": round(stop_price, 2),
        }
        session = _get_session()
        async with session.post(url, headers=self._headers(), json=payload) as resp:
            body = await resp.json()
            if resp.status != 200 or not body.get("success", True):
                return {"error": body, "status_code": resp.status}
            # 2026-11-04 -- confirmed live (Andy's own ES1! test): this
            # endpoint's real success envelope is NOT the {success, data}
            # shape every other CrossTrade endpoint uses -- order details
            # sit under "response" instead ({"destination":..., "api":...,
            # "account":..., "instrument":..., "durationMs":..., "success":
            # true, "response": {"orderId": ...}}). The order still placed
            # for real the first time this ran against "data" -- it just
            # couldn't be displayed, so it may still be resting unseen.
            return body.get("response") or body.get("data") or {}

    async def amend_stop_to_breakeven(self, stop_order_id: int, new_be_price: float, qty: int) -> bool:
        """PUT /accounts/{account}/orders/{id}/change -- amends the live
        stop leg's price. Returns False (never raises) on any failure --
        same contract as TradovateClient.amend_stop_to_breakeven()."""
        url = f"{BASE_URL}/accounts/{self.crosstrade_account_id}/orders/{stop_order_id}/change"
        payload = {"stopPrice": round(new_be_price, 2)}
        session = _get_session()
        try:
            async with session.put(url, headers=self._headers(), json=payload) as resp:
                if resp.status != 200:
                    return False
                body = await resp.json()
                return bool(body.get("success", True))
        except Exception as e:
            print(f"[CROSSTRADE] amend_stop_to_breakeven exception: {e}")
            return False

    async def cancel_order(self, order_id: int) -> bool:
        """POST /accounts/{account}/orders/{id}/cancel. Never raises --
        same contract as TradovateClient.cancel_order(). Records the real
        failure reason on self.last_cancel_error rather than just
        returning a bare False -- found diagnosing a real "Cancelled:
        false" with zero context 2026-11-04."""
        self.last_cancel_error = None
        url = f"{BASE_URL}/accounts/{self.crosstrade_account_id}/orders/{order_id}/cancel"
        session = _get_session()
        try:
            # 2026-11-04 -- a real call with no body returned HTTP 400
            # {"error": "Invalid request"}. _headers() always declares
            # Content-Type: application/json; sending that with a
            # genuinely empty body is a common cause of exactly this kind
            # of generic rejection -- an explicit {} satisfies it.
            async with session.post(url, headers=self._headers(), json={}) as resp:
                text = await resp.text()
                if resp.status != 200:
                    self.last_cancel_error = f"HTTP {resp.status}: {text}"
                    return False
                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    body = text
                if isinstance(body, dict) and not body.get("success", True):
                    self.last_cancel_error = f"HTTP {resp.status}: {body}"
                    return False
                if isinstance(body, dict):
                    return bool(body.get("success", True))
                self.last_cancel_error = f"HTTP {resp.status}: unexpected body: {body}"
                return False
        except Exception as e:
            self.last_cancel_error = f"request exception: {e}"
            print(f"[CROSSTRADE] cancel_order exception: {e}")
            return False

    async def emergency_market_close(self, symbol: str, qty: int, direction: str) -> bool:
        """POST /accounts/{account}/positions/flatten, scoped to this one
        instrument and side -- deliberately NOT POST /positions/flatten
        (CrossTrade's global "FLATTENEVERYTHING" across ALL linked
        identities, which would reach beyond this one account). Never
        raises -- same contract as TradovateClient.emergency_market_close()."""
        url = f"{BASE_URL}/accounts/{self.crosstrade_account_id}/positions/flatten"
        payload = {"instrument": symbol, "marketPosition": "long" if direction.upper() == "LONG" else "short"}
        session = _get_session()
        try:
            async with session.post(url, headers=self._headers(), json=payload) as resp:
                if resp.status != 200:
                    return False
                body = await resp.json()
                return bool(body.get("success", True))
        except Exception as e:
            print(f"[CROSSTRADE] emergency_market_close exception: {e}")
            return False
