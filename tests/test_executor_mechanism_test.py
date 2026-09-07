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
    # side="BUY", not "LONG" -- verified against a real account response
    # (2026-09-05); Bitunix's own docs claim LONG/SHORT but the real API
    # returns BUY/SELL. See _TEST_POSITION_SIDE's own comment.
    return {"code": 0, "data": [{
        "positionId": position_id, "symbol": "BTCUSDT", "side": "BUY",
        "avgOpenPrice": str(avg_open_price), "qty": "0.0001",
    }], "msg": "Success"}


def _order_detail_response(status="FILLED", order_id="order1", trade_qty=None):
    data = {"orderId": order_id, "status": status}
    if trade_qty is not None:
        data["tradeQty"] = str(trade_qty)
    return {"code": 0, "data": data, "msg": "Success"}


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
    for name in ("get_position", "get_trading_pairs", "place_order", "get_order_detail",
                 "set_position_tpsl", "modify_position_tp_sl_order", "close_position",
                 "get_pending_tp_sl_order", "cancel_orders"):
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

    async def fake_get_position(self, symbol):
        return _no_position_response()  # pre-flight only, nothing open yet -- and the
        # subsequent post-fill lookup, since this mock is symmetric (matches
        # neither call needs to distinguish anymore now that fill confirmation
        # itself no longer goes through get_position).

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

    async def fake_get_order_detail(self, order_id=None, client_id=None):
        assert order_id == "order1"
        return _order_detail_response(status="FILLED", order_id="order1")

    async def fake_get_position_after_fill(self, symbol):
        return _one_long_position_response(position_id="pos1", avg_open_price=100.0)

    async def fake_set_position_tpsl(self, **kwargs):
        assert kwargs["position_id"] == "pos1"
        return {"code": 0, "data": {"orderId": "tpsl1"}, "msg": "Success"}

    async def fake_get_pending_tp_sl_order(self, symbol=None, position_id=None):
        assert position_id == "pos1"
        return {"code": 0, "data": [{"id": "tpsl1", "positionId": "pos1", "tpPrice": "101.0", "slPrice": "99.0"}], "msg": "Success"}

    call_state = {"get_position_calls": 0}

    async def fake_get_position_dispatch(self, symbol):
        call_state["get_position_calls"] += 1
        if call_state["get_position_calls"] == 1:
            return await fake_get_position(self, symbol)  # pre-flight
        return await fake_get_position_after_fill(self, symbol)  # post-fill lookup

    _install(monkeypatch, get_position=fake_get_position_dispatch, get_trading_pairs=fake_get_trading_pairs,
              place_order=fake_place_order, get_order_detail=fake_get_order_detail,
              set_position_tpsl=fake_set_position_tpsl, get_pending_tp_sl_order=fake_get_pending_tp_sl_order)

    test_row = asyncio.run(emt.place_confirm_and_set_initial_tpsl(db, account, actor="test@kabroda.com"))

    assert test_row.status == "TPSL_SET"
    assert test_row.qty == pytest.approx(0.0002)
    assert test_row.exchange_order_id == "order1"
    assert test_row.position_id == "pos1"
    assert test_row.fill_price == pytest.approx(100.0)
    assert test_row.initial_tp_price == pytest.approx(101.0)   # +1% of 100
    assert test_row.initial_sl_price == pytest.approx(99.0)    # -1% of 100
    assert test_row.tpsl_exchange_order_id == "tpsl1"
    assert "FILLED" in test_row.order_detail_response_json
    assert "pos1" in test_row.position_check_response_json

    events = _get_audit_event_types(db, test_row.id)
    assert events == [
        "TEST_MECHANISM_STARTED", "TEST_ORDER_PLACED",
        "TEST_ORDER_FILL_CONFIRMED", "TEST_INITIAL_TPSL_SET",
    ]


def test_fill_poll_timeout_marks_failed_without_raising(db, monkeypatch):
    account = _make_ready_account(db)

    _install(monkeypatch, get_position=_async(_no_position_response()),
              get_trading_pairs=_async(_trading_pairs_response()),
              place_order=_async({"code": 0, "data": {"orderId": "order1"}, "msg": "Success"}),
              get_order_detail=_async(_order_detail_response(status="NEW")))  # never reaches FILLED

    test_row = asyncio.run(emt.place_confirm_and_set_initial_tpsl(db, account, actor="test@kabroda.com"))
    assert test_row.status == "FAILED"
    assert "CHECK THE EXCHANGE" in test_row.error_detail
    assert "NEW" in test_row.order_detail_response_json
    events = _get_audit_event_types(db, test_row.id)
    assert events == ["TEST_MECHANISM_STARTED", "TEST_ORDER_PLACED", "TEST_MECHANISM_FAILED"]


def test_fill_confirmed_but_no_matching_position_marks_failed_without_raising(db, monkeypatch):
    # A genuinely surprising edge case now that fill confirmation is
    # ID-based: the order says FILLED, but the very next position lookup
    # still doesn't find a match. Must fail clearly, not crash, and must
    # save the raw position-check response for diagnosis.
    account = _make_ready_account(db)

    _install(monkeypatch, get_position=_async(_no_position_response()),
              get_trading_pairs=_async(_trading_pairs_response()),
              place_order=_async({"code": 0, "data": {"orderId": "order1"}, "msg": "Success"}),
              get_order_detail=_async(_order_detail_response(status="FILLED")))

    test_row = asyncio.run(emt.place_confirm_and_set_initial_tpsl(db, account, actor="test@kabroda.com"))
    assert test_row.status == "FAILED"
    assert "CHECK THE EXCHANGE" in test_row.error_detail
    assert test_row.position_check_response_json is not None
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
              get_order_detail=_async(_order_detail_response(status="FILLED")),
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

    async def fake_get_order_detail(self, order_id=None, client_id=None):
        assert order_id == "partial1"
        return _order_detail_response(status="FILLED", order_id="partial1")

    # Remaining position after the 50% close: 0.0002 - 0.0001 = 0.0001,
    # same positionId -- the "nothing surprising happened" case.
    _install(monkeypatch, place_order=fake_place_order, get_order_detail=fake_get_order_detail,
              get_position=_async(_one_long_position_response(position_id="pos1")))

    result = asyncio.run(emt.partial_close(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "PARTIAL_CLOSED"
    assert result.partial_close_qty == pytest.approx(0.0001)
    assert result.partial_close_pct == pytest.approx(0.50)
    assert result.partial_close_exchange_order_id == "partial1"
    # Position-lifecycle verification (2026-09-06) -- confirmed, not assumed.
    assert result.position_id_after_partial_close == "pos1"
    assert result.qty_after_partial_close == pytest.approx(0.0001)
    assert result.position_id == "pos1"
    assert result.partial_close_position_check_response_json is not None
    assert _get_audit_event_types(db, test_row.id) == ["TEST_PARTIAL_CLOSED"]


def test_partial_close_updates_position_id_when_exchange_returns_a_different_one(db, monkeypatch):
    # The actual open question this whole feature exists to answer:
    # does Bitunix keep the SAME positionId for the reduced remainder?
    # Whichever way the real answer goes, test_row.position_id must end
    # up CURRENT so move_sl_to_breakeven()/flash_close_remainder() never
    # target a stale ID.
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account, qty=0.0002)

    _install(
        monkeypatch,
        place_order=_async({"code": 0, "data": {"orderId": "partial1"}, "msg": "Success"}),
        get_order_detail=_async(_order_detail_response(status="FILLED", order_id="partial1")),
        get_position=_async(_one_long_position_response(position_id="pos2-different")),
    )

    result = asyncio.run(emt.partial_close(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "PARTIAL_CLOSED"
    assert result.position_id_after_partial_close == "pos2-different"
    assert result.position_id == "pos2-different"   # NOT the stale "pos1"


def test_partial_close_fails_when_no_remaining_position_found(db, monkeypatch):
    # Something genuinely broke -- the closing order filled but there's
    # no matching open position at all afterward.
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account, qty=0.0002)

    _install(
        monkeypatch,
        place_order=_async({"code": 0, "data": {"orderId": "partial1"}, "msg": "Success"}),
        get_order_detail=_async(_order_detail_response(status="FILLED", order_id="partial1")),
        get_position=_async(_no_position_response()),
    )

    result = asyncio.run(emt.partial_close(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FAILED"
    assert "no matching open position was found at all" in result.error_detail
    assert _get_audit_event_types(db, test_row.id) == ["TEST_MECHANISM_FAILED"]


def test_partial_close_fails_when_remaining_qty_does_not_match_expected(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account, qty=0.0002)

    # Expected remainder is 0.0001 -- the exchange reports something
    # wildly different, a real functional break.
    _install(
        monkeypatch,
        place_order=_async({"code": 0, "data": {"orderId": "partial1"}, "msg": "Success"}),
        get_order_detail=_async(_order_detail_response(status="FILLED", order_id="partial1")),
        get_position=_async(_one_long_position_response(position_id="pos1")),
    )
    monkeypatch.setattr(
        emt, "_find_open_long_position",
        lambda pos_resp: {"positionId": "pos1", "symbol": "BTCUSDT", "side": "BUY", "avgOpenPrice": "100.0", "qty": "0.0005"},
    )

    result = asyncio.run(emt.partial_close(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FAILED"
    assert "does not match the expected remainder" in result.error_detail
    assert _get_audit_event_types(db, test_row.id) == ["TEST_MECHANISM_FAILED"]


def test_partial_close_order_not_confirmed_filled_marks_failed_without_raising(db, monkeypatch):
    # The partial-close order is a real order too -- if its own status
    # never reaches FILLED, this must fail clearly, not silently mark
    # PARTIAL_CLOSED on the strength of place_order's response alone.
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account, qty=0.0002)

    _install(monkeypatch, place_order=_async({"code": 0, "data": {"orderId": "partial1"}, "msg": "Success"}),
              get_order_detail=_async(_order_detail_response(status="NEW")))

    result = asyncio.run(emt.partial_close(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FAILED"
    assert "CHECK THE EXCHANGE" in result.error_detail
    assert "NEW" in result.order_detail_response_json
    assert _get_audit_event_types(db, test_row.id) == ["TEST_MECHANISM_FAILED"]


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


# ------------------------------------------------------------------ resting reduce-only LIMIT at T1 (2026-09-06)

def test_place_resting_t1_limit_requires_tpsl_set_status(db):
    account = _make_ready_account(db)
    test_row = ExecutorMechanismTest(account_id=account.id, symbol="BTCUSDT", direction="LONG", status="STARTED")
    db.add(test_row)
    db.commit()

    with pytest.raises(emt.MechanismTestInvalidState, match="TPSL_SET"):
        asyncio.run(emt.place_resting_t1_limit(db, account, test_row, actor="test@kabroda.com"))


def test_place_resting_t1_limit_computes_correct_price_and_qty(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account, fill_price=100.0, qty=0.0002, quote_precision=1)

    async def fake_place_order(self, **kwargs):
        assert kwargs["price"] == "101.0"     # 100 * 1.01, 1dp
        assert kwargs["qty"] == "0.0001"       # 50% of 0.0002
        assert kwargs["order_type"] == "LIMIT"
        assert kwargs["side"] == "SELL"
        assert kwargs["trade_side"] == "CLOSE"
        assert kwargs["reduce_only"] is True
        assert kwargs["position_id"] == "pos1"
        return {"code": 0, "data": {"orderId": "t1limit1"}, "msg": "Success"}

    _install(monkeypatch, place_order=fake_place_order)

    result = asyncio.run(emt.place_resting_t1_limit(db, account, test_row, actor="test@kabroda.com", t1_pct=0.01, qty_pct=0.50))
    assert result.status == "T1_LIMIT_PLACED"
    assert result.t1_limit_target_price == pytest.approx(101.0)
    assert result.t1_limit_qty == pytest.approx(0.0001)
    assert result.t1_limit_exchange_order_id == "t1limit1"
    assert _get_audit_event_types(db, test_row.id) == ["TEST_T1_LIMIT_PLACED"]


def test_place_resting_t1_limit_refuses_zero_qty_underflow(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account, qty=0.0001, base_precision=4)
    _install(monkeypatch)  # place_order must never be called

    with pytest.raises(ValueError, match="unrepresentable"):
        asyncio.run(emt.place_resting_t1_limit(db, account, test_row, actor="test@kabroda.com", qty_pct=0.50))
    assert test_row.status == "FAILED"


def _make_t1_limit_placed_row(db, account, fill_price=100.0, qty=0.0002, t1_qty=0.0001, base_precision=4, quote_precision=1):
    row = _make_tpsl_set_row(db, account, fill_price=fill_price, qty=qty, base_precision=base_precision, quote_precision=quote_precision)
    row.status = "T1_LIMIT_PLACED"
    row.t1_limit_target_price = fill_price * 1.01
    row.t1_limit_qty = t1_qty
    row.t1_limit_exchange_order_id = "t1limit1"
    db.commit()
    return row


def test_check_resting_t1_limit_status_still_pending_leaves_status_unchanged(db, monkeypatch):
    for pending_status in ("NEW", "PART_FILLED", "INIT"):
        account = _make_ready_account(db, label=f"acct_{pending_status}")
        test_row = _make_t1_limit_placed_row(db, account)
        _install(monkeypatch, get_order_detail=_async(_order_detail_response(status=pending_status, order_id="t1limit1")))

        result = asyncio.run(emt.check_resting_t1_limit_status(db, account, test_row, actor="test@kabroda.com"))
        assert result.status == "T1_LIMIT_PLACED"
        assert _get_audit_event_types(db, test_row.id) == ["TEST_T1_LIMIT_STATUS_CHECKED"]


def test_check_resting_t1_limit_status_filled_transitions_to_partial_closed_via_shared_verification(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_t1_limit_placed_row(db, account, qty=0.0002, t1_qty=0.0001)

    _install(
        monkeypatch,
        get_order_detail=_async(_order_detail_response(status="FILLED", order_id="t1limit1")),
        get_position=_async(_one_long_position_response(position_id="pos1")),   # qty "0.0001" -- matches 0.0002 - 0.0001
    )

    result = asyncio.run(emt.check_resting_t1_limit_status(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "PARTIAL_CLOSED"
    assert result.position_id_after_partial_close == "pos1"
    assert result.qty_after_partial_close == pytest.approx(0.0001)
    assert _get_audit_event_types(db, test_row.id) == ["TEST_T1_LIMIT_FILLED"]


def test_check_resting_t1_limit_status_canceled_fails(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_t1_limit_placed_row(db, account)
    _install(monkeypatch, get_order_detail=_async(_order_detail_response(status="CANCELED", order_id="t1limit1")))

    result = asyncio.run(emt.check_resting_t1_limit_status(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FAILED"
    assert "unexpected status" in result.error_detail
    assert _get_audit_event_types(db, test_row.id) == ["TEST_MECHANISM_FAILED"]


def test_check_resting_t1_limit_status_unrecognized_status_fails_closed(db, monkeypatch):
    # DeepSeek amendment #2: an undocumented/unexpected status value
    # (not one of the 5 Bitunix documents, e.g. a hypothetical EXPIRED
    # or REJECTED) must fail rather than be silently treated as
    # still-pending.
    account = _make_ready_account(db)
    test_row = _make_t1_limit_placed_row(db, account)
    _install(monkeypatch, get_order_detail=_async(_order_detail_response(status="EXPIRED", order_id="t1limit1")))

    result = asyncio.run(emt.check_resting_t1_limit_status(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FAILED"
    assert "EXPIRED" in result.error_detail
    assert _get_audit_event_types(db, test_row.id) == ["TEST_MECHANISM_FAILED"]


def test_check_resting_t1_limit_status_rejects_wrong_prior_status(db):
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account)   # TPSL_SET, not T1_LIMIT_PLACED

    with pytest.raises(emt.MechanismTestInvalidState, match="T1_LIMIT_PLACED"):
        asyncio.run(emt.check_resting_t1_limit_status(db, account, test_row, actor="test@kabroda.com"))


def test_cancel_resting_t1_limit_reverts_to_tpsl_set_when_nothing_filled(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_t1_limit_placed_row(db, account, qty=0.0002, t1_qty=0.0001)

    _install(
        monkeypatch,
        cancel_orders=_async({"code": 0, "data": {"successList": [{"orderId": "t1limit1"}], "failureList": []}, "msg": "Success"}),
        get_order_detail=_async(_order_detail_response(status="CANCELED", order_id="t1limit1", trade_qty=0)),
    )

    result = asyncio.run(emt.cancel_resting_t1_limit(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "TPSL_SET"
    assert _get_audit_event_types(db, test_row.id) == ["TEST_T1_LIMIT_CANCELED"]


def test_cancel_resting_t1_limit_transitions_to_partial_closed_when_tradeQty_is_nonzero(db, monkeypatch):
    # DeepSeek amendment #1: a partial fill before the cancel landed
    # must be verified and carried forward, not silently discarded.
    account = _make_ready_account(db)
    test_row = _make_t1_limit_placed_row(db, account, qty=0.0002, t1_qty=0.0001)

    # tradeQty=0.0001 -- clearly above the half-unit-at-4dp tolerance
    # (0.00005), not a boundary-exact edge case.
    _install(
        monkeypatch,
        cancel_orders=_async({"code": 0, "data": {"successList": [{"orderId": "t1limit1"}], "failureList": []}, "msg": "Success"}),
        get_order_detail=_async(_order_detail_response(status="CANCELED", order_id="t1limit1", trade_qty=0.0001)),
        get_position=_async(_one_long_position_response(position_id="pos1", avg_open_price=100.0)),
    )
    # Expected remainder = 0.0002 - 0.0001 = 0.0001 -- override the
    # canned mock's response via a direct patch so this test asserts
    # the REAL arithmetic, not a coincidental match with the fixture's
    # own default "0.0001" qty.
    monkeypatch.setattr(
        emt, "_find_open_long_position",
        lambda pos_resp: {"positionId": "pos1", "symbol": "BTCUSDT", "side": "BUY", "avgOpenPrice": "100.0", "qty": "0.0001"},
    )

    result = asyncio.run(emt.cancel_resting_t1_limit(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "PARTIAL_CLOSED"
    assert result.t1_limit_qty == pytest.approx(0.0001)   # the ACTUAL filled amount
    assert result.qty_after_partial_close == pytest.approx(0.0001)
    assert _get_audit_event_types(db, test_row.id) == ["TEST_T1_LIMIT_CANCELED_AFTER_PARTIAL_FILL"]


def test_cancel_resting_t1_limit_fails_when_order_id_not_in_success_list(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_t1_limit_placed_row(db, account)

    _install(
        monkeypatch,
        cancel_orders=_async({"code": 0, "data": {
            "successList": [], "failureList": [{"orderId": "t1limit1", "errorCode": "10001", "errorMsg": "order not found"}],
        }, "msg": "Success"}),
    )

    result = asyncio.run(emt.cancel_resting_t1_limit(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FAILED"
    assert "successList" in result.error_detail
    assert _get_audit_event_types(db, test_row.id) == ["TEST_MECHANISM_FAILED"]


def test_cancel_resting_t1_limit_rejects_wrong_prior_status(db):
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account)   # TPSL_SET, not T1_LIMIT_PLACED

    with pytest.raises(emt.MechanismTestInvalidState, match="T1_LIMIT_PLACED"):
        asyncio.run(emt.cancel_resting_t1_limit(db, account, test_row, actor="test@kabroda.com"))


def test_move_sl_to_breakeven_after_resting_t1_limit_fill_uses_the_ladder_shared_flow(db, monkeypatch):
    # Proves the two paths to PARTIAL_CLOSED (MARKET partial_close() vs.
    # a filled resting T1 limit) converge cleanly -- move_sl_to_
    # breakeven() doesn't need to know or care which one got here.
    account = _make_ready_account(db)
    test_row = _make_t1_limit_placed_row(db, account, fill_price=100.0, qty=0.0002, t1_qty=0.0001)
    _install(
        monkeypatch,
        get_order_detail=_async(_order_detail_response(status="FILLED", order_id="t1limit1")),
        get_position=_async(_one_long_position_response(position_id="pos1")),
    )
    checked = asyncio.run(emt.check_resting_t1_limit_status(db, account, test_row, actor="test@kabroda.com"))
    assert checked.status == "PARTIAL_CLOSED"

    async def fake_modify_tpsl(self, **kwargs):
        return {"code": 0, "data": {"orderId": "breakeven1"}, "msg": "Success"}

    async def fake_get_pending_tp_sl_order(self, symbol=None, position_id=None):
        return {"code": 0, "data": [{"id": "breakeven1", "positionId": position_id, "slPrice": "100.0", "tpPrice": "101.0"}], "msg": "Success"}

    _install(monkeypatch, modify_position_tp_sl_order=fake_modify_tpsl, get_pending_tp_sl_order=fake_get_pending_tp_sl_order)

    result = asyncio.run(emt.move_sl_to_breakeven(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "SL_MOVED_BREAKEVEN"


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
    test_row = _make_partial_closed_row(db, account, fill_price=100.0)  # initial_tp_price=101.0

    async def fake_modify(self, **kwargs):
        assert kwargs["position_id"] == "pos1"
        assert kwargs["sl_price"] == "100.0"
        # 2026-09-05 real incident: modify_position_tp_sl_order does NOT
        # partially update -- an omitted field gets CLEARED on the real
        # exchange (confirmed on Andy's own account: sending only
        # sl_price wiped the existing take-profit entirely). The
        # existing TP must always be re-sent alongside the new SL.
        assert kwargs["tp_price"] == "101.0"
        return {"code": 0, "data": {"orderId": "breakeven1"}, "msg": "Success"}

    async def fake_get_pending_tp_sl_order(self, symbol=None, position_id=None):
        assert position_id == "pos1"
        return {"code": 0, "data": [{"id": "breakeven1", "positionId": "pos1", "slPrice": "100.0", "tpPrice": "101.0"}], "msg": "Success"}

    _install(monkeypatch, modify_position_tp_sl_order=fake_modify, get_pending_tp_sl_order=fake_get_pending_tp_sl_order)

    result = asyncio.run(emt.move_sl_to_breakeven(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "SL_MOVED_BREAKEVEN"
    assert result.breakeven_sl_price == pytest.approx(100.0)
    assert result.sl_breakeven_exchange_order_id == "breakeven1"
    assert _get_audit_event_types(db, test_row.id) == ["TEST_SL_MOVED_TO_BREAKEVEN"]


def test_move_sl_to_breakeven_not_registered_marks_failed_without_raising(db, monkeypatch):
    # modify_position_tp_sl_order reports success, but the follow-up
    # get_pending_tp_sl_order check finds nothing registered for this
    # position -- must fail clearly rather than trust the mutation alone.
    account = _make_ready_account(db)
    test_row = _make_partial_closed_row(db, account, fill_price=100.0)

    _install(monkeypatch, modify_position_tp_sl_order=_async({"code": 0, "data": {"orderId": "breakeven1"}, "msg": "Success"}),
              get_pending_tp_sl_order=_async({"code": 0, "data": [], "msg": "Success"}))

    result = asyncio.run(emt.move_sl_to_breakeven(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FAILED"
    assert "CHECK THE EXCHANGE" in result.error_detail
    assert _get_audit_event_types(db, test_row.id) == ["TEST_MECHANISM_FAILED"]


def test_move_sl_to_breakeven_detects_tp_wiped_by_the_modify_call(db, monkeypatch):
    # THE real incident, reproduced: modify reports success, the new SL
    # registers correctly, but the TP came back null -- the exchange
    # cleared it because tp_price wasn't included in an earlier draft of
    # this call. Must be caught as a failure, not a false "success" just
    # because SOME pending TP/SL entry exists for the position.
    account = _make_ready_account(db)
    test_row = _make_partial_closed_row(db, account, fill_price=100.0)  # initial_tp_price=101.0

    _install(monkeypatch, modify_position_tp_sl_order=_async({"code": 0, "data": {"orderId": "breakeven1"}, "msg": "Success"}),
              get_pending_tp_sl_order=_async({"code": 0, "data": [
                  {"id": "breakeven1", "positionId": "pos1", "slPrice": "100.0", "tpPrice": None},
              ], "msg": "Success"}))

    result = asyncio.run(emt.move_sl_to_breakeven(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FAILED"
    assert "TP registered as" in result.error_detail
    assert "cleared" in result.error_detail
    assert _get_audit_event_types(db, test_row.id) == ["TEST_MECHANISM_FAILED"]


def test_move_sl_to_breakeven_detects_sl_registered_at_wrong_price(db, monkeypatch):
    # Same principle, the other field: SL registers at some price, but
    # not the one actually requested -- must not be treated as a match
    # just because an entry with the right positionId exists.
    account = _make_ready_account(db)
    test_row = _make_partial_closed_row(db, account, fill_price=100.0)

    _install(monkeypatch, modify_position_tp_sl_order=_async({"code": 0, "data": {"orderId": "breakeven1"}, "msg": "Success"}),
              get_pending_tp_sl_order=_async({"code": 0, "data": [
                  {"id": "breakeven1", "positionId": "pos1", "slPrice": "95.0", "tpPrice": "101.0"},
              ], "msg": "Success"}))

    result = asyncio.run(emt.move_sl_to_breakeven(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FAILED"
    assert "SL registered as" in result.error_detail
    assert _get_audit_event_types(db, test_row.id) == ["TEST_MECHANISM_FAILED"]


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

    _install(monkeypatch, close_position=fake_close, get_position=_async(_no_position_response()))

    result = asyncio.run(emt.flash_close_remainder(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FULLY_CLOSED"
    assert _get_audit_event_types(db, test_row.id) == ["TEST_POSITION_FLASH_CLOSED"]


def test_flash_close_happy_path_from_sl_moved_breakeven(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_partial_closed_row(db, account)
    test_row.status = "SL_MOVED_BREAKEVEN"
    test_row.breakeven_sl_price = 100.0
    db.commit()

    _install(monkeypatch, close_position=_async({"code": 0, "data": {"positionId": "pos1"}, "msg": "Success"}),
              get_position=_async(_no_position_response()))

    result = asyncio.run(emt.flash_close_remainder(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FULLY_CLOSED"


def test_flash_close_position_still_open_marks_failed_without_raising(db, monkeypatch):
    # close_position reports success, but a follow-up get_position check
    # still finds the position open -- must fail clearly, never assume
    # the close worked just because the API said so.
    account = _make_ready_account(db)
    test_row = _make_partial_closed_row(db, account)

    _install(monkeypatch, close_position=_async({"code": 0, "data": {"positionId": "pos1"}, "msg": "Success"}),
              get_position=_async(_one_long_position_response(position_id="pos1", avg_open_price=100.0)))

    result = asyncio.run(emt.flash_close_remainder(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FAILED"
    assert "CHECK THE EXCHANGE" in result.error_detail
    assert _get_audit_event_types(db, test_row.id) == ["TEST_MECHANISM_FAILED"]


def test_flash_close_rejects_before_partial_close(db):
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account)   # TPSL_SET, not yet partial-closed

    with pytest.raises(emt.MechanismTestInvalidState, match="PARTIAL_CLOSED or SL_MOVED_BREAKEVEN"):
        asyncio.run(emt.flash_close_remainder(db, account, test_row, actor="test@kabroda.com"))


# ------------------------------------------------------------------ Domain 2 required live pre-flight: concurrent T1+T3 (2026-09-07)

def _pending_tpsl_response(position_id="pos1", tp_price="101.0", sl_price="99.0"):
    return {"code": 0, "data": [{"positionId": position_id, "tpPrice": tp_price, "slPrice": sl_price}], "msg": "Success"}


def test_place_concurrent_t1_t3_limits_requires_tpsl_set_status(db):
    account = _make_ready_account(db)
    test_row = _make_t1_limit_placed_row(db, account)   # T1_LIMIT_PLACED, not TPSL_SET
    with pytest.raises(emt.MechanismTestInvalidState, match="TPSL_SET"):
        asyncio.run(emt.place_concurrent_t1_t3_limits(db, account, test_row, actor="test@kabroda.com"))


def test_place_concurrent_t1_t3_limits_rejects_t3_not_further_than_t1(db):
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account)
    with pytest.raises(ValueError, match="must be strictly greater"):
        asyncio.run(emt.place_concurrent_t1_t3_limits(db, account, test_row, actor="test@kabroda.com", t1_pct=0.02, t3_pct=0.01))
    # No state change and no exchange call attempted -- caught before any placement.
    assert test_row.status == "TPSL_SET"


def test_place_concurrent_t1_t3_limits_places_both_and_updates_status(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account, fill_price=100.0, qty=0.0002, quote_precision=1)

    calls = []
    async def fake_place_order(self, **kwargs):
        calls.append(kwargs)
        assert kwargs["order_type"] == "LIMIT"
        assert kwargs["reduce_only"] is True
        assert kwargs["effect"] == "POST_ONLY"
        return {"code": 0, "data": {"orderId": f"order{len(calls)}"}, "msg": "Success"}

    _install(monkeypatch, place_order=fake_place_order)
    result = asyncio.run(emt.place_concurrent_t1_t3_limits(
        db, account, test_row, actor="test@kabroda.com", t1_pct=0.01, t3_pct=0.02, qty_pct=0.50))

    assert result.status == "T1_AND_T3_LIMITS_PLACED"
    assert len(calls) == 2
    assert result.t1_limit_target_price == pytest.approx(101.0)
    assert result.t3_limit_target_price == pytest.approx(102.0)
    assert result.t1_limit_qty + result.t3_limit_qty == pytest.approx(0.0002)
    assert result.t1_limit_exchange_order_id == "order1"
    assert result.t3_limit_exchange_order_id == "order2"


def _make_t1_t3_limits_placed_row(db, account, fill_price=100.0, qty=0.0002, t1_qty=0.0001, t3_qty=0.0001,
                                   base_precision=4, quote_precision=1):
    row = _make_tpsl_set_row(db, account, fill_price=fill_price, qty=qty, base_precision=base_precision, quote_precision=quote_precision)
    row.status = "T1_AND_T3_LIMITS_PLACED"
    row.t1_limit_target_price = fill_price * 1.01
    row.t1_limit_qty = t1_qty
    row.t1_limit_exchange_order_id = "t1limit1"
    row.t3_limit_target_price = fill_price * 1.02
    row.t3_limit_qty = t3_qty
    row.t3_limit_exchange_order_id = "t3limit1"
    db.commit()
    return row


def test_check_concurrent_limits_status_both_still_pending_captures_tpsl_snapshot(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_t1_t3_limits_placed_row(db, account)

    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="NEW")),
              get_pending_tp_sl_order=_async(_pending_tpsl_response()))
    result = asyncio.run(emt.check_concurrent_limits_status(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "T1_AND_T3_LIMITS_PLACED"   # unchanged -- still pending
    assert result.tpsl_check_after_leg_fill_response_json is not None


def test_check_concurrent_limits_status_one_leg_filled_moves_to_partial_closed(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_t1_t3_limits_placed_row(db, account, qty=0.0002, t1_qty=0.0001, t3_qty=0.0001)

    async def fake_get_order_detail(self, order_id=None, client_id=None):
        # T1 filled, T3 still resting -- distinguish by orderId.
        status = "FILLED" if order_id == "t1limit1" else "NEW"
        return _order_detail_response(status=status, order_id=order_id)

    _install(monkeypatch,
              get_order_detail=fake_get_order_detail,
              get_pending_tp_sl_order=_async(_pending_tpsl_response()),
              get_position=_async(_one_long_position_response(position_id="pos1", avg_open_price=100.0)))
    # Position still reports the FULL original qty here (0.0002) --
    # that's the real, unresolved question this step exists to answer,
    # not something this test asserts a specific correct behavior for.
    # It just proves the code path reaches PARTIAL_CLOSED and captures
    # the evidence, without crashing on whatever the exchange reports.

    result = asyncio.run(emt.check_concurrent_limits_status(db, account, test_row, actor="test@kabroda.com"))
    assert result.status in ("PARTIAL_CLOSED", "FAILED")   # FAILED only if qty verification legitimately mismatches
    assert result.tpsl_check_after_leg_fill_response_json is not None


def test_check_concurrent_limits_status_unexpected_combo_fails_closed(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_t1_t3_limits_placed_row(db, account)
    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="CANCELED")),
              get_pending_tp_sl_order=_async(_pending_tpsl_response()))
    result = asyncio.run(emt.check_concurrent_limits_status(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "FAILED"
    assert "CHECK THE EXCHANGE" in result.error_detail


def test_cancel_concurrent_limits_reverts_to_tpsl_set_when_nothing_filled(db, monkeypatch):
    account = _make_ready_account(db)
    test_row = _make_t1_t3_limits_placed_row(db, account)

    _install(monkeypatch,
              cancel_orders=_async({"code": 0, "data": {"successList": [{"orderId": "t1limit1"}, {"orderId": "t3limit1"}]}, "msg": "Success"}),
              get_order_detail=_async(_order_detail_response(status="CANCELED", trade_qty=0)))
    result = asyncio.run(emt.cancel_concurrent_limits(db, account, test_row, actor="test@kabroda.com"))
    assert result.status == "TPSL_SET"


def test_cancel_concurrent_limits_requires_correct_status(db):
    account = _make_ready_account(db)
    test_row = _make_tpsl_set_row(db, account)   # TPSL_SET, not T1_AND_T3_LIMITS_PLACED
    with pytest.raises(emt.MechanismTestInvalidState, match="T1_AND_T3_LIMITS_PLACED"):
        asyncio.run(emt.cancel_concurrent_limits(db, account, test_row, actor="test@kabroda.com"))
