"""
Unit coverage for trade_plan.anticipate_setup() -- the 2026-08-31 fix for
the WAITING-state plan visibility gap Andy found on the live site
(kabroda.com AGENT_LOG.md / Kabroda AI Brain repo AGENT_LOG.md, "WAITING-
state plan visibility gap"). Answers the one thing decision_engine.py's
real gate can't know pre-cross: which direction to anticipate.

anticipate_setup() calls market_regime.classify_market_regime(),
micro_regime.classify_regime(), and htf_fuel.htf_fuel() internally (via
function-local imports) -- monkeypatched here rather than hand-derived
with valid multi-timeframe candle data, matching test_decision_engine.py's
established isolation pattern for the same underlying modules.
reachability.py is real, pure math -- exercised directly via box/atr.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import market_regime
import micro_regime
import htf_fuel

import trade_plan as tp

# box=10, atr=25 -> ratio=0.4 -- reachable (<=0.55) and PREMIUM-tier boundary (<=0.40)
BO, BD, ATR = 100.0, 90.0, 25.0
LIVE_HOUR = 15  # not in DEAD_HOURS (0-11, 18-20)


def _patch_regimes(monkeypatch, daily_quality="GOOD", daily_bias="UP",
                    micro_regime_value="TRENDING", trend_1h="BULLISH", trend_4h="BULLISH"):
    monkeypatch.setattr(market_regime, "classify_market_regime",
                         lambda candles: {"table": "TRENDING_UP", "quality": daily_quality,
                                           "policy": {"bias": daily_bias}})
    monkeypatch.setattr(micro_regime, "classify_regime",
                         lambda candles: {"regime": micro_regime_value})
    monkeypatch.setattr(htf_fuel, "htf_fuel",
                         lambda c1h, c4h, side: {"trend_1h": trend_1h, "trend_4h": trend_4h,
                                                  "aligned": 2, "opposed": 0, "carry": "STRONG"})


def test_viable_via_daily_bias_alignment_long(monkeypatch):
    _patch_regimes(monkeypatch, daily_quality="GOOD", daily_bias="UP")
    result = tp.anticipate_setup(BO, BD, ATR, [{}], [{}], [{}], [{}], LIVE_HOUR)
    assert result["viable"] is True
    assert result["side"] == "LONG"
    assert result["reason"] == "anticipating LONG -- aligned with a UP daily trend on a GOOD table"
    # 2026-09-10: the structured fields lock_disposition() keys off.
    assert result["category"] == "VIABLE"
    assert result["box_atr_ratio"] == 0.4
    assert result["htf_backs_side"] is True   # both HTF timeframes BULLISH, side LONG


def test_viable_via_daily_bias_with_no_htf_backing_is_flagged(monkeypatch):
    # Side comes off a GOOD UP daily table, but neither 1H nor 4H is BULLISH
    # -> htf_backs_side False (the C_WEAK "watching one side" case).
    _patch_regimes(monkeypatch, daily_quality="GOOD", daily_bias="UP",
                    trend_1h="NEUTRAL", trend_4h="NEUTRAL")
    result = tp.anticipate_setup(BO, BD, ATR, [{}], [{}], [{}], [{}], LIVE_HOUR)
    assert result["viable"] is True and result["side"] == "LONG"
    assert result["htf_backs_side"] is False


def test_not_viable_categories_are_labeled(monkeypatch):
    # v2 (2026-09-11): DEAD_HOUR/DEAD_TAPE are no longer categories this
    # function can return at all -- session_hour_utc and micro_regime.py
    # are not even consulted any more (measured against the real v2
    # candidate and cut, see anticipate_setup()'s own docstring). Only
    # WIDE_BOX and NO_DIRECTION remain real not-viable categories.
    _patch_regimes(monkeypatch)
    wide = tp.anticipate_setup(100.0, 0.0, 1.0, [{}], [{}], [{}], [{}], LIVE_HOUR)
    assert wide["category"] == "WIDE_BOX"

    _patch_regimes(monkeypatch, daily_quality="MARGINAL", daily_bias=None,
                    trend_1h="BULLISH", trend_4h="BEARISH")
    no_dir = tp.anticipate_setup(BO, BD, ATR, [{}], [{}], [{}], [{}], LIVE_HOUR)
    assert no_dir["category"] == "NO_DIRECTION"


def test_viable_via_daily_bias_alignment_short(monkeypatch):
    _patch_regimes(monkeypatch, daily_quality="GOOD", daily_bias="DOWN", trend_1h="BEARISH", trend_4h="BEARISH")
    result = tp.anticipate_setup(BO, BD, ATR, [{}], [{}], [{}], [{}], LIVE_HOUR)
    assert result["viable"] is True
    assert result["side"] == "SHORT"


def test_not_viable_reachability_fails(monkeypatch):
    _patch_regimes(monkeypatch)
    result = tp.anticipate_setup(100.0, 0.0, 1.0, [{}], [{}], [{}], [{}], LIVE_HOUR)  # box=100, atr=1 -> way too wide
    assert result["viable"] is False
    assert "reach" in result["reason"].lower() or "T1" in result["reason"]


def test_dead_hour_no_longer_blocks_viability(monkeypatch):
    # v2 (2026-09-11): dead-hour vetoing was measured against the real v2
    # candidate (brain/audit_evidence/d0_veto_stack_on_candidate.py, only 4
    # samples, too few to read either direction) and cut -- session_hour_utc
    # 19 (the old DEAD_HOURS range) no longer makes an otherwise-viable
    # GOOD/UP daily-bias setup non-viable. Renamed/inverted from the old
    # test_not_viable_dead_hour, which asserted the retired behavior.
    _patch_regimes(monkeypatch)
    result = tp.anticipate_setup(BO, BD, ATR, [{}], [{}], [{}], [{}], session_hour_utc=19)
    assert result["viable"] is True
    assert result["category"] == "VIABLE"


def test_dead_micro_regime_no_longer_blocks_viability(monkeypatch):
    # v2: micro_regime.py is no longer even called by anticipate_setup()
    # (dead-tape vetoing measured and cut, same commit as dead-hour above --
    # 26 real trades averaging +0.35R, still positive, a real but modest
    # frequency-for-selectivity tradeoff CC's call skipped). Setting
    # micro_regime_value="DEAD" here has no effect any more -- confirms it,
    # rather than asserting the retired veto (the old test_not_viable_dead_
    # micro_regime).
    _patch_regimes(monkeypatch, micro_regime_value="DEAD")
    result = tp.anticipate_setup(BO, BD, ATR, [{}], [{}], [{}], [{}], LIVE_HOUR)
    assert result["viable"] is True
    assert result["category"] == "VIABLE"


def test_viable_via_htf_fallback_when_no_clear_daily_bias(monkeypatch):
    # quality not GOOD -- the counter-trend-alignment path doesn't apply,
    # but both HTF timeframes lean BULLISH.
    _patch_regimes(monkeypatch, daily_quality="MARGINAL", daily_bias=None,
                    trend_1h="BULLISH", trend_4h="BULLISH")
    result = tp.anticipate_setup(BO, BD, ATR, [{}], [{}], [{}], [{}], LIVE_HOUR)
    assert result["viable"] is True
    assert result["side"] == "LONG"
    assert "HTF" in result["reason"]


def test_not_viable_genuinely_ambiguous_direction(monkeypatch):
    # No daily bias, HTF split (one bullish one bearish) -- genuinely
    # undetermined. Must defer (viable=False), not guess.
    _patch_regimes(monkeypatch, daily_quality="MARGINAL", daily_bias=None,
                    trend_1h="BULLISH", trend_4h="BEARISH")
    result = tp.anticipate_setup(BO, BD, ATR, [{}], [{}], [{}], [{}], LIVE_HOUR)
    assert result["viable"] is False
    assert "awaiting a cross" in result["reason"]


def test_session_hour_none_is_never_a_dead_hour(monkeypatch):
    _patch_regimes(monkeypatch)
    result = tp.anticipate_setup(BO, BD, ATR, [{}], [{}], [{}], [{}], session_hour_utc=None)
    assert result["viable"] is True
