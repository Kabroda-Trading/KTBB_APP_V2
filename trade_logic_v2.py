"""
KTBB Trade Logic Module v2.1 (hardened + backward-compatible)

- Consumes structural levels from Execution Anchor (SSE); does NOT invent levels.
- Produces a deterministic "plan" summary for the Daily Market Review (DMR).
- Not a live signal engine; intraday confirmations are described as requirements.

This hardened version fixes:
- Missing 'symbol' crashes by deriving symbol from inputs or defaulting safely.
- Missing 'bias_label' by deriving from inputs or falling back to "neutral".
- Indentation / formatting issues in outlook text builder.
- Backward compatibility for older call sites.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple


Side = Literal["long", "short", "neutral"]


@dataclass(frozen=True)
class StrategyDescriptor:
    id: str
    name: str
    direction: Side
    summary: str


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
            "Trade rotations from VAH back to VAL (and vice versa) when value is balanced and "
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
# Helpers
# ----------------------------------------------------------------------

def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except Exception:
        return default


def _safe_get_float(d: Dict[str, Any], key: str) -> float:
    return _safe_float((d or {}).get(key, 0.0), 0.0)


def _normalize_symbol(symbol: Optional[str], inputs: Optional[Dict[str, Any]]) -> str:
    if symbol and str(symbol).strip():
        return str(symbol).strip().upper()
    if inputs:
        s = inputs.get("symbol") or inputs.get("ticker") or inputs.get("market") or ""
        if str(s).strip():
            return str(s).strip().upper()
    return "BTC"


def _normalize_bias_label(bias_label: Optional[str], inputs: Optional[Dict[str, Any]]) -> str:
    if bias_label and str(bias_label).strip():
        b = str(bias_label).strip().lower()
        if b in ("bullish", "bearish", "neutral"):
            return b
    if inputs:
        b = (inputs.get("bias_label") or inputs.get("bias") or inputs.get("analysis_bias") or "").strip().lower()
        if b in ("bullish", "bearish", "neutral"):
            return b
    return "neutral"


def _pick_regime(levels: Dict[str, float], bias_label: str) -> Tuple[str, Side]:
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
            primary = ["S4_neutral"]
            secondary = ["S5_neutral", "S6_neutral"]

    elif regime == "range":
        primary = ["S5_neutral"]
        secondary = ["S4_neutral", "S6_neutral"]

    else:  # pre-breakout
        if primary_side == "long":
            primary = ["S7_long", "S0_long"]
            secondary = ["S1_long", "S2_long", "S5_neutral"]
        elif primary_side == "short":
            primary = ["S7_short", "S0_short"]
            secondary = ["S1_short", "S2_short", "S5_neutral"]
        else:
            primary = ["S4_neutral"]
            secondary = ["S5_neutral", "S6_neutral"]

    primary = [sid for sid in primary if sid in STRATEGIES]
    secondary = [sid for sid in secondary if sid in STRATEGIES]
    return primary, secondary


def _build_targets_hint(levels: Dict[str, float], htf_shelves: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ds = _safe_get_float(levels, "daily_support")
    dr = _safe_get_float(levels, "daily_resistance")

    htf_shelves = htf_shelves or {}
    res_shelves = htf_shelves.get("resistance") or []
    sup_shelves = htf_shelves.get("support") or []

    def _pick_closest(entries: List[Dict[str, Any]], base: float) -> List[float]:
        lvls: List[float] = []
        for e in entries:
            lvl = _safe_float((e or {}).get("level"), None)
            if lvl is not None:
                lvls.append(lvl)
        lvls = sorted(lvls, key=lambda x: abs(x - base))
        return lvls[:2]

    long_hint = None
    short_hint = None

    if dr > 0:
        long_hint = {
            "primary_htf": dr,
            "htf_extensions": _pick_closest(res_shelves, dr),
            "intraday_steps": [],
        }

    if ds > 0:
        short_hint = {
            "primary_htf": ds,
            "htf_extensions": _pick_closest(sup_shelves, ds),
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
    lines: List[str] = ["4) Trade Strategy Outlook"]

    if primary_side == "long":
        lines.append(
            f"- Primary idea: favor long setups in {symbol}. If price confirms above the breakout trigger, "
            "treat pullbacks as opportunities while structural support holds."
        )
    elif primary_side == "short":
        lines.append(
            f"- Primary idea: favor short setups in {symbol}. If price confirms below the breakdown trigger, "
            "sell pullbacks into resistance while downside structure holds."
        )
    else:
        lines.append(
            f"- Primary idea: treat {symbol} as rotational until a clean trigger break creates directional flow."
        )

    if regime == "trend":
        lines.append("- Regime: trend – prioritize trigger-based continuation; avoid fading strong momentum.")
    elif regime == "range":
        lines.append("- Regime: range – prioritize mean-reversion at edges; avoid chasing breakouts.")
    else:
        lines.append("- Regime: pre-breakout – triggers are compressed; expect fake-outs before commitment.")

    if primary_strats:
        primary_labels = ", ".join(s.name for s in primary_strats)
        lines.append(f"- Primary playbook: {primary_labels}.")

    if secondary_strats:
        secondary_labels = ", ".join(s.name for s in secondary_strats)
        lines.append(f"- Secondary / tactical plays: {secondary_labels}.")

    lines.append("- Execution note: entries still require intraday confirmation (15m trigger + 5m alignment).")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# Public entrypoint (backward-compatible)
# ----------------------------------------------------------------------

def build_trade_logic_summary(
    symbol: Optional[str] = None,
    levels: Optional[Dict[str, Any]] = None,
    bias_label: Optional[str] = None,
    htf_shelves: Optional[Dict[str, Any]] = None,
    range_30m: Optional[Dict[str, Any]] = None,
    inputs: Optional[Dict[str, Any]] = None,
    **_ignored: Any,
) -> Dict[str, Any]:
    """
    Backward compatible:
    - Old: build_trade_logic_summary(symbol, levels, bias_label, htf_shelves, range_30m)
    - New: build_trade_logic_summary(levels=..., range_30m=..., htf_shelves=..., inputs=...)
    """
    symbol_n = _normalize_symbol(symbol, inputs)
    bias_n = _normalize_bias_label(bias_label, inputs)
    levels_n = (levels or {})  # do not mutate
    htf_n = htf_shelves or {}
    _ = range_30m  # reserved for future refinements

    regime, primary_side = _pick_regime(levels_n, bias_n)
    primary_ids, secondary_ids = _pick_strategy_ids(regime, primary_side)

    primary_strats = [STRATEGIES[sid] for sid in primary_ids]
    secondary_strats = [STRATEGIES[sid] for sid in secondary_ids]

    targets_hint = _build_targets_hint(levels_n, htf_n)
    outlook_text = _build_outlook_text(
        symbol=symbol_n,
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
            {"id": s.id, "name": s.name, "direction": s.direction, "summary": s.summary}
            for s in primary_strats
        ],
        "secondary_strategies": [
            {"id": s.id, "name": s.name, "direction": s.direction, "summary": s.summary}
            for s in secondary_strats
        ],
        "targets_hint": targets_hint,
        "outlook_text": outlook_text,
    }
# -------------------------------------------------------------------
# Public API shim (stable function name expected by dmr_report.py)
# -------------------------------------------------------------------

def compute_trade_logic(*, symbol: str = "BTCUSDT", inputs: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    """
    Stable entrypoint expected by dmr_report.py.
    Delegates to the module's canonical public function (build_trade_logic_summary).
    Does NOT change any trade logic internals.
    """
    inputs = inputs or {}

    return build_trade_logic_summary(
        symbol=symbol,
        inputs=inputs,
        levels=(inputs.get("levels") or {}),
        htf_shelves=(inputs.get("htf_shelves") or {}),
        range_30m=(inputs.get("range_30m") or inputs.get("range30m") or {}),
        bias_label=(inputs.get("bias_label") or inputs.get("bias") or inputs.get("analysis_bias")),
        **kwargs,
    )

