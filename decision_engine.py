# decision_engine.py
# ==============================================================================
# PHASE 4 — CODED 15M DECISION LAYER (graded conviction model)
# Replaces the disabled LLM chain (see kabroda_mas_flow.py's DISABLED comment).
#
# Design locked 2026-08-27 after CONFLUENCE_RESEARCH_REPORT.md (6-agent
# research pass): a rigid trend-AND-volatility-AND-momentum gate is a real,
# documented anti-pattern -- a 4-year backtest of that shape produced 1,213
# stand-downs against 228 approved trades, and the approved trades lost money
# on average. The fix, per the report and Andy's lock-in: structure stays a
# hard prerequisite; trend becomes a hard veto; volatility and momentum
# become GRADED contributors (confirm/don't, not gate/don't); 1H/4H fuel
# agreement is an informational booster that can upgrade LEAN to STRONG but
# never vetoes and never manufactures a signal alone. Output is a 3-tier
# conviction (STRONG/LEAN/NEUTRAL) per direction, not binary pass/fail.
#
# SCOPE, stated plainly: only the trend-continuation direction Kabroda's own
# structure system naturally produces (breakout + acceptance) is covered.
# The Krown System's counter-trend templates (a pullback-to-55-EMA entry) are
# a genuinely different entry mechanism, deliberately not built here -- it
# would mean a second, conflicting entry trigger, against the "one clean
# decision-maker" principle this whole rebuild is anchored on.
#
# No LLM call anywhere in this file. No prose generation. Cost is zero.
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from harness.unified_audit_writer import gauge as _gauge

GaugeTuple = Tuple[str, str, Optional[float], Optional[str]]

# Real, citation-backed zones (EXTERNAL_VALIDATION_REPORT.md, 2026-08-26).
BBWP_COMPRESSION = 38.0
BBWP_EXPANSION = 75.0
PMARP_OVEREXTENDED = 85.0

_BULLISH_DIVERGENCE = {"BULLISH", "HIDDEN_BULLISH"}
_BEARISH_DIVERGENCE = {"BEARISH", "HIDDEN_BEARISH"}


def _tf_reading(confluence_tf: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Safe defaults for a missing/error timeframe reading -- never crash on
    absent data, just fail honestly toward NEUTRAL."""
    c = confluence_tf or {}
    return {
        "direction_vote": c.get("direction_vote", "UNKNOWN"),
        "bbwp_value": c.get("bbwp_value", 50.0),
        "pmarp_value": c.get("pmarp_value", 50.0),
        "divergence": c.get("divergence", "NONE"),
    }


def evaluate_15m_decision(
    levels: Dict[str, Any],
    targets: Dict[str, Any],
    structure_state: Dict[str, Any],
    confluence_15m: Optional[Dict[str, Any]],
    confluence_1h: Optional[Dict[str, Any]],
    confluence_4h: Optional[Dict[str, Any]],
    stoch_cross_15m: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[GaugeTuple]]:
    """Returns (decision_dict, gauge_readings). decision_dict has exactly
    the ExecutiveBrief field names (kabroda_mas_flow.py) plus a `conviction`
    field (STRONG_LONG/LEAN_LONG/NEUTRAL/LEAN_SHORT/STRONG_SHORT) -- callers
    do `ExecutiveBrief(**{k: v for k, v in decision_dict.items() if k in
    ExecutiveBrief.__fields__})`. This module does not import ExecutiveBrief
    itself, to avoid a decision_engine <-> kabroda_mas_flow import cycle
    (kabroda_mas_flow already imports this module to call it)."""

    tf15 = _tf_reading(confluence_15m)
    tf1h = _tf_reading(confluence_1h)
    tf4h = _tf_reading(confluence_4h)
    stoch = stoch_cross_15m or {"cross_up": False, "cross_down": False, "k": 50.0, "d": 50.0}

    permission = structure_state.get("permission", {}) if structure_state else {}
    action = structure_state.get("action") if structure_state else None
    side = permission.get("side")  # "LONG" | "SHORT" | None

    def _agrees(direction_vote: str) -> Optional[bool]:
        """None when the reading is UNKNOWN/insufficient data -- treated as
        non-confirming, not as a false disagreement."""
        if direction_vote not in ("BULLISH", "BEARISH"):
            return None
        if side == "LONG":
            return direction_vote == "BULLISH"
        if side == "SHORT":
            return direction_vote == "BEARISH"
        return None

    trend_agrees = _agrees(tf15["direction_vote"])

    volatility_confirms: Optional[bool] = None
    momentum_confirms: Optional[bool] = None
    fuel_confirms: Optional[bool] = None

    if side in ("LONG", "SHORT"):
        bbwp_actionable = tf15["bbwp_value"] <= BBWP_COMPRESSION or tf15["bbwp_value"] >= BBWP_EXPANSION
        pmarp_not_opposing = (
            tf15["pmarp_value"] < PMARP_OVEREXTENDED if side == "LONG"
            else tf15["pmarp_value"] > (100.0 - PMARP_OVEREXTENDED)
        )
        volatility_confirms = bbwp_actionable and pmarp_not_opposing

        stoch_confirms = stoch.get("cross_up") if side == "LONG" else stoch.get("cross_down")
        div_set = _BULLISH_DIVERGENCE if side == "LONG" else _BEARISH_DIVERGENCE
        divergence_confirms = tf15["divergence"] in div_set
        momentum_confirms = bool(stoch_confirms) or divergence_confirms

        fuel_1h = _agrees(tf1h["direction_vote"])
        fuel_4h = _agrees(tf4h["direction_vote"])
        fuel_confirms = bool(fuel_1h) and bool(fuel_4h)

    gauges: List[GaugeTuple] = [g for g in [
        _gauge("15M", "trend_direction_vote", tf15["direction_vote"]),
        _gauge("15M", "trend_agrees", trend_agrees),
        _gauge("15M", "bbwp_value", tf15["bbwp_value"]),
        _gauge("15M", "pmarp_value", tf15["pmarp_value"]),
        _gauge("15M", "divergence", tf15["divergence"]),
        _gauge("15M", "stoch_cross_up", stoch.get("cross_up")),
        _gauge("15M", "stoch_cross_down", stoch.get("cross_down")),
        _gauge("15M", "volatility_confirms", volatility_confirms),
        _gauge("15M", "momentum_confirms", momentum_confirms),
        _gauge("15M", "structure_action", action),
        _gauge("15M", "structure_side", side),
        _gauge("1H", "trend_direction_vote", tf1h["direction_vote"]),
        _gauge("4H", "trend_direction_vote", tf4h["direction_vote"]),
        _gauge("1H_4H", "fuel_confirms", fuel_confirms),
    ] if g]

    def _result(conviction: str, reason: str) -> Tuple[Dict[str, Any], List[GaugeTuple]]:
        is_neutral = conviction == "NEUTRAL"
        bias = "NEUTRAL" if is_neutral else conviction.split("_")[1]
        tgt = (targets or {}).get(bias.lower(), {}) if not is_neutral else {}
        return {
            "approval_status": "STAND_DOWN" if is_neutral else "APPROVED",
            "conviction": conviction,
            "tactical_brief": reason,
            "bias": bias,
            "entry_price": tgt.get("entry", 0.0),
            "stop_loss": tgt.get("stop", 0.0),
            "t1": tgt.get("t1", 0.0),
            "t2": tgt.get("t2", 0.0),
            "t3": tgt.get("t3", 0.0),
            "formatted_newsletter_md": "",
        }, gauges

    if action != "GO" or side not in ("LONG", "SHORT"):
        return _result("NEUTRAL", f"NO_STRUCTURE_PERMISSION ({action or 'UNKNOWN'})")

    if not trend_agrees:
        return _result("NEUTRAL", f"TREND_VETO ({tf15['direction_vote']} vs structure side {side})")

    if not (targets or {}).get(side.lower()):
        return _result("NEUTRAL", "NO_TARGETS_COMPUTED")

    confirm_count = sum([bool(volatility_confirms), bool(momentum_confirms)])

    if confirm_count == 0:
        return _result("NEUTRAL", "NO_CONFIRMATION (trend agrees, but volatility and momentum both silent)")

    if confirm_count == 2:
        return _result(f"STRONG_{side}", "STRONG: trend + volatility + momentum all confirm")

    # confirm_count == 1: LEAN, unless 1H/4H fuel agreement upgrades it to
    # STRONG -- fuel can boost an existing lean, never manufacture a signal
    # alone (confirm_count == 0 always stays NEUTRAL regardless of fuel).
    if fuel_confirms:
        return _result(f"STRONG_{side}", "STRONG: trend + one of volatility/momentum + 1H/4H fuel all confirm")
    return _result(f"LEAN_{side}", "LEAN: trend confirms, only one of volatility/momentum confirms")
