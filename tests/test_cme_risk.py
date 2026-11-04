"""Unit + integration coverage for cme_risk.py -- trailing-drawdown-from-
high-water-mark tracking and the $175/$350 daily risk-budget split, against
a real file-backed SQLite DB (same convention as tests/test_alt_matrix_portfolio.py).
Deliberately does NOT exercise any cross-system (ExecutorOrder/TravelerPlan)
read -- this module has none, unlike alt_matrix_portfolio.py, because CME
shares no account or margin pool with Bitunix."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./kabroda_test_cme_risk.db")

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount, CMEOrder, CMEPlan
import executor_accounts as ea
import cme_risk


def _clean_db_files():
    for path in ["kabroda_test_cme_risk.db", "kabroda_test_cme_risk.db-journal",
                 "kabroda_test_cme_risk.db-shm", "kabroda_test_cme_risk.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    for model in (CMEOrder, CMEPlan, ExecutorAccount):
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


def _ready_account(db, trailing_dd_limit=2000.0, hwm=50000.0):
    account = ea.create_account(db, user_id=1, label="cme_test")
    db.flush()
    account.market_type = "CME_FUTURES"
    account.account_category = "PROP_EVALUATION"
    account.trailing_drawdown_limit_usd = trailing_dd_limit
    account.high_water_mark_usd = hwm
    db.commit()
    return account


def _plan(db, symbol="MNQ", date_key="2026-11-04"):
    plan = CMEPlan(symbol=symbol, date_key=date_key, lock_time=database.datetime.datetime.utcnow(), status="ARMED")
    db.add(plan)
    db.flush()
    return plan


def _order(db, plan, account, symbol, management_state="FILLED"):
    order = CMEOrder(cme_plan_id=plan.id, account_id=account.id, mode="LIVE", symbol=symbol,
                      direction="LONG", management_state=management_state)
    db.add(order)
    db.flush()
    return order


# ------------------------------------------------------------------ high-water mark

def test_update_high_water_mark_advances_on_new_peak():
    account = ExecutorAccount(high_water_mark_usd=50000.0)
    assert cme_risk.update_high_water_mark(account, 51000.0) == 51000.0
    assert account.high_water_mark_usd == 51000.0


def test_update_high_water_mark_never_moves_down():
    account = ExecutorAccount(high_water_mark_usd=51000.0)
    assert cme_risk.update_high_water_mark(account, 49000.0) == 51000.0
    assert account.high_water_mark_usd == 51000.0


def test_update_high_water_mark_initializes_from_none():
    account = ExecutorAccount(high_water_mark_usd=None)
    assert cme_risk.update_high_water_mark(account, 50000.0) == 50000.0


# ------------------------------------------------------------------ trailing drawdown check

def test_check_trailing_drawdown_fails_closed_when_unconfigured():
    account = ExecutorAccount(trailing_drawdown_limit_usd=None, high_water_mark_usd=50000.0)
    result = cme_risk.check_trailing_drawdown(account, 49000.0)
    assert result["ok"] is False
    assert result["reason"] == "NO_TRAILING_DD_LIMIT_CONFIGURED"


def test_check_trailing_drawdown_ok_well_within_buffer():
    account = ExecutorAccount(trailing_drawdown_limit_usd=2000.0, high_water_mark_usd=50000.0)
    result = cme_risk.check_trailing_drawdown(account, 49500.0)
    assert result["ok"] is True
    assert result["drawdown_usd"] == pytest.approx(500.0)
    assert result["buffer_remaining_usd"] == pytest.approx(1500.0)
    assert result["warn"] is False


def test_check_trailing_drawdown_warns_once_past_half_the_buffer():
    account = ExecutorAccount(trailing_drawdown_limit_usd=2000.0, high_water_mark_usd=50000.0)
    result = cme_risk.check_trailing_drawdown(account, 48900.0)   # dd=1100, buffer=900 < 1000 (50%)
    assert result["ok"] is True
    assert result["warn"] is True


def test_check_trailing_drawdown_breached_at_exactly_the_limit():
    account = ExecutorAccount(trailing_drawdown_limit_usd=2000.0, high_water_mark_usd=50000.0)
    result = cme_risk.check_trailing_drawdown(account, 48000.0)   # dd == limit exactly
    assert result["ok"] is False
    assert "TRAILING_DD_BREACHED" in result["reason"]


def test_check_trailing_drawdown_advances_hwm_before_checking():
    account = ExecutorAccount(trailing_drawdown_limit_usd=2000.0, high_water_mark_usd=50000.0)
    result = cme_risk.check_trailing_drawdown(account, 52000.0)   # new peak -- zero drawdown
    assert account.high_water_mark_usd == 52000.0
    assert result["ok"] is True
    assert result["drawdown_usd"] == 0.0


# ------------------------------------------------------------------ risk_budget_for_symbol

def test_risk_budget_full_when_other_symbol_has_no_open_order(db):
    account = _ready_account(db)
    budget = cme_risk.risk_budget_for_symbol(db, account.id, "MNQ", 350.0, "2026-11-04")
    assert budget == 350.0


def test_risk_budget_split_when_other_symbol_already_open_today(db):
    account = _ready_account(db)
    plan = _plan(db, symbol="MES", date_key="2026-11-04")
    _order(db, plan, account, symbol="MES", management_state="FILLED")
    budget = cme_risk.risk_budget_for_symbol(db, account.id, "MNQ", 350.0, "2026-11-04")
    assert budget == 175.0


def test_risk_budget_full_when_other_symbols_order_is_from_a_different_day(db):
    account = _ready_account(db)
    plan = _plan(db, symbol="MES", date_key="2026-11-03")
    _order(db, plan, account, symbol="MES", management_state="FILLED")
    budget = cme_risk.risk_budget_for_symbol(db, account.id, "MNQ", 350.0, "2026-11-04")
    assert budget == 350.0


def test_risk_budget_full_when_other_symbols_order_already_closed(db):
    account = _ready_account(db)
    plan = _plan(db, symbol="MES", date_key="2026-11-04")
    _order(db, plan, account, symbol="MES", management_state="CLOSED_TARGET")
    budget = cme_risk.risk_budget_for_symbol(db, account.id, "MNQ", 350.0, "2026-11-04")
    assert budget == 350.0


# ------------------------------------------------------------------ can_open_new_position

def test_can_open_new_position_admits_a_clean_candidate(db):
    account = _ready_account(db)
    result = cme_risk.can_open_new_position(
        db, account, "MNQ", "2026-11-04", current_equity=50000.0,
        daily_risk_budget_usd=350.0, contracts_cap=3, box_points=20.0,
    )
    assert result["admitted"] is True
    assert result["contracts"] == 3
    assert result["risk_budget_usd"] == 350.0


def test_can_open_new_position_rejects_if_already_open_on_symbol_today(db):
    # WAITING_RETEST, not PENDING_ENTRY -- PENDING_ENTRY is deliberately
    # NOT "open" (see _OPEN_CME_STATES's own comment, 2026-11-04): the D1
    # fan-out creates a PENDING_ENTRY row for BOTH directions as a
    # legitimate OCO-style pair, and treating that as "already open"
    # made the second direction self-reject against its own sibling.
    # WAITING_RETEST (a cross already confirmed, bracket already
    # resting) is the correct state for what this test is actually
    # checking -- a real, already-in-progress trade on this symbol today.
    account = _ready_account(db)
    plan = _plan(db, symbol="MNQ", date_key="2026-11-04")
    _order(db, plan, account, symbol="MNQ", management_state="WAITING_RETEST")
    result = cme_risk.can_open_new_position(
        db, account, "MNQ", "2026-11-04", current_equity=50000.0,
        daily_risk_budget_usd=350.0, contracts_cap=3, box_points=20.0,
    )
    assert result["admitted"] is False
    assert result["reason"] == "ALREADY_OPEN_ON_SYMBOL_TODAY"


def test_can_open_new_position_rejects_on_trailing_dd_breach(db):
    account = _ready_account(db, trailing_dd_limit=2000.0, hwm=50000.0)
    result = cme_risk.can_open_new_position(
        db, account, "MNQ", "2026-11-04", current_equity=47000.0,   # dd=3000 > 2000 limit
        daily_risk_budget_usd=350.0, contracts_cap=3, box_points=20.0,
    )
    assert result["admitted"] is False
    assert "TRAILING_DD_BREACHED" in result["reason"]


def test_can_open_new_position_splits_budget_when_other_symbol_live(db):
    account = _ready_account(db)
    other_plan = _plan(db, symbol="MES", date_key="2026-11-04")
    _order(db, other_plan, account, symbol="MES", management_state="FILLED")
    result = cme_risk.can_open_new_position(
        db, account, "MNQ", "2026-11-04", current_equity=50000.0,
        daily_risk_budget_usd=350.0, contracts_cap=3, box_points=20.0,
    )
    assert result["admitted"] is True
    assert result["risk_budget_usd"] == 175.0
    assert result["contracts"] == 3   # floor(175/(20*2))=4, capped at contracts_cap=3
