"""
Unit coverage for cme_crosstrade_client.py's new fee-capture methods
(get_fills_for_order/get_fills_history) and the sum_fill_fees() pure
helper -- fee-capture build, 2026-11-xx.

No aiohttp-mocking precedent existed anywhere in this codebase before this
file (confirmed via grep -- tests/test_cme_market.py's own header says so
explicitly). CrossTradeClient calls session.get(...) directly (unlike
BitunixClient's single _request() chokepoint), so this file builds a
small fake aiohttp session/response harness instead of monkeypatching one
method. The fake response object implements __aenter__/__aexit__ to match
aiohttp's real `async with session.get(...) as resp:` usage, and
FakeSession.get() is a plain (non-async) method returning that object
directly -- matching aiohttp's own ClientSession.get(), which returns a
context manager, not a coroutine.
"""
import asyncio
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import cme_crosstrade_client as ctc


class _FakeResponse:
    def __init__(self, status=200, json_body=None, text_body="", json_raises=False):
        self.status = status
        self._json_body = json_body if json_body is not None else {}
        self._text_body = text_body
        self._json_raises = json_raises

    async def json(self, content_type=None):
        if self._json_raises:
            raise ValueError("not JSON")
        return self._json_body

    async def text(self):
        return self._text_body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls = []

    def get(self, url, headers=None, params=None):
        self.calls.append({"url": url, "headers": headers, "params": params})
        return self._response

    def post(self, url, headers=None, params=None, json=None):
        self.calls.append({"url": url, "headers": headers, "params": params, "method": "POST", "json": json})
        return self._response


def _install(monkeypatch, response: _FakeResponse) -> _FakeSession:
    session = _FakeSession(response)
    monkeypatch.setattr(ctc, "_get_session", lambda: session)
    return session


# ------------------------------------------------------------------ get_fills_for_order

def test_get_fills_for_order_hits_the_right_url_with_bearer_auth(monkeypatch):
    response = _FakeResponse(status=200, json_body={"success": True, "data": [{"orderId": "123"}]})
    session = _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    result = asyncio.run(client.get_fills_for_order(123))
    assert result == [{"orderId": "123"}]
    assert session.calls == [{
        "url": f"{ctc.BASE_URL}/fills/order/123",
        "headers": {"Authorization": "Bearer tok", "Content-Type": "application/json", "Accept": "application/json"},
        "params": None,
    }]


def test_get_fills_for_order_raises_on_non_200(monkeypatch):
    response = _FakeResponse(status=500, text_body="server error")
    _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    with pytest.raises(RuntimeError, match="fills-by-order query failed"):
        asyncio.run(client.get_fills_for_order(123))


def test_get_fills_for_order_raises_on_reported_failure(monkeypatch):
    response = _FakeResponse(status=200, json_body={"success": False, "error": "bad request"})
    _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    with pytest.raises(RuntimeError, match="reported failure"):
        asyncio.run(client.get_fills_for_order(123))


def test_get_fills_for_order_returns_empty_list_when_data_missing(monkeypatch):
    response = _FakeResponse(status=200, json_body={"success": True})
    _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    assert asyncio.run(client.get_fills_for_order(123)) == []


# ------------------------------------------------------------------ get_fills_history

def test_get_fills_history_sends_from_to_account_environment_cursor(monkeypatch):
    response = _FakeResponse(status=200, json_body={"success": True, "data": [{"id": "f1"}]})
    session = _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    result = asyncio.run(client.get_fills_history("2026-11-05T10:00:00+00:00", "2026-11-05T10:05:00+00:00", is_demo=False))
    assert result == [{"id": "f1"}]
    assert session.calls == [{
        "url": f"{ctc.BASE_URL}/fills/history",
        "headers": {"Authorization": "Bearer tok", "Content-Type": "application/json", "Accept": "application/json"},
        "params": {
            "from": "2026-11-05T10:00:00+00:00", "to": "2026-11-05T10:05:00+00:00",
            "account": "acct1", "environment": "live", "cursor": "",
        },
    }]


def test_get_fills_history_uses_demo_environment_when_is_demo_true(monkeypatch):
    response = _FakeResponse(status=200, json_body={"success": True, "data": []})
    session = _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    asyncio.run(client.get_fills_history("f", "t", is_demo=True))
    assert session.calls[0]["params"]["environment"] == "demo"


def test_get_fills_history_raises_on_non_200(monkeypatch):
    response = _FakeResponse(status=500, text_body="boom")
    _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    with pytest.raises(RuntimeError, match="fills/history query failed"):
        asyncio.run(client.get_fills_history("f", "t"))


def test_get_fills_history_raises_on_reported_failure(monkeypatch):
    response = _FakeResponse(status=200, json_body={"success": False, "error": "bad window"})
    _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    with pytest.raises(RuntimeError, match="reported failure"):
        asyncio.run(client.get_fills_history("f", "t"))


# ------------------------------------------------------------------ sum_fill_fees() -- pure
# function, no network. Field names are a DEFENSIVE GUESS (see module
# header) -- these tests pin the current guessed parsing behavior, not a
# confirmed-correct schema.

def test_sum_fill_fees_sums_commission_and_fees_when_both_present():
    fills = [{"commission": "1.50", "fees": "0.25"}]
    assert ctc.sum_fill_fees(fills) == pytest.approx(1.75)


def test_sum_fill_fees_sums_across_multiple_fills():
    fills = [{"commission": "1.00", "fees": "0.10"}, {"commission": "2.00", "fees": "0.20"}]
    assert ctc.sum_fill_fees(fills) == pytest.approx(3.30)


def test_sum_fill_fees_uses_commission_alone_when_fees_absent():
    fills = [{"commission": "1.50"}]
    assert ctc.sum_fill_fees(fills) == pytest.approx(1.50)


def test_sum_fill_fees_falls_back_to_fee_when_neither_primary_name_present():
    fills = [{"fee": "0.75"}]
    assert ctc.sum_fill_fees(fills) == pytest.approx(0.75)


def test_sum_fill_fees_falls_back_to_commission_fee_when_neither_primary_name_present():
    fills = [{"commissionFee": "0.33"}]
    assert ctc.sum_fill_fees(fills) == pytest.approx(0.33)


def test_sum_fill_fees_does_not_double_count_fallback_when_primary_present():
    # commission is present (even if 0) -- fee/commissionFee fallbacks
    # must NOT also be added, or this would double-count one real fee
    # under two guessed names.
    fills = [{"commission": "1.00", "fee": "999.00"}]
    assert ctc.sum_fill_fees(fills) == pytest.approx(1.00)


def test_sum_fill_fees_empty_list():
    assert ctc.sum_fill_fees([]) == 0.0


def test_sum_fill_fees_tolerates_non_numeric_values():
    fills = [{"commission": "not-a-number"}]
    assert ctc.sum_fill_fees(fills) == 0.0


# ------------------------------------------------------------------ get_market_bars /
# _parse_market_bars -- Session 11 zero-lag CME market-data path. The
# `data` shape is NOT live-verified (CrossTrade specs it as a bare object),
# so these pin the DEFENSIVE parser's two handled shapes and the 408 gap
# contract, not a confirmed-correct schema.


def test_get_market_bars_posts_to_market_bars_with_dated_instrument(monkeypatch):
    response = _FakeResponse(status=200, json_body={"success": True, "data": {"bars": []}})
    session = _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    result = asyncio.run(client.get_market_bars("MNQ 12-26", period_type="Minute", period=5, days_back=1, limit=100))
    assert result == []
    assert session.calls == [{
        "url": f"{ctc._MARKET_BASE}/market/bars",
        "headers": {"Authorization": "Bearer tok", "Content-Type": "application/json", "Accept": "application/json"},
        "params": None,
        "method": "POST",
        "json": {"instrument": "MNQ 12-26", "periodType": "Minute", "period": 5, "daysBack": 1, "limit": 100},
    }]


def test_get_market_bars_parses_the_documented_bars_wrapper_shape(monkeypatch):
    bars = [
        {"time": "2026-11-09T22:35:00Z", "timestamp": 1794285300,
         "open": 21050.25, "high": 21055.00, "low": 21048.50, "close": 21052.75, "volume": 3200},
    ]
    response = _FakeResponse(status=200, json_body={"success": True, "data": {"bars": bars}})
    _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    result = asyncio.run(client.get_market_bars("MNQ 12-26"))
    assert len(result) == 1
    b = result[0]
    assert b["open"] == 21050.25
    assert b["close"] == 21052.75
    assert b["volume"] == 3200
    assert b["timestamp"] == 1794285300
    assert b["time"] == datetime.datetime(2026, 11, 9, 22, 35, 0, tzinfo=datetime.timezone.utc)


def test_get_market_bars_parses_a_flat_array_fallback_shape(monkeypatch):
    bars = [{"time": "2026-11-09T22:35:00Z", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}]
    response = _FakeResponse(status=200, json_body={"success": True, "data": bars})
    _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    result = asyncio.run(client.get_market_bars("MNQ 12-26"))
    assert len(result) == 1
    assert result[0]["open"] == 1.0
    assert result[0]["close"] == 1.5


def test_get_market_bars_coerces_epoch_time_when_no_iso_string(monkeypatch):
    # Correct epoch for 2026-11-09T22:35:00Z (the MD's own example paired an
    # ISO string with an epoch that decoded to the NEXT day -- an internal
    # inconsistency we are NOT reproducing; this test uses the true epoch).
    bars = [{"timestamp": 1794263700, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}]
    response = _FakeResponse(status=200, json_body={"success": True, "data": {"bars": bars}})
    _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    result = asyncio.run(client.get_market_bars("MNQ 12-26"))
    assert result[0]["time"] == datetime.datetime(2026, 11, 9, 22, 35, 0, tzinfo=datetime.timezone.utc)


def test_get_market_bars_returns_empty_list_on_408_not_raises(monkeypatch):
    # 408 = NT8 add-on disconnected -- a quiet gap, NOT an error. The caller
    # surfaces VPS_NT8_DISCONNECTED.
    response = _FakeResponse(status=408, text_body='{"error": "add-on not connected"}')
    _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    assert asyncio.run(client.get_market_bars("MNQ 12-26")) == []


def test_get_market_bars_raises_on_other_non_200(monkeypatch):
    response = _FakeResponse(status=429, text_body='{"error": "rate limit"}')
    _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    with pytest.raises(RuntimeError, match="market bars query failed"):
        asyncio.run(client.get_market_bars("MNQ 12-26"))


def test_get_market_bars_raises_on_non_json_200(monkeypatch):
    response = _FakeResponse(status=200, text_body="not json at all", json_raises=True)
    _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    with pytest.raises(RuntimeError, match="non-JSON"):
        asyncio.run(client.get_market_bars("MNQ 12-26"))


def test_parse_market_bars_returns_empty_on_unknown_data_shape():
    assert ctc._parse_market_bars({"success": True, "data": {"unexpected": True}}) == []
    assert ctc._parse_market_bars({"success": True}) == []
    assert ctc._parse_market_bars({"success": True, "data": "not-a-list"}) == []


def test_get_market_bars_parses_live_verified_st_louis_vps_payload(monkeypatch):
    # Verified live against St. Louis VPS NinjaTrader 8 response (2026-11-09)
    payload = {
        "instrument": "MNQ 12-26",
        "periodType": "minute",
        "period": 5,
        "count": 1,
        "bars": [
            {
                "time": "2026-11-10T05:15:00.0000000Z",
                "epoch": 1794287700000,
                "open": 27968.75,
                "high": 27995.5,
                "low": 27968.75,
                "close": 27988,
                "volume": 4809
            }
        ],
        "success": True
    }
    response = _FakeResponse(status=200, json_body=payload)
    _install(monkeypatch, response)
    client = ctc.CrossTradeClient(bearer_token="tok", crosstrade_account_id="acct1")
    result = asyncio.run(client.get_market_bars("MNQ 12-26", period_type="minute", period=5))
    assert len(result) == 1
    b = result[0]
    assert b["open"] == 27968.75
    assert b["high"] == 27995.5
    assert b["low"] == 27968.75
    assert b["close"] == 27988
    assert b["volume"] == 4809
    assert b["timestamp"] == 1794287700
    assert b["time"] == datetime.datetime(2026, 11, 10, 5, 15, 0, tzinfo=datetime.timezone.utc)
