"""
Integration coverage for dry_run_split_engine.py -- runs the ACTUAL
process_fill()/run_dry_run_split_loop() chain against a monkeypatched
candle feed, same harness style as tests/test_traveler_plan_engine.py.
Exercises the real production code path end to end: a FILLED TradePlan ->
executor_engine.process_fill() immediately marks a DRY_RUN/MGMT_SPLIT
order ENTRY_FILLED_ORDERS_PLACED (Ruling B's fix, executor_engine.py) ->
dry_run_split_engine's own poll (mgmt_split_dry_run.py) walks it to a real
close.

Ruling B (DeepSeek, relayed by Andy 2026-09-15): v1/v2's own DRY_RUN
orders used to sit at PENDING_ENTRY forever, with no exit ever recorded --
this is the fix, and this file is its own step's own test, additive only,
never touching executor_live_engine.py's real LIVE poll loop.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_dry_run_split_engine.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import datetime as dt
from datetime import timezone, timedelta

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, TradePlan, ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorSizingPolicy
import dry_run_split_engine as dse
import executor_engine
import executor_accounts as ea


def _clean_db_files():
    for path in ["kabroda_test_dry_run_split_engine.db", "kabroda_test_dry_run_split_engine.db-journal",
                 "kabroda_test_dry_run_split_engine.db-shm", "kabroda_test_dry_run_split_engine.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


class _StopLoop(Exception):
    pass


def _c5m(close, ts, high=None, low=None):
    return {"close": close, "high": high if high is not None else close, "low": low if low is not None else close, "time": ts}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", Fernet.generate_key().decode("utf-8"))
    _clean_db_files()
    database.init_db()
    db = SessionLocal()
    for model in (TradePlan, ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorSizingPolicy):
        db.query(model).delete()
    db.commit()
    db.close()

    DEFAULT_DATE_KEY = (dt.datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

    def make_filled_plan(symbol="BTC/USDT", **kwargs):
        db = SessionLocal()
        defaults = dict(
            symbol=symbol, date_key=DEFAULT_DATE_KEY, session_id="us_ny_futures",
            status="FILLED", direction="LONG",
            trigger_price=50000.0, fill_price=50000.0,
            fill_time=dt.datetime.fromtimestamp(1700000000, tz=timezone.utc),
            stop_price=49700.0, t1=50300.0, t2=50600.0, t3=50900.0,
        )
        defaults.update(kwargs)
        row = TradePlan(**defaults)
        db.add(row)
        db.commit()
        row_id = row.id
        db.close()
        return row_id

    def make_account(gate_profile="GATE_V2", mgmt_profile="MGMT_SPLIT", risk_last_usd=100.0):
        db = SessionLocal()
        account = ea.create_account(db, user_id=1, label="split_dry_run_test_account")
        db.flush()
        ea.set_account_profile(db, account, gate_profile=gate_profile, mgmt_profile=mgmt_profile, by="test")
        state = ea.get_or_init_risk_state(db, account)
        state.risk_last_usd = risk_last_usd
        db.commit()
        account_id = account.id
        db.close()
        return account_id

    def process_the_fill(plan_id):
        db = SessionLocal()
        plan = db.query(TradePlan).filter_by(id=plan_id).first()
        asyncio.run(executor_engine.process_fill(db, plan))
        db.commit()
        db.close()

    def run_polls(candles_5m_by_symbol=None, polls=1):
        candles_5m_by_symbol = candles_5m_by_symbol or {}

        async def fake_5m(symbol, limit=2016):
            return candles_5m_by_symbol.get(symbol, [])

        sleeps = {"n": 0}

        async def fake_sleep(seconds):
            sleeps["n"] += 1
            if sleeps["n"] >= polls:
                raise _StopLoop()

        monkeypatch.setattr(dse.market_data, "fetch_live_5m", fake_5m)
        monkeypatch.setattr(dse.asyncio, "sleep", fake_sleep)

        async def main():
            try:
                await dse.run_dry_run_split_loop()
            except _StopLoop:
                pass

        asyncio.run(main())

    def get_orders(trade_plan_id=None):
        db = SessionLocal()
        q = db.query(ExecutorOrder)
        if trade_plan_id is not None:
            q = q.filter_by(trade_plan_id=trade_plan_id)
        rows = q.all()
        db.expunge_all()
        db.close()
        return rows

    yield {
        "make_filled_plan": make_filled_plan, "make_account": make_account,
        "process_the_fill": process_the_fill, "run_polls": run_polls, "get_orders": get_orders,
    }

    db = SessionLocal()
    for model in (TradePlan, ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorSizingPolicy):
        db.query(model).delete()
    db.commit()
    db.close()
    _clean_db_files()


def test_process_fill_immediately_marks_a_dry_run_split_order_entry_filled(env):
    account_id = env["make_account"]()
    plan_id = env["make_filled_plan"]()
    env["process_the_fill"](plan_id)

    orders = env["get_orders"](trade_plan_id=plan_id)
    assert len(orders) == 1
    order = orders[0]
    assert order.account_id == account_id
    assert order.decision == "WOULD_PLACE"
    assert order.gate_profile_used == "GATE_V2"
    assert order.mgmt_profile_used == "MGMT_SPLIT"
    assert order.entry_fill_price == 50000.0
    # SQLite round-trips a naive datetime (strips tzinfo) -- same convention
    # every other datetime-comparison test in this codebase already accepts.
    assert order.entry_fill_time == dt.datetime.fromtimestamp(1700000000, tz=timezone.utc).replace(tzinfo=None)
    assert order.management_state == "ENTRY_FILLED_ORDERS_PLACED"


def test_dry_run_split_loop_takes_t1_then_closes_at_t3(env):
    env["make_account"]()
    plan_id = env["make_filled_plan"]()
    env["process_the_fill"](plan_id)

    ct = 1700000000
    t1_bar = [_c5m(50350.0, ct + 300, high=50400.0, low=50200.0)]  # T1 (50300) touched
    env["run_polls"](candles_5m_by_symbol={"BTC/USDT": t1_bar}, polls=1)

    order = env["get_orders"](trade_plan_id=plan_id)[0]
    assert order.management_state == "T1_FILLED"
    assert order.t1_fill_price == 50300.0
    expected_t1_leg_r = 0.5 * ((50300.0 - 50000.0) / 300.0)
    assert order.t1_leg_r == pytest.approx(expected_t1_leg_r)

    runner_bar = [
        _c5m(50350.0, ct + 300, high=50400.0, low=50200.0),
        _c5m(50950.0, ct + 600, high=51000.0, low=50800.0),   # T3 (50900) touched
    ]
    env["run_polls"](candles_5m_by_symbol={"BTC/USDT": runner_bar}, polls=1)

    order = env["get_orders"](trade_plan_id=plan_id)[0]
    assert order.management_state == "CLOSED_T3"
    assert order.exit_reason == "T3"
    assert order.exit_price == 50900.0
    assert order.closed_at is not None
    expected_runner_r = 0.5 * ((50900.0 - 50000.0) / 300.0)
    assert order.realized_pnl_r == pytest.approx(expected_t1_leg_r + expected_runner_r)


def test_dry_run_split_loop_stop_before_t1_is_a_clean_full_loss(env):
    env["make_account"]()
    plan_id = env["make_filled_plan"]()
    env["process_the_fill"](plan_id)

    ct = 1700000000
    stop_bar = [_c5m(49650.0, ct + 300, high=49900.0, low=49600.0)]  # stop (49700) touched via low
    env["run_polls"](candles_5m_by_symbol={"BTC/USDT": stop_bar}, polls=1)

    order = env["get_orders"](trade_plan_id=plan_id)[0]
    assert order.management_state == "CLOSED_STOP_BEFORE_T1"
    assert order.exit_reason == "STOP_BEFORE_T1"
    assert order.exit_price == 49700.0
    assert order.realized_pnl_r == pytest.approx(-1.0)
    assert order.closed_at is not None


def test_dry_run_split_loop_ignores_gate_traveler_accounts(env):
    env["make_account"](gate_profile="GATE_TRAVELER", mgmt_profile="MGMT_E1_STACK")
    # A GATE_TRAVELER account never acts on a TradePlan fill at all
    # (executor_engine._process_account()'s own gate_profile_of() guard) --
    # no order is even created, so there is nothing for this loop to skip
    # incorrectly. Confirms the new loop's own account-side plumbing (via
    # process_fill) does not accidentally create SPLIT-shaped work for a
    # traveler account.
    plan_id = env["make_filled_plan"]()
    env["process_the_fill"](plan_id)
    assert env["get_orders"](trade_plan_id=plan_id) == []


def test_dry_run_split_loop_leaves_live_orders_alone(env):
    # A LIVE account's order must NEVER be picked up by this DRY_RUN-only
    # loop -- construct one directly (bypassing the real exchange calls
    # process_fill's LIVE branch would make) and confirm the loop's own
    # query filter (mode == "DRY_RUN") skips it entirely.
    db = SessionLocal()
    account = ea.create_account(db, user_id=1, label="live_account")
    db.flush()
    order = ExecutorOrder(
        trade_plan_id=1, account_id=account.id, mode="LIVE", symbol="BTC/USDT", direction="LONG",
        entry_fill_price=50000.0, entry_fill_time=dt.datetime.fromtimestamp(1700000000, tz=timezone.utc),
        stop_price=49700.0, t1_price=50300.0, t3_price=50900.0,
        management_state="PENDING_ENTRY", decision="WOULD_PLACE",
        mgmt_profile_used="MGMT_SPLIT", gate_profile_used="GATE_V2",
    )
    db.add(order)
    db.commit()
    order_id = order.id
    db.close()

    stop_bar = [_c5m(49650.0, 1700000000 + 300, high=49900.0, low=49600.0)]
    env["run_polls"](candles_5m_by_symbol={"BTC/USDT": stop_bar}, polls=1)

    db = SessionLocal()
    reloaded = db.query(ExecutorOrder).filter_by(id=order_id).first()
    assert reloaded.management_state == "PENDING_ENTRY"   # untouched
    db.close()
