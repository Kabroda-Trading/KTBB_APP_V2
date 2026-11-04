"""Unit + parity coverage for cme_signals.py.

Parity tests feed real, committed CME calibration windows (tests/fixtures/
cme_futures/*.csv, 1-minute bars copied from the Kabroda AI Brain repo's own
calibration_data/cme_futures/) through calculate_session_lock() and confirm
agreement with the real locks_cme_nq.csv/locks_cme_es.csv rows those exact
sessions produced -- the files the verified 10.4-year backtest and the
4,049-trade joint-basket ledger (CC_INTERFACE.md item M3) were built from.
This is the regression guard for three Part-0 findings (2026-11-04 audit,
none previously disclosed, found by reading scripts/
reconstruct_locks_cme_symbol.py directly rather than relaying either of the
Brain repo's two "reference" engines, both of which get this wrong):
  1. The TPO value area is built from 1-MINUTE bars (>=15 required), not six
     5-minute bars.
  2. Row size is 0.05% of the window's low price, not a fixed 0.25 tick.
  3. The value area expands by PAIRS of rows, not one row at a time.
Five real sessions are covered, spanning both DST states and both symbols:
one EDT NQ (reachable), one EDT NQ (unreachable), one EST NQ (reachable),
one EDT ES (reachable), one EST ES (unreachable) -- independently verified
against the real locks_cme_*.csv rows before being committed as fixtures
(2026-11-04), not assumed correct."""
import csv
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import cme_signals as cs

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "cme_futures")


def _load_bars(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    return [
        {"open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]), "close": float(r["close"])}
        for r in rows
    ]


# ------------------------------------------------------------------ D1 parity -- real locked sessions

PARITY_CASES = [
    # (fixture file, symbol, daily_atr14, expected box/bo/bd/reachability/status)
    ("nq_0609_calibration_1m.csv", "MNQ", 51.75, {
        "box": 13.48, "bo": 4514.59, "bd": 4501.11, "reachability": 0.2605, "status": "ARMED",
    }),
    ("nq_0613_calibration_1m.csv", "MNQ", 31.09, {
        "box": 17.71, "reachability": 0.5696, "status": "STAND_DOWN_UNREACHABLE",
    }),
    ("nq_1201_calibration_1m.csv", "MNQ", 43.59, {
        "box": 19.1, "bo": 4805.6, "bd": 4786.5, "reachability": 0.4382, "status": "ARMED",
    }),
    ("es_0613_calibration_1m.csv", "MES", 14.12, {
        "box": 5.2, "bo": 2083.7, "bd": 2078.5, "reachability": 0.3679, "status": "ARMED",
    }),
    ("es_1228_calibration_1m.csv", "MES", 13.64, {
        "box": 10.15, "reachability": 0.7439, "status": "STAND_DOWN_UNREACHABLE",
    }),
]


@pytest.mark.parametrize("fixture_file,symbol,atr14,expected", PARITY_CASES)
def test_calculate_session_lock_matches_real_locked_session(fixture_file, symbol, atr14, expected):
    bars = _load_bars(os.path.join(FIXTURES, fixture_file))
    assert len(bars) == 31   # 9:30-10:00 ET inclusive, 1-minute bars

    plan = cs.calculate_session_lock(symbol, bars, atr14)
    assert plan is not None
    assert plan["status"] == expected["status"]
    assert plan["reachability"] == pytest.approx(expected["reachability"], abs=0.001)
    assert plan["box"] == pytest.approx(expected["box"], abs=0.02)
    if expected["status"] == "ARMED":
        assert plan["bo"] == pytest.approx(expected["bo"], abs=0.02)
        assert plan["bd"] == pytest.approx(expected["bd"], abs=0.02)


def test_mnq_stand_down_session_has_no_trigger_fields():
    # STAND_DOWN_UNREACHABLE plans deliberately omit bo/bd/stop/target --
    # they're meaningless once the session is standing down, and populating
    # them would invite a caller to act on an unreachable day's numbers.
    bars = _load_bars(os.path.join(FIXTURES, "nq_0613_calibration_1m.csv"))
    plan = cs.calculate_session_lock("MNQ", bars, 31.09)
    assert plan["status"] == "STAND_DOWN_UNREACHABLE"
    assert "bo" not in plan and "bd" not in plan


def test_stop_rule_is_symbol_specific_va_boundary_for_nq_box_1x_for_es():
    # Confirmed by direct measurement of the real joint-basket ledger (see
    # module header) -- NQ uses VA-boundary stop (val/vah), ES uses a fixed
    # 1.0x box stop (== bd/bo, since box == bo - bd). Do not let these two
    # collapse to the same rule.
    nq_bars = _load_bars(os.path.join(FIXTURES, "nq_0609_calibration_1m.csv"))
    nq_plan = cs.calculate_session_lock("MNQ", nq_bars, 51.75)
    assert nq_plan["stop_long"] == pytest.approx(nq_plan["val"], abs=1e-6)
    assert nq_plan["stop_short"] == pytest.approx(nq_plan["vah"], abs=1e-6)

    es_bars = _load_bars(os.path.join(FIXTURES, "es_0613_calibration_1m.csv"))
    es_plan = cs.calculate_session_lock("MES", es_bars, 14.12)
    assert es_plan["stop_long"] == pytest.approx(es_plan["bd"], abs=1e-6)
    assert es_plan["stop_short"] == pytest.approx(es_plan["bo"], abs=1e-6)
    assert (es_plan["bo"] - es_plan["stop_long"]) == pytest.approx(es_plan["box"], abs=1e-6)


# ------------------------------------------------------------------ D1 -- insufficient / degenerate windows

def test_fewer_than_minimum_calibration_bars_returns_none():
    bars = [{"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0}] * 10
    assert cs.compute_tpo_value_area(bars) is None
    assert cs.calculate_session_lock("MNQ", bars, 50.0) is None


def test_zero_range_window_returns_none():
    bars = [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 20
    assert cs.compute_tpo_value_area(bars) is None


def test_reachability_ratio_fails_closed_on_missing_atr():
    assert cs.reachability_ratio(10.0, None) > cs.REACHABILITY_MAX
    assert cs.reachability_ratio(10.0, 0.0) > cs.REACHABILITY_MAX


# ------------------------------------------------------------------ D2a/D2b -- confirmed close + bounded retest

def test_check_cross_detects_confirmed_long_close_beyond_bo():
    bars = [{"close": 100.0}, {"close": 110.5}]   # bo = 110
    result = cs.check_cross(bars, bo=110.0, bd=90.0)
    assert result["side"] == "LONG"
    assert result["trigger"] == 110.0
    assert result["cross_index"] == 1


def test_check_cross_detects_confirmed_short_close_beyond_bd():
    bars = [{"close": 95.0}, {"close": 89.5}]   # bd = 90
    result = cs.check_cross(bars, bo=110.0, bd=90.0)
    assert result["side"] == "SHORT"


def test_check_cross_returns_none_if_no_close_beyond_either_trigger_within_window():
    bars = [{"close": 100.0}] * (cs.CROSS_WINDOW_BARS + 5)
    assert cs.check_cross(bars, bo=110.0, bd=90.0) is None


def test_check_cross_only_scans_up_to_the_window_bound():
    # A close beyond bo sitting past the 12-bar window must NOT count.
    bars = [{"close": 100.0}] * cs.CROSS_WINDOW_BARS + [{"close": 999.0}]
    assert cs.check_cross(bars, bo=110.0, bd=90.0) is None


def test_check_retest_fill_long_fills_on_wick_touch_back_to_trigger():
    bars = [{"low": 112.0, "high": 115.0}, {"low": 110.0, "high": 113.0}]
    result = cs.check_retest_fill(bars, side="LONG", trigger=110.0)
    assert result["fill_price"] == 110.0
    assert result["fill_index"] == 1


def test_check_retest_fill_returns_none_if_price_never_comes_back():
    bars = [{"low": 115.0, "high": 120.0}] * (cs.RETEST_WINDOW_BARS + 2)
    assert cs.check_retest_fill(bars, side="LONG", trigger=110.0) is None


# ------------------------------------------------------------------ D3 -- breakeven ratchet + exit

def test_check_breakeven_ratchet_long_and_short():
    assert cs.check_breakeven_ratchet(108.0, "LONG", be_trigger=108.0) is True
    assert cs.check_breakeven_ratchet(107.9, "LONG", be_trigger=108.0) is False
    assert cs.check_breakeven_ratchet(92.0, "SHORT", be_trigger=92.0) is True


def test_check_exit_stop_hit_before_ratchet():
    bars = [{"low": 89.0, "high": 100.0}]
    result = cs.check_exit(bars, "LONG", stop_price=90.0, target_price=130.0, be_trigger=108.0, be_stop_price=100.9)
    assert result == {"exit_reason": "CLOSED_STOP", "exit_price": 90.0, "exit_bar": bars[0]}


def test_check_exit_ratchet_then_be_stop_takes_effect_next_bar_not_same_bar():
    # Bar 0 touches the ratchet trigger (108) AND dips to the original
    # stop's territory is irrelevant here -- the key is bar 0's own low
    # must NOT be checked against the just-moved be_stop. Bar 1 then dips
    # to exactly the be_stop price and should close there. This is the
    # direct regression test for M3's own root-cause fix (the same-bar
    # look-ahead bug that falsely stopped out 218/218 real winners).
    bars = [
        {"low": 100.9, "high": 108.5},   # touches ratchet trigger (108); low is AT be_stop but must not exit yet
        {"low": 100.9, "high": 102.0},   # NOW the be_stop is live -> exits here
    ]
    result = cs.check_exit(bars, "LONG", stop_price=90.0, target_price=130.0, be_trigger=108.0, be_stop_price=100.9)
    assert result["exit_reason"] == "CLOSED_BE_STOP"
    assert result["exit_price"] == 100.9
    assert result["exit_bar"] is bars[1]


def test_check_exit_target_hit():
    bars = [{"low": 109.0, "high": 131.0}]
    result = cs.check_exit(bars, "LONG", stop_price=90.0, target_price=130.0)
    assert result["exit_reason"] == "CLOSED_TARGET"
    assert result["exit_price"] == 130.0


def test_check_exit_returns_none_while_still_open():
    bars = [{"low": 105.0, "high": 107.0}]
    assert cs.check_exit(bars, "LONG", stop_price=90.0, target_price=130.0) is None


def test_check_exit_short_side_mirrors_long():
    bars = [{"low": 69.0, "high": 91.0}]
    result = cs.check_exit(bars, "SHORT", stop_price=90.0, target_price=70.0)
    assert result["exit_reason"] == "CLOSED_STOP"
    assert result["exit_price"] == 90.0


# ------------------------------------------------------------------ dynamic contract sizing (CANON §2:48)

def test_dynamic_contract_count_mnq_and_mes_multipliers():
    # $350 / (box * $2/pt) for MNQ, $350 / (box * $5/pt) for MES.
    assert cs.dynamic_contract_count("MNQ", box_points=20.0, daily_risk_budget_usd=350.0) == 3  # floor(350/40)=8, capped at 3
    assert cs.dynamic_contract_count("MNQ", box_points=175.0, daily_risk_budget_usd=350.0) == 1  # floor(350/350)=1
    assert cs.dynamic_contract_count("MES", box_points=20.0, daily_risk_budget_usd=350.0) == 3   # floor(350/100)=3


def test_dynamic_contract_count_never_returns_zero():
    assert cs.dynamic_contract_count("MNQ", box_points=1000.0, daily_risk_budget_usd=350.0) == 1


def test_dynamic_contract_count_respects_a_lower_cap():
    assert cs.dynamic_contract_count("MNQ", box_points=20.0, daily_risk_budget_usd=350.0, contracts_cap=1) == 1
