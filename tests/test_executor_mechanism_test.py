"""
Unit coverage for executor_mechanism_test.py -- the Stage 2 (2026-09-05)
real-money tiny order mechanism test. DB-backed, same fixture style as
tests/test_executor_accounts.py/test_executor_plan_builder.py. Every
BitunixClient method is monkeypatched at the class level -- NO real
network call is ever made here; the actual live chain is exercised
manually against a real account instead.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_executor_mechanism_test.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio

import pytest
from cryptography.fernet import Fernet

import database
from database import (
    SessionLocal, ExecutorAccount, ExecutorOrder, ExecutorAuditLog,
    ExecutorRiskState, ExecutorGlobalConfig, ExecutorMechanismTest, TradePlan,
)
import executor_accounts as ea
import executor_control as ec
import executor_bitunix_client as ebc
import executor_mechanism_test as emt


def _clean_db_files():
    for path in ["kabroda_test_executor_mechanism_test.db", "kabroda_test_executor_mechanism_test.db-journal",
                 "kabroda_test_executor_mechanism_test.db-shm", "kabroda_test_executor_mechanism_test.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    for model in (ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorAccount,
                  ExecutorGlobalConfig, ExecutorMechanismTest, TradePlan):
        session.query(model).delete()
    session.commit()


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setattr(emt, "_FILL_POLL_INTERVAL_SEC", 0)  # speed up polling tests
    _clean_db_files()
    database.init_db()
    session = SessionLocal()
    _clean_rows(session)
    yield session
    _clean_rows(session)
    session.close()
    database.engine.dispose()
    _clean_db_files()


def _make_ready_account(db, label="andy_bitunix_main"):
    """Live orders enabled globally, account has credentials, active,
    not kill-switched -- every gate open."""
    account = ea.create_account(db, user_id=1, label=label)
    db.flush()
    ea.set_credentials(db, account, api_key="fake-key", api_secret="fake-secret", set_by="test@kabroda.com")
    ec.enable_live_orders(db, reason="testing", by="andy@kabroda.com")
    db.commit()
    return account


def _no_position_response():
    return {"code": 0, "data": [], "msg": "Success"}


def _one_long_position_response(position_id="pos1", avg_open_price=100.0):
    return {"code": 0, "data": [{
        "positionId": position_id, "symbol": "BTCUSDT", "side": "LONG",
        "avgOpenPrice": str(avg_open_price), "qty": "0.0001",
    }], "msg": "Success"}


def _trading_pairs_response(min_trade_volume="0.0001", base_precision=4, quote_precision=1):
    return {"code": 0, "data": [{
        "symbol": "BTCUSDT", "minTradeVolume": min_trade_volume,
        "basePrecision": base_precision, "quotePrecision": quote_precision,
    }], "msg": "Success"}


def _async(value):
    """Wraps a fixed return value into an async callable -- BitunixClient
    methods are all awaited, so a fake must be a real coroutine
    function, not a plain lambda."""
    async def _fake(self, *a, **kw):
        return value
    return _fake


def _install(monkeypatch, **fakes):
    """fakes: method_name -> async callable(self, *a, **kw). Any
    BitunixClient method not passed raises AssertionError if called --
    proves a gate blocked BEFORE any exchange call, or that a step
    never reaches a call it shouldn't."""
    for name in ("get_position", "get_trading_pairs", "place_order",
                 "set_position_tpsl", "modify_position_tp_sl_order", "close_position"):
        fake = fakes.get(name)
        if fake is None:
            async def _unexpected(self, *a, __name=name, **kw):
                raise AssertionError(f"BitunixClient.{__name}() should not have been called")
            fake = _unexpected
        monkeypatch.setattr(ebc.BitunixClient, name, fake)


def _get_audit_event_types(db, test_id):
    # These orchestration functions flush() to populate IDs as they go
    # but, matching executor_engine.py's own established convention,
    # leave the final commit() to the caller (main.py's routes always
    # commit after every action) -- so a direct read here must commit
    # first to see the last write_audit() call's row.
    db.commit()
    rows = db.query(ExecutorAuditLog).filter_by(executor_mechanism_test_id=test_id).order_by(ExecutorAuditLog.id).all()
    return [r.event_type for r in rows]


# ------------------------------------------------------------------ gating

def test_gating_blocks_when_live_orders_disabled_before_any_client_call(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    ea.set_credentials(db, account, api_key="fake-key", api_secret="fake-secret", set_by="test@kabroda.com")
    db.commit()
    # live orders NOT enabled

    with pytest.raises(emt.MechanismTestBlocked, match="live orders"):
        asyncio.run(emt.place_confirm_and_set_initial_tpsl(db, account, actor="test@kabroda.com"))
    assert db.query(ExecutorMechanismTest).count() == 0


def test_gating_blocks_when_account_kill_switch_engaged(db):
    account = _make_ready_account(db)
    ea.engage_kill_switch(db, account, reason="testing", by="andy@kabroda.com")
    db.commit()

    with pytest.raises(emt.MechanismTestBlocked, match="kill switch"):
        asyncio.run(emt.place_confirm_and_set_initial_tpsl(db, account, actor="test@kabroda.com"))
    assert db.query(ExecutorMechanismTest).count() == 0


def test_gating_blocks_when_global_kill_switch_engaged(db):
    account = _make_ready_account(db)
    ec.engage_global_kill_switch(db, reason="emergency stop", by="andy@kabroda.com")
    db.commit()

    with pytest.raises(emt.MechanismTestBlocked, match="global"):
        asyncio.run(emt.place_confirm_and_set_initial_tpsl(db, account, actor="test@kabroda.com"))
    assert db.query(ExecutorMechanismTest).count() == 0


def test_gating_blocks_when_no_credentials_set(db):
    account = ea.create_account(db, user_id=1, label="andy_bitunix_main")
    ec.enable_live_orders(db, reason="testing", by="andy@kabroda.com")
    db.commit()

    with pytest.raises(emt.MechanismTestBlocked, match="credentials"):
        asyncio.run(emt.place_confirm_and_set_initial_tpsl(db, account, actor="test@kabroda.com"))
    assert db.query(ExecutorMechanismTest).count() == 0


# ------------------------------------------------------------------ pre-flight collision guard

def test_pre_flight_refuses_if_an_open_long_position_already_exists(db, monkeypatch):
    account = _make_ready_account(db)
    _install(monkeypatch, get_position=_async(_one_long_position_response()))

    with pytest.raises(emt.MechanismTestBlocked, match="already exists"):
        asyncio.run(emt.place_confirm_and_set_initial_tpsl(db, account, actor="test@kabroda.com"))
    assert db.query(ExecutorMechanismTest).count() == 0


# ------------------------------------------------------------------ happy path: place -> confirm -> set tpsl

def test_happy_path_place_confirm_and_set_initial_tpsl(db, monkeypatch):
    account = _make_ready_account(db)

    call_state = {"get_position_calls": 0}

    async def fake_get_position(self, symbol):
        call_state["get_position_calls"] += 1
        if call_state["get_position_calls"] == 1:
            return _no_position_response()  # pre-flight: nothing open yet
        return _one_long_position_response(position_id="pos1", avg_open_price=100.0)  # poll: filled

    async def fake_get_trading_pairs(self, symbol):
        return _trading_pairs_response()

    async def fake_place_order(self, **kwargs):
        assert kwargs["symbol"] == "BTCUSDT"
        # 2x minTradeVolume, not exactly the minimum -- see
        # place_confirm_and_set_initial_tpsl()'s own comment: a 50%
        # partial-close of the true minimum floors to zero.
        assert kwargs["qty"] == "0.0002"
        assert kwargs["side"] == "BUY"
        assert kwargs["trade_side"] == "OPEN"
        assert kwargs["order_type"] == "MARKET"
        return {"code": 0, "data": {"orderId": "order1", "clientId": "client1"}, "msg": "Success"}

    async def fake_set_position_tpsl(self, **kwargs):
        assert kwargs["position_id"] == "pos1"
        return {"code": 0, "data": {"orderId": "tpsl1"}, "msg": "Success"}

    _install(monkeypatch, get_position=fake_get_position, get_trading_pairs=fake_get_trading_pairs,
              place_order=fake_place_order, set_position_tpsl=fake_set_position_tpsl)

    test_row = asyncio.run(emt.place_confirm_and_set_initial_tpsl(db, account, actor="test@kabroda.com"))

    assert test_row.status == "TPSL_SET"
    assert test_row.qty == pytest.approx(0.0002)
    assert test_row.exchange_order_id == "order1"
    assert test_row.position_id == "pos1"
    assert test_row.fill_price == pytest.approx(100.0)
    assert test_row.initial_tp_price == pytest.approx(101.0)   # +1% of 100
    assert test_row.initial_sl_price == pytest.approx(99.0)    # -1% of 100
    assert test_row.tpsl_exchange_order_id == "tpsl1"

    events = _get_audit_event_types(db, test_row.id)
    assert events == [
        "TEST_MECHANISM_STARTED", "TEST_ORDER_PLACED",
        "TEST_ORDER_FILL_CONFIRMED", "TEST_INITIAL_TPSL_SET",
    ]


def test_fill_poll_timeout_marks_failed_without_raising(db, monkeypatch):
    account = _make_ready_account(db)

    async def fake_get_position(self, symbol):
        return _no_position_response()  # never fills

    _install(monkeypatch, get_position=fake_get_position, get_trading_pairs=_async(_trading_pairs_response()),
              place_order=_async({"code": 0, "data": {"orderId": "order1"}, "msg": "Success"}))

    test_row = asyncio.run(emt.place_confirm_and_set_initial_tpsl(db, account, actor="test@kabroda.com"))
    assert test_row.status == "FAILED"
    assert "CHECK THE EXCHANGE" in test_row.error_detail
    events = _get_audit_event_types(db, test_row.id)
    assert events == ["TEST_MECHANISM_STARTED", "TEST_ORDER_PLACED", "TEST_MECHANISM_FAILED"]


def test_exception_mid_sequence_marks_failed_reraises_and_preserves_prior_progress(db, monkeypatch):
    account = _make_ready_account(db)

    call_state = {"get_position_calls": 0}

    async def fake_get_position(self, symbol):
        call_state["get_position_calls"] += 1
        if call_state["get_position_calls"] == 1:
            return _no_position_response()  # pre-flight
        return _one_long_position_response(position_id="pos1", avg_open_price=100.0)  # poll: filled

    async def fake_set_position_tpsl_raises(self, **kwargs):
        raise RuntimeError("simulated exchange error")

    _install(monkeypatch, get_position=fake_get_position,
              get_trading_pairs=_async(_trading_pairs_response()),
              place_order=_async({"code": 0, "data": {"orderId": "order1"}, "msg": "Success"}),
              set_position_tpsl=fake_set_position_tpsl_raises)

    with pytest.raises(RuntimeError, match="simulated exchange error"):
        asyncio.run(emt.place_confirm_and_set_initial_tpsl(db, account, actor="test@kabroda.com"))

    test_row = db.query(ExecutorMechanismTest).filter_by(account_id=account.id).first()
    assert test_row.status == "FAILED"
    assert test_row.error_detail == "simulated exchange error"
    # Prior progress preserved even though the overall action failed:
    assert test_row.exchange_order_id == "order1"
    assert test_row.position_id == "pos1"
    assert test_row.fill_price == pytest.approx(100.0)


def _make_tpsl_set_row(db, account, fill_price=100.0, qty=0.0002, base_precision=4, quote_precision=1):
    row = ExecutorMechanismTest(
        account_id=account.id, symbol="BTCUSDT", direction="LONG", status="TPSL_SET",
        min_trade_volume=qty, base_precision=base_precision, quote_precision=quote_precision,
        qty=qty, exchange_order_id="order1", position_id="pos1", fill_price=fill_price,
        initial_tp_price=fill_price * 1.01, initial_sl_price=fill_price * 0.99,
        tpsl_exchange_order_id="tpsl1",
    )
    db.add(row)
    db.commit()
    return row


# ------------------------------------------------------------------ partial close

def test_partial_close_happy_path_and_qty_math(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account, qty=0.0002)

    async def fake_place_order(self, **kwargs):
        assert kwargs["qty"] == "0.0001"   # 50% of 0.0002, floored to 4dp
        assert kwargs["side"] == "SELL"
        assert kwargs["trade_side"] == "CLOSE"
        assert kwargs["reduce_only"] is True
        assert kwargs["position_id"] == "pos1"
        return {"code": 0, "data": {"orderId": "partial1"}, "msg": "Success"}

    _install(monkeypatch, place_order=fake_place_order)

    result = asyncio.run(emt.partial_close(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "PARTIAL_CLOSED"
    assert result.partial_close_qty == pytest.approx(0.0001)
    assert result.partial_close_pct == pytest.approx(0.50)
    assert result.partial_close_exchange_order_id == "partial1"
    assert _get_audit_event_types(db, test_row.id) == ["TEST_PARTIAL_CLOSED"]


def test_partial_close_refuses_zero_qty_underflow_without_sending_an_order(db, monkeypatch):
    # A 50% close of the exchange's true minimum step (0.0001 at 4dp)
    # floors to exactly 0 -- must refuse, not send a zero-qty order.
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account, qty=0.0001, base_precision=4)

    _install(monkeypatch)  # place_order must never be called

    with pytest.raises(ValueError, match="unrepresentable"):
        asyncio.run(emt.partial_close(db, account, test_row, actor="test@kabroda.com"))

    assert test_row.status == "FAILED"
    assert _get_audit_event_types(db, test_row.id) == ["TEST_MECHANISM_FAILED"]


def test_partial_close_rejects_wrong_prior_status(db):
    account = _make_ready_account(db)
    test_row = ExecutorMechanismTest(account_id=account.id, symbol="BTCUSDT", direction="LONG", status="STARTED")
    db.add(test_row)
    db.commit()

    with pytest.raises(emt.MechanismTestInvalidState, match="TPSL_SET"):
        asyncio.run(emt.partial_close(db, account, test_row, actor="test@kabroda.com"))


def test_partial_close_blocked_by_kill_switch_even_with_correct_prior_status(db):
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account)
    ea.engage_kill_switch(db, account, reason="testing", by="andy@kabroda.com")
    db.commit()

    with pytest.raises(emt.MechanismTestBlocked, match="kill switch"):
        asyncio.run(emt.partial_close(db, account, test_row, actor="test@kabroda.com"))


# ------------------------------------------------------------------ move SL to breakeven

def _make_partial_closed_row(db, account, fill_price=100.0):
    row = _make_tpsl_set_row(db, account, fill_price=fill_price)
    row.status = "PARTIAL_CLOSED"
    row.partial_close_pct = 0.50
    row.partial_close_qty = 0.00005
    row.partial_close_exchange_order_id = "partial1"
    db.commit()
    return row


def test_move_sl_to_breakeven_happy_path_sets_price_to_exact_fill_price(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_partial_closed_row(db, account, fill_price=100.0)

    async def fake_modify(self, **kwargs):
        assert kwargs["position_id"] == "pos1"
        assert kwargs["sl_price"] == "100.0"
        return {"code": 0, "data": {"orderId": "breakeven1"}, "msg": "Success"}

    _install(monkeypatch, modify_position_tp_sl_order=fake_modify)

    result = asyncio.run(emt.move_sl_to_breakeven(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "SL_MOVED_BREAKEVEN"
    assert result.breakeven_sl_price == pytest.approx(100.0)
    assert result.sl_breakeven_exchange_order_id == "breakeven1"
    assert _get_audit_event_types(db, test_row.id) == ["TEST_SL_MOVED_TO_BREAKEVEN"]


def test_move_sl_to_breakeven_rejects_wrong_prior_status(db):
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account)   # TPSL_SET, not PARTIAL_CLOSED

    with pytest.raises(emt.MechanismTestInvalidState, match="PARTIAL_CLOSED"):
        asyncio.run(emt.move_sl_to_breakeven(db, account, test_row, actor="test@kabroda.com"))


# ------------------------------------------------------------------ flash close remainder

def test_flash_close_happy_path_from_partial_closed(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_partial_closed_row(db, account)

    async def fake_close(self, position_id):
        assert position_id == "pos1"
        return {"code": 0, "data": {"positionId": "pos1"}, "msg": "Success"}

    _install(monkeypatch, close_position=fake_close)

    result = asyncio.run(emt.flash_close_remainder(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FULLY_CLOSED"
    assert _get_audit_event_types(db, test_row.id) == ["TEST_POSITION_FLASH_CLOSED"]


def test_flash_close_happy_path_from_sl_moved_breakeven(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_partial_closed_row(db, account)
    test_row.status = "SL_MOVED_BREAKEVEN"
    test_row.breakeven_sl_price = 100.0
    db.commit()

    _install(monkeypatch, close_position=_async({"code": 0, "data": {"positionId": "pos1"}, "msg": "Success"}))

    result = asyncio.run(emt.flash_close_remainder(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FULLY_CLOSED"


def test_flash_close_rejects_before_partial_close(db):
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account)   # TPSL_SET, not yet partial-closed

    with pytest.raises(emt.MechanismTestInvalidState, match="PARTIAL_CLOSED or SL_MOVED_BREAKEVEN"):
        asyncio.run(emt.flash_close_remainder(db, account, test_row, actor="test@kabroda.com"))
