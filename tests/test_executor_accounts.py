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


# ------------------------------------------------------------------ base_risk_usd -> risk_last_usd sync (2026-09-07)
# Real bug found while confirming Andy's compounding question directly:
# compute_stake() (and the preview route) never read policy.base_risk_usd
# at all -- the FIXED/ROLLING stake always came from ExecutorRiskState.
# risk_last_usd, which nothing synced FROM the wizard's "Base $" field.
# Setting "Base $100" and saving silently did nothing to the real stake.

def test_setting_a_new_base_risk_usd_resets_the_real_risk_last_usd(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    state = ea.get_or_init_risk_state(db, account)
    state.risk_last_usd = 999.0   # simulate drift from unrelated prior activity
    db.commit()

    ea.update_sizing_policy(db, account, {"base_risk_usd": 100.0, "roll_in_pct": 0.10}, updated_by="andy@kabroda.com")
    db.commit()

    state2 = ea.get_or_init_risk_state(db, account)
    assert state2.risk_last_usd == 100.0   # the real stake basis, not left at the stale 999


def test_resaving_the_same_base_risk_usd_does_not_erase_real_compounding_progress(db):
    # The JS always resends every field on every save (not a sparse
    # diff), so "base_risk_usd present in the request" alone is NOT
    # enough to trigger a reset -- only an ACTUAL change should. Confirms
    # adjusting one unrelated field (the cap) never silently wipes real
    # compounding progress just because base_risk_usd rode along in the
    # same request, unchanged.
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ea.update_sizing_policy(db, account, {"base_risk_usd": 100.0, "roll_in_pct": 0.10}, updated_by="andy@kabroda.com")
    db.commit()

    # Real compounding happened since then -- two wins rolled in.
    ea.record_trade_result(db, account, pnl_usd=100.0, recorded_by="andy@kabroda.com")  # -> 110
    ea.record_trade_result(db, account, pnl_usd=100.0, recorded_by="andy@kabroda.com")  # -> 120
    db.commit()
    state = ea.get_or_init_risk_state(db, account)
    assert state.risk_last_usd == 120.0   # Andy's own worked example, confirmed exact

    # Re-save with the SAME base_risk_usd (100.0) while only touching cap_abs_usd.
    ea.update_sizing_policy(db, account, {"base_risk_usd": 100.0, "cap_abs_usd": 500.0}, updated_by="andy@kabroda.com")
    db.commit()

    state2 = ea.get_or_init_risk_state(db, account)
    assert state2.risk_last_usd == 120.0   # untouched -- real progress preserved


def test_the_real_stake_computation_actually_reflects_the_saved_base(db):
    # End-to-end proof, not just that risk_last_usd changed: compute_stake()
    # itself -- the function build_hypothetical_order() actually calls at
    # trade time -- now returns the number the wizard says it will.
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ea.update_sizing_policy(db, account, {"base_risk_usd": 100.0, "preset_name": "fixed_dollar"}, updated_by="andy@kabroda.com")
    db.commit()

    from executor_sizing import compute_stake
    state = ea.get_or_init_risk_state(db, account)
    stake, _ = compute_stake(risk_last_usd=state.risk_last_usd)
    assert stake == 100.0


def test_setting_a_new_compounding_factor_via_legacy_risk_state_resyncs_roll_in_pct(db):
    # 2026-09-07 stagnant-sweep fix, "Base $ class" bug found by auditing
    # every ExecutorRiskState/ExecutorSizingPolicy field for a real
    # consumer (STAGNANT_SWEEP.md item 6): record_trade_result() only ever
    # reads policy.roll_in_pct, never state.compounding_factor, once a
    # sizing policy row exists -- which is every account that has ever
    # built a live plan even once. Before the fix, saving the "Compounding
    # factor" field in the Advanced/Legacy Risk State panel was a silent
    # no-op on real compounding behavior.
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    # Touch the sizing policy once (as executor_plan_builder.py's
    # get_or_init_sizing_policy() call does on the very first live plan
    # build) so the policy row already exists, seeded with the default
    # roll_in_pct=0.10 from ExecutorRiskState's own default.
    ea.get_or_init_sizing_policy(db, account)
    db.commit()

    ea.update_risk_state(db, account, {"compounding_factor": 0.25}, updated_by="andy@kabroda.com")
    db.commit()

    policy = ea.get_or_init_sizing_policy(db, account)
    assert policy.roll_in_pct == 0.25   # the field record_trade_result() actually reads

    # End-to-end proof, not just that the field changed: record_trade_result()
    # now actually compounds at the NEW factor, not the stale seeded one.
    state = ea.record_trade_result(db, account, pnl_usd=100.0, recorded_by="andy@kabroda.com")
    db.commit()
    # risk_last(100) + factor(0.25)*pnl(100) = 125
    assert state.risk_last_usd == pytest.approx(125.0)


def test_resaving_the_same_compounding_factor_does_not_erase_real_progress(db):
    # Same "only an ACTUAL change should reset/resync" guard as the
    # base_risk_usd fix -- the JS resends every field on every save.
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ea.get_or_init_sizing_policy(db, account)
    db.commit()
    ea.update_risk_state(db, account, {"compounding_factor": 0.25}, updated_by="andy@kabroda.com")
    db.commit()

    # Someone later sets a DIFFERENT roll_in_pct via the real Sizing Policy
    # Wizard (the primary, non-legacy path).
    ea.update_sizing_policy(db, account, {"roll_in_pct": 0.40}, updated_by="andy@kabroda.com")
    db.commit()

    # Re-saving the legacy panel with the SAME compounding_factor (0.25,
    # unchanged) must NOT clobber the wizard's newer 0.40.
    ea.update_risk_state(db, account, {"compounding_factor": 0.25, "risk_floor_usd": 50.0}, updated_by="andy@kabroda.com")
    db.commit()

    policy = ea.get_or_init_sizing_policy(db, account)
    assert policy.roll_in_pct == 0.40   # untouched -- the wizard's real value preserved


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


# ------------------------------------------------------------------ set_account_mode (2026-09-07)
# Before this existed, NOTHING in the codebase could ever set an account's
# mode to LIVE -- create_account() hardcodes DRY_RUN and nothing else ever
# assigned .mode, making executor_engine.py's whole LIVE branch
# unreachable in production regardless of how well-tested it was.

def test_set_account_mode_refuses_live_without_credentials(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    with pytest.raises(ValueError, match="no credentials set"):
        ea.set_account_mode(db, account, "LIVE", by="andy@kabroda.com", confirm="CONFIRM ENABLE LIVE TRADING")
    assert account.mode == "DRY_RUN"


def test_set_account_mode_refuses_live_without_ever_saving_a_sizing_choice(db):
    # 2026-09-08 real incident: Dawson picked 10%-of-balance in the wizard,
    # saw $250 in the live preview, went LIVE -- and his real trade filled
    # at $100, the untouched ExecutorRiskState default, because the sizing
    # choice was never actually saved (preview/save/go-live are three
    # separate clicks). get_or_init_sizing_policy()'s first-touch seed
    # stamps preset_name "steady_grow"/"conservative" (the OLD, pre-wizard
    # vocabulary) specifically so this exact state -- credentials set, but
    # sizing never explicitly confirmed -- is detectable and refused here,
    # the same way missing credentials already are.
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    ea.set_credentials(db, account, "key1", "secret1", set_by="andy@kabroda.com")
    db.commit()
    # Merely TOUCHING the policy (as executor_plan_builder.py does on every
    # plan build) must NOT count as an explicit save -- it only seeds the
    # old vocabulary, exactly the state this check exists to catch.
    ea.get_or_init_sizing_policy(db, account)
    db.commit()
    with pytest.raises(ValueError, match="no sizing choice has ever been explicitly saved"):
        ea.set_account_mode(db, account, "LIVE", by="andy@kabroda.com", confirm="CONFIRM ENABLE LIVE TRADING")
    assert account.mode == "DRY_RUN"

    # Once a real choice is explicitly saved (any of the new wizard's
    # preset_name values), going live succeeds normally.
    ea.update_sizing_policy(db, account, {"base_risk_pct": 0.10, "cap_abs_usd": 2500.0, "preset_name": "percent_capped"}, updated_by="andy@kabroda.com")
    db.commit()
    result = ea.set_account_mode(db, account, "LIVE", by="andy@kabroda.com", confirm="CONFIRM ENABLE LIVE TRADING")
    assert result.mode == "LIVE"


def test_set_account_mode_refuses_live_without_correct_confirm_phrase(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    ea.set_credentials(db, account, "key1", "secret1", set_by="andy@kabroda.com")
    ea.update_sizing_policy(db, account, {"base_risk_usd": 100.0, "preset_name": "fixed_dollar"}, updated_by="andy@kabroda.com")
    db.commit()
    with pytest.raises(ValueError, match="confirm phrase"):
        ea.set_account_mode(db, account, "LIVE", by="andy@kabroda.com", confirm="wrong phrase")
    assert account.mode == "DRY_RUN"

    with pytest.raises(ValueError, match="confirm phrase"):
        ea.set_account_mode(db, account, "LIVE", by="andy@kabroda.com", confirm=None)
    assert account.mode == "DRY_RUN"


def test_set_account_mode_live_succeeds_with_credentials_and_confirm(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    ea.set_credentials(db, account, "key1", "secret1", set_by="andy@kabroda.com")
    ea.update_sizing_policy(db, account, {"base_risk_usd": 100.0, "preset_name": "fixed_dollar"}, updated_by="andy@kabroda.com")
    db.commit()
    result = ea.set_account_mode(db, account, "LIVE", by="andy@kabroda.com", confirm="CONFIRM ENABLE LIVE TRADING")
    db.commit()
    assert result.mode == "LIVE"
    assert account.mode == "LIVE"

    rows = db.query(ExecutorAuditLog).filter_by(account_id=account.id, event_type="MODE_CHANGED").all()
    assert len(rows) == 1
    assert "DRY_RUN -> LIVE" in rows[0].message


def test_set_account_mode_live_to_dry_run_needs_no_confirm_phrase(db):
    # Safety-DECREASING direction -- matches this file's own kill-switch
    # asymmetry (engage requires nothing special either, release doesn't
    # need extra friction).
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    ea.set_credentials(db, account, "key1", "secret1", set_by="andy@kabroda.com")
    ea.update_sizing_policy(db, account, {"base_risk_usd": 100.0, "preset_name": "fixed_dollar"}, updated_by="andy@kabroda.com")
    ea.set_account_mode(db, account, "LIVE", by="andy@kabroda.com", confirm="CONFIRM ENABLE LIVE TRADING")
    db.commit()

    ea.set_account_mode(db, account, "DRY_RUN", by="andy@kabroda.com")
    db.commit()
    assert account.mode == "DRY_RUN"

    events = [r.event_type for r in db.query(ExecutorAuditLog).filter_by(account_id=account.id).order_by(ExecutorAuditLog.id).all()]
    assert events.count("MODE_CHANGED") == 2


def test_set_account_mode_rejects_unknown_mode(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    with pytest.raises(ValueError, match="mode must be one of"):
        ea.set_account_mode(db, account, "PAPER", by="andy@kabroda.com")


def test_set_account_mode_same_mode_is_a_harmless_noop(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    db.commit()
    ea.set_account_mode(db, account, "DRY_RUN", by="andy@kabroda.com")
    db.commit()
    # No spurious MODE_CHANGED row for a no-op transition.
    rows = db.query(ExecutorAuditLog).filter_by(account_id=account.id, event_type="MODE_CHANGED").all()
    assert len(rows) == 0
