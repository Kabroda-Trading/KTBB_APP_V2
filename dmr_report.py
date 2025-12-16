# dmr_report.py — CLEAN, DETERMINISTIC DMR PAYLOAD (+ run_auto_ai entrypoint)
from __future__ import annotations

from typing import Dict, Any, Optional
from datetime import datetime, timezone

from trade_logic_v2 import build_trade_logic_summary


def _today_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def compute_dmr(symbol: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic payload builder. DOES NOT call OpenAI.
    Expects `inputs` to already include:
      - levels
      - range_30m
      - htf_shelves
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")

    levels = inputs.get("levels") or {}
    range_30m = inputs.get("range_30m") or {}
    htf_shelves = inputs.get("htf_shelves") or {}

    trade_logic = build_trade_logic_summary(
        symbol=symbol,
        levels=levels,
        range_30m=range_30m,
        htf_shelves=htf_shelves,
        inputs=inputs,
    )

    payload = {
        "symbol": symbol,
        "date": inputs.get("date") or _today_utc_str(),
        "inputs": inputs,
        "levels": levels,
        "range_30m": range_30m,
        "htf_shelves": htf_shelves,
        "trade_logic": trade_logic,
        # report_text is appended by main.py (OpenAI) so compute remains pure/deterministic.
    }
    return payload


def run_auto_ai(symbol: str, user_timezone: Optional[str] = None) -> Dict[str, Any]:
    """
    Convenience entrypoint so main.py can call dmr_report.run_auto_ai().
    Pulls fresh inputs from your existing pipeline (data_feed) then computes payload.
    """
    symbol = (symbol or "BTCUSDT").strip().upper()

    import data_feed  # your pipeline

    inputs = None

    # Try common pipeline function names without forcing a refactor.
    for fn_name in ("build_auto_inputs", "get_auto_inputs", "auto_inputs", "build_inputs"):
        if hasattr(data_feed, fn_name):
            fn = getattr(data_feed, fn_name)
            try:
                inputs = fn(symbol=symbol, user_timezone=user_timezone)  # type: ignore
            except TypeError:
                # some versions don't accept tz
                inputs = fn(symbol=symbol)  # type: ignore
            break

    if inputs is None:
        raise RuntimeError("data_feed.py missing an auto-input builder (build_auto_inputs/get_auto_inputs/etc.)")

    if not isinstance(inputs, dict):
        raise RuntimeError("data_feed auto-input builder returned non-dict inputs")

    return compute_dmr(symbol=symbol, inputs=inputs)
