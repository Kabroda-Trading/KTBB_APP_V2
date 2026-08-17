# harness/timeframe_calibration.py
# =============================================================================
# ATR-based calibration thresholds per timeframe.
#
# Source: DS's 2026-07-19 research handoff (external BTC professional-trading
# benchmarks) — NOT yet backtested against Kabroda's own production data.
# Per this project's own anti-overfitting discipline (no parameter change
# without N>=30 AND a mechanistic reason; a backtest or external research
# citation alone is not sufficient — see CLAUDE.md-adjacent rules in the
# master plan), these are STARTING HYPOTHESES for H11-H14 in audit_ai.py to
# log and watch, tier-gated through harness.tier_labels — not enforcement
# thresholds. Nothing here gates a live trade or changes construction logic.
# See CC_HANDOFF.md's 2026-07-19 exchange for the full reasoning.
# =============================================================================

from typing import NamedTuple, Optional


class TFCalibration(NamedTuple):
    min_atr_pct: float    # H13: below this, ATR itself is "too small" for this timeframe
    target_atr_min: float  # H11: target_distance_pct / atr_pct_at_decision should be >= this
    target_atr_max: float
    stop_atr_min: float    # H12: stop_distance_pct / atr_pct_at_decision should be within [min, max]
    stop_atr_max: float
    min_rr: float           # H14: target_distance_pct / stop_distance_pct should be >= this


CALIBRATION = {
    "15M": TFCalibration(min_atr_pct=0.10, target_atr_min=2.0, target_atr_max=3.0, stop_atr_min=1.5, stop_atr_max=2.0, min_rr=1.5),
    "1H":  TFCalibration(min_atr_pct=0.30, target_atr_min=2.0, target_atr_max=3.0, stop_atr_min=1.5, stop_atr_max=2.5, min_rr=2.0),
    "4H":  TFCalibration(min_atr_pct=0.80, target_atr_min=2.0, target_atr_max=3.5, stop_atr_min=2.0, stop_atr_max=3.0, min_rr=2.0),
}


def get_calibration(decision_timeframe: str) -> Optional[TFCalibration]:
    return CALIBRATION.get((decision_timeframe or "").upper())
