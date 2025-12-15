# data_feed.py
from __future__ import annotations

from typing import Dict, Any, Optional
from datetime import datetime, timezone
import re

from sse_engine import compute_sse_levels


def resolve_symbol(symbol: str) -> str:
    """
    Frontend uses BTC; internal uses BTCUSDT.
    Also allow user to pass BTCUSDT directly.
    """
    s = (symbol or "").strip().upper()
    if not s:
        return "BTCUSDT"
    if s.endswith("USDT"):
        return s
    if s in ("BTC", "XBT"):
        return "BTCUSDT"
    # simple default: append USDT for single tickers
    if re.fullmatch(r"[A-Z0-9]{2,10}", s):
        return f"{s}USDT"
    return "BTCUSDT"


def _today_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# -------------------------------------------------------------------
# NOTE:
# I am preserving your existing “raw input builder” shape, but the
# important change is: we *must* attach SSE outputs to inputs.
# -------------------------------------------------------------------

def build_auto_inputs(symbol: str = "BTCUSDT", session_tz: str = "UTC") -> Dict[str, Any]:
    """
    Returns a single dict that includes:
      - raw market inputs (FRVP, HTF supplies/demands, 30m range, last_price, etc.)
      - AND the computed SSE payload:
          inputs["levels"]
          inputs["range_30m"]
          inputs["htf_shelves"]
          inputs["intraday_shelves"]
          inputs["bias_label"]
    """

    symbol = resolve_symbol(symbol)

    # ----------------------------------------------------------------
    # Your existing snapshot/build logic
    # (I’m using the exact key contract your app already expects.)
    # ----------------------------------------------------------------
    snap = _fetch_market_snapshot(symbol=symbol, session_tz=session_tz)

    inputs: Dict[str, Any] = {
        "date": snap.get("date") or _today_utc_str(),
        "symbol": symbol,
        "session_tz": session_tz,

        # Current price
        "last_price": snap.get("last_price"),

        # HTF anchors (from your workflow)
        "h4_supply": snap.get("h4_supply"),
        "h4_demand": snap.get("h4_demand"),
        "h1_supply": snap.get("h1_supply"),
        "h1_demand": snap.get("h1_demand"),

        # 24h FRVP
        "f24_vah": snap.get("f24_vah"),
        "f24_val": snap.get("f24_val"),
        "f24_poc": snap.get("f24_poc"),

        # Morning FRVP
        "morn_vah": snap.get("morn_vah"),
        "morn_val": snap.get("morn_val"),
        "morn_poc": snap.get("morn_poc"),

        # Opening range (06:30–07:00 CST in your docs; you’re storing the high/low)
        "range30m_high": snap.get("range30m_high"),
        "range30m_low": snap.get("range30m_low"),

        # Optional extras you already pass around
        "news": snap.get("news"),
        "sentiment": snap.get("sentiment"),
    }

    # Normalize range_30m shape for downstream consumers
    inputs["range_30m"] = {
        "high": inputs.get("range30m_high"),
        "low": inputs.get("range30m_low"),
    }

    # ----------------------------------------------------------------
    # CRITICAL: compute SSE (levels + shelves) and attach it to inputs.
    # This is what your compute_dmr() expects to already exist.
    # ----------------------------------------------------------------
    sse = compute_sse_levels(inputs)

    # Attach SSE outputs
    inputs["levels"] = sse.get("levels") or {}
    inputs["htf_shelves"] = sse.get("htf_shelves") or {}
    inputs["intraday_shelves"] = sse.get("intraday_shelves") or {}
    inputs["bias_label"] = sse.get("bias_label") or "neutral"

    return inputs


# -------------------------------------------------------------------
# Replace this stub with your real implementation.
# If you already have this implemented in your existing file,
# keep your real code and only keep the SSE-attach logic above.
# -------------------------------------------------------------------
def _fetch_market_snapshot(symbol: str, session_tz: str) -> Dict[str, Any]:
    """
    If your current data_feed.py already has real exchange calls,
    DO NOT use this stub—keep your existing implementation.

    This exists only so the file is complete.
    """
    # IMPORTANT: return the same keys your existing pipeline expects.
    return {
        "date": _today_utc_str(),
        "symbol": symbol,
        "last_price": None,

        "h4_supply": None,
        "h4_demand": None,
        "h1_supply": None,
        "h1_demand": None,

        "f24_vah": None,
        "f24_val": None,
        "f24_poc": None,

        "morn_vah": None,
        "morn_val": None,
        "morn_poc": None,

        "range30m_high": None,
        "range30m_low": None,

        "news": None,
        "sentiment": None,
    }
