# dmr_report.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import data_feed  # your existing module
from trade_logic_v2 import build_trade_logic_summary  # your existing deterministic module


def _today_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def build_execution_rules() -> Dict[str, Any]:
    # Keep this deterministic + stable for the AI + UI
    return {
        "trigger_confirmation": "TWO consecutive 15m closes beyond breakout/breakdown trigger",
        "entry_timing": "After confirmation, require 5m alignment for entry timing",
        "hard_exit": "5m close through the 21 SMA (directional)",
    }


def build_momentum_summary(payload: Dict[str, Any]) -> Dict[str, str]:
    # Minimal deterministic summary (AI will expand using doctrine + tf_facts)
    # If you later add real momentum calc, do it here (still deterministic).
    return {
        "4H": "Neutral momentum observed with price action within a balanced range.",
        "1H": "Neutral momentum indicating decision in the market.",
        "15M": "Neutral momentum suggesting a lack of strong directional bias.",
        "5M": "Neutral momentum reflecting a tight trading range.",
    }


def build_tf_facts(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    A small “facts pack” for the model so it can talk like KTBB without inventing:
    - where price is relative to POC/VAH/VAL
    - key VP zones that exist in inputs
    """
    inputs = payload.get("inputs") or {}
    last_price = inputs.get("last_price")

    def _rel(px: Optional[float], lvl: Optional[float]) -> Optional[str]:
        if px is None or lvl is None:
            return None
        return "above" if px > lvl else "below" if px < lvl else "at"

    f24 = {
        "vah": inputs.get("f24_vah"),
        "val": inputs.get("f24_val"),
        "poc": inputs.get("f24_poc"),
    }
    morning = {
        "vah": inputs.get("morn_vah"),
        "val": inputs.get("morn_val"),
        "poc": inputs.get("morn_poc"),
    }
    weekly = {
        "vah": inputs.get("weekly_vah"),
        "val": inputs.get("weekly_val"),
        "poc": inputs.get("weekly_poc"),
    }

    return {
        "last_price": last_price,
        "f24": {
            **f24,
            "price_vs_vah": _rel(last_price, f24.get("vah")),
            "price_vs_val": _rel(last_price, f24.get("val")),
            "price_vs_poc": _rel(last_price, f24.get("poc")),
        },
        "morning": {
            **morning,
            "price_vs_vah": _rel(last_price, morning.get("vah")),
            "price_vs_val": _rel(last_price, morning.get("val")),
            "price_vs_poc": _rel(last_price, morning.get("poc")),
        },
        "weekly": {
            **weekly,
            "price_vs_vah": _rel(last_price, weekly.get("vah")),
            "price_vs_val": _rel(last_price, weekly.get("val")),
            "price_vs_poc": _rel(last_price, weekly.get("poc")),
        },
    }


def compute_dmr(symbol: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pure deterministic compute. DO NOT call OpenAI here.
    This is the locked “numbers pipeline” payload builder.
    """
    symbol = (symbol or "BTCUSDT").strip().upper()

    # Ensure required containers exist
    inputs = inputs if isinstance(inputs, dict) else {}
    inputs.setdefault("levels", {})
    inputs.setdefault("htf_shelves", {})
    inputs.setdefault("intraday_shelves", {})

    # If levels missing, try SSE engine (optional; never crash the app)
    levels = inputs.get("levels")
    if not isinstance(levels, dict) or not levels:
        try:
            import sse_engine  # type: ignore

            sse_out = sse_engine.compute_sse_levels(inputs)
            if isinstance(sse_out, dict):
                if isinstance(sse_out.get("levels"), dict):
                    inputs["levels"] = sse_out["levels"]
                if isinstance(sse_out.get("htf_shelves"), dict):
                    inputs["htf_shelves"] = sse_out["htf_shelves"]
                if isinstance(sse_out.get("intraday_shelves"), dict):
                    inputs["intraday_shelves"] = sse_out["intraday_shelves"]
        except Exception:
            # Scaffold only; no crash
            inputs.setdefault("levels", {})
            inputs.setdefault("htf_shelves", {})
            inputs.setdefault("intraday_shelves", {})

    levels = inputs.get("levels") or {}
    htf_shelves = inputs.get("htf_shelves") or {}
    intraday_shelves = inputs.get("intraday_shelves") or {}
    range_30m = inputs.get("range_30m") or {}

    trade_logic = build_trade_logic_summary(
        symbol=symbol,
        levels=levels,
        htf_shelves=htf_shelves,
        range_30m=range_30m,
        inputs=inputs,
    )

    payload: Dict[str, Any] = {
        "symbol": symbol,
        "date": inputs.get("date") or _today_utc_str(),
        "inputs": inputs,
        "levels": levels,
        "range_30m": range_30m,
        "htf_shelves": htf_shelves,
        "intraday_shelves": intraday_shelves,
        "trade_logic": trade_logic,
    }

    # “Facts pack” for Kabroda AI
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


def run_auto_ai(symbol: str, user_timezone: Optional[str] = None) -> Dict[str, Any]:
    """
    Back-compat entrypoint: main.py can call this if needed.
    Still deterministic (no OpenAI).
    """
    tz = (user_timezone or "UTC").strip() or "UTC"
    return run_auto_raw(symbol=symbol, session_tz=tz)
