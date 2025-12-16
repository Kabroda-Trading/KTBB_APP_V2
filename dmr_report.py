# dmr_report.py — DMR generator entrypoint + deterministic payload
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from data_feed import build_auto_inputs
from sse_engine import compute_sse_levels
from trade_logic_v2 import build_trade_logic_summary


def _today_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if s in ("BTC", "BTCUSDT"):
        return "BTCUSDT"
    return s or "BTCUSDT"


def run_auto_ai(symbol: str, user_timezone: Optional[str] = None) -> Dict[str, Any]:
    """
    Public entrypoint called by main.py.
    Produces a deterministic payload (and leaves AI narrative generation to main.py).
    """
    market = _normalize_symbol(symbol)
    tz = (user_timezone or "UTC").strip() or "UTC"

    # 1) Pull market inputs (price + 30m range + h1/h4 anchors, etc.)
    inputs = build_auto_inputs(symbol=market, session_tz=tz)

    # 2) SSE expects r30_high/r30_low, but data_feed provides range30m_high/low
    inputs["r30_high"] = inputs.get("range30m_high")
    inputs["r30_low"] = inputs.get("range30m_low")

    # 3) Compute deterministic SSE levels/shelves
    sse = compute_sse_levels(inputs)

    levels = sse.get("levels") or {}
    htf_shelves = sse.get("htf_shelves") or {}
    intraday_shelves = sse.get("intraday_shelves") or sse.get("intraday") or {}

    # Ensure these are available for trade logic
    inputs["levels"] = levels
    inputs["htf_shelves"] = htf_shelves
    inputs["intraday_shelves"] = intraday_shelves

    # 4) Trade logic outlook (deterministic)
    trade_logic = build_trade_logic_summary(
        symbol=market,
        levels=levels,
        range_30m=inputs.get("range_30m") or {"high": inputs.get("range30m_high"), "low": inputs.get("range30m_low")},
        htf_shelves=htf_shelves,
        inputs=inputs,
    )

    # 5) Flat payload expected by app.html renderDMR()
    payload: Dict[str, Any] = {
        "symbol": market,
        "date": inputs.get("date") or _today_utc_str(),
        "session_tz": tz,
        "levels": levels,
        "range_30m": inputs.get("range_30m") or {"high": inputs.get("range30m_high"), "low": inputs.get("range30m_low")},
        "htf_shelves": htf_shelves,
        "intraday_shelves": intraday_shelves,
        "trade_logic": trade_logic,
        # report_text optionally added in main.py if OPENAI_API_KEY exists
    }
    return payload
