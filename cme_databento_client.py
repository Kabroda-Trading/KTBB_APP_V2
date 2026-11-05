# cme_databento_client.py
# ==============================================================================
# DATABENTO HISTORICAL MARKET DATA CLIENT -- the market-data source for CME
# D1 (1-minute calibration bars) / D2 (5-minute cross/retest bars), added
# 2026-11-04 once direct Tradovate Market Data access was confirmed blocked
# by the SAME gate as trading access: Tradovate's own docs show accessToken
# and mdAccessToken come from the identical /auth/accesstokenrequest call --
# if that call can't issue a trading token for an account, it can't issue a
# market-data token for it either (confirmed, not inferred -- see
# AGENT_LOG.md, both repos, 2026-11-04). CrossTrade (the execution path) has
# no market-data surface at all on its Tradovate-linked side either --
# confirmed directly against its real endpoint list and OpenAPI spec; bars/
# quotes exist only on CrossTrade's NT8-add-on surface, which would require
# a persistently-running NinjaTrader desktop -- ruled out the same day.
#
# Databento is an officially CME-recognized data vendor (cmegroup.com lists
# it under Technology Vendor Services), billed pay-as-you-go by actual
# consumption -- a good fit for this system's real volume (~30 one-minute
# bars/day/symbol for D1, a handful of polls/day for D2, not a tick
# firehose).
#
# CONFIRMED against Databento's own docs (fetched directly, not guessed):
#   dataset="GLBX.MDP3" (CME Globex MDP 3.0), schema="ohlcv-1m" (there is
#   NO native 5-minute schema -- only ohlcv-1s/1m/1h/1d exist; cme_market.py
#   aggregates 1m bars into 5m bars itself), stype_in="continuous",
#   symbols="MES.c.0"/"MNQ.c.0" (front-month continuous, calendar-rule rank
#   0 -- "c" offsets from the closest expiration, confirmed as the correct
#   front-month rule, distinct from "n"/"v" which rank by open interest/
#   volume and can diverge from the calendar front month near rollover).
#   Record fields: ts_event (uint64, nanoseconds since epoch, marks the bar's
#   INCLUSIVE START), open/high/low/close (int64, FIXED-POINT, 1 unit =
#   1e-9 -- must divide by 1e9 for a real decimal price), volume (uint64).
#   Auth: HTTP Basic, the API key as the username, password left blank
#   (every Databento API key is a 32-char string starting with "db-").
#
# **NOT YET LIVE-VERIFIED -- flagged, not presented as certain**: the exact
# raw HTTP endpoint/verb for timeseries.get_range. Databento's public docs
# document the Python SDK's method signature in detail but no worked curl
# example for the equivalent raw HTTP call was found. Built here as a GET
# request with query parameters against the documented base URL pattern
# ("https://hist.databento.com/v0/METHOD_FAMILY.METHOD") -- the most
# RESTful reading of a read-only range query. Confirm on the first real
# call once an API key exists, same as every other NOT YET LIVE-VERIFIED
# item in this build (Tradovate order responses, CrossTrade order
# responses, both confirmed/corrected against live data the same way).
# ==============================================================================

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List

import aiohttp

BASE_URL = "https://hist.databento.com/v0"
DATASET = "GLBX.MDP3"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15.0)

# Front-month continuous contract, calendar rule (rank 0) -- confirmed
# format directly from Databento's symbology docs. Add new roots here if
# this system ever trades a third CME symbol.
_CONTINUOUS_SYMBOL = {"MES": "MES.c.0", "MNQ": "MNQ.c.0"}


class DatabentoClient:
    """Stateless aside from the API key -- deliberately NOT scoped to an
    ExecutorAccount the way every exchange client in this codebase is.
    Market data for MES/MNQ is identical regardless of which Tradovate/
    CrossTrade account is trading it, so this reads a single shared
    DATABENTO_API_KEY env var (same "global infra credential, not a
    per-account encrypted one" convention as COINALYZE_API_KEY), not
    anything decrypted per-account."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def get_ohlcv_1m_bars(
        self, root_symbol: str, start_utc: datetime.datetime, end_utc: datetime.datetime,
    ) -> List[Dict[str, Any]]:
        """Returns 1-minute bars in chronological order as
        {"ts_event": datetime (UTC, bar start), "open","high","low","close": float}
        dicts -- real decimal prices, already descaled from Databento's
        fixed-point integers. cme_market.py aggregates these into 5-minute
        bars itself where needed; this client stays a thin, faithful I/O
        wrapper, same layering as every other exchange client here."""
        symbol = _CONTINUOUS_SYMBOL.get(root_symbol.upper())
        if symbol is None:
            raise ValueError(f"no continuous-contract symbol mapping for root '{root_symbol}' -- add it to _CONTINUOUS_SYMBOL")

        params = {
            "dataset": DATASET,
            "symbols": symbol,
            "schema": "ohlcv-1m",
            "stype_in": "continuous",
            "start": start_utc.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": end_utc.strftime("%Y-%m-%dT%H:%M:%S"),
            "encoding": "json",
        }
        auth = aiohttp.BasicAuth(login=self.api_key, password="")
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.get(f"{BASE_URL}/timeseries.get_range", params=params, auth=auth) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"Databento bars query failed ({resp.status}): {text}")

        bars = []
        for line in text.strip().splitlines():
            if not line:
                continue
            record = json.loads(line)
            ts_ns = int(record["ts_event"])
            bars.append({
                "ts_event": datetime.datetime.fromtimestamp(ts_ns / 1e9, tz=datetime.timezone.utc),
                "open": record["open"] / 1e9,
                "high": record["high"] / 1e9,
                "low": record["low"] / 1e9,
                "close": record["close"] / 1e9,
            })
        bars.sort(key=lambda b: b["ts_event"])   # JSON-lines order isn't contractually guaranteed sorted
        return bars
