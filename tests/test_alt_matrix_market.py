"""Coverage for alt_matrix_market.py::fetch_funding_rate() -- every
failure path must return None (fail-closed), never raise, never guess a
value. No real network calls (Bitunix client monkeypatched)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./kabroda_test_alt_matrix_market.db")

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount
import executor_accounts as ea
import executor_bitunix_client as ebc
import alt_matrix_market as amm


def _clean_db_files():
    for path in ["kabroda_test_alt_matrix_market.db", "kabroda_test_alt_matrix_market.db-journal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", Fernet.generate_key().decode("utf-8"))
    _clean_db_files()
    database.init_db()
    session = SessionLocal()
    for model in (ExecutorAccount,):
        session.query(model).delete()
    session.commit()
    yield session
    session.close()
    database.engine.dispose()
    _clean_db_files()


def _account_with_creds(db):
    account = ea.create_account(db, user_id=1, label="funding_test")
    db.flush()
    ea.set_credentials(db, account, api_key="k", api_secret="s", set_by="test@kabroda.com")
    db.commit()
    return account


def _account_without_creds(db):
    account = ea.create_account(db, user_id=1, label="funding_test_nocreds")
    db.commit()
    return account


def _run(coro):
    return asyncio.run(coro)


def _async(value=None, exc=None):
    async def _fake(self, *a, **kw):
        if exc is not None:
            raise exc
        return value
    return _fake


def test_fetch_funding_rate_none_account_returns_none():
    assert _run(amm.fetch_funding_rate("SOL/USDT", None)) is None


def test_fetch_funding_rate_no_credentials_returns_none(db):
    account = _account_without_creds(db)
    assert _run(amm.fetch_funding_rate("SOL/USDT", account)) is None


def test_fetch_funding_rate_exception_returns_none(db, monkeypatch):
    account = _account_with_creds(db)
    monkeypatch.setattr(ebc.BitunixClient, "get_funding_rate", _async(exc=ConnectionError("blip")))
    assert _run(amm.fetch_funding_rate("SOL/USDT", account)) is None


def test_fetch_funding_rate_error_code_returns_none(db, monkeypatch):
    account = _account_with_creds(db)
    monkeypatch.setattr(ebc.BitunixClient, "get_funding_rate", _async({"code": 10001, "msg": "fail", "data": None}))
    assert _run(amm.fetch_funding_rate("SOL/USDT", account)) is None


def test_fetch_funding_rate_object_shape_parses(db, monkeypatch):
    account = _account_with_creds(db)
    monkeypatch.setattr(ebc.BitunixClient, "get_funding_rate", _async({"code": 0, "data": {"fundingRate": "0.0003"}}))
    assert _run(amm.fetch_funding_rate("SOL/USDT", account)) == pytest.approx(0.0003)


def test_fetch_funding_rate_array_shape_parses(db, monkeypatch):
    # The doc's own worked example shows `data` as a single-element array
    # even though the field table describes one object -- must handle both.
    account = _account_with_creds(db)
    monkeypatch.setattr(ebc.BitunixClient, "get_funding_rate", _async({"code": 0, "data": [{"fundingRate": "0.0007"}]}))
    assert _run(amm.fetch_funding_rate("SOL/USDT", account)) == pytest.approx(0.0007)


def test_fetch_funding_rate_empty_array_returns_none(db, monkeypatch):
    account = _account_with_creds(db)
    monkeypatch.setattr(ebc.BitunixClient, "get_funding_rate", _async({"code": 0, "data": []}))
    assert _run(amm.fetch_funding_rate("SOL/USDT", account)) is None


def test_fetch_funding_rate_missing_field_returns_none(db, monkeypatch):
    account = _account_with_creds(db)
    monkeypatch.setattr(ebc.BitunixClient, "get_funding_rate", _async({"code": 0, "data": {"symbol": "SOLUSDT"}}))
    assert _run(amm.fetch_funding_rate("SOL/USDT", account)) is None


def test_fetch_funding_rate_unparseable_field_returns_none(db, monkeypatch):
    account = _account_with_creds(db)
    monkeypatch.setattr(ebc.BitunixClient, "get_funding_rate", _async({"code": 0, "data": {"fundingRate": "not-a-number"}}))
    assert _run(amm.fetch_funding_rate("SOL/USDT", account)) is None


def test_fetch_funding_rate_strips_slash_before_calling_client(db, monkeypatch):
    account = _account_with_creds(db)
    seen = {}
    async def _fake(self, symbol):
        seen["symbol"] = symbol
        return {"code": 0, "data": {"fundingRate": "0.0001"}}
    monkeypatch.setattr(ebc.BitunixClient, "get_funding_rate", _fake)
    _run(amm.fetch_funding_rate("SOL/USDT", account))
    assert seen["symbol"] == "SOLUSDT"
