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


def _trade_plan(db, direction="LONG", tier="STANDARD", date_key="2026-09-07", status="FILLED"):
    plan = TradePlan(symbol="BTC/USDT", date_key=date_key, session_id="us_ny_futures", status=status,
                      direction=direction, tier=tier, trigger_price=100.0, stop_price=90.0, t1=106.18, t2=110.0, t3=116.18)
    db.add(plan)
    db.flush()
    return plan


# A date_key far enough in the future that _compute_session_expires_at()
# never treats it as expired, regardless of when this suite actually runs
# -- P0-1's cancel-on-expiry check (executor_live_engine.py::
# _plan_has_expired()) must never fire for these existing, unrelated
# tests. Computed, not a hardcoded future year, so it never itself
# becomes "the past" the way this file's own "2026-09-07" default already
# has relative to whenever this suite is actually run.
_FAR_FUTURE_DATE_KEY = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650)).strftime("%Y-%m-%d")
# A date_key unambiguously in the past -- for P0-1's own expired-plan tests.
_FAR_PAST_DATE_KEY = "2020-01-01"


def _cancel_orders_response(order_id="entry-order-1", success=True):
    if success:
        return {"code": 0, "data": {"successList": [{"orderId": order_id}], "failureList": []}, "msg": "Success"}
    return {"code": 0, "data": {"successList": [], "failureList": [{"orderId": order_id, "errorCode": "40001", "errorMsg": "order not found"}]}, "msg": "Success"}


def _cancel_orders_response_multi(order_ids, missing=()):
    # 2026-09-22 audit fix (v2 orphaned T1/T3 cancel): a batch response
    # covering N ids in one call, matching executor_mechanism_test.py::
    # cancel_concurrent_limits()'s own already-live-tested 2-id shape.
    # `missing` lets a test simulate a genuine partial success/failure
    # within the same batch call.
    success = [oid for oid in order_ids if oid not in missing]
    failure = [{"orderId": oid, "errorCode": "40001", "errorMsg": "order not found"} for oid in missing]
    return {"code": 0, "data": {"successList": [{"orderId": oid} for oid in success], "failureList": failure}, "msg": "Success"}


def _recording_async(value=None, exc=None):
    """Like _async(), but records every call's (args, kwargs) on the
    returned list -- for asserting exactly which order_ids a mocked
    BitunixClient method was called with, not just that SOME call happened.
    Needed because _cancel_orphaned_exit_orders() (like the traveler
    engine's own _cancel_orphaned_t1()) catches ANY exception from the real
    call internally and logs an audit row rather than raising -- so
    _install()'s own "unmocked method raises AssertionError" self-test
    trick is silently swallowed for this call and proves nothing on its
    own; an explicit recorded call is the only real proof of the wiring."""
    calls = []

    async def _fake(self, *a, **kw):
        calls.append((a, kw))
        if exc is not None:
            raise exc
        return value

    return _fake, calls


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
                 "set_position_tpsl", "modify_position_tp_sl_order", "cancel_orders"):
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


def test_place_entry_order_post_only_rejection_is_handled_not_crashed(db, monkeypatch):
    # 2026-09-07 END_TO_END_AUDIT.md fix: a POST_ONLY rejection (data:
    # null, a real, documented possible response) used to raise an
    # unhandled TypeError reading resp["data"]["orderId"]. Must now be
    # caught, recorded, and alerted -- not crash up into process_fill().
    account = _ready_account(db)
    plan = _trade_plan(db)
    order = _order_row(db, account, plan)

    sent = []
    monkeypatch.setattr("notify.send_admin_email", lambda subject, body: sent.append((subject, body)) or True)
    rejection = {"code": 10007, "msg": "order would cross the spread", "data": None}
    _install(monkeypatch, get_trading_pairs=_async(_trading_pairs_response()), place_order=_async(rejection))

    _run(ele.place_entry_order(db, account, plan, order))  # must not raise

    assert order.management_state == "CLOSED_ERROR"
    assert order.entry_exchange_order_id is None
    assert len(sent) == 1
    assert "entry placement failed" in sent[0][0].lower()


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


def test_partial_exit_placement_failure_alerts_and_stops_not_crashes(db, monkeypatch):
    # 2026-09-07 END_TO_END_AUDIT.md fix -- the serious one: entry has
    # ALREADY FILLED (real money, real open position) when the T1 leg
    # fails to place (POST_ONLY rejection). Before this fix: unhandled
    # exception, caught only by process_fill()'s outer try/except as a
    # console print, management_state stuck at PENDING_ENTRY forever even
    # though a real position sits there -- the stop DID place correctly
    # (real protection exists) but nothing ever recorded or alerted that
    # T3 was also never attempted and the layout is incomplete.
    account = _ready_account(db)
    plan = _trade_plan(db, direction="LONG")
    order = _order_row(db, account, plan, direction="LONG", entry_exchange_order_id="entry-order-1")

    sent = []
    monkeypatch.setattr("notify.send_admin_email", lambda subject, body: sent.append((subject, body)) or True)

    call_count = {"n": 0}
    async def _fake_place_order(self, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:   # T1
            return {"code": 10007, "msg": "order would cross the spread", "data": None}
        return _place_order_response("t3-order")   # T3 succeeds

    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="FILLED")),
              get_position=_async(_one_position_response()),
              get_trading_pairs=_async(_trading_pairs_response()),
              set_position_tpsl=_async(_tpsl_response()),   # stop succeeds
              place_order=_fake_place_order)

    _run(ele.check_entry_fill_and_place_exits(db, account, plan, order))  # must not raise

    assert order.management_state == "ENTRY_FILLED_UNPROTECTED"
    # The stop and T3 that DID succeed must still be recorded -- partial
    # protection is real protection, not thrown away because one leg failed.
    assert order.sl_exchange_order_id is not None
    assert order.t3_exchange_order_id is not None
    assert order.t1_exchange_order_id is None   # the one that failed
    assert len(sent) == 1
    assert "unprotected" in sent[0][0].lower()
    assert "T1" in sent[0][1]


def test_entry_filled_unprotected_is_terminal_loop_stops_touching_it(db):
    account = _ready_account(db)
    plan = _trade_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_UNPROTECTED")
    _run(ele.poll_open_position(db, account, plan, order))   # must not raise, must not act
    assert order.management_state == "ENTRY_FILLED_UNPROTECTED"


def test_entry_not_yet_filled_makes_no_state_change(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db, date_key=_FAR_FUTURE_DATE_KEY)   # not expired -- see P0-1 tests below for that case
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")
    _install(monkeypatch, get_order_detail=_async(_order_detail_response(status="NEW")))
    _run(ele.check_entry_fill_and_place_exits(db, account, plan, order))
    assert order.management_state == "PENDING_ENTRY"
    assert order.entry_status == "NEW"


# ------------------------------------------------------------------ P0-1: cancel-on-expiry (CC_WORK_ORDER_LIVE_DAY_2026-09-19.md)
# Andy's explicit no-go: "a random limit order floating around out there
# is a big no-no in trading." A real resting entry order must never keep
# sitting on the exchange once the parent plan it was created for has
# expired (session close) or already resolved to DONE.

def test_expired_session_cancels_the_resting_entry_order(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db, date_key=_FAR_PAST_DATE_KEY)   # session closed long ago
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")
    _install(monkeypatch,
             get_order_detail=_async_seq([
                 _order_detail_response(status="NEW"),        # first check: still resting
                 _order_detail_response(status="CANCELED"),   # confirms the cancel landed
             ]),
             cancel_orders=_async(_cancel_orders_response(order_id="entry-order-1")))
    _run(ele.check_entry_fill_and_place_exits(db, account, plan, order))

    assert order.management_state == "CLOSED_EXPIRED"
    assert order.close_reason == "EXPIRED"
    assert order.closed_at is not None
    db.flush()   # SessionLocal is autoflush=False -- must flush before querying write_audit()'s pending row
    rows = db.query(ExecutorAuditLog).filter_by(executor_order_id=order.id, event_type="ORDER_CANCELLED_ON_EXPIRY").all()
    assert len(rows) == 1


def test_plan_already_done_cancels_the_resting_entry_order_even_before_session_close(db, monkeypatch):
    # An early WIDE_STOP_FIRST invalidation can mark the plan DONE well
    # before session close -- the real order must still be cancelled, not
    # just left resting until the session boundary passes too.
    account = _ready_account(db)
    plan = _trade_plan(db, date_key=_FAR_FUTURE_DATE_KEY, status="DONE")
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")
    _install(monkeypatch,
             get_order_detail=_async_seq([
                 _order_detail_response(status="NEW"),
                 _order_detail_response(status="CANCELED"),
             ]),
             cancel_orders=_async(_cancel_orders_response(order_id="entry-order-1")))
    _run(ele.check_entry_fill_and_place_exits(db, account, plan, order))

    assert order.management_state == "CLOSED_EXPIRED"


def test_cancel_race_with_a_real_fill_hands_off_to_the_normal_fill_path(db, monkeypatch):
    # The most important safety property of this whole mechanism: if the
    # order actually filled in the moments between the "still resting"
    # check and the cancel landing, that fill must NEVER be discarded --
    # a filled, unprotected real position is far more dangerous than a
    # stray resting order.
    account = _ready_account(db)
    plan = _trade_plan(db, date_key=_FAR_PAST_DATE_KEY)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")
    _install(monkeypatch,
             get_order_detail=_async_seq([
                 _order_detail_response(status="NEW"),      # still resting at the top-of-tick check
                 _order_detail_response(status="FILLED"),   # raced -- actually filled before the cancel landed
             ]),
             cancel_orders=_async(_cancel_orders_response(order_id="entry-order-1")))
    _run(ele.check_entry_fill_and_place_exits(db, account, plan, order))

    # management_state is untouched -- still PENDING_ENTRY, so the very
    # next tick's normal FILLED branch picks it up and protects the
    # position exactly as it always would have.
    assert order.management_state == "PENDING_ENTRY"
    db.flush()   # SessionLocal is autoflush=False -- must flush before querying write_audit()'s pending row
    rows = db.query(ExecutorAuditLog).filter_by(executor_order_id=order.id, event_type="ERROR").all()
    assert any("raced with a real fill" in (r.message or "") for r in rows)


def test_cancel_not_confirmed_in_success_list_retries_next_tick(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db, date_key=_FAR_PAST_DATE_KEY)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")
    _install(monkeypatch,
             get_order_detail=_async_seq([
                 _order_detail_response(status="NEW"),
                 _order_detail_response(status="NEW"),   # cancel not actually confirmed by the exchange
             ]),
             cancel_orders=_async(_cancel_orders_response(order_id="entry-order-1", success=False)))
    _run(ele.check_entry_fill_and_place_exits(db, account, plan, order))

    assert order.management_state == "PENDING_ENTRY"   # unchanged -- never assume cancelled
    db.flush()   # SessionLocal is autoflush=False -- must flush before querying write_audit()'s pending row
    rows = db.query(ExecutorAuditLog).filter_by(executor_order_id=order.id, event_type="ERROR").all()
    assert any("did not report" in (r.message or "") for r in rows)


def test_cancel_orders_call_failure_retries_next_tick_never_assumes_cancelled(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db, date_key=_FAR_PAST_DATE_KEY)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")

    async def _raise(self, *a, **kw):
        raise ConnectionError("simulated network failure")

    _install(monkeypatch,
             get_order_detail=_async(_order_detail_response(status="NEW")),
             cancel_orders=_raise)
    _run(ele.check_entry_fill_and_place_exits(db, account, plan, order))

    assert order.management_state == "PENDING_ENTRY"
    db.flush()   # SessionLocal is autoflush=False -- must flush before querying write_audit()'s pending row
    rows = db.query(ExecutorAuditLog).filter_by(executor_order_id=order.id, event_type="ERROR").all()
    assert any("cancel_orders call failed" in (r.message or "") for r in rows)


def test_closed_expired_is_terminal_loop_stops_touching_it(db):
    account = _ready_account(db)
    plan = _trade_plan(db)
    order = _order_row(db, account, plan, management_state="CLOSED_EXPIRED")
    _run(ele.poll_open_position(db, account, plan, order))   # must not raise, must not act
    assert order.management_state == "CLOSED_EXPIRED"


# poll_open_position -- T2 breakeven: test_premium_be_move_only_at_t2_
# touch_not_before / test_standard_never_moves_stop_at_t2 REMOVED 2026-09-15
# (Andy/DeepSeek's "delete outright" ruling, CC_QUESTION_T2_BREAKEVEN.md,
# Kabroda AI Brain repo) -- the PREMIUM-only mechanical breakeven-move-at-T2
# branch they tested is deleted from executor_live_engine.py entirely
# (order_row.tier can never be "PREMIUM" under the v2 gate, and the deleted
# tests only ever exercised it via synthetic fixtures that bypassed the
# real pipeline -- see executor_live_engine.py's own header comment, "THE
# MANAGEMENT RULE, v2"). No replacement test needed: the stop never moves
# for anyone now, which is already covered by every other test in this
# file NOT asserting a BE move.

# ------------------------------------------------------------------ poll_open_position -- closure & R math

def test_full_stop_before_t1_is_exactly_minus_one_r(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1",
                        t1_exchange_order_id="t1-1", t3_exchange_order_id="t3-1")

    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="NEW")),   # T1 never filled
              get_position=_async(_no_position_response()),                   # position now flat -- stopped out
              # 2026-09-22 audit fix: both T1 and T3 are now orphaned --
              # a real success response here so this stays a clean happy
              # path (see test_stop_before_t1_cancels_both_orphaned_limits
              # below for the actual proof of what cancel_orders was called
              # with; without SOME mock here this would silently swallow
              # _install()'s auto-raised AssertionError as a "cancel
              # failed" case and pollute this test with an unasserted
              # ERROR audit row).
              cancel_orders=_async(_cancel_orders_response_multi(["t1-1", "t3-1"])))
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
              get_position=_async(_no_position_response()),                   # flat -- runner stopped
              # 2026-09-22 audit fix: only T3 is orphaned here (T1 already
              # filled) -- see test_runner_stop_cancels_only_orphaned_t3_
              # not_t1 below for the actual proof.
              cancel_orders=_async(_cancel_orders_response(order_id="t3-1")))
    _run(ele.poll_open_position(db, account, plan, order))

    expected_t1_leg = 0.5 * (106.18 - 100.0) / 10.0
    expected_runner = 0.5 * (90.0 - 100.0) / 10.0   # -0.5
    assert order.close_reason == "RUNNER_STOP"
    assert order.realized_pnl_r == pytest.approx(expected_t1_leg + expected_runner)
    assert order.management_state == "CLOSED_RUNNER_STOP"


def test_t1_then_t3_blended_r(db, monkeypatch):
    # No cancel_orders mock installed here on purpose: T3 fills NORMALLY in
    # this scenario, so neither of the 2026-09-22 audit-fix call sites
    # should ever be reached. This test doubles as a regression guard --
    # if the cancel call were ever mistakenly wired into the normal-T3-fill
    # path, _install()'s own "should not have been called" auto-raise would
    # fire (and, per the note on the other two tests above, get silently
    # swallowed as a caught exception -- so this guard is soft, not a hard
    # failure signal on its own; the real proof that this path is excluded
    # lives in the two dedicated cancel-site tests below).
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


# ------------------------------------------------------------------ 2026-09-22 audit fix: orphaned T1/T3 cancel-on-closure
# (CC_INTERFACE.md audit item 5, "no floating orders" -- found via today's
# full checklist audit: neither closure branch ever cancelled the resting
# T1/T3 limit(s) left behind when the exchange-side stop closed the
# position first. Mirrors executor_live_e1_engine.py's own already-shipped
# _cancel_orphaned_t1() test coverage.)

def test_stop_before_t1_cancels_both_orphaned_limits(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1",
                        t1_exchange_order_id="t1-1", t3_exchange_order_id="t3-1")
    fake_cancel, calls = _recording_async(_cancel_orders_response_multi(["t1-1", "t3-1"]))

    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="NEW")),
              get_position=_async(_no_position_response()),
              cancel_orders=fake_cancel)
    _run(ele.poll_open_position(db, account, plan, order))

    assert len(calls) == 1
    (symbol, order_ids), _kw = calls[0]
    assert symbol == "BTCUSDT"
    assert set(order_ids) == {"t1-1", "t3-1"}
    assert order.close_reason == "STOP_BEFORE_T1"
    assert order.realized_pnl_r == -1.0
    assert order.management_state == "CLOSED_STOP_BEFORE_T1"


def test_runner_stop_cancels_only_orphaned_t3_not_t1(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db, tier="STANDARD")
    order = _order_row(db, account, plan, tier="STANDARD", management_state="T1_FILLED",
                        t1_status="FILLED", t1_fill_price=106.18, t1_leg_r=0.5 * (106.18 - 100.0) / 10.0,
                        entry_fill_price=100.0, position_id="pos1",
                        t1_exchange_order_id="t1-1", t3_exchange_order_id="t3-1", sl_price_current=90.0)
    fake_cancel, calls = _recording_async(_cancel_orders_response(order_id="t3-1"))

    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="NEW")),
              get_position=_async(_no_position_response()),
              cancel_orders=fake_cancel)
    _run(ele.poll_open_position(db, account, plan, order))

    assert len(calls) == 1
    (symbol, order_ids), _kw = calls[0]
    assert order_ids == ["t3-1"]   # T1 (already filled) must NOT be in this call
    assert order.management_state == "CLOSED_RUNNER_STOP"


def test_stop_before_t1_cancel_orders_exception_still_finalizes_close(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1",
                        t1_exchange_order_id="t1-1", t3_exchange_order_id="t3-1")
    fake_cancel, calls = _recording_async(exc=ConnectionError("network blip"))

    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="NEW")),
              get_position=_async(_no_position_response()),
              cancel_orders=fake_cancel)
    _run(ele.poll_open_position(db, account, plan, order))

    assert len(calls) == 1   # the attempt was made
    assert order.close_reason == "STOP_BEFORE_T1"
    assert order.realized_pnl_r == -1.0
    assert order.management_state == "CLOSED_STOP_BEFORE_T1"   # finalized anyway -- never blocked
    audit_row = db.query(ExecutorAuditLog).filter_by(
        account_id=account.id, executor_order_id=order.id, event_type="ERROR").first()
    assert audit_row is not None
    assert "cancel_orders call failed" in audit_row.message


def test_runner_stop_cancel_orders_exception_still_finalizes_close(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db, tier="STANDARD")
    order = _order_row(db, account, plan, tier="STANDARD", management_state="T1_FILLED",
                        t1_status="FILLED", t1_fill_price=106.18, t1_leg_r=0.5 * (106.18 - 100.0) / 10.0,
                        entry_fill_price=100.0, position_id="pos1",
                        t1_exchange_order_id="t1-1", t3_exchange_order_id="t3-1", sl_price_current=90.0)
    fake_cancel, calls = _recording_async(exc=ConnectionError("network blip"))

    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="NEW")),
              get_position=_async(_no_position_response()),
              cancel_orders=fake_cancel)
    _run(ele.poll_open_position(db, account, plan, order))

    assert len(calls) == 1
    assert order.management_state == "CLOSED_RUNNER_STOP"
    audit_row = db.query(ExecutorAuditLog).filter_by(
        account_id=account.id, executor_order_id=order.id, event_type="ERROR").first()
    assert audit_row is not None
    assert "cancel_orders call failed" in audit_row.message


def test_stop_before_t1_cancel_not_in_success_list_logs_missing_ids(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1",
                        t1_exchange_order_id="t1-1", t3_exchange_order_id="t3-1")
    # Both ids missing from successList entirely (an empty batch response).
    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="NEW")),
              get_position=_async(_no_position_response()),
              cancel_orders=_async({"code": 0, "data": {"successList": [], "failureList": []}, "msg": "Success"}))
    _run(ele.poll_open_position(db, account, plan, order))

    assert order.management_state == "CLOSED_STOP_BEFORE_T1"   # finalized regardless
    audit_row = db.query(ExecutorAuditLog).filter_by(
        account_id=account.id, executor_order_id=order.id, event_type="ERROR").first()
    assert audit_row is not None
    assert "t1-1" in audit_row.message and "t3-1" in audit_row.message


def test_runner_stop_cancel_not_in_success_list_logs_missing_t3(db, monkeypatch):
    account = _ready_account(db)
    plan = _trade_plan(db, tier="STANDARD")
    order = _order_row(db, account, plan, tier="STANDARD", management_state="T1_FILLED",
                        t1_status="FILLED", t1_fill_price=106.18, t1_leg_r=0.5 * (106.18 - 100.0) / 10.0,
                        entry_fill_price=100.0, position_id="pos1",
                        t1_exchange_order_id="t1-1", t3_exchange_order_id="t3-1", sl_price_current=90.0)
    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="NEW")),
              get_position=_async(_no_position_response()),
              cancel_orders=_async({"code": 0, "data": {"successList": [], "failureList": []}, "msg": "Success"}))
    _run(ele.poll_open_position(db, account, plan, order))

    assert order.management_state == "CLOSED_RUNNER_STOP"
    audit_row = db.query(ExecutorAuditLog).filter_by(
        account_id=account.id, executor_order_id=order.id, event_type="ERROR").first()
    assert audit_row is not None
    assert "t3-1" in audit_row.message
    assert "t1-1" not in audit_row.message   # T1 was never in this call's own id list at all


def test_stop_before_t1_partial_cancel_success_logs_only_the_missing_id(db, monkeypatch):
    # A genuine partial result within ONE 2-id batch call: t1-1 succeeds,
    # t3-1 doesn't. The first place a 2-id batch cancel runs inside a fully
    # unattended poll loop (executor_mechanism_test.py's own 2-id precedent
    # is a manually-driven pre-flight tool) -- worth its own explicit case.
    account = _ready_account(db)
    plan = _trade_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1",
                        t1_exchange_order_id="t1-1", t3_exchange_order_id="t3-1")
    _install(monkeypatch,
              get_order_detail=_async(_order_detail_response(status="NEW")),
              get_position=_async(_no_position_response()),
              cancel_orders=_async(_cancel_orders_response_multi(["t1-1", "t3-1"], missing=["t3-1"])))
    _run(ele.poll_open_position(db, account, plan, order))

    assert order.management_state == "CLOSED_STOP_BEFORE_T1"
    audit_row = db.query(ExecutorAuditLog).filter_by(
        account_id=account.id, executor_order_id=order.id, event_type="ERROR").first()
    assert audit_row is not None
    assert "t3-1" in audit_row.message
    assert "t1-1" not in audit_row.message   # t1-1 succeeded -- must not be named as missing


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
