"""
Regression coverage for trade_plan_engine.py's monitoring loop -- runs the
ACTUAL run_trade_plan_loop() coroutine against monkeypatched exchange calls
and synthetic candle sequences, exercising the real production code path
(not a reimplementation), matching tests/test_runner_mechanic.py's
established harness pattern for ledger_closing_engine.py.

Rewritten 2026-09-15 for v2 (Krown Cross + 4H RSI gate, no fuel -- see
decision_engine.py's own header comment). v1's fuel-based tests (FUELED/
CONFLICTED/NO_FUEL verdicts, PREMIUM/STANDARD tier stamping, PROMOTED_
PUSH_FLOOR, VETOED-then-retest, the counter-trend veto) exercised behavior
that no longer exists in the real gate -- decision_engine.py's DEAD_HOURS
constant is gone entirely too (dead-hour vetoing was measured and cut, see
that file's header comment), so the old `monkeypatch.setattr(decision_
engine, "DEAD_HOURS", set())` lines in several of these tests would now
raise AttributeError on their own, independent of anything else.

Also rewritten for this session's trade_plan_engine.py change: a STOPPED
row now resolves UNCONDITIONALLY to DONE on the next poll (SS8's fuel-
gated re-entry-after-wick-fake has no v2-consistent replacement signal --
see that file's own header comment, "no leg 2" reasoning) -- there is no
more NO_PUSH-stays-STOPPED / FUELED-recross-becomes-REENTRY_ARMED / thin-
recross-becomes-DONE distinction to test, one test covers it.

check_reentry_eligibility()/advance_reentry_plan()/resolve_reentry_fill()
(trade_plan.py) and the REENTRY_ARMED branch this file used to test via
test_reentry_armed_fueled_cross_fills_via_loop were DELETED 2026-09-15
(Andy-approved, v1-dead-machinery audit) -- confirmed genuinely
unreachable (nothing has set TradePlan.status="REENTRY_ARMED" since the
2026-09-11 STOPPED-always-resolves-to-DONE change, which made check_
reentry_eligibility() -- the only thing that could ever set it -- dead on
arrival). Removed rather than kept as dormant coverage, since there is no
code left for it to cover.
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
from database import ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorGlobalConfig
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


def _c5m(close):
    return {"close": close}


def _beyond_trigger_candles(trigger, is_long, n=30):
    """Confirmed 5m closes already beyond the trigger. v2's advance_waiting_
    plan()/decision_engine.py read live_price/side straight off
    candles_5m[-1]["close"] -- no volume/push math involved any more (fuel
    is retired, see decision_engine.py's own header comment), so these are
    just plain closes on the correct side of the level."""
    near = trigger - 5.0 if is_long else trigger + 5.0
    beyond = trigger + 5.0 if is_long else trigger - 5.0
    return ([_c5m(near)] * (n - 6)) + ([_c5m(beyond)] * 6)


def _inside_box_candles(trigger, is_long, n=60):
    """Price never reaches the trigger -- no cross, side stays None."""
    near = trigger - 5.0 if is_long else trigger + 5.0
    return [_c5m(near)] * n


def _c1m(l, h, ts=0):
    return {"l": l, "h": h, "ts": ts}


def _gate_pass(monkeypatch, votes=2, aligned=2):
    """Same helper pattern as tests/test_trade_plan_state_machine.py's own
    _pass_gate() -- patches the real htf_fuel module functions, which both
    trade_plan.py's _confirm_v2_gate_at_cross() (a local `import htf_fuel`)
    and decision_engine.py's `_htf_fuel` module-level import see, since
    they're the same module object. Covers every test in this file that
    needs the real v2 gate (reachability + HTF aligned>=1 + Krown Cross
    votes==2 + RSI-at-lock in zone) to actually pass at a real cross."""
    monkeypatch.setattr(htf_fuel, "htf_fuel", lambda c1h, c4h, side: {
        "aligned": aligned, "trend_1h": "BULLISH", "trend_4h": "BULLISH", "opposed": 0,
    })
    monkeypatch.setattr(htf_fuel, "krown_cross_votes", lambda c1h, c4h, side: {"votes": votes})


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
    # 2026-09-04: the executor bot's hook (trade_plan_engine.py's
    # _notify_executor()) now fires on every real FILLED transition this
    # file's tests drive -- ExecutorOrder's (trade_plan_id, account_id)
    # unique constraint means a leftover row from an earlier test FILE
    # (this module.engine is shared/cached across the whole pytest
    # session, per the comment above) can collide and roll back this
    # file's own TradePlan write. Row-level cleanup here too, same
    # rationale as TradePlan/CampaignLog above.
    for _model in (ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorGlobalConfig):
        db.query(_model).delete()
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
            return []  # unused directly -- fake_atr below controls the value the gate sees

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
    for _model in (ExecutorAccount, ExecutorRiskState, ExecutorOrder, ExecutorAuditLog, ExecutorGlobalConfig):
        db.query(_model).delete()
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


def test_waiting_cross_sends_armed_email_via_loop(poll_env, monkeypatch):
    _gate_pass(monkeypatch)
    sent = _capture_emails(monkeypatch)
    poll_env["make_plan"](
        status="WAITING", direction="LONG", trigger_price=100.0, t2=110.0,
        stop_price=90.0, stop_basis="beyond sweep wick low", t1=112.0, t3=132.0,
        rsi_4h_at_lock=70.0,
    )
    candles = _beyond_trigger_candles(100.0, is_long=True)
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1, daily_atr14=40.0)  # box=10, atr=40 -> ratio=0.25

    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA ARMED")


def test_waiting_cross_armed_email_carries_the_locked_alignment_reading(poll_env, monkeypatch):
    # htf_aligned/trend_1h/trend_4h are real, persisted TradePlan columns
    # (set once at lock, never recomputed at the cross). This confirms they
    # actually survive the real WAITING->FILLED transition through the
    # loop's own update path and reach the sent ARMED email -- not just
    # that render_brief() can format them in isolation. v2 (2026-09-15):
    # the email body used to lead with "Fuel <verdict> -> <tier word>" --
    # fuel is retired, so it's just the HTF trend reads + the tier word now
    # (build_alignment_email_line()'s own comment).
    _gate_pass(monkeypatch)
    sent = _capture_emails(monkeypatch)
    poll_env["make_plan"](
        status="WAITING", direction="LONG", trigger_price=100.0, t2=110.0,
        stop_price=90.0, stop_basis="beyond sweep wick low", t1=112.0, t3=132.0,
        rsi_4h_at_lock=70.0, htf_aligned=2, trend_1h="BULLISH", trend_4h="BULLISH",
    )
    candles = _beyond_trigger_candles(100.0, is_long=True)
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1, daily_atr14=40.0)

    assert len(sent) == 1
    subject, body = sent[0]
    assert subject.startswith("KABRODA ARMED")
    assert "1H trend BULLISH | 4H trend BULLISH | FULLY ALIGNED" in body


def test_waiting_cross_gate_declines_sends_done_email_not_armed(poll_env, monkeypatch):
    # v2: no more FUELED/CONFLICTED/NO_FUEL verdicts and no more VETOED-
    # then-retest state (trade_plan.py's advance_waiting_plan() docstring)
    # -- a real cross the 4-condition gate declines (here: no HTF carry)
    # goes straight WAITING -> DONE, framed as a stand-down, never ARMED.
    # Replaces three now-obsolete v1 tests: thin/CONFLICTED-push-doesn't-
    # arm, FUELED-but-no-HTF-carry-doesn't-arm, and a real ghost push
    # sending a VETOED email (VETOED is no longer a status anything writes
    # -- trade_plan_notify.py's own header comment).
    sent = _capture_emails(monkeypatch)
    monkeypatch.setattr(htf_fuel, "htf_fuel", lambda c1h, c4h, side: {"aligned": 0})
    monkeypatch.setattr(htf_fuel, "krown_cross_votes", lambda c1h, c4h, side: {"votes": 2})
    poll_env["make_plan"](
        status="WAITING", direction="LONG", trigger_price=100.0, t2=110.0,
        stop_price=90.0, stop_basis="beyond sweep wick low", t1=112.0, t3=132.0,
        rsi_4h_at_lock=70.0,
    )
    candles = _beyond_trigger_candles(100.0, is_long=True)
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1, daily_atr14=40.0)

    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA DONE")
    plan = poll_env["get_plan"]()
    assert plan.status == "DONE"
    assert plan.tier is None   # v2 has no tier at all
    assert "carry" in plan.last_transition_reason


def test_waiting_session_expiry_sends_done_email_via_loop(poll_env, monkeypatch):
    sent = _capture_emails(monkeypatch)
    poll_env["make_plan"](status="WAITING", direction="LONG", trigger_price=100.0, date_key="2020-01-01")
    # _advance_one() bails before ever checking session expiry if
    # fetch_live_5m returns empty (same as a real "market data unavailable"
    # skip) -- supply real (untouched) candles so the expiry branch is
    # actually reached, matching how a live poll would look.
    candles = _inside_box_candles(100.0, is_long=True)
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
    candles = [{"close": 85.0} for _ in range(30)]  # opposite (SHORT) trigger = 90, broken
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "DONE"
    assert "OPPOSITE trigger" in row.last_transition_reason
    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA DONE")


def test_waiting_opposite_side_break_full_gate_sends_done_email_via_loop(poll_env, monkeypatch):
    # 2026-09-01 P0 follow-up: with a real SessionLock available, the
    # opposite-break enrichment runs the ACTUAL, unmodified gate and Andy
    # gets the real verdict -- not just "wrong side, no plan." v2 (2026-
    # 09-15): the counter-trend veto this test used to exercise is retired
    # (decision_engine.py's own header comment: measured n=0 on the real
    # v2 population, cut) -- market_regime/micro_regime are still computed
    # and surfaced on GateLog for display, but no longer decide pass/fail,
    # so the real miss here is "no HTF carry" instead.
    sent = _capture_emails(monkeypatch)
    monkeypatch.setattr(market_regime, "classify_market_regime", lambda candles: {
        "table": "TRENDING_UP", "quality": "GOOD", "policy": {"bias": "UP"},
    })
    monkeypatch.setattr(micro_regime, "classify_regime", lambda candles: {"regime": "TRENDING"})
    monkeypatch.setattr(htf_fuel, "htf_fuel", lambda c1h, c4h, side: {
        "trend_1h": "BEARISH", "trend_4h": "NEUTRAL", "aligned": 0, "opposed": 1,
    })
    monkeypatch.setattr(htf_fuel, "krown_cross_votes", lambda c1h, c4h, side: {"votes": 0})

    poll_env["make_lock"](levels={
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "range30m_high": 100.0, "range30m_low": 90.0,
    })
    poll_env["make_gate_log"](state="PASS")  # the stale lock-time placeholder, must get overwritten
    poll_env["make_plan"](status="WAITING", direction="LONG", trigger_price=100.0, t2=110.0)
    candles = [{"close": 85.0} for _ in range(30)]  # closes below BD=90 -> real side=SHORT
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1, daily_atr14=20.0)  # box=10, atr=20 -> ratio=0.5, reachable

    row = poll_env["get_plan"]()
    assert row.status == "DONE"
    assert "full gate ran" in row.last_transition_reason
    assert "no carry" in row.last_transition_reason.lower()
    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA VETOED")
    assert "SHORT" in sent[0][0]

    # 2026-09-01 (steady-state row ownership): the Brain's forward-test
    # log (GateLog, exported via /api/export/gate-log.csv) must reflect
    # this real, detected verdict -- not stay frozen at the lock-time PASS.
    gate_row = poll_env["get_gate_log"]()
    assert gate_row.state == "PASS"  # decision_engine's own verdict_state for a declined cross
    assert gate_row.side == "SHORT"
    assert "carry" in gate_row.headline.lower()
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
    _gate_pass(monkeypatch)

    poll_env["make_lock"](levels={
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "range30m_high": 100.0, "range30m_low": 90.0,
        "rsi_4h_at_lock": 70.0,
    })
    poll_env["make_gate_log"](state="PASS")
    poll_env["make_plan"](status="WAITING", direction="LONG", trigger_price=100.0, t2=110.0, rsi_4h_at_lock=70.0)
    candles = _beyond_trigger_candles(100.0, is_long=True)  # real fill on the anticipated (LONG) side
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1, daily_atr14=20.0)

    row = poll_env["get_plan"]()
    assert row.status == "FILLED"

    gate_row = poll_env["get_gate_log"]()
    assert gate_row.side == "LONG"
    assert gate_row.state == "TAKE"  # v2: one real-trade outcome, not TAKE_PREMIUM/TAKE_STANDARD
    assert gate_row.daily_regime_table == "TRENDING_UP"


# ------------------------------------------------------------------ NO_PLAN poll routing (2026-09-02, Andy's decision)
# A NO_PLAN morning is no longer permanently final -- these exercise the
# REAL run_trade_plan_loop() path, same harness style as the opposite-
# break/own-cross tests above (real market_regime/micro_regime/htf_fuel
# modules monkeypatched, real decision_engine.evaluate_15m_decision() and
# trade_plan.advance_no_plan() both run for real).

def test_no_plan_real_cross_promotes_to_filled_and_sends_armed_email(poll_env, monkeypatch):
    sent = _capture_emails(monkeypatch)
    monkeypatch.setattr(market_regime, "classify_market_regime", lambda candles: {
        "table": "TRENDING_UP", "quality": "GOOD", "policy": {"bias": "UP"},
    })
    monkeypatch.setattr(micro_regime, "classify_regime", lambda candles: {"regime": "TRENDING"})
    _gate_pass(monkeypatch)

    poll_env["make_lock"](levels={
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        # range30m_low 97 (not 90): a NO_PLAN promotion applies v2's one r30
        # stop (100 - box*0.12 off r30_low), and the old 90 put that stop
        # too far for the 1:1 R:R floor -- the audit-fix behavior, not a bug.
        "range30m_high": 100.0, "range30m_low": 97.0,
        "rsi_4h_at_lock": 70.0,
    })
    poll_env["make_gate_log"](state="PASS")  # the stale lock-time NO_PLAN placeholder, must get overwritten
    poll_env["make_plan"](status="NO_PLAN", direction=None, trigger_price=None)
    candles = _beyond_trigger_candles(100.0, is_long=True)  # real break through BO -> LONG
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1, daily_atr14=20.0)  # box=10, atr=20 -> ratio=0.5, reachable

    row = poll_env["get_plan"]()
    assert row.status == "FILLED"
    assert row.direction == "LONG"
    assert row.tier is None  # v2 has no tier at all
    assert row.fill_price == 100.0
    assert "real cross" in row.last_transition_reason

    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA ARMED")
    assert "LONG" in sent[0][0]

    # Same "site's own row IS the verdict row" invariant as the opposite-
    # break/own-cross paths -- GateLog must reflect the real promotion,
    # not stay frozen at the lock-time NO_PLAN/PASS placeholder.
    gate_row = poll_env["get_gate_log"]()
    assert gate_row.state == "TAKE"
    assert gate_row.side == "LONG"


def test_no_plan_stays_no_plan_when_gate_still_says_no(poll_env, monkeypatch):
    sent = _capture_emails(monkeypatch)
    poll_env["make_lock"](levels={
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "range30m_high": 100.0, "range30m_low": 90.0,
    })
    poll_env["make_plan"](status="NO_PLAN", direction=None, trigger_price=None)
    candles = [{"close": 95.0} for _ in range(30)]  # still inside the box -- no cross
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "NO_PLAN"  # unchanged -- still silently waiting
    assert sent == []  # no email for a non-event


def test_no_plan_wick_through_still_forming_candle_does_not_trigger_evaluation(poll_env, monkeypatch):
    # THE exact 2026-09-04 P0 incident (Kabroda AI Brain repo AGENT_LOG.md,
    # DeepSeek + Andy), reproduced end to end through the REAL poll loop --
    # not just market_data.confirmed_5m_closes()'s own isolated unit tests.
    # An 8:35 CT bar wicked through BD (low 78,973) but closed back above
    # it (79,349); the system evaluated it as a real cross anyway because
    # candles_5m[-1]["close"] was trusted even though that bar's 5-minute
    # window hadn't elapsed yet. Reproduced here at trigger=90 for round
    # numbers: the trailing candle's CURRENT (in-progress) price sits below
    # BD=90, but its 5m window is still open -- must NOT read as a cross.
    import time as _time
    sent = _capture_emails(monkeypatch)
    poll_env["make_lock"](levels={
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "range30m_high": 100.0, "range30m_low": 90.0,
    })
    poll_env["make_plan"](status="NO_PLAN", direction=None, trigger_price=None)

    now = _time.time()
    def c(open_ts, close):
        return {"time": int(open_ts), "close": close}
    candles = (
        [c(now - 300 * (i + 2), 95.0) for i in range(50)][::-1]  # confirmed history, inside the box (near BD)
        + [c(now, 89.0)]  # still-forming candle, CURRENT price wicked below BD -- NOT yet confirmed
    )
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "NO_PLAN"  # the wick must NOT have been read as a cross
    assert sent == []  # no evaluation fired -> no email, matching Andy's exact report


def test_no_plan_confirmed_close_through_trigger_does_trigger_evaluation(poll_env, monkeypatch):
    # The other half of the P0 fix: once that same candle's window has
    # genuinely elapsed (a real, confirmed close beyond the trigger), the
    # cross MUST still be detected -- confirmed_5m_closes() only strips a
    # truly still-forming trailing candle, never a real closed one. v2:
    # fuel is gone, so this now just needs the real 4-condition gate to
    # pass on the confirmed cross (a SHORT here, for variety).
    import time as _time
    sent = _capture_emails(monkeypatch)
    monkeypatch.setattr(market_regime, "classify_market_regime", lambda candles: {
        "table": "TRENDING_UP", "quality": "GOOD", "policy": {"bias": "DOWN"},
    })
    monkeypatch.setattr(micro_regime, "classify_regime", lambda candles: {"regime": "TRENDING"})
    _gate_pass(monkeypatch)
    poll_env["make_lock"](levels={
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        # range30m_high 94 (not 100): a SHORT NO_PLAN promotion applies
        # v2's one r30 stop (r30_high + 0.12*box above the BD entry); the
        # old 100 put that stop past the 1:1 R:R floor for T1 -- the
        # audit-fix behavior, consistent with the other fill paths.
        "range30m_high": 94.0, "range30m_low": 90.0,
        "rsi_4h_at_lock": 30.0,
    })
    poll_env["make_gate_log"](state="PASS")
    poll_env["make_plan"](status="NO_PLAN", direction=None, trigger_price=None)

    now = _time.time()
    def c(open_ts, close):
        return {"time": int(open_ts), "close": close}
    candles = (
        [c(now - 300 * (i + 8), 92.0) for i in range(250)][::-1]  # baseline, near BD
        + [c(now - 300 * (i + 2), 85.0) for i in range(6)][::-1]  # real, CONFIRMED closes through BD
    )
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1, daily_atr14=20.0)

    row = poll_env["get_plan"]()
    assert row.status == "FILLED"  # a real, confirmed cross -- must be detected
    assert len(sent) == 1


def test_no_plan_real_cross_declined_by_gate_becomes_done_with_vetoed_email(poll_env, monkeypatch):
    # THE gap caught before this ever shipped: an earlier draft only
    # handled the TAKE case above and silently did nothing for a real
    # cross the gate declines -- the agreed contract requires "fail ->
    # VETOED + email with reason," not silence. v2 (2026-09-15): the
    # specific miss here is "no HTF carry" -- the counter-trend veto this
    # test used to exercise is retired (decision_engine.py's header
    # comment, n=0 measured on the real v2 population).
    sent = _capture_emails(monkeypatch)
    monkeypatch.setattr(market_regime, "classify_market_regime", lambda candles: {
        "table": "TRENDING_UP", "quality": "GOOD", "policy": {"bias": "UP"},
    })
    monkeypatch.setattr(micro_regime, "classify_regime", lambda candles: {"regime": "TRENDING"})
    monkeypatch.setattr(htf_fuel, "htf_fuel", lambda c1h, c4h, side: {"aligned": 0})
    monkeypatch.setattr(htf_fuel, "krown_cross_votes", lambda c1h, c4h, side: {"votes": 2})

    poll_env["make_lock"](levels={
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "range30m_high": 100.0, "range30m_low": 90.0,
    })
    poll_env["make_gate_log"](state="PASS")
    poll_env["make_plan"](status="NO_PLAN", direction=None, trigger_price=None)
    candles = _beyond_trigger_candles(90.0, is_long=False)  # real break through BD -> SHORT, no HTF carry
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1, daily_atr14=20.0)

    row = poll_env["get_plan"]()
    assert row.status == "DONE"
    assert "carry" in row.last_transition_reason.lower()

    assert len(sent) == 1
    assert sent[0][0].startswith("KABRODA VETOED")
    assert "SHORT" in sent[0][0]

    gate_row = poll_env["get_gate_log"]()
    assert gate_row.state == "PASS"
    assert gate_row.side == "SHORT"


def test_no_plan_session_expired_no_cross_becomes_done_without_email(poll_env, monkeypatch):
    sent = _capture_emails(monkeypatch)
    poll_env["make_plan"](status="NO_PLAN", direction=None, trigger_price=None, date_key="2020-01-01")
    poll_env["run_polls"](polls=1)  # no candles/lock needed -- expiry check comes first, same as STOPPED's own test

    row = poll_env["get_plan"]()
    assert row.status == "DONE"
    assert "session ended" in row.last_transition_reason
    # The STAND DOWN lock email already told Andy nothing would follow
    # unless a real cross changed it -- this bookkeeping transition must
    # NOT contradict that with a second email.
    assert sent == []


def test_waiting_cross_fills_via_loop(poll_env, monkeypatch):
    _gate_pass(monkeypatch)
    poll_env["make_plan"](status="WAITING", direction="LONG", trigger_price=100.0, t2=110.0, rsi_4h_at_lock=70.0)
    candles = _beyond_trigger_candles(100.0, is_long=True)
    poll_env["run_polls"](candles_5m_by_symbol={"BTC/USDT": candles}, polls=1, daily_atr14=40.0)

    row = poll_env["get_plan"]()
    assert row.status == "FILLED"
    assert row.fill_price == 100.0
    assert row.entry_mode in ("TRIGGER_AT_LEVEL", "RETEST_LIMIT_AT_LINE")


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


def test_reentry_filled_t1_reached_stays_filled_now_that_resolve_reentry_fill_is_gone(poll_env):
    # v2 (2026-09-15): resolve_reentry_fill() -- the only thing that used to
    # resolve a reentry_used=True FILLED plan to DONE at T1 ("documented
    # gap, not guessed") -- was deleted along with the rest of the dead SS8
    # re-entry chain (confirmed unreachable; see trade_plan.py's own
    # comment where the functions used to be). mirror_campaign_outcome()'s
    # own reentry_used guard now means a plan in this state (impossible to
    # create new -- nothing sets reentry_used=True any more -- but a
    # hypothetical pre-rebuild leftover row could still carry the flag)
    # simply stays FILLED forever via this path instead of ever resolving.
    # Accepted, documented gap for a state that can no longer be reached by
    # any current code path (the live DB has zero such rows as of this
    # audit) -- not worth a new resolution mechanism for dead state.
    poll_env["make_plan"](
        status="FILLED", direction="LONG", trigger_price=100.0,
        stop_price=90.0, t1=112.0, fill_time=poll_env["now"] - timedelta(minutes=5),
        reentry_used=True,
    )
    poll_env["make_campaign"](status="CLOSED_LOSS", target_hit="STOP")  # stale, must be ignored
    candles_1m = [_c1m(111.0, 113.0)]  # T1 reached
    poll_env["run_polls"](candles_1m_by_symbol={"BTC/USDT": candles_1m}, polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "FILLED"


def test_stopped_always_resolves_to_done_via_loop(poll_env):
    # v2 (2026-09-11): SS8's fuel-gated re-entry-after-wick-fake is retired
    # -- trade_plan_engine.py's STOPPED branch now resolves unconditionally
    # to DONE on the very next poll, no candle data even consulted (there
    # is no more NO_PUSH-stays-STOPPED / FUELED-recross-becomes-REENTRY_
    # ARMED / thin-recross-becomes-DONE distinction to make -- replaces
    # four now-obsolete v1 tests that each exercised one of those branches).
    poll_env["make_plan"](status="STOPPED", direction="LONG", trigger_price=100.0, reentry_used=False)
    poll_env["run_polls"](polls=1)

    row = poll_env["get_plan"]()
    assert row.status == "DONE"
    assert "not part of v2" in row.last_transition_reason
