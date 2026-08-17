# harness/tier_labels.py
# =============================================================================
# KABRODA FORWARD-AUDIT LOOP — Four-Tier Label System
#
# Replaces the two-tier CANDIDATE/FINDING system. Label is based on the
# CELL's own N — never the overall stream N. A TANGLED subgroup at N=8
# is DIRECTIONAL_OBSERVATION even when the approved stream reaches N=50.
#
# Tiers:
#   DIRECTIONAL_OBSERVATION  N < 30    No statistical test. Result in expected
#                                       direction; may be noise.
#   PRELIMINARY_SIGNAL       N 30–49   Binomial test runs. p-value reported.
#                                       Not yet at conventional significance.
#   PROVISIONAL_FINDING      N 50–99   Statistically significant at stated
#                                       threshold. Not multi-regime validated.
#   VALIDATED_EDGE           N 100+    Multi-regime validation available.
#                                       Walk-forward and regime tests can run.
#
# READ-ONLY helper. No write path.
# =============================================================================

from typing import Optional


TIER_DIRECTIONAL   = "DIRECTIONAL_OBSERVATION"
TIER_PRELIMINARY   = "PRELIMINARY_SIGNAL"
TIER_PROVISIONAL   = "PROVISIONAL_FINDING"
TIER_VALIDATED     = "VALIDATED_EDGE"

# N boundaries for each tier transition
BOUNDARY_PRELIMINARY = 30
BOUNDARY_PROVISIONAL = 50
BOUNDARY_VALIDATED   = 100


def tier_label(n: int) -> str:
    """Return the evidence tier label for a cell with n observations."""
    if n < BOUNDARY_PRELIMINARY:
        return TIER_DIRECTIONAL
    if n < BOUNDARY_PROVISIONAL:
        return TIER_PRELIMINARY
    if n < BOUNDARY_VALIDATED:
        return TIER_PROVISIONAL
    return TIER_VALIDATED


def tier_note(n: int) -> str:
    """Return a one-line interpretation note to append to outputs."""
    t = tier_label(n)
    if t == TIER_DIRECTIONAL:
        return f"(N={n} — {TIER_DIRECTIONAL}: below statistical inference threshold of {BOUNDARY_PRELIMINARY})"
    if t == TIER_PRELIMINARY:
        return f"(N={n} — {TIER_PRELIMINARY}: binomial test active, p-value reported)"
    if t == TIER_PROVISIONAL:
        return f"(N={n} — {TIER_PROVISIONAL}: significant at threshold, multi-regime not yet validated)"
    return f"(N={n} — {TIER_VALIDATED}: walk-forward and regime segmentation tests available)"


def pct_with_n(numerator: int, denominator: int, label: Optional[str] = None) -> str:
    """
    Format a percentage with its N. Structurally impossible to emit a bare percentage.
    Optionally appends a tier label.
    """
    if denominator == 0:
        return "— (N=0)"
    pct = round(numerator / denominator * 100.0, 1)
    base = f"{pct}% (N={denominator})"
    if label:
        return f"{base} [{label}]"
    return base
