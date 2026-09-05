"""
Unit coverage for executor_accounts.py + executor_control.py -- DB-backed
(throwaway sqlite file), same fixture style as tests/test_trade_plan_engine.py.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_executor_accounts.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorGlobalConfig
import executor_accounts as ea
import executor_control as ec


def _clean_db_files():
    for path in ["kabroda_test_executor_accounts.db", "kabroda_test_executor_accounts.db-journal",
                 "kabroda_test_executor_accounts.db-shm", "kabroda_test_executor_accounts.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    # Row-level cleanup, not just _clean_db_files() -- database.engine is
    # cached module-globally at first import across the WHOLE pytest
    # session (documented in test_trade_plan_engine.py's own fixture),
    # so whichever physical db file "wins" gets shared across every test
    # file. ExecutorOrder/ExecutorAuditLog specifically must be cleaned
    # here too -- a leftover audit row from another file with the same
    # account_id (autoincrement restarts at 1 per fresh table) breaks
    # exact-count assertions like len(rows) == 1.
    for model in (ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorAccount, ExecutorGlobalConfig):
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


# ------------------------------------------------------------------ set_credentials / get_decrypted_credentials

def test_set_credentials_round_trip_and_not_stored_as_plaintext(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()

    ea.set_credentials(db, account, api_key="real-key-abc", api_secret="real-secret-xyz", set_by="andy@kabroda.com")
    db.commit()

    assert account.api_key_encrypted != "real-key-abc"
    assert account.api_secret_encrypted != "real-secret-xyz"
    assert "real-key-abc" not in (account.api_key_encrypted or "")

    key, secret = ea.get_decrypted_credentials(account)
    assert key == "real-key-abc"
    assert secret == "real-secret-xyz"


def test_set_credentials_writes_audit_row_without_the_secret(db):
    from database import ExecutorAuditLog
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ea.set_credentials(db, account, "key123", "secret456", set_by="andy@kabroda.com")
    db.commit()

    rows = db.query(ExecutorAuditLog).filter_by(account_id=account.id, event_type="CREDENTIAL_SET").all()
    assert len(rows) == 1
    assert "key123" not in (rows[0].message or "") + (rows[0].detail_json or "")
    assert "secret456" not in (rows[0].message or "") + (rows[0].detail_json or "")


def test_credential_rotation_logs_rotated_not_set(db):
    from database import ExecutorAuditLog
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ea.set_credentials(db, account, "key1", "secret1", set_by="andy@kabroda.com")
    db.commit()
    ea.set_credentials(db, account, "key2", "secret2", set_by="andy@kabroda.com")
    db.commit()

    events = [r.event_type for r in db.query(ExecutorAuditLog).filter_by(account_id=account.id).order_by(ExecutorAuditLog.id).all()]
    assert events == ["ACCOUNT_CREATED", "CREDENTIAL_SET", "CREDENTIAL_ROTATED"]
    key, secret = ea.get_decrypted_credentials(account)
    assert (key, secret) == ("key2", "secret2")


def test_get_decrypted_credentials_none_when_unset(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    assert ea.get_decrypted_credentials(account) == (None, None)


# ------------------------------------------------------------------ get_or_init_risk_state

def test_get_or_init_risk_state_defaults(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    state = ea.get_or_init_risk_state(db, account)
    db.commit()
    assert state.risk_last_usd == 100.0
    assert state.risk_floor_usd == 100.0
    assert state.risk_cap_usd == 1000.0
    assert state.compounding_factor == 0.10

    # idempotent -- a second call returns the SAME row, doesn't create a duplicate
    state2 = ea.get_or_init_risk_state(db, account)
    assert state2.id == state.id


# ------------------------------------------------------------------ is_account_tradeable (full matrix)

def test_is_account_tradeable_all_clear(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ok, reason = ea.is_account_tradeable(db, account)
    assert ok is True


def test_is_account_tradeable_false_when_inactive(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    account.is_active = False
    db.commit()
    ok, reason = ea.is_account_tradeable(db, account)
    assert ok is False
    assert "inactive" in reason


def test_is_account_tradeable_false_when_account_kill_switch_engaged(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ea.engage_kill_switch(db, account, reason="testing", by="andy@kabroda.com")
    db.commit()
    ok, reason = ea.is_account_tradeable(db, account)
    assert ok is False
    assert "kill switch" in reason


def test_is_account_tradeable_false_when_global_kill_switch_engaged(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ec.engage_global_kill_switch(db, reason="emergency stop", by="andy@kabroda.com")
    db.commit()
    ok, reason = ea.is_account_tradeable(db, account)
    assert ok is False
    assert "global" in reason


def test_release_kill_switch_restores_tradeability(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ea.engage_kill_switch(db, account, reason="testing", by="andy@kabroda.com")
    db.commit()
    assert ea.is_account_tradeable(db, account)[0] is False

    ea.release_kill_switch(db, account, by="andy@kabroda.com")
    db.commit()
    assert ea.is_account_tradeable(db, account)[0] is True


def test_release_global_kill_switch_restores_tradeability(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ec.engage_global_kill_switch(db, reason="emergency stop", by="andy@kabroda.com")
    db.commit()
    assert ea.is_account_tradeable(db, account)[0] is False

    ec.release_global_kill_switch(db, by="andy@kabroda.com")
    db.commit()
    assert ea.is_account_tradeable(db, account)[0] is True


def test_global_kill_switch_defaults_to_not_engaged_with_no_config_row(db):
    # No ExecutorGlobalConfig row exists yet (fresh DB) -- must default to
    # NOT engaged, not fail closed here (Stage 1 is already safe by
    # construction -- no exchange calls regardless of this flag).
    assert ec.is_global_kill_switch_engaged(db) is False


# ------------------------------------------------------------------ live orders gate (Stage 2, 2026-09-05)
# Opposite polarity from the kill switch above: this flag PERMITS
# real-money order placement only when True, default False.

def test_live_orders_defaults_to_disabled_with_no_config_row(db):
    assert ec.is_live_orders_enabled(db) is False


def test_enable_live_orders_sets_flag_and_metadata(db):
    ec.enable_live_orders(db, reason="tiny order mechanism test", by="andy@kabroda.com")
    db.commit()
    assert ec.is_live_orders_enabled(db) is True


def test_disable_live_orders_clears_flag_and_metadata(db):
    ec.enable_live_orders(db, reason="tiny order mechanism test", by="andy@kabroda.com")
    db.commit()
    assert ec.is_live_orders_enabled(db) is True

    ec.disable_live_orders(db, by="andy@kabroda.com")
    db.commit()
    assert ec.is_live_orders_enabled(db) is False


def test_live_orders_and_kill_switch_are_independent_flags(db):
    # Enabling live orders does not clear an engaged global kill switch,
    # and vice versa -- these are two independent gates, both must be
    # satisfied for a real-money action (kill switch clear AND live
    # orders enabled), neither implies the other.
    ec.engage_global_kill_switch(db, reason="emergency stop", by="andy@kabroda.com")
    ec.enable_live_orders(db, reason="testing", by="andy@kabroda.com")
    db.commit()
    assert ec.is_global_kill_switch_engaged(db) is True
    assert ec.is_live_orders_enabled(db) is True
