"""
Integration coverage for traveler_plan_engine.py's monitoring loop -- runs
the ACTUAL run_traveler_plan_loop() coroutine against monkeypatched
exchange calls, same harness style as tests/test_trade_plan_engine.py.
Exercises the real production code path end to end: WAITING_CROSS ->
WAITING_TOUCH -> FILLED -> the executor hook (a real GATE_TRAVELER
account) -> MGMT_E1_STACK's own poll closing the resulting order.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_traveler_plan_engine.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import datetime as dt
from datetime import timezone, timedelta

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, TravelerPlan, ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorSizingPolicy
import traveler_plan_engine as tpe
import executor_accounts as ea
import notify


def _clean_db_files():
    for path in ["kabroda_test_traveler_plan_engine.db", "kabroda_test_traveler_plan_engine.db-journal",
                 "kabroda_test_traveler_plan_engine.db-shm", "kabroda_test_traveler_plan_engine.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


class _StopLoop(Exception):
    pass


def _c5m(close, ts, high=None, low=None):
    return {"close": close, "high": high if high is not None else close, "low": low if low is not None else close, "time": ts}


def _h4_long_skip(cross_epoch, closed_bars=20):
    # 2026-09-21 (CC_WORK_ORDER_D1_RSI_AT_CROSS.md): the tercile skip now
    # reads gate_traveler.rsi_at_cross(candles_4h, cross_time), not a plan-
    # level rsi_4h_at_lock field -- same tuned parameters as tests/
    # test_gate_traveler.py's LONG_SKIP_H4 (verified there -> ~46.87, below
    # FULL_D1_CUTS["LONG"][0]=51.49), just re-anchored to this test's own
    # cross epoch (the construction is translation-invariant).
    start = cross_epoch - (closed_bars + 2) * 14400
    closes = [100.0]
    for i in range(closed_bars - 1):
        closes.append(closes[-1] + (0.1 if i % 2 else -0.1))
    return [{"close": c, "time": start + i * 14400} for i, c in enumerate(closes)]


@pytest.fixture
def poll_env(monkeypatch):
    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", Fernet.generate_key().decode("utf-8"))
    _clean_db_files()
    database.init_db()
    db = SessionLocal()
    for model in (TravelerPlan, ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorSizingPolicy):
        db.query(model).delete()
    db.commit()
    db.close()

    DEFAULT_DATE_KEY = (dt.datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

    def make_plan(symbol="BTC/USDT", date_key=None, session_id="us_ny_futures", **kwargs):
        date_key = date_key or DEFAULT_DATE_KEY
        db = SessionLocal()
        defaults = dict(
            symbol=symbol, date_key=date_key, session_id=session_id,
            status="WAITING_CROSS", breakout_trigger=100.0, breakdown_trigger=90.0,
            r30_high=100.0, r30_low=90.0, rsi_4h_at_lock=55.0,
        )
        defaults.update(kwargs)
        row = TravelerPlan(**defaults)
        db.add(row)
        db.commit()
        db.close()

    def make_traveler_account(gate_profile="GATE_TRAVELER", mgmt_profile="MGMT_E1_STACK", risk_last_usd=100.0):
        db = SessionLocal()
        account = ea.create_account(db, user_id=1, label="traveler_test_account")
        db.flush()
        ea.set_account_profile(db, account, gate_profile=gate_profile, mgmt_profile=mgmt_profile, by="test")
        state = ea.get_or_init_risk_state(db, account)
        state.risk_last_usd = risk_last_usd
        db.commit()
        account_id = account.id
        db.close()
        return account_id

    def run_polls(candles_5m_by_symbol=None, candles_1h_by_symbol=None, candles_4h_by_symbol=None, polls=1):
        candles_5m_by_symbol = candles_5m_by_symbol or {}
        candles_1h_by_symbol = candles_1h_by_symbol or {}
        candles_4h_by_symbol = candles_4h_by_symbol or {}

        async def fake_5m(symbol, limit=310):
            return candles_5m_by_symbol.get(symbol, [])

        async def fake_1h(symbol, limit=100):
            return candles_1h_by_symbol.get(symbol, [])

        async def fake_4h(symbol, limit=100):
            return candles_4h_by_symbol.get(symbol, [])

        sleeps = {"n": 0}

        async def fake_sleep(seconds):
            sleeps["n"] += 1
            if sleeps["n"] >= polls:
                raise _StopLoop()

        monkeypatch.setattr(tpe.market_data, "fetch_live_5m", fake_5m)
        monkeypatch.setattr(tpe.market_data, "fetch_live_1h", fake_1h)
        monkeypatch.setattr(tpe.market_data, "fetch_live_4h", fake_4h)
        monkeypatch.setattr(tpe.asyncio, "sleep", fake_sleep)

        async def main():
            try:
                await tpe.run_traveler_plan_loop()
            except _StopLoop:
                pass

        asyncio.run(main())

    def get_plan(symbol="BTC/USDT"):
        db = SessionLocal()
        row = db.query(TravelerPlan).filter(TravelerPlan.symbol == symbol).first()
        db.close()
        return row

    def get_orders(traveler_plan_id=None):
        db = SessionLocal()
        q = db.query(ExecutorOrder)
        if traveler_plan_id is not None:
            q = q.filter_by(traveler_plan_id=traveler_plan_id)
        rows = q.all()
        db.expunge_all()
        db.close()
        return rows

    yield {
        "make_plan": make_plan, "make_traveler_account": make_traveler_account,
        "run_polls": run_polls, "get_plan": get_plan, "get_orders": get_orders,
    }

    db = SessionLocal()
    for model in (TravelerPlan, ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorSizingPolicy):
        db.query(model).delete()
    db.commit()
    db.close()
    _clean_db_files()


def test_waiting_cross_advances_to_waiting_touch_on_a_real_cross(poll_env):
    poll_env["make_plan"]()
    candles = [_c5m(95.0, 1700000000 + i * 300) for i in range(5)] + [_c5m(105.0, 1700000000 + 5 * 300)]
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "WAITING_TOUCH"
    assert row.direction == "LONG"
    assert row.tercile_skipped is False


def test_waiting_cross_no_cross_yet_stays_waiting(poll_env):
    poll_env["make_plan"]()
    candles = [_c5m(95.0, 1700000000 + i * 300) for i in range(5)]  # never crosses
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "WAITING_CROSS"


def test_waiting_touch_fills_and_fires_the_executor_hook_for_a_gate_traveler_account(poll_env):
    # BTC-scale prices with a realistic, tight box (0.6% of price) -- a
    # toy 100/90-style box (10% of price) fails the liquidation-vs-stop
    # safety check at the default 10x leverage baseline (the stop distance
    # exceeds a real leverage's liquidation buffer), which is a test-data
    # realism issue, not a code bug -- found while writing this test.
    account_id = poll_env["make_traveler_account"]()
    ct = 1700000000
    poll_env["make_plan"](
        status="WAITING_TOUCH", direction="LONG",
        breakout_trigger=50000.0, breakdown_trigger=49700.0, opposite_trigger=49700.0,
        stop_price=49664.0, t1_price=50300.0, rsi_4h_at_cross=55.0,
        cross_time=dt.datetime.fromtimestamp(ct, tz=timezone.utc),
        journey_cap_at=dt.datetime.fromtimestamp(ct, tz=timezone.utc) + timedelta(days=7),
    )
    candles = [
        _c5m(50100.0, ct),              # cross bar itself -- skipped
        _c5m(50050.0, ct + 300),        # still above trigger
        _c5m(49900.0, ct + 600),        # wick touches the trigger (low <= 50000) -- FILL
    ]
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "FILLED"
    assert row.fill_price == 50000.0   # the trigger, not the touching bar's own close (49900.0)

    orders = poll_env["get_orders"](traveler_plan_id=row.id)
    assert len(orders) == 1
    order = orders[0]
    assert order.account_id == account_id
    assert order.decision == "WOULD_PLACE"
    assert order.gate_profile_used == "GATE_TRAVELER"
    assert order.mgmt_profile_used == "MGMT_E1_STACK"
    assert order.entry_fill_price == 50000.0
    assert order.stop_price == 49664.0
    assert order.t1_price == 50300.0
    assert order.management_state == "ENTRY_FILLED_ORDERS_PLACED"
    # F_A (2026-09-21: reads rsi_4h_at_cross, not rsi_4h_at_lock -- CC_WORK_
    # ORDER_D1_RSI_AT_CROSS.md): 55.0 is not extreme for LONG (needs >=80) -> 0.5
    assert order.sizing_multiplier_used == pytest.approx(0.5)
    assert order.qty == pytest.approx((100.0 * 0.5) / abs(50000.0 - 49664.0))


def test_waiting_touch_fills_on_a_wick_only_touch_through_the_full_loop(poll_env):
    # The unit-level proof lives in test_gate_traveler.py; this exercises the
    # SAME wick-only scenario (close stays on the far side of the trigger,
    # only the wick touches it) through the REAL production loop end to end
    # -- proving the fill actually threads through to a real order at the
    # trigger price, not just that the pure function returns the right dict.
    account_id = poll_env["make_traveler_account"]()
    ct = 1700000000
    poll_env["make_plan"](
        status="WAITING_TOUCH", direction="LONG",
        breakout_trigger=50000.0, breakdown_trigger=49700.0, opposite_trigger=49700.0,
        stop_price=49664.0, t1_price=50300.0, rsi_4h_at_cross=55.0,
        cross_time=dt.datetime.fromtimestamp(ct, tz=timezone.utc),
        journey_cap_at=dt.datetime.fromtimestamp(ct, tz=timezone.utc) + timedelta(days=7),
    )
    candles = [
        _c5m(50100.0, ct),                                          # cross bar -- skipped
        _c5m(50200.0, ct + 300, low=49950.0, high=50250.0),         # close stays ABOVE trigger; the WICK (low) touches -- FILL
    ]
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "FILLED"
    assert row.fill_price == 50000.0   # the trigger -- never the wick's own low (49950.0) or the bar's close (50200.0)

    orders = poll_env["get_orders"](traveler_plan_id=row.id)
    assert len(orders) == 1
    order = orders[0]
    assert order.account_id == account_id
    assert order.entry_fill_price == 50000.0


def test_waiting_touch_fill_f_a_reads_rsi_4h_at_cross_not_at_lock(poll_env):
    # 2026-09-21 (CC_WORK_ORDER_D1_RSI_AT_CROSS.md): a real positive check,
    # not just the "not extreme" default from the test above -- 0.5 is ALSO
    # what f_a_multiplier(None, ...) returns, so that test alone would still
    # pass even if F_A silently stopped reading anything at all. Setting
    # rsi_4h_at_lock to an EXTREME value here (which must NOT drive F_A) and
    # rsi_4h_at_cross to the SAME extreme (which must) proves the wiring
    # points at the right column, not just that the not-extreme path works.
    poll_env["make_traveler_account"]()
    ct = 1700000000
    poll_env["make_plan"](
        status="WAITING_TOUCH", direction="LONG",
        breakout_trigger=50000.0, breakdown_trigger=49700.0, opposite_trigger=49700.0,
        stop_price=49664.0, t1_price=50300.0,
        rsi_4h_at_lock=10.0,     # extreme in the WRONG direction if this were read -- must be ignored
        rsi_4h_at_cross=85.0,    # >= F_A_LONG_EXTREME_THRESHOLD (80) -> 1.0
        cross_time=dt.datetime.fromtimestamp(ct, tz=timezone.utc),
        journey_cap_at=dt.datetime.fromtimestamp(ct, tz=timezone.utc) + timedelta(days=7),
    )
    candles = [_c5m(50100.0, ct), _c5m(49900.0, ct + 300)]
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    order = poll_env["get_orders"]()[0]
    assert order.sizing_multiplier_used == pytest.approx(1.0)
    assert order.qty == pytest.approx((100.0 * 1.0) / abs(50000.0 - 49664.0))


def test_waiting_touch_ignores_non_gate_traveler_accounts(poll_env):
    poll_env["make_traveler_account"](gate_profile="GATE_V2", mgmt_profile="MGMT_SPLIT")
    ct = 1700000000
    poll_env["make_plan"](
        status="WAITING_TOUCH", direction="LONG",
        breakout_trigger=50000.0, breakdown_trigger=49700.0, opposite_trigger=49700.0,
        stop_price=49664.0, t1_price=50300.0,
        cross_time=dt.datetime.fromtimestamp(ct, tz=timezone.utc),
        journey_cap_at=dt.datetime.fromtimestamp(ct, tz=timezone.utc) + timedelta(days=7),
    )
    candles = [_c5m(50100.0, ct), _c5m(49900.0, ct + 300)]
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "FILLED"
    assert poll_env["get_orders"](traveler_plan_id=row.id) == []  # GATE_V2 account never acts on a TravelerPlan fill


def test_mgmt_e1_stack_poll_closes_the_order_on_a_real_stop_touch(poll_env):
    account_id = poll_env["make_traveler_account"]()
    ct = 1700000000
    poll_env["make_plan"](
        status="WAITING_TOUCH", direction="LONG",
        breakout_trigger=50000.0, breakdown_trigger=49700.0, opposite_trigger=49700.0,
        stop_price=49664.0, t1_price=50300.0, rsi_4h_at_lock=55.0,
        cross_time=dt.datetime.fromtimestamp(ct, tz=timezone.utc),
        journey_cap_at=dt.datetime.fromtimestamp(ct, tz=timezone.utc) + timedelta(days=7),
    )
    fill_candles = [_c5m(50100.0, ct), _c5m(49900.0, ct + 300)]
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": fill_candles}, polls=1)
    row = poll_env["get_plan"]()
    assert row.status == "FILLED"

    # Second poll cycle: price drops through the stop (49664).
    walk_candles = [
        _c5m(49900.0, ct + 300),
        _c5m(49600.0, ct + 600, high=49700.0, low=49500.0),  # stop touched via low
    ]
    flat_htf = [{"close": 50000.0} for _ in range(20)]
    poll_env["run_polls"](
        candles_5m_by_symbol={"BTC/USDT": walk_candles},
        candles_1h_by_symbol={"BTC/USDT": flat_htf}, candles_4h_by_symbol={"BTC/USDT": flat_htf},
        polls=1,
    )

    orders = poll_env["get_orders"]()
    assert len(orders) == 1
    order = orders[0]
    assert order.management_state == "CLOSED_STOP"
    assert order.exit_reason == "STOP"
    assert order.exit_price == 49664.0
    assert order.realized_pnl_r == pytest.approx(-1.0)


def test_mgmt_e1_stack_closure_compounds_the_account_ledger(poll_env):
    # Ruling D: MGMT_E1_STACK's own DRY_RUN walk must feed record_trade_
    # result() symmetrically with dry_run_split_engine.py's own MGMT_SPLIT
    # walk, labeled as simulation in the audit trail.
    account_id = poll_env["make_traveler_account"]()
    db = SessionLocal()
    account = db.query(ExecutorAccount).filter_by(id=account_id).first()
    policy = ea.get_or_init_sizing_policy(db, account)
    policy.roll_in_pct = 0.5
    db.commit()
    starting_risk = ea.get_or_init_risk_state(db, account).risk_last_usd
    db.close()

    ct = 1700000000
    poll_env["make_plan"](
        status="WAITING_TOUCH", direction="LONG",
        breakout_trigger=50000.0, breakdown_trigger=49700.0, opposite_trigger=49700.0,
        stop_price=49664.0, t1_price=50300.0, rsi_4h_at_lock=55.0,
        cross_time=dt.datetime.fromtimestamp(ct, tz=timezone.utc),
        journey_cap_at=dt.datetime.fromtimestamp(ct, tz=timezone.utc) + timedelta(days=7),
    )
    fill_candles = [_c5m(50100.0, ct), _c5m(49900.0, ct + 300)]
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": fill_candles}, polls=1)

    # T1 (50300) touched -- a real winner.
    walk_candles = [_c5m(49900.0, ct + 300), _c5m(50350.0, ct + 600, high=50400.0, low=50200.0)]
    flat_htf = [{"close": 50000.0} for _ in range(20)]
    poll_env["run_polls"](
        candles_5m_by_symbol={"BTC/USDT": walk_candles},
        candles_1h_by_symbol={"BTC/USDT": flat_htf}, candles_4h_by_symbol={"BTC/USDT": flat_htf},
        polls=1,
    )

    db = SessionLocal()
    state = ea.get_or_init_risk_state(db, db.query(ExecutorAccount).filter_by(id=account_id).first())
    assert state.risk_last_usd != starting_risk
    assert state.last_trade_pnl_usd is not None and state.last_trade_pnl_usd > 0

    audit_row = db.query(ExecutorAuditLog).filter_by(account_id=account_id, event_type="TRADE_RESULT_RECORDED").first()
    assert audit_row is not None
    assert audit_row.message.startswith("[SIMULATION]")
    db.close()


# ------------------------------------------------------------------ Ruling C: TRAVELER's own email notifications
# (DeepSeek, relayed by Andy 2026-09-15 -- traveler_plan_notify.py, the
# same "one email per required transition" pattern trade_plan_notify.py
# already uses for v1/v2, tagged TRAVELER throughout.)

def _capture_emails(monkeypatch):
    sent = []
    def fake_send(subject, body):
        sent.append((subject, body))
        return True
    monkeypatch.setattr(notify, "send_admin_email", fake_send)
    return sent


def test_waiting_touch_fill_sends_a_traveler_armed_email_via_loop(poll_env, monkeypatch):
    sent = _capture_emails(monkeypatch)
    poll_env["make_traveler_account"]()
    ct = 1700000000
    poll_env["make_plan"](
        status="WAITING_TOUCH", direction="LONG",
        breakout_trigger=50000.0, breakdown_trigger=49700.0, opposite_trigger=49700.0,
        stop_price=49664.0, t1_price=50300.0, rsi_4h_at_lock=55.0,
        cross_time=dt.datetime.fromtimestamp(ct, tz=timezone.utc),
        journey_cap_at=dt.datetime.fromtimestamp(ct, tz=timezone.utc) + timedelta(days=7),
    )
    candles = [_c5m(50100.0, ct), _c5m(49900.0, ct + 300)]
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA TRAVELER ARMED")
    assert "50,000" in sent[0][0] or "50000" in sent[0][0]   # the trigger, not the touching bar's own close


def test_waiting_cross_tercile_skipped_sends_a_traveler_done_email_via_loop(poll_env, monkeypatch):
    sent = _capture_emails(monkeypatch)
    ct = 1700000000
    cross_epoch = ct + 5 * 300
    poll_env["make_plan"]()
    candles = [_c5m(95.0, ct + i * 300) for i in range(5)] + [_c5m(105.0, cross_epoch)]
    poll_env["run_polls"](
        candles_5m_by_symbol={"BTC/USDT": candles},
        candles_4h_by_symbol={"BTC/USDT": _h4_long_skip(cross_epoch)},
        polls=1,
    )

    row = poll_env["get_plan"]()
    assert row.status == "TERCILE_SKIPPED"
    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA TRAVELER DONE")
    assert "tercile-skipped" in sent[0][1]


def test_waiting_touch_opposite_break_sends_a_traveler_done_email_via_loop(poll_env, monkeypatch):
    sent = _capture_emails(monkeypatch)
    ct = 1700000000
    poll_env["make_plan"](
        status="WAITING_TOUCH", direction="LONG",
        breakout_trigger=50000.0, breakdown_trigger=49700.0, opposite_trigger=49700.0,
        stop_price=49664.0, t1_price=50300.0,
        cross_time=dt.datetime.fromtimestamp(ct, tz=timezone.utc),
        journey_cap_at=dt.datetime.fromtimestamp(ct, tz=timezone.utc) + timedelta(days=7),
    )
    candles = [_c5m(49650.0, ct + 300)]   # closes BELOW the opposite trigger (49700) -- journey invalidated
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "DONE"
    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA TRAVELER DONE")
    assert "opposite trigger" in sent[0][1]


def test_waiting_cross_to_waiting_touch_sends_no_email_via_loop(poll_env, monkeypatch):
    sent = _capture_emails(monkeypatch)
    poll_env["make_plan"]()
    candles = [_c5m(95.0, 1700000000 + i * 300) for i in range(5)] + [_c5m(105.0, 1700000000 + 5 * 300)]
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "WAITING_TOUCH"
    assert sent == []
