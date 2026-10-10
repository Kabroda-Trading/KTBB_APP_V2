# alt_matrix_market.py
# ==============================================================================
# ALT MATRIX MARKET DATA -- thin wrappers over the existing, already-
# generic market_data.py fetchers (fetch_bitunix_4h/fetch_bitunix_daily,
# confirmed_closes -- all confirmed symbol-parameterized with zero BTC/
# Traveler coupling, see the approved plan's own audit), plus ONE new
# function: fetch_funding_rate(). No funding-rate fetcher existed
# anywhere in this codebase before this file.
#
# funding_rate=None is the FAIL-CLOSED signal alt_matrix_signals.
# evaluate_d1() already expects (never silently 0%/safe, per the
# approved plan) -- every failure path below returns None, never raises,
# so a flaky funding read can never crash the signal loop, only veto
# that one symbol's entry for this cycle.
# ==============================================================================

from typing import Any, Dict, List, Optional

import market_data
from database import ExecutorAccount


async def fetch_confirmed_4h(symbol: str, target_bars: int = 400) -> List[Dict[str, Any]]:
    import time
    candles = await market_data.fetch_bitunix_4h(symbol, target_bars=target_bars)
    return market_data.confirmed_closes(candles, 14400, now_ts=time.time())


async def fetch_confirmed_daily(symbol: str, target_bars: int = 300) -> List[Dict[str, Any]]:
    import time
    candles = await market_data.fetch_bitunix_daily(symbol, target_bars=target_bars)
    return market_data.confirmed_closes(candles, 86400, now_ts=time.time())


async def fetch_funding_rate(symbol: str, account: Optional[ExecutorAccount]) -> Optional[float]:
    """Returns the current funding rate as a decimal (e.g. 0.0005 = 0.05%),
    or None on ANY failure -- no credentials, network error, unexpected
    API error code, or a response shape that doesn't match what
    executor_bitunix_client.get_funding_rate() documents (see that
    method's own docstring -- LIVE-VERIFIED by Andy against a real
    account, Step 6, 2026-10-10). Both response shapes the docs
    themselves are inconsistent about are still handled defensively
    below, since no specific shape was recorded from the live check.
    Every Bitunix call in this codebase is signed (no unauthenticated
    code path exists in BitunixClient at all, confirmed by reading
    _request()), so this still needs AN account's real credentials even
    though funding rate itself is account-agnostic market data --
    callers pass any one enabled Alt Matrix LIVE account, never
    fabricate a dummy key."""
    if account is None:
        return None
    import executor_accounts
    import executor_bitunix_client

    api_key, api_secret = executor_accounts.get_decrypted_credentials(account)
    if not api_key or not api_secret:
        return None
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)
    try:
        resp = await client.get_funding_rate(symbol.replace("/", ""))
    except Exception:
        return None
    if resp.get("code") not in (0, None):
        return None

    data = resp.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None

    rate = data.get("fundingRate")
    if rate is None:
        return None
    try:
        return float(rate)
    except (TypeError, ValueError):
        return None
