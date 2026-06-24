# Kabroda Battle-Test Harness

Read-only analysis tooling for the Kabroda trading system.

**Hard wall: this harness has no write path to any live config, tuning parameter,
or live trading column.** The only exception is the forward-audit loop infrastructure
(`session_audit_log`, `trials_log`) — those are audit tables, not live config.
No FK to `session_locks`. No UPDATE of any live column. The audit tables describe
what happened; they do not change what the system does.

---

## The two rules (stamped on every output)

1. **Every number reports its N.** No percentage is ever stated without its sample
   count beside it. `pct_with_n()` in `tier_labels.py` is the only function that
   formats a rate — structurally impossible to emit a bare percentage.

2. **Backtest ranks candidates; forward log promotes them.** Nothing computed here
   changes a live parameter. Output is a ranked candidate list. Promotion happens
   only after a candidate proves out on forward sessions. The harness has no write
   path to live config.

---

## Four-tier label system

Labels are assigned per-cell based on **that cell's own N**, not the overall stream N.
A TANGLED subgroup at N=8 is DIRECTIONAL_OBSERVATION even when the stream reaches N=50.

| Tier | N range | Statistical test | Interpretation |
|---|---|---|---|
| DIRECTIONAL_OBSERVATION | N < 30 | None | Result in expected direction; may be noise |
| PRELIMINARY_SIGNAL | N 30–49 | Binomial test runs | p-value reported; not yet at conventional significance |
| PROVISIONAL_FINDING | N 50–99 | Significant at threshold | Multi-regime validation not yet available |
| VALIDATED_EDGE | N 100+ | Full suite available | Walk-forward and regime segmentation can run |

---

## What's built

| File | What it does | Status |
|---|---|---|
| `query_layer.py` | Production PostgreSQL read-only connection + canonical dataset fetch | Built |
| `join_logic.py` | Assembles approved-stream and stand-down-stream event dicts with indicator readings | Built |
| `baseline.py` | Data-collection-mode snapshot — N on every cell, four-tier labels, FLAG block | Built, runs now |
| `tier_labels.py` | Four-tier label logic; `pct_with_n()` formatting; shared by all modules | Built |
| `binomial_checkpoint.py` | N-milestone statistical tests (30/50/100); logs each run to `trials_log` | Built |
| `snapshot_report.py` | FLAG block detection; reads from `session_audit_log` and `trials_log` | Built |
| `audit_writer.py` | `write_decision_record()` + `backfill_outcome()` — writes to `session_audit_log` only | Built |
| `deferred_tests.py` | Stubs for all N-gated tests with gates documented | Stubs only |

---

## What's stubbed (do not implement until N gate is reached)

| Test | Function | N gate | What it does |
|---|---|---|---|
| Ablation suite | `deferred_tests.ablation_suite()` | 50+ resolved | Remove each indicator one at a time; report accuracy delta |
| Parameter sweep | `deferred_tests.parameter_sweep()` | 100+ resolved | Sweep box_floor_pct range; report curve shape (not a winner) |
| Scenario scoreboard | `deferred_tests.scenario_scoreboard()` | 200+ resolved | Score named parameter configs head-to-head |
| Walk-forward IS/WFA/OOS | `deferred_tests.walk_forward()` | 70+ resolved | Anchored WFA from 2026-05-27 origin; 4-week OOS steps; WFE ratio |
| Regime segmentation | `deferred_tests.regime_segment()` | 50+ resolved | Split by Elliott Wave regime; flag if outperformance concentrates in one regime |
| Plateau test | `deferred_tests.plateau_test()` | 100+ resolved AND active candidate | Test ±10% perturbations on candidate config; report curve shape |

"Resolved" = canonical APPROVED (CLOSED_WIN or CLOSED_LOSS) + scoreable stand-downs
(outcome_direction_correct populated).

**Architecture decision locked (walk-forward):** anchored IS window is the correct
choice for this system. Once-per-day systems accumulate data slowly — a rolling
window that drops early sessions throws away evidence. Implement anchored when gate
is reached; no re-approval needed.

---

## Write exception #1 — Forward-audit loop

`audit_writer.py` writes to two tables:
- `session_audit_log` — one row per MAS decision; frozen inputs captured at decision time
- `trials_log` — one row per backtest/checkpoint/replay

Neither table has any connection to live trading config. They describe system behavior;
they do not influence it. `audit_writer.py` is called from production code
(`kabroda_mas_flow.py`, `ledger_closing_engine.py`) with try/except wrapping — a
failed audit write never blocks or alters a trade decision or close.

---

## Write exception #2 — Intraday session monitor

`session_monitor.py` (not a harness file — lives in project root) writes to:
- `monitor_event_log` — one row per 15-minute poll during the active session window
- `monitor_config` — configuration and notification gate state (read-only from here)

Hard wall remains intact: `session_monitor.py` has no FK to `session_locks` or
`campaign_logs`. It does not update any live column. Every write is wrapped in
try/except — a failed poll row never stops the monitor loop or affects any trade path.

The monitor is observe-and-log only in v1. Notifications are built but disabled.
Three gates must simultaneously clear before notifications can fire:
- **Gate A**: 30+ resolved-session transition events (evidence threshold)
- **Gate B**: human harness review confirms signal plausibility
- **Gate C**: explicit `monitor_config.notification_enabled` flip by a human

The monitor cannot enable itself. `session_audit_log.micro_state_lock` (added in
Phase C) allows condition re-derivation to use the exact micro_state that was active
at lock time, rather than the energy_status proxy used in v1 for older rows.

---

## Database schema dependencies

The harness reads (all via `database.py` ORM models):
- `campaign_logs` — filtered to `is_canonical=TRUE`
- `decision_journal` — filtered to `source='mas_flow'`
- `jewel_snapshot_log` — filtered to `session_label='NY_OPEN'`
- `session_audit_log` — forward-audit records (flag detection reads label_tier)
- `trials_log` — comparisons-evaluated counter

Join keys:
- campaign → decision_journal: `(symbol, date_key == session_date)`
- campaign → JEWEL: calendar date from `jewel_snapshot_log.timestamp`

---

## How to run

**Requires: Render Shell** (or any environment with `DATABASE_URL` pointing to
production PostgreSQL). Local `kabroda.db` (SQLite) has 0 campaign_logs — the
production connection check in `query_layer.py` raises an error if SQLite is detected.

```bash
# From the Render Shell, project root:
python harness/baseline.py
```

Re-run weekly. The output is timestamped. N climbs; tier labels upgrade automatically
at 30, 50, and 100.

---

## Trials counter

Every replay, parameter sweep, or binomial checkpoint run is logged to `trials_log`.
The cumulative count (`SELECT COUNT(*) WHERE against_n <= current_n`) is the
"comparisons spent" figure. When this count exceeds 20 against the same dataset,
any new result requires Bonferroni correction (`p < 0.05 / count`) before acting.
The snapshot FLAG block reports this count and triggers the Bonferroni note when needed.
