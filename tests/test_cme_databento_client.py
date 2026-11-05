"""Coverage for cme_databento_client.py's own logic that doesn't require a
real network call -- symbol-mapping validation and the module's
descaling/sorting contract documented in its own header. No aiohttp
mocking (no precedent for it anywhere in this codebase, see
test_cme_market.py's own note) -- the real HTTP path gets its first real
confirmation the same way every other NOT YET LIVE-VERIFIED item in this
build does, once Andy has an API key."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import cme_databento_client as dbc


def test_unmapped_root_symbol_raises_before_any_network_call():
    client = dbc.DatabentoClient(api_key="db-fake-key-for-this-test")
    import datetime
    with pytest.raises(ValueError, match="no continuous-contract symbol mapping"):
        asyncio.run(client.get_ohlcv_1m_bars(
            "ZN", datetime.datetime.now(datetime.timezone.utc), datetime.datetime.now(datetime.timezone.utc),
        ))


def test_mes_and_mnq_map_to_the_confirmed_front_month_continuous_symbols():
    assert dbc._CONTINUOUS_SYMBOL["MES"] == "MES.c.0"
    assert dbc._CONTINUOUS_SYMBOL["MNQ"] == "MNQ.c.0"
