"""
Unit coverage for executor_live_e1_engine.py -- P3 (CC_INTERFACE.md HARD
PRE-LIVE BLOCKER, Kabroda AI Brain repo, 2026-09-20). Same fixture/mocking
style as tests/test_executor_live_engine.py: every BitunixClient method is
monkeypatched at the class level, NO real network call is ever made here.

check_c5_or_bbwp() itself is unit-tested against real data in
tests/test_mgmt_e1_stack.py -- these tests monkeypatch it directly to
control the condition deterministically, focusing on THIS module's own
dispatch/race/cancel logic, not re-proving the RSI/BBWP math.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_executor_live_e1_engine.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import datetime

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount, ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorSizingPolicy, ExecutorGlobalConfig, TravelerPlan
import executor_accounts as ea
import executor_control as ec
import executor_bitunix_client as ebc
import executor_live_e1_engine as e1e
import mgmt_e1_stack


def _clean_db_files():
    for path in ["kabroda_test_executor_live_e1_engine.db", "kabroda_test_executor_live_e1_engine.db-journal",
                 "kabroda_test_executor_live_e1_engine.db-shm", "kabroda_test_executor_live_e1_engine.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    for model in (ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorSizingPolicy, ExecutorAccount, ExecutorGlobalConfig, TravelerPlan):
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


def _ready_account(db, label="traveler_live_test"):
    account = ea.create_account(db, user_id=1, label=label)
    db.flush()
    account.mode = "LIVE"
    ea.set_credentials(db, account, api_key="fake-key", api_secret="fake-secret", set_by="test@kabroda.com")
    ec.enable_live_orders(db, reason="testing", by="andy@kabroda.com")
    db.commit()
    return account


_FAR_FUTURE = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650)
_FAR_PAST = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)


def _traveler_plan(db, direction="LONG", status="WAITING_TOUCH", journey_cap_at=None):
    plan = TravelerPlan(
        symbol="BTC/USDT", date_key="2026-09-20", session_id="us_ny_futures", status=status,
        direction=direction, breakout_trigger=100.0, breakdown_trigger=90.0,
        stop_price=90.0, t1_price=106.18,
        journey_cap_at=journey_cap_at if journey_cap_at is not None else _FAR_FUTURE,
    )
    db.add(plan)
    db.flush()
    return plan


def _order_row(db, account, traveler_plan, direction="LONG", management_state="PENDING_ENTRY", **overrides):
    fields = dict(
        trade_plan_id=traveler_plan.id, traveler_plan_id=traveler_plan.id,
        account_id=account.id, mode="LIVE",
        symbol="BTC/USDT", direction=direction, entry_price=100.0, stop_price=90.0,
        t1_price=106.18, qty=0.01, risk_dollars_used=100.0,
        decision="WOULD_PLACE", management_state=management_state,
        gate_profile_used="GATE_TRAVELER", mgmt_profile_used="MGMT_E1_STACK",
    )
    fields.update(overrides)
    order = ExecutorOrder(**fields)
    db.add(order)
    db.flush()
    return order


def _order_detail_response(status="NEW", order_id="order1"):
    return {"code": 0, "data": {"orderId": order_id, "status": status}, "msg": "Success"}


def _one_position_response(position_id="pos1", avg_open_price=100.0, side="BUY"):
    return {"code": 0, "data": [{"positionId": position_id, "symbol": "BTCUSDT", "side": side, "avgOpenPrice": str(avg_open_price), "qty": "0.01"}], "msg": "Success"}


def _no_position_response():
    return {"code": 0, "data": [], "msg": "Success"}


def _trading_pairs_response(min_trade_volume="0.0001", base_precision=4, quote_precision=1):
    return {"code": 0, "data": [{"symbol": "BTCUSDT", "minTradeVolume": min_trade_volume,
                                  "basePrecision": base_precision, "quotePrecision": quote_precision}], "msg": "Success"}


def _tpsl_response(order_id="tpsl1"):
    return {"code": 0, "data": {"orderId": order_id}, "msg": "Success"}


def _place_order_response(order_id="order2"):
    return {"code": 0, "data": {"orderId": order_id}, "msg": "Success"}


def _cancel_orders_response(order_id="entry-order-1", success=True):
    if success:
        return {"code": 0, "data": {"successList": [{"orderId": order_id}], "failureList": []}, "msg": "Success"}
    return {"code": 0, "data": {"successList": [], "failureList": [{"orderId": order_id, "errorCode": "40001", "errorMsg": "order not found"}]}, "msg": "Success"}


def _close_position_response():
    return {"code": 0, "data": {}, "msg": "Success"}


def _install(monkeypatch, **fakes):
    for name in ("get_position", "get_trading_pairs", "place_order", "get_order_detail",
                 "set_position_tpsl", "cancel_orders", "close_position"):
        fake = fakes.get(name)
        if fake is None:
            async def _unexpected(self, *a, __name=name, **kw):
                raise AssertionError(f"BitunixClient.{__name}() should not have been called")
            fake = _unexpected
        monkeypatch.setattr(ebc.BitunixClient, name, fake)


def _async(value):
    async def _fake(self, *a, **kw):
        return value
    return _fake


def _async_seq(values):
    it = iter(values)
    async def _fake(self, *a, **kw):
        return next(it)
    return _fake


def _fake_candles(n=20, price=100.0):
    return [{"close": price} for _ in range(n)]


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ place_traveler_entry_order

def test_place_traveler_entry_order_places_a_resting_post_only_limit(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state=None)
    _install(monkeypatch, get_trading_pairs=_async(_trading_pairs_response()), place_order=_async(_place_order_response()))
    _run(e1e.place_traveler_entry_order(db, account, plan, order))
    assert order.management_state == "PENDING_ENTRY"
    assert order.entry_exchange_order_id == "order2"


# ------------------------------------------------------------------ check_traveler_entry_fill_and_protect

def test_entry_not_yet_filled_makes_no_state_change_when_plan_not_expired(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db, journey_cap_at=_FAR_FUTURE)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")
    _install(monkeypatch, get_order_detail=_async(_order_detail_response(status="NEW")))
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))
    assert order.management_state == "PENDING_ENTRY"


def test_entry_fill_places_stop_and_full_qty_t1_only_no_t3(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")
    _install(monkeypatch,
             get_order_detail=_async(_order_detail_response(status="FILLED")),
             get_position=_async(_one_position_response()),
             get_trading_pairs=_async(_trading_pairs_response()),
             set_position_tpsl=_async(_tpsl_response()),
             place_order=_async(_place_order_response(order_id="t1-order")))
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))
    assert order.management_state == "ENTRY_FILLED_ORDERS_PLACED"
    assert order.sl_exchange_order_id == "tpsl1"
    assert order.t1_exchange_order_id == "t1-order"
    assert order.t3_exchange_order_id is None   # never touched -- no T3 for E1


def test_entry_protection_failure_lands_in_unprotected_state(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")

    async def _tpsl_fail(self, *a, **kw):
        return {"code": 1, "msg": "boom", "data": {}}

    _install(monkeypatch,
             get_order_detail=_async(_order_detail_response(status="FILLED")),
             get_position=_async(_one_position_response()),
             get_trading_pairs=_async(_trading_pairs_response()),
             set_position_tpsl=_tpsl_fail,
             place_order=_async(_place_order_response(order_id="t1-order")))
    monkeypatch.setattr("notify.send_admin_email", lambda subject, body: True)
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))
    assert order.management_state == "ENTRY_FILLED_UNPROTECTED"


# ------------------------------------------------------------------ P0-1-parity: cancel-on-expiry for the entry (scenario k, CC's own addition)

def test_expired_journey_cancels_the_resting_entry_order(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db, journey_cap_at=_FAR_PAST)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")
    _install(monkeypatch,
             get_order_detail=_async_seq([_order_detail_response(status="NEW"), _order_detail_response(status="CANCELED")]),
             cancel_orders=_async(_cancel_orders_response(order_id="entry-order-1")))
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))
    assert order.management_state == "CLOSED_EXPIRED"
    assert order.close_reason == "EXPIRED"


def test_plan_status_done_cancels_the_resting_entry_order(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db, status="DONE", journey_cap_at=_FAR_FUTURE)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")
    _install(monkeypatch,
             get_order_detail=_async_seq([_order_detail_response(status="NEW"), _order_detail_response(status="CANCELED")]),
             cancel_orders=_async(_cancel_orders_response(order_id="entry-order-1")))
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))
    assert order.management_state == "CLOSED_EXPIRED"


def test_tercile_skipped_status_also_cancels_the_resting_entry_order(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db, status="TERCILE_SKIPPED", journey_cap_at=_FAR_FUTURE)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")
    _install(monkeypatch,
             get_order_detail=_async_seq([_order_detail_response(status="NEW"), _order_detail_response(status="CANCELED")]),
             cancel_orders=_async(_cancel_orders_response(order_id="entry-order-1")))
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))
    assert order.management_state == "CLOSED_EXPIRED"


def test_expiry_cancel_race_with_a_real_fill_hands_off_untouched(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db, journey_cap_at=_FAR_PAST)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")
    _install(monkeypatch,
             get_order_detail=_async_seq([_order_detail_response(status="NEW"), _order_detail_response(status="FILLED")]),
             cancel_orders=_async(_cancel_orders_response(order_id="entry-order-1")))
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))
    assert order.management_state == "PENDING_ENTRY"   # untouched -- next tick's normal fill path takes over


def test_expiry_cancel_call_failure_retries_next_tick(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db, journey_cap_at=_FAR_PAST)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")

    async def _raise(self, *a, **kw):
        raise ConnectionError("simulated network failure")

    _install(monkeypatch, get_order_detail=_async(_order_detail_response(status="NEW")), cancel_orders=_raise)
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))
    assert order.management_state == "PENDING_ENTRY"


# ------------------------------------------------------------------ (a) normal T1 fill

def test_normal_t1_fill_closes_100_percent_and_records_result(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    _install(monkeypatch,
             get_position=_async(_no_position_response()),
             get_order_detail=_async(_order_detail_response(status="FILLED", order_id="t1-1")))
    _run(e1e.poll_traveler_position(db, account, plan, order))
    assert order.management_state == "CLOSED_T1"
    assert order.close_reason == "T1"
    assert order.realized_pnl_r == pytest.approx((106.18 - 100.0) / 10.0)
    db.flush()
    rows = db.query(ExecutorAuditLog).filter_by(executor_order_id=order.id, event_type="POSITION_CLOSED").all()
    assert len(rows) == 1


def test_closed_states_are_terminal_loop_stops_touching_them(db):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="CLOSED_T1")
    _run(e1e.poll_traveler_position(db, account, plan, order))   # must not raise, must not act
    assert order.management_state == "CLOSED_T1"


# ------------------------------------------------------------------ (b)/(c) C5/BBWP fire before T1

def test_c5_fires_before_t1_market_closes_and_cancels_t1(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h: (True, False))
    _install(monkeypatch,
             get_position=_async_seq([_one_position_response(), _no_position_response()]),
             close_position=_async(_close_position_response()),
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")),
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))

    async def _fake_1h(symbol, limit=200):
        return _fake_candles()

    async def _fake_4h(symbol, limit=200):
        return _fake_candles()

    monkeypatch.setattr(e1e.market_data, "fetch_live_1h", _fake_1h)
    monkeypatch.setattr(e1e.market_data, "fetch_live_4h", _fake_4h)
    monkeypatch.setattr(e1e, "_current_live_price", lambda symbol: asyncio.sleep(0, result=104.5))

    _run(e1e.poll_traveler_position(db, account, plan, order))
    assert order.management_state == "CLOSED_C5_EXIT"
    assert order.c5_fired is True
    assert order.bbwp_fired is False
    assert order.exit_price == 104.5


def test_bbwp_fires_before_t1_market_closes_and_cancels_t1(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h: (False, True))
    _install(monkeypatch,
             get_position=_async_seq([_one_position_response(), _no_position_response()]),
             close_position=_async(_close_position_response()),
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")),
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))

    async def _fake_candles_fn(symbol, limit=200):
        return _fake_candles()

    monkeypatch.setattr(e1e.market_data, "fetch_live_1h", _fake_candles_fn)
    monkeypatch.setattr(e1e.market_data, "fetch_live_4h", _fake_candles_fn)
    monkeypatch.setattr(e1e, "_current_live_price", lambda symbol: asyncio.sleep(0, result=95.5))

    _run(e1e.poll_traveler_position(db, account, plan, order))
    assert order.management_state == "CLOSED_BBWP_EXIT"
    assert order.bbwp_fired is True


# ------------------------------------------------------------------ (d) STOP fires first (exchange-side)

def test_stop_fires_first_cancels_orphaned_t1(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    _install(monkeypatch,
             get_position=_async(_no_position_response()),
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")),
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))
    _run(e1e.poll_traveler_position(db, account, plan, order))
    assert order.management_state == "CLOSED_STOP"
    assert order.exit_price == 90.0   # the order's own recorded stop price
    assert order.realized_pnl_r == pytest.approx(-1.0)


# ------------------------------------------------------------------ (e) TIME at journey_cap_at

def test_time_exit_market_closes_and_cancels_t1(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db, journey_cap_at=_FAR_PAST)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h: (False, False))
    _install(monkeypatch,
             get_position=_async_seq([_one_position_response(), _no_position_response()]),
             close_position=_async(_close_position_response()),
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")),
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))

    async def _fake_candles_fn(symbol, limit=200):
        return _fake_candles()

    monkeypatch.setattr(e1e.market_data, "fetch_live_1h", _fake_candles_fn)
    monkeypatch.setattr(e1e.market_data, "fetch_live_4h", _fake_candles_fn)
    monkeypatch.setattr(e1e, "_current_live_price", lambda symbol: asyncio.sleep(0, result=101.0))

    _run(e1e.poll_traveler_position(db, account, plan, order))
    assert order.management_state == "CLOSED_TIME"


# ------------------------------------------------------------------ (f) race: T1 fills during the market-close confirmation window

def test_c5_fires_but_t1_actually_filled_first_reconciled_as_t1(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h: (True, False))
    _install(monkeypatch,
             get_position=_async_seq([_one_position_response(), _no_position_response()]),
             close_position=_async(_close_position_response()),
             get_order_detail=_async(_order_detail_response(status="FILLED", order_id="t1-1")),   # T1 actually filled
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))

    async def _fake_candles_fn(symbol, limit=200):
        return _fake_candles()

    monkeypatch.setattr(e1e.market_data, "fetch_live_1h", _fake_candles_fn)
    monkeypatch.setattr(e1e.market_data, "fetch_live_4h", _fake_candles_fn)

    _run(e1e.poll_traveler_position(db, account, plan, order))
    # Reconciled as a genuine T1 fill, NOT double-booked as a C5 exit.
    assert order.management_state == "CLOSED_T1"
    assert order.c5_fired is False


# ------------------------------------------------------------------ (g) orphaned-T1 cancel failure does not block finalizing the close

def test_orphaned_t1_cancel_failure_still_finalizes_the_close(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    _install(monkeypatch,
             get_position=_async(_no_position_response()),
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")),
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1", success=False)))
    _run(e1e.poll_traveler_position(db, account, plan, order))
    # The position is genuinely flat (STOP fired) -- a failed cleanup cancel
    # of an already-unfillable reduce-only order must not block closing the
    # trade out; it's a hygiene issue, not a safety one (a reduce-only order
    # can never open new exposure with no position behind it).
    assert order.management_state == "CLOSED_STOP"
    db.flush()
    rows = db.query(ExecutorAuditLog).filter_by(executor_order_id=order.id, event_type="ERROR").all()
    assert any("did not report" in (r.message or "") for r in rows)


def test_close_position_call_failure_retries_next_tick(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h: (True, False))

    async def _raise(self, *a, **kw):
        raise ConnectionError("simulated network failure")

    _install(monkeypatch, get_position=_async(_one_position_response()), close_position=_raise)

    async def _fake_candles_fn(symbol, limit=200):
        return _fake_candles()

    monkeypatch.setattr(e1e.market_data, "fetch_live_1h", _fake_candles_fn)
    monkeypatch.setattr(e1e.market_data, "fetch_live_4h", _fake_candles_fn)

    _run(e1e.poll_traveler_position(db, account, plan, order))
    assert order.management_state == "ENTRY_FILLED_ORDERS_PLACED"   # untouched -- retry next tick


# ------------------------------------------------------------------ (h) engine-selection guard

def test_e1_order_never_reaches_the_split_engines_query(db):
    import executor_live_engine
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="PENDING_ENTRY", entry_exchange_order_id="e1-order")
    matches = db.query(ExecutorOrder).filter(
        ExecutorOrder.management_state.isnot(None),
        ~ExecutorOrder.management_state.in_(executor_live_engine._TERMINAL_STATES),
        ExecutorOrder.entry_exchange_order_id.isnot(None),
        ExecutorOrder.traveler_plan_id.is_(None),
    ).all()
    assert order not in matches


def test_split_order_never_reaches_the_e1_engines_query(db):
    account = _ready_account(db)
    plan = _traveler_plan(db)   # unused by the split order, just needs a valid TradePlan-shaped row id
    order = _order_row(db, account, plan, management_state="PENDING_ENTRY", entry_exchange_order_id="split-order",
                        traveler_plan_id=None, mgmt_profile_used="MGMT_SPLIT")
    matches = db.query(ExecutorOrder).filter(
        ExecutorOrder.management_state.isnot(None),
        ~ExecutorOrder.management_state.in_(e1e._E1_LIVE_TERMINAL_STATES),
        ExecutorOrder.entry_exchange_order_id.isnot(None),
        ExecutorOrder.traveler_plan_id.isnot(None),
    ).all()
    assert order not in matches


# ------------------------------------------------------------------ (i) exit price honesty

def test_market_close_exit_price_uses_last_known_live_price_not_fabricated(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h: (True, False))
    _install(monkeypatch,
             get_position=_async_seq([_one_position_response(), _no_position_response()]),
             close_position=_async(_close_position_response()),
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")),
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))

    async def _fake_candles_fn(symbol, limit=200):
        return _fake_candles()

    monkeypatch.setattr(e1e.market_data, "fetch_live_1h", _fake_candles_fn)
    monkeypatch.setattr(e1e.market_data, "fetch_live_4h", _fake_candles_fn)
    monkeypatch.setattr(e1e, "_current_live_price", lambda symbol: asyncio.sleep(0, result=103.25))

    _run(e1e.poll_traveler_position(db, account, plan, order))
    assert order.exit_price == 103.25
    db.flush()
    row = db.query(ExecutorAuditLog).filter_by(executor_order_id=order.id, event_type="POSITION_CLOSED").first()
    assert "approximated" in (row.message or "")


# ---- 2026-09-21: the live poll must not act on a forming-bar dip -----------------
# check_c5_or_bbwp is deliberately NOT patched here -- the strip lives inside
# it, so patching it would hide exactly the regression this guards. The 1H
# series' last bar is the CURRENT (still-forming) hour with a sharp dip; the
# confirmed bars are a clean rise, so a correct poll leaves the position
# alone. close_position is recorded (not left un-faked): a per-leg
# try/except inside the poll would swallow an AssertionError and hide it.

def _rising_bars(interval, n=41, forming_dip=0.0):
    import time as _t
    now = _t.time()
    last_open = int(now // interval) * interval          # bar containing "now" = forming
    closes = [100.0]
    for i in range(n - 2):
        closes.append(closes[-1] + (-0.5 if i % 4 == 3 else 1.5))
    closes.append(closes[-1] - forming_dip if forming_dip else closes[-1] + 1.5)
    return [{"close": c, "time": last_open - (len(closes) - 1 - i) * interval} for i, c in enumerate(closes)]


def test_forming_1h_dip_does_not_fire_a_live_c5_market_close(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    close_calls = []

    async def _recording_close(self, *a, **kw):
        close_calls.append(1)
        return _close_position_response()

    _install(monkeypatch,
             get_position=_async(_one_position_response()),
             close_position=_recording_close,
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")))

    async def _fake_1h(symbol, limit=200):
        return _rising_bars(3600, forming_dip=6.0)

    async def _fake_4h(symbol, limit=200):
        return _rising_bars(14400)

    monkeypatch.setattr(e1e.market_data, "fetch_live_1h", _fake_1h)
    monkeypatch.setattr(e1e.market_data, "fetch_live_4h", _fake_4h)

    _run(e1e.poll_traveler_position(db, account, plan, order))
    assert close_calls == [], "a forming-bar dip must never reach close_position()"
    assert order.management_state == "ENTRY_FILLED_ORDERS_PLACED"
    assert order.c5_fired in (None, False)


# ---- 2026-09-21 audit: LIVE rows must never carry the DRY_RUN fill booking, and the
# simulated E1 walk must never select a LIVE order ---------------------------------

import executor_engine
import executor_plan_builder
import traveler_plan_engine


def _would_place_dict(plan, account):
    return {
        "trade_plan_id": plan.id, "traveler_plan_id": plan.id, "account_id": account.id,
        "mode": account.mode, "symbol": plan.symbol, "direction": plan.direction,
        "entry_price": 100.0, "stop_price": 90.0, "t1_price": 106.18,
        "qty": 0.01, "risk_dollars_used": 100.0,
        "decision": "WOULD_PLACE", "decision_reason": "test",
    }


def _traveler_account(db, mode, label):
    account = ea.create_account(db, user_id=1, label=label)
    db.flush()
    ea.set_account_profile(db, account, "GATE_TRAVELER", "MGMT_E1_STACK", by="test")   # before LIVE: that guard refuses LIVE accounts without credentials
    account.mode = mode
    db.commit()
    return account


def _filled_plan(db):
    plan = _traveler_plan(db, status="FILLED")
    plan.fill_price = 99.0
    plan.fill_time = datetime.datetime(2026, 9, 21, 14, 0, tzinfo=datetime.timezone.utc)
    db.flush()
    return plan


def test_live_traveler_row_does_not_inherit_the_dry_run_fill_booking(db, monkeypatch):
    account = _traveler_account(db, "LIVE", "live_no_phantom")
    plan = _filled_plan(db)
    assert ec.is_live_orders_enabled(db) is False   # switch OFF: no exchange call can happen here

    async def _fake_build(db_, plan_, account_, risk_):
        return _would_place_dict(plan_, account_)
    monkeypatch.setattr(executor_plan_builder, "build_hypothetical_traveler_order", _fake_build)

    _run(executor_engine._process_traveler_account(db, plan, account))
    order = db.query(ExecutorOrder).filter_by(account_id=account.id, traveler_plan_id=plan.id).one()
    assert order.entry_fill_price is None
    assert order.entry_fill_time is None
    assert order.management_state != "ENTRY_FILLED_ORDERS_PLACED"


def test_dry_run_traveler_row_still_books_the_fill_immediately(db, monkeypatch):
    account = _traveler_account(db, "DRY_RUN", "dry_still_books")
    plan = _filled_plan(db)

    async def _fake_build(db_, plan_, account_, risk_):
        return _would_place_dict(plan_, account_)
    monkeypatch.setattr(executor_plan_builder, "build_hypothetical_traveler_order", _fake_build)

    _run(executor_engine._process_traveler_account(db, plan, account))
    order = db.query(ExecutorOrder).filter_by(account_id=account.id, traveler_plan_id=plan.id).one()
    assert order.entry_fill_price == 99.0
    assert order.entry_fill_time is not None
    assert order.management_state == "ENTRY_FILLED_ORDERS_PLACED"


def test_simulated_e1_walk_selects_only_dry_run_orders(db):
    dry_acct = _traveler_account(db, "DRY_RUN", "walk_dry")
    live_acct = _traveler_account(db, "LIVE", "walk_live")
    p_dry, p_live, p_done = _filled_plan(db), _filled_plan(db), _filled_plan(db)
    o_dry = _order_row(db, dry_acct, p_dry, management_state="ENTRY_FILLED_ORDERS_PLACED", mode="DRY_RUN",
                       entry_fill_time=datetime.datetime(2026, 9, 21, 14, 0))
    # A real, filled LIVE order: entry_fill_time is set by the live engine at
    # the real fill -- exactly what would let the simulated walk start
    # driving it if the query did not filter on mode.
    _order_row(db, live_acct, p_live, management_state="ENTRY_FILLED_ORDERS_PLACED", mode="LIVE",
               entry_exchange_order_id="real-1", position_id="pos1",
               entry_fill_time=datetime.datetime(2026, 9, 21, 14, 0))
    _order_row(db, dry_acct, p_done, management_state="CLOSED_T1", mode="DRY_RUN")
    db.commit()

    selected = traveler_plan_engine._open_dry_run_e1_orders(db)
    assert [o.id for o in selected] == [o_dry.id]
