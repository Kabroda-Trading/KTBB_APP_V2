# dmr_report.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sse_engine import compute_sse_levels
from trade_logic_v2 import build_trade_logic_summary


def _fmt(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{float(x):,.1f}"


def _yaml_block(d: Dict[str, Any]) -> str:
    import yaml  # pyyaml should already exist; if not, replace with manual yaml string builder
    return "```yaml\n" + yaml.safe_dump(d, sort_keys=False).strip() + "\n```"


def compute_dmr(symbol: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    date_str = datetime.utcnow().strftime("%Y-%m-%d")

    sse = compute_sse_levels(inputs)
    levels = sse["levels"]
    htf_shelves = sse.get("htf_shelves") or {}
    intraday_shelves = sse.get("intraday_shelves") or {}

    # Bias label (simple, deterministic): price vs weekly POC
    px = inputs.get("last_price")
    weekly_poc = inputs.get("weekly_poc")
    bias_label = "neutral"
    try:
        if px is not None and weekly_poc is not None:
            if float(px) > float(weekly_poc):
                bias_label = "bullish"
            elif float(px) < float(weekly_poc):
                bias_label = "bearish"
    except Exception:
        bias_label = "neutral"

    # Trade logic summary (Section 4 “outlook_text” comes from here)
    trade_logic = build_trade_logic_summary(
        levels=levels,
        bias_label=bias_label,
        htf_shelves=htf_shelves,
        range_30m={"high": inputs.get("r30_high"), "low": inputs.get("r30_low")},
    )

    # Deterministic fallback narrative (AI can overwrite this via /run-auto-ai)
    report = []
    report.append(f"Daily Market Review")
    report.append(f"{symbol} • {date_str}")
    report.append("")
    report.append("1) Market Momentum Summary")
    report.append(f"- 4H: Watching primary HTF shelves; daily resistance {_fmt(levels['daily_resistance'])} / support {_fmt(levels['daily_support'])}.")
    report.append(f"- 1H: Respect shelves into triggers; breakout {_fmt(levels['breakout_trigger'])} / breakdown {_fmt(levels['breakdown_trigger'])}.")
    report.append(f"- 15M: Two-close confirmation required at triggers; avoid chop between them.")
    report.append(f"- 5M: Execution filter lives on pullbacks after 15M confirmation; hard-exit rule applies.")
    report.append("")
    report.append("2) Sentiment Snapshot")
    report.append(f"Bias: {bias_label}. Compression between triggers implies potential expansion once confirmed.")
    report.append("")
    report.append("3) Key Support & Resistance")
    report.append(
        f"Daily Support {_fmt(levels['daily_support'])} < Breakdown {_fmt(levels['breakdown_trigger'])} < "
        f"Breakout {_fmt(levels['breakout_trigger'])} < Daily Resistance {_fmt(levels['daily_resistance'])}."
    )
    report.append("")
    report.append("4) Trade Strategy Outlook")
    report.append(trade_logic.get("outlook_text", "(none)"))
    report.append("")
    report.append("5) News-Based Risk Alert")
    report.append("No scheduled news injected today.")
    report.append("")
    report.append("6) Execution Considerations")
    report.append("Anchor risk to OR invalidation + the opposite trigger; avoid overtrading inside the trigger box.")
    report.append("")
    report.append("7) Weekly Zone Reference")
    report.append(
        f"Weekly VAL {_fmt(inputs.get('weekly_val'))} • POC {_fmt(inputs.get('weekly_poc'))} • VAH {_fmt(inputs.get('weekly_vah'))}"
    )
    report.append("")
    report.append("8) YAML Key Level Output Block")
    report.append(_yaml_block({
        "symbol": symbol,
        "date": date_str,
        "levels": {
            "daily_resistance": levels["daily_resistance"],
            "daily_support": levels["daily_support"],
            "breakout_trigger": levels["breakout_trigger"],
            "breakdown_trigger": levels["breakdown_trigger"],
            "range30m_high": levels["range30m_high"],
            "range30m_low": levels["range30m_low"],
        },
        "range_30m": {"high": inputs.get("r30_high"), "low": inputs.get("r30_low")},
        "htf_shelves": htf_shelves,
        "intraday_shelves": intraday_shelves,
        "weekly": {
            "val": inputs.get("weekly_val"),
            "poc": inputs.get("weekly_poc"),
            "vah": inputs.get("weekly_vah"),
        },
    }))

    return {
        "symbol": symbol,
        "date": date_str,
        "inputs": inputs,
        "bias_label": bias_label,
        "levels": levels,
        "range_30m": {"high": inputs.get("r30_high"), "low": inputs.get("r30_low")},
        "htf_shelves": htf_shelves,
        "intraday_shelves": intraday_shelves,
        "trade_logic": trade_logic,
        "report_text": "\n".join(report),
        "report": "\n".join(report),
    }
