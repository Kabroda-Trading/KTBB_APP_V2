# decision_engine.py
# ==============================================================================
# PHASE 4 — CODED 15M DECISION LAYER
# Replaces the disabled LLM chain (Senior Analyst + interpreters, see
# kabroda_mas_flow.py's DISABLED comment) with real, deterministic code.
# Spec source: EXTERNAL_VALIDATION_REPORT.md (this repo, root) — the 4
# Krown System templates, citation-backed against Trading Knowledge.
#
# SCOPE, stated plainly rather than silently narrowed: only Templates 1
# (Uptrend Long) and 3 (Downtrend Short) are implemented. Templates 2/4
# (Counter-Trend Short/Long) describe a pullback-to-55-EMA entry mechanic
# that is a genuinely different entry philosophy from Kabroda's own
# breakout-and-acceptance structure system (sse_engine.py/
# structure_state_engine.py) -- forcing them in would mean inventing a
# second, conflicting entry trigger rather than confirming the one
# structure already produced. Left out for v1, not silently dropped.
#
# DESIGN, per Andy's direct correction to the first draft of this plan:
#   - Structure (structure_state_engine.compute_structure_state()) is the
#     PRIMARY gate -- has price earned permission to trade, and which side.
#     Untouched, not recomputed here.
#   - The matched template (trend + volatility + momentum, evaluated on
#     15M's OWN data only) either confirms that side (APPROVED) or vetoes
#     it (STAND_DOWN, named reason) -- one clear decision-maker, not a
#     second cross-timeframe ruling.
#   - 1H/4H confluence is attached as an informational gauge ONLY. It never
#     changes APPROVED to STAND_DOWN or back. "Does the 1H/4H have fuel" is
#     answered honestly on the output, not used as a hidden veto.
#   - STAND_DOWN is a first-class, equally-valid output -- not a fallback.
#   - No prose, no formatting, no LLM call anywhere in this file. Cost is
#     zero. tactical_brief/formatted_newsletter_md are short, deterministic
#     status strings only.
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from harness.unified_audit_writer import gauge as _gauge

GaugeTuple = Tuple[str, str, Optional[float], Optional[str]]

# Real, citation-backed zones (EXTERNAL_VALIDATION_REPORT.md, 2026-08-26).
BBWP_COMPRESSION = 38.0
BBWP_EXPANSION = 75.0
PMARP_OVEREXTENDED = 85.0


def _tf_reading(confluence_tf: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Safe defaults for a missing/error timeframe reading -- never crash on
    absent data, just fail the template match honestly (STAND_DOWN)."""
    c = confluence_tf or {}
    return {
        "direction_vote": c.get("direction_vote", "UNKNOWN"),
        "ema21": c.get("ema21"),
        "ema55": c.get("ema55"),
        "bbwp_value": c.get("bbwp_value", 50.0),
        "pmarp_value": c.get("pmarp_value", 50.0),
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
    the ExecutiveBrief field names (kabroda_mas_flow.py) so the caller can
    do `ExecutiveBrief(**decision_dict)` directly -- this module does not
    import ExecutiveBrief itself, to avoid a decision_engine <->
    kabroda_mas_flow import cycle (kabroda_mas_flow already imports this
    module to call it)."""

    tf15 = _tf_reading(confluence_15m)
    tf1h = _tf_reading(confluence_1h)
    tf4h = _tf_reading(confluence_4h)
    stoch = stoch_cross_15m or {"cross_up": False, "cross_down": False, "k": 50.0, "d": 50.0}

    permission = structure_state.get("permission", {}) if structure_state else {}
    action = structure_state.get("action") if structure_state else None
    side = permission.get("side")  # "LONG" | "SHORT" | None

    gauges: List[GaugeTuple] = [g for g in [
        _gauge("15M", "trend_direction_vote", tf15["direction_vote"]),
        _gauge("15M", "bbwp_value", tf15["bbwp_value"]),
        _gauge("15M", "pmarp_value", tf15["pmarp_value"]),
        _gauge("15M", "stoch_cross_up", stoch.get("cross_up")),
        _gauge("15M", "stoch_cross_down", stoch.get("cross_down")),
        _gauge("15M", "structure_action", action),
        _gauge("15M", "structure_side", side),
        _gauge("1H", "trend_direction_vote", tf1h["direction_vote"]),
        _gauge("1H", "fuel_agrees", (tf1h["direction_vote"] == "BULLISH" and side == "LONG")
               or (tf1h["direction_vote"] == "BEARISH" and side == "SHORT") if side else None),
        _gauge("4H", "trend_direction_vote", tf4h["direction_vote"]),
        _gauge("4H", "fuel_agrees", (tf4h["direction_vote"] == "BULLISH" and side == "LONG")
               or (tf4h["direction_vote"] == "BEARISH" and side == "SHORT") if side else None),
    ] if g]

    def _stand_down(reason: str) -> Tuple[Dict[str, Any], List[GaugeTuple]]:
        return {
            "approval_status": "STAND_DOWN",
            "tactical_brief": reason,
            "bias": "NEUTRAL",
            "entry_price": 0.0,
            "stop_loss": 0.0,
            "t1": 0.0,
            "t2": 0.0,
            "t3": 0.0,
            "formatted_newsletter_md": "",
        }, gauges

    if action != "GO" or side not in ("LONG", "SHORT"):
        return _stand_down(f"NO_STRUCTURE_PERMISSION ({action or 'UNKNOWN'})")

    tgt = (targets or {}).get(side.lower())
    if not tgt:
        return _stand_down("NO_TARGETS_COMPUTED")

    if side == "LONG":
        # Template 1 -- Uptrend Long: 21 EMA > 55 EMA, BBWP in an actionable
        # zone (compressed and about to break, OR already expanding to
        # confirm the move), PMARP not already overextended (that's an exit
        # signal per the template, not an entry green light), momentum
        # (stochastic) not actively crossing down against the trade.
        trend_ok = tf15["direction_vote"] == "BULLISH"
        volatility_ok = tf15["bbwp_value"] <= BBWP_COMPRESSION or tf15["bbwp_value"] >= BBWP_EXPANSION
        momentum_ok = tf15["pmarp_value"] < PMARP_OVEREXTENDED and not stoch.get("cross_down")
        template = "TEMPLATE_1_UPTREND_LONG"
    else:
        # Template 3 -- Downtrend Short: mirror of Template 1.
        trend_ok = tf15["direction_vote"] == "BEARISH"
        volatility_ok = tf15["bbwp_value"] <= BBWP_COMPRESSION or tf15["bbwp_value"] >= BBWP_EXPANSION
        momentum_ok = tf15["pmarp_value"] > (100.0 - PMARP_OVEREXTENDED) and not stoch.get("cross_up")
        template = "TEMPLATE_3_DOWNTREND_SHORT"

    if not trend_ok:
        return _stand_down(f"TREND_DISAGREES_WITH_STRUCTURE ({tf15['direction_vote']})")
    if not volatility_ok:
        return _stand_down(f"VOLATILITY_NOT_ACTIONABLE (bbwp={tf15['bbwp_value']})")
    if not momentum_ok:
        return _stand_down(f"MOMENTUM_DOES_NOT_CONFIRM (pmarp={tf15['pmarp_value']})")

    return {
        "approval_status": "APPROVED",
        "tactical_brief": template,
        "bias": side,
        "entry_price": tgt["entry"],
        "stop_loss": tgt["stop"],
        "t1": tgt["t1"],
        "t2": tgt["t2"],
        "t3": tgt["t3"],
        "formatted_newsletter_md": "",
    }, gauges
