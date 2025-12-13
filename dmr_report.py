# dmr_report.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def _n(x: Any) -> Optional[float]:
    """Coerce to float if possible, else None."""
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _fmt(x: Optional[float]) -> str:
    if x is None:
        return "—"
    # 1 decimal is enough for crypto tape; keep consistent
    return f"{x:,.1f}"


def _pick(inputs: Dict[str, Any], *keys: str) -> Optional[float]:
    for k in keys:
        if k in inputs:
            v = _n(inputs.get(k))
            if v is not None:
                return v
    return None


def _build_htf_shelves(inputs: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Maps the common input keys we've been using into HTF shelves.
    We treat H4 as primary shelf by default.
    """
    h4_supply = _pick(inputs, "h4_supply", "H4_supply", "h4_resistance")
    h4_demand = _pick(inputs, "h4_demand", "H4_demand", "h4_support")
    h1_supply = _pick(inputs, "h1_supply", "H1_supply", "h1_resistance")
    h1_demand = _pick(inputs, "h1_demand", "H1_demand", "h1_support")

    resistance = []
    support = []

    if h1_supply is not None:
        resistance.append({"tf": "1H", "level": h1_supply, "strength": 6, "primary": False})
    if h4_supply is not None:
        resistance.append({"tf": "4H", "level": h4_supply, "strength": 8, "primary": True})

    if h4_demand is not None:
        support.append({"tf": "4H", "level": h4_demand, "strength": 8, "primary": True})
    if h1_demand is not None:
        support.append({"tf": "1H", "level": h1_demand, "strength": 6, "primary": False})

    return {"resistance": resistance, "support": support}


def _build_yaml_block(levels: Dict[str, Any], r30: Dict[str, Any]) -> str:
    return (
        "YAML Block (for TradingView / scripting):\n"
        "triggers:\n"
        f"  breakout: {levels.get('breakout_trigger', '—')}\n"
        f"  breakdown: {levels.get('breakdown_trigger', '—')}\n\n"
        f"daily_resistance: {levels.get('daily_resistance', '—')}\n"
        f"daily_support: {levels.get('daily_support', '—')}\n\n"
        "range_30m:\n"
        f"  high: {r30.get('high', '—')}\n"
        f"  low: {r30.get('low', '—')}\n"
    )


def _deterministic_narrative(symbol: str, date_str: str, levels: Dict[str, Any], r30: Dict[str, Any], htf: Dict[str, Any], inputs: Dict[str, Any]) -> str:
    """
    Tactical baseline narrative. Elite will replace this with AI content.
    Keep it clean and human-readable. YAML block is appended (hidden behind toggle in UI).
    """
    dr = _n(levels.get("daily_resistance"))
    ds = _n(levels.get("daily_support"))
    bo = _n(levels.get("breakout_trigger"))
    bd = _n(levels.get("breakdown_trigger"))
    rhi = _n(r30.get("high"))
    rlo = _n(r30.get("low"))

    width_pct = None
    if dr is not None and ds is not None and ds != 0:
        width_pct = abs((dr - ds) / ds) * 100.0

    # Light regime guess (deterministic)
    regime = "balanced rotation"
    if width_pct is not None and width_pct < 2.0:
        regime = "compression / coil"
    if width_pct is not None and width_pct > 5.0:
        regime = "wide rotation"

    lines = []
    lines.append("--- Daily Market Review ---")
    lines.append(f"Kabroda — Daily Market Review ({symbol}) — {date_str}")
    lines.append("")
    lines.append("1) Market Momentum Summary")
    lines.append(f"- Structure: daily support near {_fmt(ds)} and daily resistance near {_fmt(dr)}.")
    if width_pct is not None:
        lines.append(f"- Daily band width: ~{width_pct:.1f}% → {regime}.")
    else:
        lines.append(f"- Regime: {regime}.")
    lines.append("")
    lines.append("2) Key Levels")
    lines.append(f"- Daily Support: {_fmt(ds)}")
    lines.append(f"- Daily Resistance: {_fmt(dr)}")
    lines.append(f"- Breakout Trigger: {_fmt(bo)}")
    lines.append(f"- Breakdown Trigger: {_fmt(bd)}")
    lines.append(f"- 30m Opening Range: {_fmt(rlo)} → {_fmt(rhi)}")
    lines.append("")
    lines.append("3) Plan")
    lines.append("- Primary idea: treat price as rotation until a clean trigger break confirms expansion.")
    lines.append("- If breakout confirms: look for pullback + continuation setups (risk anchored to OR or invalidation).")
    lines.append("- If breakdown confirms: favor short-side continuation or mean-reversion failures back into value.")
    lines.append("")
    lines.append("4) Execution Notes")
    lines.append("- Keep risk anchored to the opposite side of the 30m Opening Range or the invalidation trigger.")
    lines.append("- Avoid over-trading chop between triggers; best R:R often appears on expansion away from the center zone.")
    lines.append("")
    lines.append(_build_yaml_block(levels, r30))
    return "\n".join(lines)


def compute_dmr(symbol: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic DMR compute.
    `inputs` is produced by data_feed.build_auto_inputs().
    """
    date_str = datetime.utcnow().strftime("%Y-%m-%d")

    # Pull the common inputs we’ve been using
    h4_supply = _pick(inputs, "h4_supply")
    h4_demand = _pick(inputs, "h4_demand")
    r30_high = _pick(inputs, "r30_high", "r30_hi", "range_30m_high", "opening_range_30_high")
    r30_low = _pick(inputs, "r30_low", "r30_lo", "range_30m_low", "opening_range_30_low")

    # Define the core levels
    levels = {
        "daily_resistance": h4_supply,
        "daily_support": h4_demand,
        # OR high/low act as triggers
        "breakout_trigger": r30_high,
        "breakdown_trigger": r30_low,
    }

    range_30m = {
        "high": r30_high,
        "low": r30_low,
    }

    htf_shelves = _build_htf_shelves(inputs)

    # NEW: compute strategy-aware KTBB summary (S0–S8 bridge)
    trade_logic = None
    try:
        from trade_logic_v2 import build_trade_logic_summary
        trade_logic = build_trade_logic_summary(
            symbol=symbol,
            inputs=inputs,
            levels=levels,
            range_30m=range_30m,
            htf_shelves=htf_shelves,
        )
    except Exception:
        # Keep deterministic system stable even if trade logic evolves
        trade_logic = None

    report_text = _deterministic_narrative(symbol, date_str, levels, range_30m, htf_shelves, inputs)

    return {
        "inputs": inputs,
        "levels": levels,
        "range_30m": range_30m,
        "htf_shelves": htf_shelves,
        "trade_logic": trade_logic,  # <— added
        # For backward compatibility with older UI code
        "report_text": report_text,
        "report": report_text,
        "date": date_str,
        "symbol": symbol,
    }

