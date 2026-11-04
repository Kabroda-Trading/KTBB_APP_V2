# cme_market.py
# ==============================================================================
# CME MARKET DATA -- fetches confirmed candles for D1 (1-minute calibration
# bars) and D2/D3 (5-minute bars) directly from Tradovate's OWN quote feed
# for the actual traded contract (MNQZ6/ESZ6), per this build's own Part-0
# finding: the one live daemon script in the Kabroda AI Brain repo computed
# its decision inputs from yfinance's free, delayed NQ=F continuous proxy
# instead -- the same class of decision-feed-vs-execution-venue mismatch
# already found and ruled on once for BTC (CC_INTERFACE.md item L2, Kraken
# vs Bitunix). Trading MNQZ6/ESZ6 off a Tradovate-native feed, not a Yahoo
# proxy, avoids reintroducing that exact problem here.
#
# **HONEST GAP, NOT A GUESS**: Tradovate's REAL-TIME/HISTORICAL MARKET DATA
# API (chart bars, quotes) is a SEPARATE product from the trading REST API
# cme_tradovate_client.py already implements (Tradovate calls it the
# "Market Data API" / md-api, typically a different base URL and often a
# WebSocket for live quotes plus a REST history endpoint for bars) -- no
# method shape for it has been confirmed against a real response anywhere
# in this codebase or the Kabroda AI Brain repo's own committed files.
# Fabricating a plausible-but-unverified endpoint here would be exactly the
# kind of "confident but unconfirmed" code this project's own standing
# discipline exists to catch -- especially dangerous for something that
# would silently drive a live cross/retest decision. fetch_confirmed_5m()
# and fetch_calibration_1m() below raise NotImplementedError with this
# explanation rather than guess. This is the one remaining live-API
# research item before cme_engine.py's watch loop can run for real --
# same category as Alt Matrix's own Step 6 ("Andy runs the live-API checks
# this plan can't resolve from reading code alone").
# ==============================================================================

from typing import Any, Dict, List


async def fetch_calibration_1m(client: "cme_tradovate_client.TradovateClient", symbol: str) -> List[Dict[str, Any]]:
    """Should return the real 1-minute bars for today's 9:30-10:00 ET
    calibration window, in the shape cme_signals.compute_tpo_value_area()
    expects ({"open","high","low","close"} per bar). NOT YET IMPLEMENTED --
    see module header."""
    raise NotImplementedError(
        "cme_market.fetch_calibration_1m() needs Tradovate's real Market Data "
        "API confirmed against a live response before this can run for real "
        "-- see this module's own header. Do not fabricate a response shape."
    )


async def fetch_confirmed_5m(client: "cme_tradovate_client.TradovateClient", symbol: str, since_utc) -> List[Dict[str, Any]]:
    """Should return confirmed (fully closed) 5-minute bars for `symbol`
    since `since_utc`, in the shape cme_signals.check_cross()/
    check_retest_fill() expect. NOT YET IMPLEMENTED -- see module header."""
    raise NotImplementedError(
        "cme_market.fetch_confirmed_5m() needs Tradovate's real Market Data "
        "API confirmed against a live response before this can run for real "
        "-- see this module's own header. Do not fabricate a response shape."
    )
