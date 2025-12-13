"""
KTBB Trade Logic Module v2.0 (backend implementation stub)

This module is a *consumer* of the structural levels coming out of the
Execution Anchor (SSE). It does **not** invent or mutate levels.

Inputs (from backend):

    levels: {
        "daily_support": float,
        "daily_resistance": float,
        "breakout_trigger": float,
        "breakdown_trigger": float,
        "range30m_high": float,
        "range30m_low": float,
    }

    bias_label: "bullish" | "bearish" | "neutral"
    htf_shelves: optional HTF shelf ladders (same shape as SSE output)
    range_30m: { "high": float, "low": float }

Outputs (high-level summary for DMR + future Battle Box UI):

    {
        "analysis_bias": "long" | "short" | "neutral",
        "regime": "trend" | "range" | "pre-breakout",
        "primary_side": "long" | "short" | "neutral",
        "primary_strategies": [ { id, name, direction, summary } ],
        "secondary_strategies": [ ... ],
        "targets_hint": {
            "long": {
                "primary_htf": float,
                "htf_extensions": [float],
                "intraday_steps": [float],
            } | null,
            "short": {
                "primary_htf": float,
                "htf_extensions": [float],
                "intraday_steps": [float],
            } | null,
        },
        "outlook_text": str,  # text block for DMR Section 4
    }

This implementation intentionally keeps *entry_conditions_met* and
*exit_conditions_met* out of scope, because those require live 15m/5m
candle state and EMAs, which the current backend does not supply.
The DMR is a **pre-session plan**, not a live signal engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple


Side = Literal["long", "short", "neutral"]


@dataclass
class StrategyDescriptor:
    id: str           # e.g. "S0"
    name: str         # human label
    direction: Side   # long / short / neutral (for range tactics)
    summary: str      # one-line description for UI / logs


# ----------------------------------------------------------------------
# Strategy catalog (compressed, human-readable summaries)
# ----------------------------------------------------------------------

STRATEGIES: Dict[str, StrategyDescriptor] = {
    # Trend / trigger based
    "S0_long": StrategyDescriptor(
        id="S0",
        name="Trigger Pullback – Long",
        direction="long",
        summary=(
            "Use two 15m closes above the breakout trigger to establish a long bias; "
            "enter on 5m pullbacks toward the 21 EMA with trend alignment to the 200 EMA."
        ),
    ),
    "S0_short": StrategyDescriptor(
        id="S0",
        name="Trigger Pullback – Short",
        direction="short",
        summary=(
            "Use two 15m closes below the breakdown trigger to establish a short bias; "
            "enter on 5m pullbacks toward the 21 EMA with trend aligned below the 200 EMA."
        ),
    ),
    "S1_long": StrategyDescriptor(
        id="S1",
        name="Pocket Compression – Long",
        direction="long",
        summary=(
            "Longs from compression pockets between breakdown trigger and daily support, "
            "once downside fails and 15m reclaims the trigger."
        ),
    ),
    "S1_short": StrategyDescriptor(
        id="S1",
        name="Pocket Compression – Short",
        direction="short",
        summary=(
            "Shorts from compression pockets between breakout trigger and daily resistance, "
            "once upside fails and 15m loses the trigger."
        ),
    ),
    "S2_long": StrategyDescriptor(
        id="S2",
        name="Trigger Shadow – Long",
        direction="long",
        summary=(
            "Fade failed breakdowns where price sweeps below breakdown trigger and reclaims it; "
            "target a squeeze back toward breakout trigger / daily resistance."
        ),
    ),
    "S2_short": StrategyDescriptor(
        id="S2",
        name="Trigger Shadow – Short",
        direction="short",
        summary=(
            "Fade failed breakouts where price wicks above breakout trigger and falls back inside; "
            "target a rotation back toward breakdown trigger / daily support."
        ),
    ),
    "S3_long": StrategyDescriptor(
        id="S3",
        name="HTF Shelf Stepping – Long",
        direction="long",
        summary=(
            "Use stacked HTF demand shelves below daily support as step-in zones when trend is up "
            "but morning drives price into deeper support."
        ),
    ),
    "S3_short": StrategyDescriptor(
        id="S3",
        name="HTF Shelf Stepping – Short",
        direction="short",
        summary=(
            "Use stacked HTF supply shelves above daily resistance as step-in zones when trend is down "
            "but morning pushes into overhead supply."
        ),
    ),

    # Range / reversion structures
    "S4_neutral": StrategyDescriptor(
        id="S4",
        name="Mid-Band Fade",
        direction="neutral",
        summary=(
            "Fade pushes away from the daily mid-band back toward value when both triggers sit deep "
            "inside the daily range and HTF shelves are balanced."
        ),
    ),
    "S5_neutral": StrategyDescriptor(
        id="S5",
        name="Range Extremes",
        direction="neutral",
        summary=(
            "Fade touches of daily support/resistance in clean range conditions when triggers are wide "
            "and value remains centered."
        ),
    ),
    "S6_neutral": StrategyDescriptor(
        id="S6",
        name="Value Rotation",
        direction="neutral",
        summary=(
            "Trade rotations from VAH back to VAL (and vice versa) when 24h value is balanced and "
            "HTF shelves are not dominant."
        ),
    ),

    # HTF magnets / range-to-trigger
    "S7_long": StrategyDescriptor(
        id="S7",
        name="Range to Trigger – Long",
        direction="long",
        summary=(
            "Use intraday range edges and value to join a developing long trend toward the breakout "
            "trigger once shorts fail to push away from daily support."
        ),
    ),
    "S7_short": StrategyDescriptor(
        id="S7",
        name="Range to Trigger – Short",
        direction="short",
        summary=(
            "Use intraday range edges and value to join a developing short trend toward the breakdown "
            "trigger once longs fail to push away from daily resistance."
        ),
    ),
    "S8_long": StrategyDescriptor(
        id="S8",
        name="HTF Magnet – Long",
        direction="long",
        summary=(
            "Treat a strong HTF demand shelf as a magnet and look for 5m confirmation signals when "
            "price approaches from above in an otherwise bullish environment."
        ),
    ),
    "S8_short": StrategyDescriptor(
        id="S8",
        name="HTF Magnet – Short",
        direction="short",
        summary=(
            "Treat a strong HTF supply shelf as a magnet and look for 5m confirmation signals when "
            "price approaches from below in an otherwise bearish environment."
        ),
    ),
}


# ----------------------------------------------------------------------
# Helper utilities
# ----------------------------------------------------------------------


def _safe_get_float(d: Dict[str, Any], key: str) -> float:
    try:
        return float(d.get(key, 0.0))
    except Exception:
        return 0.0


def _pick_regime(
    levels: Dict[str, float],
    bias_label: str,
) -> Tuple[str, Side]:
    """
    Rough regime classification based purely on anchors:

      - "trend" when bias is strongly directional and triggers are reasonably wide
      - "range" when triggers are very wide and value is expected to rotate
      - "pre-breakout" when triggers are compressed

    For now we don't have direct band_width here, so we use distances
    between daily S/R and triggers as a proxy.
    """
    ds = _safe_get_float(levels, "daily_support")
    dr = _safe_get_float(levels, "daily_resistance")
    bt = _safe_get_float(levels, "breakout_trigger")
    bd = _safe_get_float(levels, "breakdown_trigger")

    band_width = max(dr - ds, 1e-6)
    trigger_span = max(bt - bd, 0.0)
    span_ratio = trigger_span / band_width if band_width > 0 else 0.0

    if span_ratio < 0.35:
        regime = "pre-breakout"
    elif span_ratio > 0.7:
        regime = "range"
    else:
        regime = "trend"

    if bias_label == "bullish":
        primary_side: Side = "long"
    elif bias_label == "bearish":
        primary_side = "short"
    else:
        primary_side = "neutral"

    return regime, primary_side


def _pick_strategy_ids(regime: str, primary_side: Side) -> Tuple[List[str], List[str]]:
    """
    Map regime + side into primary/secondary strategy sets (ids inside STRATEGIES).
    """
    primary: List[str] = []
    secondary: List[str] = []

    if regime == "trend":
        if primary_side == "long":
            primary = ["S0_long", "S7_long"]
            secondary = ["S1_long", "S2_long", "S3_long", "S8_long"]
        elif primary_side == "short":
            primary = ["S0_short", "S7_short"]
            secondary = ["S1_short", "S2_short", "S3_short", "S8_short"]
        else:
            # neutral bias in trend regime → stay focused on range tactics
            primary = ["S4_neutral"]
            secondary = ["S5_neutral", "S6_neutral"]
    elif regime == "range":
        primary = ["S5_neutral"]
        secondary = ["S4_neutral", "S6_neutral"]
    else:  # "pre-breakout"
        if primary_side == "long":
            primary = ["S7_long", "S0_long"]
            secondary = ["S1_long", "S2_long", "S5_neutral"]
        elif primary_side == "short":
            primary = ["S7_short", "S0_short"]
            secondary = ["S1_short", "S2_short", "S5_neutral"]
        else:
            primary = ["S4_neutral"]
            secondary = ["S5_neutral", "S6_neutral"]

    # filter out any ids that aren't in STRATEGIES (defensive)
    primary = [sid for sid in primary if sid in STRATEGIES]
    secondary = [sid for sid in secondary if sid in STRATEGIES]

    return primary, secondary


def _build_targets_hint(
    levels: Dict[str, float],
    htf_shelves: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Approximate target ladders based on Execution Anchor rules.

    - Longs: daily_resistance + up to 2 HTF shelves above/below, plus
      up to 3 intraday shelves on breakout side (if present).
    - Shorts: daily_support + same idea on downside.
    """
    ds = _safe_get_float(levels, "daily_support")
    dr = _safe_get_float(levels, "daily_resistance")

    htf_shelves = htf_shelves or {}
    res_shelves = htf_shelves.get("resistance") or []
    sup_shelves = htf_shelves.get("support") or []

    def pick_levels(entries: List[Dict[str, Any]], base: float) -> List[float]:
        lvls: List[float] = []
        for e in entries:
            try:
                lvls.append(float(e.get("level")))
            except Exception:
                continue
        # Keep the 2 closest to base
        lvls = sorted(lvls, key=lambda x: abs(x - base))
        return lvls[:2]

    long_hint = None
    short_hint = None

    if dr > 0.0:
        long_hint = {
            "primary_htf": dr,
            "htf_extensions": pick_levels(res_shelves, dr),
            # intraday shelves not wired yet → empty until backend exposes them
            "intraday_steps": [],
        }

    if ds > 0.0:
        short_hint = {
            "primary_htf": ds,
            "htf_extensions": pick_levels(sup_shelves, ds),
            "intraday_steps": [],
        }

    return {"long": long_hint, "short": short_hint}


def _build_outlook_text(
    symbol: str,
    regime: str,
    primary_side: Side,
    primary_strats: List[StrategyDescriptor],
    secondary_strats: List[StrategyDescriptor],
) -> str:
    """
    Human-readable text block for DMR Section 4, describing
    the *idea* of the day using the strategy labels.
    """
    lines: List[str] = ["4) Trade Strategy Outlook"]

    if primary_side == "long":
        bias_sentence = (
            f"- Primary idea: favor long setups in {symbol}, "
            f"treating dips as opportunities to join strength as long as the "
            f"structural anchors remain intact."
        )
    elif primary_side == "short":
        bias_sentence = (
            f"- Primary idea: favor short setups in {symbol}, "
            f"selling rips back into resistance while the downside structure remains valid."
        )
    else:
        bias_sentence = (
            f"- Primary idea: treat {symbol} as a rotation / range environment until "
            "a clear break of the triggers forces a new trend."
        )

    lines.append(bias_sentence)

    # Regime commentary
    if regime == "trend":
        lines.append(
            "- Regime: trend – expect directional follow-through once triggers confirm; "
            "be careful fading strength/weakness."
        )
    elif regime == "range":
        lines.append(
            "- Regime: range – expect repeated tests of daily support/resistance and value edges; "
            "mean-reversion tactics take priority over chasing breakouts."
        )
    else:
        lines.append(
            "- Regime: pre-breakout – triggers are compressed; expect fake-outs and liquidity hunts "
            "around both sides before a sustained move."
        )

        # Strategy lists
        if primary_strats:
        # Show only human-readable strategy names (hide S0/S1/etc.)
            primary_labels = ", ".join(s.name for s in primary_strats)
            lines.append(f"- Primary playbook: {primary_labels}.")

        if secondary_strats:
            secondary_labels = ", ".join(s.name for s in secondary_strats)
            lines.append(f"- Secondary / tactical plays: {secondary_labels}.")


    lines.append(
        "- Remember: all entries still require the intraday checklist "
        "(15m trigger confirmation + 5m trend alignment + oscillator reset)."
    )

    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# Public entrypoint
# ----------------------------------------------------------------------


def build_trade_logic_summary(
    symbol: str,
    levels: Dict[str, float],
    bias_label: str,
    htf_shelves: Optional[Dict[str, Any]] = None,
    range_30m: Optional[Dict[str, float]] = None,  # reserved for future refinements
) -> Dict[str, Any]:
    """
    Main function used by dmr_report.generate_dmr_report.

    It does NOT look at candles directly; it only consumes the structural
    levels, the pre-session bias, and (optionally) the SSE shelf ladders.
    """
    regime, primary_side = _pick_regime(levels, bias_label)
    primary_ids, secondary_ids = _pick_strategy_ids(regime, primary_side)

    primary_strats = [STRATEGIES[sid] for sid in primary_ids]
    secondary_strats = [STRATEGIES[sid] for sid in secondary_ids]

    targets_hint = _build_targets_hint(levels, htf_shelves)
    outlook_text = _build_outlook_text(
        symbol=symbol,
        regime=regime,
        primary_side=primary_side,
        primary_strats=primary_strats,
        secondary_strats=secondary_strats,
    )

    return {
        "analysis_bias": primary_side,
        "regime": regime,
        "primary_side": primary_side,
        "primary_strategies": [
            {
                "id": s.id,
                "name": s.name,
                "direction": s.direction,
                "summary": s.summary,
            }
            for s in primary_strats
        ],
        "secondary_strategies": [
            {
                "id": s.id,
                "name": s.name,
                "direction": s.direction,
                "summary": s.summary,
            }
            for s in secondary_strats
        ],
        "targets_hint": targets_hint,
        "outlook_text": outlook_text,
    }
