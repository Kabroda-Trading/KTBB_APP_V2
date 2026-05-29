# test_fixes.py
# ==============================================================================
# Integration tests for Fix 1, Fix 2, and Fix 3 context string display.
# Run with: python test_fixes.py
# Uses the real kabroda.db SQLite — test rows are inserted and cleaned up.
# ==============================================================================

import datetime
from datetime import timedelta

from database import init_db, SessionLocal, DecisionJournal, CampaignLog

init_db()

_PASS = 0
_FAIL = 0


def _ok(msg: str) -> None:
    global _PASS
    _PASS += 1
    print(f"PASS: {msg}")


def _fail(msg: str, err: Exception) -> None:
    global _FAIL
    _FAIL += 1
    print(f"FAIL: {msg} — {err}")


# ==============================================================================
# FIX 1 — outcome_direction_correct and outcome_price_4h written after 4h
# ==============================================================================

def test_fix1_outcome_fields_long():
    from main import _do_outcome_tick

    db = SessionLocal()
    old_ts = datetime.datetime.utcnow() - timedelta(hours=5)
    row = DecisionJournal(
        symbol="BTC/USDT",
        timestamp=old_ts,
        decision_type="MAS_APPROVED",
        confluence_score=2,
        confluence_direction="LONG",
        energy_status="STRONG",
        bo_price=100000.0,
        bd_price=99000.0,
        asset_price=100000.0,
        session_date="2026-05-29",
        decision_reason="test_fix1_long",
    )
    db.add(row)
    db.commit()
    row_id = row.id
    db.close()

    try:
        # LONG at $100,000, current price $101,000 — positive move, direction correct
        _do_outcome_tick(101000.0)

        db = SessionLocal()
        updated = db.query(DecisionJournal).filter(DecisionJournal.id == row_id).first()
        assert updated.outcome_direction_correct is True, \
            f"Expected True, got {updated.outcome_direction_correct}"
        assert updated.outcome_price_4h == 101000.0, \
            f"Expected 101000.0, got {updated.outcome_price_4h}"
        assert updated.outcome_pct_move_4h > 0, \
            f"Expected positive pct_move, got {updated.outcome_pct_move_4h}"
        db.close()
        _ok("Fix 1 — LONG outcome_direction_correct=True, price and pct_move written")
    except AssertionError as e:
        _fail("Fix 1 — LONG outcome fields", e)
    finally:
        db = SessionLocal()
        db.query(DecisionJournal).filter(DecisionJournal.id == row_id).delete()
        db.commit()
        db.close()


def test_fix1_outcome_fields_short_wrong():
    from main import _do_outcome_tick

    db = SessionLocal()
    old_ts = datetime.datetime.utcnow() - timedelta(hours=5)
    row = DecisionJournal(
        symbol="BTC/USDT",
        timestamp=old_ts,
        decision_type="MAS_APPROVED",
        confluence_score=2,
        confluence_direction="SHORT",
        energy_status="OVEREXTENDED",
        bo_price=100000.0,
        bd_price=99000.0,
        asset_price=99000.0,
        session_date="2026-05-29",
        decision_reason="test_fix1_short",
    )
    db.add(row)
    db.commit()
    row_id = row.id
    db.close()

    try:
        # SHORT at $99,000, current price $100,000 — price went UP, direction WRONG
        _do_outcome_tick(100000.0)

        db = SessionLocal()
        updated = db.query(DecisionJournal).filter(DecisionJournal.id == row_id).first()
        assert updated.outcome_direction_correct is False, \
            f"Expected False, got {updated.outcome_direction_correct}"
        assert updated.outcome_pct_move_4h > 0, \
            f"Expected positive move (wrong direction for SHORT), got {updated.outcome_pct_move_4h}"
        db.close()
        _ok("Fix 1 — SHORT outcome_direction_correct=False when price moved against bias")
    except AssertionError as e:
        _fail("Fix 1 — SHORT wrong direction", e)
    finally:
        db = SessionLocal()
        db.query(DecisionJournal).filter(DecisionJournal.id == row_id).delete()
        db.commit()
        db.close()


def test_fix1_target_hit():
    from main import _do_outcome_tick

    db = SessionLocal()
    win_log = CampaignLog(
        symbol="BTC/USDT",
        date_key="2026-05-29",
        session_id="test_session_win",
        bias="LONG",
        grade="MAS_AUTO",
        entry_price=100000.0,
        stop_loss=99000.0,
        t1=101000.0,
        t2=101618.0,
        t3=102618.0,
        total_contracts=0.0,
        status="CLOSED_WIN",
    )
    loss_log = CampaignLog(
        symbol="BTC/USDT",
        date_key="2026-05-29",
        session_id="test_session_loss",
        bias="LONG",
        grade="MAS_AUTO",
        entry_price=100000.0,
        stop_loss=99000.0,
        t1=101000.0,
        t2=101618.0,
        t3=102618.0,
        total_contracts=0.0,
        status="CLOSED_LOSS",
    )
    db.add(win_log)
    db.add(loss_log)
    db.commit()
    win_id = win_log.id
    loss_id = loss_log.id
    db.close()

    try:
        _do_outcome_tick(101000.0)

        db = SessionLocal()
        w = db.query(CampaignLog).filter(CampaignLog.id == win_id).first()
        l = db.query(CampaignLog).filter(CampaignLog.id == loss_id).first()
        assert w.target_hit == "T1", f"Expected T1, got {w.target_hit}"
        assert l.target_hit == "STOP", f"Expected STOP, got {l.target_hit}"
        db.close()
        _ok("Fix 1 — target_hit=T1 for CLOSED_WIN, target_hit=STOP for CLOSED_LOSS")
    except AssertionError as e:
        _fail("Fix 1 — target_hit", e)
    finally:
        db = SessionLocal()
        db.query(CampaignLog).filter(CampaignLog.id.in_([win_id, loss_id])).delete()
        db.commit()
        db.close()


def test_fix1_skips_recent_rows():
    """Rows younger than 4h must not be filled."""
    from main import _do_outcome_tick

    db = SessionLocal()
    recent_ts = datetime.datetime.utcnow() - timedelta(hours=1)
    row = DecisionJournal(
        symbol="BTC/USDT",
        timestamp=recent_ts,
        decision_type="MAS_APPROVED",
        confluence_score=1,
        confluence_direction="LONG",
        energy_status="STRONG",
        bo_price=100000.0,
        bd_price=99000.0,
        asset_price=100000.0,
        session_date="2026-05-29",
        decision_reason="test_fix1_recent",
    )
    db.add(row)
    db.commit()
    row_id = row.id
    db.close()

    try:
        _do_outcome_tick(101000.0)

        db = SessionLocal()
        updated = db.query(DecisionJournal).filter(DecisionJournal.id == row_id).first()
        assert updated.outcome_direction_correct is None, \
            f"Expected None (too recent), got {updated.outcome_direction_correct}"
        db.close()
        _ok("Fix 1 — rows < 4h old are NOT filled")
    except AssertionError as e:
        _fail("Fix 1 — recent row should be skipped", e)
    finally:
        db = SessionLocal()
        db.query(DecisionJournal).filter(DecisionJournal.id == row_id).delete()
        db.commit()
        db.close()


# ==============================================================================
# FIX 2 — kinematic_grade, real energy_status, real confluence_score stored
# ==============================================================================

def test_fix2_kinematic_grade():
    from kabroda_mas_flow import _inject_decision_journal, ExecutiveBrief

    brief = ExecutiveBrief(
        approval_status="APPROVED",
        tactical_brief="test tactical brief",
        bias="LONG",
        entry_price=100000.0,
        stop_loss=99000.0,
        t1=101000.0,
        t2=101618.0,
        t3=102618.0,
        formatted_newsletter_md="# Test Brief",
    )

    battlebox_payload = {
        "levels": {
            "breakout_trigger": 100000.0,
            "breakdown_trigger": 99000.0,
        },
        "context": {
            "1h_fuel_status": "STRONG",
            "fuel_gauge": {
                "1H": {"trend": "BULLISH", "momentum": "POSITIVE", "rsi": 61.0},
                "4H": {"trend": "BULLISH", "momentum": "POSITIVE", "rsi": 58.0},
                "15M_JEWEL": {
                    "kinematic_grade": "PRIMED",
                    "rsi": 62.0,
                    "ribbon_spread_pct": 0.42,
                    "deviation_from_mean_pct": 0.8,
                },
            },
        },
    }

    _inject_decision_journal("BTC/USDT", "2026-05-29", brief, battlebox_payload)

    db = SessionLocal()
    try:
        row = (
            db.query(DecisionJournal)
            .filter(
                DecisionJournal.symbol == "BTC/USDT",
                DecisionJournal.session_date == "2026-05-29",
                DecisionJournal.decision_reason == "test tactical brief",
            )
            .order_by(DecisionJournal.id.desc())
            .first()
        )
        assert row is not None, "No row found — _inject_decision_journal may have failed"
        assert row.kinematic_grade == "PRIMED", \
            f"Expected PRIMED, got {row.kinematic_grade}"
        assert row.energy_status == "STRONG", \
            f"Expected STRONG, got {row.energy_status}"
        assert row.confluence_score == 3, \
            f"Expected 3 (1H BULLISH + 4H BULLISH + 15M PRIMED), got {row.confluence_score}"
        _ok("Fix 2 — kinematic_grade=PRIMED, energy_status=STRONG, confluence_score=3")
        db.delete(row)
        db.commit()
    except AssertionError as e:
        _fail("Fix 2 — kinematic_grade / energy_status / confluence_score", e)
    finally:
        db.close()


def test_fix2_short_alignment():
    """Verify confluence_score counts correctly for SHORT bias."""
    from kabroda_mas_flow import _inject_decision_journal, ExecutiveBrief

    brief = ExecutiveBrief(
        approval_status="REJECTED",
        tactical_brief="test short brief",
        bias="SHORT",
        entry_price=99000.0,
        stop_loss=100000.0,
        t1=98000.0,
        t2=97382.0,
        t3=96382.0,
        formatted_newsletter_md="# Test Short",
    )

    battlebox_payload = {
        "levels": {
            "breakout_trigger": 100000.0,
            "breakdown_trigger": 99000.0,
        },
        "context": {
            "1h_fuel_status": "CHOP_RISK",
            "fuel_gauge": {
                "1H": {"trend": "BEARISH", "momentum": "NEGATIVE", "rsi": 38.0},
                "4H": {"trend": "BULLISH", "momentum": "POSITIVE", "rsi": 55.0},
                "15M_JEWEL": {
                    "kinematic_grade": "TANGLED",
                    "rsi": 45.0,
                    "ribbon_spread_pct": 0.08,
                    "deviation_from_mean_pct": 0.2,
                },
            },
        },
    }

    _inject_decision_journal("BTC/USDT", "2026-05-29", brief, battlebox_payload)

    db = SessionLocal()
    try:
        row = (
            db.query(DecisionJournal)
            .filter(
                DecisionJournal.symbol == "BTC/USDT",
                DecisionJournal.decision_reason == "test short brief",
            )
            .order_by(DecisionJournal.id.desc())
            .first()
        )
        assert row is not None, "No row found"
        # 1H BEARISH = +1, 4H BULLISH = 0, 15M TANGLED = 0 → score = 1
        assert row.confluence_score == 1, \
            f"Expected 1 (only 1H aligned for SHORT), got {row.confluence_score}"
        assert row.kinematic_grade == "TANGLED", \
            f"Expected TANGLED, got {row.kinematic_grade}"
        assert row.energy_status == "CHOP_RISK", \
            f"Expected CHOP_RISK, got {row.energy_status}"
        _ok("Fix 2 — SHORT confluence_score=1 (only 1H aligned), kinematic_grade=TANGLED")
        db.delete(row)
        db.commit()
    except AssertionError as e:
        _fail("Fix 2 — SHORT alignment count", e)
    finally:
        db.close()


# ==============================================================================
# FIX 3 — Show updated context string (visual verification)
# ==============================================================================

def show_fix3_context():
    from kabroda_mas_flow import _build_senior_analyst_context, _compute_targets

    levels = {
        "breakout_trigger": 107450.0,
        "breakdown_trigger": 106100.0,
        "daily_resistance": 108200.0,
        "daily_support": 105500.0,
        "range30m_high": 107300.0,
        "range30m_low": 106200.0,
        "f24_poc": 106700.0,
        "f24_vah": 107380.0,
        "f24_val": 106050.0,
    }
    context = {
        "macro_bias": "BULLISH",
        "micro_bias": "BULLISH",
        "micro_state": "SWEET_ZONE",
        "1h_fuel_status": "STRONG",
        "fuel_gauge": {
            "1H": {
                "trend": "BULLISH",
                "momentum": "POSITIVE",
                "rsi": 62.4,
                "jewel": {
                    "rsi_zone": "OVERBOUGHT_VALUE",
                    "signal": "TRENDING_STRONG",
                    "adx": 28.5,
                    "adx_rising": True,
                    "stoch_zone": "NEUTRAL",
                },
            },
            "4H": {
                "trend": "BULLISH",
                "momentum": "POSITIVE",
                "rsi": 58.1,
                "jewel": {
                    "rsi_zone": "VALUE_ZONE",
                    "signal": "BOUNCE_PRIMED",
                    "adx": 22.1,
                    "adx_rising": True,
                    "stoch_zone": "NEUTRAL",
                },
            },
            "15M_JEWEL": {
                "kinematic_grade": "PRIMED",
                "rsi": 61.0,
                "ribbon_spread_pct": 0.42,
                "deviation_from_mean_pct": 0.9,
            },
        },
        "kde_peaks": [
            {"price": 107900.0, "heat_score": 8.2, "intensity": "HEAVY"},
            {"price": 106050.0, "heat_score": 12.5, "intensity": "MAXIMUM"},
        ],
        "macro_structure": [
            {"type": "BEAR_WAVE_4_BOUNCE", "price": 80632.0},
            {"type": "BEAR_WAVE_3_LOW", "price": 60055.0},
        ],
        "macro_environment": {"SPX": "RISK-ON", "DXY": "BEARISH", "VIX": "18.4"},
    }
    targets = _compute_targets(107450.0, 106100.0)

    ctx = _build_senior_analyst_context(
        symbol="BTC/USDT",
        date_key="2026-05-29",
        session_id="us_ny_futures",
        levels=levels,
        context=context,
        targets=targets,
        cro_memory="MEMORY BANK: 3 Wins, 1 Loss. Net PnL: +2.00. Recent performance positive.",
        narrative_ctx="NARRATIVE CONTEXT (Yesterday): Bear Wave 4 bounce in progress.",
        jewel_ctx="OVERNIGHT JEWEL SNAPSHOTS: NY_OPEN gate OPEN, BULLISH, STRONG conviction.",
    )
    print("\n" + "=" * 70)
    print("FIX 3 — UPDATED SENIOR ANALYST CONTEXT STRING")
    print("=" * 70)
    print(ctx)
    print("=" * 70)


# ==============================================================================
# RUNNER
# ==============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("KABRODA FIX TESTS")
    print("=" * 70)

    print("\n--- Fix 1: Outcome Tracker ---")
    test_fix1_outcome_fields_long()
    test_fix1_outcome_fields_short_wrong()
    test_fix1_target_hit()
    test_fix1_skips_recent_rows()

    print("\n--- Fix 2: kinematic_grade in DecisionJournal ---")
    test_fix2_kinematic_grade()
    test_fix2_short_alignment()

    print("\n--- Fix 3: Senior Analyst Context String ---")
    show_fix3_context()

    print("\n" + "=" * 70)
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    print("=" * 70)
