# dmr_report.py
from __future__ import annotations
from typing import Any, Dict
from datetime import datetime
import trade_logic_v2
import sse_engine  # <--- THE MISSING LINK

def _extract_sse_inputs(daily_candles, intraday_candles, current_price):
    """
    Bridge function: Maps Raw Candles -> SSE Engine Inputs.
    Since we don't have a separate H4 scanner running, we map 
    Daily High/Low to Structural Supply/Demand to feed the engine valid data.
    """
    # 1. Get Previous Day (for Macro Structure)
    prev_day = daily_candles[-2] if len(daily_candles) > 1 else {}
    
    # 2. Get Last Completed 30m Candle (for Session Anchor)
    # This represents the "Initial Balance" or current session anchor
    last_30m = intraday_candles[-2] if len(intraday_candles) > 1 else {}

    return {
        "last_price": current_price,
        "session_open_price": last_30m.get("open", 0.0),
        
        # Session Range Inputs
        "r30_high": last_30m.get("high", 0.0),
        "r30_low": last_30m.get("low", 0.0),
        
        # Structural Inputs (Mapped from Daily to satisfy SSE requirements)
        # The SSE Engine will use these to determine Daily Support/Resistance
        "h4_supply": prev_day.get("high", 0.0),
        "h4_demand": prev_day.get("low", 0.0),
        
        # Optional: Can add H1 proxies here if available, else 0
        "h1_supply": 0.0,
        "h1_demand": 0.0,
        
        # Value Area Proxies (Optional fallback)
        "f24_vah": prev_day.get("high", 0.0),
        "f24_val": prev_day.get("low", 0.0),
    }

def generate_report_from_inputs(inputs: Dict[str, Any], session_tz: str = "UTC") -> Dict[str, Any]:
    """
    1. Receives Raw Data (Async Fetch)
    2. Prepares Data for SSE Engine
    3. Runs SSE Engine (Ferrari Mode)
    4. Runs Trade Logic
    """
    symbol = inputs.get("symbol", "BTCUSDT")
    daily_data = inputs.get("daily_candles", [])
    intraday_data = inputs.get("intraday_candles", [])
    current_price = inputs.get("current_price") or inputs.get("last_price") or 0.0
    
    # --- STEP 1: PREPARE DATA FOR SSE ---
    sse_input_data = _extract_sse_inputs(daily_data, intraday_data, current_price)
    
    # --- STEP 2: RUN THE SSE ENGINE ---
    # This replaces the simple math with your advanced logic
    computed_data = sse_engine.compute_sse_levels(sse_input_data)
    
    # --- STEP 3: HYDRATE INPUTS ---
    # Merge the sophisticated levels back into the main inputs object
    inputs["levels"] = computed_data["levels"]
    inputs["htf_shelves"] = computed_data["htf_shelves"]
    inputs["intraday_shelves"] = computed_data["intraday_shelves"]
    
    # Ensure range_30m exists for frontend display
    inputs["range_30m"] = {
        "high": computed_data["levels"].get("range30m_high", 0.0),
        "low": computed_data["levels"].get("range30m_low", 0.0)
    }

    # --- STEP 4: RUN TRADE LOGIC ---
    # Now Trade Logic has the high-quality SSE levels to work with
    trade_logic = trade_logic_v2.compute_trade_logic(symbol=symbol, inputs=inputs)

    # --- STEP 5: OUTPUT ---
    out: Dict[str, Any] = {
        "symbol": symbol,
        "date": inputs.get("date") or datetime.now().strftime("%Y-%m-%d"),
        "last_price": current_price,
        "session_tz": session_tz,
        
        # High-Quality Data from SSE
        "levels": computed_data["levels"],
        "range_30m": inputs["range_30m"],
        "trade_logic": trade_logic,
        
        # Passthroughs
        "inputs": inputs,
        "htf_shelves": inputs["htf_shelves"],
        "intraday_shelves": inputs["intraday_shelves"],
        "news": inputs.get("news", []),
        "events": inputs.get("events", [])
    }
    return out

# Backward compatibility alias
run_auto_raw = generate_report_from_inputs