# Backtest Verification Protocol

**Created 2026-11-11.** This is a process document, not a one-time audit —
it exists so that every future change to a decision-layer file (Traveler,
CME, Alt Matrix) gets checked against its real backtest ground truth
*before* anyone claims "this matches the backtest," instead of that claim
being asserted and trusted on sight.

## Why this exists

The 2026-11-05 → 2026-11-11 CME/Traveler audit (`CME_TRAVELER_CROSS_
CONTAMINATION_AUDIT_2026_11_11.md`, this repo) found a repeating failure
shape across multiple subsystems: live code quietly drifted from what was
actually backtested, and the drift was discovered only when someone went
and re-ran or re-read the original measurement, not from the code "looking
wrong" on inspection. Four confirmed real instances, same shape each time:

| Subsystem | What drifted | How it was caught |
|---|---|---|
| CME D1 (reachability) | `daily_atr14` hardcoded to `None`, so the reachability sentinel failed on every lock | Dated comment found during this session's own line-by-line read of `cme_engine.py`, not a test failure |
| CME D1 (observability) | A calibration-fetch exception wrote **no row at all**, indistinguishable from "nothing happened yet" | Andy noticed the desk UI symptom first; code read confirmed the silent `return` |
| Traveler D3 (C5/BBWP exit) | Exit-timing bug — fixed 2026-11-08, see `AGENT_LOG.md` 2026-11-08 entries | Found and fixed before this audit; cited here as the same failure *class* |
| Traveler D1 (R1 Re-Arm) | Re-arm path was "completely blinded" — fixed 2026-11-08 | Same — found and fixed before this audit |

None of these were caught by `pytest tests/` passing. All four were caught
by a human or an agent going back to the *original measurement* and
re-checking the live code against it line-by-line. That's the discipline
this protocol tries to make routine instead of incidental.

A fifth, lower-stakes case from the same window: `KABRODA_MASTER_
ENGINEERING_SPECIFICATION.md` (v3.2) was found to contain fabricated
mechanics — a wrong entry-trigger formula, an invented reachability veto
for Traveler, a resurrected "measured harmful" management style — that
never reached real code, but *would* have been trusted as ground truth by
the next reader if this audit hadn't checked it against `CLAUDE.md`'s own
protected invariants first. Documentation can drift from reality just like
code can; this protocol treats both the same way.

## The core rule

**"Matches the backtest" is a claim about a specific file, at a specific
commit, diffed against a specific named script or dataset — never a
general impression.** Before accepting or writing that claim for any
decision-layer change:

1. Name the exact ground-truth artifact (script, CSV, or scorecard) being
   checked against — see the map below. If no such artifact exists for
   what changed, say so explicitly; "no ground truth to check against" is
   an honest answer, "it looks consistent with the spec" is not a
   substitute for one.
2. Re-derive or re-read the actual numbers from that artifact — don't
   trust a prior session's or another agent's summary of what it says.
   This project's own established discipline (every prior Claude Code
   session this year, reinforced explicitly in the 2026-11-11 audit):
   verify AGENT_LOG/commit-message claims against real code or a real
   test run, every time, including claims from a *previous Claude Code
   session* or from Claude Code's own prior work.
3. State the comparison's actual precision — "matched to 2 decimal places
   on one real session" is a real, specific claim; "verified" with no
   stated method is not.
4. If the artifact that defines "correct" doesn't exist yet for the thing
   being changed (a brand-new feature, not a refactor of measured
   behavior), say that plainly instead of inventing a plausible-sounding
   verification. This is the same rule the user's global library
   conventions already apply to documentation — it applies here too.

## Ground-truth map, per subsystem (verified to exist, 2026-11-11)

**Traveler (BTC, `gate_traveler.py` / `mgmt_e1_stack.py`)**
- `Kabroda AI Brain/brain/audit_evidence/traveler_study_2016_2020/
  scorecard_2016_2020.md` — the scorecard this lineage's frozen constants
  (`FULL_D1_CUTS`, trigger-touch-fill shape, stop/T1 formula) trace back
  to.
- `.../traveler_study_2016_2020/rearm_ledger_2016_2020.csv` and
  `journey_ledger_2016_2020.csv` — the actual trade-by-trade ledgers; any
  change to re-arm logic or journey/exit logic should be checkable against
  these row-for-row, not just "looks consistent with the rule."
- `.../traveler_study/lab_touchfill_arms.py` + `lab_touchfill_results.md`
  — the touch-fill (trigger-touch, not confirmed-close) arm comparison;
  this is the specific study that justified `CLAUDE.md`'s protected
  invariant #1 (do not revert to a confirmed-close fill).
- `.../traveler_study/lab_composite_stack.py` + `lab_composite_stack_
  results.md` — the full D1/D2/D3 stack composite result, `n=136`,
  `avgR +0.5216` leg-1 / `+0.6709` SPLIT-managed, cited directly in
  `CLAUDE.md`'s "Calibrated Gate" history section.
- Same-species studies exist for the other Traveler-pattern symbols:
  `.../traveler_study_sol/phase3_results.md` + `journey_ledger.csv`,
  `.../traveler_study_eth/phase3_results.md` + `journey_ledger.csv` — the
  Alt Matrix SOL/ETH legs' own backtest ground truth, separate from BTC's.

**CME (MNQ/MES, `cme_signals.py` / `cme_engine.py` / `cme_market.py`)**
- `Kabroda AI Brain/scripts/reconstruct_locks_cme_symbol.py` — the actual
  backtest-generating script. `cme_signals.py`'s own header already
  documents a real, rigorous parity check against this script, matched to
  2 decimal places against a real 2016 session — this is the model other
  files in this table should be held to, not a one-off.
- `Kabroda AI Brain/brain/audit_evidence/tradfi_futures/cme_futures_
  scorecard_2016_2026.md` — the scorecard behind `REACHABILITY_MAX=0.55`,
  `TARGET_MULT=2.0`, `BE_TRIGGER_MULT=0.8`, `BE_STOP_MULT=0.1`, and the
  symbol-specific `STOP_MODE_BY_SYMBOL` split.
- `.../tradfi_futures/ledger_cme_nq_primary.csv` /
  `ledger_cme_es_primary.csv` — the real trade ledgers those constants are
  derived from.
- `Kabroda AI Brain/brain/calibration/reconstruct_locks.py` +
  `compare_levels.py` — the TPO-vs-VRVP level-parity check cited in
  `CLAUDE.md`'s "24h value area" section (88% same-side, 78%
  same-outcome, 1.00x median box ratio on 123 real locks). Any change to
  `sse_engine.py::_calculate_tpo_value_area()` must stay bit-identical to
  `brain/engine/repro_levels.py::_tpo_value_area()` — `CLAUDE.md` already
  says this explicitly; this protocol just makes it checkable by naming
  the actual diff target.

**Alt Matrix (SOL/ETH admission, `alt_matrix_portfolio.py` /
`alt_matrix_engine.py`)**
- `Kabroda AI Brain/brain/audit_evidence/altcoin_study_sol/` and
  `altcoin_study_eth/` — the `step3_*` CSVs comparing management variants
  (`SPLIT_050_100`, `ALLOUT_050`, `ARM1`, `STANDARD_100`) per symbol. Any
  change to Alt Matrix's management rule should cite which `step3_*`
  variant it's implementing and why, not just "matches the spirit of
  Traveler's rule."
- The account-sharing/admission-cap design itself (30% margin floor / 20%
  risk cap / 3-position cap) traces to Andy's 2026-10-09 "Option A" ruling
  (`AGENT_LOG.md` + `CC_INTERFACE.md` §5, Brain repo) — a policy decision,
  not a backtest-derived constant, so its "ground truth" is the ruling
  text itself, not a CSV. Don't conflate the two kinds of justification.

**Where there is deliberately no backtest to check against**: the
Gravity Map (Class 0/1/2 KDE weighting) and `fuel_gate.py` are both real,
live-computed tools that are explicitly *not* decision inputs (per
`CLAUDE.md`). Don't invent a backtest-parity claim for either — their
correctness standard is "computes what it says it computes," not "matches
a measured edge," because they aren't claimed to have one.

## Known failure classes to specifically re-check for

Drawn from the four real instances above — when reviewing *any* change to
a decision-layer file, explicitly check for these shapes, not just "does
the diff look reasonable":

1. **A value frozen-at-one-moment vs. recomputed-live.** The CME ATR14
   bug and the Traveler RSI-at-lock-vs-RSI-at-cross distinction
   (`CLAUDE.md`'s "Calibrated Gate" section, condition 4) are both this
   shape: a backtest measured one specific timing, and live code must
   reproduce that exact timing, not a "more current" value that feels more
   correct.
2. **An exception path that returns/continues silently instead of
   persisting an honest status.** The CME `MISSED_WINDOW` gap was exactly
   this — `except Exception: return` with no row written. Any new
   try/except in a decision-layer polling loop should be checked for
   whether its failure path is observable or silent.
3. **A resurrected or half-reverted old design.** The Master Spec
   fabrication (an invented reachability veto, a resurrected breakeven/
   partial-exit style) is the documentation-side version of this; the
   2026-09-15 close-based-fill regression (`CLAUDE.md` invariant #1) is
   the code-side version. When a change makes a decision-layer file look
   *more* like a retired design (V2 Crown, pre-rebuild Traveler, PREMIUM/
   STANDARD tiers), stop and check it against `CLAUDE.md`'s "What Must
   Never Be Changed" list before proceeding, even if the change itself
   seems locally reasonable.
4. **A positive-allowlist subsystem accidentally made to depend on a
   negative-exclusion guard that can go stale.** Not a confirmed bug this
   round (see the corrected §5 finding in the CME/Traveler audit doc —
   Alt Matrix sharing Traveler's account is intentional), but the
   *pattern* is real: `gate_profile_of()` returning only one valid value
   now means it can never again discriminate between subsystems, which is
   a design fact worth remembering next time a new subsystem is added
   that genuinely does need excluding.

## Procedure before shipping a decision-layer change

1. Identify which row of the ground-truth map above the change touches.
   If it touches more than one subsystem's files, check each separately —
   don't assume a fix verified for Traveler also holds for CME's
   structurally similar code.
2. Re-run or re-read the specific named artifact (script output, CSV,
   scorecard) — not a summary of it from memory or from another agent's
   prior claim.
3. Run `pytest tests/` (full suite) — necessary, not sufficient. The four
   real bugs in this doc's opening table all had the suite passing the
   whole time; tests check that the code does what the tests expect, not
   that the tests expect the right thing.
4. Write down, in the commit message or `AGENT_LOG.md`, exactly what was
   checked and against what artifact — "verified against `reconstruct_
   locks_cme_symbol.py`'s 2016 session, 2 decimal places" is checkable by
   the next reader; "verified" alone is not.
5. If the check surfaces a real discrepancy, follow this repo's own
   established correction standard (`CLAUDE.md`, global): fix it and say
   so plainly, don't hedge it or leave a wrong claim standing "with a
   caveat."

## Still open from the 2026-11-11 audit

Andy's specific ask (`AGENT_LOG.md`, both repos, 2026-11-11) — have
DeepSeek walk through, using the real evidence above (not a restatement of
the spec), **why** the live C5/re-arm fix and the CME reachability/
target/stop math actually match what was measured, not just confirm that
they do. That explanation hasn't been written yet. This protocol is the
"how to check it" half of that ask; the "here's why it's true" walkthrough
is a separate, still-pending piece of work for whichever agent picks it up
next — append it to this file as a new section, or to the audit doc,
rather than starting a third document that duplicates this one's map.
