"""
Regression coverage for decision_engine.py -- v2 (Krown Cross + 4H RSI gate).

Rewritten 2026-09-11 for the v2 rebuild (CC_PACKAGE.md, Kabroda AI Brain
repo; CANON.md §8). v1's tests (fuel gate, PREMIUM/STANDARD tiers,
STANDARD_FUEL_RATIO_FLOOR, PROMOTED_PUSH_FLOOR, the dead-tape/counter-trend/
no-fuel veto stack) all exercised behavior that no longer exists -- fuel was
found to be a post-fill information artifact (not decision-time computable;
confirmed in both the calibration scripts and this file's own fuel_gate.py),
and the veto stack was measured against the real v2 candidate population and
found not to earn its complexity (brain/audit_evidence/
d0_veto_stack_on_candidate.py, Kabroda AI Brain repo). See decision_engine.py's
own header comment for the full v1->v2 rationale.

v2's gate has FOUR conditions, all must pass: reachability (box<=0.55xATR,
unchanged from v1), HTF aligned>=1 (the OLD 9/21 EMA read, unchanged from v1
-- real and load-bearing, not redundant with Krown Cross), Krown Cross
votes==2 (NEW 21/55 EMA stack + 6-bar slope, BOTH 1H and 4H), and 4H RSI(14)
Wilder in the control zone AT LOCK (LONG 62-80, SHORT 20-38, read from
levels["rsi_4h_at_lock"] -- frozen by battlebox_pipeline.py, not recomputed
here). One outcome now: TAKE or PASS. No tier, no PREMIUM/STANDARD split.

htf_fuel.py/market_regime.py/micro_regime.py are monkeypatched here rather
than driven with hand-built multi-timeframe candle data, so this test
isolates decision_engine.py's own wiring instead of re-deriving indicator
algorithms' exact numeric thresholds -- same approach the v1 tests used.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import decision_engine as de

_LEVELS_TIGHT_BOX = {
    "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
    "range30m_high": 100.0, "range30m_low": 90.0,
    "daily_atr14": 25.0,  # box=10, box/atr=0.40 -> well inside the 0.55 ceiling
    "price": 101.0,       # beyond BO -> LONG
    "rsi_4h_at_lock": 70.0,  # inside the LONG control zone (62-80)
}


def _patch_neutral_regime(monkeypatch):
    """Regime/table combo that would have tripped the old dead-tape/counter-
    trend vetoes if they still existed -- used to prove v2 no longer checks
    them, not just to get a clean baseline."""
    monkeypatch.setattr(de._micro_regime, "classify_regime", lambda candles: {"regime": "TRENDING"})
    monkeypatch.setattr(de._market_regime, "classify_market_regime",
                         lambda candles: {"table": "NEUTRAL", "quality": "NEUTRAL", "policy": {"bias": None}})


def _patch_htf(monkeypatch, aligned: int, opposed: int = 0):
    trend = "BULLISH" if aligned else "NEUTRAL"
    monkeypatch.setattr(de._htf_fuel, "htf_fuel",
                         lambda c1h, c4h, side: {"trend_1h": trend, "trend_4h": trend,
                                                  "aligned": aligned, "opposed": opposed, "carry": "N/A"})


def _patch_cross(monkeypatch, votes: int):
    monkeypatch.setattr(de._htf_fuel, "krown_cross_votes",
                         lambda c1h, c4h, side: {"votes": votes,
                                                  "cross_1h": votes >= 1, "cross_4h": votes >= 2})


def _evaluate(levels=None, hour=15):
    levels = levels or _LEVELS_TIGHT_BOX
    return de.evaluate_15m_decision(
        levels=levels,
        candles_5m=[{}] * 30, candles_15m=[{}] * 30, candles_1h=[{}] * 30,
        candles_4h=[{}] * 30, candles_1d=[{}] * 30, session_hour_utc=hour,
    )


def _take(monkeypatch, levels=None):
    """Every v2 condition passing -- the baseline every negative test
    flips exactly one condition away from."""
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=2)
    _patch_cross(monkeypatch, votes=2)
    return _evaluate(levels=levels)


def test_all_four_conditions_passing_is_a_take(monkeypatch):
    decision_dict, _ = _take(monkeypatch)
    assert decision_dict["verdict_state"] == "TAKE"
    assert decision_dict["approval_status"] == "APPROVED"
    assert decision_dict["tier"] is None  # v2 has no tier


def test_diagnostics_exposed_on_a_real_take_path(monkeypatch):
    decision_dict, _ = _take(monkeypatch)
    assert decision_dict["trend_1h"] == "BULLISH"
    assert decision_dict["trend_4h"] == "BULLISH"
    assert decision_dict["htf_aligned"] == 2
    assert decision_dict["htf_opposed"] == 0
    assert decision_dict["krown_cross_votes"] == 2
    assert decision_dict["rsi_4h_at_lock"] == 70.0
    # fuel is fully retired -- keys present, always None
    assert decision_dict["fuel_verdict"] is None
    assert decision_dict["fuel_push_ratio"] is None


def test_diagnostics_safe_and_none_when_no_signal_yet():
    levels = {
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "range30m_high": 100.0, "range30m_low": 90.0,
        "daily_atr14": 25.0, "price": 95.0,  # inside the box
    }
    decision_dict, _ = _evaluate(levels=levels)
    assert decision_dict["verdict_state"] == "PASS"
    assert decision_dict["side"] is None
    for key in ("fuel_verdict", "fuel_push_ratio", "trend_1h", "trend_4h",
                "htf_aligned", "htf_opposed", "krown_cross_votes", "rsi_4h_at_lock"):
        assert decision_dict[key] is None


def test_reachability_failure_blocks_take(monkeypatch):
    levels = dict(_LEVELS_TIGHT_BOX)
    levels["daily_atr14"] = 10.0  # box=10, box/atr=1.0 -> way past the 0.55 ceiling
    decision_dict, _ = _take(monkeypatch, levels=levels)
    assert decision_dict["verdict_state"] == "PASS"
    assert not decision_dict["gate"]["checks"]["reachability"]


def test_htf_aligned_zero_blocks_take(monkeypatch):
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=0)
    _patch_cross(monkeypatch, votes=2)
    decision_dict, _ = _evaluate()
    assert decision_dict["verdict_state"] == "PASS"
    assert not decision_dict["gate"]["checks"]["htf_aligned"]
    assert any("carry" in m for m in decision_dict["gate"]["misses"])


def test_krown_cross_one_vote_is_not_enough(monkeypatch):
    """votes==1 (only one of 1H/4H) must NOT take -- votes==2 is required,
    and this is deliberately not the same check as htf_aligned>=1 (the two
    EMA pairs, 9/21 vs 21/55, can and do disagree)."""
    _patch_neutral_regime(monkeypatch)
    _patch_htf(monkeypatch, aligned=2)
    _patch_cross(monkeypatch, votes=1)
    decision_dict, _ = _evaluate()
    assert decision_dict["verdict_state"] == "PASS"
    assert not decision_dict["gate"]["checks"]["krown_cross"]
    assert any("Krown Cross" in m for m in decision_dict["gate"]["misses"])


def test_rsi_outside_zone_blocks_take(monkeypatch):
    levels = dict(_LEVELS_TIGHT_BOX)
    levels["rsi_4h_at_lock"] = 50.0  # dead center, well outside 62-80
    decision_dict, _ = _take(monkeypatch, levels=levels)
    assert decision_dict["verdict_state"] == "PASS"
    assert not decision_dict["gate"]["checks"]["rsi_4h_zone"]


def test_rsi_missing_fails_safe_not_open(monkeypatch):
    """No rsi_4h_at_lock in levels (e.g. a lock persisted before this
    field existed) must PASS, not silently take on unknown momentum."""
    levels = dict(_LEVELS_TIGHT_BOX)
    del levels["rsi_4h_at_lock"]
    decision_dict, _ = _take(monkeypatch, levels=levels)
    assert decision_dict["verdict_state"] == "PASS"
    assert not decision_dict["gate"]["checks"]["rsi_4h_zone"]


def test_rsi_long_zone_boundaries():
    """LONG: 62 <= r < 80. Pinned directly at _core_gate(), no monkeypatching."""
    cross = {"votes": 2}
    htf = {"aligned": 1}
    assert de._core_gate(box=10.0, atr=25.0, cross=cross, htf=htf,
                          rsi_4h_at_lock=62.0, side="LONG")["pass"] is True
    assert de._core_gate(box=10.0, atr=25.0, cross=cross, htf=htf,
                          rsi_4h_at_lock=61.9, side="LONG")["pass"] is False
    assert de._core_gate(box=10.0, atr=25.0, cross=cross, htf=htf,
                          rsi_4h_at_lock=79.9, side="LONG")["pass"] is True
    assert de._core_gate(box=10.0, atr=25.0, cross=cross, htf=htf,
                          rsi_4h_at_lock=80.0, side="LONG")["pass"] is False


def test_rsi_short_zone_boundaries():
    """SHORT: 20 < r <= 38."""
    cross = {"votes": 2}
    htf = {"aligned": 1}
    assert de._core_gate(box=10.0, atr=25.0, cross=cross, htf=htf,
                          rsi_4h_at_lock=20.0, side="SHORT")["pass"] is False
    assert de._core_gate(box=10.0, atr=25.0, cross=cross, htf=htf,
                          rsi_4h_at_lock=20.1, side="SHORT")["pass"] is True
    assert de._core_gate(box=10.0, atr=25.0, cross=cross, htf=htf,
                          rsi_4h_at_lock=38.0, side="SHORT")["pass"] is True
    assert de._core_gate(box=10.0, atr=25.0, cross=cross, htf=htf,
                          rsi_4h_at_lock=38.1, side="SHORT")["pass"] is False


def test_dead_tape_no_longer_blocks_a_take(monkeypatch):
    """v1's dead-tape veto is retired (measured, not carried over -- see
    module docstring). A DEAD 15m regime must NOT block an otherwise-
    passing v2 gate."""
    monkeypatch.setattr(de._micro_regime, "classify_regime", lambda candles: {"regime": "DEAD"})
    monkeypatch.setattr(de._market_regime, "classify_market_regime",
                         lambda candles: {"table": "NEUTRAL", "quality": "NEUTRAL", "policy": {"bias": None}})
    monkeypatch.setattr(de._htf_fuel, "htf_fuel",
                         lambda c1h, c4h, side: {"trend_1h": "BULLISH", "trend_4h": "BULLISH",
                                                  "aligned": 2, "opposed": 0, "carry": "N/A"})
    _patch_cross(monkeypatch, votes=2)
    decision_dict, _ = _evaluate()
    assert decision_dict["verdict_state"] == "TAKE"
    assert decision_dict["micro_regime"] == "DEAD"  # still surfaced for display


def test_counter_trend_no_longer_blocks_a_take(monkeypatch):
    """v1's counter-trend veto is retired. A GOOD daily table against the
    side must NOT block an otherwise-passing v2 LONG."""
    monkeypatch.setattr(de._micro_regime, "classify_regime", lambda candles: {"regime": "TRENDING"})
    monkeypatch.setattr(de._market_regime, "classify_market_regime",
                         lambda candles: {"table": "GOOD", "quality": "GOOD", "policy": {"bias": "DOWN"}})
    monkeypatch.setattr(de._htf_fuel, "htf_fuel",
                         lambda c1h, c4h, side: {"trend_1h": "BULLISH", "trend_4h": "BULLISH",
                                                  "aligned": 2, "opposed": 0, "carry": "N/A"})
    _patch_cross(monkeypatch, votes=2)
    decision_dict, _ = _evaluate()  # LONG side, DOWN-biased GOOD table
    assert decision_dict["verdict_state"] == "TAKE"
    assert decision_dict["market_regime_table"] == "GOOD"  # still surfaced for display


def test_no_fuel_gate_call_exists_at_all(monkeypatch):
    """fuel_gate.py is not imported/called anywhere in decision_engine.py
    any more -- confirms the retirement is real, not just untested."""
    assert not hasattr(de, "_fuel_gate")


def test_plan_uses_t1_at_1_box_not_0_618(monkeypatch):
    """v2's T1 anchor moved from 0.618x box to 1.0x box (CC_PACKAGE.md §1).
    box=10 here -> T1 should sit at trigger+10, not trigger+6.18."""
    decision_dict, _ = _take(monkeypatch)
    assert decision_dict["t1"] == 110.0  # entry 100 + 1.0*box(10)
    assert decision_dict["t3"] == 116.18  # entry 100 + 1.618*box(10)


def test_management_text_has_no_tier_branching(monkeypatch):
    decision_dict, _ = _take(monkeypatch)
    mgmt = decision_dict["plan"]["management"]
    assert "PREMIUM" not in mgmt
    assert "STANDARD" not in mgmt
    assert "50%" in mgmt
