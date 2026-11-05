"""Coverage for account_balance_snapshot.py -- the desk-page build's new
background loop that periodically writes a real balance/equity reading for
every active ExecutorAccount (Bitunix and Tradovate/CrossTrade alike).
Same fake-client monkeypatch style as tests/test_executor_live_e1_engine.py/
test_cme_executor.py: no real network calls."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./kabroda_test_account_balance_snapshot.db")

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount, ExecutorAccountBalanceSnapshot
import executor_accounts as ea
import executor_bitunix_client as ebc
import cme_tradovate_client as tvc
import account_balance_snapshot as abs_mod


def _clean_db_files():
    for path in ["kabroda_test_account_balance_snapshot.db", "kabroda_test_account_balance_snapshot.db-journal",
                 "kabroda_test_account_balance_snapshot.db-shm", "kabroda_test_account_balance_snapshot.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    for model in (ExecutorAccountBalanceSnapshot, ExecutorAccount):
        session.query(model).delete()
    session.commit()


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", Fernet.generate_key().decode("utf-8"))
    _clean_db_files()
    database.init_db()
    session = SessionLocal()
    _clean_rows(session)
    yield session
    _clean_rows(session)
    session.close()
    database.engine.dispose()
    _clean_db_files()


def _run(coro):
    return asyncio.run(coro)


def _async(value=None, exc=None):
    async def _fake(self, *a, **kw):
        if exc is not None:
            raise exc
        return value
    return _fake


def _bitunix_account(db, label="bitunix_acct", **overrides):
    account = ea.create_account(db, user_id=1, label=label, exchange="bitunix")
    db.flush()
    ea.set_credentials(db, account, api_key="fake-key", api_secret="fake-secret", set_by="test@kabroda.com")
    for k, v in overrides.items():
        setattr(account, k, v)
    db.commit()
    return account


def _tradovate_account(db, label="tradovate_acct", **overrides):
    account = ea.create_account(db, user_id=1, label=label, exchange="tradovate")
    db.flush()
    ea.set_credentials(db, account, api_key="fake-user", api_secret="fake-pass", set_by="test@kabroda.com")
    account.tradovate_account_id = 12345
    for k, v in overrides.items():
        setattr(account, k, v)
    db.commit()
    return account


def test_snapshot_one_bitunix_account_writes_a_row(db, monkeypatch):
    account = _bitunix_account(db)
    monkeypatch.setattr(ebc.BitunixClient, "get_balance",
                         _async({"code": 0, "data": {"available": "100.0", "margin": "0", "isolationUnrealizedPNL": "0"}}))

    _run(abs_mod.run_one_snapshot_tick(db))

    rows = db.query(ExecutorAccountBalanceSnapshot).filter_by(account_id=account.id).all()
    assert len(rows) == 1
    assert rows[0].balance_usd == pytest.approx(100.0)
    assert rows[0].equity_usd == pytest.approx(100.0)
    assert "verified against the real exchange" in rows[0].source


def test_snapshot_one_tradovate_account_writes_a_row(db, monkeypatch):
    # get_balance() itself does the HTTP call AND the field-name parsing
    # (cashBalance -> cash_balance etc, cme_tradovate_client.py) -- the
    # monkeypatch must return that ALREADY-PARSED shape, not the raw
    # Tradovate JSON.
    account = _tradovate_account(db)
    monkeypatch.setattr(tvc.TradovateClient, "get_balance",
                         _async({"cash_balance": 5000.0, "total_equity": 5050.0, "realized_pnl": 0.0, "open_pnl": 50.0}))

    _run(abs_mod.run_one_snapshot_tick(db))

    rows = db.query(ExecutorAccountBalanceSnapshot).filter_by(account_id=account.id).all()
    assert len(rows) == 1
    assert rows[0].balance_usd == pytest.approx(5000.0)
    assert rows[0].equity_usd == pytest.approx(5050.0)
    assert rows[0].source == "SNAPSHOT_LOOP"


def test_one_accounts_failure_does_not_block_the_others(db, monkeypatch):
    """The real reliability requirement: a down exchange or bad credential
    on one account must never stop the rest of the tick's writes."""
    broken = _tradovate_account(db, label="broken_acct")
    monkeypatch.setattr(tvc.TradovateClient, "get_balance", _async(exc=RuntimeError("exchange down")))
    healthy = _bitunix_account(db, label="healthy_acct")
    monkeypatch.setattr(ebc.BitunixClient, "get_balance",
                         _async({"code": 0, "data": {"available": "200.0", "margin": "0", "isolationUnrealizedPNL": "0"}}))

    _run(abs_mod.run_one_snapshot_tick(db))

    assert db.query(ExecutorAccountBalanceSnapshot).filter_by(account_id=broken.id).count() == 0
    healthy_rows = db.query(ExecutorAccountBalanceSnapshot).filter_by(account_id=healthy.id).all()
    assert len(healthy_rows) == 1
    assert healthy_rows[0].balance_usd == pytest.approx(200.0)


def test_inactive_account_is_skipped(db, monkeypatch):
    account = _bitunix_account(db, is_active=False)
    monkeypatch.setattr(ebc.BitunixClient, "get_balance",
                         _async({"code": 0, "data": {"available": "100.0", "margin": "0", "isolationUnrealizedPNL": "0"}}))

    _run(abs_mod.run_one_snapshot_tick(db))

    assert db.query(ExecutorAccountBalanceSnapshot).filter_by(account_id=account.id).count() == 0
