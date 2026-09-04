"""
Unit coverage for executor_sizing.py -- pure-function tests with hand-
computed expected outputs, matching this codebase's established style
(reachability.py, stop_planner.py, fuel_gate.py). No mocks of the
function under test -- real math, real numbers.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import executor_sizing as es


# ------------------------------------------------------------------ compute_qty

def test_compute_qty_matches_hand_calc():
    # Real 9/3 plan numbers from the design conversation (Kabroda AI Brain
    # repo AGENT_LOG.md): entry 78795.7, stop 77768.3, $100 risk -> ~0.0973 BTC.
    qty = es.compute_qty(risk_dollars=100.0, entry_price=78795.7, stop_price=77768.3)
    assert qty == pytest.approx(0.09734, abs=0.0001)


def test_compute_qty_zero_stop_distance_raises():
    with pytest.raises(ValueError, match="stop_distance"):
        es.compute_qty(100.0, 100.0, 100.0)


def test_compute_qty_negative_risk_raises():
    with pytest.raises(ValueError, match="risk_dollars"):
        es.compute_qty(-50.0, 100.0, 95.0)


# ------------------------------------------------------------------ compute_next_risk

def test_compute_next_risk_normal_case():
    # 100 + 0.10*500 = 150, within [100, 1000]
    assert es.compute_next_risk(risk_last=100.0, last_trade_pnl=500.0) == pytest.approx(150.0)


def test_compute_next_risk_clamps_to_cap():
    # 1000 + 0.10*2000 = 1200 -> clamped to 1000
    assert es.compute_next_risk(risk_last=1000.0, last_trade_pnl=2000.0) == 1000.0


def test_compute_next_risk_clamps_to_floor():
    # 100 + 0.10*(-2000) = -100 -> clamped to 100
    assert es.compute_next_risk(risk_last=100.0, last_trade_pnl=-2000.0) == 100.0


def test_compute_next_risk_custom_floor_cap_factor():
    assert es.compute_next_risk(200.0, 100.0, floor=50.0, cap=500.0, factor=0.20) == pytest.approx(220.0)


# ------------------------------------------------------------------ estimate_liquidation_price

def test_estimate_liquidation_long():
    assert es.estimate_liquidation_price(100.0, 10, "LONG") == pytest.approx(90.0)


def test_estimate_liquidation_short():
    assert es.estimate_liquidation_price(100.0, 10, "SHORT") == pytest.approx(110.0)


def test_estimate_liquidation_invalid_direction_raises():
    with pytest.raises(ValueError, match="direction"):
        es.estimate_liquidation_price(100.0, 10, "SIDEWAYS")


def test_estimate_liquidation_bad_inputs_raise():
    with pytest.raises(ValueError):
        es.estimate_liquidation_price(0.0, 10, "LONG")
    with pytest.raises(ValueError):
        es.estimate_liquidation_price(100.0, 0, "LONG")


# ------------------------------------------------------------------ check_liquidation_safety

def test_liquidation_safety_passes_long():
    # entry 100, stop 95 (distance 5), liq 90 (distance 10) -- liq well beyond stop
    ok, detail = es.check_liquidation_safety(100.0, 95.0, 90.0, "LONG")
    assert ok is True
    assert "stop fires first" in detail


def test_liquidation_safety_fails_long_liq_inside_stop():
    # entry 100, stop 95 (distance 5), liq 97 (distance 3) -- liquidation BEFORE the stop
    ok, detail = es.check_liquidation_safety(100.0, 95.0, 97.0, "LONG")
    assert ok is False
    assert "refuse this trade" in detail


def test_liquidation_safety_passes_short():
    ok, _ = es.check_liquidation_safety(100.0, 105.0, 110.0, "SHORT")
    assert ok is True


def test_liquidation_safety_fails_short_liq_inside_stop():
    ok, _ = es.check_liquidation_safety(100.0, 105.0, 103.0, "SHORT")
    assert ok is False


def test_liquidation_safety_stop_on_wrong_side():
    # LONG with a stop ABOVE entry -- malformed input, must fail, not crash
    ok, detail = es.check_liquidation_safety(100.0, 105.0, 90.0, "LONG")
    assert ok is False
    assert "wrong side" in detail


# ------------------------------------------------------------------ suggest_leverage

def test_suggest_leverage_baseline_sufficient():
    # notional = 100*1 = 100; margin at 10x = 10; balance 1000, threshold 800 -- comfortably fine
    lev, detail = es.suggest_leverage(
        entry_price=100.0, stop_price=95.0, direction="LONG", qty=1.0,
        leverage_baseline=10, free_balance_usd=1000.0,
    )
    assert lev == 10
    assert "baseline leverage OK" in detail


def test_suggest_leverage_raises_when_margin_pressured_and_still_safe():
    # notional = 100*10 = 1000; balance 100, threshold 80. margin_at(10)=100>80.
    # Hand-verified: margin drops <=80 first at 13x (1000/13=76.9); stop_distance=5
    # (entry 100, stop 95) stays liq-safe at 13x (liq=100*(1-1/13)=92.3, distance 7.7>5).
    lev, detail = es.suggest_leverage(
        entry_price=100.0, stop_price=95.0, direction="LONG", qty=10.0,
        leverage_baseline=10, free_balance_usd=100.0, max_margin_pct=0.80,
    )
    assert lev == 13
    assert "10x -> 13x" in detail


def test_suggest_leverage_refuses_to_raise_past_liquidation_safety():
    # notional = 100*20 = 2000; balance 100, threshold 80 -- needs high leverage
    # to satisfy margin, but stop_distance=6 (entry 100, stop 94) goes liq-unsafe
    # starting at 17x (100/17=5.88<6) -- must refuse rather than pick an unsafe lev.
    lev, detail = es.suggest_leverage(
        entry_price=100.0, stop_price=94.0, direction="LONG", qty=20.0,
        leverage_baseline=10, free_balance_usd=100.0, max_margin_pct=0.80, max_leverage=20,
    )
    assert lev == 10  # falls back to baseline, does NOT pick the unsafe higher leverage
    assert "reduce risk_dollars" in detail


def test_suggest_leverage_no_balance_figure_uses_baseline_unchecked():
    lev, detail = es.suggest_leverage(
        entry_price=100.0, stop_price=95.0, direction="LONG", qty=10.0,
        leverage_baseline=10, free_balance_usd=None,
    )
    assert lev == 10
    assert "no balance figure" in detail
