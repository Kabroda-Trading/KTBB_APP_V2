# dmr_report.py
from __future__ import annotations
from typing import Any, Dict
import trade_logic_v2

# NOTE: We removed 'data_feed' import because we no longer fetch here. 
# The data is passed in directly from main.py.

def generate_report_from_inputs(inputs: Dict[str, Any], session_tz: str = "UTC") -> Dict[str, Any]:
    """
    Takes pre-fetched inputs from Main and runs the math.
    Replaces 'run_auto_raw' to support the new Async Engine.
    """
    # 1. Extract Data
    # The new async feed returns 'daily_candles' and 'intraday_candles'.
    # We map them to what the logic expects.
    
    symbol = inputs.get("symbol", "BTCUSDT") # Fallback if not mapped
    
    # 2. Run Trade Logic
    # We pass the entire inputs dict to the logic engine
    trade_logic = trade_logic_v2.compute_trade_logic(symbol=symbol, inputs=inputs)

    # 3. Extract Computed Levels (if trade_logic computed them) or existing ones
    levels = inputs.get("levels") or {}
    range_30m = inputs.get("range_30m") or inputs.get("range30m") or {}
    htf_shelves = inputs.get("htf_shelves") or {}
    intraday_shelves = inputs.get("intraday_shelves") or {}

    out: Dict[str, Any] = {
        "symbol": symbol,
        "date": inputs.get("date") or "",
        "last_price": inputs.get("last_price") or inputs.get("current_price"),
        "session_tz": session_tz,
        
        # DATA HOISTING FOR FRONTEND
        "inputs": inputs,
        "levels": levels,
        "range_30m": range_30m,
        "htf_shelves": htf_shelves,
        "intraday_shelves": intraday_shelves,
        "trade_logic": trade_logic,
        
        # NEWS HOISTING
        "news": inputs.get("news", []),
        "events": inputs.get("events", [])
    }
    return out

# Compatibility alias in case any legacy code calls the old name
run_auto_raw = generate_report_from_inputs