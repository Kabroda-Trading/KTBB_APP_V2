# V2 Crown Retirement — Dependency Map (2026-09-23)

**Status:** planning artifact, not yet executed. Written to protect Andy's explicit rule
("we wouldn't want to remove anything that is being run through V2 but then being fed
into the Traveler system") before any removal happens. Produced by an overview-level
Explore-agent sweep of the whole site repo, cross-checked by CC. Citations are
`file:line` where pinned; a bare filename means the whole file was read.

**Why this exists:** Andy ruled (Brain repo `AGENT_LOG.md`, 2026-09-23 08:40 CT) to
retire V2 Crown (`decision_engine.py`'s gate) entirely and make the Traveler
(`gate_traveler.py`/`MGMT_E1_STACK`) the sole live decision system, then expanded that
into a full strategic site audit (08:47 CT). This doc is the dependency map that audit
has to respect — what's safe to remove, what must stay, what's shared.

---

## The two-phase insight

The dependency sweep found something that changes how this should be sequenced:
**V2 can already be turned off operationally, right now, with zero code deletion.**
`/api/executor/accounts/{id}/profile` (`main.py:1640`) sets `gate_profile`/`mgmt_profile`
per account — flipping an account to `GATE_TRAVELER`/`MGMT_E1_STACK` is already a live,
reversible, tested mechanism (`executor_engine.py::_process_account()` short-circuits
immediately for any non-`GATE_V2` account, `executor_engine.py:61-62`). This means the
retirement doesn't have to be one irreversible code-deletion event:

- **Phase A (operational, near-zero risk, reversible in one click):** confirm no
  account is left on `GATE_V2`/`MGMT_SPLIT`; the live radar's V2 gate keeps running
  (it's display/logging, not order placement, unless an account is LIVE+V2) but nothing
  trades on it.
- **Phase B (the real cleanup, sequenced carefully, per the map below):** the actual
  code/table/route/template removal, radar rebuild around traveler comms, and whole-site
  documentation pass Andy asked for.

---

## 1. V2-ONLY — removal candidates (Phase B)

**Core decision files** (zero Traveler import anywhere, grepped repo-wide):
`decision_engine.py`, `htf_fuel.py`, `market_regime.py`, `micro_regime.py`,
`reachability.py`, `fuel_gate.py` (already dead — zero live callers), `stop_planner.py`
(`plan_stop()` has zero call sites; `rr_floor_ok()` is only called from `trade_plan.py`,
so the module is V2-only in practice), `trade_plan.py`, `trade_plan_engine.py`,
`trade_plan_notify.py`, `executor_live_engine.py`, `dry_run_split_engine.py`,
`mgmt_split_dry_run.py` (own header: *"GATE_TRAVELER's own DRY_RUN walk is
traveler_plan_engine.py's job, not this file's"*), `ledger_closing_engine.py`
(the deprecated 30/70 `CampaignLog` shadow sim), `market_radar.py` (the backend module —
calls `decision_engine.evaluate_15m_decision()` fresh every poll).

**DB tables:** `TradePlan`, `CampaignLog`, `GateLog`, `DecisionJournal`,
`SessionAuditLog`, `DecisionGaugeReading`, `DecisionLog`, `AuditSuggestionLog` (fed
exclusively from `CampaignLog`-based analysis). Zero references to any of these in
`gate_traveler.py`/`traveler_plan_engine.py`/`traveler_plan_notify.py`/
`mgmt_e1_stack.py`/`executor_live_e1_engine.py` (repo-wide grep, confirmed).

**Support files:** `harness/audit_writer.py`, `harness/unified_audit_writer.py` — called
only from `kabroda_mas_flow.py`'s V2 branch, never the Traveler injection block.

**Background tasks** (`main.py`'s `lifespan()`): `trade_plan_task` (`main.py:623`),
`dry_run_split_task` (`main.py:625`), `executor_live_task` (`main.py:628`),
`ledger_task` (`main.py:622`).

**Routes:** `/api/admin/trade-plan-status` (`main.py:1187`), `/api/radar/snapshot`
(`main.py:927`), `/api/export/gate-log.csv` (`main.py:2438`),
`/admin/export-audit-ledger` (`main.py:2581-2608`), `/api/v1/system/trades`
(`main.py:3314-3337`). The wider `/api/v1/system/*`/`/api/dashboard/*` family is very
likely all `CampaignLog`-based too but **needs a route-by-route grep before removal**
(not individually verified this pass — see §4).

**Template:** within `templates/market_radar.html`: the `#planStatePanel` panel body,
`renderPlanState()`/`pollPlanState()` JS (`market_radar.html:1452-1531`), the main
"TARGET LOCK" gate/levels readout fed by `/api/radar/snapshot`.

## 2. TRAVELER-ONLY — keep, not in scope for removal

`gate_traveler.py`, `traveler_plan_engine.py`, `traveler_plan_notify.py`
(zero cross-import with `trade_plan_notify.py` — confirmed by reading both fully),
`mgmt_e1_stack.py`, `executor_live_e1_engine.py`. `TravelerPlan` table.
`traveler_plan_task`/`executor_live_e1_task` (`main.py:624,626`).
`/api/admin/traveler-plan-status` (`main.py:1234`). Within `market_radar.html`:
`#travelerStatePanel`'s container CSS and `renderTravelerState()`/`pollTravelerState()`
JS bodies — but see §3 for what these two panels actually share.

## 3. SHARED / PROTECTED — do not remove, this is the bucket Andy is worried about

**The session/level SSOT both systems are fed from:**
- `session_manager.py` — pure calendar/anchor math, not gate-specific at all.
- `sse_engine.py` — computes `breakout_trigger`/`breakdown_trigger`/`range30m_high/low`/
  `f24_*`/ATR. `gate_traveler.py:42-44,61-62` explicitly reuses `decision_engine.py`'s
  own `STOP_BUFFER_BOX=0.12`/`T1_BOX=1.0` constants and the same bo/bd/r30 numbers.
  **The locks/session-anchor math is not "V2 code" — it's the whole site's SSOT. Do not
  touch it as part of a "V2 cleanup."**
- `battlebox_pipeline.py` — the whole lock pipeline. `battlebox_pipeline.py:711-719`
  fires `kabroda_mas_flow.run_mas_analysis()` on every fresh lock — the single trigger
  point for BOTH `TradePlan` and `TravelerPlan` creation. The RSI-at-lock capture
  (`battlebox_pipeline.py:637-657`) is dual-purpose: V2's gate reads it directly, and
  `TravelerPlan` stores a copy (audit/display only) — the Traveler's own real gate input
  is a *different* value, `rsi_4h_at_cross` (`gate_traveler.py:79-121`, computed fresh
  from raw 4H candles at the actual cross, not the frozen lock read). **Easy to conflate
  — verify with a fresh grep at removal time, don't assume this summary is still current
  by then.**
- `kabroda_mas_flow.py::run_mas_analysis()` — **the single shared spine.** Fetches
  candles once, then branches: writes `CampaignLog`/`GateLog`/`TradePlan` (V2 sub-path,
  `kabroda_mas_flow.py:180-236,257-455`) AND independently writes `TravelerPlan` from the
  SAME `bo`/`bd`/`r30_high`/`r30_low`/`rsi_4h_at_lock` locals
  (`kabroda_mas_flow.py:246-255,669-730`). **This file cannot be deleted or have its V2
  half stripped without surgery** — the candle-fetch/`levels` prep at the top
  (`kabroda_mas_flow.py:142-178`) is load-bearing for the Traveler too.
- `main.py`'s Senior Analyst scheduler (`main.py:136-295`, task at `main.py:629`) — a
  restart-recovery safety net that still needs to fire the lock (and therefore
  `TravelerPlan`) even if nobody hit the live radar page. Its own dedup check reads
  `CampaignLog.is_canonical` (`main.py:153-160,251-255`) — **if `CampaignLog` is removed,
  this needs rewiring to a different dedup signal (e.g. `SessionLock` existence), not
  deletion of the task itself.**

**The executor stack — one shared account/order/audit layer, two thin decision-producer entry points:**
- `executor_accounts.py`, `executor_control.py` — 100% shared, zero lineage branching.
- `executor_engine.py` — one file, two clean parallel halves (`process_fill()`/
  `_process_account()` for V2, `process_traveler_fill()`/`_process_traveler_account()`
  for the Traveler), both calling the same shared helpers below.
- `executor_plan_builder.py` — `build_hypothetical_order()` (V2) and
  `build_hypothetical_traveler_order()` (Traveler) both funnel into the same
  `_size_and_check_order()`/margin-query core.
- `executor_sizing.py` — shared (`compute_qty`, `banded_risk`, liquidation/leverage
  checks); only `f_a_multiplier()` is Traveler-specific, a small carve-out, not a reason
  to touch the file.
- `executor_bitunix_client.py` — pure exchange client, used by both live engines.
- **`ExecutorOrder` table** — one table, `trade_plan_id` (required) AND
  `traveler_plan_id` (nullable) columns side by side (`database.py:2304-2309`).
  **Cannot be dropped or stripped — the Traveler's own real orders live in these same rows.**
- `ExecutorAccount`/`ExecutorRiskState`/`ExecutorSizingPolicy`/`ExecutorGlobalConfig`/
  `ExecutorMechanismTest`/`ExecutorAuditLog` tables — all shared.
- Every `/api/executor/*` route (`main.py:1511-2325`) — shared, including the profile
  switch that IS the operational retirement mechanism (§ above).
- `templates/executor_admin.html` — one shared admin page, not split by lineage.
- `notify.py` — shared email transport (separate content builders per lineage).

**`market_radar.html` is genuinely one shared template, confirmed, not two separate
pages that happen to share a URL:**
- `.plan-state-badge` CSS class is used verbatim by BOTH panels' JS
  (`market_radar.html:1458` and `:1559` — identical `className` assignment). The file's
  own comment even says so: *"st-filled/st-done above are shared with TradePlan
  already."*
- `_fmtLevel()`/`_parseServerTimestamp()` JS helpers are called by both
  `renderPlanState()` and `renderTravelerState()`. **Deleting these while "cleaning up
  the v2 panel" would break the Traveler panel's own rendering too.**

**The Gravity Map is confirmed independent of both systems** (`gravity_engine.py`/
`kabroda_macro_engine.py`/`gravity_math.py`) — neither gate reads it as a decision input.
`battlebox_pipeline.py` (shared, must stay) is what feeds it, one-way. Safe either way —
V2 removal doesn't touch it, and it was never a V2 dependency to begin with. One stale
comment found (`gravity_engine.py:16-18` mentions `CampaignLog` — the actual import was
removed 2026-09-07, the file touches nothing V2-related; comment-only, harmless, worth a
one-line fix whenever that file is next touched).

## 4. Needs a deeper, dedicated check before removal (not resolved by this overview pass)

- **`/api/v1/system/*` and `/api/dashboard/*` route family** — pattern strongly suggests
  all `CampaignLog`/legacy-dashboard-only, but each route needs its own grep before
  deletion, not an assumption from the pattern.
- **No Traveler-side bulk export exists** analogous to `GET /api/export/gate-log.csv` —
  only `/api/admin/traveler-plan-status`, which returns the single latest row for the
  live radar panel, not a historical export. If the Brain is meant to audit the
  Traveler's forward performance the way it does V2's today, this export doesn't exist
  yet. Worth raising with Andy/DeepSeek — may be a real, separate gap the retirement
  should not paper over.
- **`executor_mechanism_test.py`** — a standalone manual admin diagnostic
  (`/api/executor/accounts/{id}/tiny-test/*`), not invoked by either automated engine.
  Its order shape (concurrent T1+T3 limits, partial close, BE-move) is SPLIT/V2-flavored
  with no Traveler analog exercised anywhere. Not decision-path-coupled, so V2 removal
  doesn't obsolete it structurally — but worth an explicit conversation with Andy about
  whether it should stay, get an E1-shaped counterpart, or be retired alongside V2.
- **`session_monitor.py`** and the weekly scheduler (`main.py:632`, both its real actions
  already removed, now a no-op loop) — independent of both lineages, out of scope for
  this specific audit, flagged only so they're not mistaken for V2/Traveler-specific
  later.

---

## Sourcing

Overview-level Explore-agent sweep of the site repo, 2026-09-23 — read every file named
above plus a repo-wide grep pass for cross-references; citations are `file:line` where
pinned. This is a first-pass map for planning, not a substitute for re-verifying at the
moment each specific removal actually happens (see the RSI dual-capture and
`CampaignLog.is_canonical` caveats above especially — both are the kind of detail that's
easy to get right today and silently break in three weeks).
