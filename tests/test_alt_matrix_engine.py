"""Coverage for alt_matrix_engine.py -- the orchestration layer that ties
D1 (alt_matrix_signals), D3 (alt_matrix_management), the concurrency
check (alt_matrix_portfolio), and real order placement (alt_matrix_
executor) together. evaluate_d1()'s own math is already covered by
tests/test_alt_matrix_signals.py (22 tests) -- these tests monkeypatch
it directly where convenient, so engine-level tests exercise THIS
module's own branching (which account gets skipped and why, exits-
before-entries ordering, idempotent restart-safety) without re-fighting
EMA math to engineer a real crossing candle series."""
import asyncio
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./kabroda_test_alt_matrix_engine.db")

import pytest
from cryptography.fernet import Fernet

import database
from database import (
    SessionLocal, ExecutorAccount, AltMatrixPlan, AltMatrixOrder, AltMatrixConfig,
    AltMatrixTransition, ExecutorSizingPolicy,
)
import executor_accounts as ea
import executor_control as ec
import executor_bitunix_client as ebc
import alt_matrix_clock as amc
import alt_matrix_engine as ame
import alt_matrix_market as amm
import alt_matrix_portfolio as amp
import alt_matrix_signals as ams

UTC_EPOCH = datetime.datetime(1970, 1, 1)


def _epoch(dt: datetime.datetime) -> int:
    return int((dt - UTC_EPOCH).total_seconds())


def _clean_db_files():
    for path in ["kabroda_test_alt_matrix_engine.db", "kabroda_test_alt_matrix_engine.db-journal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    for model in (AltMatrixTransition, AltMatrixOrder, AltMatrixPlan, AltMatrixConfig, ExecutorSizingPolicy, ExecutorAccount):
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


def _account(db, label="engine_test", mode="DRY_RUN", active=True, kill_switch=False):
    account = ea.create_account(db, user_id=1, label=label)
    db.flush()
    account.mode = mode
    account.is_active = active
    account.kill_switch_engaged = kill_switch
    if mode == "LIVE":
        ea.set_credentials(db, account, api_key="k", api_secret="s", set_by="test@kabroda.com")
        ec.enable_live_orders(db, reason="testing", by="andy@kabroda.com")
    db.commit()
    return account


def _config(db, account, sol=True, eth=True):
    cfg = AltMatrixConfig(account_id=account.id, sol_enabled=sol, eth_enabled=eth)
    db.add(cfg)
    db.commit()
    return cfg


def _plan(db, symbol="SOL/USDT", status="ARMED", **overrides):
    defaults = dict(symbol=symbol, signal_bar_time=datetime.datetime(2026, 10, 9, 4, 0, 0),
                     date_key="2026-10-09", atr14=2.0, status=status)
    defaults.update(overrides)
    plan = AltMatrixPlan(**defaults)
    db.add(plan)
    db.flush()
    return plan


# advance() computes entry_fill_epoch via entry_fill_time.timestamp() --
# .timestamp() on a NAIVE datetime is LOCAL-tz-interpreting (same pre-
# existing convention mgmt_e1_stack.py's own advance() already uses, see
# alt_matrix_engine.py's own header discussion). This test machine is
# NOT UTC (confirmed: Central Time), so a fixed naive wall-clock literal
# would NOT round-trip back to the same epoch its own candles use (built
# via the UTC-unambiguous _epoch() helper) -- fromtimestamp() (the
# inverse of .timestamp()) makes ENTRY_FILL_TIME round-trip correctly on
# ANY machine's local TZ, not just one that happens to be UTC.
_ENTRY_FILL_EPOCH = _epoch(datetime.datetime(2026, 10, 9, 4, 0, 0))
ENTRY_FILL_TIME = datetime.datetime.fromtimestamp(_ENTRY_FILL_EPOCH)


def _mgmt_order(db, account, plan, management_state="FILLED", **overrides):
    defaults = dict(
        alt_matrix_plan_id=plan.id, account_id=account.id, mode=account.mode,
        symbol=plan.symbol, direction="LONG", qty=10.0, r_distance=2.0,
        entry_fill_price=100.0, entry_fill_time=ENTRY_FILL_TIME,
        sl_price_initial=98.0, sl_price_current=98.0, be_amended=False,
        management_state=management_state,
    )
    defaults.update(overrides)
    order = AltMatrixOrder(**defaults)
    db.add(order)
    db.flush()
    return order


def _run(coro):
    return asyncio.run(coro)


def _bar(close, high=None, low=None, offset=1, base_epoch=None):
    high = high if high is not None else close
    low = low if low is not None else close
    base = _ENTRY_FILL_EPOCH if base_epoch is None else base_epoch
    return {"time": base + offset * amc.BAR_SECONDS, "close": close, "high": high, "low": low}


# ------------------------------------------------------------------ _enabled_accounts_for_symbol / _any_credentialed_account

def test_enabled_accounts_filters_by_symbol_flag_and_sorts_by_id(db):
    a1 = _account(db, "a1")
    a2 = _account(db, "a2")
    a3 = _account(db, "a3")
    _config(db, a2, sol=True, eth=False)
    _config(db, a3, sol=False, eth=True)
    _config(db, a1, sol=True, eth=True)

    sol_accounts = ame._enabled_accounts_for_symbol(db, "SOL/USDT")
    eth_accounts = ame._enabled_accounts_for_symbol(db, "ETH/USDT")
    assert [a.id for a in sol_accounts] == sorted([a1.id, a2.id])
    assert [a.id for a in eth_accounts] == sorted([a1.id, a3.id])


def test_any_credentialed_account_skips_accounts_without_credentials(db):
    a1 = _account(db, "nocreds")
    _config(db, a1)
    assert ame._any_credentialed_account(db) is None

    a2 = _account(db, "withcreds", mode="LIVE")
    _config(db, a2)
    found = ame._any_credentialed_account(db)
    assert found is not None and found.id == a2.id


# ------------------------------------------------------------------ _close_any_plan_fully_resolved

def test_plan_marked_done_only_when_no_open_or_pending_orders_remain(db):
    # Two orders on the SAME plan must belong to two DIFFERENT accounts --
    # AltMatrixOrder's own unique constraint is (alt_matrix_plan_id, account_id).
    account1 = _account(db, "acct1")
    account2 = _account(db, "acct2")
    plan = _plan(db)
    o1 = _mgmt_order(db, account1, plan, management_state="CLOSED_STOP")
    o2 = _mgmt_order(db, account2, plan, management_state="FILLED")
    ame._close_any_plan_fully_resolved(db, plan.id)
    assert plan.status == "ARMED"   # o2 still open

    o2.management_state = "CLOSED_EMA55_CLOSE"
    db.flush()
    ame._close_any_plan_fully_resolved(db, plan.id)
    assert plan.status == "DONE"


def test_plan_not_downgraded_from_a_non_armed_status(db):
    account = _account(db)
    plan = _plan(db, status="SKIPPED_MACRO")
    ame._close_any_plan_fully_resolved(db, plan.id)
    assert plan.status == "SKIPPED_MACRO"   # never overwritten by the DONE convenience marker


# ------------------------------------------------------------------ _advance_one_order_management -- DRY_RUN simulation

def test_dry_run_amend_to_be_simulated_directly_no_exchange_call(db):
    account = _account(db, mode="DRY_RUN")
    plan = _plan(db)
    order = _mgmt_order(db, account, plan, management_state="FILLED", entry_fill_price=100.0, sl_price_current=90.0, r_distance=10.0)
    candles = [_bar(125.0, high=130.0, low=120.0, offset=1)]   # MFE (130-100)/10=3.0R -- fires AMEND_TO_BE

    _run(ame._advance_one_order_management(db, account, order, candles, datetime.datetime.utcnow()))
    db.flush()   # SessionLocal is autoflush=False -- a plain query after db.add() won't see it otherwise

    assert order.be_amended is True
    assert order.management_state == "TRAILING"
    assert order.sl_price_current == pytest.approx(101.0)   # entry + 0.1R
    transitions = db.query(AltMatrixTransition).filter_by(alt_matrix_order_id=order.id).all()
    assert len(transitions) == 1 and transitions[0].to_state == "TRAILING"


def test_dry_run_exit_simulated_directly_and_marks_plan_done(db):
    account = _account(db, mode="DRY_RUN")
    plan = _plan(db)
    order = _mgmt_order(db, account, plan, management_state="FILLED", entry_fill_price=100.0, sl_price_current=90.0, r_distance=10.0)
    candles = [_bar(85.0, high=92.0, low=88.0, offset=1)]   # low 88 <= stop 90 -- STOP exit

    _run(ame._advance_one_order_management(db, account, order, candles, datetime.datetime.utcnow()))

    assert order.management_state == "CLOSED_STOP"
    assert order.exit_price == 90.0
    assert order.realized_pnl_r == pytest.approx((90.0 - 100.0) / 10.0)
    assert plan.status == "DONE"


def test_live_amend_delegates_to_executor_not_simulated(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _mgmt_order(db, account, plan, management_state="FILLED", entry_fill_price=100.0, sl_price_current=90.0, r_distance=10.0)
    candles = [_bar(125.0, high=130.0, low=120.0, offset=1)]

    called = {}
    async def _fake_amend(db_, account_, order_, be_price):
        called["be_price"] = be_price
        return True
    monkeypatch.setattr(ame.alt_matrix_executor, "amend_to_breakeven", _fake_amend)

    _run(ame._advance_one_order_management(db, account, order, candles, datetime.datetime.utcnow()))
    assert called["be_price"] == pytest.approx(101.0)
    # order_row itself is untouched by the engine in the LIVE path --
    # amend_to_breakeven() owns writing be_amended/management_state for real.
    assert order.be_amended is False


def test_live_exit_delegates_to_executor_not_simulated(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _mgmt_order(db, account, plan, management_state="FILLED", entry_fill_price=100.0, sl_price_current=90.0, r_distance=10.0)
    candles = [_bar(85.0, high=92.0, low=88.0, offset=1)]

    called = {}
    async def _fake_close(db_, account_, order_, exit_reason):
        called["exit_reason"] = exit_reason
    monkeypatch.setattr(ame.alt_matrix_executor, "market_close", _fake_close)

    _run(ame._advance_one_order_management(db, account, order, candles, datetime.datetime.utcnow()))
    assert called["exit_reason"] == "STOP"
    assert order.management_state == "FILLED"   # untouched -- market_close() owns finalizing for real


def test_advance_management_returns_none_action_leaves_order_untouched(db):
    account = _account(db)
    plan = _plan(db)
    order = _mgmt_order(db, account, plan, management_state="FILLED", entry_fill_price=100.0, sl_price_current=90.0, r_distance=10.0)
    candles = [_bar(100.0, high=101.0, low=99.0, offset=1)]   # flat -- no action

    _run(ame._advance_one_order_management(db, account, order, candles, datetime.datetime.utcnow()))
    assert order.management_state == "FILLED"
    assert db.query(AltMatrixTransition).count() == 0


def test_mfe_is_persisted_every_tick_even_with_no_action(db):
    account = _account(db)
    plan = _plan(db)
    order = _mgmt_order(db, account, plan, management_state="FILLED", entry_fill_price=100.0, sl_price_current=90.0, r_distance=10.0)
    candles = [_bar(108.0, high=112.0, low=106.0, offset=1)]   # MFE (112-100)/10=1.2R -- below any action threshold

    _run(ame._advance_one_order_management(db, account, order, candles, datetime.datetime.utcnow()))

    assert order.management_state == "FILLED"   # no action fired
    assert order.mfe_r == pytest.approx(1.2)
    assert order.mfe_price == pytest.approx(112.0)
    assert order.mfe_updated_at is not None


# ------------------------------------------------------------------ _try_enter_for_account

def _common_entry_mocks(monkeypatch, admitted=True, sizing_ok=True):
    async def _fake_exch(account):
        return {"equity": 10000.0, "available": 8000.0, "margin": 2000.0, "open_count": 0, "committed_risk": 0.0, "unprotected": False}
    monkeypatch.setattr(amp, "exchange_account_state", _fake_exch)

    async def _fake_check_admission(db, account, symbol, risk, margin):
        if admitted:
            return {"admitted": True, "reason": None, "snapshot": {"symbol": symbol}}
        return {"admitted": False, "reason": "max_open_positions_reached", "snapshot": {"symbol": symbol}}
    monkeypatch.setattr(ame.alt_matrix_portfolio, "check_admission", _fake_check_admission)

    async def _fake_size_entry(db, account, symbol, equity, entry_price, stop_price):
        if sizing_ok:
            return {"decision": "WOULD_PLACE", "decision_reason": None, "qty": 5.0, "risk_dollars_used": 100.0,
                    "leverage": 5, "margin_required_usd": 500.0, "liquidation_price_estimate": 50.0,
                    "liquidation_check_passed": True, "liquidation_check_detail": "ok"}
        return {"decision": "REJECTED", "decision_reason": "leverage too high", "qty": None, "risk_dollars_used": None,
                "leverage": None, "margin_required_usd": None, "liquidation_price_estimate": None,
                "liquidation_check_passed": False, "liquidation_check_detail": "leverage too high"}
    monkeypatch.setattr(ame.alt_matrix_executor, "size_entry", _fake_size_entry)


def test_try_enter_skips_kill_switch_engaged_account(db, monkeypatch):
    account = _account(db, kill_switch=True)
    plan = _plan(db)
    _common_entry_mocks(monkeypatch)
    decision = _run(ame._try_enter_for_account(db, account, plan, "SOL/USDT", 100.0, 97.0, 2.0))
    assert decision == "SKIPPED_KILL_SWITCH"
    order = db.query(AltMatrixOrder).filter_by(account_id=account.id, alt_matrix_plan_id=plan.id).first()
    assert order.management_state == "NOT_ENTERED"


def test_try_enter_skips_inactive_account(db, monkeypatch):
    account = _account(db, active=False)
    plan = _plan(db)
    _common_entry_mocks(monkeypatch)
    decision = _run(ame._try_enter_for_account(db, account, plan, "SOL/USDT", 100.0, 97.0, 2.0))
    assert decision == "SKIPPED_ACCOUNT_INACTIVE"


def test_try_enter_skips_when_account_already_has_an_open_order_for_symbol(db, monkeypatch):
    account = _account(db)
    # The EXISTING open order belongs to an EARLIER plan -- a brand new
    # plan is what _try_enter_for_account is being asked to act on now
    # (an order already existing for THIS SAME plan+account is a
    # different, unrelated invariant enforced by the DB's own unique
    # constraint, not what this test is checking).
    older_plan = _plan(db, signal_bar_time=datetime.datetime(2026, 10, 8, 20, 0, 0))
    _mgmt_order(db, account, older_plan, management_state="FILLED", symbol="SOL/USDT")
    new_plan = _plan(db)
    _common_entry_mocks(monkeypatch)
    decision = _run(ame._try_enter_for_account(db, account, new_plan, "SOL/USDT", 100.0, 97.0, 2.0))
    assert decision == "SKIPPED_IN_TRADE"


def test_try_enter_rejects_on_exchange_query_failure(db, monkeypatch):
    account = _account(db)
    plan = _plan(db)
    async def _fail(account_):
        raise ConnectionError("blip")
    monkeypatch.setattr(amp, "exchange_account_state", _fail)
    decision = _run(ame._try_enter_for_account(db, account, plan, "SOL/USDT", 100.0, 97.0, 2.0))
    assert decision == "REJECTED"


def test_try_enter_rejects_on_sizing_failure(db, monkeypatch):
    account = _account(db)
    plan = _plan(db)
    _common_entry_mocks(monkeypatch, sizing_ok=False)
    decision = _run(ame._try_enter_for_account(db, account, plan, "SOL/USDT", 100.0, 97.0, 2.0))
    assert decision == "REJECTED"


def test_try_enter_concurrency_skipped_when_admission_refuses(db, monkeypatch):
    account = _account(db)
    plan = _plan(db)
    _common_entry_mocks(monkeypatch, admitted=False)
    decision = _run(ame._try_enter_for_account(db, account, plan, "SOL/USDT", 100.0, 97.0, 2.0))
    assert decision == "CONCURRENCY_SKIPPED"


def test_try_enter_dry_run_happy_path_simulates_fill(db, monkeypatch):
    account = _account(db, mode="DRY_RUN")
    plan = _plan(db)
    _common_entry_mocks(monkeypatch)
    decision = _run(ame._try_enter_for_account(db, account, plan, "SOL/USDT", 100.0, 97.0, 2.0))
    assert decision == "WOULD_PLACE"
    order = db.query(AltMatrixOrder).filter_by(account_id=account.id, alt_matrix_plan_id=plan.id).first()
    assert order.management_state == "FILLED"
    assert order.entry_fill_price == 100.0
    assert order.qty == 5.0


def test_try_enter_live_happy_path_delegates_to_executor(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    _common_entry_mocks(monkeypatch)
    called = {}
    async def _fake_place(db_, account_, order_row):
        called["order_id"] = order_row.id
    monkeypatch.setattr(ame.alt_matrix_executor, "place_entry_and_protect", _fake_place)
    decision = _run(ame._try_enter_for_account(db, account, plan, "SOL/USDT", 100.0, 97.0, 2.0))
    assert decision == "WOULD_PLACE"
    assert "order_id" in called


# ------------------------------------------------------------------ _process_symbol_signal -- orchestration-level

LAST_BAR_OPEN = datetime.datetime(2026, 10, 9, 0, 0, 0)
EVAL_INSTANT = datetime.datetime(2026, 10, 9, 4, 0, 5)
SIGNAL_BAR_TIME = datetime.datetime(2026, 10, 9, 4, 0, 0)


def _confirmed_candles(n, last_open_dt, close=100.0):
    last_epoch = _epoch(last_open_dt)
    return [{"time": last_epoch - (n - 1 - i) * amc.BAR_SECONDS, "open": close, "high": close + 1, "low": close - 1, "close": close}
            for i in range(n)]


def _patch_market(monkeypatch, candles_4h, candles_1d, funding=0.0):
    async def _fake_4h(symbol, target_bars=400):
        return candles_4h
    async def _fake_daily(symbol, target_bars=300):
        return candles_1d
    async def _fake_funding(symbol, account):
        return funding
    monkeypatch.setattr(amm, "fetch_confirmed_4h", _fake_4h)
    monkeypatch.setattr(amm, "fetch_confirmed_daily", _fake_daily)
    monkeypatch.setattr(amm, "fetch_funding_rate", _fake_funding)


def test_signal_skips_on_stale_fetch(db, monkeypatch):
    stale_candles = _confirmed_candles(60, LAST_BAR_OPEN - datetime.timedelta(hours=4))   # one boundary behind
    daily = _confirmed_candles(210, LAST_BAR_OPEN)
    _patch_market(monkeypatch, stale_candles, daily)
    _run(ame._process_symbol_signal(db, "SOL/USDT", EVAL_INSTANT))
    assert db.query(AltMatrixPlan).count() == 0


def test_signal_no_cross_creates_no_plan_row(db, monkeypatch):
    candles_4h = _confirmed_candles(60, LAST_BAR_OPEN)
    daily = _confirmed_candles(210, LAST_BAR_OPEN)
    _patch_market(monkeypatch, candles_4h, daily)
    monkeypatch.setattr(ams, "evaluate_d1", lambda *a, **kw: {"signal": False, "reason": "no_cross_this_bar", "cross_pass": False})
    _run(ame._process_symbol_signal(db, "SOL/USDT", EVAL_INSTANT))
    assert db.query(AltMatrixPlan).count() == 0


def test_signal_cross_but_macro_fails_creates_skipped_macro_row_no_orders(db, monkeypatch):
    candles_4h = _confirmed_candles(60, LAST_BAR_OPEN)
    daily = _confirmed_candles(210, LAST_BAR_OPEN)
    _patch_market(monkeypatch, candles_4h, daily)
    verdict = {"signal": False, "reason": "SKIPPED_MACRO", "cross_pass": True, "macro_pass": False,
               "funding_pass": True, "atr14": 2.0, "daily_close": 90.0, "sma200": 100.0,
               "ema21": 1.0, "ema21_prev": 0.9, "ema55": 1.0, "ema55_prev": 1.1, "funding_rate": 0.0}
    monkeypatch.setattr(ams, "evaluate_d1", lambda *a, **kw: verdict)

    account = _account(db)
    _config(db, account)

    _run(ame._process_symbol_signal(db, "SOL/USDT", EVAL_INSTANT))

    plan = db.query(AltMatrixPlan).filter_by(symbol="SOL/USDT", signal_bar_time=SIGNAL_BAR_TIME).first()
    assert plan is not None
    assert plan.status == "SKIPPED_MACRO"
    assert db.query(AltMatrixOrder).count() == 0   # no entry fan-out for a non-ARMED plan


def test_signal_armed_fans_out_to_enabled_accounts_and_enters(db, monkeypatch):
    candles_4h = _confirmed_candles(60, LAST_BAR_OPEN, close=100.0)
    daily = _confirmed_candles(210, LAST_BAR_OPEN)
    _patch_market(monkeypatch, candles_4h, daily)
    verdict = {"signal": True, "reason": None, "cross_pass": True, "macro_pass": True, "funding_pass": True,
               "atr14": 2.0, "daily_close": 110.0, "sma200": 100.0,
               "ema21": 1.0, "ema21_prev": 0.9, "ema55": 1.0, "ema55_prev": 1.1, "funding_rate": 0.0}
    monkeypatch.setattr(ams, "evaluate_d1", lambda *a, **kw: verdict)
    _common_entry_mocks(monkeypatch)

    account = _account(db, mode="DRY_RUN")
    _config(db, account, sol=True, eth=True)

    _run(ame._process_symbol_signal(db, "SOL/USDT", EVAL_INSTANT))

    plan = db.query(AltMatrixPlan).filter_by(symbol="SOL/USDT", signal_bar_time=SIGNAL_BAR_TIME).first()
    assert plan is not None and plan.status == "ARMED"
    order = db.query(AltMatrixOrder).filter_by(alt_matrix_plan_id=plan.id, account_id=account.id).first()
    assert order is not None and order.decision == "WOULD_PLACE" and order.management_state == "FILLED"
    assert order.sl_price_initial == pytest.approx(100.0 - ame._STOP_ATR_MULTIPLE * 2.0)


def test_signal_is_idempotent_on_restart_replay(db, monkeypatch):
    candles_4h = _confirmed_candles(60, LAST_BAR_OPEN, close=100.0)
    daily = _confirmed_candles(210, LAST_BAR_OPEN)
    _patch_market(monkeypatch, candles_4h, daily)
    verdict = {"signal": True, "reason": None, "cross_pass": True, "macro_pass": True, "funding_pass": True,
               "atr14": 2.0, "daily_close": 110.0, "sma200": 100.0,
               "ema21": 1.0, "ema21_prev": 0.9, "ema55": 1.0, "ema55_prev": 1.1, "funding_rate": 0.0}
    monkeypatch.setattr(ams, "evaluate_d1", lambda *a, **kw: verdict)
    _common_entry_mocks(monkeypatch)
    account = _account(db, mode="DRY_RUN")
    _config(db, account)

    _run(ame._process_symbol_signal(db, "SOL/USDT", EVAL_INSTANT))
    first_count = db.query(AltMatrixPlan).count()
    _run(ame._process_symbol_signal(db, "SOL/USDT", EVAL_INSTANT))   # simulate a restart re-run of the SAME boundary
    second_count = db.query(AltMatrixPlan).count()
    assert first_count == 1
    assert second_count == 1   # no duplicate row -- idempotent


def test_exits_run_before_entries_in_the_same_tick(db, monkeypatch):
    # The approved plan's own requirement: a position that exits THIS
    # tick must free its concurrency slot in time for a new entry the
    # SAME tick to use it.
    candles_4h = _confirmed_candles(60, LAST_BAR_OPEN, close=100.0)
    # Make the LAST bar's low breach the stop of an existing open order (stop=90).
    candles_4h[-1]["low"] = 85.0
    daily = _confirmed_candles(210, LAST_BAR_OPEN)
    _patch_market(monkeypatch, candles_4h, daily)
    verdict = {"signal": True, "reason": None, "cross_pass": True, "macro_pass": True, "funding_pass": True,
               "atr14": 2.0, "daily_close": 110.0, "sma200": 100.0,
               "ema21": 1.0, "ema21_prev": 0.9, "ema55": 1.0, "ema55_prev": 1.1, "funding_rate": 0.0}
    monkeypatch.setattr(ams, "evaluate_d1", lambda *a, **kw: verdict)

    account = _account(db, mode="DRY_RUN")
    _config(db, account)

    # Pre-existing FILLED order for this exact (account, symbol) -- would
    # normally make _try_enter_for_account refuse via SKIPPED_IN_TRADE...
    existing_plan = _plan(db, symbol="SOL/USDT", signal_bar_time=datetime.datetime(2026, 10, 8, 20, 0, 0))
    # entry_fill_time must be strictly BEFORE the bar under evaluation's
    # own open (LAST_BAR_OPEN) for advance()'s "since entry" filter to
    # include it at all -- entered on the prior close, same round-trip-
    # safe construction as ENTRY_FILL_TIME above.
    prior_entry_time = datetime.datetime.fromtimestamp(_epoch(LAST_BAR_OPEN - datetime.timedelta(seconds=amc.BAR_SECONDS)))
    existing_order = _mgmt_order(db, account, existing_plan, management_state="FILLED", symbol="SOL/USDT",
                                  entry_fill_price=100.0, sl_price_current=90.0, r_distance=10.0,
                                  entry_fill_time=prior_entry_time)

    async def _fake_exch(account_):
        return {"equity": 10000.0, "available": 8000.0, "margin": 2000.0, "open_count": 0, "committed_risk": 0.0, "unprotected": False}
    monkeypatch.setattr(amp, "exchange_account_state", _fake_exch)
    async def _fake_check_admission(db_, account_, symbol, risk, margin):
        return {"admitted": True, "reason": None, "snapshot": {}}
    monkeypatch.setattr(ame.alt_matrix_portfolio, "check_admission", _fake_check_admission)
    async def _fake_size_entry(db_, account_, symbol, equity, entry_price, stop_price):
        return {"decision": "WOULD_PLACE", "decision_reason": None, "qty": 5.0, "risk_dollars_used": 100.0,
                "leverage": 5, "margin_required_usd": 500.0, "liquidation_price_estimate": 50.0,
                "liquidation_check_passed": True, "liquidation_check_detail": "ok"}
    monkeypatch.setattr(ame.alt_matrix_executor, "size_entry", _fake_size_entry)

    _run(ame._process_symbol_signal(db, "SOL/USDT", EVAL_INSTANT))

    # ...but because the D3 step ran FIRST this same tick, the stop touch
    # on the last bar's low (85 <= 90) closes the old order BEFORE the new
    # entry attempt checks "already open" -- so the new entry must succeed.
    assert existing_order.management_state == "CLOSED_STOP"
    new_plan = db.query(AltMatrixPlan).filter_by(symbol="SOL/USDT", signal_bar_time=SIGNAL_BAR_TIME).first()
    new_order = db.query(AltMatrixOrder).filter_by(alt_matrix_plan_id=new_plan.id, account_id=account.id).first()
    assert new_order is not None and new_order.decision == "WOULD_PLACE"


# ------------------------------------------------------------------ notify wiring (DRY_RUN paths)

def _capture_emails(monkeypatch):
    import notify
    admin_calls, account_calls = [], []
    monkeypatch.setattr(notify, "send_admin_email", lambda subject, body: admin_calls.append({"subject": subject, "body": body}) or True)
    monkeypatch.setattr(notify, "send_account_email", lambda subject, body, account_id: account_calls.append({"subject": subject, "body": body, "account_id": account_id}) or True)
    return admin_calls, account_calls


def test_dry_run_entry_sends_account_email(db, monkeypatch):
    account = _account(db, mode="DRY_RUN")
    plan = _plan(db)
    admin_calls, account_calls = _capture_emails(monkeypatch)
    _common_entry_mocks(monkeypatch)

    decision = _run(ame._try_enter_for_account(db, account, plan, "SOL/USDT", 100.0, 97.0, 2.0))

    assert decision == "WOULD_PLACE"
    assert len(account_calls) == 1
    assert account_calls[0]["account_id"] == account.id
    assert "Position Opened" in account_calls[0]["subject"]


def test_dry_run_breakeven_sends_account_email(db, monkeypatch):
    account = _account(db, mode="DRY_RUN")
    plan = _plan(db)
    order = _mgmt_order(db, account, plan, management_state="FILLED", entry_fill_price=100.0, sl_price_current=90.0, r_distance=10.0)
    candles = [_bar(125.0, high=130.0, low=120.0, offset=1)]
    _, account_calls = _capture_emails(monkeypatch)

    _run(ame._advance_one_order_management(db, account, order, candles, datetime.datetime.utcnow()))

    assert len(account_calls) == 1
    assert "Breakeven" in account_calls[0]["subject"]


def test_dry_run_exit_sends_account_email_no_approximation_note(db, monkeypatch):
    account = _account(db, mode="DRY_RUN")
    plan = _plan(db)
    order = _mgmt_order(db, account, plan, management_state="FILLED", entry_fill_price=100.0, sl_price_current=90.0, r_distance=10.0)
    candles = [_bar(85.0, high=92.0, low=88.0, offset=1)]
    _, account_calls = _capture_emails(monkeypatch)

    _run(ame._advance_one_order_management(db, account, order, candles, datetime.datetime.utcnow()))

    assert len(account_calls) == 1
    assert "Simulated close" in account_calls[0]["body"]
    assert "Note:" not in account_calls[0]["body"]   # DRY_RUN never approximates


def test_armed_plan_sends_admin_signal_email(db, monkeypatch):
    candles_4h = _confirmed_candles(60, LAST_BAR_OPEN, close=100.0)
    daily = _confirmed_candles(210, LAST_BAR_OPEN)
    _patch_market(monkeypatch, candles_4h, daily)
    verdict = {"signal": True, "reason": None, "cross_pass": True, "macro_pass": True, "funding_pass": True,
               "atr14": 2.0, "daily_close": 110.0, "sma200": 100.0,
               "ema21": 1.0, "ema21_prev": 0.9, "ema55": 1.0, "ema55_prev": 1.1, "funding_rate": 0.0}
    monkeypatch.setattr(ams, "evaluate_d1", lambda *a, **kw: verdict)
    admin_calls, _ = _capture_emails(monkeypatch)

    _run(ame._process_symbol_signal(db, "SOL/USDT", EVAL_INSTANT))

    assert len(admin_calls) == 1
    assert "Silver Cross Confirmed" in admin_calls[0]["subject"]


def test_skipped_macro_plan_sends_admin_signal_email_not_account_email(db, monkeypatch):
    candles_4h = _confirmed_candles(60, LAST_BAR_OPEN)
    daily = _confirmed_candles(210, LAST_BAR_OPEN)
    _patch_market(monkeypatch, candles_4h, daily)
    verdict = {"signal": False, "reason": "SKIPPED_MACRO", "cross_pass": True, "macro_pass": False,
               "funding_pass": True, "atr14": 2.0, "daily_close": 90.0, "sma200": 100.0,
               "ema21": 1.0, "ema21_prev": 0.9, "ema55": 1.0, "ema55_prev": 1.1, "funding_rate": 0.0}
    monkeypatch.setattr(ams, "evaluate_d1", lambda *a, **kw: verdict)
    admin_calls, account_calls = _capture_emails(monkeypatch)

    _run(ame._process_symbol_signal(db, "SOL/USDT", EVAL_INSTANT))

    assert len(admin_calls) == 1
    assert "Cross Filtered" in admin_calls[0]["subject"]
    assert len(account_calls) == 0   # no order was ever attempted for a non-ARMED plan
