# harness/deferred_tests.py
# =============================================================================
# KABRODA BATTLE-TEST HARNESS — Deferred Test Stubs
#
# These tests are NOT implemented. They are documented stubs that mark where
# each test plugs into the harness plumbing, what N gate it requires, and
# what it will do when activated.
#
# DO NOT implement until the N gate is reached.
# DO NOT cross-wire any output from these stubs to a live config write.
#
# Activation gates (resolved evaluable events required):
#   ablation_suite      — 50+ resolved
#   parameter_sweep     — 100+ resolved
#   scenario_scoreboard — 200+ resolved (or see note in stub)
#
# Current N: 12 (as of 2026-06-22). Gates are all closed.
# =============================================================================

from typing import Any, Dict, List


ABLATION_GATE        = 50
PARAMETER_GATE       = 100
SCOREBOARD_GATE      = 200
WALK_FORWARD_GATE    = 70    # minimum for credible IS/WFA/OOS split (anchored, 4-week OOS steps)
REGIME_SEGMENT_GATE  = 50    # minimum for regime breakdown with N ≥ 10 per cell
PLATEAU_TEST_GATE    = 100   # minimum for parameter-sensitivity plateau analysis


def _gate_check(n_current: int, gate: int, name: str) -> None:
    """Raises NotImplementedError if the N gate is not met."""
    if n_current < gate:
        raise NotImplementedError(
            f"{name} requires {gate} resolved evaluable events. "
            f"Current N={n_current}. Accumulate more data and re-check."
        )


# =============================================================================
# STUB 1 — ABLATION SUITE
# Gate: 50+ resolved evaluable events
# =============================================================================

def ablation_suite(
    approved_events: List[Dict[str, Any]],
    standdown_events: List[Dict[str, Any]],
    n_current: int,
) -> Dict[str, Any]:
    """
    [STUB — NOT IMPLEMENTED. Gate: {gate} resolved events. Current N={n}]

    What this will do when activated:
      Remove each indicator one at a time from the prediction set and re-run
      the accuracy calculation. For each removal, report:
        - Baseline accuracy (all indicators present): X% (N=total)
        - Accuracy with this indicator removed: Y% (N=total)
        - Delta: (Y - X) percentage points
        - Interpretation: positive delta = removing it HELPED (indicator adding noise);
          negative delta = removing it HURT (indicator contributing signal).

    Indicators to ablate (one at a time, all others held constant):
      1. energy_status         — ACTIVE / BUILDING / DEPLETED
      2. kinematic_grade       — PRIMED / OVEREXTENDED / TANGLED / UNKNOWN
      3. box_size_pct          — treated as a threshold gate (below floor = stand-down)
      4. jewel_gate_open       — boolean gate at session lock
      5. jewel_exit_warning    — boolean modifier (when TRUE, reduces conviction)

    Output (when implemented): dict keyed by indicator name with
    {baseline_accuracy_pct, ablated_accuracy_pct, delta_pct, n, label}
    Every percentage includes its N. No result reported as a finding unless N ≥ {gate}.

    Reads from: approved_events and standdown_events (from join_logic.py streams).
    Writes to:  nothing. Returns a dict. No live config path.
    """.format(gate=ABLATION_GATE, n=n_current)

    _gate_check(n_current, ABLATION_GATE, "ablation_suite")
    # Implementation goes here after gate is reached.
    raise NotImplementedError


# =============================================================================
# STUB 2 — PARAMETER SWEEP
# Gate: 100+ resolved evaluable events
# =============================================================================

def parameter_sweep(
    approved_events: List[Dict[str, Any]],
    standdown_events: List[Dict[str, Any]],
    n_current: int,
    sweep_targets: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    [STUB — NOT IMPLEMENTED. Gate: {gate} resolved events. Current N={n}]

    What this will do when activated:
      For each tunable threshold, sweep the full candidate range and report the
      accuracy / net-R curve at each setting. We want to see the SHAPE, not a
      single "best" number. A real edge shows up as a plateau; noise shows up as
      a jagged spike.

    Primary sweep target — box_size_pct floor:
      Range: 0.3% to 1.5% in 0.05% increments
      For each floor value f:
        - Sessions with box_size_pct < f → would have been STAND_DOWN
        - Remaining sessions → scored as-is
        - Report: accuracy_pct (N), net_R (N), sessions_excluded (N)
      Output: one row per floor value, sorted ascending.

    Secondary sweep targets (add after primary is validated):
      - energy_status threshold (e.g. DEPLETED → automatic STAND_DOWN floor)
      - kinematic_grade exclusion (e.g. TANGLED → hard gate?)

    sweep_targets: optional dict to override default sweep ranges.

    Output (when implemented): {
      "box_floor_curve": [{"floor_pct": f, "accuracy_pct": X, "n": N, "net_r": R}, ...],
      ...
    }
    Every percentage includes its N. No single "best" value reported as a recommendation —
    report the curve, let the human read the plateau. Gate: {gate} resolved events.

    Reads from: approved_events and standdown_events (from join_logic.py streams).
    Writes to:  nothing. Returns a dict. No live config path.
    """.format(gate=PARAMETER_GATE, n=n_current)

    _gate_check(n_current, PARAMETER_GATE, "parameter_sweep")
    # Implementation goes here after gate is reached.
    raise NotImplementedError


# =============================================================================
# STUB 3 — SCENARIO SCOREBOARD
# Gate: 200+ resolved evaluable events
# (Lower interim gate of 60+ is defensible IF scenarios differ only in one
#  dimension and cells have N ≥ 15 each. Document that choice explicitly when
#  implementing — do not quietly lower the gate without noting it here.)
# =============================================================================

def scenario_scoreboard(
    approved_events: List[Dict[str, Any]],
    standdown_events: List[Dict[str, Any]],
    n_current: int,
    scenarios: Dict[str, Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    [STUB — NOT IMPLEMENTED. Gate: {gate} resolved events. Current N={n}]

    What this will do when activated:
      Score a set of named parameter configurations (A/B/C/D) head-to-head across
      the pooled dataset. Each scenario is a complete parameter set defining which
      sessions would have been approved vs. stood down under that config.

    Scenario structure (TBD — define when implementing):
      Each scenario is a dict of thresholds: {
        "box_floor_pct": 0.5,
        "energy_required": ["ACTIVE", "BUILDING"],
        "kinematic_excluded": ["TANGLED"],
        "jewel_gate_required": True,
        ...
      }
      For each scenario, replay every canonical session: apply the scenario's rules,
      produce an approve/stand-down decision, score it against the actual outcome,
      compute accuracy_pct (N) and net_R (N).

    Output (when implemented): ranked list of scenarios by net_R, with accuracy_pct
    and N printed for each. Lower-N scenarios labeled CANDIDATE regardless of ranking.

    Writes to:  nothing. Returns a dict. No live config path.

    Note on interim gate: if scenarios differ in only ONE dimension (e.g. only box_floor
    differs) and each scenario has N ≥ 15, a one-way comparison at N=60 is defensible.
    If implementing early, document the N explicitly and label all results CANDIDATE.
    Full multi-dimension comparison requires {gate}+ resolved events.
    """.format(gate=SCOREBOARD_GATE, n=n_current)

    _gate_check(n_current, SCOREBOARD_GATE, "scenario_scoreboard")
    # Implementation goes here after gate is reached.
    raise NotImplementedError


# =============================================================================
# STUB 4 — WALK-FORWARD IS / WFA / OOS MACHINERY
# Gate: 70+ resolved evaluable events
#
# Architecture decision (locked — does not require re-approval when implementing):
#   ANCHORED walk-forward: IS window always starts at 2026-05-27 (first canonical
#   record) and expands forward. OOS steps forward in 4-week blocks. Anchored
#   (not rolling) because each resolved session is hard-won data — a rolling
#   window that drops early sessions throws away evidence.
# =============================================================================

def walk_forward(
    approved_events: List[Dict[str, Any]],
    standdown_events: List[Dict[str, Any]],
    n_current: int,
    oos_step_weeks: int = 4,
) -> Dict[str, Any]:
    """
    [STUB — NOT IMPLEMENTED. Gate: {gate} resolved events. Current N={n}]

    What this will do when activated:
      Anchored walk-forward analysis (IS origin: 2026-05-27; OOS step: {step} weeks).

      For each OOS window:
        - IS: all events from 2026-05-27 up to (but not including) the OOS window
        - OOS: the next {step} weeks of resolved events
        - Evaluate: does IS-apparent accuracy transfer to OOS? Compute WFE ratio.

      WFE (Walk-Forward Efficiency) = OOS accuracy / IS accuracy.
      WFE bands from literature (orientation only — calibrated for higher-frequency systems):
        >70%: Most IS edge survived. Strong candidate.
        50–70%: Meaningful degradation, real edge still present.
        <35%: Failure — IS was largely curve-fit.

      Output: list of OOS window results + aggregate WFE across all windows.
      A "majority pass" rule: if most OOS windows show positive accuracy transfer,
      the parameter set is viable. One catastrophic OOS window is a hard reject.

      All percentages include their N. OOS windows with N < 5 are flagged THIN.

    Reads from: approved_events and standdown_events.
    Writes to:  nothing. Returns a dict. No live config path.
    """.format(gate=WALK_FORWARD_GATE, n=n_current, step=oos_step_weeks)

    _gate_check(n_current, WALK_FORWARD_GATE, "walk_forward")
    raise NotImplementedError


# =============================================================================
# STUB 5 — REGIME SEGMENTATION TEST
# Gate: 50+ resolved evaluable events
# =============================================================================

def regime_segment(
    approved_events: List[Dict[str, Any]],
    standdown_events: List[Dict[str, Any]],
    n_current: int,
) -> Dict[str, Any]:
    """
    [STUB — NOT IMPLEMENTED. Gate: {gate} resolved events. Current N={n}]

    What this will do when activated:
      Split events by macro regime label from agent_chain_json (the MSA agent's
      Elliott Wave verdict — e.g. BULL_WAVE_3, BULL_WAVE_4_CORRECTIVE, etc.).
      For each regime category:
        - Report approved accuracy (wins / total) with N
        - Report stand-down correctness rate with N
        - Flag if N < 10: THIN — do not interpret

      Diagnostic: if outperformance concentrates in one regime, the system's
      edge may be regime-coincident (not structural). A genuine structural edge
      produces broadly consistent accuracy across regime categories.

      The segmentation data comes from session_audit_log.agent_chain_json
      (frozen at decision time), making the regime label auditable.

    Reads from: approved_events, standdown_events, session_audit_log.
    Writes to:  nothing. Returns a dict. No live config path.
    """.format(gate=REGIME_SEGMENT_GATE, n=n_current)

    _gate_check(n_current, REGIME_SEGMENT_GATE, "regime_segment")
    raise NotImplementedError


# =============================================================================
# STUB 6 — PARAMETER SENSITIVITY (PLATEAU) TEST
# Gate: 100+ resolved evaluable events AND at least one ACTIVE_CANDIDATE
#       entry in trials_log (a candidate must exist before plateau-testing it)
# =============================================================================

def plateau_test(
    approved_events: List[Dict[str, Any]],
    standdown_events: List[Dict[str, Any]],
    n_current: int,
    base_config: Dict[str, Any] = None,
    perturbation_pct: float = 10.0,
) -> Dict[str, Any]:
    """
    [STUB — NOT IMPLEMENTED. Gate: {gate} resolved events. Current N={n}]

    What this will do when activated:
      For a given ACTIVE_CANDIDATE parameter configuration, test adjacent values
      ±{pct}% in the smallest meaningful increment. Report the accuracy curve shape.

      A genuine structural edge is INSENSITIVE to small parameter perturbations —
      it produces a plateau: a range of nearby parameter values that all perform
      similarly. If changing a threshold by {pct}% collapses performance, the
      edge is cliff-dependent (likely a data-mined artifact).

      Report: one row per parameter value tested, sorted ascending.
      Each row: parameter value, accuracy_pct (N), net_R (N), delta from base.
      Curve shape is the output — NOT a "best value." Do not select a winner
      from this curve. Report the plateau width; let the human read it.

      Each adjacent value tested is logged to trials_log as a comparison spent.

    Reads from: approved_events, standdown_events, trials_log for base_config.
    Writes to:  trials_log (one row per adjacent value tested). No live config path.
    """.format(gate=PLATEAU_TEST_GATE, n=n_current, pct=perturbation_pct)

    _gate_check(n_current, PLATEAU_TEST_GATE, "plateau_test")
    raise NotImplementedError
