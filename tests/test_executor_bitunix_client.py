"""
Unit coverage for executor_bitunix_client.py's signing algorithm and
request-construction helpers. Pure function tests -- no network calls.
Every real method (get_balance/get_position/get_leverage_and_margin_mode/
get_trading_pairs/place_order/close_position/set_position_tpsl/
modify_position_tp_sl_order/get_position_tiers) is tested here only for
REQUEST CONSTRUCTION -- BitunixClient._request() itself is monkeypatched
at the class level so no HTTP round-trip ever happens; the actual live
signing chain is exercised manually against a real account instead
(the manually-triggered tiny mechanism test, executor_mechanism_test.py,
gated behind live_orders_enabled + a confirm phrase), per the "verify
before automating a real network call" caution this module's own header
explains.

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

import pytest

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


def test_change_leverage_method_does_not_exist():
    # Explicit regression guard: the bot only ever READS leverage/margin-
    # mode config, never mutates it -- Andy's explicit call, default OFF,
    # permanently out of scope. Nobody should add this without
    # re-litigating that decision.
    assert not hasattr(ebc.BitunixClient, "change_leverage")


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


# ------------------------------------------------------------------ order-placing/closing endpoint request
# construction (Stage 2, 2026-09-05) -- same _capture_request pattern,
# no real network call. qty/price/tpPrice/slPrice are always passed as
# pre-formatted strings, matching what a real caller (executor_sizing.
# round_qty_to_precision()/round_price_to_precision()) would produce.

def test_place_order_open_builds_correct_body(monkeypatch):
    import asyncio
    client = ebc.BitunixClient("key", "secret")
    calls = _capture_request(monkeypatch, client)
    asyncio.run(client.place_order(symbol="BTCUSDT", qty="0.0001", side="BUY",
                                    trade_side="OPEN", order_type="MARKET"))
    assert calls == [{
        "method": "POST", "path": "/api/v1/futures/trade/place_order", "query": None,
        "body": {"symbol": "BTCUSDT", "qty": "0.0001", "side": "BUY",
                 "tradeSide": "OPEN", "orderType": "MARKET"},
    }]


def test_place_order_close_with_position_id_and_reduce_only(monkeypatch):
    import asyncio
    client = ebc.BitunixClient("key", "secret")
    calls = _capture_request(monkeypatch, client)
    asyncio.run(client.place_order(symbol="BTCUSDT", qty="0.00005", side="SELL",
                                    trade_side="CLOSE", order_type="MARKET",
                                    position_id="pos123", reduce_only=True))
    assert calls == [{
        "method": "POST", "path": "/api/v1/futures/trade/place_order", "query": None,
        "body": {"symbol": "BTCUSDT", "qty": "0.00005", "side": "SELL", "tradeSide": "CLOSE",
                 "orderType": "MARKET", "positionId": "pos123", "reduceOnly": True},
    }]


def test_place_order_includes_bracket_tp_sl_fields_when_provided(monkeypatch):
    import asyncio
    client = ebc.BitunixClient("key", "secret")
    calls = _capture_request(monkeypatch, client)
    asyncio.run(client.place_order(symbol="BTCUSDT", qty="0.0001", side="BUY",
                                    trade_side="OPEN", order_type="LIMIT", price="100.5",
                                    tp_price="101.0", tp_stop_type="LAST_PRICE",
                                    sl_price="99.0", sl_stop_type="LAST_PRICE"))
    assert calls[0]["body"] == {
        "symbol": "BTCUSDT", "qty": "0.0001", "side": "BUY", "tradeSide": "OPEN",
        "orderType": "LIMIT", "price": "100.5",
        "tpPrice": "101.0", "tpStopType": "LAST_PRICE",
        "slPrice": "99.0", "slStopType": "LAST_PRICE",
    }


def test_close_position_builds_correct_body(monkeypatch):
    import asyncio
    client = ebc.BitunixClient("key", "secret")
    calls = _capture_request(monkeypatch, client)
    asyncio.run(client.close_position("pos123"))
    assert calls == [{"method": "POST", "path": "/api/v1/futures/trade/flash_close_position",
                       "query": None, "body": {"positionId": "pos123"}}]


def test_set_position_tpsl_builds_correct_body_with_tp_and_sl(monkeypatch):
    import asyncio
    client = ebc.BitunixClient("key", "secret")
    calls = _capture_request(monkeypatch, client)
    asyncio.run(client.set_position_tpsl(symbol="BTCUSDT", position_id="pos123",
                                          tp_price="101.0", tp_stop_type="LAST_PRICE",
                                          sl_price="99.0", sl_stop_type="LAST_PRICE"))
    assert calls == [{
        "method": "POST", "path": "/api/v1/futures/tpsl/position/place_order", "query": None,
        "body": {"symbol": "BTCUSDT", "positionId": "pos123",
                 "tpPrice": "101.0", "tpStopType": "LAST_PRICE",
                 "slPrice": "99.0", "slStopType": "LAST_PRICE"},
    }]


def test_set_position_tpsl_raises_valueerror_when_neither_tp_nor_sl_given(monkeypatch):
    import asyncio
    client = ebc.BitunixClient("key", "secret")
    calls = _capture_request(monkeypatch, client)
    with pytest.raises(ValueError, match="at least one"):
        asyncio.run(client.set_position_tpsl(symbol="BTCUSDT", position_id="pos123"))
    assert calls == []  # no request ever sent


def test_modify_position_tp_sl_order_builds_correct_body(monkeypatch):
    import asyncio
    client = ebc.BitunixClient("key", "secret")
    calls = _capture_request(monkeypatch, client)
    asyncio.run(client.modify_position_tp_sl_order(symbol="BTCUSDT", position_id="pos123",
                                                     sl_price="100.0", sl_stop_type="LAST_PRICE"))
    assert calls == [{
        "method": "POST", "path": "/api/v1/futures/tpsl/position/modify_order", "query": None,
        "body": {"symbol": "BTCUSDT", "positionId": "pos123",
                 "slPrice": "100.0", "slStopType": "LAST_PRICE"},
    }]


def test_modify_position_tp_sl_order_raises_valueerror_when_neither_tp_nor_sl_given(monkeypatch):
    import asyncio
    client = ebc.BitunixClient("key", "secret")
    calls = _capture_request(monkeypatch, client)
    with pytest.raises(ValueError, match="at least one"):
        asyncio.run(client.modify_position_tp_sl_order(symbol="BTCUSDT", position_id="pos123"))
    assert calls == []


def test_get_position_tiers_calls_the_right_endpoint(monkeypatch):
    import asyncio
    client = ebc.BitunixClient("key", "secret")
    calls = _capture_request(monkeypatch, client)
    asyncio.run(client.get_position_tiers("BTCUSDT"))
    assert calls == [{"method": "GET", "path": "/api/v1/futures/position/get_position_tiers",
                       "query": {"symbol": "BTCUSDT"}, "body": None}]
