from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import sse_engine
import data_feed
from trade_logic_v2 import build_trade_logic_summary


def _today_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def compute_dmr(symbol: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")

    # Ensure OR keys exist for SSE expectations
    if inputs.get("r30_high") is None and inputs.get("range30m_high") is not None:
        inputs["r30_high"] = inputs["range30m_high"]
    if inputs.get("r30_low") is None and inputs.get("range30m_low") is not None:
        inputs["r30_low"] = inputs["range30m_low"]

    if not isinstance(inputs.get("range_30m"), dict):
        inputs["range_30m"] = {"high": inputs.get("r30_high"), "low": inputs.get("r30_low")}

    # SSE levels
    sse_out = sse_engine.compute_sse_levels(inputs)
    levels = sse_out.get("levels") or {}
    htf_shelves = sse_out.get("htf_shelves") or {}
    intraday_shelves = sse_out.get("intraday_shelves") or {}

    inputs["levels"] = levels
    inputs["htf_shelves"] = htf_shelves
    inputs["intraday_shelves"] = intraday_shelves

    # Trade logic summary (deterministic)
    trade_logic = build_trade_logic_summary(
        symbol=symbol,
        levels=levels,
        htf_shelves=htf_shelves,
        range_30m=inputs.get("range_30m"),
        inputs=inputs,
    )

    return {
        "symbol": symbol,
        "date": inputs.get("date") or _today_utc_str(),
        "inputs": inputs,
        "levels": levels,
        "range_30m": inputs.get("range_30m") or {},
        "htf_shelves": htf_shelves,
        "intraday_shelves": intraday_shelves,
        "trade_logic": trade_logic,
    }


def run_auto_raw(symbol: str, session_tz: str) -> Dict[str, Any]:
    symbol = (symbol or "BTCUSDT").strip().upper()
    inputs = data_feed.build_auto_inputs(symbol=symbol, session_tz=session_tz)
    if not isinstance(inputs, dict):
        raise RuntimeError("data_feed.build_auto_inputs returned non-dict inputs")
    return compute_dmr(symbol=symbol, inputs=inputs)
