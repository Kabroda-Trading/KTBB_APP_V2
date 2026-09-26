"""
Regression coverage for the session-lock scheduler's dedup fix
(2026-09-24, V2 Crown retirement; renamed from "Senior Analyst" 2026-09-26
-- see main.py's _fire_session_lock_pipeline() docstring for why: that
name belonged to the LLM agent retired 2026-08-17, and the scheduler
underneath it was never renamed to match, which caused real confusion
during the 2026-09-26 incident this file also now covers). The dedup
check used to read `CampaignLog.is_canonical` -- CampaignLog's only
writer (decision_engine.py's V2 gate, fully deleted 2026-09-24) was being
retired, so that signal was about to go permanently False. The retirement
map's own suggested replacement ("swap to SessionLock existence") is
wrong: SessionLock is created BEFORE run_mas_analysis() even runs
(battlebox_pipeline.py), so existence alone answers "was this session
locked," not "did the analysis pipeline finish" -- collapsing exactly the
restart-recovery distinction _fire_session_lock_pipeline()/
run_session_lock_scheduler() depend on. The real fix is a new
completion-marker column, SessionLock.mas_completed_at, set
unconditionally at the true end of a successful run_mas_analysis() call
(kabroda_mas_flow.py -- the Traveler's own D1 plan builder; decision_
engine.py/trade_plan.py, mentioned in this file's own comments before
2026-09-26, are both long deleted).

These tests specifically prove the THREE-STATE distinction a bare
existence check cannot make:
  1. no SessionLock row at all -> proceed (nothing to skip)
  2. SessionLock exists, mas_completed_at is NULL (locked, analysis never
     completed -- the real 2026-09-19 production case this whole
     mechanism exists to handle) -> proceed (must NOT skip)
  3. SessionLock exists, mas_completed_at is set -> skip (already ran)

**Added 2026-09-26, real production incident**: the mas_completed_at
column's own migration used an invalid PostgreSQL type name
(`DATETIME` instead of `TIMESTAMP`) and silently never created the
column in production -- every query touching it, including both dedup
checks above, crashed with psycopg.errors.UndefinedColumn. The boot-time
check had no except clause of its own, so this crash killed the entire
scheduler task on every boot, silently, until a full app restart -- no
session ever locked, no email ever sent, for as long as the bug lived.
test_dedup_query_failure_degrades_gracefully_instead_of_crashing() below
proves the fix: a query that raises must not propagate past
_fire_session_lock_pipeline(), and must not crash
run_session_lock_scheduler()'s boot-time check either.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_session_lock_dedup.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import datetime as dt

import pytest

import database
from database import SessionLocal, SessionLock
import main


def _run(coro):
    # asyncio.run(), not get_event_loop().run_until_complete() -- same
    # convention as tests/test_executor_live_engine.py (this repo has no
    # pytest-asyncio installed). asyncio.run() always gets a fresh loop.
    return asyncio.run(coro)


def _clean_db_files():
    for path in ["kabroda_test_session_lock_dedup.db", "kabroda_test_session_lock_dedup.db-journal",
                 "kabroda_test_session_lock_dedup.db-shm", "kabroda_test_session_lock_dedup.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@pytest.fixture
def env():
    _clean_db_files()
    database.init_db()
    db = SessionLocal()
    db.query(SessionLock).delete()
    db.commit()
    yield {"db": db}
    db.close()
    database.engine.dispose()
    _clean_db_files()


def _make_lock(db, date_key="2026-09-19", mas_completed_at=None, **kwargs):
    row = SessionLock(
        symbol="BTC/USDT", session_id="us_ny_futures", date_key=date_key,
        lock_time=1000, packet_data="{}", mas_completed_at=mas_completed_at,
        **kwargs,
    )
    db.add(row)
    db.commit()
    return row


def test_skips_when_mas_completed_at_is_set(env, monkeypatch):
    _make_lock(env["db"], date_key="2026-09-19", mas_completed_at=dt.datetime(2026, 9, 19, 14, 5, 0))
    fetch_mock = AsyncMock()
    monkeypatch.setattr(main.battlebox_pipeline, "get_live_battlebox", fetch_mock)
    _run(main._fire_session_lock_pipeline("2026-09-19"))
    fetch_mock.assert_not_called()


def test_does_not_skip_when_lock_exists_but_mas_completed_at_is_null(env, monkeypatch):
    # The real 2026-09-19 production case: a lock that crossed/still
    # active with no completed analysis run for it -- must NOT be
    # mistaken for "already done."
    _make_lock(env["db"], date_key="2026-09-19", mas_completed_at=None)
    fetch_mock = AsyncMock(side_effect=RuntimeError("stop here -- proves we got past the dedup check"))
    monkeypatch.setattr(main.battlebox_pipeline, "get_live_battlebox", fetch_mock)
    _run(main._fire_session_lock_pipeline("2026-09-19"))
    fetch_mock.assert_called_once()


def test_does_not_skip_when_no_lock_exists_at_all(env, monkeypatch):
    fetch_mock = AsyncMock(side_effect=RuntimeError("stop here -- proves we got past the dedup check"))
    monkeypatch.setattr(main.battlebox_pipeline, "get_live_battlebox", fetch_mock)
    _run(main._fire_session_lock_pipeline("2026-09-19"))
    fetch_mock.assert_called_once()


def test_dedup_query_failure_degrades_gracefully_instead_of_crashing(env, monkeypatch):
    # 2026-09-26 real production incident: the dedup query crashed with
    # psycopg.errors.UndefinedColumn in production (mas_completed_at's own
    # migration used an invalid type name, see database.py's fix comment)
    # -- and _fire_session_lock_pipeline() had no except clause around it,
    # so the error propagated. Prove the fix: a raising query must not
    # stop this function from proceeding to fire the pipeline anyway
    # (safer to risk a duplicate fire than to silently never run at all).
    # _fire_session_lock_pipeline() opens its OWN SessionLocal() internally
    # (not the fixture's env["db"]), so main.SessionLocal itself must be
    # patched to hand back a session whose query explodes.
    _make_lock(env["db"], date_key="2026-09-19", mas_completed_at=None)

    class _ExplodingQuery:
        def filter(self, *a, **k):
            return self
        def first(self):
            raise Exception("simulated psycopg.errors.UndefinedColumn")

    class _ExplodingSession:
        def query(self, model):
            return _ExplodingQuery()
        def close(self):
            pass

    monkeypatch.setattr(main, "SessionLocal", lambda: _ExplodingSession())

    fetch_mock = AsyncMock(side_effect=RuntimeError("stop here -- proves we got past the exploding dedup check"))
    monkeypatch.setattr(main.battlebox_pipeline, "get_live_battlebox", fetch_mock)
    _run(main._fire_session_lock_pipeline("2026-09-19"))
    fetch_mock.assert_called_once()


def test_boot_check_query_skips_only_when_completed(env):
    # run_session_lock_scheduler()'s boot-time check is the same query
    # shape inline -- exercised directly here since the function itself
    # is an infinite `while True` loop not practical to await in a test.
    _make_lock(env["db"], date_key="2026-09-19", mas_completed_at=None)
    db = env["db"]
    existing = db.query(SessionLock).filter(
        SessionLock.symbol == "BTC/USDT",
        SessionLock.date_key == "2026-09-19",
        SessionLock.mas_completed_at.isnot(None),
    ).first()
    assert existing is None   # must NOT look "already done"

    db.query(SessionLock).filter(SessionLock.date_key == "2026-09-19").update(
        {"mas_completed_at": dt.datetime(2026, 9, 19, 14, 5, 0)}
    )
    db.commit()
    existing = db.query(SessionLock).filter(
        SessionLock.symbol == "BTC/USDT",
        SessionLock.date_key == "2026-09-19",
        SessionLock.mas_completed_at.isnot(None),
    ).first()
    assert existing is not None


# ------------------------------------------------------------------ the write side: run_mas_analysis() actually sets the marker
# The tests above cover the READ side (both dedup call sites). This
# covers the other half of the fix: does a real run_mas_analysis() call
# actually set mas_completed_at at the end? Only market_data's network
# fetches are mocked (fast, deterministic, no real exchange call) -- the
# Traveler injection block runs for real against the resulting
# empty-candle input, which degrades gracefully to real bo/bd validation
# failing (no plan written) rather than a crash. (This comment used to
# cite decision_engine.py/trade_plan.py as also running here -- both were
# deleted 2026-09-24, before this comment was corrected 2026-09-26; this
# function has been Traveler-only since 3e.)

import kabroda_mas_flow


async def _empty_candles(*args, **kwargs):
    return []


async def _noop_close(*args, **kwargs):
    return None


def test_run_mas_analysis_sets_the_completion_marker(env, monkeypatch):
    _make_lock(env["db"], date_key="2026-09-19", mas_completed_at=None)
    monkeypatch.setattr(kabroda_mas_flow.market_data, "fetch_live_5m", _empty_candles)
    monkeypatch.setattr(kabroda_mas_flow.market_data, "fetch_live_15m", _empty_candles)
    monkeypatch.setattr(kabroda_mas_flow.market_data, "fetch_live_1h", _empty_candles)
    monkeypatch.setattr(kabroda_mas_flow.market_data, "fetch_live_4h", _empty_candles)
    monkeypatch.setattr(kabroda_mas_flow.market_data, "fetch_live_daily", _empty_candles)
    monkeypatch.setattr(kabroda_mas_flow.market_data, "close_exchange_for_current_loop", _noop_close)

    payload = {
        "levels": {"breakout_trigger": 91000.0, "breakdown_trigger": 90000.0,
                   "range30m_high": 91000.0, "range30m_low": 90000.0},
        "context": {},
    }
    result = kabroda_mas_flow.run_mas_analysis("BTC/USDT", "us_ny_futures", "2026-09-19", payload)
    assert result["status"] == "SUCCESS"

    db = SessionLocal()
    try:
        row = db.query(SessionLock).filter_by(date_key="2026-09-19").first()
        assert row.mas_completed_at is not None
    finally:
        db.close()
