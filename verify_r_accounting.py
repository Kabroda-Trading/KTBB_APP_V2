# verify_r_accounting.py
# ==============================================================================
# STANDALONE SYNTHETIC VERIFICATION — R-accounting fix (2026-07-04)
#
# NOT wired into main.py. NOT production code. Makes no DB writes, no network
# calls. Run directly:
#   python verify_r_accounting.py
#
# PURPOSE: confirm ledger_closing_engine._frac_r() computes true fractional R
# (actual reward / actual risk) instead of the old hardcoded ±1.0 assumption,
# using hand-computed synthetic cases -- not live data. This sidesteps the
# Kraken retroactive-history limit found earlier this session (public OHLCV
# history for 1m/15m candles doesn't reach back far enough to re-verify old
# closed trades), since this is pure arithmetic against fabricated inputs.
#
# Cases cover: the symmetric case where old and new formulas already agree
# (sanity check the fix doesn't disturb already-correct rows), the
# floored-leg and capped-leg cases where the 4H/1H v4 ATR rails decouple
# target distance from actual stop distance (where the old formula was
# silently wrong), SHORT-bias versions of each, and the zero-risk edge case.
# ==============================================================================

from ledger_closing_engine import _frac_r

OLD_FORMULA_T1_HIT = 1.0  # what every T1 hit used to hardcode, regardless of actual distances

CASES = [
    {
        "label": "Symmetric LONG (risk == reward) -- old and new should agree",
        "entry": 100.0, "stop": 95.0, "t1": 105.0, "is_long": True,
        "expected": 1.0,
    },
    {
        "label": "Floored-leg LONG (4H/1H ATR floor inflated target beyond actual risk)",
        "entry": 100.0, "stop": 99.0, "t1": 103.0, "is_long": True,
        "expected": 3.0,
    },
    {
        "label": "Capped-leg LONG (4H/1H ATR cap shrank target below actual risk)",
        "entry": 100.0, "stop": 80.0, "t1": 109.0, "is_long": True,
        "expected": 0.45,
    },
    {
        "label": "Symmetric SHORT (risk == reward) -- old and new should agree",
        "entry": 100.0, "stop": 105.0, "t1": 95.0, "is_long": False,
        "expected": 1.0,
    },
    {
        "label": "Floored-leg SHORT",
        "entry": 100.0, "stop": 101.0, "t1": 97.0, "is_long": False,
        "expected": 3.0,
    },
    {
        "label": "Capped-leg SHORT",
        "entry": 100.0, "stop": 120.0, "t1": 91.0, "is_long": False,
        "expected": 0.45,
    },
    {
        "label": "15M-style ATR/wall-adjusted stop (T1 distance != actual stop distance)",
        "entry": 62458.70, "stop": 61200.00, "t1": 63800.00, "is_long": True,
        "expected": round((63800.00 - 62458.70) / (62458.70 - 61200.00), 4),
    },
    {
        "label": "Zero-risk edge case (entry == stop, data anomaly) -- must not crash",
        "entry": 100.0, "stop": 100.0, "t1": 105.0, "is_long": True,
        "expected": round(5.0 / 0.01, 4),  # floored risk of 0.01 -- large, flagged, not a crash
    },
]


def main():
    print(f"{'Case':70s} {'OldFormula':>10s} {'NewFormula':>10s} {'Expected':>10s} {'Result':>8s}")
    all_pass = True
    for c in CASES:
        new_r = _frac_r(c["entry"], c["stop"], c["t1"], c["is_long"])
        ok = abs(new_r - c["expected"]) < 0.001
        all_pass = all_pass and ok
        flag = "OLD WAS WRONG" if abs(OLD_FORMULA_T1_HIT - c["expected"]) > 0.001 else "old was fine"
        print(f"{c['label']:70s} {OLD_FORMULA_T1_HIT:10.4f} {new_r:10.4f} {c['expected']:10.4f} "
              f"{'PASS' if ok else 'FAIL':>8s}  ({flag})")

    print()
    print("ALL CASES PASSED" if all_pass else "SOME CASES FAILED -- do not deploy until fixed")


if __name__ == "__main__":
    main()
