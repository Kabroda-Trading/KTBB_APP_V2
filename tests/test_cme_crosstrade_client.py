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
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import cme_crosstrade_client as ctc


class _FakeResponse:
    def __init__(self, status=200, json_body=None, text_body=""):
        self.status = status
        self._json_body = json_body if json_body is not None else {}
        self._text_body = text_body

    async def json(self):
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
