# fuel_gate.py
# ==============================================================================
# FUEL GATE — is the push real, or a ghost?
#
# Ported (2026-08-30) from `Kabroda AI Brain`'s brain/engine/fuel_gate.py,
# part of the KABRODA_REBUILD_SPEC.md core. Measures 5M push volume at the
# trigger cross against a 24h baseline. VOL_FUELED/VOL_THIN thresholds and
# the median-not-mean push measurement are validated (CALIBRATION.md §10/§12,
# Kabroda AI Brain repo) — not re-derived here, ported exactly.
# ==============================================================================

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional

# Volume behind the push vs 24h baseline.
# FUELED (>= 0.8) is a real signal - 62% T1-win vs 50% for CONFLICTED.
# VOL_THIN (0.35) moves the CONFLICTED/NO_FUEL boundary; kept, not
# independently validated (small-n caveat noted in the source repo).
VOL_FUELED = 0.8   # >= 0.8x baseline = real participation (validated)
VOL_THIN = 0.35    # < 0.35x baseline = ghost push (kept, not independently validated)


def measure_push_volume(
    candles_5m: List[Dict[str, Any]],
    trigger: float,
    side: str,
    lookback: int = 12,
    baseline_bars: int = 288,
) -> Dict[str, Any]:
    """Volume of the CURRENT push beyond `trigger` vs the ~24h immediately
    before it crossed.

    Anchored to the MOST RECENT crossing, not the first one in the buffer:
    with 40-60h of 5m history, a day-old poke through the level would
    otherwise be mistaken for today's push. If price is not currently beyond
    the trigger there is no active push to measure -> NO_PUSH."""
    if not candles_5m or len(candles_5m) < 20:
        return {"ratio": None, "verdict": "UNKNOWN"}
    vols = [float(c.get("volume") or 0) for c in candles_5m]
    closes = [float(c["close"]) for c in candles_5m]

    def _beyond(cl: float) -> bool:
        return cl < trigger if side == "SHORT" else cl > trigger

    if not _beyond(closes[-1]):
        return {"ratio": None, "verdict": "NO_PUSH",
                "note": "price is not currently beyond the trigger"}

    # start of the current contiguous run beyond the trigger = the cross
    cross_idx = len(closes) - 1
    while cross_idx > 0 and _beyond(closes[cross_idx - 1]):
        cross_idx -= 1

    push = vols[cross_idx:cross_idx + lookback] or vols[cross_idx:]
    base_slice = vols[max(0, cross_idx - baseline_bars):cross_idx]
    baseline = (sum(base_slice) / len(base_slice)) if base_slice else 0.0
    if baseline <= 0:
        return {"ratio": None, "verdict": "UNKNOWN",
                "note": "trigger cross precedes available 5m history"}

    # Median, not mean: one large-order outlier candle must not define the
    # push's energy.
    push_rep = statistics.median(push) if push else 0.0
    ratio = push_rep / baseline
    verdict = "FUELED" if ratio >= VOL_FUELED else ("THIN" if ratio < VOL_THIN else "NORMAL")
    return {"ratio": round(ratio, 2), "verdict": verdict,
            "push_bars": len(push),
            "push_median": round(push_rep, 4),
            "push_mean": round(sum(push) / len(push), 4) if push else 0.0,
            "baseline": round(baseline, 4),
            "baseline_bars": len(base_slice)}


def evaluate_fuel_gate(
    candles_5m: List[Dict[str, Any]],
    trigger: float,
    side: str,
    divergence: Optional[str] = None,
    fuel_1h: Optional[str] = None,
    fuel_4h: Optional[str] = None,
) -> Dict[str, Any]:
    """Combine push volume + divergence + higher-TF fuel into a single verdict.

    `divergence` is the 15M divergence reading (BULLISH/HIDDEN_BULLISH/
    BEARISH/HIDDEN_BEARISH/NONE). `fuel_1h`/`fuel_4h` are the timeframe_trend
    strings from htf_fuel.py."""
    vol = measure_push_volume(candles_5m, trigger, side)
    checks: Dict[str, Any] = {"push_volume": vol}

    if vol.get("verdict") == "NO_PUSH":
        return {"verdict": "NO_PUSH", "checks": checks}

    vol_ratio = vol.get("ratio")
    if vol_ratio is None:
        vol_ok = None
    elif vol_ratio < VOL_THIN:
        vol_ok = False
    elif vol_ratio >= VOL_FUELED:
        vol_ok = True
    else:
        vol_ok = None
    checks["volume_energy"] = vol_ok

    if side == "SHORT":
        div_against = divergence in ("BULLISH", "HIDDEN_BULLISH")
    else:
        div_against = divergence in ("BEARISH", "HIDDEN_BEARISH")
    checks["divergence_against"] = div_against

    want = "BULLISH" if side == "LONG" else "BEARISH"
    against = "BEARISH" if side == "LONG" else "BULLISH"
    opposing = sum(1 for f in (fuel_1h, fuel_4h) if f == against)
    aligned = sum(1 for f in (fuel_1h, fuel_4h) if f == want)
    checks["htf_opposing_count"] = opposing
    checks["htf_aligned_count"] = aligned

    if vol_ok is False and (div_against or opposing >= 1):
        verdict = "NO_FUEL"
    elif vol_ok is False or div_against or opposing >= 2:
        verdict = "CONFLICTED"
    elif vol_ok is True and not div_against and opposing == 0:
        verdict = "FUELED"
    else:
        verdict = "CONFLICTED"

    return {"verdict": verdict, "checks": checks,
            "htf_aligned": aligned, "htf_opposed": opposing}
