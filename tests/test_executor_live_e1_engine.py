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


def _bbwp_burn_candles_4h():
    """2026-09-22 (CC_INTERFACE.md item 3): a real (not monkeypatched) 4H
    series long enough for BBWP_PERIOD(96)+BBWP_LOOKBACK(768)=864 that
    drives a genuine bbwp_burn() True -- same construction as tests/
    test_mgmt_e1_stack.py's own helper (verified there: bbwp[-1]=95.229...
    > 70 and < bbwp[-2]=96.511...), duplicated per this codebase's own
    test-fixture convention. Anchored to real wall-clock "now" since
    poll_traveler_position() calls fetch_bitunix_1h/4h with no fixed clock.

    2026-09-27: dampened the final 6 bars (was a single violent +4.0 tail
    on top of 90 alternating +-40/-39 bars) -- C5's 4H leg and BBWP now
    read the SAME Bitunix fetch (Andy ruling 14:55 CT), and the original
    violent tail also tripped c5_momentum_decay() on this data (verified
    directly against study_indicators.py's real functions, same fix
    applied in tests/test_traveler_plan_engine.py's own copy of this
    helper). A calmer final stretch (85 violent bars instead of 90, then
    6 small +2.0/-1.5 bars) keeps bbwp_burn() genuinely true while
    c5_momentum_decay() reads False on the same data."""
    closes = [100.0]
    for i in range(150):
        closes.append(closes[-1] + (12.0 if i % 2 == 0 else -11.0))
    n_phase2 = 865 - len(closes) - 85 - 6
    for i in range(n_phase2):
        closes.append(closes[-1] + (0.4 if i % 2 == 0 else -0.3))
    for i in range(85):
        closes.append(closes[-1] + (40.0 if i % 2 == 0 else -39.0))
    for delta in (2.0, -1.5, 2.0, -1.5, 2.0, -1.5):
        closes.append(closes[-1] + delta)
    last_open = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - 14400 - 60
    n = len(closes)
    return [{"close": c, "time": last_open - (n - 1 - i) * 14400} for i, c in enumerate(closes)]


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ 2026-09-30: run_executor_live_e1_loop()'s own hang resilience
# Real production incident (DeepSeek's prod-DB find, Kabroda AI Brain
# AGENT_LOG.md 18:26 CT): traveler_plan_engine.py's sibling loop froze
# solid at the 13:00 UTC lock cycle and never resumed for the rest of the
# day -- a genuine network-level hang, not a raised exception. This loop
# watches REAL, live-money positions, so it got the identical defense-in-
# depth fix (bound each order's poll in asyncio.wait_for(), and move
# db = SessionLocal() inside its own try/except) in the same pass. These
# tests drive the actual run_executor_live_e1_loop() coroutine directly
# (not poll_traveler_position() in isolation, which the rest of this file
# already covers) to prove the LOOP ITSELF survives both failure shapes.

class _StopE1Loop(Exception):
    pass


def test_executor_live_loop_survives_a_hung_poll_and_resumes_next_cycle(db, monkeypatch):
    monkeypatch.setattr(e1e, "_ROW_TIMEOUT_SECONDS", 0.05)   # keep the test fast, not 25 real seconds
    account = _ready_account(db)
    plan = _traveler_plan(db)
    _order_row(db, account, plan, entry_exchange_order_id="entry-order-1", management_state="PENDING_ENTRY")
    db.commit()

    calls = {"n": 0}

    async def _hang_once_then_noop(db_arg, account_arg, plan_arg, order_arg):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.Event().wait()   # never resolves -- simulates a genuine network hang

    monkeypatch.setattr(e1e, "poll_traveler_position", _hang_once_then_noop)

    sleeps = {"n": 0}

    async def fake_sleep(seconds):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise _StopE1Loop()

    monkeypatch.setattr(e1e.asyncio, "sleep", fake_sleep)

    async def main():
        try:
            await e1e.run_executor_live_e1_loop()
        except _StopE1Loop:
            pass

    asyncio.run(main())

    # The real assertion: a SECOND poll cycle actually happened after the
    # first one hung -- proving the loop survived instead of freezing
    # forever the way the real 09-30 incident did.
    assert calls["n"] >= 2


def test_executor_live_loop_survives_sessionlocal_itself_raising(db, monkeypatch):
    # A second, related latent bug fixed in the same pass: db = SessionLocal()
    # used to sit OUTSIDE the loop body's own try/except -- if opening a
    # session itself ever raised (e.g. a genuinely exhausted DB connection
    # pool during the same lock-cycle's own burst of DB activity), the
    # exception would propagate out of the whole while-loop body and
    # silently kill this entire background task with no restart.
    account = _ready_account(db)
    plan = _traveler_plan(db)
    _order_row(db, account, plan, entry_exchange_order_id="entry-order-1", management_state="PENDING_ENTRY")
    db.commit()

    # Isolate this test to the SessionLocal-recovery behavior only -- a real
    # poll_traveler_position() would try a real BitunixClient network call
    # on the second (successful) cycle, which this test has no interest in.
    async def _noop_poll(*a, **kw):
        return None
    monkeypatch.setattr(e1e, "poll_traveler_position", _noop_poll)

    calls = {"n": 0}
    real_session_local = database.SessionLocal

    def _explode_once_then_real():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated exhausted DB connection pool")
        return real_session_local()

    # run_executor_live_e1_loop() does `from database import SessionLocal`
    # LOCALLY, once at the top of its own function body (not a module-level
    # import) -- patching e1e.SessionLocal would do nothing, since the
    # function never reads that name. Patch the actual source instead; the
    # function's own local import re-binds fresh every time it's called,
    # so it picks this up.
    monkeypatch.setattr("database.SessionLocal", _explode_once_then_real)

    sleeps = {"n": 0}

    async def fake_sleep(seconds):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise _StopE1Loop()

    monkeypatch.setattr(e1e.asyncio, "sleep", fake_sleep)

    async def main():
        try:
            await e1e.run_executor_live_e1_loop()
        except _StopE1Loop:
            pass

    asyncio.run(main())

    assert calls["n"] >= 2   # the second SessionLocal() call actually happened -- the loop survived


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


def test_entry_fill_sends_a_real_fill_confirmed_email_with_full_manual_trade_info(db, monkeypatch):
    # 2026-09-27 (Andy ruling 14:55 CT, items 5/6): the genuinely real,
    # per-account "position opened" event -- must carry direction, entry,
    # stop, T1, risk dollars, and which account, and must only fire once
    # the exchange itself confirms the fill (this function only reaches
    # this point after get_order_detail() returns status=="FILLED").
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1",
                        stop_price=84657.86, t1_price=86311.56, risk_dollars_used=100.0)
    sent = []
    monkeypatch.setattr("notify.send_account_email", lambda subject, body, account_id: sent.append((subject, body)) or True)
    _install(monkeypatch,
             get_order_detail=_async(_order_detail_response(status="FILLED")),
             get_position=_async(_one_position_response()),
             get_trading_pairs=_async(_trading_pairs_response()),
             set_position_tpsl=_async(_tpsl_response()),
             place_order=_async(_place_order_response(order_id="t1-order")))
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))

    assert order.management_state == "ENTRY_FILLED_ORDERS_PLACED"
    assert len(sent) == 1
    subject, body = sent[0]
    assert "Real Fill Confirmed" in subject
    assert "traveler_live_test" in subject   # the account label
    assert "traveler_live_test" in body
    assert "84,657.86" in body   # stop
    assert "86,311.56" in body   # target
    assert "$100.00" in body     # risk dollars


def test_entry_fill_unprotected_state_does_not_send_the_real_fill_email(db, monkeypatch):
    # The real-fill email is a success-path confirmation -- an incomplete-
    # protection failure sends its OWN existing alert email instead (see
    # test_entry_protection_failure_lands_in_unprotected_state below),
    # never this one, since Andy should not get told "confirmed, all good"
    # about a position that isn't actually protected yet.
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")

    async def _tpsl_fail(self, *a, **kw):
        return {"code": 1, "msg": "boom", "data": {}}

    sent = []
    monkeypatch.setattr("notify.send_account_email", lambda subject, body, account_id: sent.append((subject, body)) or True)
    _install(monkeypatch,
             get_order_detail=_async(_order_detail_response(status="FILLED")),
             get_position=_async(_one_position_response()),
             get_trading_pairs=_async(_trading_pairs_response()),
             set_position_tpsl=_tpsl_fail,
             place_order=_async(_place_order_response(order_id="t1-order")))
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))

    assert order.management_state == "ENTRY_FILLED_UNPROTECTED"
    assert len(sent) == 1
    assert "Real Fill Confirmed" not in sent[0][0]
    assert "unprotected" in sent[0][0].lower()


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
    monkeypatch.setattr("notify.send_account_email", lambda subject, body, account_id: True)
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))
    assert order.management_state == "ENTRY_FILLED_UNPROTECTED"


# ------------------------------------------------------------------ item 4 (2026-09-27): reconciliation for a non-expiry exchange-side cancel

def test_entry_canceled_on_exchange_before_expiry_reconciles_not_a_loss(db, monkeypatch):
    # 2026-09-27 (Andy ruling 14:55 CT, item 4 -- the reconciliation gap
    # confirmed by both the code audit and DeepSeek's DB trace): a real
    # POST_ONLY rejection (exactly like the same-day incident) discovered
    # on the very FIRST get_order_detail() check, long before the journey
    # has expired. Must reconcile to a distinct terminal state -- never
    # silently fall through to "still resting," never CLOSED_EXPIRED (that
    # means something different: the bot's own expiry decision), never a
    # FILLED/-1R booking.
    account = _ready_account(db)
    plan = _traveler_plan(db, journey_cap_at=_FAR_FUTURE)   # nowhere near expiry
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")
    sent = []
    monkeypatch.setattr("notify.send_account_email", lambda subject, body, account_id: sent.append((subject, body)) or True)
    _install(monkeypatch, get_order_detail=_async(_order_detail_response(status="CANCELED")))
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))

    assert order.management_state == "CLOSED_ENTRY_CANCELED"
    assert order.entry_status == "CANCELED"
    assert order.close_reason == "ENTRY_CANCELED"
    # 2026-09-28 real bug, caught live by Andy: exit_reason (not just
    # close_reason) must be set too -- traveler_radar.py's own
    # _mgmt_fields() exposes THIS field as mgmt_exit_reason, and the admin
    # panel's renderTravelerState() branches on it specifically to decide
    # "closed" vs "position open (LIVE)". Without it, this exact canceled-
    # no-fill order displayed as an open live position on the radar.
    assert order.exit_reason == "ENTRY_CANCELED"
    assert order.closed_at is not None
    assert order.management_state in e1e._E1_LIVE_TERMINAL_STATES   # the poll loop must stop re-checking it
    assert len(sent) == 1
    assert "canceled on the exchange" in sent[0][0].lower()


def test_init_db_backfills_exit_reason_on_historical_entry_canceled_rows(db):
    # 2026-09-28: the test above proves the CODE fix for new rows going
    # forward. But CLOSED_ENTRY_CANCELED is a genuine terminal state --
    # e1e._E1_LIVE_TERMINAL_STATES correctly stops the poll loop from
    # ever revisiting it -- so the two real rows written by the buggy
    # code BEFORE this fix (2026-09-27's two canceled entry orders, caught
    # live by Andy on the radar screen still showing "position open
    # (LIVE)") could never self-correct just by deploying the code fix.
    # database.py::init_db() has a one-time idempotent backfill for
    # exactly this. Simulate a pre-fix historical row via raw SQL
    # (bypassing the ORM/code path entirely, the same way the real buggy
    # code once did), then prove re-running init_db() corrects it.
    from sqlalchemy import text as _text

    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="CLOSED_ENTRY_CANCELED",
                        close_reason="ENTRY_CANCELED")
    db.commit()
    order_id = order.id

    with database.engine.begin() as conn:
        conn.execute(_text("UPDATE executor_orders SET exit_reason = NULL WHERE id = :id"), {"id": order_id})
    db.expire_all()
    assert db.query(ExecutorOrder).filter_by(id=order_id).first().exit_reason is None   # confirm the pre-fix shape

    database.init_db()   # idempotent -- must run the one-time backfill

    db.expire_all()
    row = db.query(ExecutorOrder).filter_by(id=order_id).first()
    assert row.exit_reason == "ENTRY_CANCELED"


def test_init_db_backfill_does_not_touch_rows_with_a_different_real_exit_reason(db):
    # Guard against an overly-broad backfill: a row that's
    # CLOSED_ENTRY_CANCELED but already has ITS OWN exit_reason (however
    # that happened) must be left alone -- the backfill's WHERE clause is
    # "... AND exit_reason IS NULL", not a blind overwrite.
    from sqlalchemy import text as _text

    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="CLOSED_ENTRY_CANCELED",
                        close_reason="ENTRY_CANCELED", exit_reason="SOMETHING_ELSE")
    db.commit()
    order_id = order.id

    database.init_db()

    db.expire_all()
    row = db.query(ExecutorOrder).filter_by(id=order_id).first()
    assert row.exit_reason == "SOMETHING_ELSE"


# ------------------------------------------------------------------ 2026-10-01: one-time manual close-out of traveler_plans id=14
# Andy's own ruling ("let's just go with 1"): the 2026-09-30 traveler-
# engine hang (fixed site commit 9368030) meant a real cross at 13:05 UTC
# that day was never evaluated; by the time polling resumed, price had
# moved back inside the range, and gate_traveler.py's own cross check
# (_confirmed_side(), latest-bar-only) can never retroactively catch a
# missed cross. Rather than leave that one journey polling forever with
# no path to resolution, close it out manually via a narrowly-scoped,
# idempotent one-time fix -- NOT a general "close stale WAITING_CROSS
# rows" rule, which would be a real behavior change to the gate itself.

def test_init_db_closes_out_the_one_known_missed_cross_plan_14(db):
    plan = TravelerPlan(
        id=14, symbol="BTC/USDT", date_key="2026-09-30", session_id="us_ny_futures",
        status="WAITING_CROSS", breakout_trigger=85491.0, breakdown_trigger=82937.15,
        r30_high=85491.0, r30_low=83832.1, rsi_4h_at_lock=51.6,
    )
    db.add(plan)
    db.commit()

    database.init_db()

    db.expire_all()
    row = db.query(TravelerPlan).filter_by(id=14).first()
    assert row.status == "DONE"
    assert "missed cross" in row.last_transition_reason.lower()
    assert "9368030" in row.last_transition_reason   # cites the actual hang fix, not a vague note
    # Never touched the real journey fields -- it genuinely never crossed,
    # and fabricating cross/direction data here would be dishonest.
    assert row.direction is None
    assert row.cross_time is None


def test_init_db_close_out_fix_does_not_touch_a_different_id_even_with_the_same_status(db):
    # Guard against an overly-broad match: the fix is scoped to id=14
    # specifically, not "any WAITING_CROSS row" -- a different plan that
    # happens to also be WAITING_CROSS (a real, still-watching journey on
    # some other day) must be left completely alone.
    other_plan = TravelerPlan(
        symbol="BTC/USDT", date_key="2026-10-02", session_id="us_ny_futures",
        status="WAITING_CROSS", breakout_trigger=90000.0, breakdown_trigger=88000.0,
        r30_high=90000.0, r30_low=88500.0, rsi_4h_at_lock=55.0,
    )
    db.add(other_plan)
    db.commit()
    other_id = other_plan.id
    assert other_id != 14   # sanity: a fresh-inserted row must not collide with the one we're protecting

    database.init_db()

    db.expire_all()
    row = db.query(TravelerPlan).filter_by(id=other_id).first()
    assert row.status == "WAITING_CROSS"   # untouched
    assert row.last_transition_reason is None


def test_init_db_close_out_fix_is_idempotent_once_already_done(db):
    plan = TravelerPlan(
        id=14, symbol="BTC/USDT", date_key="2026-09-30", session_id="us_ny_futures",
        status="DONE", last_transition_reason="some other, already-correct reason",
        breakout_trigger=85491.0, breakdown_trigger=82937.15,
        r30_high=85491.0, r30_low=83832.1, rsi_4h_at_lock=51.6,
    )
    db.add(plan)
    db.commit()

    database.init_db()   # must not overwrite an already-DONE row's reason

    db.expire_all()
    row = db.query(TravelerPlan).filter_by(id=14).first()
    assert row.status == "DONE"
    assert row.last_transition_reason == "some other, already-correct reason"


def test_entry_canceled_reconciliation_never_books_a_fill_or_loss(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db, journey_cap_at=_FAR_FUTURE)
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1")
    monkeypatch.setattr("notify.send_account_email", lambda subject, body, account_id: True)
    _install(monkeypatch, get_order_detail=_async(_order_detail_response(status="CANCELED")))
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))

    assert order.entry_fill_price is None   # never a fill
    assert order.realized_pnl_r is None     # never a booked loss


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


# ------------------------------------------------------------------ 2026-10-06 (R1 re-arm): a re-arm order's expiry reads rearm_entry_expires_at
# (the 90-bar/7.5h window), NOT the primary's own 7-day journey_cap_at --
# see _plan_has_expired()'s own docstring. These prove the two caps are
# genuinely independent, not just "same field, different name".

def test_rearm_order_expires_on_its_own_90_bar_cap_even_with_a_far_future_journey_cap(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db, journey_cap_at=_FAR_FUTURE)
    plan.rearm_entry_expires_at = _FAR_PAST
    db.flush()
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1", is_rearm=True)
    _install(monkeypatch,
             get_order_detail=_async_seq([_order_detail_response(status="NEW"), _order_detail_response(status="CANCELED")]),
             cancel_orders=_async(_cancel_orders_response(order_id="entry-order-1")))
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))
    assert order.management_state == "CLOSED_EXPIRED"
    assert order.close_reason == "EXPIRED"


def test_rearm_order_not_expired_while_within_its_90_bar_cap_even_with_a_past_journey_cap(db, monkeypatch):
    # cancel_orders is tracked (not left unset) because
    # _cancel_expired_traveler_entry_order() swallows ANY cancel_orders
    # exception into an audit-log write and a silent return -- leaving
    # management_state at the same PENDING_ENTRY a correctly-not-expired
    # order would also show. Asserting management_state alone would pass
    # even if the expiry branch wrongly fired and then failed to cancel;
    # asserting cancel_orders was never called is what actually proves the
    # expiry branch didn't fire at all.
    account = _ready_account(db)
    plan = _traveler_plan(db, journey_cap_at=_FAR_PAST)   # primary's own cap already elapsed
    plan.rearm_entry_expires_at = _FAR_FUTURE              # re-arm's own cap has not
    db.flush()
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1", is_rearm=True)
    cancel_calls = []

    async def _track_cancel(self, *a, **kw):
        cancel_calls.append((a, kw))
        return _cancel_orders_response(order_id="entry-order-1")

    _install(monkeypatch, get_order_detail=_async(_order_detail_response(status="NEW")), cancel_orders=_track_cancel)
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))
    assert order.management_state == "PENDING_ENTRY"   # still resting -- rearm cap governs, not journey_cap_at
    assert cancel_calls == []   # expiry/cancel path must never have fired


def test_rearm_order_with_no_rearm_expiry_set_never_expires_on_journey_cap_alone(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db, journey_cap_at=_FAR_PAST)   # primary's own cap already elapsed
    assert plan.rearm_entry_expires_at is None
    order = _order_row(db, account, plan, entry_exchange_order_id="entry-order-1", is_rearm=True)
    cancel_calls = []

    async def _track_cancel(self, *a, **kw):
        cancel_calls.append((a, kw))
        return _cancel_orders_response(order_id="entry-order-1")

    _install(monkeypatch, get_order_detail=_async(_order_detail_response(status="NEW")), cancel_orders=_track_cancel)
    _run(e1e.check_traveler_entry_fill_and_protect(db, account, plan, order))
    assert order.management_state == "PENDING_ENTRY"
    assert cancel_calls == []   # expiry/cancel path must never have fired


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


def test_normal_t1_fill_sends_a_management_event_email(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    sent = []
    monkeypatch.setattr("notify.send_account_email", lambda subject, body, account_id: sent.append((subject, body)) or True)
    _install(monkeypatch,
             get_position=_async(_no_position_response()),
             get_order_detail=_async(_order_detail_response(status="FILLED", order_id="t1-1")))
    _run(e1e.poll_traveler_position(db, account, plan, order))

    assert len(sent) == 1
    subject, body = sent[0]
    assert "Closed" in subject
    assert "target hit (T1)" in body
    assert "Real order -- live money." in body   # LIVE, not the DRY_RUN caveat
    assert "approximated" not in body.lower()  # T1 is a real resting-limit fill, never approximated


def test_stop_exit_email_has_no_approximated_caveat(db, monkeypatch):
    # Validated mapping (Plan-agent pass, 2026-09-23): approximated is
    # exit_reason in {C5_EXIT, BBWP_EXIT, TIME} on LIVE only -- STOP fills
    # at its own exchange trigger, same non-flagged convention v2 already
    # uses for STOP_BEFORE_T1. Getting this wrong would mislabel a real,
    # reliable exit price as an approximation.
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    sent = []
    monkeypatch.setattr("notify.send_account_email", lambda subject, body, account_id: sent.append((subject, body)) or True)
    _install(monkeypatch,
             get_position=_async(_no_position_response()),
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")),
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))
    _run(e1e.poll_traveler_position(db, account, plan, order))

    assert len(sent) == 1
    subject, body = sent[0]
    assert "stop hit" in body
    assert "approximated" not in body.lower()


def test_c5_exit_email_has_the_approximated_caveat(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    sent = []
    monkeypatch.setattr("notify.send_account_email", lambda subject, body, account_id: sent.append((subject, body)) or True)
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h, candles_4h_bbwp=None: (True, False))
    _install(monkeypatch,
             get_position=_async_seq([_one_position_response(), _no_position_response()]),
             close_position=_async(_close_position_response()),
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")),
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))

    async def _fake_1h(symbol, target_bars=200):
        return _fake_candles()

    async def _fake_4h(symbol, target_bars=200):
        return _fake_candles()

    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_1h", _fake_1h)
    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_4h", _fake_4h)
    monkeypatch.setattr(e1e, "_current_live_price", lambda symbol: asyncio.sleep(0, result=104.5))

    _run(e1e.poll_traveler_position(db, account, plan, order))

    assert len(sent) == 1
    subject, body = sent[0]
    assert "momentum-decay exhaustion (C5) exit" in body
    assert "approximated" in body.lower()


def test_management_event_email_failure_never_blocks_the_real_bookkeeping(db, monkeypatch):
    """The single most important correctness rule for this change (Plan-
    agent validation, 2026-09-23): a bug in the new email-dispatch code
    must never roll back the closure's own real DB writes (management_
    state, the audit row, record_trade_result()) that already committed
    for this tick -- run_executor_live_e1_loop() commits once per order
    per tick and rolls back the WHOLE tick on any uncaught exception."""
    def _boom(subject, body, account_id):
        raise RuntimeError("SMTP exploded")
    monkeypatch.setattr("notify.send_account_email", _boom)

    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    _install(monkeypatch,
             get_position=_async(_no_position_response()),
             get_order_detail=_async(_order_detail_response(status="FILLED", order_id="t1-1")))
    # Must not raise -- this call itself is the real assertion.
    _run(e1e.poll_traveler_position(db, account, plan, order))

    assert order.management_state == "CLOSED_T1"
    assert order.realized_pnl_r == pytest.approx((106.18 - 100.0) / 10.0)
    db.flush()
    audit_rows = db.query(ExecutorAuditLog).filter_by(executor_order_id=order.id, event_type="POSITION_CLOSED").all()
    ledger_rows = db.query(ExecutorAuditLog).filter_by(account_id=account.id, event_type="TRADE_RESULT_RECORDED").all()
    assert len(audit_rows) == 1     # the pre-existing write_audit() call, unaffected by the email exception
    assert len(ledger_rows) == 1    # record_trade_result(), which runs BEFORE the email dispatch, still ran


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
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h, candles_4h_bbwp=None: (True, False))
    _install(monkeypatch,
             get_position=_async_seq([_one_position_response(), _no_position_response()]),
             close_position=_async(_close_position_response()),
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")),
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))

    async def _fake_1h(symbol, target_bars=200):
        return _fake_candles()

    async def _fake_4h(symbol, target_bars=200):
        return _fake_candles()

    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_1h", _fake_1h)
    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_4h", _fake_4h)
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
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h, candles_4h_bbwp=None: (False, True))
    _install(monkeypatch,
             get_position=_async_seq([_one_position_response(), _no_position_response()]),
             close_position=_async(_close_position_response()),
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")),
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))

    async def _fake_candles_fn(symbol, target_bars=200):
        return _fake_candles()

    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_1h", _fake_candles_fn)
    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_4h", _fake_candles_fn)
    monkeypatch.setattr(e1e, "_current_live_price", lambda symbol: asyncio.sleep(0, result=95.5))

    _run(e1e.poll_traveler_position(db, account, plan, order))
    assert order.management_state == "CLOSED_BBWP_EXIT"
    assert order.bbwp_fired is True


def test_bbwp_fires_through_the_real_math_on_its_own_bitunix_feed(db, monkeypatch):
    # 2026-09-22 (CC_INTERFACE.md item 3): unlike the test above, this does
    # NOT mock check_c5_or_bbwp -- it is the one test that would actually
    # catch a forgotten `candles_4h_bbwp=` at this module's own call site.
    # 2026-09-27: candles_1h stays flat/no-signal (its own separate
    # fetch_bitunix_1h call, unaffected by this same-day change); the real
    # BBWP trigger arrives via fetch_bitunix_4h, which C5's own 4H leg now
    # ALSO reads (Andy ruling 14:55 CT collapsed the two feeds into one --
    # see _bbwp_burn_candles_4h()'s own updated docstring for why its tail
    # needed dampening once C5 started reading this same data).
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    _install(monkeypatch,
             get_position=_async_seq([_one_position_response(), _no_position_response()]),
             close_position=_async(_close_position_response()),
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")),
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))

    async def _fake_flat(symbol, target_bars=200):
        return _fake_candles()

    async def _fake_bitunix(symbol, target_bars=900):
        return _bbwp_burn_candles_4h()

    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_1h", _fake_flat)
    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_4h", _fake_bitunix)
    monkeypatch.setattr(e1e, "_current_live_price", lambda symbol: asyncio.sleep(0, result=95.5))

    _run(e1e.poll_traveler_position(db, account, plan, order))
    assert order.management_state == "CLOSED_BBWP_EXIT"
    assert order.bbwp_fired is True
    assert order.c5_fired is False


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
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h, candles_4h_bbwp=None: (False, False))
    _install(monkeypatch,
             get_position=_async_seq([_one_position_response(), _no_position_response()]),
             close_position=_async(_close_position_response()),
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")),
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))

    async def _fake_candles_fn(symbol, target_bars=200):
        return _fake_candles()

    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_1h", _fake_candles_fn)
    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_4h", _fake_candles_fn)
    monkeypatch.setattr(e1e, "_current_live_price", lambda symbol: asyncio.sleep(0, result=101.0))

    _run(e1e.poll_traveler_position(db, account, plan, order))
    assert order.management_state == "CLOSED_TIME"


# ------------------------------------------------------------------ (f) race: T1 fills during the market-close confirmation window

def test_c5_fires_but_t1_actually_filled_first_reconciled_as_t1(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h, candles_4h_bbwp=None: (True, False))
    _install(monkeypatch,
             get_position=_async_seq([_one_position_response(), _no_position_response()]),
             close_position=_async(_close_position_response()),
             get_order_detail=_async(_order_detail_response(status="FILLED", order_id="t1-1")),   # T1 actually filled
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))

    async def _fake_candles_fn(symbol, target_bars=200):
        return _fake_candles()

    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_1h", _fake_candles_fn)
    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_4h", _fake_candles_fn)

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
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h, candles_4h_bbwp=None: (True, False))

    async def _raise(self, *a, **kw):
        raise ConnectionError("simulated network failure")

    _install(monkeypatch, get_position=_async(_one_position_response()), close_position=_raise)

    async def _fake_candles_fn(symbol, target_bars=200):
        return _fake_candles()

    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_1h", _fake_candles_fn)
    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_4h", _fake_candles_fn)

    _run(e1e.poll_traveler_position(db, account, plan, order))
    assert order.management_state == "ENTRY_FILLED_ORDERS_PLACED"   # untouched -- retry next tick


# (h) engine-selection guard -- test_e1_order_never_reaches_the_split_
# engines_query()/test_split_order_never_reaches_the_e1_engines_query()
# removed 2026-09-24 (V2 Crown retirement, Step 3f-ii) along with
# executor_live_engine.py (the V2/SPLIT engine) itself -- both tests
# existed only to prove the two live engines' polling queries never
# cross-contaminate on the same ExecutorOrder table. Once there's only
# one engine, that premise is moot, not just broken by the import going
# away.


# ------------------------------------------------------------------ (i) exit price honesty

def test_market_close_exit_price_uses_last_known_live_price_not_fabricated(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h, candles_4h_bbwp=None: (True, False))
    _install(monkeypatch,
             get_position=_async_seq([_one_position_response(), _no_position_response()]),
             close_position=_async(_close_position_response()),
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")),
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))

    async def _fake_candles_fn(symbol, target_bars=200):
        return _fake_candles()

    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_1h", _fake_candles_fn)
    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_4h", _fake_candles_fn)
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

    async def _fake_1h(symbol, target_bars=200):
        return _rising_bars(3600, forming_dip=6.0)

    async def _fake_4h(symbol, target_bars=200):
        return _rising_bars(14400)

    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_1h", _fake_1h)
    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_4h", _fake_4h)

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

    async def _fake_build(db_, plan_, account_, risk_, is_rearm=False):
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

    async def _fake_build(db_, plan_, account_, risk_, is_rearm=False):
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


# ------------------------------------------------------------------ 2026-10-06 (R1 re-arm): _advance_live_rearm_watch() LIVE wrapper
# gate_traveler.advance_rearm_watch()'s own math is already fully unit-
# tested (tests/test_gate_traveler.py) and the DRY_RUN walk's own call to
# it is covered end-to-end (tests/test_traveler_plan_engine.py's rearm
# full-walk test). These tests cover ONLY what's new and LIVE-specific:
# that the wrapper fetches candles, applies gate_traveler's returned
# updates to the real TravelerPlan row, and fires executor_engine.
# process_traveler_rearm_cross() on (and only on) a genuine REARM_WATCH ->
# REARM_WAITING_TOUCH transition -- same "mock at the already-tested
# pure-function boundary" discipline this file already uses for mgmt_e1_
# stack.check_c5_or_bbwp() in the C5/BBWP tests above.

def _fake_rearm_candle_fetchers(monkeypatch, n=20):
    async def _fake_5m(symbol, target_bars=310):
        return _fake_candles(n=n)

    async def _fake_1h(symbol, target_bars=200):
        return _fake_candles(n=n)

    async def _fake_4h(symbol, target_bars=200):
        return _fake_candles(n=n)

    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_5m", _fake_5m)
    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_1h", _fake_1h)
    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_4h", _fake_4h)
    monkeypatch.setattr(e1e.market_data, "confirmed_5m_closes", lambda candles, now_ts=None: candles)


def test_rearm_watch_transition_to_waiting_touch_fires_the_rearm_cross_hook(db, monkeypatch):
    plan = _traveler_plan(db, status="FILLED")
    plan.rearm_status = "REARM_WATCH"
    db.flush()
    _fake_rearm_candle_fetchers(monkeypatch)
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h, now_ts=None, candles_4h_bbwp=None: (False, False))
    monkeypatch.setattr(e1e.gate_traveler, "advance_rearm_watch", lambda *a, **kw: {
        "rearm_status": "REARM_WAITING_TOUCH",
        "rearm_cross_time": datetime.datetime(2026, 9, 21, 15, 0, tzinfo=datetime.timezone.utc),
        "rearm_cross_price": 101.5, "rearm_last_transition_reason": "re-crossed, tercile passed",
    })

    cross_calls = []

    async def _fake_rearm_cross(db_, plan_):
        cross_calls.append(plan_.id)
    monkeypatch.setattr(executor_engine, "process_traveler_rearm_cross", _fake_rearm_cross)
    monkeypatch.setattr("notify.send_admin_email", lambda *a, **kw: None)

    _run(e1e._advance_live_rearm_watch(db, plan, datetime.datetime.now(datetime.timezone.utc)))

    assert plan.rearm_status == "REARM_WAITING_TOUCH"
    assert plan.rearm_cross_price == 101.5
    assert cross_calls == [plan.id]


def test_rearm_watch_no_update_makes_no_change_and_fires_no_hook(db, monkeypatch):
    plan = _traveler_plan(db, status="FILLED")
    plan.rearm_status = "REARM_WATCH"
    db.flush()
    _fake_rearm_candle_fetchers(monkeypatch)
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h, now_ts=None, candles_4h_bbwp=None: (True, False))
    monkeypatch.setattr(e1e.gate_traveler, "advance_rearm_watch", lambda *a, **kw: {})

    cross_calls = []

    async def _fake_rearm_cross(db_, plan_):
        cross_calls.append(plan_.id)
    monkeypatch.setattr(executor_engine, "process_traveler_rearm_cross", _fake_rearm_cross)

    _run(e1e._advance_live_rearm_watch(db, plan, datetime.datetime.now(datetime.timezone.utc)))

    assert plan.rearm_status == "REARM_WATCH"   # unchanged
    assert cross_calls == []


def test_rearm_watch_window_closed_transition_does_not_fire_the_cross_hook(db, monkeypatch):
    # A transition DID happen (REARM_WATCH -> REARM_WINDOW_CLOSED), but it's
    # not the specific REARM_WAITING_TOUCH transition that means "place the
    # real order" -- the cross hook must stay silent.
    plan = _traveler_plan(db, status="FILLED")
    plan.rearm_status = "REARM_WATCH"
    db.flush()
    _fake_rearm_candle_fetchers(monkeypatch)
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h, now_ts=None, candles_4h_bbwp=None: (False, False))
    monkeypatch.setattr(e1e.gate_traveler, "advance_rearm_watch", lambda *a, **kw: {
        "rearm_status": "REARM_WINDOW_CLOSED", "rearm_last_transition_reason": "window closed, no re-cross",
    })

    cross_calls = []

    async def _fake_rearm_cross(db_, plan_):
        cross_calls.append(plan_.id)
    monkeypatch.setattr(executor_engine, "process_traveler_rearm_cross", _fake_rearm_cross)
    monkeypatch.setattr("notify.send_admin_email", lambda *a, **kw: None)

    _run(e1e._advance_live_rearm_watch(db, plan, datetime.datetime.now(datetime.timezone.utc)))

    assert plan.rearm_status == "REARM_WINDOW_CLOSED"
    assert cross_calls == []


# ------------------------------------------------------------------ 2026-10-06 (R1 re-arm): the REARM_WATCH-entry hook inside _finalize_traveler_close()
# mgmt_e1_stack.start_rearm_watch_if_eligible() itself is already covered
# by its own module's test (test_mgmt_e1_stack.py / this session's own
# mutation-testing pass on it). These tests cover the LIVE wiring: that a
# real C5_EXIT closure flips TravelerPlan.rearm_status via this hook, and
# that STOP/T1/TIME closures (not C5_EXIT) never do.

def test_live_c5_exit_closure_enters_rearm_watch(db, monkeypatch):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    order = _order_row(db, account, plan, management_state="ENTRY_FILLED_ORDERS_PLACED",
                        entry_fill_price=100.0, position_id="pos1", t1_exchange_order_id="t1-1")
    monkeypatch.setattr(mgmt_e1_stack, "check_c5_or_bbwp", lambda c1h, c4h, candles_4h_bbwp=None: (True, False))
    _install(monkeypatch,
             get_position=_async_seq([_one_position_response(), _no_position_response()]),
             close_position=_async(_close_position_response()),
             get_order_detail=_async(_order_detail_response(status="NEW", order_id="t1-1")),
             cancel_orders=_async(_cancel_orders_response(order_id="t1-1")))

    async def _fake_1h(symbol, target_bars=200):
        return _fake_candles()

    async def _fake_4h(symbol, target_bars=200):
        return _fake_candles()

    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_1h", _fake_1h)
    monkeypatch.setattr(e1e.market_data, "fetch_bitunix_4h", _fake_4h)
    monkeypatch.setattr(e1e, "_current_live_price", lambda symbol: asyncio.sleep(0, result=104.5))
    monkeypatch.setattr("notify.send_admin_email", lambda *a, **kw: None)

    _run(e1e.poll_traveler_position(db, account, plan, order))
    assert order.management_state == "CLOSED_C5_EXIT"
    assert plan.rearm_status == "REARM_WATCH"


def test_live_stop_closure_does_not_enter_rearm_watch(db, monkeypatch):
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
    assert plan.rearm_status is None


# ------------------------------------------------------------------ 2026-10-06 (R1 re-arm): the real DB-level UniqueConstraint
# Distinct from executor_plan_builder.py's own application-level dedup
# check (already mutation-tested there) -- this proves the actual
# SQLAlchemy-declared schema itself. The `db` fixture builds a real,
# file-backed SQLite database via database.init_db(), so a UniqueConstraint
# collision here is a genuine sqlite integrity error, not a mock.
# ExecutorOrder.__table_args__ now includes is_rearm in both unique
# constraints -- this must allow exactly one primary (is_rearm=False) and
# one re-arm (is_rearm=True) row per (plan, account), and reject a second
# of either.

def test_db_allows_one_primary_and_one_rearm_order_per_plan_and_account(db):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    _order_row(db, account, plan, is_rearm=False)
    _order_row(db, account, plan, is_rearm=True)   # must not raise
    db.commit()
    assert db.query(ExecutorOrder).filter_by(traveler_plan_id=plan.id, account_id=account.id).count() == 2


def test_db_rejects_a_second_rearm_order_for_the_same_plan_and_account(db):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    _order_row(db, account, plan, is_rearm=True)
    db.commit()
    with pytest.raises(Exception):
        _order_row(db, account, plan, is_rearm=True)
    db.rollback()


def test_db_rejects_a_second_primary_order_for_the_same_plan_and_account(db):
    account = _ready_account(db)
    plan = _traveler_plan(db)
    _order_row(db, account, plan, is_rearm=False)
    db.commit()
    with pytest.raises(Exception):
        _order_row(db, account, plan, is_rearm=False)
    db.rollback()
