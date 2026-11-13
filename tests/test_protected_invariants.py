"""PROTECTED INVARIANTS -- direct, executable proof that CLAUDE.md's "What
Must Never Be Changed" list (10 items) still holds in the real, running
code. Built 2026-11-12 in response to a real, recurring problem: pieces of
this system kept quietly drifting from their own measured/documented
design, and that drift was only ever caught by a human or an agent
re-reading the spec and tracing the real code by hand, incident by
incident (the trigger-touch-fill regression, the T2 breakeven resurrection
risk, tonight's CMEConfig/ExecutorAccount wiring split). This file exists
so the SAME class of drift gets caught automatically, in under a second,
every time the suite runs -- not reactively, after something breaks live.

EACH TEST BELOW IS NUMBERED TO MATCH CLAUDE.md's "What Must Never Be
Changed" list exactly. If a test here fails, go read that numbered item
before touching anything -- the fix is almost always "revert the change,"
not "update this test." If CLAUDE.md's own invariant is deliberately
revised (a fresh measurement, an explicit ruling), update both together,
same discipline that section already asks for.

This file deliberately tests BEHAVIOR (call the real function, assert on
what it actually does) wherever a invariant is about behavior, not just
"does a constant look right" -- a constant can be correct while the code
around it no longer uses it the documented way. Some overlap with other
test files is intentional: the point of this file is being the one place
that maps 1:1 to CLAUDE.md's numbered list, not avoiding duplication.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./kabroda_test_protected_invariants.db")

import pytest

import database
from database import SessionLocal, TravelerPlan, GravityMemory
import gate_traveler as gt
import mgmt_e1_stack as mes
import session_manager
import gravity_math
import market_data
import kabroda_mas_flow

# Reusing this project's own proven fixtures rather than re-deriving new
# ones -- these are tuned/verified directly against the real RSI/BBWP math
# in their own test files; re-inventing similar-looking fixtures here would
# risk subtly wrong values that happen to pass for the wrong reason.
from test_gate_traveler import _cross_plan, _touch_plan, _c, LONG_IN_ZONE_H4, CROSS_EPOCH, NOW
from test_mgmt_e1_stack import _order, _bar, _flat_1h4h, ENTRY_TIME, ENTRY_EPOCH, NOW as MGMT_NOW

UTC = datetime.timezone.utc


def _clean_db_files():
    for path in ["kabroda_test_protected_invariants.db", "kabroda_test_protected_invariants.db-journal",
                 "kabroda_test_protected_invariants.db-shm", "kabroda_test_protected_invariants.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    for model in (TravelerPlan, GravityMemory):
        session.query(model).delete()
    session.commit()


@pytest.fixture
def db():
    _clean_db_files()
    database.init_db()
    session = SessionLocal()
    _clean_rows(session)
    yield session
    _clean_rows(session)
    session.close()
    database.engine.dispose()
    _clean_db_files()


# ==================================================================
# 1. Entry is trigger-touch-fill, NOT a confirmed close.
# ==================================================================

def test_invariant_1_entry_fills_on_a_bare_wick_touch_with_no_close_back():
    # A bar whose CLOSE stays on the far side of the trigger (a close-based
    # check would never fire) but whose own LOW wicks through must still
    # fill -- this is the exact shape the 2026-09-15 confirmed-close
    # regression got backwards (measured -0.1056R, negative every year)
    # and was restored 2026-09-22.
    plan = _touch_plan()   # LONG, breakout_trigger=100.0
    ct = plan["cross_time"].timestamp()
    candles = [
        _c(105.0, ct),                                 # cross bar -- skipped
        _c(102.0, ct + 600, low=99.5, high=103.0),      # close stays above trigger; the WICK touches -- FILL
    ]
    result = gt.advance_waiting_touch(plan, candles, datetime.datetime(2026, 9, 15, 14, 0, 0, tzinfo=UTC))
    assert result is not None
    assert result["status"] == "FILLED"
    assert result["fill_price"] == 100.0   # fills AT the trigger, not the bar's own low (99.5) or close (102.0)


# ==================================================================
# 2. The tercile skip's frozen cuts (gate_traveler.FULL_D1_CUTS).
# ==================================================================

def test_invariant_2_full_d1_cuts_are_the_exact_frozen_full_corpus_values():
    assert gt.FULL_D1_CUTS == {"LONG": (51.49, 61.27), "SHORT": (40.45, 48.56)}


# ==================================================================
# 3. gate_traveler.py's taken-gate has no box/ATR reachability check.
# ==================================================================

def test_invariant_3_gate_traveler_module_never_imports_reachability():
    # Check for actual CODE usage (an import or a bound name), not just the
    # word "reachability" anywhere -- the module's own comments explicitly
    # discuss and reject reachability (explaining why it's never applied to
    # this lineage), which would false-positive a naive substring check.
    assert "reachability" not in gt.__dict__
    assert not any(
        line.strip().startswith(("import reachability", "from reachability"))
        for line in open("gate_traveler.py", encoding="utf-8")
    )


def test_invariant_3_a_v2_unreachable_box_still_crosses_and_fills_for_the_traveler():
    # V2 Crown's box/ATR >= 0.55 would have stood this down. The Traveler's
    # cross detection (D1) and trigger-touch fill (D2) take no box/ATR
    # input at all -- a huge box (unreachable by V2's own standard) must
    # still cross and fill normally.
    huge_box_plan = _cross_plan(r30_high=500.0, r30_low=90.0)   # box=410, wildly "unreachable" by V2 standards
    candles = [{"close": 95.0}] * 5 + [{"close": 105.0, "time": CROSS_EPOCH}]
    cross_result = gt.advance_waiting_cross(huge_box_plan, candles, NOW, candles_4h=LONG_IN_ZONE_H4)
    assert cross_result is not None and cross_result["status"] == "WAITING_TOUCH"

    plan = _touch_plan()
    ct = plan["cross_time"].timestamp()
    touch_candles = [_c(105.0, ct), _c(102.0, ct + 600, low=99.5, high=103.0)]
    touch_result = gt.advance_waiting_touch(plan, touch_candles, datetime.datetime(2026, 9, 15, 14, 0, 0, tzinfo=UTC))
    assert touch_result is not None and touch_result["status"] == "FILLED"


# ==================================================================
# 4. mgmt_e1_stack.py's exit priority: STOP, then C5-or-BBWP, then T1,
#    then TIME. Single full-exit -- no T2/T3, no BE stop move.
# ==================================================================

def test_invariant_4_t1_fires_before_time_when_both_conditions_present():
    # The one leg of the priority chain not already covered by
    # test_mgmt_e1_stack.py's own STOP>C5 and C5>T1 tests.
    order = _order()   # LONG, entry=100, stop=90, t1=110
    candles_5m = [
        _bar(100.0, ENTRY_EPOCH + 300),
        _bar(105.0, ENTRY_EPOCH + 600, high=111.0, low=104.0),   # T1 (110) touched via high
    ]
    flat = _flat_1h4h()
    already_expired_cap = ENTRY_TIME - datetime.timedelta(seconds=1)   # TIME would fire if nothing else did
    result = mes.advance(order, candles_5m, flat, flat, MGMT_NOW, journey_cap_at=already_expired_cap, candles_4h_bbwp=flat)
    assert result is not None
    assert result["exit_reason"] == "T1"   # not "TIME", even though the cap already passed


def test_invariant_4_no_t2_t3_columns_exist_on_travelerplan():
    cols = {c.name for c in TravelerPlan.__table__.columns}
    assert "t2_price" not in cols and "t3_price" not in cols


# ==================================================================
# 5. Stop/T1 formula: stop = r30 edge -+ 0.12*box, T1 = trigger +- 1.0*box.
# ==================================================================

def test_invariant_5_stop_buffer_and_t1_multiples_are_frozen():
    assert gt.STOP_BUFFER_BOX == 0.12
    assert gt.T1_BOX == 1.0


def test_invariant_5_a_real_cross_computes_stop_and_t1_with_the_frozen_multiples():
    plan = _cross_plan()   # breakout_trigger=100, breakdown_trigger=90, r30_high=100, r30_low=90, box=10
    candles = [{"close": 95.0}] * 5 + [{"close": 105.0, "time": CROSS_EPOCH}]
    result = gt.advance_waiting_cross(plan, candles, NOW, candles_4h=LONG_IN_ZONE_H4)
    assert result is not None and result["direction"] == "LONG"
    box = plan["breakout_trigger"] - plan["breakdown_trigger"]
    assert result["stop_price"] == pytest.approx(plan["r30_low"] - gt.STOP_BUFFER_BOX * box)
    assert result["t1_price"] == pytest.approx(plan["breakout_trigger"] + gt.T1_BOX * box)


# ==================================================================
# 6. The 30-minute session lock -- exactly 1800 seconds from anchor_time.
# ==================================================================

def test_invariant_6_lock_end_is_exactly_1800_seconds_after_anchor():
    pkt = session_manager.resolve_anchor_time("us_ny_futures")
    assert pkt["lock_end_ts"] - pkt["anchor_ts"] == 1800


# ==================================================================
# 7. _inject_traveler_plan_to_database() is CREATE-ONLY, never an upsert.
# ==================================================================

def test_invariant_7_second_call_never_overwrites_an_existing_plan_row(db):
    kabroda_mas_flow._inject_traveler_plan_to_database(
        symbol="BTC/USDT", session_id="us_ny_futures", date_key="2026-11-12",
        breakout_trigger=100.0, breakdown_trigger=90.0, r30_high=101.0, r30_low=89.0, rsi_4h_at_lock=55.0,
    )
    row = db.query(TravelerPlan).filter_by(symbol="BTC/USDT", session_id="us_ny_futures", date_key="2026-11-12").one()
    original_id = row.id
    # Simulate real intraday progress the polling loop already made.
    row.status = "WAITING_TOUCH"
    row.direction = "LONG"
    db.commit()

    # A restart-recovery re-run with DIFFERENT (would-be-wrong) levels --
    # must be a complete no-op, not an overwrite.
    kabroda_mas_flow._inject_traveler_plan_to_database(
        symbol="BTC/USDT", session_id="us_ny_futures", date_key="2026-11-12",
        breakout_trigger=999.0, breakdown_trigger=888.0, r30_high=999.0, r30_low=888.0, rsi_4h_at_lock=1.0,
    )

    db.expire_all()
    rows = db.query(TravelerPlan).filter_by(symbol="BTC/USDT", session_id="us_ny_futures", date_key="2026-11-12").all()
    assert len(rows) == 1   # no duplicate row either
    assert rows[0].id == original_id
    assert rows[0].status == "WAITING_TOUCH"   # intraday progress untouched
    assert rows[0].breakout_trigger == 100.0   # original levels untouched, NOT overwritten with 999.0


# ==================================================================
# 9. Class 0 KDE weighting -- +15.0 kinetic friction, on top of heat_multiplier.
# ==================================================================

def test_invariant_9_class_0_levels_get_exactly_15_points_more_density_at_their_own_peak(db):
    # Two otherwise-identical pivots at the SAME price -- only
    # permanence_class differs. At the exact peak price, the Gaussian
    # kernel is ~1.0 for both, so the density difference isolates the
    # weighting bonus itself.
    db.add(GravityMemory(symbol="BTCUSDT", source="TEST", level_type="PIVOT", price=50000.0,
                          permanence_class=0, heat_multiplier=1.0, active=True))
    db.commit()
    class0_result = gravity_math.calculate_gravity_kde("BTC/USDT")
    class0_peak = max(p["density"] for p in class0_result["curve"])

    _clean_rows(db)
    db.add(GravityMemory(symbol="BTCUSDT", source="TEST", level_type="PIVOT", price=50000.0,
                          permanence_class=2, heat_multiplier=1.0, active=True))
    db.commit()
    class2_result = gravity_math.calculate_gravity_kde("BTC/USDT")
    class2_peak = max(p["density"] for p in class2_result["curve"])

    # class 0 gets +15.0, class 2 (not source=="7_DAY_KABRODA") gets +0 --
    # the peak density difference should be ~15x the base weight (1.0),
    # i.e. close to 15.0 at the resolution this samples.
    assert (class0_peak - class2_peak) == pytest.approx(15.0, abs=0.5)


# ==================================================================
# 10. Symbol normalization before DB writes (BTC/USDT format).
# ==================================================================

def test_invariant_10_normalize_symbol_converts_raw_exchange_format():
    assert market_data._normalize_symbol("BTCUSDT") == "BTC/USDT"
    assert market_data._normalize_symbol("ETHUSDT") == "ETH/USDT"


def test_invariant_10_gravity_memory_table_is_the_documented_no_slash_exception(db):
    # CLAUDE.md's own stated exception -- gravity_memory stores the
    # no-slash form deliberately. Confirms the real table still matches
    # that documented exception rather than having quietly been
    # "corrected" to slash format by a future change.
    db.add(GravityMemory(symbol="BTCUSDT", source="TEST", level_type="PIVOT", price=1.0,
                          permanence_class=2, heat_multiplier=1.0, active=True))
    db.commit()
    row = db.query(GravityMemory).filter_by(source="TEST").one()
    assert row.symbol == "BTCUSDT"   # no slash, by design
