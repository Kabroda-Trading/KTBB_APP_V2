"""Coverage for alt_matrix_market.py::fetch_funding_rate() -- every
failure path must return None (fail-closed), never raise, never guess a
value. No real network calls (executor_bitunix_client.
fetch_public_funding_rate() monkeypatched directly -- this endpoint is
genuinely public/unauthenticated, confirmed by DeepSeek's live smoke
test 2026-10-10, so no DB/account fixture is needed at all anymore)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import executor_bitunix_client as ebc
import alt_matrix_market as amm


def _run(coro):
    return asyncio.run(coro)


def _async(value=None, exc=None):
    async def _fake(*a, **kw):
        if exc is not None:
            raise exc
        return value
    return _fake


def test_fetch_funding_rate_exception_returns_none(monkeypatch):
    monkeypatch.setattr(ebc, "fetch_public_funding_rate", _async(exc=ConnectionError("blip")))
    assert _run(amm.fetch_funding_rate("SOL/USDT")) is None


def test_fetch_funding_rate_error_code_returns_none(monkeypatch):
    monkeypatch.setattr(ebc, "fetch_public_funding_rate", _async({"code": 10001, "msg": "fail", "data": None}))
    assert _run(amm.fetch_funding_rate("SOL/USDT")) is None


def test_fetch_funding_rate_real_verified_response_shape_parses(monkeypatch):
    # The ACTUAL live response DeepSeek captured 2026-10-10 10:28 CT
    # (CC_INTERFACE.md section 6) -- data is a single object, resolving
    # the docs' own object-vs-array inconsistency for real.
    real_response = {
        "code": 0, "msg": "Success",
        "data": {
            "symbol": "SOLUSDT", "markPrice": "110.43", "lastPrice": "110.43",
            "indexPrice": "110.48", "fundingRate": "0.003502", "fundingInterval": 8,
            "nextFundingTime": "1791648000000", "maxFundingRate": "0.375", "minFundingRate": "-0.375",
        },
    }
    monkeypatch.setattr(ebc, "fetch_public_funding_rate", _async(real_response))
    assert _run(amm.fetch_funding_rate("SOL/USDT")) == pytest.approx(0.003502)


def test_fetch_funding_rate_array_shape_still_tolerated_defensively(monkeypatch):
    # No longer the confirmed live shape, but costs nothing to keep
    # tolerating in case of a future API change.
    monkeypatch.setattr(ebc, "fetch_public_funding_rate", _async({"code": 0, "data": [{"fundingRate": "0.0007"}]}))
    assert _run(amm.fetch_funding_rate("SOL/USDT")) == pytest.approx(0.0007)


def test_fetch_funding_rate_empty_array_returns_none(monkeypatch):
    monkeypatch.setattr(ebc, "fetch_public_funding_rate", _async({"code": 0, "data": []}))
    assert _run(amm.fetch_funding_rate("SOL/USDT")) is None


def test_fetch_funding_rate_missing_field_returns_none(monkeypatch):
    monkeypatch.setattr(ebc, "fetch_public_funding_rate", _async({"code": 0, "data": {"symbol": "SOLUSDT"}}))
    assert _run(amm.fetch_funding_rate("SOL/USDT")) is None


def test_fetch_funding_rate_unparseable_field_returns_none(monkeypatch):
    monkeypatch.setattr(ebc, "fetch_public_funding_rate", _async({"code": 0, "data": {"fundingRate": "not-a-number"}}))
    assert _run(amm.fetch_funding_rate("SOL/USDT")) is None


def test_fetch_funding_rate_strips_slash_before_calling_the_endpoint(monkeypatch):
    seen = {}
    async def _fake(symbol):
        seen["symbol"] = symbol
        return {"code": 0, "data": {"fundingRate": "0.0001"}}
    monkeypatch.setattr(ebc, "fetch_public_funding_rate", _fake)
    _run(amm.fetch_funding_rate("SOL/USDT"))
    assert seen["symbol"] == "SOLUSDT"


def test_fetch_funding_rate_requires_no_account_or_credentials_at_all(monkeypatch):
    # The real point of this whole fix: no ExecutorAccount, no DB, no
    # credentials anywhere in this test -- the call still works, because
    # the endpoint is genuinely public.
    monkeypatch.setattr(ebc, "fetch_public_funding_rate", _async({"code": 0, "data": {"fundingRate": "0.0002"}}))
    assert _run(amm.fetch_funding_rate("ETH/USDT")) == pytest.approx(0.0002)
