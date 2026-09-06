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


# ------------------------------------------------------------------ check_leverage_is_safe
# (replaces the old suggest_leverage() -- Bitunix's real place_order API
# has no leverage parameter, so "suggesting" one to use was never
# actionable. This checks whatever REAL leverage the caller already
# queried from the exchange.)

def test_check_leverage_is_safe_long_passes():
    # entry 100, stop 95 (distance 5) at 10x: liq=90, distance 10 > 5 -- safe
    ok, detail, liq = es.check_leverage_is_safe(100.0, 95.0, "LONG", 10)
    assert ok is True
    assert liq == pytest.approx(90.0)
    assert "stop fires first" in detail


def test_check_leverage_is_safe_refuses_high_leverage_like_andys_real_account():
    # The real scenario this replaces: Andy's actual account was set to
    # 40x while the design assumed 10x. At 40x with a tight-ish stop
    # (entry 100, stop 99, distance 1): liq = 100*(1-1/40) = 97.5,
    # distance 2.5 > 1 -- still safe here (40x isn't unsafe for EVERY
    # stop, only tight ones -- see the next test for where it fails).
    ok, detail, liq = es.check_leverage_is_safe(100.0, 99.0, "LONG", 40)
    assert ok is True
    assert liq == pytest.approx(97.5)


def test_check_leverage_is_safe_refuses_when_real_leverage_is_too_high_for_the_stop():
    # entry 100, stop 97.5 (distance 2.5) at 40x: liq=97.5, distance 2.5 --
    # NOT strictly greater than the stop distance -- unsafe (liquidation
    # at or inside the stop, not comfortably beyond it).
    ok, detail, liq = es.check_leverage_is_safe(100.0, 97.5, "LONG", 40)
    assert ok is False
    assert liq == pytest.approx(97.5)
    assert "refuse this trade" in detail


def test_check_leverage_is_safe_short():
    ok, detail, liq = es.check_leverage_is_safe(100.0, 105.0, "SHORT", 10)
    assert ok is True
    assert liq == pytest.approx(110.0)


# ------------------------------------------------------------------ maintenance margin rate (Stage 2, 2026-09-05)
# Folds a REAL, live-queried maintenance margin rate into the
# liquidation estimate (get_position_tiers) instead of the old naive
# "100%-of-margin-lost" bound. Default 0.0 must reproduce every existing
# number above exactly -- these tests confirm the new param is additive,
# not a behavior change for existing callers.

def test_estimate_liquidation_default_mmr_matches_naive_formula_exactly():
    assert es.estimate_liquidation_price(100.0, 10, "LONG") == pytest.approx(90.0)
    assert es.estimate_liquidation_price(100.0, 10, "SHORT") == pytest.approx(110.0)


def test_estimate_liquidation_with_real_mmr_moves_liquidation_closer_to_entry():
    # Bitunix docs' own BTCUSDT tier-1 example: leverage=125, mmr=0.004.
    # liq = 100*(1 - 1/125 + 0.004) = 100*0.996 = 99.6 -- closer to entry
    # than the naive 100*(1-1/125)=99.2 bound would suggest.
    liq = es.estimate_liquidation_price(100.0, 125, "LONG", maintenance_margin_rate=0.004)
    assert liq == pytest.approx(99.6)
    naive = es.estimate_liquidation_price(100.0, 125, "LONG")
    assert liq > naive  # mmr moves LONG liquidation UP, closer to entry


def test_estimate_liquidation_clamps_when_mmr_exceeds_inverse_leverage():
    # leverage=2 (1/leverage=0.5), mmr=0.6 > 0.5 -- adverse_move_pct
    # clamps to 0.0, liq pins to entry_price exactly (fails safe).
    liq_long = es.estimate_liquidation_price(100.0, 2, "LONG", maintenance_margin_rate=0.6)
    assert liq_long == pytest.approx(100.0)
    liq_short = es.estimate_liquidation_price(100.0, 2, "SHORT", maintenance_margin_rate=0.6)
    assert liq_short == pytest.approx(100.0)


def test_check_leverage_is_safe_with_real_mmr_can_flip_a_previously_safe_trade_to_unsafe():
    # entry 100, stop 97.6 (distance 2.4) at 40x: naive liq (mmr=0.0) =
    # 100*(1-1/40) = 97.5, distance 2.5 > 2.4 -- SAFE under the old naive
    # formula. With a real mmr of 0.004: liq = 100*(1-1/40+0.004) = 97.9,
    # distance 2.1 < 2.4 -- UNSAFE. The real mmr is what should govern.
    ok_naive, _, liq_naive = es.check_leverage_is_safe(100.0, 97.6, "LONG", 40)
    assert ok_naive is True
    assert liq_naive == pytest.approx(97.5)

    ok_real, detail_real, liq_real = es.check_leverage_is_safe(100.0, 97.6, "LONG", 40, maintenance_margin_rate=0.004)
    assert ok_real is False
    assert liq_real == pytest.approx(97.9)
    assert "refuse this trade" in detail_real


# ------------------------------------------------------------------ precision formatting (Stage 2, 2026-09-05)
# Bitunix's real order params (qty/price) are string-typed on the wire,
# bounded by basePrecision/quotePrecision -- these are pure, hand-
# computed-reference tests, no DB/network, matching this module's style.

def test_round_qty_to_precision_floors_not_rounds():
    assert es.round_qty_to_precision(0.09876, 3) == "0.098"   # would be "0.099" if rounded


def test_round_qty_to_precision_no_scientific_notation_and_no_trailing_float_artifacts():
    assert es.round_qty_to_precision(0.0001, 4) == "0.0001"
    assert es.round_qty_to_precision(0.1, 2) == "0.10"


def test_round_qty_to_precision_zero_precision_returns_integer_string():
    assert es.round_qty_to_precision(5.9, 0) == "5"


def test_round_qty_to_precision_rejects_negative_precision():
    with pytest.raises(ValueError, match="precision"):
        es.round_qty_to_precision(1.0, -1)


def test_round_price_to_precision_rounds_half_up():
    # Contrast with round_qty_to_precision's floor behavior -- a price
    # has no "never exceed a floor" constraint.
    assert es.round_price_to_precision(101.5, 0) == "102"
    assert es.round_price_to_precision(101.4, 0) == "101"


def test_round_price_to_precision_rejects_negative_precision():
    with pytest.raises(ValueError, match="precision"):
        es.round_price_to_precision(1.0, -1)


# ------------------------------------------------------------------ compute_stake (Sizing Policy Wizard, 2026-09-05)
# Every preset is just which optional params are set -- deliberately
# one primitive, no separate code path per preset. Numbers below are
# Andy's own worked examples, not invented.

def test_compute_stake_fixed_mode_uses_risk_last_usd():
    stake, detail = es.compute_stake(risk_last_usd=100.0)
    assert stake == pytest.approx(100.0)
    assert detail["base"] == pytest.approx(100.0)
    assert detail["tier_applied"] is False
    assert detail["cap_binding"] == "none"


def test_compute_stake_percent_of_balance_base():
    # Andy's own example: $1,000 account -> $100 stake; $2,000 -> $200.
    stake1, _ = es.compute_stake(risk_last_usd=100.0, base_risk_pct=0.10, account_balance_usd=1000.0)
    assert stake1 == pytest.approx(100.0)
    stake2, _ = es.compute_stake(risk_last_usd=100.0, base_risk_pct=0.10, account_balance_usd=2000.0)
    assert stake2 == pytest.approx(200.0)


def test_compute_stake_raises_when_pct_base_missing_balance():
    with pytest.raises(ValueError, match="account_balance_usd"):
        es.compute_stake(risk_last_usd=100.0, base_risk_pct=0.10)


def test_compute_stake_tier_switch_applies_above_threshold_and_reverts_below_it():
    # Andy's own example: 10% of account, capped at $1,000 flat once the
    # account reaches $10,000 -- a $12,000 account does NOT get $1,200,
    # it gets $1,000. A $9,000 account (below threshold) reverts to 10%.
    above, detail_above = es.compute_stake(
        risk_last_usd=100.0, base_risk_pct=0.10, account_balance_usd=12000.0,
        tier_threshold_usd=10000.0, tier_flat_usd=1000.0)
    assert above == pytest.approx(1000.0)
    assert detail_above["tier_applied"] is True

    below, detail_below = es.compute_stake(
        risk_last_usd=100.0, base_risk_pct=0.10, account_balance_usd=9000.0,
        tier_threshold_usd=10000.0, tier_flat_usd=1000.0)
    assert below == pytest.approx(900.0)
    assert detail_below["tier_applied"] is False


def test_compute_stake_tier_switch_requires_balance():
    with pytest.raises(ValueError, match="account_balance_usd"):
        es.compute_stake(risk_last_usd=100.0, tier_threshold_usd=10000.0, tier_flat_usd=1000.0)


def test_compute_stake_abs_and_pct_caps_take_the_minimum():
    # $500 stake, but capped at the LOWER of $200 flat or 5% of a
    # $10,000 account ($500) -- the abs cap is tighter here.
    stake, detail = es.compute_stake(risk_last_usd=500.0, cap_abs_usd=200.0, cap_pct=0.05, account_balance_usd=10000.0)
    assert stake == pytest.approx(200.0)
    assert detail["cap_binding"] == "abs"

    # Now the pct cap is tighter (2% of $10,000 = $200 vs abs $400).
    stake2, detail2 = es.compute_stake(risk_last_usd=500.0, cap_abs_usd=400.0, cap_pct=0.02, account_balance_usd=10000.0)
    assert stake2 == pytest.approx(200.0)
    assert detail2["cap_binding"] == "pct"


def test_compute_stake_no_cap_binding_when_stake_already_under_both():
    stake, detail = es.compute_stake(risk_last_usd=50.0, cap_abs_usd=1000.0, cap_pct=0.10, account_balance_usd=10000.0)
    assert stake == pytest.approx(50.0)
    assert detail["cap_binding"] == "none"


def test_compute_stake_derisk_shrinks_after_n_losses():
    # Below the threshold: no shrink.
    stake_before, detail_before = es.compute_stake(risk_last_usd=500.0, derisk_n=3, derisk_factor=0.5, consecutive_losses=2)
    assert stake_before == pytest.approx(500.0)
    assert detail_before["derisk_applied"] is False

    # At the threshold: single-step shrink, not compounding.
    stake_at, detail_at = es.compute_stake(risk_last_usd=500.0, derisk_n=3, derisk_factor=0.5, consecutive_losses=3)
    assert stake_at == pytest.approx(250.0)
    assert detail_at["derisk_applied"] is True

    # Beyond the threshold: still a single 0.5x, not 0.5^2.
    stake_beyond, _ = es.compute_stake(risk_last_usd=500.0, derisk_n=3, derisk_factor=0.5, consecutive_losses=5)
    assert stake_beyond == pytest.approx(250.0)


def test_compute_stake_derisk_and_cap_compose():
    # Derisk shrinks the stake, THEN the cap still applies to the result.
    stake, detail = es.compute_stake(
        risk_last_usd=500.0, derisk_n=1, derisk_factor=0.5, consecutive_losses=1, cap_abs_usd=100.0)
    assert stake == pytest.approx(100.0)
    assert detail["derisk_applied"] is True
    assert detail["cap_binding"] == "abs"
