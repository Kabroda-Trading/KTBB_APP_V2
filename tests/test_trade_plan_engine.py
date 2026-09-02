"""
Regression coverage for trade_plan_engine.py's monitoring loop -- runs the
ACTUAL run_trade_plan_loop() coroutine against monkeypatched exchange calls
and synthetic candle sequences, exercising the real production code path
(not a reimplementation), matching tests/test_runner_mechanic.py's
established harness pattern for ledger_closing_engine.py.

Includes a dedicated regression test for a bug caught during design (not
in shipped code): treating a STOPPED row's NO_PUSH fuel read as "not
fueled" would prematurely resolve every wick-fake to DONE the very next
poll, since price is rarely still beyond the trigger the instant after a
stop-out. The loop must leave the row untouched until price actually
returns to the trigger.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_trade_plan_engine.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import datetime as dt
import json
from datetime import timezone, timedelta

import pytest

import database
from database import SessionLocal, TradePlan, CampaignLog, SessionLock, GateLog
import trade_plan_engine as tpe
import notify
import market_regime
import micro_regime
import htf_fuel


def _clean_db_files():
    for path in ["kabroda_test_trade_plan_engine.db", "kabroda_test_trade_plan_engine.db-journal",
                 "kabroda_test_trade_plan_engine.db-shm", "kabroda_test_trade_plan_engine.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


class _StopLoop(Exception):
    """Raised by the mocked asyncio.sleep to end run_trade_plan_loop()'s
    while-True after a fixed number of iterations."""


def _c5m(close, volume):
    return {"close": close, "volume": volume}


def _fueled_5m_candles(trigger, is_long, baseline_vol=10.0, push_vol=10.0, baseline_n=250, push_n=6):
    """ratio = push_vol/baseline_vol -- 1.0 here, comfortably >= VOL_FUELED (0.8)."""
    near = trigger - 5.0 if is_long else trigger + 5.0
    beyond = trigger + 5.0 if is_long else trigger - 5.0
    return ([_c5m(near, baseline_vol)] * baseline_n) + ([_c5m(beyond, push_vol)] * push_n)


def _thin_5m_candles(trigger, is_long, baseline_vol=10.0, push_vol=2.0, baseline_n=250, push_n=6):
    near = trigger - 5.0 if is_long else trigger + 5.0
    beyond = trigger + 5.0 if is_long else trigger - 5.0
    return ([_c5m(near, baseline_vol)] * baseline_n) + ([_c5m(beyond, push_vol)] * push_n)


def _no_push_5m_candles(trigger, is_long, baseline_vol=10.0, n=256):
    """Price never crosses the trigger -- fuel_gate.measure_push_volume's NO_PUSH case."""
    near = trigger - 5.0 if is_long else trigger + 5.0
    return [_c5m(near, baseline_vol)] * n


def _c1m(l, h, ts=0):
    return {"l": l, "h": h, "ts": ts}


@pytest.fixture
def poll_env(monkeypatch):
    # database.py's engine is created once, from os.environ["DATABASE_URL"]
    # at MODULE IMPORT time, and cached for the whole pytest process --
    # whichever db-touching test file imports `database` FIRST wins that
    # race (alphabetically, tests/test_runner_mechanic.py), regardless of
    # what this file's own os.environ["DATABASE_URL"] says. _clean_db_files()
    # alone can silently no-op against the wrong path in that case, letting
    # rows from earlier tests/files accumulate and leak into this file's
    # runs. Row-level cleanup (not file-level) is robust to that either way.
    _clean_db_files()
    database.init_db()
    db = SessionLocal()
    db.query(TradePlan).delete()
    db.query(CampaignLog).delete()
    db.commit()
    db.close()
    # `now` here is used only to build FIELD VALUES (commit_after/fill_time
    # offsets) for assertion consistency -- run_trade_plan_loop() itself
    # calls datetime.now(timezone.utc) LIVE, uncontrolled by this fixture,
    # for the actual now_utc it passes into _advance_one(). That real
    # now_utc is compared against session_expires_at, which _advance_one()
    # derives fresh from row.date_key via the real
    # _compute_session_expires_at() (NY Futures close, 15:00 ET / 19:00 UTC
    # in August) -- so a hardcoded date_key is flaky-by-design: every test
    # that doesn't care about session expiry starts silently failing the
    # moment real wall-clock time crosses 19:00 UTC on whatever day this
    # suite happens to run (found 2026-08-31, ~19:02 UTC real time, mid-
    # session). DEFAULT_DATE_KEY is computed from REAL current time (a day
    # ahead) specifically so session_expires_at always lands safely in the
    # future regardless of when the suite runs -- decoupled from `now`
    # below, which stays a fixed, deterministic value purely for building
    # readable, consistent field offsets (matching test_trade_plan_state_
    # machine.py's own fixed NOW constant). Nothing in _advance_one() cross-
    # checks date_key against commit_after/fill_time's own date component,
    # so this split is harmless. Tests that need an ALREADY-expired session
    # (e.g. session-expiry tests) pass an explicit past date_key instead.
    DEFAULT_DATE_KEY = (dt.datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    now = dt.datetime(2026, 8, 31, 14, 0, 0, tzinfo=timezone.utc)

    def make_plan(symbol="BTC/USDT", date_key=None, session_id="us_ny_futures", **kwargs):
        date_key = date_key or DEFAULT_DATE_KEY
        db = SessionLocal()
        defaults = dict(
            symbol=symbol, date_key=date_key, session_id=session_id,
            status="WAITING", direction="LONG", trigger_price=100.0,
            commit_after=now - timedelta(minutes=5),
        )
        defaults.update(kwargs)
        row = TradePlan(**defaults)
        db.add(row)
        db.commit()
        db.close()

    def make_campaign(symbol="BTC/USDT", date_key=None, session_id="us_ny_futures", **kwargs):
        date_key = date_key or DEFAULT_DATE_KEY  # must match make_plan()'s default so the two join correctly
        db = SessionLocal()
        defaults = dict(
            symbol=symbol, date_key=date_key, session_id=session_id,
            bias="LONG", grade="TAKE_STANDARD", entry_price=100.0, stop_loss=95.0,
            t1=112.0, t2=120.0, t3=132.0, total_contracts=1.0,
            status="PENDING", is_canonical=True,
        )
        defaults.update(kwargs)
        row = CampaignLog(**defaults)
        db.add(row)
        db.commit()
        db.close()

    def run_polls(candles_5m_by_symbol=None, candles_1m_by_symbol=None, polls=1,
                  candles_1h_by_symbol=None, candles_4h_by_symbol=None, daily_atr14=0.0):
        candles_5m_by_symbol = candles_5m_by_symbol or {}
        candles_1m_by_symbol = candles_1m_by_symbol or {}
        candles_1h_by_symbol = candles_1h_by_symbol or {}
        candles_4h_by_symbol = candles_4h_by_symbol or {}

        async def fake_5m(symbol, limit=310):
            return candles_5m_by_symbol.get(symbol, [])

        async def fake_1m(symbol, since_ms, limit=720):
            return candles_1m_by_symbol.get(symbol, [])

        async def fake_1h(symbol, limit=100):
            return candles_1h_by_symbol.get(symbol, [])

        async def fake_4h(symbol, limit=100):
            return candles_4h_by_symbol.get(symbol, [])

        async def fake_15m(symbol, limit=300):
            return candles_5m_by_symbol.get(symbol, [])  # reuse the 5m fixture data -- content doesn't matter for these tests

        async def fake_daily(symbol, limit=60):
            return []  # unused directly -- fake_atr below controls the value tier-stamping sees

        def fake_atr(candles_1d):
            return daily_atr14

        sleeps = {"n": 0}

        async def fake_sleep(seconds):
            sleeps["n"] += 1
            if sleeps["n"] >= polls:
                raise _StopLoop()

        monkeypatch.setattr(tpe.market_data, "fetch_live_5m", fake_5m)
        monkeypatch.setattr(tpe.market_data, "fetch_live_15m", fake_15m)
        monkeypatch.setattr(tpe.market_data, "fetch_live_1h", fake_1h)
        monkeypatch.setattr(tpe.market_data, "fetch_live_4h", fake_4h)
        monkeypatch.setattr(tpe.market_data, "fetch_live_daily", fake_daily)
        monkeypatch.setattr(tpe.market_data, "_calc_daily_atr14", fake_atr)
        monkeypatch.setattr(tpe, "_fetch_1m_since", fake_1m)
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

    def make_lock(symbol="BTC/USDT", date_key=None, session_id="us_ny_futures", levels=None):
        # 2026-09-01 P0 follow-up: _enrich_opposite_break_with_full_gate()
        # reads the real SessionLock row for the locked levels, same as
        # DeepSeek's own incident reconstruction and market_radar.py's
        # live dossier.
        date_key = date_key or DEFAULT_DATE_KEY
        db = SessionLocal()
        db.query(SessionLock).filter(
            SessionLock.symbol == symbol, SessionLock.session_id == session_id, SessionLock.date_key == date_key,
        ).delete()
        row = SessionLock(
            symbol=symbol, session_id=session_id, date_key=date_key, lock_time=0,
            packet_data=json.dumps({"levels": levels or {}}),
        )
        db.add(row)
        db.commit()
        db.close()

    def make_gate_log(symbol="BTC/USDT", date_key=None, state="PASS", **kwargs):
        date_key = date_key or DEFAULT_DATE_KEY
        db = SessionLocal()
        row = GateLog(symbol=symbol, date_key=date_key, state=state, **kwargs)
        db.add(row)
        db.commit()
        db.close()

    def get_gate_log(symbol="BTC/USDT", date_key=None):
        date_key = date_key or DEFAULT_DATE_KEY
        db = SessionLocal()
        row = (
            db.query(GateLog)
            .filter(GateLog.symbol == symbol, GateLog.date_key == date_key)
            .order_by(GateLog.id.desc())
            .first()
        )
        db.close()
        return row

    yield {
        "now": now, "make_plan": make_plan, "make_campaign": make_campaign,
        "make_lock": make_lock, "make_gate_log": make_gate_log, "get_gate_log": get_gate_log,
        "run_polls": run_polls, "get_plan": get_plan,
    }

    db = SessionLocal()
    db.query(TradePlan).delete()
    db.query(CampaignLog).delete()
    db.query(SessionLock).delete()
    db.query(GateLog).delete()
    db.commit()
    db.close()
    _clean_db_files()


# ------------------------------------------------------------------ notifications
# (Andy's build request, trade_plan_notify.py -- one email per required
# state transition: ARMED/VETOED/DONE, never STOPPED/REENTRY_ARMED)

def _capture_emails(monkeypatch):
    sent = []
    def fake_send(subject, body):
        sent.append((subject, body))
        return True
    monkeypatch.setattr(notify, "send_admin_email", fake_send)
    return sent


def test_waiting_fueled_cross_sends_armed_email_via_loop(poll_env, monkeypatch):
    sent = _capture_emails(monkeypatch)
    poll_env["make_plan"](
        status="WAITING", direction="LONG", trigger_price=100.0,
        stop_price=90.0, stop_basis="beyond sweep wick low", t1=112.0, t2=120.0, t3=132.0,
    )
    candles = _fueled_5m_candles(100.0, is_long=True)
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA ARMED")


def test_waiting_unfueled_cross_sends_vetoed_email_via_loop(poll_env, monkeypatch):
    sent = _capture_emails(monkeypatch)
    poll_env["make_plan"](status="WAITING", direction="LONG", trigger_price=100.0)
    candles = _thin_5m_candles(100.0, is_long=True)
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA VETOED")


def test_waiting_session_expiry_sends_done_email_via_loop(poll_env, monkeypatch):
    sent = _capture_emails(monkeypatch)
    poll_env["make_plan"](status="WAITING", direction="LONG", trigger_price=100.0, date_key="2020-01-01")
    # _advance_one() bails before ever checking session expiry if
    # fetch_live_5m returns empty (same as a real "market data unavailable"
    # skip) -- supply real (untouched) candles so the expiry branch is
    # actually reached, matching how a live poll would look.
    candles = _no_push_5m_candles(100.0, is_long=True)
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA DONE")


def test_filled_wide_stop_wicked_sends_no_email_via_loop(poll_env, monkeypatch):
    # STOPPED is a real transition -- just not a required notify event.
    sent = _capture_emails(monkeypatch)
    poll_env["make_plan"](
        status="FILLED", direction="LONG", trigger_price=100.0,
        stop_price=90.0, t1=112.0, fill_time=poll_env["now"] - timedelta(minutes=30),
    )
    candles_1m = [_c1m(98, 101), _c1m(89.0, 99.0)]  # stop touched, T1 never reached
    poll_env["run_polls"](candles_1m_by_symbol={"BTC/USDT": candles_1m}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "STOPPED"
    assert sent == []


def test_filled_t1_reached_sends_done_email_via_loop(poll_env, monkeypatch):
    sent = _capture_emails(monkeypatch)
    poll_env["make_plan"](
        status="FILLED", direction="LONG", trigger_price=100.0,
        stop_price=90.0, t1=112.0, fill_time=poll_env["now"] - timedelta(minutes=30),
    )
    poll_env["make_campaign"](status="CLOSED_WIN")
    candles_1m = [_c1m(98, 101), _c1m(111.0, 113.0)]
    poll_env["run_polls"](candles_1m_by_symbol={"BTC/USDT": candles_1m}, polls=1)

    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA DONE")


def test_waiting_opposite_side_break_sends_done_email_via_loop(poll_env, monkeypatch):
    # P0 regression, real loop: a LONG-anticipated plan must detect (and
    # notify on) a real break through the OPPOSITE trigger, not sit
    # WAITING forever with zero signal. No SessionLock row exists for this
    # test -- the full-gate enrichment below can't run without one, so
    # this specifically exercises the plain fallback path.
    sent = _capture_emails(monkeypatch)
    poll_env["make_plan"](status="WAITING", direction="LONG", trigger_price=100.0, t2=110.0)
    candles = [{"close": 85.0, "volume": 10.0} for _ in range(30)]  # opposite (SHORT) trigger = 90, broken
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "DONE"
    assert "OPPOSITE trigger" in row.last_transition_reason
    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA DONE")


def test_waiting_opposite_side_break_full_gate_sends_vetoed_email_via_loop(poll_env, monkeypatch):
    # 2026-09-01 P0 follow-up: with a real SessionLock available, the
    # opposite-break enrichment runs the ACTUAL, unmodified gate and Andy
    # gets the real verdict -- reproducing the incident's own resolution
    # (a confirmed counter-trend veto, not a missed trade).
    sent = _capture_emails(monkeypatch)
    monkeypatch.setattr(market_regime, "classify_market_regime", lambda candles: {
        "table": "TRENDING_UP", "quality": "GOOD", "policy": {"bias": "UP"},
    })
    monkeypatch.setattr(micro_regime, "classify_regime", lambda candles: {"regime": "TRENDING"})
    monkeypatch.setattr(htf_fuel, "htf_fuel", lambda c1h, c4h, side: {
        "trend_1h": "BEARISH", "trend_4h": "NEUTRAL", "aligned": 0, "opposed": 1,
    })

    poll_env["make_lock"](levels={
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "range30m_high": 100.0, "range30m_low": 90.0,
    })
    poll_env["make_gate_log"](state="PASS")  # the stale lock-time placeholder, must get overwritten
    poll_env["make_plan"](status="WAITING", direction="LONG", trigger_price=100.0, t2=110.0)
    candles = [{"close": 85.0, "volume": 10.0} for _ in range(30)]  # closes below BD=90 -> real side=SHORT
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "DONE"
    assert "full gate ran" in row.last_transition_reason
    assert "counter-trend" in row.last_transition_reason.lower() or "UP daily trend" in row.last_transition_reason
    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA VETOED")
    assert "SHORT" in sent[0][0]

    # 2026-09-01 (steady-state row ownership): the Brain's forward-test
    # log (GateLog, exported via /api/export/gate-log.csv) must reflect
    # this real, detected verdict -- not stay frozen at the lock-time PASS.
    gate_row = poll_env["get_gate_log"]()
    assert gate_row.state == "PASS"  # decision_engine's own verdict_state for a vetoed cross
    assert gate_row.side == "SHORT"
    assert "counter-trend" in gate_row.headline.lower() or "UP daily trend" in gate_row.headline
    assert gate_row.daily_regime_table == "TRENDING_UP"
    assert gate_row.daily_regime_quality == "GOOD"


def test_waiting_own_cross_syncs_gate_log_via_loop(poll_env, monkeypatch):
    # The SAME staleness gap, opposite cause: the ANTICIPATED side's own
    # cross (a real ARMED trade) must also overwrite the lock-time
    # placeholder -- not just the opposite-break path.
    monkeypatch.setattr(market_regime, "classify_market_regime", lambda candles: {
        "table": "TRENDING_UP", "quality": "GOOD", "policy": {"bias": "UP"},
    })
    monkeypatch.setattr(micro_regime, "classify_regime", lambda candles: {"regime": "TRENDING"})
    monkeypatch.setattr(htf_fuel, "htf_fuel", lambda c1h, c4h, side: {
        "trend_1h": "BULLISH", "trend_4h": "BULLISH", "aligned": 2, "opposed": 0,
    })

    poll_env["make_lock"](levels={
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "range30m_high": 100.0, "range30m_low": 90.0,
    })
    poll_env["make_gate_log"](state="PASS")
    poll_env["make_plan"](status="WAITING", direction="LONG", trigger_price=100.0, t2=110.0)
    candles = _fueled_5m_candles(100.0, is_long=True)  # real fill on the anticipated (LONG) side
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "FILLED"

    gate_row = poll_env["get_gate_log"]()
    assert gate_row.side == "LONG"
    assert gate_row.state in ("TAKE_PREMIUM", "TAKE_STANDARD", "ALMOST", "PASS")
    assert gate_row.daily_regime_table == "TRENDING_UP"


def _fueled_5m_ohlc_candles(trigger, is_long, baseline_vol=10.0, push_vol=10.0, baseline_n=250, push_n=6, near_offset=2.0):
    """Same shape/fuel math as _fueled_5m_candles(), but with full OHLC --
    promote_no_plan_on_real_cross() feeds these into stop_planner.py's
    swing/sweep detection (_find_swing_points/_find_sweep_wicks), which
    needs open/high/low, not just close/volume like the WAITING-path
    tests above only ever needed (advance_waiting_plan()'s FILLED
    transition doesn't call stop_planner -- the stop was already planned
    at lock). near_offset defaults tight (2.0, not _fueled_5m_candles()'s
    5.0) so the swing-low/high stop_planner finds off the "near" baseline
    stays close enough to entry to clear the R:R floor against a real
    box-derived T1 -- confirmed against stop_planner.plan_stop()/
    rr_floor_ok() directly before picking this value."""
    near = trigger - near_offset if is_long else trigger + near_offset
    beyond = trigger + 5.0 if is_long else trigger - 5.0

    def flat(price, volume):
        return {"open": price, "high": price + 0.5, "low": price - 0.5, "close": price, "volume": volume}

    return ([flat(near, baseline_vol)] * baseline_n) + ([flat(beyond, push_vol)] * push_n)


# ------------------------------------------------------------------ NO_PLAN poll routing (2026-09-02, Andy's decision)
# A NO_PLAN morning is no longer permanently final -- these exercise the
# REAL run_trade_plan_loop() path, same harness style as the opposite-
# break/own-cross tests above (real market_regime/micro_regime/htf_fuel
# modules monkeypatched, real decision_engine.evaluate_15m_decision() and
# trade_plan.promote_no_plan_on_real_cross() both run for real).

def test_no_plan_real_cross_promotes_to_filled_and_sends_armed_email(poll_env, monkeypatch):
    import decision_engine
    monkeypatch.setattr(decision_engine, "DEAD_HOURS", set())  # test-time robustness against real wall-clock hour
    sent = _capture_emails(monkeypatch)
    monkeypatch.setattr(market_regime, "classify_market_regime", lambda candles: {
        "table": "TRENDING_UP", "quality": "GOOD", "policy": {"bias": "UP"},
    })
    monkeypatch.setattr(micro_regime, "classify_regime", lambda candles: {"regime": "TRENDING"})
    monkeypatch.setattr(htf_fuel, "htf_fuel", lambda c1h, c4h, side: {
        "trend_1h": "BULLISH", "trend_4h": "NEUTRAL", "aligned": 1, "opposed": 0,
    })

    poll_env["make_lock"](levels={
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "range30m_high": 100.0, "range30m_low": 90.0,
        "f24_vah": 105.0, "f24_val": 85.0,
    })
    poll_env["make_gate_log"](state="PASS")  # the stale lock-time NO_PLAN placeholder, must get overwritten
    poll_env["make_plan"](status="NO_PLAN", direction=None, trigger_price=None)
    candles = _fueled_5m_ohlc_candles(100.0, is_long=True)  # real fueled break through BO -> LONG
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1, daily_atr14=20.0)  # box=10, atr=20 -> ratio=0.5, reachable

    row = poll_env["get_plan"]()
    assert row.status == "FILLED"
    assert row.direction == "LONG"
    assert row.tier == "STANDARD"  # only 1/2 HTF aligned -- not PREMIUM
    assert row.fuel_at_cross == "FUELED"
    assert row.fill_price == 100.0
    assert "real cross" in row.last_transition_reason

    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA ARMED")
    assert "LONG" in sent[0][0]

    # Same "site's own row IS the verdict row" invariant as the opposite-
    # break/own-cross paths -- GateLog must reflect the real promotion,
    # not stay frozen at the lock-time NO_PLAN/PASS placeholder.
    gate_row = poll_env["get_gate_log"]()
    assert gate_row.state in ("TAKE_PREMIUM", "TAKE_STANDARD")
    assert gate_row.side == "LONG"


def test_no_plan_stays_no_plan_when_gate_still_says_no(poll_env, monkeypatch):
    sent = _capture_emails(monkeypatch)
    poll_env["make_lock"](levels={
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "range30m_high": 100.0, "range30m_low": 90.0,
    })
    poll_env["make_plan"](status="NO_PLAN", direction=None, trigger_price=None)
    candles = [{"close": 95.0, "volume": 10.0} for _ in range(30)]  # still inside the box -- no cross
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "NO_PLAN"  # unchanged -- still silently waiting
    assert sent == []  # no email for a non-event


def test_no_plan_session_expired_no_cross_becomes_done_without_email(poll_env, monkeypatch):
    sent = _capture_emails(monkeypatch)
    poll_env["make_plan"](status="NO_PLAN", direction=None, trigger_price=None, date_key="2020-01-01")
    poll_env["run_polls"](polls=1)  # no candles/lock needed -- expiry check comes first, same as STOPPED's own expiry test

    row = poll_env["get_plan"]()
    assert row.status == "DONE"
    assert "session ended" in row.last_transition_reason
    # The STAND DOWN lock email already told Andy nothing would follow
    # unless a real cross changed it -- this bookkeeping transition must
    # NOT contradict that with a second email.
    assert sent == []


def test_waiting_fueled_cross_fills_via_loop(poll_env):
    poll_env["make_plan"](status="WAITING", direction="LONG", trigger_price=100.0)
    candles = _fueled_5m_candles(100.0, is_long=True)
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "FILLED"
    assert row.fill_price == 100.0
    assert row.entry_mode in ("TRIGGER_AT_LEVEL", "RETEST_LIMIT_AT_LINE")


def test_waiting_fueled_cross_stamps_tier_via_loop(poll_env, monkeypatch):
    # 2026-08-31 fix: a plan generated pre-cross (tier=None) gets a real
    # tier stamped through the actual monitoring loop, not just the pure
    # function in isolation.
    import htf_fuel as _htf_fuel
    monkeypatch.setattr(_htf_fuel, "htf_fuel", lambda c1h, c4h, side: {
        "trend_1h": "BULLISH", "trend_4h": "BULLISH", "aligned": 2, "opposed": 0,
    })
    poll_env["make_plan"](status="WAITING", direction="LONG", trigger_price=100.0, tier=None, t2=110.0)
    candles = _fueled_5m_candles(100.0, is_long=True)
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1, daily_atr14=25.0)

    row = poll_env["get_plan"]()
    assert row.status == "FILLED"
    assert row.tier == "PREMIUM"  # box=10 (t2-trigger), atr=25 -> ratio=0.4, both HTF aligned


def test_reentry_armed_fueled_cross_fills_via_loop(poll_env):
    poll_env["make_plan"](status="REENTRY_ARMED", direction="LONG", trigger_price=100.0, reentry_used=False)
    candles = _fueled_5m_candles(100.0, is_long=True)
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "FILLED"
    assert row.reentry_used is True
    assert row.reentry_fill_price == 100.0


def test_filled_wide_stop_wicked_becomes_stopped_via_loop(poll_env):
    poll_env["make_plan"](
        status="FILLED", direction="LONG", trigger_price=100.0,
        stop_price=90.0, t1=112.0, fill_time=poll_env["now"] - timedelta(minutes=30),
    )
    candles_1m = [_c1m(98, 101), _c1m(89.0, 99.0)]  # stop touched, T1 never reached
    poll_env["run_polls"](candles_1m_by_symbol={"BTC/USDT": candles_1m}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "STOPPED"
    assert row.stopped_time is not None


def test_filled_t1_reached_then_campaign_resolves_done_via_loop(poll_env):
    poll_env["make_plan"](
        status="FILLED", direction="LONG", trigger_price=100.0,
        stop_price=90.0, t1=112.0, fill_time=poll_env["now"] - timedelta(minutes=30),
    )
    poll_env["make_campaign"](status="CLOSED_WIN")
    candles_1m = [_c1m(98, 101), _c1m(111.0, 113.0)]  # T1 reached, wide stop never touched
    poll_env["run_polls"](candles_1m_by_symbol={"BTC/USDT": candles_1m}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "DONE"
    assert "CLOSED_WIN" in row.last_transition_reason


def test_reentry_filled_ignores_stale_campaign_and_keeps_polling_via_loop(poll_env):
    """Regression: a re-entry fill must NOT be closed out by mirroring a
    STALE, terminal CampaignLog row left over from the ORIGINAL fill's own
    (unrelated) stop-out. Before the fix, this would have closed to DONE
    on the very first poll even though nothing has happened to the
    re-entry itself yet."""
    poll_env["make_plan"](
        status="FILLED", direction="LONG", trigger_price=100.0,
        stop_price=90.0, t1=112.0, fill_time=poll_env["now"] - timedelta(minutes=5),
        reentry_used=True,
    )
    poll_env["make_campaign"](status="CLOSED_LOSS", target_hit="STOP")  # stale, from the original fill
    candles_1m = [_c1m(98, 101)]  # wide stop not hit, T1 not reached -- NEITHER_YET
    poll_env["run_polls"](candles_1m_by_symbol={"BTC/USDT": candles_1m}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "FILLED"  # unchanged -- not wrongly closed via the stale CampaignLog


def test_reentry_filled_t1_reached_resolves_done_without_campaign_via_loop(poll_env):
    poll_env["make_plan"](
        status="FILLED", direction="LONG", trigger_price=100.0,
        stop_price=90.0, t1=112.0, fill_time=poll_env["now"] - timedelta(minutes=5),
        reentry_used=True,
    )
    poll_env["make_campaign"](status="CLOSED_LOSS", target_hit="STOP")  # stale, must be ignored
    candles_1m = [_c1m(111.0, 113.0)]  # T1 reached
    poll_env["run_polls"](candles_1m_by_symbol={"BTC/USDT": candles_1m}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "DONE"
    assert "documented gap" in row.last_transition_reason


def test_stopped_no_push_stays_stopped_not_prematurely_done(poll_env):
    """Regression: NO_PUSH must NOT be treated as 'fuel gone' -- the row
    must be left alone to wait for a real cross, not resolved to DONE."""
    poll_env["make_plan"](status="STOPPED", direction="LONG", trigger_price=100.0, reentry_used=False)
    candles = _no_push_5m_candles(100.0, is_long=True)  # price never returns to the trigger
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "STOPPED"  # unchanged -- not DONE


def test_stopped_fueled_recross_becomes_reentry_armed_via_loop(poll_env):
    poll_env["make_plan"](status="STOPPED", direction="LONG", trigger_price=100.0, reentry_used=False)
    candles = _fueled_5m_candles(100.0, is_long=True)
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "REENTRY_ARMED"


def test_stopped_thin_recross_becomes_done_via_loop(poll_env):
    poll_env["make_plan"](status="STOPPED", direction="LONG", trigger_price=100.0, reentry_used=False)
    candles = _thin_5m_candles(100.0, is_long=True)
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "DONE"


def test_stopped_session_expired_no_cross_becomes_done_via_loop(poll_env):
    # A date_key far in the past -- _compute_session_expires_at derives a
    # boundary that's already well behind "now" for any session.
    poll_env["make_plan"](
        status="STOPPED", direction="LONG", trigger_price=100.0,
        reentry_used=False, date_key="2020-01-01",
    )
    poll_env["run_polls"](polls=1)  # no candles needed -- expiry check comes first

    row = poll_env["get_plan"]()
    assert row.status == "DONE"
    assert "session ended" in row.last_transition_reason
