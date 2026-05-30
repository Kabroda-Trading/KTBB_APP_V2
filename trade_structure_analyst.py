# trade_structure_analyst.py
# ==============================================================================
# TRADE STRUCTURE ANALYST — Pure Python Math Layer
# Runs immediately before the Senior Analyst context is built.
# No LLM. No DB writes. No side effects.
#
# Responsibilities:
#   1. Compute structural stops from 30M range + ATR (gravity-aware)
#   2. Snap Fibonacci targets to intercepting HEAVY/MAXIMUM gravity walls
#
# ATR source: levels["atr"] — 14-period ATR from resampled 15M candles.
# Called by: kabroda_mas_flow.run_mas_analysis()
# ==============================================================================

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple

_BLOCKING_INTENSITIES = {"HEAVY", "MAXIMUM"}


def _heavy_peaks(kde_peaks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [p for p in kde_peaks if p.get("intensity") in _BLOCKING_INTENSITIES]


def _find_wall_long(lo: float, hi: float, heavy: List[Dict]) -> Optional[Dict]:
    """
    Ascending scan: returns the first obstacle in (lo, hi) — lowest price wall.
    Used for LONG targets (first wall encountered going up from entry)
    and LONG stop zone (lowest wall between base_stop and r30_low).
    """
    candidates = [p for p in heavy if lo < p.get("price", 0) < hi]
    return min(candidates, key=lambda p: p["price"]) if candidates else None


def _find_wall_short(lo: float, hi: float, heavy: List[Dict]) -> Optional[Dict]:
    """
    Descending scan: returns the first obstacle in (lo, hi) — highest price wall.
    Used for SHORT targets (first wall encountered going down from entry)
    and SHORT stop zone (highest wall between r30_high and base_stop).
    """
    candidates = [p for p in heavy if lo < p.get("price", 0) < hi]
    return max(candidates, key=lambda p: p["price"]) if candidates else None


def _structural_stop_long(
    r30_low: float, atr: float, heavy: List[Dict]
) -> Tuple[float, str]:
    """
    LONG stop: r30_low - ATR*0.5
    If a HEAVY/MAXIMUM wall sits between that stop and r30_low,
    move stop to wall_price - ATR*0.25.
    """
    base = round(r30_low - atr * 0.5, 2)
    wall = _find_wall_long(base, r30_low, heavy)
    if wall:
        adjusted = round(wall["price"] - atr * 0.25, 2)
        return adjusted, (
            f"Adjusted: base ${base:,.2f} → wall [{wall.get('intensity','?')}] "
            f"at ${wall['price']:,.2f} intercepts between base stop and 30M low. "
            f"Stop moved below wall: ${adjusted:,.2f} (wall − ATR×0.25)"
        )
    return base, (
        f"No adjustment: ${base:,.2f} "
        f"(30M low ${r30_low:,.2f} − ATR×0.5 ${atr * 0.5:,.2f}). "
        f"No HEAVY/MAXIMUM wall in stop zone."
    )


def _structural_stop_short(
    r30_high: float, atr: float, heavy: List[Dict]
) -> Tuple[float, str]:
    """
    SHORT stop: r30_high + ATR*0.5
    If a HEAVY/MAXIMUM wall sits between r30_high and that stop,
    move stop to wall_price + ATR*0.25.
    """
    base = round(r30_high + atr * 0.5, 2)
    wall = _find_wall_short(r30_high, base, heavy)
    if wall:
        adjusted = round(wall["price"] + atr * 0.25, 2)
        return adjusted, (
            f"Adjusted: base ${base:,.2f} → wall [{wall.get('intensity','?')}] "
            f"at ${wall['price']:,.2f} intercepts between 30M high and base stop. "
            f"Stop moved above wall: ${adjusted:,.2f} (wall + ATR×0.25)"
        )
    return base, (
        f"No adjustment: ${base:,.2f} "
        f"(30M high ${r30_high:,.2f} + ATR×0.5 ${atr * 0.5:,.2f}). "
        f"No HEAVY/MAXIMUM wall in stop zone."
    )


def _snap_long(
    entry: float, target: float, label: str, heavy: List[Dict]
) -> Tuple[float, str]:
    """
    LONG target snapping: check for HEAVY/MAXIMUM wall between entry and target.
    Snaps to the nearest (lowest) intercepting wall.
    """
    wall = _find_wall_long(entry, target, heavy)
    if wall:
        return round(wall["price"], 2), (
            f"{label} adjusted — wall [{wall.get('intensity','?')} | "
            f"heat={wall.get('heat_score', 0):.1f}] at ${wall['price']:,.2f} "
            f"intercepts before Fib ${target:,.2f}. Snapped to wall."
        )
    return round(target, 2), (
        f"{label} clear — no HEAVY/MAXIMUM wall between entry and Fib ${target:,.2f}. "
        f"Using Fibonacci."
    )


def _snap_short(
    entry: float, target: float, label: str, heavy: List[Dict]
) -> Tuple[float, str]:
    """
    SHORT target snapping: check for HEAVY/MAXIMUM wall between target and entry.
    Snaps to the nearest (highest) intercepting wall.
    """
    wall = _find_wall_short(target, entry, heavy)
    if wall:
        return round(wall["price"], 2), (
            f"{label} adjusted — wall [{wall.get('intensity','?')} | "
            f"heat={wall.get('heat_score', 0):.1f}] at ${wall['price']:,.2f} "
            f"intercepts before Fib ${target:,.2f}. Snapped to wall."
        )
    return round(target, 2), (
        f"{label} clear — no HEAVY/MAXIMUM wall between entry and Fib ${target:,.2f}. "
        f"Using Fibonacci."
    )


def apply_trade_structure(
    levels: Dict[str, Any],
    context: Dict[str, Any],
    raw_targets: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Main entry point. Accepts battlebox levels, context, and raw Fibonacci targets.
    Returns an adjusted targets dict with the same shape as _compute_targets(),
    plus 'structure_notes' (string injected into LLM context) and
    'reasoning' (dict written to CampaignLog.structure_reasoning for backtesting).

    Output shape: {"distance", "long": {...}, "short": {...}, "structure_notes", "reasoning"}
    """
    atr = float(levels.get("atr") or 0)
    r30_high = float(levels.get("range30m_high") or 0)
    r30_low = float(levels.get("range30m_low") or 0)
    kde_peaks = context.get("kde_peaks", [])
    heavy = _heavy_peaks(kde_peaks)

    raw_long = raw_targets.get("long", {})
    raw_short = raw_targets.get("short", {})
    distance = raw_targets.get("distance", 0)

    # Guard: if ATR or range levels are missing, pass through unchanged
    if atr <= 0 or r30_high <= 0 or r30_low <= 0:
        notes = (
            "Trade Structure Analyst: insufficient data for structural adjustments "
            f"(ATR={atr}, 30M_HIGH={r30_high}, 30M_LOW={r30_low}). "
            "Raw Fibonacci targets are in effect."
        )
        result = dict(raw_targets)
        result["structure_notes"] = notes
        result["reasoning"] = {"status": "PASSTHROUGH", "reason": notes}
        return result

    # --- Stops ---
    long_stop, long_stop_rsn = _structural_stop_long(r30_low, atr, heavy)
    short_stop, short_stop_rsn = _structural_stop_short(r30_high, atr, heavy)

    # --- LONG targets ---
    long_entry = float(raw_long.get("entry", 0))
    raw_lt1 = float(raw_long.get("t1", 0))
    raw_lt2 = float(raw_long.get("t2", 0))
    raw_lt3 = float(raw_long.get("t3", 0))

    lt1, lt1_rsn = _snap_long(long_entry, raw_lt1, "T1 (LONG)", heavy)
    lt2, lt2_rsn = _snap_long(long_entry, raw_lt2, "T2 (LONG)", heavy)
    lt3, lt3_rsn = _snap_long(long_entry, raw_lt3, "T3 (LONG)", heavy)

    # --- SHORT targets ---
    short_entry = float(raw_short.get("entry", 0))
    raw_st1 = float(raw_short.get("t1", 0))
    raw_st2 = float(raw_short.get("t2", 0))
    raw_st3 = float(raw_short.get("t3", 0))

    st1, st1_rsn = _snap_short(short_entry, raw_st1, "T1 (SHORT)", heavy)
    st2, st2_rsn = _snap_short(short_entry, raw_st2, "T2 (SHORT)", heavy)
    st3, st3_rsn = _snap_short(short_entry, raw_st3, "T3 (SHORT)", heavy)

    # --- structure_notes block for Senior Analyst context ---
    structure_notes = "\n".join([
        f"ATR (15M, 14-period): ${atr:,.2f}",
        f"30M Range: ${r30_low:,.2f} – ${r30_high:,.2f}",
        f"HEAVY/MAXIMUM walls in active play: {len(heavy)}",
        "",
        f"LONG STOP:  {long_stop_rsn}",
        f"LONG T1:    {lt1_rsn}",
        f"LONG T2:    {lt2_rsn}",
        f"LONG T3:    {lt3_rsn}",
        "",
        f"SHORT STOP: {short_stop_rsn}",
        f"SHORT T1:   {st1_rsn}",
        f"SHORT T2:   {st2_rsn}",
        f"SHORT T3:   {st3_rsn}",
    ])

    # --- Full reasoning dict for DB audit trail ---
    reasoning = {
        "atr": atr,
        "r30_high": r30_high,
        "r30_low": r30_low,
        "heavy_wall_count": len(heavy),
        "long": {
            "original_stop": raw_long.get("stop"),
            "adjusted_stop": long_stop,
            "stop_reasoning": long_stop_rsn,
            "original_t1": raw_lt1, "adjusted_t1": lt1, "t1_reasoning": lt1_rsn,
            "original_t2": raw_lt2, "adjusted_t2": lt2, "t2_reasoning": lt2_rsn,
            "original_t3": raw_lt3, "adjusted_t3": lt3, "t3_reasoning": lt3_rsn,
        },
        "short": {
            "original_stop": raw_short.get("stop"),
            "adjusted_stop": short_stop,
            "stop_reasoning": short_stop_rsn,
            "original_t1": raw_st1, "adjusted_t1": st1, "t1_reasoning": st1_rsn,
            "original_t2": raw_st2, "adjusted_t2": st2, "t2_reasoning": st2_rsn,
            "original_t3": raw_st3, "adjusted_t3": st3, "t3_reasoning": st3_rsn,
        },
    }

    return {
        "distance": distance,
        "long": {
            "entry": long_entry,
            "stop": long_stop,
            "t1": lt1,
            "t2": lt2,
            "t3": lt3,
        },
        "short": {
            "entry": short_entry,
            "stop": short_stop,
            "t1": st1,
            "t2": st2,
            "t3": st3,
        },
        "structure_notes": structure_notes,
        "reasoning": reasoning,
    }
