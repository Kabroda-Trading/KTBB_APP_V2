"""
Regression coverage for ledger_closing_engine.py's real 30/70 runner
mechanic (KABRODA_REBUILD_SPEC.md §6: 30% off at T1, stop moves to the
runner-stop level, 70% rides to T3 -- validated to beat both a 50/50 split
and a 100%-at-T1 close in the calibration backtest).

Found 2026-08-30: the real status/realized_pnl/closed_at fields were still
closing 100% at T1 (the REJECTED alternative), with only a separate,
non-authoritative "shadow" tracker modeling a DIFFERENT rejected split
(50/50). Fixed by making the validated rule the real, live mechanic.

These tests run the ACTUAL run_ledger_audit_loop() coroutine against
monkeypatched exchange calls and synthetic candle sequences -- exercising
the real production code path, not a reimplementation of its logic.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_runner.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import datetime as dt
from datetime import timezone, timedelta

import pytest

import database
from database import SessionLocal, CampaignLog
import ledger_closing_engine as lce


def _clean_db_files():
    for path in ["kabroda_test_runner.db", "kabroda_test_runner.db-journal",
                 "kabroda_test_runner.db-shm", "kabroda_test_runner.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


# ── Shared round-number setup (LONG side) ────────────────────────────────
ENTRY = 100.0
ORIG_STOP = 80.0                  # risk = 20
BOX = 100.0                       # t2 = entry + 1.0*box
T1 = ENTRY + 0.618 * BOX          # 161.8
T2 = ENTRY + 1.0 * BOX            # 200.0
T3 = ENTRY + 1.618 * BOX          # 261.8
RUNNER_STOP = ENTRY - 0.15 * BOX  # 85.0


def _frac_r(entry, stop, exit_price, is_long=True):
    risk = abs(entry - stop)
    move = (exit_price - entry) if is_long else (entry - exit_price)
    return move / risk


T1_R = _frac_r(ENTRY, ORIG_STOP, T1)
T1_LEG_R = 0.30 * T1_R
RUNNER_STOP_R = _frac_r(ENTRY, ORIG_STOP, RUNNER_STOP)
T3_R = _frac_r(ENTRY, ORIG_STOP, T3)
EXPECTED_RUNNER_LOSS_R = T1_LEG_R + 0.70 * RUNNER_STOP_R
EXPECTED_RUNNER_WIN_R = T1_LEG_R + 0.70 * T3_R


class _StopLoop(Exception):
    """Raised by the mocked asyncio.sleep to end run_ledger_audit_loop()'s
    while-True after a fixed number of iterations."""


@pytest.fixture
def poll_env(monkeypatch):
    """Sets up a fresh DB, monkeypatches the exchange-facing calls, and
    returns helpers for building rows/candle sequences. Runs
    run_ledger_audit_loop() for exactly `polls` iterations when called."""
    _clean_db_files()
    database.init_db()

    now = dt.datetime.now(timezone.utc)
    filled_at = now - timedelta(minutes=30)

    def make_candle(ts_offset_min, o, h, l, c):
        ts = int((filled_at + timedelta(minutes=ts_offset_min)).timestamp() * 1000)
        return {"ts": ts, "o": o, "h": h, "l": l, "c": c}

    def make_row(symbol, t1=T1, t2=T2, t3=T3, session_expires=None):
        if session_expires is None:
            session_expires = now + timedelta(hours=6)
        db = SessionLocal()
        row = CampaignLog(
            symbol=symbol, date_key="2026-08-31", session_id="us_ny_futures",
            bias="LONG", grade="TAKE_STANDARD",
            entry_price=ENTRY, stop_loss=ORIG_STOP, t1=t1, t2=t2, t3=t3,
            total_contracts=1.0, status="PENDING", realized_pnl=0.0,
            mas_approval_status="APPROVED", conviction="TAKE_STANDARD",
            entry_filled_at=filled_at, session_expires_at=session_expires,
            is_canonical=True, session_timeframe="15M",
        )
        db.add(row)
        db.commit()
        db.close()

    def run_polls(fetch_by_symbol_and_poll, polls=1):
        """fetch_by_symbol_and_poll: dict[symbol] -> list-of-candle-lists,
        one entry per poll (index by poll number, clamped to the last
        available entry if fewer sequences than polls are given)."""
        state = {"poll": 0}

        async def fake_fetch(symbol, since_ms, limit=720):
            seqs = fetch_by_symbol_and_poll.get(symbol)
            if not seqs:
                return []
            idx = min(state["poll"], len(seqs) - 1)
            return seqs[idx]

        async def fake_price(symbol):
            return ENTRY

        sleeps = {"n": 0}

        async def fake_sleep(seconds):
            sleeps["n"] += 1
            state["poll"] += 1
            if sleeps["n"] >= polls:
                raise _StopLoop()

        monkeypatch.setattr(lce, "_fetch_1m_since", fake_fetch)
        monkeypatch.setattr(lce, "_get_live_price", fake_price)
        monkeypatch.setattr(lce.asyncio, "sleep", fake_sleep)

        async def main():
            try:
                await lce.run_ledger_audit_loop()
            except _StopLoop:
                pass

        asyncio.run(main())

    def get_row(symbol):
        db = SessionLocal()
        row = db.query(CampaignLog).filter(CampaignLog.symbol == symbol).first()
        db.close()
        return row

    yield {
        "now": now, "filled_at": filled_at,
        "candle": make_candle, "make_row": make_row,
        "run_polls": run_polls, "get_row": get_row,
    }

    database.engine.dispose()
    _clean_db_files()


def test_stop_hit_before_t1(poll_env):
    """Stop touches before T1 -- unchanged behavior: full CLOSED_LOSS, -1R."""
    poll_env["make_row"]("SCNA")
    seq = [poll_env["candle"](0, 100, 101, 79, 79)]
    poll_env["run_polls"]({"SCNA": [seq]}, polls=1)

    row = poll_env["get_row"]("SCNA")
    assert row.status == "CLOSED_LOSS"
    assert row.target_hit == "STOP"
    assert row.realized_pnl == pytest.approx(-1.0)
    assert row.runner_active is False


def test_t1_then_runner_stop_across_polls(poll_env):
    """T1 hit on poll 1 opens the runner; runner-stop hit on poll 2 closes
    it at the correctly blended R. Also proves cross-poll continuity: poll
    2's re-fetched batch re-includes the stale pre-T1 candle, which must
    NOT be mistaken for a fresh runner-stop/T3 touch."""
    poll_env["make_row"]("SCNB")
    poll1 = [poll_env["candle"](0, 100, 165, 99, 162)]           # touches T1
    poll2 = poll1 + [poll_env["candle"](1, 162, 163, 84, 90)]    # touches runner-stop 85
    poll_env["run_polls"]({"SCNB": [poll1, poll2]}, polls=2)

    row = poll_env["get_row"]("SCNB")
    assert row.status == "CLOSED_LOSS"
    assert row.target_hit == "RUNNER_STOP"
    assert row.realized_pnl == pytest.approx(EXPECTED_RUNNER_LOSS_R)
    assert row.t1_leg_r == pytest.approx(T1_LEG_R)
    assert row.runner_stop == pytest.approx(RUNNER_STOP)


def test_t1_then_t3_across_polls(poll_env):
    """T1 hit on poll 1 opens the runner; T3 hit on poll 2 closes it as a
    win at the correctly blended R."""
    poll_env["make_row"]("SCNC")
    poll1 = [poll_env["candle"](0, 100, 165, 99, 162)]
    poll2 = poll1 + [poll_env["candle"](1, 162, 262, 160, 260)]  # touches T3
    poll_env["run_polls"]({"SCNC": [poll1, poll2]}, polls=2)

    row = poll_env["get_row"]("SCNC")
    assert row.status == "CLOSED_WIN"
    assert row.target_hit == "T3"
    assert row.realized_pnl == pytest.approx(EXPECTED_RUNNER_WIN_R)


def test_t1_and_t3_same_batch(poll_env):
    """T1 and T3 both touch within the SAME candle batch (a fast-moving
    day) -- must resolve fully in one pass, not defer to a second poll."""
    poll_env["make_row"]("SCND")
    seq = [
        poll_env["candle"](0, 100, 165, 99, 162),   # touches T1
        poll_env["candle"](1, 162, 262, 160, 260),  # touches T3, same batch
    ]
    poll_env["run_polls"]({"SCND": [seq]}, polls=1)

    row = poll_env["get_row"]("SCND")
    assert row.status == "CLOSED_WIN"
    assert row.target_hit == "T3"
    assert row.realized_pnl == pytest.approx(EXPECTED_RUNNER_WIN_R)


def test_legacy_row_without_t2_t3_falls_back_to_terminal_t1(poll_env):
    """A row missing t2/t3 (nothing to derive a runner-stop from) must fall
    back to the pre-2026-08-30 terminal-at-T1 close instead of crashing."""
    poll_env["make_row"]("SCNF", t2=None, t3=None)
    seq = [poll_env["candle"](0, 100, 165, 99, 162)]
    poll_env["run_polls"]({"SCNF": [seq]}, polls=1)

    row = poll_env["get_row"]("SCNF")
    assert row.status == "CLOSED_WIN"
    assert row.target_hit == "T1"
    assert row.realized_pnl == pytest.approx(T1_R)
    assert row.runner_active is False


def test_runner_active_unresolved_at_session_expiry(poll_env):
    """T1 hit opens the runner; neither runner-stop nor T3 touch before the
    next session open -- must close CLOSED_AT_EXPIRY with R blended from
    the locked-in T1 leg plus a mark-to-market runner leg, not just the
    plain single-leg fractional R."""
    now = poll_env["now"]
    poll_env["make_row"]("SCNG", session_expires=now - timedelta(days=2))
    seq = [poll_env["candle"](0, 100, 165, 99, 162)]  # touches T1, final close 162
    poll_env["run_polls"]({"SCNG": [seq]}, polls=1)

    expected = T1_LEG_R + 0.70 * _frac_r(ENTRY, ORIG_STOP, 162.0)
    row = poll_env["get_row"]("SCNG")
    assert row.status == "CLOSED_AT_EXPIRY"
    assert row.target_hit == "EXPIRY"
    assert row.realized_pnl == pytest.approx(expected)
    assert row.runner_active is True
