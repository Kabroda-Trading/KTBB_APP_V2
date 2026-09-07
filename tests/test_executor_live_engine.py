"""
Unit coverage for executor_live_engine.py -- Domain 2, Stage 3 (2026-09-07).
Same fixture/mocking style as tests/test_executor_mechanism_test.py: every
BitunixClient method is monkeypatched at the class level, NO real network
call is ever made here (the real chain is verified live, separately, via
the mechanism-test pre-flight step). market_data's live-candle fetches are
monkeypatched too -- no real MEXC/Kraken call either.

Hand-computed R math throughout, same convention as audit_engine.py/
CampaignLog: entry=100, stop=90 (risk=10, LONG), t1=106.18 (0.618x),
t2=110 (1.0x), t3=116.18 (1.618x) -- decision_engine.py's own box
multiples (CLAUDE.md rule #1).
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_executor_live_engine.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import datetime

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount, ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorSizingPolicy, ExecutorGlobalConfig, TradePlan
import executor_accounts as ea
import executor_control as ec
import executor_bitunix_client as ebc
import executor_live_engine as ele
import market_data


def _clean_db_files():
    for path in ["kabroda_test_executor_live_engine.db", "kabroda_test_executor_live_engine.db-journal",
                 "kabroda_test_executor_live_engine.db-shm", "kabroda_test_executor_live_engine.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    for model in (ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorSizingPolicy, ExecutorAccount, ExecutorGlobalConfig, TradePlan):
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


def _ready_account(db, label="andy_bitunix_live"):
    account = ea.create_account(db, user_id=1, label=label)
    db.flush()
    account.mode = "LIVE"
    ea.set_credentials(db, account, api_key="fake-key", api_secret="fake-secret", set_by="test@kabroda.com")
    ec.enable_live_orders(db, reason="testing", by="andy@kabroda.com")
    db.commit()
    return account


def _trade_plan(db, direction="LONG", tier="STANDARD"):
    plan = TradePlan(symbol="BTC/USDT", date_key="2026-09-07", session_id="us_ny_futures", status="FILLED",
                      direction=direction, tier=tier, trigger_price=100.0, stop_price=90.0, t1=106.18, t2=110.0, t3=116.18)
    db.add(plan)
    db.flush()
    return plan


def _order_row(db, account, trade_plan, tier="STANDARD", direction="LONG", management_state="PENDING_ENTRY", **overrides):
    fields = dict(
        trade_plan_id=trade_plan.id, account_id=account.id, mode="LIVE",
        symbol="BTC/USDT", direction=direction, tier=tier, entry_price=100.0, stop_price=90.0,
        t1_price=106.18, t2_price=110.0, t3_price=116.18, qty=0.01,
        risk_dollars_used=100.0, decision="WOULD_PLACE", management_state=management_state,
    )
    fields.update(overrides)
    order = ExecutorOrder(**fields)
    db.add(order)
    db.flush()
    return order


def _trading_pairs_response(min_trade_volume="0.0001", base_precision=4, quote_precision=1):
    return {"code": 0, "data": [{"symbol": "BTCUSDT", "minTradeVolume": min_trade_volume,
                                  "basePrecision": base_precision, "quotePrecision": quote_precision}], "msg": "Success"}


def _order_detail_response(status="NEW", order_id="order1"):
    return {"code": 0, "data": {"orderId": order_id, "status": status}, "msg": "Success"}


def _one_position_response(position_id="pos1", avg_open_price=100.0, side="BUY"):
    return {"code": 0, "data": [{"positionId": position_id, "symbol": "BTCUSDT", "side": side, "avgOpenPrice": str(avg_open_price), "qty": "0.01"}], "msg": "Success"}


def _no_position_response():
    return {"code": 0, "data": [], "msg": "Success"}


def _place_order_response(order_id="order2"):
    return {"code": 0, "data": {"orderId": order_id}, "msg": "Success"}


def _tpsl_response(order_id="tpsl1"):
    return {"code": 0, "data": {"orderId": order_id}, "msg": "Success"}


def _install(monkeypatch, **fakes):
    for name in ("get_position", "get_trading_pairs", "place_order", "get_order_detail",
                 "set_position_tpsl", "modify_position_tp_sl_order"):
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


def _fake_candles(price, n=2):
    return [{"close": price, "open": price, "high": price, "low": price, "volume": 1.0} for _ in range(n)]


def _run(coro):
    # asyncio.run(), not get_event_loop().run_until_complete() -- same
    # convention as tests/test_executor_mechanism_test.py. get_event_loop()
    # can return an already-closed loop left behind by an earlier test
    # file in the same full-suite run; asyncio.run() always gets a fresh
    # one and closes it properly.
    return asyncio.run(coro)


# ------------------------------------------------------------------ place_entry_order

def test_place_entry_order_uses_post_only_and_correct_side(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db, direction="LONG")
    order = _order_row(db, account, plan, direction="LONG")

    captured = {}
    async def _fake_place_order(self, **kw):
        captured.update(kw)
        return _place_order_response("entry-order-1")

    _install(monkeypatch, get_trading_pairs=_async(_trading_pairs_response()), place_order=_fake_place_order)
    _run(ele.place_entry_order(db, account, plan, order))

    assert captured["effect"] == "POST_ONLY"
    assert captured["side"] == "BUY"          # LONG entry
    assert captured["trade_side"] == "OPEN"
    assert captured["order_type"] == "LIMIT"
    assert order.entry_exchange_order_id == "entry-order-1"
    assert order.management_state == "PENDING_ENTRY"


def test_place_entry_order_short_uses_sell_side(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db, direction="SHORT")
    order = _order_row(db, account, plan, direction="SHORT", entry_price=90.0, stop_price=100.0,
                        t1_price=83.82, t2_price=80.0, t3_price=73.82)

    captured = {}
    async def _fake_place_order(self, **kw):
        captured.update(kw)
        return _place_order_response("entry-order-short")

    _install(monkeypatch, get_trading_pairs=_async(_trading_pairs_response()), place_order=_fake_place_order)
    _run(ele.place_entry_order(db, account, plan, order))
    assert captured["side"] == "SELL"


# ------------------------------------------------------------------ check_entry_fill_and_place_exits

def test_entry_fill_places_all_three_orders_atomically(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db, direction="LONG")
    order = _order_row(db, account, plan, direction="LONG", entry_exchange_order_id="entry-order-1")

    place_order_calls = []
    async def _fake_place_order(self, **kw):
        place_order_calls.append(kw)
        return _place_order_response(f"order-{len(place_order_calls)}")

    tpsl_calls = []
    async def _fake_set_tpsl(self, **kw):
        tpsl_calls.append(kw)
        return _tpsl_response()

    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="FILLED")),
              get_position=_async(_one_position_response()),
              get_trading_pairs=_async(_trading_pairs_response()),
              set_position_tpsl=_fake_set_tpsl,
              place_order=_fake_place_order)

    _run(ele.check_entry_fill_and_place_exits(db, account, plan, order))

    assert order.management_state == "ENTRY_FILLED_ORDERS_PLACED"
    assert order.position_id == "pos1"
    assert order.entry_fill_price == 100.0
    # stop + T1 + T3 all placed, in one call each -- atomically.
    assert len(tpsl_calls) == 1
    assert tpsl_calls[0]["sl_price"] == "90.0"
    assert len(place_order_calls) == 2
    assert all(c["reduce_only"] is True and c["effect"] == "POST_ONLY" for c in place_order_calls)
    assert order.t1_exchange_order_id is not None
    assert order.t3_exchange_order_id is not None


def test_entry_not_yet_filled_makes_no_state_change(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")
    _install(monkeypatch, get_order_detail=_async(_order_detail_response(status="NEW")))
    _run(ele.check_entry_fill_and_place_exits(db, account, plan, order))
    assert order.management_state == "PENDING_ENTRY"
    assert order.entry_status == "NEW"


# ------------------------------------------------------------------ poll_open_position -- T2 breakeven

def test_premium_be_move_only_at_t2_touch_not_before(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db, tier="PREMIUM")
    order = _order_row(db, account, plan, tier="PREMIUM", management_state="T1_FILLED_BE_PENDING",
                        t1_status="FILLED", t1_fill_price=106.18, t1_leg_r=0.309,
                        entry_fill_price=100.0, position_id="pos1")

    modify_calls = []
    async def _fake_modify(self, **kw):
        modify_calls.append(kw)
        return _tpsl_response()

    # Price short of T2 (110) -- no BE move.
    _install(monkeypatch,
              get_position=_async(_one_position_response()),
              get_trading_pairs=_async(_trading_pairs_response()),
              modify_position_tp_sl_order=_fake_modify)
    monkeypatch.setattr(market_data, "fetch_live_5m", _async(_fake_candles(105.0)))
    _run(ele.poll_open_position(db, account, plan, order))
    assert len(modify_calls) == 0
    assert order.management_state == "T1_FILLED_BE_PENDING"
    assert order.sl_moved_to_be_at is None

    # Price now at/through T2 -- exactly one BE call.
    monkeypatch.setattr(market_data, "fetch_live_5m", _async(_fake_candles(111.0)))
    monkeypatch.setattr(market_data, "fetch_live_15m", _async(_fake_candles(111.0)))
    import fuel_gate, micro_regime
    monkeypatch.setattr(fuel_gate, "evaluate_fuel_gate", lambda *a, **kw: {"verdict": "FUELED"})
    monkeypatch.setattr(micro_regime, "classify_regime", lambda *a, **kw: {"regime": "TRENDING"})
    _run(ele.poll_open_position(db, account, plan, order))
    assert len(modify_calls) == 1
    assert modify_calls[0]["sl_price"] == "100.0"
    assert order.management_state == "BE_MOVED"
    assert order.sl_moved_to_be_at is not None
    assert order.t2_reval_fuel_verdict == "FUELED"
    assert order.t2_reval_micro_regime == "TRENDING"


def test_standard_never_moves_stop_at_t2(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db, tier="STANDARD")
    order = _order_row(db, account, plan, tier="STANDARD", management_state="T1_FILLED",
                        t1_status="FILLED", t1_fill_price=106.18, t1_leg_r=0.309,
                        entry_fill_price=100.0, position_id="pos1")

    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="FILLED")),
              get_position=_async(_one_position_response()))
    # No market_data patch needed -- STANDARD's management_state is
    # "T1_FILLED", not "T1_FILLED_BE_PENDING", so the T2 branch's own
    # condition (tier == PREMIUM and state == T1_FILLED_BE_PENDING) is
    # never true; modify_position_tp_sl_order must never be called.
    _run(ele.poll_open_position(db, account, plan, order))
    assert order.management_state == "T1_FILLED"   # unchanged, no BE state exists for STANDARD


# ------------------------------------------------------------------ poll_open_position -- closure & R math

def test_full_stop_before_t1_is_exactly_minus_one_r(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1",
                        t1_exchange_order_id="t1-1", t3_exchange_order_id="t3-1")

    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="NEW")),   # T1 never filled
              get_position=_async(_no_position_response()))                    # position now flat -- stopped out
    _run(ele.poll_open_position(db, account, plan, order))

    assert order.close_reason == "STOP_BEFORE_T1"
    assert order.realized_pnl_r == -1.0
    assert order.management_state == "CLOSED_STOP_BEFORE_T1"
    assert order.closed_at is not None


def test_t1_then_runner_stop_blended_r(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db, tier="STANDARD")
    order = _order_row(db, account, plan, tier="STANDARD", management_state="T1_FILLED",
                        t1_status="FILLED", t1_fill_price=106.18, t1_leg_r=0.5 * (106.18 - 100.0) / 10.0,
                        entry_fill_price=100.0, position_id="pos1",
                        t1_exchange_order_id="t1-1", t3_exchange_order_id="t3-1",
                        sl_price_current=90.0)   # STANDARD -- stop never moved

    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="NEW")),   # T3 never filled
              get_position=_async(_no_position_response()))                    # flat -- runner stopped
    _run(ele.poll_open_position(db, account, plan, order))

    expected_t1_leg = 0.5 * (106.18 - 100.0) / 10.0
    expected_runner = 0.5 * (90.0 - 100.0) / 10.0   # -0.5
    assert order.close_reason == "RUNNER_STOP"
    assert order.realized_pnl_r == pytest.approx(expected_t1_leg + expected_runner)
    assert order.management_state == "CLOSED_RUNNER_STOP"


def test_t1_then_t3_blended_r(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db, tier="STANDARD")
    order = _order_row(db, account, plan, tier="STANDARD", management_state="T1_FILLED",
                        t1_status="FILLED", t1_fill_price=106.18, t1_leg_r=0.5 * (106.18 - 100.0) / 10.0,
                        entry_fill_price=100.0, position_id="pos1",
                        t1_exchange_order_id="t1-1", t3_exchange_order_id="t3-1")

    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="FILLED")),  # T3 filled
              get_position=_async(_no_position_response()))
    _run(ele.poll_open_position(db, account, plan, order))

    expected_t1_leg = 0.5 * (106.18 - 100.0) / 10.0
    expected_runner = 0.5 * (116.18 - 100.0) / 10.0
    assert order.close_reason == "T3"
    assert order.t3_fill_price == 116.18
    assert order.realized_pnl_r == pytest.approx(expected_t1_leg + expected_runner)
    assert order.management_state == "CLOSED_T3"


def test_close_calls_record_trade_result_automatically(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", risk_dollars_used=250.0,
                        t1_exchange_order_id="t1-1", t3_exchange_order_id="t3-1")

    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="NEW")),
              get_position=_async(_no_position_response()))
    _run(ele.poll_open_position(db, account, plan, order))

    db.commit()
    risk_state = db.query(ExecutorRiskState).filter_by(account_id=account.id).first()
    assert risk_state is not None
    assert risk_state.last_trade_pnl_usd == pytest.approx(-1.0 * 250.0)
    assert risk_state.last_updated_from_trade_plan_id == plan.id


# ------------------------------------------------------------------ idempotency (via the ExecutorOrder unique constraint)

def test_entry_placement_is_idempotent_per_trade_plan(db):
    account = _ready_account(db)
    plan = _trade_plan(db)
    _order_row(db, account, plan)
    db.commit()

    from sqlalchemy.exc import IntegrityError
    dup = ExecutorOrder(trade_plan_id=plan.id, account_id=account.id, mode="LIVE",
                         symbol="BTC/USDT", direction="LONG", decision="WOULD_PLACE")
    db.add(dup)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
