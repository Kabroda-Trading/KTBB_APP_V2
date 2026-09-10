"""
Regression coverage for decision_engine.py.

Originally scoped narrowly to the SS9a diagnostic fields (fuel_verdict,
fuel_push_ratio, trend_1h, trend_4h, htf_aligned, htf_opposed) added
2026-08-31 -- purely additive, no gate formula/threshold/verdict logic
touched. Extended 2026-09-06 with real coverage of the tier/verdict logic
itself, per the Three-Outcome Gate Rebuild (GATE_REBUILD_SPEC.md, Kabroda AI
Brain repo): the divergence veto is removed, ALMOST is retired, and STANDARD's
eligibility widens to admit fuel-CONFLICTED-but-otherwise-valid setups, with
the HTF-carry check now conditional on fuel state (required only when
FUELED -- see decision_engine.py's own header comment and _core_gate()'s
docstring-equivalent inline comment for the real-corpus data behind this).

decision_engine.py is a protected file (CLAUDE.md "What Must Never Be
Changed" #1/#3) for its gate FORMULAS and evaluation TIMING specifically --
confirmed by reading those two numbered rules directly. Neither is touched by
this rebuild; only the tier/outcome logic layered on top of them changed, and
that logic is exactly what these new tests exercise. market_regime.py/
micro_regime.py/htf_fuel.py/fuel_gate.py are monkeypatched here rather than
driven with hand-built multi-timeframe candle data, so this test isolates
decision_engine.py's own wiring instead of re-deriving four separate
indicator algorithms' exact numeric thresholds.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import decision_engine as de

_LEVELS_TIGHT_BOX = {
    "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
    "range30m_high": 100.0, "range30m_low": 90.0,
    "daily_atr14": 25.0,  # box=10, box/atr=0.40 -> PREMIUM-tier boundary
    "price": 101.0,       # beyond BO -> LONG
}


def _patch_neutral_regime(monkeypatch):
    """Regime/table combo that never trips the dead-tape or counter-trend
    hard vetoes, so tests can isolate the fuel/HTF gate logic."""
    monkeypatch.setattr(de._micro_regime, "classify_regime", lambda candles: {"regime": "TRENDING"})
    monkeypatch.setattr(de._market_regime, "classify_market_regime",
                         lambda candles: {"table": "NEUTRAL", "quality": "NEUTRAL", "policy": {"bias": None}})


def _patch_htf(monkeypatch, aligned: int, opposed: int = 0):
    trend = "BULLISH" if aligned else "NEUTRAL"
    monkeypatch.setattr(de._htf_fuel, "htf_fuel",
                         lambda c1h, c4h, side: {"trend_1h": trend, "trend_4h": trend,
                                                  "aligned": aligned, "opposed": opposed, "carry": "N/A"})


def _patch_fuel(monkeypatch, verdict: str, ratio: float = 1.5):
    # 2026-09-09: default raised from 1.0 to 1.5, safely clear of the new
    # STANDARD_FUEL_RATIO_FLOOR (1.1) -- tests that aren't specifically
    # exercising the floor shouldn't incidentally trip it. Tests for the
    # floor itself pass an explicit ratio.
    monkeypatch.setattr(de._fuel_gate, "evaluate_fuel_gate",
                         lambda c5m, trigger, side, **kw: {
                             "verdict": verdict,
                             "checks": {"push_volume": {"ratio": ratio}},
                             "htf_aligned": kw.get("fuel_1h"), "htf_opposed": 0,
                         })


def _evaluate(levels=None, hour=15):
    levels = levels or _LEVELS_TIGHT_BOX
    return de.evaluate_15m_decision(
        levels=levels,
        candles_5m=[{}] * 30, candles_15m=[{}] * 30, candles_1h=[{}] * 30,
        candles_4h=[{}] * 30, candles_1d=[{}] * 30, session_hour_utc=hour,
    )


def test_diagnostics_exposed_on_a_real_take_premium_path(monkeypatch):
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=2)
    _patch_fuel(monkeypatch, "FUELED", ratio=1.23)

    decision_dict, _ = _evaluate()

    assert decision_dict["verdict_state"] == "TAKE_PREMIUM"
    assert decision_dict["fuel_verdict"] == "FUELED"
    assert decision_dict["fuel_push_ratio"] == 1.23
    assert decision_dict["trend_1h"] == "BULLISH"
    assert decision_dict["trend_4h"] == "BULLISH"
    assert decision_dict["htf_aligned"] == 2
    assert decision_dict["htf_opposed"] == 0


def test_diagnostics_safe_and_none_when_no_signal_yet():
    # side is None (price inside the box) -- the gate short-circuits before
    # micro_regime/market_regime/htf_fuel/fuel_gate ever run. The new
    # fields must be present and None, not a KeyError/AttributeError.
    levels = {
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "range30m_high": 100.0, "range30m_low": 90.0,
        "daily_atr14": 25.0, "price": 95.0,  # inside the box
    }
    decision_dict, _ = _evaluate(levels=levels)
    assert decision_dict["verdict_state"] == "PASS"
    assert decision_dict["side"] is None
    for key in ("fuel_verdict", "fuel_push_ratio", "trend_1h", "trend_4h", "htf_aligned", "htf_opposed"):
        assert decision_dict[key] is None


def test_take_standard_now_covers_fuel_conflicted_when_otherwise_valid(monkeypatch):
    # GATE_REBUILD_SPEC.md §1: fuel-CONFLICTED-but-valid is a real STANDARD
    # trade now, with NO HTF qualifier -- verified against the real 80-row
    # CONFLICTED corpus (all three HTF buckets profitable). aligned=0 here
    # (no carry at all) to prove the HTF check is genuinely skipped, not just
    # satisfied by coincidence.
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=0)
    _patch_fuel(monkeypatch, "CONFLICTED")

    decision_dict, _ = _evaluate()

    assert decision_dict["verdict_state"] == "TAKE_STANDARD"
    assert decision_dict["tier"] == "STANDARD"
    assert decision_dict["gate"]["checks"]["htf_carry"] is True


def test_take_standard_conflicted_eligible_across_all_real_htf_buckets(monkeypatch):
    # The real corpus splits CONFLICTED profitability across HTF=0/1/2
    # (avg R +0.44/+0.24/+1.41) -- none of the three buckets should be
    # excluded by the gate.
    for aligned in (0, 1, 2):
        _patch_neutral_regime(monkeypatch)
        _patch_htf(monkeypatch, aligned=aligned)
        _patch_fuel(monkeypatch, "CONFLICTED")

        decision_dict, _ = _evaluate()
        assert decision_dict["verdict_state"] == "TAKE_STANDARD", f"failed at aligned={aligned}"


def test_take_standard_still_covers_fueled_not_premium(monkeypatch):
    # The pre-existing FUELED-but-not-premium population (HTF=1, not 2) --
    # confirms it isn't lost in the rebuild, just merged into the same
    # STANDARD label per the spec's naming resolution.
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=1)
    _patch_fuel(monkeypatch, "FUELED")

    decision_dict, _ = _evaluate()

    assert decision_dict["verdict_state"] == "TAKE_STANDARD"
    assert decision_dict["tier"] == "STANDARD"


def test_take_premium_still_requires_fueled_specifically_not_conflicted(monkeypatch):
    # CONFLICTED can never earn PREMIUM, even with both HTFs carrying and a
    # tight box -- only FUELED does. Real boundary, worth locking down.
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=2)
    _patch_fuel(monkeypatch, "CONFLICTED")

    decision_dict, _ = _evaluate()

    assert decision_dict["verdict_state"] == "TAKE_STANDARD"
    assert decision_dict["tier"] == "STANDARD"


def test_no_fuel_is_still_a_hard_veto_not_folded_into_standard(monkeypatch):
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=2)
    _patch_fuel(monkeypatch, "NO_FUEL")

    decision_dict, _ = _evaluate()

    assert decision_dict["verdict_state"] == "PASS"
    assert decision_dict["gate"]["misses"] == ["ghost push (NO_FUEL)"]
    assert "ghost push" in decision_dict["tactical_brief"]


def test_almost_state_no_longer_reachable(monkeypatch):
    # This exact scenario (FUELED, reachability ok, hour ok, only HTF carry
    # missing) used to be ALMOST via the old one_gap branch. That branch is
    # gone -- it now falls straight through to PASS, naming the same cause.
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=0)
    _patch_fuel(monkeypatch, "FUELED")

    decision_dict, _ = _evaluate()

    assert decision_dict["verdict_state"] == "PASS"
    assert decision_dict["verdict_state"] != "ALMOST"
    assert "no carry fuel" in decision_dict["gate"]["misses"][0]


def test_divergence_argument_removed_from_signature():
    params = inspect.signature(de.evaluate_15m_decision).parameters
    assert "confluence_15m" not in params


def test_pass_always_names_a_specific_cause(monkeypatch):
    # Hard-veto path: the headline is hand-crafted per veto, but must still
    # name the specific cause, not a generic stand-down string
    # (GATE_REBUILD_SPEC.md §1's transparency requirement).
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=2)
    _patch_fuel(monkeypatch, "NO_FUEL")
    veto_decision, _ = _evaluate()
    assert veto_decision["verdict_state"] == "PASS"
    assert veto_decision["gate"]["misses"] == ["ghost push (NO_FUEL)"]
    assert "ghost push" in veto_decision["tactical_brief"]

    # Core-gate-miss path: the headline is built directly from gate["misses"],
    # so the exact named cause must appear verbatim.
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=0)
    _patch_fuel(monkeypatch, "FUELED")
    gap_decision, _ = _evaluate()
    assert gap_decision["verdict_state"] == "PASS"
    assert gap_decision["gate"]["misses"]
    assert gap_decision["gate"]["misses"][0] in gap_decision["tactical_brief"]


def test_standard_fuel_ratio_floor_blocks_a_thin_but_technically_fueled_push(monkeypatch):
    # 2026-09-09 STANDARD_FUEL_RATIO_FLOOR (1.1): a push that clears FUELED's
    # own 0.8 threshold but not the stricter 1.1 STANDARD floor must PASS,
    # not TAKE_STANDARD -- this is the exact 0.8-1.0 "marginal zone" the
    # backtest found barely profitable/high-stop-rate.
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=1)
    _patch_fuel(monkeypatch, "FUELED", ratio=0.95)
    decision_dict, _ = _evaluate()
    assert decision_dict["verdict_state"] == "PASS"
    assert "1.1" in decision_dict["gate"]["misses"][0]
    assert "0.95" in decision_dict["gate"]["misses"][0]


def test_standard_fuel_ratio_floor_boundary_exactly_at_1_1_takes_it(monkeypatch):
    # >= is inclusive -- exactly 1.1 must qualify, not just "above" it.
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=1)
    _patch_fuel(monkeypatch, "FUELED", ratio=1.1)
    decision_dict, _ = _evaluate()
    assert decision_dict["verdict_state"] == "TAKE_STANDARD"


def test_standard_fuel_ratio_floor_boundary_just_under_1_1_rejects_it(monkeypatch):
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=1)
    _patch_fuel(monkeypatch, "FUELED", ratio=1.099)
    decision_dict, _ = _evaluate()
    assert decision_dict["verdict_state"] == "PASS"


def test_standard_fuel_ratio_floor_applies_to_conflicted_too(monkeypatch):
    # CONFLICTED needs no HTF carry, but it still needs to clear the same
    # STANDARD floor -- the floor is a fuel-quality gate, not a FUELED-only
    # add-on.
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=0)
    _patch_fuel(monkeypatch, "CONFLICTED", ratio=0.5)
    decision_dict, _ = _evaluate()
    assert decision_dict["verdict_state"] == "PASS"
    assert "floor" in decision_dict["gate"]["misses"][0]


def test_standard_fuel_ratio_floor_does_not_touch_premium(monkeypatch):
    # PREMIUM already requires FUELED (ratio >= 0.8) specifically -- a push
    # between 0.8 and the new 1.1 STANDARD floor must still earn PREMIUM
    # when aligned==2 and the box is tight. The floor is a STANDARD-only
    # addition, not a blanket fuel-quality raise.
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=2)
    _patch_fuel(monkeypatch, "FUELED", ratio=0.85)
    decision_dict, _ = _evaluate()
    assert decision_dict["verdict_state"] == "TAKE_PREMIUM"


def test_standard_fuel_ratio_floor_fails_safe_when_ratio_unavailable(monkeypatch):
    # push_ratio=None (couldn't be measured) must not silently pass the
    # floor -- fail safe to PASS, matching every other "unknown" path in
    # this gate (e.g. fuel_verdict unknown -> not FUELED/CONFLICTED -> PASS).
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=1)
    _patch_fuel(monkeypatch, "FUELED", ratio=None)
    decision_dict, _ = _evaluate()
    assert decision_dict["verdict_state"] == "PASS"


def test_standard_fuel_ratio_floor_reason_names_the_real_ratio():
    # gate["pass"] must be False (not just tier=None) when the floor blocks
    # an otherwise-passing setup -- confirms the caller's `if gate["pass"]:`
    # branch (the ONLY place evaluate_15m_decision() checks this) can't
    # silently fall through to TAKE_STANDARD. Direct _core_gate() call,
    # no monkeypatching, to pin this down at the exact function the fix
    # lives in.
    fuel = {"verdict": "FUELED", "checks": {"push_volume": {"ratio": 0.9}}}
    htf = {"aligned": 1}
    gate = de._core_gate(box=10.0, atr=25.0, fuel=fuel, htf=htf, session_hour=15)
    assert gate["pass"] is False
    assert gate["tier"] is None
    assert "1.1" in gate["misses"][0]
