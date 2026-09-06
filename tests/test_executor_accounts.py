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
from database import SessionLocal, ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorGlobalConfig, ExecutorSizingPolicy
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
    for model in (ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorSizingPolicy, ExecutorAccount, ExecutorGlobalConfig):
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


# ------------------------------------------------------------------ get_or_init_sizing_policy / update_sizing_policy / record_trade_result (Sizing Policy Wizard, 2026-09-05)

def test_get_or_init_sizing_policy_seeds_from_existing_risk_state(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    # Simulate an account already running the old fixed/rolling risk
    # state before the wizard existed -- non-default values, so a lazy
    # copy-vs-default bug would be caught.
    state = ea.get_or_init_risk_state(db, account)
    state.risk_last_usd = 150.0
    state.risk_cap_usd = 2000.0
    state.compounding_factor = 0.15
    db.commit()

    policy = ea.get_or_init_sizing_policy(db, account)
    db.commit()
    assert policy.preset_name == "steady_grow"  # compounding_factor > 0
    assert policy.base_risk_usd == 150.0
    assert policy.roll_in_pct == 0.15
    assert policy.cap_abs_usd == 2000.0
    assert policy.base_risk_pct is None

    # idempotent -- a second call returns the SAME row, doesn't create a duplicate
    policy2 = ea.get_or_init_sizing_policy(db, account)
    assert policy2.id == policy.id


def test_get_or_init_sizing_policy_labels_conservative_when_no_roll_in(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    state = ea.get_or_init_risk_state(db, account)
    state.compounding_factor = 0.0
    db.commit()

    policy = ea.get_or_init_sizing_policy(db, account)
    assert policy.preset_name == "conservative"


def test_update_sizing_policy_rejects_both_base_fields_set(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    with pytest.raises(ValueError, match="exactly one"):
        ea.update_sizing_policy(db, account, {"base_risk_usd": 100.0, "base_risk_pct": 0.10}, updated_by="andy@kabroda.com")


def test_update_sizing_policy_rejects_neither_base_field_set(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    # Force the seeded lazy-init base_risk_usd off, then try to clear it
    # with no replacement -- the merged view has neither set.
    with pytest.raises(ValueError, match="exactly one"):
        ea.update_sizing_policy(db, account, {"base_risk_usd": None}, updated_by="andy@kabroda.com")


def test_update_sizing_policy_rejects_tier_fields_set_alone(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    with pytest.raises(ValueError, match="tier_threshold_usd and tier_flat_usd"):
        ea.update_sizing_policy(db, account, {"tier_threshold_usd": 10000.0}, updated_by="andy@kabroda.com")


def test_update_sizing_policy_rejects_derisk_fields_set_alone(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    with pytest.raises(ValueError, match="derisk_n and derisk_factor"):
        ea.update_sizing_policy(db, account, {"derisk_n": 3}, updated_by="andy@kabroda.com")


def test_update_sizing_policy_switching_to_percent_mode_clears_fixed_field(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ea.get_or_init_sizing_policy(db, account)  # seeds base_risk_usd
    db.commit()

    policy = ea.update_sizing_policy(db, account, {"base_risk_pct": 0.10}, updated_by="andy@kabroda.com")
    db.commit()
    assert policy.base_risk_pct == 0.10
    assert policy.base_risk_usd is None


def test_update_sizing_policy_persists_and_writes_audit(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ea.update_sizing_policy(
        db, account,
        {"preset_name": "scale_with_account", "base_risk_pct": 0.10, "base_risk_usd": None,
         "tier_threshold_usd": 10000.0, "tier_flat_usd": 1000.0},
        updated_by="andy@kabroda.com",
    )
    db.commit()

    policy = db.query(ExecutorSizingPolicy).filter_by(account_id=account.id).one()
    assert policy.base_risk_pct == 0.10
    assert policy.tier_threshold_usd == 10000.0
    assert policy.tier_flat_usd == 1000.0

    rows = db.query(ExecutorAuditLog).filter_by(account_id=account.id, event_type="SIZING_POLICY_UPDATED").all()
    assert len(rows) == 1


def test_record_trade_result_rolls_risk_last_usd_when_roll_in_pct_set(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ea.update_sizing_policy(db, account, {"base_risk_usd": 100.0, "roll_in_pct": 0.10}, updated_by="andy@kabroda.com")
    db.commit()

    state = ea.record_trade_result(db, account, pnl_usd=50.0, trade_plan_id=42, recorded_by="andy@kabroda.com")
    db.commit()
    # risk_last (100) + factor(0.10)*pnl(50) = 105, within [floor,cap]
    assert state.risk_last_usd == pytest.approx(105.0)
    assert state.last_trade_pnl_usd == 50.0
    assert state.last_updated_from_trade_plan_id == 42


def test_record_trade_result_does_not_roll_when_roll_in_pct_unset(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    # Explicit fixed-mode policy: no roll-in, unlike the lazy-init default
    # (which seeds roll_in_pct from ExecutorRiskState.compounding_factor).
    ea.update_sizing_policy(db, account, {"base_risk_usd": 100.0, "roll_in_pct": None}, updated_by="andy@kabroda.com")
    db.commit()
    original = ea.get_or_init_risk_state(db, account).risk_last_usd

    state = ea.record_trade_result(db, account, pnl_usd=50.0, recorded_by="andy@kabroda.com")
    db.commit()
    assert state.risk_last_usd == original


def test_record_trade_result_resets_consecutive_losses_on_win(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    state = ea.get_or_init_risk_state(db, account)
    state.consecutive_losses = 2
    db.commit()

    state = ea.record_trade_result(db, account, pnl_usd=25.0, recorded_by="andy@kabroda.com")
    db.commit()
    assert state.consecutive_losses == 0


def test_record_trade_result_increments_consecutive_losses_on_loss(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()

    state = ea.record_trade_result(db, account, pnl_usd=-30.0, recorded_by="andy@kabroda.com")
    db.commit()
    assert state.consecutive_losses == 1

    state = ea.record_trade_result(db, account, pnl_usd=-15.0, recorded_by="andy@kabroda.com")
    db.commit()
    assert state.consecutive_losses == 2


def test_record_trade_result_zero_pnl_counts_as_a_loss_not_a_win(db):
    # pnl_usd > 0 is the ONLY win condition -- a scratch/breakeven trade
    # (0.0) must not reset the streak, matching compute_stake()'s own
    # >= derisk_n threshold semantics (a streak of exact scratches should
    # still be able to trip the derisk modifier).
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    state = ea.record_trade_result(db, account, pnl_usd=0.0, recorded_by="andy@kabroda.com")
    db.commit()
    assert state.consecutive_losses == 1


def test_record_trade_result_writes_audit(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ea.record_trade_result(db, account, pnl_usd=10.0, trade_plan_id=7, recorded_by="andy@kabroda.com")
    db.commit()
    rows = db.query(ExecutorAuditLog).filter_by(account_id=account.id, event_type="TRADE_RESULT_RECORDED").all()
    assert len(rows) == 1
    assert rows[0].trade_plan_id == 7
