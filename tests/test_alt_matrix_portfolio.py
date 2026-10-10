"""Unit + integration coverage for alt_matrix_portfolio.py -- the ONE Alt
Matrix module allowed to reference ExecutorOrder. Exercises the real
check_admission() against a real file-backed SQLite DB (same convention
as tests/test_executor_live_e1_engine.py), with the Bitunix client
monkeypatched (no real network calls)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./kabroda_test_alt_matrix_portfolio.db")

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount, ExecutorOrder, TravelerPlan, AltMatrixOrder, AltMatrixPlan
import executor_accounts as ea
import executor_control as ec
import executor_bitunix_client as ebc
import alt_matrix_portfolio as amp


def _clean_db_files():
    for path in ["kabroda_test_alt_matrix_portfolio.db", "kabroda_test_alt_matrix_portfolio.db-journal",
                 "kabroda_test_alt_matrix_portfolio.db-shm", "kabroda_test_alt_matrix_portfolio.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    for model in (ExecutorOrder, AltMatrixOrder, AltMatrixPlan, ExecutorAccount, TravelerPlan):
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


def _ready_account(db, label="alt_matrix_test"):
    account = ea.create_account(db, user_id=1, label=label)
    db.flush()
    account.mode = "LIVE"
    ea.set_credentials(db, account, api_key="fake-key", api_secret="fake-secret", set_by="test@kabroda.com")
    ec.enable_live_orders(db, reason="testing", by="andy@kabroda.com")
    db.commit()
    return account


def _traveler_plan(db, **overrides):
    defaults = dict(symbol="BTC/USDT", date_key="2026-10-09", session_id="us_ny_futures", status="WAITING_TOUCH")
    defaults.update(overrides)
    plan = TravelerPlan(**defaults)
    db.add(plan)
    db.flush()
    return plan


def _btc_resting_order(db, traveler_plan, risk_dollars_used=100.0, **overrides):
    defaults = dict(
        trade_plan_id=traveler_plan.id, traveler_plan_id=traveler_plan.id,
        account_id=1, mode="LIVE", symbol="BTC/USDT", direction="LONG",
        entry_price=100.0, stop_price=90.0, qty=0.01,
        risk_dollars_used=risk_dollars_used, decision="WOULD_PLACE",
        management_state="PENDING_ENTRY",
        gate_profile_used="GATE_TRAVELER", mgmt_profile_used="MGMT_E1_STACK",
    )
    defaults.update(overrides)
    order = ExecutorOrder(**defaults)
    db.add(order)
    db.flush()
    return order


_alt_plan_id_counter = {"n": 0}


def _alt_pending_order(db, account, risk_dollars_used=50.0, **overrides):
    # Unique on (alt_matrix_plan_id, account_id) -- each call needs its
    # own distinct plan_id unless the caller explicitly wants to test the
    # constraint itself.
    _alt_plan_id_counter["n"] += 1
    defaults = dict(
        alt_matrix_plan_id=_alt_plan_id_counter["n"], account_id=account.id, mode="LIVE",
        symbol="SOL/USDT", direction="LONG", risk_dollars_used=risk_dollars_used,
        management_state="PENDING_ENTRY",
    )
    defaults.update(overrides)
    order = AltMatrixOrder(**defaults)
    db.add(order)
    db.flush()
    return order


def _balance_response(available=1000.0, margin=0.0, unrealized=0.0):
    return {"code": 0, "data": {"available": str(available), "margin": str(margin), "isolationUnrealizedPNL": str(unrealized)}, "msg": "Success"}


def _positions_response(positions=None):
    return {"code": 0, "data": positions or [], "msg": "Success"}


def _tpsl_response(sl_price=None):
    data = [{"slPrice": str(sl_price)}] if sl_price is not None else []
    return {"code": 0, "data": data, "msg": "Success"}


def _install(monkeypatch, get_balance=None, get_position=None, get_pending_tp_sl_order=None):
    async def _default_balance(self, *a, **kw):
        return _balance_response()
    async def _default_position(self, *a, **kw):
        return _positions_response()
    async def _default_tpsl(self, *a, **kw):
        return _tpsl_response()
    monkeypatch.setattr(ebc.BitunixClient, "get_balance", get_balance or _default_balance)
    monkeypatch.setattr(ebc.BitunixClient, "get_position", get_position or _default_position)
    monkeypatch.setattr(ebc.BitunixClient, "get_pending_tp_sl_order", get_pending_tp_sl_order or _default_tpsl)


def _async(value):
    async def _fake(self, *a, **kw):
        return value
    return _fake


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ resting_btc_entry_exposure / alt_matrix_pending_exposure (pure DB reads)

def test_resting_btc_entry_exposure_counts_and_sums_risk(db):
    plan = _traveler_plan(db)
    _btc_resting_order(db, plan, risk_dollars_used=70.0)
    db.commit()
    result = amp.resting_btc_entry_exposure(db)
    assert result == {"count": 1, "risk_dollars": 70.0}


def test_resting_btc_entry_exposure_ignores_non_pending_or_non_live_rows(db):
    # Unique on (traveler_plan_id, account_id, is_rearm) -- each row needs
    # its own distinct account_id since all three share one plan.
    plan = _traveler_plan(db)
    _btc_resting_order(db, plan, risk_dollars_used=70.0, account_id=1, management_state="ENTRY_FILLED_ORDERS_PLACED")
    _btc_resting_order(db, plan, risk_dollars_used=80.0, account_id=2, mode="DRY_RUN")
    _btc_resting_order(db, plan, risk_dollars_used=90.0, account_id=3, decision="REJECTED")
    db.commit()
    result = amp.resting_btc_entry_exposure(db)
    assert result == {"count": 0, "risk_dollars": 0.0}


def test_resting_btc_entry_exposure_over_counts_across_multiple_live_accounts_by_design(db):
    # The "multi-account grouping" assumption from the approved plan: this
    # query has no account_id filter at all, so it naturally sums across
    # EVERY live Traveler account's resting entries, regardless of which
    # physical Bitunix account each belongs to. Over-counting only ever
    # causes extra Alt Matrix stand-downs -- it can never under-count and
    # therefore can never let Alt Matrix under-estimate BTC's real exposure.
    plan1 = _traveler_plan(db, date_key="2026-10-09")
    plan2 = _traveler_plan(db, date_key="2026-10-08")
    _btc_resting_order(db, plan1, risk_dollars_used=70.0, account_id=1)
    _btc_resting_order(db, plan2, risk_dollars_used=55.0, account_id=2)
    db.commit()
    result = amp.resting_btc_entry_exposure(db)
    assert result == {"count": 2, "risk_dollars": 125.0}


def test_alt_matrix_pending_exposure_counts_pending_and_unprotected(db):
    account = _ready_account(db)
    _alt_pending_order(db, account, risk_dollars_used=40.0, management_state="PENDING_ENTRY")
    _alt_pending_order(db, account, risk_dollars_used=60.0, management_state="ENTRY_FILLED_UNPROTECTED")
    _alt_pending_order(db, account, risk_dollars_used=999.0, management_state="CLOSED_STOP")
    _alt_pending_order(db, account, risk_dollars_used=999.0, mode="DRY_RUN")
    db.commit()
    result = amp.alt_matrix_pending_exposure(db)
    assert result == {"count": 2, "risk_dollars": 100.0}


# ------------------------------------------------------------------ exchange_account_state

def test_exchange_account_state_computes_equity_and_zero_risk_with_no_positions(db, monkeypatch):
    account = _ready_account(db)
    _install(monkeypatch, get_balance=_async(_balance_response(available=700.0, margin=0.0, unrealized=0.0)))
    result = _run(amp.exchange_account_state(account))
    assert result["equity"] == 700.0
    assert result["open_count"] == 0
    assert result["committed_risk"] == 0.0
    assert result["unprotected"] is False


def test_exchange_account_state_computes_risk_from_real_stop(db, monkeypatch):
    account = _ready_account(db)
    positions = [{"positionId": "pos1", "qty": "2.0", "avgOpenPrice": "100.0"}]
    _install(
        monkeypatch,
        get_balance=_async(_balance_response(available=500.0, margin=200.0, unrealized=10.0)),
        get_position=_async(_positions_response(positions)),
        get_pending_tp_sl_order=_async(_tpsl_response(sl_price=90.0)),
    )
    result = _run(amp.exchange_account_state(account))
    assert result["equity"] == 710.0
    assert result["open_count"] == 1
    assert result["committed_risk"] == pytest.approx(20.0)   # qty 2.0 * |100-90|
    assert result["unprotected"] is False


def test_exchange_account_state_flags_unprotected_when_no_stop_found(db, monkeypatch):
    account = _ready_account(db)
    positions = [{"positionId": "pos1", "qty": "1.0", "avgOpenPrice": "100.0"}]
    _install(
        monkeypatch,
        get_position=_async(_positions_response(positions)),
        get_pending_tp_sl_order=_async(_tpsl_response(sl_price=None)),
    )
    result = _run(amp.exchange_account_state(account))
    assert result["unprotected"] is True


def test_exchange_account_state_raises_on_real_api_error(db, monkeypatch):
    account = _ready_account(db)
    _install(monkeypatch, get_balance=_async({"code": 10001, "data": {}, "msg": "rate limited"}))
    with pytest.raises(RuntimeError):
        _run(amp.exchange_account_state(account))


# ------------------------------------------------------------------ check_admission -- the full decision

def test_check_admission_admits_a_clean_candidate(db, monkeypatch):
    account = _ready_account(db)
    _install(monkeypatch, get_balance=_async(_balance_response(available=1000.0)))
    result = _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=50.0, candidate_margin_required_usd=100.0))
    assert result["admitted"] is True
    assert result["reason"] is None


def test_check_admission_any_exchange_error_means_stand_down_never_guess(db, monkeypatch):
    account = _ready_account(db)
    async def _boom(self, *a, **kw):
        raise ConnectionError("simulated network failure")
    _install(monkeypatch, get_balance=_boom)
    result = _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=50.0, candidate_margin_required_usd=100.0))
    assert result["admitted"] is False
    assert "exchange_state_query_failed" in result["reason"]


def test_check_admission_unprotected_position_anywhere_forces_stand_down(db, monkeypatch):
    account = _ready_account(db)
    positions = [{"positionId": "pos1", "qty": "1.0", "avgOpenPrice": "100.0"}]
    _install(monkeypatch, get_position=_async(_positions_response(positions)), get_pending_tp_sl_order=_async(_tpsl_response(None)))
    result = _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=10.0, candidate_margin_required_usd=10.0))
    assert result["admitted"] is False
    assert result["reason"] == "an_open_position_has_no_registered_stop"


def test_check_admission_hand_placed_position_is_counted(db, monkeypatch):
    # A position the exchange reports that this codebase never placed
    # (no corresponding ExecutorOrder/AltMatrixOrder row at all) must
    # still count toward the open-position cap -- this is exactly why
    # the design reads the exchange directly rather than only this
    # site's own tables.
    account = _ready_account(db)
    positions = [
        {"positionId": "p1", "qty": "1.0", "avgOpenPrice": "100.0"},
        {"positionId": "p2", "qty": "1.0", "avgOpenPrice": "100.0"},
        {"positionId": "p3", "qty": "1.0", "avgOpenPrice": "100.0"},
    ]
    _install(monkeypatch, get_position=_async(_positions_response(positions)), get_pending_tp_sl_order=_async(_tpsl_response(sl_price=90.0)))
    result = _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=1.0, candidate_margin_required_usd=1.0))
    assert result["admitted"] is False
    assert result["reason"] == "max_open_positions_reached"   # 3 already open + this candidate = 4 > MAX_OPEN_POSITIONS


def test_check_admission_exactly_20_percent_risk_is_allowed_just_over_is_skipped(db, monkeypatch):
    account = _ready_account(db)
    _install(monkeypatch, get_balance=_async(_balance_response(available=1000.0)))   # equity = 1000
    exactly_20 = _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=200.0, candidate_margin_required_usd=10.0))
    assert exactly_20["admitted"] is True

    just_over = _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=200.01, candidate_margin_required_usd=10.0))
    assert just_over["admitted"] is False
    assert just_over["reason"] == "max_total_risk_pct_reached"


def test_check_admission_margin_reserve_floor_boundary(db, monkeypatch):
    # Andy's ruling: free margin AFTER this order, as a % of equity, must
    # stay >= 30%. equity=1000, available=1000 (no margin/positions yet).
    account = _ready_account(db)
    _install(monkeypatch, get_balance=_async(_balance_response(available=1000.0)))

    # Using exactly 700 of margin leaves exactly 300 free = 30% of 1000 -- allowed.
    at_floor = _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=1.0, candidate_margin_required_usd=700.0))
    assert at_floor["admitted"] is True

    # One dollar more of margin breaches the floor.
    over_floor = _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=1.0, candidate_margin_required_usd=701.0))
    assert over_floor["admitted"] is False
    assert over_floor["reason"] == "margin_reserve_floor_breached"


def test_check_admission_counts_btc_resting_entry_toward_both_caps(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    _btc_resting_order(db, plan, risk_dollars_used=150.0)   # reserves 1 slot + $150 risk
    db.commit()
    _install(monkeypatch, get_balance=_async(_balance_response(available=1000.0)))

    # 2 more Alt positions would make 3 total (BTC resting + 2 Alt) -- fine on count,
    # but risk 150 (BTC) + 50 (candidate) = 200 = exactly 20% of 1000 -- still allowed.
    result = _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=50.0, candidate_margin_required_usd=10.0))
    assert result["admitted"] is True
    assert result["snapshot"]["btc_resting_count"] == 1
    assert result["snapshot"]["btc_resting_risk"] == 150.0

    # Now push just over the risk cap.
    result2 = _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=50.01, candidate_margin_required_usd=10.0))
    assert result2["admitted"] is False
    assert result2["reason"] == "max_total_risk_pct_reached"


def test_check_admission_sees_a_just_placed_alt_order_from_a_prior_call(db, monkeypatch):
    # The "serialize SOL/ETH admission" requirement from the approved
    # plan is the ENGINE's own job (a lock around back-to-back calls),
    # not this function's -- but this proves the mechanism it depends on:
    # once the first candidate's own AltMatrixOrder row is created, the
    # SECOND call's own exposure read picks it up immediately.
    account = _ready_account(db)
    _install(monkeypatch, get_balance=_async(_balance_response(available=1000.0)))

    first = _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=150.0, candidate_margin_required_usd=10.0))
    assert first["admitted"] is True
    _alt_pending_order(db, account, risk_dollars_used=150.0, symbol="SOL/USDT")
    db.commit()

    second = _run(amp.check_admission(db, account, "ETH/USDT", candidate_risk_dollars=50.0, candidate_margin_required_usd=10.0))
    assert second["snapshot"]["alt_pending_count"] == 1
    assert second["snapshot"]["alt_pending_risk"] == 150.0
    # total committed (150 alt pending + 50 candidate) = 200 = exactly 20% of 1000 -- still allowed
    assert second["admitted"] is True


def test_check_admission_non_positive_equity_stands_down(db, monkeypatch):
    account = _ready_account(db)
    _install(monkeypatch, get_balance=_async(_balance_response(available=0.0, margin=0.0, unrealized=0.0)))
    result = _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=1.0, candidate_margin_required_usd=1.0))
    assert result["admitted"] is False
    assert result["reason"] == "non_positive_equity"


# ------------------------------------------------------------------ the no-write guard (approved plan's own verification requirement)
# This module's own header claims zero db.add()/flush()/commit() calls
# anywhere in it. Proven here, not just asserted -- and proven CORRECTLY:
# a first version of this test used a SQLAlchemy before_flush listener,
# which never actually fired, because this project's own SessionLocal is
# built with autoflush=False (database.py) -- a pending db.add() with no
# explicit flush()/commit() never triggers that event at all, so the
# listener was silently vacuous. Verified the hard way: injected a real
# db.add() into check_admission() and confirmed the before_flush version
# still reported zero violations. Fixed to inspect session.new/dirty/
# deleted directly (a before/after snapshot diff), which catches a
# pending write regardless of whether anything ever flushes it -- then
# re-verified the SAME injected write actually fails this version.

def _session_write_snapshot(session):
    return set(session.new), set(session.dirty), set(session.deleted)


def test_check_admission_never_writes_to_the_database_on_any_path(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    _btc_resting_order(db, plan, risk_dollars_used=50.0)
    alt_account = _ready_account(db, label="alt_matrix_test_2")
    _alt_pending_order(db, alt_account, risk_dollars_used=30.0)
    db.commit()

    def _assert_no_new_pending_writes(before):
        after = _session_write_snapshot(db)
        assert after[0] - before[0] == set(), f"new objects pending: {after[0] - before[0]}"
        assert after[1] - before[1] == set(), f"dirty objects pending: {after[1] - before[1]}"
        assert after[2] - before[2] == set(), f"deleted objects pending: {after[2] - before[2]}"

    # Admit path
    _install(monkeypatch, get_balance=_async(_balance_response(available=1000.0)))
    before = _session_write_snapshot(db)
    _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=10.0, candidate_margin_required_usd=10.0))
    _assert_no_new_pending_writes(before)

    # Stand-down paths: unprotected position, exchange error, margin floor, risk cap
    _install(monkeypatch, get_position=_async(_positions_response([{"positionId": "p1", "qty": "1.0", "avgOpenPrice": "100.0"}])),
             get_pending_tp_sl_order=_async(_tpsl_response(None)))
    before = _session_write_snapshot(db)
    _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=10.0, candidate_margin_required_usd=10.0))
    _assert_no_new_pending_writes(before)

    async def _boom(self, *a, **kw):
        raise ConnectionError("simulated")
    _install(monkeypatch, get_balance=_boom)
    before = _session_write_snapshot(db)
    _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=10.0, candidate_margin_required_usd=10.0))
    _assert_no_new_pending_writes(before)

    _install(monkeypatch, get_balance=_async(_balance_response(available=1000.0)))
    before = _session_write_snapshot(db)
    _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=10.0, candidate_margin_required_usd=701.0))
    _assert_no_new_pending_writes(before)
    before = _session_write_snapshot(db)
    _run(amp.check_admission(db, account, "SOL/USDT", candidate_risk_dollars=300.0, candidate_margin_required_usd=10.0))
    _assert_no_new_pending_writes(before)
