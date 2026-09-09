"""
Regression coverage for the executor bot's hook into trade_plan_engine.py
-- runs the ACTUAL run_trade_plan_loop()/_advance_one()/_apply() chain
against a monkeypatched exchange, same harness style as
tests/test_trade_plan_engine.py's poll_env fixture. Proves three things:
(1) the real TradePlan FILLED write is untouchable by an executor bug,
(2) exactly one ExecutorOrder + audit row lands per active account, and
(3) two independent accounts get independently correct sizing from the
SAME TradePlan row -- the direct proof of the multi-account requirement.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_executor_engine.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import datetime as dt
from datetime import timezone, timedelta

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, TradePlan, ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorGlobalConfig, ExecutorSizingPolicy
import trade_plan_engine as tpe
import executor_plan_builder
import executor_accounts as ea
import executor_control as ec


def _clean_db_files():
    for path in ["kabroda_test_executor_engine.db", "kabroda_test_executor_engine.db-journal",
                 "kabroda_test_executor_engine.db-shm", "kabroda_test_executor_engine.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


class _StopLoop(Exception):
    pass


def _c5m(close, volume):
    return {"close": close, "volume": volume}


def _fueled_5m_candles(trigger, is_long, baseline_vol=10.0, push_vol=10.0, baseline_n=250, push_n=6):
    near = trigger - 5.0 if is_long else trigger + 5.0
    beyond = trigger + 5.0 if is_long else trigger - 5.0
    return ([_c5m(near, baseline_vol)] * baseline_n) + ([_c5m(beyond, push_vol)] * push_n)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", Fernet.generate_key().decode("utf-8"))
    _clean_db_files()
    database.init_db()
    db = SessionLocal()
    for model in (TradePlan, ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorGlobalConfig, ExecutorSizingPolicy):
        db.query(model).delete()
    db.commit()
    db.close()

    DEFAULT_DATE_KEY = (dt.datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    now = dt.datetime(2026, 9, 4, 14, 0, 0, tzinfo=timezone.utc)

    def make_plan(symbol="BTC/USDT", date_key=None, session_id="us_ny_futures", **kwargs):
        date_key = date_key or DEFAULT_DATE_KEY
        db = SessionLocal()
        defaults = dict(
            symbol=symbol, date_key=date_key, session_id=session_id,
            status="WAITING", direction="LONG", trigger_price=100.0,
            stop_price=95.0, t1=112.0, t2=120.0, t3=132.0,
            commit_after=now - timedelta(minutes=5),
        )
        defaults.update(kwargs)
        row = TradePlan(**defaults)
        db.add(row)
        db.commit()
        db.close()

    def make_account(label="andy_bitunix_main", leverage_baseline=10, assumed_balance_usd=100000.0, risk_last_usd=100.0):
        db = SessionLocal()
        account = ea.create_account(db, user_id=1, label=label)
        account.leverage_baseline = leverage_baseline
        account.assumed_balance_usd = assumed_balance_usd
        db.flush()
        state = ea.get_or_init_risk_state(db, account)
        state.risk_last_usd = risk_last_usd
        db.commit()
        account_id = account.id
        db.close()
        return account_id

    def run_polls(candles_5m_by_symbol=None, polls=1):
        candles_5m_by_symbol = candles_5m_by_symbol or {}

        async def fake_5m(symbol, limit=310):
            return candles_5m_by_symbol.get(symbol, [])

        async def fake_1h(symbol, limit=100):
            return []

        async def fake_4h(symbol, limit=100):
            return []

        async def fake_daily(symbol, limit=60):
            return []

        def fake_atr(candles_1d):
            return 0.0

        sleeps = {"n": 0}

        async def fake_sleep(seconds):
            sleeps["n"] += 1
            if sleeps["n"] >= polls:
                raise _StopLoop()

        monkeypatch.setattr(tpe.market_data, "fetch_live_5m", fake_5m)
        monkeypatch.setattr(tpe.market_data, "fetch_live_1h", fake_1h)
        monkeypatch.setattr(tpe.market_data, "fetch_live_4h", fake_4h)
        monkeypatch.setattr(tpe.market_data, "fetch_live_daily", fake_daily)
        monkeypatch.setattr(tpe.market_data, "_calc_daily_atr14", fake_atr)
        monkeypatch.setattr(tpe.asyncio, "sleep", fake_sleep)

        async def main():
            try:
                await tpe.run_trade_plan_loop()
            except _StopLoop:
                pass

        asyncio.run(main())

    def get_plan(symbol="BTC/USDT"):
        db = SessionLocal()
        row = db.query(TradePlan).filter(TradePlan.symbol == symbol).first()
        db.close()
        return row

    def get_orders(trade_plan_id=None):
        db = SessionLocal()
        q = db.query(ExecutorOrder)
        if trade_plan_id is not None:
            q = q.filter_by(trade_plan_id=trade_plan_id)
        rows = q.all()
        db.expunge_all()
        db.close()
        return rows

    def get_audit_rows(trade_plan_id=None):
        db = SessionLocal()
        q = db.query(ExecutorAuditLog)
        if trade_plan_id is not None:
            q = q.filter_by(trade_plan_id=trade_plan_id)
        rows = q.all()
        db.expunge_all()
        db.close()
        return rows

    yield {
        "make_plan": make_plan, "make_account": make_account, "run_polls": run_polls,
        "get_plan": get_plan, "get_orders": get_orders, "get_audit_rows": get_audit_rows,
    }

    db = SessionLocal()
    for model in (TradePlan, ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorGlobalConfig, ExecutorSizingPolicy):
        db.query(model).delete()
    db.commit()
    db.close()
    _clean_db_files()


def test_tradeplan_filled_write_survives_executor_bug(env, monkeypatch):
    # THE core safety property: a broken executor must never block, delay,
    # or roll back the real TradePlan write, which stays the untouchable
    # trading brain.
    monkeypatch.setattr(
        executor_plan_builder, "build_hypothetical_order",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated executor bug")),
    )
    env["make_account"]()
    env["make_plan"]()
    candles = _fueled_5m_candles(100.0, is_long=True)
    env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = env["get_plan"]()
    assert row.status == "FILLED"  # the brain's own write, unaffected by the executor's own crash


def test_dry_run_order_and_audit_row_written_for_active_account(env):
    account_id = env["make_account"]()
    env["make_plan"]()
    candles = _fueled_5m_candles(100.0, is_long=True)
    env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    plan = env["get_plan"]()
    assert plan.status == "FILLED"

    orders = env["get_orders"](trade_plan_id=plan.id)
    assert len(orders) == 1
    order = orders[0]
    assert order.account_id == account_id
    assert order.mode == "DRY_RUN"
    assert order.decision == "WOULD_PLACE"
    assert order.exchange_order_id is None  # Stage 1 -- never a real order

    audit_rows = env["get_audit_rows"](trade_plan_id=plan.id)
    assert any(r.event_type == "ORDER_WOULD_PLACE" for r in audit_rows)


# 2026-09-07 -- the real safety-gate gap found while planning the missing
# LIVE-mode switch: the global "Live Orders" switch was never checked
# anywhere in the real executor path (only executor_mechanism_test.py's
# tiny test respected it). Restored in executor_engine.py's LIVE branch.

def _make_live_account(db_id_getter, **kwargs):
    """Same shape as env['make_account'](), but flips mode to LIVE
    directly -- this test is for executor_engine.py's OWN gating logic,
    independent of (and written before) the separate set_account_mode()
    feature that will be the real UI path for this."""
    account_id = db_id_getter(**kwargs)
    db = SessionLocal()
    account = db.query(ExecutorAccount).filter_by(id=account_id).first()
    account.mode = "LIVE"
    # 2026-09-08: this test is about the Live Orders global switch, not
    # sizing -- explicitly save a real preset so it doesn't trip the
    # separate, orthogonal "sizing was never explicitly confirmed" gate
    # (executor_engine.py, same real incident as executor_accounts.py's
    # set_account_mode() check).
    ea.update_sizing_policy(db, account, {"base_risk_usd": 100.0, "preset_name": "fixed_dollar"}, updated_by="test")
    db.commit()
    db.close()
    return account_id


def test_live_mode_account_skipped_when_live_orders_globally_disabled(env, monkeypatch):
    called = {"n": 0}
    async def _fake_place_entry_order(*a, **kw):
        called["n"] += 1
    import executor_live_engine
    monkeypatch.setattr(executor_live_engine, "place_entry_order", _fake_place_entry_order)

    account_id = _make_live_account(env["make_account"])
    env["make_plan"]()
    candles = _fueled_5m_candles(100.0, is_long=True)
    env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)  # live orders NOT enabled (default)

    assert called["n"] == 0   # never reached -- the whole point of this gate
    plan = env["get_plan"]()
    orders = env["get_orders"](trade_plan_id=plan.id)
    assert len(orders) == 1 and orders[0].mode == "LIVE" and orders[0].decision == "WOULD_PLACE"
    assert orders[0].exchange_order_id is None   # WOULD_PLACE recorded, but nothing real was attempted

    audit_rows = env["get_audit_rows"](trade_plan_id=plan.id)
    assert any("Live Orders switch is OFF" in (r.message or "") for r in audit_rows)


def test_live_mode_account_places_real_order_when_live_orders_enabled(env, monkeypatch):
    called = {"n": 0}
    async def _fake_place_entry_order(*a, **kw):
        called["n"] += 1
    import executor_live_engine
    monkeypatch.setattr(executor_live_engine, "place_entry_order", _fake_place_entry_order)

    account_id = _make_live_account(env["make_account"])
    db = SessionLocal()
    ec.enable_live_orders(db, reason="test", by="test@kabroda.com")
    db.commit()
    db.close()

    env["make_plan"]()
    candles = _fueled_5m_candles(100.0, is_long=True)
    env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    assert called["n"] == 1   # both gates open -- the real placement path is reached


def test_live_mode_account_skipped_when_sizing_was_never_explicitly_saved(env, monkeypatch):
    # 2026-09-08 real incident: Dawson's account was already LIVE with a
    # sizing policy that was never explicitly confirmed (still the
    # untouched default) -- this is the second, ongoing layer that stops
    # ANY further real order on such an account, not just the one-time
    # go-live gate in executor_accounts.py's set_account_mode().
    called = {"n": 0}
    async def _fake_place_entry_order(*a, **kw):
        called["n"] += 1
    import executor_live_engine
    monkeypatch.setattr(executor_live_engine, "place_entry_order", _fake_place_entry_order)

    # Deliberately do NOT save a sizing policy -- account_id created and
    # flipped LIVE directly, exactly reproducing the real incident (a
    # policy that only ever went through get_or_init_sizing_policy()'s
    # lazy auto-seed, never an explicit save).
    account_id = env["make_account"]()
    db = SessionLocal()
    account = db.query(ExecutorAccount).filter_by(id=account_id).first()
    account.mode = "LIVE"
    db.commit()
    ec.enable_live_orders(db, reason="test", by="test@kabroda.com")
    db.commit()
    db.close()

    env["make_plan"]()
    candles = _fueled_5m_candles(100.0, is_long=True)
    env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    assert called["n"] == 0   # real order placement never reached

    db = SessionLocal()
    audit = db.query(ExecutorAuditLog).filter_by(account_id=account_id, event_type="ERROR").all()
    assert any("no sizing choice has ever been explicitly saved" in a.message for a in audit)
    db.close()


def test_inactive_account_produces_no_order(env):
    db = SessionLocal()
    account = ea.create_account(db, user_id=1, label="inactive_account")
    account.is_active = False
    db.flush()
    ea.get_or_init_risk_state(db, account)
    db.commit()
    db.close()

    env["make_plan"]()
    candles = _fueled_5m_candles(100.0, is_long=True)
    env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    plan = env["get_plan"]()
    assert plan.status == "FILLED"
    assert env["get_orders"](trade_plan_id=plan.id) == []  # inactive accounts aren't even queried


def test_two_independent_accounts_each_get_correct_independent_sizing(env):
    # THE direct proof of the multi-account requirement: one gated
    # TradePlan row, two accounts, each with its OWN risk_last_usd,
    # producing independently correct qty = risk / stop_distance.
    account1_id = env["make_account"](label="andy_bitunix_main", risk_last_usd=100.0)
    account2_id = env["make_account"](label="gross_monkey_bitunix", risk_last_usd=250.0)
    env["make_plan"]()  # entry 100, stop 95 -> stop_distance 5
    candles = _fueled_5m_candles(100.0, is_long=True)
    env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    plan = env["get_plan"]()
    orders = {o.account_id: o for o in env["get_orders"](trade_plan_id=plan.id)}
    assert set(orders.keys()) == {account1_id, account2_id}

    assert orders[account1_id].qty == pytest.approx(100.0 / 5.0)   # 20.0
    assert orders[account2_id].qty == pytest.approx(250.0 / 5.0)   # 50.0
    assert orders[account1_id].risk_dollars_used == pytest.approx(100.0)
    assert orders[account2_id].risk_dollars_used == pytest.approx(250.0)
    assert all(o.decision == "WOULD_PLACE" for o in orders.values())


def test_kill_switch_account_produces_no_order_but_no_email_or_loop_failure(env):
    account_id = env["make_account"]()
    db = SessionLocal()
    account = db.query(ExecutorAccount).filter_by(id=account_id).first()
    ea.engage_kill_switch(db, account, reason="testing", by="andy@kabroda.com")
    db.commit()
    db.close()

    env["make_plan"]()
    candles = _fueled_5m_candles(100.0, is_long=True)
    env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    plan = env["get_plan"]()
    assert plan.status == "FILLED"  # loop itself is unaffected
    orders = env["get_orders"](trade_plan_id=plan.id)
    assert len(orders) == 1
    assert orders[0].decision == "SKIPPED_KILL_SWITCH"


def test_maintenance_margin_rate_used_persists_via_the_generic_column_filter(env):
    # Spot-check for the Stage 2 MMR fix (2026-09-05): proves
    # executor_engine.py's _ORDER_COLUMNS generic filter picked up the
    # new ExecutorOrder.maintenance_margin_rate_used column with ZERO
    # code changes in executor_engine.py itself -- it only needed
    # executor_plan_builder.py to add the key to its result dict.
    env["make_account"]()
    env["make_plan"]()
    candles = _fueled_5m_candles(100.0, is_long=True)
    env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    plan = env["get_plan"]()
    orders = env["get_orders"](trade_plan_id=plan.id)
    assert len(orders) == 1
    # No credentials set on this account -- the benign "never connected
    # yet" fallback applies, which is 0.0 (not the elevated conservative
    # constant reserved for a real query failure).
    assert orders[0].maintenance_margin_rate_used == pytest.approx(0.0)
