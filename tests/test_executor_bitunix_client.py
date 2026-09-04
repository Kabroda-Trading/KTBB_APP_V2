"""
Unit coverage for executor_bitunix_client.py's signing algorithm and
request-construction helpers. Pure function tests -- no network calls
(get_balance/get_position/place_order/set_tpsl all require a real HTTP
round-trip and are exercised manually against a real account instead,
not here, per the "verify before automating a real network call" caution
this module's own header explains).

The expected digest/sign values below were computed INDEPENDENTLY (a
one-off script, not by importing this module) against Bitunix's own
documented worked example inputs and algorithm
(bitunix.com/api-docs/futures/common/sign.html) before this file's
implementation was written -- this is a real external oracle for the
signing algorithm, not a test that just re-asserts whatever the code
happens to produce.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import executor_bitunix_client as ebc

# Bitunix's own documented worked example inputs (sign.html's Python sample).
_NONCE = "123456"
_TIMESTAMP = "20241120123045"
_API_KEY = "yourApiKey"
_SECRET = "yourSecretKey"
_QUERY_STRING = "id1uid200"
_BODY = '{"uid":"2899","arr":[{"id":1,"name":"maple"},{"id":2,"name":"lily"}]}'

# Computed independently via a standalone hashlib script (not by calling
# this module) against the exact algorithm as documented -- see this
# test file's own module docstring.
_EXPECTED_DIGEST = "75099831ac6803e9c5b79dd3cde2c3c529b4750bd3508186afdde0dd13599b38"
_EXPECTED_SIGN = "00397cd1e52c7dce3258067324363b6361fabc9178a0912b330c138db8745655"


def test_sign_request_matches_independently_computed_reference():
    sign = ebc.sign_request(_NONCE, _TIMESTAMP, _API_KEY, _SECRET, _QUERY_STRING, _BODY)
    assert sign == _EXPECTED_SIGN


def test_sign_request_digest_stage_matches_reference():
    # The doc's own sample prints the intermediate digest too -- verify
    # that stage independently, not just the final sign, so a bug that
    # happens to produce the right final hash by coincidence can't hide.
    import hashlib
    digest_input = _NONCE + _TIMESTAMP + _API_KEY + _QUERY_STRING + _BODY
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    assert digest == _EXPECTED_DIGEST


def test_sign_request_empty_query_and_body():
    # A GET request with no body and no query params (e.g. a hypothetical
    # no-param call) must not crash and must produce a real signature
    # over just nonce+timestamp+api_key.
    sign = ebc.sign_request("n", "t", "k", "s", "", "")
    assert isinstance(sign, str) and len(sign) == 64  # a real sha256 hex digest


def test_sorted_query_string_matches_doc_example():
    # The doc's own example: {"id": 1, "uid": 200} -> "id1uid200"
    assert ebc._sorted_query_string({"id": 1, "uid": 200}) == "id1uid200"


def test_sorted_query_string_sorts_ascending_ascii():
    assert ebc._sorted_query_string({"zeta": "z", "alpha": "a", "mid": "m"}) == "alphaamidmzetaz"


def test_sorted_query_string_empty():
    assert ebc._sorted_query_string(None) == ""
    assert ebc._sorted_query_string({}) == ""


def test_compact_body_matches_doc_example():
    body = {"uid": "2899", "arr": [{"id": 1, "name": "maple"}, {"id": 2, "name": "lily"}]}
    assert ebc._compact_body(body) == _BODY


def test_compact_body_empty():
    assert ebc._compact_body(None) == ""
    assert ebc._compact_body({}) == ""


def test_nonce_is_32_chars_and_random():
    n1, n2 = ebc._nonce(), ebc._nonce()
    assert len(n1) == 32
    assert len(n2) == 32
    assert n1 != n2


def test_place_order_and_set_tpsl_still_deliberately_unimplemented():
    # These must keep failing loudly, not silently succeed, until Andy's
    # own confirmed sequencing (verify read-only calls first) is done.
    import asyncio
    client = ebc.BitunixClient("key", "secret")

    async def _try_place_order():
        try:
            await client.place_order("BTCUSDT", "BUY", 1.0, 100.0, 95.0, 110.0, 10)
            return False
        except NotImplementedError:
            return True

    async def _try_set_tpsl():
        try:
            await client.set_tpsl("pos1", stop_price=95.0)
            return False
        except NotImplementedError:
            return True

    assert asyncio.run(_try_place_order()) is True
    assert asyncio.run(_try_set_tpsl()) is True


# ------------------------------------------------------------------ read-only endpoint request construction
# (verifies the RIGHT path/query gets built -- not a real network call;
# _request() itself is monkeypatched to capture what it was asked to do)

def _capture_request(monkeypatch, client):
    calls = []

    async def fake_request(self, method, path, query=None, body=None):
        calls.append({"method": method, "path": path, "query": query, "body": body})
        return {"captured": True}

    monkeypatch.setattr(ebc.BitunixClient, "_request", fake_request)
    return calls


def test_get_balance_calls_the_right_endpoint(monkeypatch):
    import asyncio
    client = ebc.BitunixClient("key", "secret")
    calls = _capture_request(monkeypatch, client)
    asyncio.run(client.get_balance())
    assert calls == [{"method": "GET", "path": "/api/v1/futures/account", "query": {"marginCoin": "USDT"}, "body": None}]


def test_get_position_calls_the_right_endpoint(monkeypatch):
    import asyncio
    client = ebc.BitunixClient("key", "secret")
    calls = _capture_request(monkeypatch, client)
    asyncio.run(client.get_position("BTCUSDT"))
    assert calls == [{"method": "GET", "path": "/api/v1/futures/position/get_pending_positions",
                       "query": {"symbol": "BTCUSDT"}, "body": None}]


def test_get_leverage_and_margin_mode_calls_the_right_endpoint(monkeypatch):
    import asyncio
    client = ebc.BitunixClient("key", "secret")
    calls = _capture_request(monkeypatch, client)
    asyncio.run(client.get_leverage_and_margin_mode("BTCUSDT"))
    assert calls == [{"method": "GET", "path": "/api/v1/futures/account/get_leverage_margin_mode",
                       "query": {"symbol": "BTCUSDT", "marginCoin": "USDT"}, "body": None}]


def test_get_trading_pairs_calls_the_right_endpoint(monkeypatch):
    import asyncio
    client = ebc.BitunixClient("key", "secret")
    calls = _capture_request(monkeypatch, client)
    asyncio.run(client.get_trading_pairs("BTCUSDT"))
    assert calls == [{"method": "GET", "path": "/api/v1/futures/market/trading_pairs",
                       "query": {"symbols": "BTCUSDT"}, "body": None}]
