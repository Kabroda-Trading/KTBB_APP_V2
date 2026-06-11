# external_intel_reporter.py
# ==============================================================================
# KABRODA EXTERNAL INTEL REPORTER
# Pure Python stdlib: urllib.request + json — zero new dependencies.
#
# Sources:
#   1. Alternative.me Fear & Greed Index  (no API key, free)
#   2. CoinGecko /api/v3/global           (no API key, free)
#
# Each source is independently guarded. One failure does not block the other.
# Returns sentinel values ("UNAVAILABLE") on any error so the Publisher agent
# can omit the section gracefully rather than crashing.
#
# Used by: publisher_crew.run_publisher()
# ==============================================================================

import json
import os
import urllib.request
from typing import Any, Dict

_FNG_URL    = "https://api.alternative.me/fng/"
_GECKO_URL  = "https://api.coingecko.com/api/v3/global"
_TIMEOUT    = 5
_UA         = "KabrodaPublisher/1.0"
_GECKO_KEY  = os.getenv("COINGECKO_API_KEY", "")


def _fetch_fear_and_greed() -> Dict[str, Any]:
    try:
        req = urllib.request.Request(_FNG_URL, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())

        entry = data["data"][0]
        value = int(entry["value"])
        classification = entry["value_classification"]

        if value <= 25:
            narrative = "extreme fear — historically associated with institutional accumulation zones"
        elif value <= 45:
            narrative = "fear — broad market risk-aversion elevated"
        elif value <= 55:
            narrative = "neutral — no strong directional sentiment bias present"
        elif value <= 75:
            narrative = "greed — risk appetite elevated, extended positioning risk building"
        else:
            narrative = "extreme greed — historically elevated probability of mean reversion"

        return {
            "status": "OK",
            "value": value,
            "classification": classification,
            "narrative": narrative,
        }

    except Exception as e:
        print(f"[INTEL REPORTER] Fear & Greed fetch failed: {e}")
        return {
            "status": "UNAVAILABLE",
            "value": None,
            "classification": "UNAVAILABLE",
            "narrative": "Market sentiment data temporarily unavailable.",
        }


def _fetch_crypto_global() -> Dict[str, Any]:
    try:
        headers = {"User-Agent": _UA}
        if _GECKO_KEY:
            headers["x-cg-demo-api-key"] = _GECKO_KEY
        req = urllib.request.Request(_GECKO_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())

        d = data.get("data", {})
        total_market_cap  = d.get("total_market_cap", {}).get("usd", 0) or 0
        total_volume      = d.get("total_volume", {}).get("usd", 0) or 0
        btc_dominance     = d.get("market_cap_percentage", {}).get("btc", 0) or 0
        cap_change_24h    = d.get("market_cap_change_percentage_24h_usd", 0) or 0

        def _fmt(v: float) -> str:
            if v >= 1e12:   return f"${v / 1e12:.2f}T"
            if v >= 1e9:    return f"${v / 1e9:.1f}B"
            return f"${v:,.0f}"

        return {
            "status": "OK",
            "total_market_cap_formatted":  _fmt(total_market_cap),
            "total_volume_24h_formatted":  _fmt(total_volume),
            "btc_dominance_pct":           round(float(btc_dominance), 1),
            "market_cap_change_24h_pct":   round(float(cap_change_24h), 2),
            "market_cap_direction":        "up" if cap_change_24h >= 0 else "down",
        }

    except Exception as e:
        print(f"[INTEL REPORTER] CoinGecko global fetch failed: {e}")
        return {
            "status": "UNAVAILABLE",
            "total_market_cap_formatted": "UNAVAILABLE",
            "total_volume_24h_formatted": "UNAVAILABLE",
            "btc_dominance_pct":          None,
            "market_cap_change_24h_pct":  None,
            "market_cap_direction":       None,
        }


def fetch_market_intel() -> Dict[str, Any]:
    """
    Public entry point. Calls both sources sequentially (each timeout=5s max).
    Returns:
      {
        "fear_and_greed": { status, value, classification, narrative },
        "crypto_global":  { status, total_market_cap_formatted, ... }
      }
    """
    return {
        "fear_and_greed": _fetch_fear_and_greed(),
        "crypto_global":  _fetch_crypto_global(),
    }
