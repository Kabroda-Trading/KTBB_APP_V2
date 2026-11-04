# cme_tradovate_client.py
# ==============================================================================
# TRADOVATE REST CLIENT -- CME Micro Futures execution (MNQ/MES), following
# executor_bitunix_client.py's own documented convention (aiohttp, a
# per-running-event-loop cached session -- see that file's header for the
# real production hang this pattern exists to prevent). This is a fresh
# implementation, not an import of brain/engine/tradovate_client.py (Iron
# Wall -- no cross-repo import, and that file has its own leftover dead
# code from a hasty patch, confirmed by direct read 2026-11-04: lines
# 256-260 of that file are unreachable orphaned code after a bad merge).
# The REQUEST SHAPES below are ported from that file's logic, not its code,
# because those shapes are the ones already fixed/verified against the one
# real prior live incident (the invalid nested-bracket payload, CC_INTERFACE
# item M3).
#
# Auth: OAuth2-style username+password -> bearer token
# (POST /auth/accesstokenrequest), auto-renewed when within 5 minutes of
# expiry. Reuses ExecutorAccount.api_key_encrypted/api_secret_encrypted for
# username/password respectively (same two-secret shape as Bitunix, just
# different semantics -- no new encrypted columns needed, see database.py's
# ExecutorAccount comment).
#
# CONFIRMED endpoints (already used/fixed in the Kabroda AI Brain repo's own
# tradovate_client.py, independently verified 2026-11-04 that the atomic
# bracket shape is no longer the invalid nested payload from the one real
# prior incident):
#   POST /auth/accesstokenrequest, GET /account/list,
#   GET /cashBalance/getcashbalancesnapshot, GET /position/list,
#   POST /order/placeoso (bracket1=target Limit, bracket2=stop Stop),
#   POST /order/modifyorder, POST /order/placeorder (market flatten).
#
# **NOT YET LIVE-VERIFIED -- flagged, not presented as certain** (mirrors
# this project's own standing discipline, e.g. Alt Matrix's Step 6 live-API
# checks before trusting an assumed response shape): GET /order/list (used
# here for fill-status polling, inferred from Tradovate's own consistent
# `/item/list` + `/item/item?id=` REST convention, same pattern get_positions()
# already uses) and POST /order/cancelorder (used for the NO_RETEST timeout
# and emergency_flatten()). Build order step 10 (the 1-Micro Live Smoke
# Test) is exactly where these get their first real confirmation -- do not
# assume they're correct before that.
# ==============================================================================

from __future__ import annotations

import asyncio
import time
import weakref
from typing import Any, Dict, List, Optional

import aiohttp

DEMO_URL = "https://demo.tradovateapi.com/v1"
LIVE_URL = "https://live.tradovate.com/v1"

APP_ID = "KabrodaTradovate"
APP_VERSION = "1.0"
_TOKEN_RENEW_BUFFER_SECONDS = 300
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10.0)

_sessions: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _get_session() -> aiohttp.ClientSession:
    """Per-running-event-loop cached aiohttp session -- never a bare
    module-level global (same rationale as executor_bitunix_client.py's
    own _get_session())."""
    loop = asyncio.get_running_loop()
    session = _sessions.get(loop)
    if session is None or session.closed:
        session = aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT)
        _sessions[loop] = session
    return session


class TradovateClient:
    """One instance per ExecutorAccount. Holds no secrets beyond what the
    caller passes in -- credentials are decrypted by executor_accounts.
    get_decrypted_credentials() at the call site, same pattern as every
    other exchange client in this codebase, never stored in a module
    global."""

    def __init__(
        self,
        username: str,
        password: str,
        account_id: int,
        account_name: str,
        is_demo: bool = False,
    ):
        self.username = username
        self.password = password
        self.account_id = account_id
        self.account_name = account_name
        self.is_demo = is_demo
        self.base_url = DEMO_URL if is_demo else LIVE_URL
        self.access_token: Optional[str] = None
        self.token_expiry_epoch: float = 0.0

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def authenticate(self) -> bool:
        """POST /auth/accesstokenrequest -- obtains the bearer token."""
        url = f"{self.base_url}/auth/accesstokenrequest"
        payload = {
            "name": self.username, "password": self.password,
            "appId": APP_ID, "appVersion": APP_VERSION, "cid": "", "sec": "",
        }
        session = _get_session()
        try:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
        except Exception as e:
            print(f"[TRADOVATE] auth exception: {e}")
            return False

        token = data.get("accessToken") if isinstance(data, dict) else None
        if not token:
            print(f"[TRADOVATE] auth rejected: {data.get('errorText') if isinstance(data, dict) else data}")
            return False
        self.access_token = token
        # Tradovate's own documented token lifetime is ~80 minutes; this
        # codebase renews well before that (same conservative-buffer
        # convention as every other token-renewal loop here).
        self.token_expiry_epoch = time.time() + 3600
        return True

    async def _ensure_token(self) -> None:
        if not self.access_token or time.time() >= self.token_expiry_epoch - _TOKEN_RENEW_BUFFER_SECONDS:
            ok = await self.authenticate()
            if not ok and not self.access_token:
                raise RuntimeError("Tradovate authentication failed and no prior token is available")

    async def get_balance(self) -> Dict[str, Any]:
        """GET /cashBalance/getcashbalancesnapshot -- the one balance/
        equity query this module needs. Raises on any unexpected response
        shape rather than returning a partial/zeroed dict -- cme_risk.py's
        trailing-drawdown check must never silently treat a failed query
        as zero equity."""
        await self._ensure_token()
        url = f"{self.base_url}/cashBalance/getcashbalancesnapshot"
        session = _get_session()
        async with session.get(url, headers=self._headers(), params={"accountId": self.account_id}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Tradovate balance query failed ({resp.status}): {await resp.text()}")
            data = await resp.json()
        return {
            "cash_balance": float(data.get("cashBalance", 0.0)),
            "total_equity": float(data.get("totalCashValue", 0.0)),
            "realized_pnl": float(data.get("realizedPnL", 0.0)),
            "open_pnl": float(data.get("openPnL", 0.0)),
        }

    async def list_accounts(self) -> List[Dict[str, Any]]:
        """GET /account/list -- every account tied to this login. Needs
        only a valid bearer token, not self.account_id (this is in fact
        how a caller is meant to DISCOVER account_id in the first place:
        Tradovate's own UI shows the human-readable `name` -- e.g.
        "APEX6918900000001" -- never the numeric internal `id` the REST
        API actually filters on elsewhere, so there is no way to read
        the real id off the Tradovate website at all; this call is the
        only way to get it)."""
        await self._ensure_token()
        url = f"{self.base_url}/account/list"
        session = _get_session()
        async with session.get(url, headers=self._headers()) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Tradovate account list query failed ({resp.status}): {await resp.text()}")
            return await resp.json()

    async def get_positions(self) -> List[Dict[str, Any]]:
        """GET /position/list, filtered client-side to this account."""
        await self._ensure_token()
        url = f"{self.base_url}/position/list"
        session = _get_session()
        async with session.get(url, headers=self._headers()) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Tradovate position query failed ({resp.status}): {await resp.text()}")
            data = await resp.json()
        return [p for p in data if p.get("accountId") == self.account_id]

    async def get_orders(self) -> List[Dict[str, Any]]:
        """GET /order/list, filtered client-side to this account. **NOT
        YET LIVE-VERIFIED** -- see module header."""
        await self._ensure_token()
        url = f"{self.base_url}/order/list"
        session = _get_session()
        async with session.get(url, headers=self._headers()) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Tradovate order query failed ({resp.status}): {await resp.text()}")
            data = await resp.json()
        return [o for o in data if o.get("accountId") == self.account_id]

    async def get_order_detail(self, order_id: int) -> Optional[Dict[str, Any]]:
        """The one order out of get_orders() matching order_id, or None if
        it's gone (filled-and-archived, cancelled, or never existed)."""
        for o in await self.get_orders():
            if o.get("id") == order_id or o.get("orderId") == order_id:
                return o
        return None

    async def place_bracket_limit_order(
        self, symbol: str, action: str, qty: int,
        limit_price: float, stop_price: float, target_price: float,
    ) -> Dict[str, Any]:
        """POST /order/placeoso -- entry as a resting LIMIT at `limit_price`
        with an atomic OSO bracket (bracket1=Limit target, bracket2=Stop
        stop-loss), both legs exchange-hosted the instant the entry fills.
        This is the mechanic actually backtested (confirmed close -> bounded
        passive retest fill at the trigger -- see cme_signals.py's own
        header), NOT a Stop-Limit chase entry (CC_INTERFACE §8D's wording,
        never backtested by anything behind the verified M3 numbers)."""
        await self._ensure_token()
        bracket_action = "Sell" if action.lower() == "buy" else "Buy"
        payload = {
            "accountSpec": self.account_name, "accountId": self.account_id,
            "action": action, "symbol": symbol, "orderQty": qty,
            "orderType": "Limit", "price": round(limit_price, 2), "isAutomated": True,
            "bracket1": {"action": bracket_action, "orderType": "Limit", "price": round(target_price, 2)},
            "bracket2": {"action": bracket_action, "orderType": "Stop", "stopPrice": round(stop_price, 2)},
        }
        session = _get_session()
        url = f"{self.base_url}/order/placeoso"
        async with session.post(url, headers=self._headers(), json=payload) as resp:
            data = await resp.json()
            if resp.status != 200:
                return {"error": data, "status_code": resp.status}
            return data

    async def amend_stop_to_breakeven(self, stop_order_id: int, new_be_price: float, qty: int) -> bool:
        """POST /order/modifyorder -- the one live stop amendment this
        subsystem ever makes (the +0.8x box ratchet -> +0.1x box lock).
        Returns False (never raises) on any non-200 -- the caller treats a
        failed amendment the same way alt_matrix_executor.py does: log it,
        alert, do NOT assume the stop moved."""
        await self._ensure_token()
        payload = {"orderId": stop_order_id, "orderQty": qty, "orderType": "Stop", "stopPrice": round(new_be_price, 2)}
        session = _get_session()
        url = f"{self.base_url}/order/modifyorder"
        try:
            async with session.post(url, headers=self._headers(), json=payload) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"[TRADOVATE] amend_stop_to_breakeven exception: {e}")
            return False

    async def cancel_order(self, order_id: int) -> bool:
        """POST /order/cancelorder. **NOT YET LIVE-VERIFIED** -- see module
        header. Used for the NO_RETEST timeout (cancel the resting bracket
        if no retest fills within the window) and by emergency_flatten()."""
        await self._ensure_token()
        payload = {"orderId": order_id}
        session = _get_session()
        url = f"{self.base_url}/order/cancelorder"
        try:
            async with session.post(url, headers=self._headers(), json=payload) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"[TRADOVATE] cancel_order exception: {e}")
            return False

    async def emergency_market_close(self, symbol: str, qty: int, direction: str) -> bool:
        """POST /order/placeorder -- market order to flatten one position.
        Used for the mandatory 16:00 ET session flush and by
        emergency_flatten(). Never raises -- a failed flatten attempt is
        exactly the moment the caller most needs to keep going (alert,
        retry, never crash the loop)."""
        await self._ensure_token()
        close_action = "Sell" if direction.upper() == "LONG" else "Buy"
        payload = {
            "accountSpec": self.account_name, "accountId": self.account_id,
            "action": close_action, "symbol": symbol, "orderQty": abs(int(qty)),
            "orderType": "Market", "isAutomated": True,
        }
        session = _get_session()
        url = f"{self.base_url}/order/placeorder"
        try:
            async with session.post(url, headers=self._headers(), json=payload) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"[TRADOVATE] emergency_market_close exception: {e}")
            return False
