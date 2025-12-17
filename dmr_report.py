from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import sse_engine
import data_feed
from trade_logic_v2 import build_trade_logic_summary

def build_execution_rules() -> Dict[str, Any]:
    return {
        "confirm_rule": "Two consecutive 15m closes beyond trigger",
        "entry_timing": "After confirmation, require 5m alignment for entry timing",
        "hard_exit": "Hard exit on 5m close through 21 SMA (directional)",
    }

def build_momentum_summary(raw: Dict[str, Any]) -> Dict[str, str]:
    # Lightweight, deterministic “momentum” so AI can speak concretely
    levels = raw.get("levels") or {}
    px = (raw.get("inputs") or {}).get("last_price")
    bo = levels.get("breakout_trigger")
    bd = levels.get("breakdown_trigger")

    def label() -> str:
        try:
            if px is None or bo is None or bd is None:
                return "unknown"
            if px > bo:
                return "bullish (above breakout trigger)"
            if px < bd:
                return "bearish (below breakdown trigger)"
            return "neutral (inside trigger band)"
        except Exception:
            return "unknown"

    m = label()
    return {
        "4H": m,
        "1H": m,
        "15M": m,
        "5M": m,
    }

def build_tf_facts(raw: Dict[str, Any]) -> Dict[str, Any]:
    inp = raw.get("inputs") or {}
    return {
        "weekly_vp": {"vah": inp.get("weekly_vah"), "poc": inp.get("weekly_poc"), "val": inp.get("weekly_val")},
        "f24_vp": {"vah": inp.get("f24_vah"), "poc": inp.get("f24_poc"), "val": inp.get("f24_val")},
        "morning_vp": {"vah": inp.get("morn_vah"), "poc": inp.get("morn_poc"), "val": inp.get("morn_val")},
    }

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
        # Add the AI “facts pack”
    payload = {
        "symbol": symbol,
        "date": inputs.get("date") or _today_utc_str(),
        "inputs": inputs,
        "levels": levels,
        "range_30m": inputs.get("range_30m") or {},
        "htf_shelves": htf_shelves,
        "intraday_shelves": intraday_shelves,
        "trade_logic": trade_logic,
    }

    payload["execution_rules"] = build_execution_rules()
    payload["momentum_summary"] = build_momentum_summary(payload)
    payload["tf_facts"] = build_tf_facts(payload)

    return payload


def run_auto_raw(symbol: str, session_tz: str) -> Dict[str, Any]:
    symbol = (symbol or "BTCUSDT").strip().upper()
    inputs = data_feed.build_auto_inputs(symbol=symbol, session_tz=session_tz)
    if not isinstance(inputs, dict):
        raise RuntimeError("data_feed.build_auto_inputs returned non-dict inputs")
    return compute_dmr(symbol=symbol, inputs=inputs)
