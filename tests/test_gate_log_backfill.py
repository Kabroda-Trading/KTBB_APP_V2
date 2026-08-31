"""
Regression coverage for ledger_closing_engine.py's two GateLog backfill
passes (KABRODA_COM_TRADE_PLAN_SPEC.md SS9a):

- _backfill_gate_log(): CampaignLog-sourced fields, including faked_first
  now pulled from the matching TradePlan row (2026-08-31 -- previously
  hardcoded None, a documented gap this closes).
- _backfill_gate_log_execution() (NEW): TradePlan-sourced execution
  fields, gated on TradePlan's OWN terminal state (NO_PLAN/DONE) via a
  separate execution_backfilled_at flag -- decoupled from
  _backfill_gate_log()'s CampaignLog-gated backfilled_at, since the two
  records don't always resolve on the same timeline.

Both functions are plain sync functions taking (db, now_utc) -- tested
directly against a real throwaway DB with hand-constructed rows, no
monkeypatching needed.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_gate_log_backfill.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import datetime as dt
from datetime import timezone, timedelta

import pytest

import database
from database import SessionLocal, CampaignLog, GateLog, TradePlan
import ledger_closing_engine as lce


def _clean_db_files():
    for path in ["kabroda_test_gate_log_backfill.db", "kabroda_test_gate_log_backfill.db-journal",
                 "kabroda_test_gate_log_backfill.db-shm", "kabroda_test_gate_log_backfill.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@pytest.fixture
def db_env():
    _clean_db_files()
    database.init_db()
    db = SessionLocal()
    db.query(GateLog).delete()
    db.query(CampaignLog).delete()
    db.query(TradePlan).delete()
    db.commit()

    now = dt.datetime.now(timezone.utc)

    def make_gate_log(symbol="BTC/USDT", date_key="2026-08-31", state="TAKE_STANDARD", **kwargs):
        row = GateLog(symbol=symbol, date_key=date_key, state=state, **kwargs)
        db.add(row)
        db.commit()
        return row

    def make_campaign(symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures", **kwargs):
        defaults = dict(
            symbol=symbol, date_key=date_key, session_id=session_id,
            bias="LONG", grade="TAKE_STANDARD", entry_price=100.0, stop_loss=90.0,
            t1=110.0, total_contracts=1.0, status="CLOSED_LOSS", target_hit="STOP",
            realized_pnl=-1.0, is_canonical=True, closed_at=now - timedelta(hours=1),
        )
        defaults.update(kwargs)
        row = CampaignLog(**defaults)
        db.add(row)
        db.commit()
        return row

    def make_plan(symbol="BTC/USDT", date_key="2026-08-31", session_id="us_ny_futures", **kwargs):
        defaults = dict(symbol=symbol, date_key=date_key, session_id=session_id, status="NO_PLAN")
        defaults.update(kwargs)
        row = TradePlan(**defaults)
        db.add(row)
        db.commit()
        return row

    yield {"db": db, "now": now, "make_gate_log": make_gate_log,
           "make_campaign": make_campaign, "make_plan": make_plan}

    db.close()
    database.engine.dispose()
    _clean_db_files()


def test_backfill_pulls_faked_first_from_matching_tradeplan(db_env):
    gl = db_env["make_gate_log"]()
    db_env["make_campaign"]()
    db_env["make_plan"](status="DONE", faked_first=True)

    lce._backfill_gate_log(db_env["db"], db_env["now"])

    db_env["db"].refresh(gl)
    assert gl.faked_first is True
    assert gl.backfilled_at is not None
    assert gl.stopped_first is True
    assert gl.r_t1only == pytest.approx(-1.0)


def test_backfill_faked_first_none_when_no_matching_tradeplan(db_env):
    gl = db_env["make_gate_log"]()
    db_env["make_campaign"]()
    # No TradePlan row created at all.

    lce._backfill_gate_log(db_env["db"], db_env["now"])

    db_env["db"].refresh(gl)
    assert gl.faked_first is None
    assert gl.backfilled_at is not None  # still backfills the CampaignLog-sourced fields


def test_execution_backfill_skips_while_tradeplan_still_in_flight(db_env):
    gl = db_env["make_gate_log"]()
    db_env["make_plan"](status="FILLED", entry_mode="TRIGGER_AT_LEVEL", fill_price=100.0)

    lce._backfill_gate_log_execution(db_env["db"], db_env["now"])

    db_env["db"].refresh(gl)
    assert gl.execution_backfilled_at is None  # not captured -- still in flight
    assert gl.execution_fill_price is None


def test_execution_backfill_fills_from_done_tradeplan(db_env):
    gl = db_env["make_gate_log"]()
    db_env["make_plan"](
        status="DONE", entry_mode="TRIGGER_AT_LEVEL", fill_time=db_env["now"] - timedelta(hours=2),
        fill_price=100.0, stop_price=95.0, stop_basis="beyond sweep wick low",
        stop_dist_atr=1.2, reentry_used=True,
    )

    lce._backfill_gate_log_execution(db_env["db"], db_env["now"])

    db_env["db"].refresh(gl)
    assert gl.execution_backfilled_at is not None
    assert gl.execution_entry_mode == "TRIGGER_AT_LEVEL"
    assert gl.execution_fill_price == 100.0
    assert gl.execution_stop_price == 95.0
    assert gl.execution_stop_basis == "beyond sweep wick low"
    assert gl.reentry_used is True


def test_execution_backfill_captures_no_plan_days_too(db_env):
    gl = db_env["make_gate_log"](state="PASS")
    db_env["make_plan"](status="NO_PLAN", no_plan_reason="box/ATR ratio 1.42 > 0.55")

    lce._backfill_gate_log_execution(db_env["db"], db_env["now"])

    db_env["db"].refresh(gl)
    assert gl.execution_backfilled_at is not None
    assert gl.execution_fill_price is None  # nothing to fill -- correctly all-None


def test_execution_backfill_skips_when_no_matching_tradeplan(db_env):
    gl = db_env["make_gate_log"]()
    # No TradePlan row at all.

    lce._backfill_gate_log_execution(db_env["db"], db_env["now"])

    db_env["db"].refresh(gl)
    assert gl.execution_backfilled_at is None  # left for a future tick, not guessed
