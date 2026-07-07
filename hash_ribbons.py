# hash_ribbons.py
# ==============================================================================
# KABRODA HASH RIBBONS (Capriole Investments' public methodology)
# Pure Python stdlib: urllib.request + json — zero new dependencies, same
# pattern as external_intel_reporter.py.
#
# Source: blockchain.com Charts API (BTC daily hash rate), no API key, free.
#
# Krown's own KULTI course names "Hash Ribbons" as component #7 of its
# 11-component stack ("On-chain Bitcoin miner capitulation and recovery
# anchor") but documents no formula anywhere in the course material. This
# implements the real, publicly documented Capriole Investments methodology
# (30D/60D SMA cross of BTC network hash rate) — honestly labeled as sourced
# from Capriole's public methodology, not Crown's own undocumented internals.
#
# CAPITULATION: 30D SMA below 60D SMA — miners under stress, historically
#   precedes local bottoms.
# RECOVERY: 30D SMA has crossed back above the 60D SMA after being below —
#   Capriole's classic buy signal (miner capitulation ending).
# NEUTRAL: 30D SMA above 60D SMA with no recent capitulation to recover from.
#
# Used by: lti_engine.py
# ==============================================================================

import json
import urllib.request
from typing import Any, Dict, List, Optional

_HASHRATE_URL = "https://api.blockchain.info/charts/hash-rate?timespan=2years&format=json&cors=true"
_TIMEOUT = 8
_UA = "KabrodaLTI/1.0"


def _sma(values: List[float], period: int) -> List[Optional[float]]:
    """Simple moving average, same-length output list (None where insufficient history)."""
    out: List[Optional[float]] = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = sum(values[i - period + 1:i + 1]) / period
    return out


def _classify_hash_ribbons(ma30: List[Optional[float]], ma60: List[Optional[float]]) -> str:
    valid = [i for i in range(len(ma30)) if ma30[i] is not None and ma60[i] is not None]
    if len(valid) < 2:
        return "UNAVAILABLE"

    last = valid[-1]
    if ma30[last] < ma60[last]:
        return "CAPITULATION"

    # Currently above -- a RECOVERY (Capriole's buy signal) only if the 30D
    # was below the 60D at some point in the recent lookback window (a fresh
    # cross-back-up), not just steady-state bull with no capitulation to
    # recover from.
    lookback_idxs = [i for i in valid if i >= last - 60][:-1]
    was_below_recently = any(ma30[i] < ma60[i] for i in lookback_idxs)
    return "RECOVERY" if was_below_recently else "NEUTRAL"


def fetch_hash_ribbons_state() -> Dict[str, Any]:
    """
    Public entry point. Returns:
      { status, state, ma30_latest, ma60_latest }
    state in {CAPITULATION, RECOVERY, NEUTRAL, UNAVAILABLE}.
    """
    try:
        req = urllib.request.Request(_HASHRATE_URL, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())

        values = [float(pt["y"]) for pt in data.get("values", [])]
        if len(values) < 61:
            raise ValueError(f"insufficient hash-rate history ({len(values)} points, need >=61)")

        ma30 = _sma(values, 30)
        ma60 = _sma(values, 60)
        state = _classify_hash_ribbons(ma30, ma60)

        last_idx = len(values) - 1
        return {
            "status": "OK",
            "state": state,
            "ma30_latest": round(ma30[last_idx], 2) if ma30[last_idx] is not None else None,
            "ma60_latest": round(ma60[last_idx], 2) if ma60[last_idx] is not None else None,
        }

    except Exception as e:
        print(f"[HASH RIBBONS] Fetch failed: {e}")
        return {"status": "UNAVAILABLE", "state": "UNAVAILABLE", "ma30_latest": None, "ma60_latest": None}
