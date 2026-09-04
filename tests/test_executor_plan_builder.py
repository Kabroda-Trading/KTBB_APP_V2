"""
Unit coverage for executor_plan_builder.py -- DB-backed with hand-built
TradePlan/ExecutorAccount/ExecutorRiskState rows, same fixture style as
tests/test_executor_accounts.py.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_executor_plan_builder.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount, ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorGlobalConfig, TradePlan
import executor_accounts as ea
import executor_plan_builder as epb


def _clean_db_files():
    for path in ["kabroda_test_executor_plan_builder.db", "kabroda_test_executor_plan_builder.db-journal",
                 "kabroda_test_executor_plan_builder.db-shm", "kabroda_test_executor_plan_builder.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    # Row-level, not just _clean_db_files() -- database.engine is cached
    # module-globally across the whole pytest session (see
    # test_trade_plan_engine.py's own fixture comment), so every test
    # file must clean up EVERY table it could plausibly collide on, not
    # just its own file's expected db path.
    for model in (ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorAccount, ExecutorGlobalConfig, TradePlan):
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


def _make_filled_plan(db, symbol="BTC/USDT", direction="LONG", entry=100.0, stop=95.0, t1=112.0, t2=120.0, t3=132.0, date_key="2026-09-04"):
    plan = TradePlan(
        symbol=symbol, date_key=date_key, session_id="us_ny_futures", status="FILLED",
        direction=direction, tier="STANDARD", trigger_price=entry, fill_price=entry,
        stop_price=stop, t1=t1, t2=t2, t3=t3,
    )
    db.add(plan)
    db.flush()
    return plan


def _make_account(db, label="andy_bitunix_main", leverage_baseline=10, assumed_balance_usd=10000.0):
    account = ea.create_account(db, user_id=1, label=label)
    account.leverage_baseline = leverage_baseline
    account.assumed_balance_usd = assumed_balance_usd
    db.flush()
    state = ea.get_or_init_risk_state(db, account)
    db.commit()
    return account, state


# ------------------------------------------------------------------ WOULD_PLACE (normal case, hand-verified math)

def test_would_place_normal_case(db):
    plan = _make_filled_plan(db, entry=100.0, stop=95.0)   # stop_distance=5
    account, state = _make_account(db, assumed_balance_usd=100000.0)  # huge balance -- no margin pressure
    # risk_last_usd default 100.0 -> qty = 100/5 = 20.0

    order = epb.build_hypothetical_order(db, plan, account, state)
    assert order["decision"] == "WOULD_PLACE"
    assert order["qty"] == pytest.approx(20.0)
    assert order["stop_distance"] == pytest.approx(5.0)
    assert order["leverage_used"] == 10  # baseline, no margin pressure with $100k balance
    assert order["liquidation_check_passed"] is True
    # liq at 10x LONG: 100*(1-0.1)=90, distance 10 > stop_distance 5 -- safe
    assert order["liquidation_price_estimate"] == pytest.approx(90.0)


def test_would_place_short_side(db):
    plan = _make_filled_plan(db, direction="SHORT", entry=100.0, stop=105.0)
    account, state = _make_account(db, assumed_balance_usd=100000.0)
    order = epb.build_hypothetical_order(db, plan, account, state)
    assert order["decision"] == "WOULD_PLACE"
    assert order["direction"] == "SHORT"
    assert order["liquidation_price_estimate"] == pytest.approx(110.0)


# ------------------------------------------------------------------ REJECTED (forced liquidation-check failure)

def test_rejected_when_liquidation_inside_stop(db):
    # A very tight stop (0.5 away) at 10x baseline leverage: liq_distance =
    # 100*(1/10)=10, far beyond the 0.5 stop -- need a MUCH higher leverage
    # to force liq inside the stop. Force it via a huge leverage_baseline
    # directly (bypassing suggest_leverage's own safety refusal by putting
    # the baseline itself already past the unsafe point).
    plan = _make_filled_plan(db, entry=100.0, stop=99.5)   # stop_distance=0.5
    account, state = _make_account(db, leverage_baseline=250, assumed_balance_usd=100000.0)
    # liq at 250x LONG: 100*(1-1/250)=99.6, distance 0.4 < stop_distance 0.5 -- UNSAFE
    order = epb.build_hypothetical_order(db, plan, account, state)
    assert order["decision"] == "REJECTED"
    assert order["liquidation_check_passed"] is False
    assert "refuse this trade" in order["decision_reason"]


# ------------------------------------------------------------------ SKIPPED_KILL_SWITCH / SKIPPED_ACCOUNT_INACTIVE

def test_skipped_when_account_kill_switch_engaged(db):
    plan = _make_filled_plan(db)
    account, state = _make_account(db)
    ea.engage_kill_switch(db, account, reason="testing", by="andy@kabroda.com")
    db.commit()
    order = epb.build_hypothetical_order(db, plan, account, state)
    assert order["decision"] == "SKIPPED_KILL_SWITCH"


def test_skipped_when_account_inactive(db):
    plan = _make_filled_plan(db)
    account, state = _make_account(db)
    account.is_active = False
    db.commit()
    order = epb.build_hypothetical_order(db, plan, account, state)
    assert order["decision"] == "SKIPPED_ACCOUNT_INACTIVE"


# ------------------------------------------------------------------ SKIPPED_ALREADY_IN_TRADE

def test_skipped_already_in_trade_same_plan_twice(db):
    plan = _make_filled_plan(db)
    account, state = _make_account(db, assumed_balance_usd=100000.0)
    first = epb.build_hypothetical_order(db, plan, account, state)
    assert first["decision"] == "WOULD_PLACE"
    db.add(ExecutorOrder(**{k: v for k, v in first.items() if k in ExecutorOrder.__table__.columns.keys()}))
    db.commit()

    second = epb.build_hypothetical_order(db, plan, account, state)
    assert second["decision"] == "SKIPPED_ALREADY_IN_TRADE"


def test_skipped_already_in_trade_different_open_plan(db):
    account, state = _make_account(db, assumed_balance_usd=100000.0)

    plan1 = _make_filled_plan(db, symbol="BTC/USDT", date_key="2026-09-03")
    order1 = epb.build_hypothetical_order(db, plan1, account, state)
    assert order1["decision"] == "WOULD_PLACE"
    db.add(ExecutorOrder(**{k: v for k, v in order1.items() if k in ExecutorOrder.__table__.columns.keys()}))
    db.commit()
    # plan1 stays "FILLED" (not DONE) -- still open

    plan2 = _make_filled_plan(db, symbol="ETH/USDT", date_key="2026-09-04")
    order2 = epb.build_hypothetical_order(db, plan2, account, state)
    assert order2["decision"] == "SKIPPED_ALREADY_IN_TRADE"


def test_not_skipped_when_prior_plan_is_done(db):
    account, state = _make_account(db, assumed_balance_usd=100000.0)

    plan1 = _make_filled_plan(db, symbol="BTC/USDT", date_key="2026-09-03")
    order1 = epb.build_hypothetical_order(db, plan1, account, state)
    db.add(ExecutorOrder(**{k: v for k, v in order1.items() if k in ExecutorOrder.__table__.columns.keys()}))
    plan1.status = "DONE"   # resolved
    db.commit()

    plan2 = _make_filled_plan(db, symbol="ETH/USDT", date_key="2026-09-04")
    order2 = epb.build_hypothetical_order(db, plan2, account, state)
    assert order2["decision"] == "WOULD_PLACE"
