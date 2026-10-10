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


async def fetch_confirmed_4h(symbol: str, target_bars: int = 400) -> List[Dict[str, Any]]:
    import time
    candles = await market_data.fetch_bitunix_4h(symbol, target_bars=target_bars)
    return market_data.confirmed_closes(candles, 14400, now_ts=time.time())


async def fetch_confirmed_daily(symbol: str, target_bars: int = 300) -> List[Dict[str, Any]]:
    import time
    candles = await market_data.fetch_bitunix_daily(symbol, target_bars=target_bars)
    return market_data.confirmed_closes(candles, 86400, now_ts=time.time())


async def fetch_funding_rate(symbol: str) -> Optional[float]:
    """Returns the current funding rate as a decimal (e.g. 0.0005 = 0.05%),
    or None on ANY failure -- network error, unexpected API error code,
    or an unexpected response shape -- never silently guessed as 0%/safe
    (evaluate_d1()'s own fail-closed contract).

    No account/credentials parameter -- DeepSeek's live smoke test
    (2026-10-10 10:28 CT, CC_INTERFACE.md section 6) confirmed this
    specific Bitunix endpoint is genuinely PUBLIC and UNAUTHENTICATED,
    unlike every other call in this codebase. An earlier, doc-sourced-
    only draft of this function required an account's real credentials
    purely because every OTHER BitunixClient method does -- that
    requirement is gone now that the real behavior is known, via
    executor_bitunix_client.fetch_public_funding_rate() (a module-level
    function, not a BitunixClient instance method, for the same reason).

    Verified real response shape (2026-10-10): `data` is a single
    object (not the array the docs' own worked example showed) --
    handled as such below; an array is no longer expected, but still
    tolerated defensively since it costs nothing and the docs'
    inconsistency was never fully explained."""
    import executor_bitunix_client

    try:
        resp = await executor_bitunix_client.fetch_public_funding_rate(symbol.replace("/", ""))
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
