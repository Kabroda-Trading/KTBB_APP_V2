"""
Regression coverage for decision_engine.py's SS9a diagnostic fields
(fuel_verdict, fuel_push_ratio, trend_1h, trend_4h, htf_aligned,
htf_opposed) -- added 2026-08-31 so GateLog's forward-test log can surface
them without re-deriving anything. Purely additive: no gate formula,
threshold, or verdict logic is touched, only confirmed here.

decision_engine.py is a protected file (CLAUDE.md "What Must Never Be
Changed" #1/#3) -- market_regime.py/micro_regime.py/htf_fuel.py/
fuel_gate.py are monkeypatched here rather than driven with hand-built
multi-timeframe candle data, so this test isolates decision_engine.py's
own wiring (the thing that changed) instead of re-deriving four separate
indicator algorithms' exact numeric thresholds.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import decision_engine as de


def test_diagnostics_exposed_on_a_real_take_premium_path(monkeypatch):
    monkeypatch.setattr(de._micro_regime, "classify_regime", lambda candles: {"regime": "TRENDING"})
    monkeypatch.setattr(de._market_regime, "classify_market_regime",
                         lambda candles: {"table": "GOOD", "quality": "GOOD", "policy": {"bias": "UP"}})
    monkeypatch.setattr(de._htf_fuel, "htf_fuel",
                         lambda c1h, c4h, side: {"trend_1h": "BULLISH", "trend_4h": "BULLISH",
                                                  "aligned": 2, "opposed": 0, "carry": "STRONG"})
    monkeypatch.setattr(de._fuel_gate, "evaluate_fuel_gate",
                         lambda c5m, trigger, side, **kw: {
                             "verdict": "FUELED",
                             "checks": {"push_volume": {"ratio": 1.23}},
                             "htf_aligned": 2, "htf_opposed": 0,
                         })

    levels = {
        "breakout_trigger": 100.0, "breakdown_trigger": 90.0,
        "range30m_high": 100.0, "range30m_low": 90.0,
        "daily_atr14": 25.0,  # box=10, box/atr=0.40 -> PREMIUM boundary
        "price": 101.0,       # beyond BO -> LONG
    }
    decision_dict, _ = de.evaluate_15m_decision(
        levels=levels, confluence_15m=None,
        candles_5m=[{}] * 30, candles_15m=[{}] * 30, candles_1h=[{}] * 30,
        candles_4h=[{}] * 30, candles_1d=[{}] * 30, session_hour_utc=15,
    )

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
    decision_dict, _ = de.evaluate_15m_decision(
        levels=levels, confluence_15m=None,
        candles_5m=[], candles_15m=[], candles_1h=[], candles_4h=[], candles_1d=[],
        session_hour_utc=15,
    )
    assert decision_dict["verdict_state"] == "PASS"
    assert decision_dict["side"] is None
    for key in ("fuel_verdict", "fuel_push_ratio", "trend_1h", "trend_4h", "htf_aligned", "htf_opposed"):
        assert decision_dict[key] is None
