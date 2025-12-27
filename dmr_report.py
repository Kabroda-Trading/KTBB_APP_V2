# dmr_report.py
from __future__ import annotations
from typing import Any, Dict
import data_feed
import trade_logic_v2

def run_auto_raw(symbol: str = "BTCUSDT", session_tz: str = "UTC") -> Dict[str, Any]:
    symbol = (symbol or "BTCUSDT").strip().upper()
    session_tz = (session_tz or "UTC").strip() or "UTC"

    inputs = data_feed.get_inputs(symbol=symbol, date=None, session_tz=session_tz)

    levels = inputs.get("levels") or {}
    range_30m = inputs.get("range_30m") or inputs.get("range30m") or {}
    htf_shelves = inputs.get("htf_shelves") or {}
    intraday_shelves = inputs.get("intraday_shelves") or {}

    trade_logic = trade_logic_v2.compute_trade_logic(symbol=symbol, inputs=inputs)

    out: Dict[str, Any] = {
        "symbol": symbol,
        "date": inputs.get("date") or "",
        "last_price": inputs.get("last_price"),
        "session_tz": session_tz,
        
        # DATA HOISTING FOR FRONTEND
        "inputs": inputs,
        "levels": levels,
        "range_30m": range_30m,
        "htf_shelves": htf_shelves,
        "intraday_shelves": intraday_shelves,
        "trade_logic": trade_logic,
        
        # NEWS HOISTING (Critical for GPT)
        "news": inputs.get("news", []),
        "events": inputs.get("events", [])
    }
    return out