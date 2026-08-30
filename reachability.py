# reachability.py
# ==============================================================================
# REACHABILITY GATE — is T1 close enough to actually get hit?
#
# Ported verbatim (2026-08-30) from `Kabroda AI Brain`'s
# brain/engine/reachability.py — the validated core of KABRODA_REBUILD_SPEC.md.
# Andy's authorization: this and its two sibling modules (htf_fuel.py,
# fuel_gate.py) REPLACE decision_engine.py's old graded-conviction model and
# trade_structure_analyst.py's ATR+gravity-wall stop/target math entirely.
# Nothing from the old system is kept "just in case."
#
# CALIBRATION.md §12 (Kabroda AI Brain repo), the single strongest signal in
# a 1,913-trigger-break backtest. T1 = 0.618x the box past the trigger, so a
# wide box puts T1 a canyon away. Measured against T1-reach:
#
#     box / daily-ATR(14)   reach T1
#     <= 0.25               65%
#     0.25 - 0.40           55%
#     0.40 - 0.55           50%
#     0.55 - 0.75           41%
#     > 1.0                 24%
#
# box/ATR is the cleanest normalization (self-adjusts for volatility regime).
# MEDIUM operating point (Andy-locked 2026-08-30): gate at 0.55.
# ==============================================================================

from __future__ import annotations

from typing import Any, Dict

MAX_BOX_ATR = 0.55          # MEDIUM operating point (Andy-locked 2026-08-30)
PREMIUM_BOX_ATR = 0.40      # matches verdict.py's tier constant — the number
                             # actually used in the Brain's real decision path
                             # (its own reachability.py has a stale, unused
                             # 0.25 duplicate — flagged back to that repo,
                             # not ported here)


def reachability(box: float, atr: float) -> Dict[str, Any]:
    """box = breakout_trigger - breakdown_trigger; atr = daily ATR(14)."""
    if not box or box <= 0 or not atr or atr <= 0:
        return {"ratio": None, "ok": False, "tier": "UNKNOWN",
                "note": "no box / ATR to size reachability"}
    ratio = box / atr
    ok = ratio <= MAX_BOX_ATR
    tier = "PREMIUM" if ratio <= PREMIUM_BOX_ATR else ("OK" if ok else "TOO_WIDE")
    return {
        "ratio": round(ratio, 3), "ok": ok, "tier": tier,
        "note": (f"box is {ratio:.2f}x daily ATR - "
                 + ("T1 is in reach" if ok else "T1 is out of reach, skip")),
    }
