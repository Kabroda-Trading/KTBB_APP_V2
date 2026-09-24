# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Strategic Direction (2026-09-23, current — read this before anything else)

**The Traveler (`GATE_TRAVELER`/`MGMT_E1_STACK`) is becoming Kabroda's sole live decision system. V2 Crown (`GATE_V2`/`MGMT_SPLIT` — `decision_engine.py`'s gate) is authorized for retirement.** Andy's ruling, `Kabroda AI Brain` repo `AGENT_LOG.md` 2026-09-23 08:40 CT, expanded into a full strategic site audit at 08:47 CT the same day. This is not a guess or a preference — it's measured: on the identical 5-year corpus (`locks_2021_2026.csv`, 2,056 locks), V2 Crown's extra selectivity (Krown Cross votes==2, RSI-at-lock zone) takes fewer trades at a *lower* avgR than the simpler v3 baseline it descends from (202 trades/+0.292R vs 601 trades/+0.35R), and the Traveler's own trade set contains essentially all of both within their shared date range (v3: 502/601 = 84%, all 99 "missing" predate the Traveler's 2022-05-17 corpus start; V2 Crown: 160/202 = 79%, same explanation) — independently re-verified from raw committed data, not taken on the log's word (`AGENT_LOG.md` 2026-09-23, both repos). A separately-routed measurement (should V2's runner pull early on the same exhaustion signal the Traveler uses?) also came back a coin-flip and stays out, same P4 precedent. None of this indicts V2 Crown as broken — it just never earned its added complexity once measured head-to-head against what replaced it.

**What this means in practice, right now:**
- Do not build new V2 Crown features, retune its gate constants, or treat "The Calibrated Gate" section below as describing the strategic future — it describes what's still *live in code today*, not where this is going. It stays accurate as a description of current behavior until Phase B (below) actually removes it; do not let it silently go stale the way it already did once (see that section's own 2026-09-22 correction note) — if V2 code changes during the retirement, this file changes the same day.
- **Retirement is two phases, not one event.** Phase A (operational, already fully built, reversible in one click): confirm no `ExecutorAccount` is left on `GATE_V2`/`MGMT_SPLIT` — the `/api/executor/accounts/{id}/profile` switch (`main.py:1640`) already lets any account flip to `GATE_TRAVELER`/`MGMT_E1_STACK` with zero code change. Phase B (the real cleanup): actual code/table/route/template removal, the radar rebuilt around Traveler communication (copy-paste buttons mirroring Traveler plan fields, email content mirrored on the radar, executor toggle flows), and a whole-site cleanup pass — sequenced carefully against `V2_RETIREMENT_MAP.md` (repo root), documenting as it goes per Andy's explicit instruction, so nothing gets removed that the Traveler still depends on.
- **`V2_RETIREMENT_MAP.md` is the load-bearing reference for Phase B** — a from-source dependency map classifying every file/table/route/template as V2-only (removal candidate), Traveler-only (keep), or shared/protected (do NOT remove — this is the bucket Andy explicitly warned about: "we wouldn't want to remove anything that is being run through V2 but then being fed into the Traveler system"). The shared list includes things that look V2-adjacent but aren't: `session_manager.py`/`sse_engine.py`/`battlebox_pipeline.py` (the session-lock SSOT both systems read from), `kabroda_mas_flow.py::run_mas_analysis()` (one function that writes both `TradePlan` AND `TravelerPlan`), the whole executor account/order/sizing stack (`ExecutorOrder` has both a `trade_plan_id` and a `traveler_plan_id` column on the same table), and `templates/market_radar.html` (one template, one shared CSS badge class and shared JS formatting helpers used by both panels). Re-verify against current source before removing anything from the V2-only list — that map is a planning snapshot, not a standing guarantee.
- Standing traveler work orders are not blocked by any of this and should proceed normally — none of them touch V2's file set (confirmed 2026-09-23, `AGENT_LOG.md` both repos).

---

## Reading DeepSeek/Antigravity's Conversation History

The user also works on this project through Antigravity (running DeepSeek), a separate agent from Claude Code. That agent's conversations persist permanently to disk as JSONL transcripts, and Claude Code can read them directly to get up to speed on work that happened outside this session — no need to ask the user to re-explain what DeepSeek already did.

**Where:** `C:\Users\Shadow\.gemini\antigravity\brain\<conversationId>\.system_generated\logs\transcript_full.jsonl` — one JSON object per line, fields include `step_index`, `source` (`USER_EXPLICIT`/`SYSTEM`/`MODEL`), `type` (`USER_INPUT`/`CONVERSATION_HISTORY`/`PLANNER_RESPONSE`), `created_at`, and `content`. User text is wrapped in `<USER_REQUEST>...</USER_REQUEST>` tags. DeepSeek's real prose responses have `source: "MODEL"` with non-empty `content`; most other MODEL entries are tool-call output only (often large — file dumps, bash output) and are low signal for understanding intent.

**Finding the right conversation:** `C:\Users\Shadow\Workspace\claude-antigravity-bridge\state\registry.json` maps `conversationId` → `{transcriptPath, workspacePaths, modelName, lastSeenAt}`. Match on `workspacePaths` containing this project's path to find the relevant conversation(s).

**How to actually do this well:** these transcripts get large (multi-MB, thousands of entries) and are dominated by tool-call noise. Don't try to read one raw start-to-finish. Dispatch a subagent (Explore or general-purpose) with a narrow brief: read the transcript alongside this project's own docs (`WORK_LOG.md`, `SYSTEM_FLOW.md`, `CC_HANDOFF.md`), and produce a structured report — what was built, why, what's still open, what looks off. This worked well in practice (2026-08-06): a subagent read `WORK_LOG.md` and `SYSTEM_FLOW.md` in full plus a sampled pass over the transcript, and surfaced a real, load-bearing finding neither doc stated explicitly: both governing docs had stopped being updated on 2026-07-16, right as a second track of undocumented work (`bold-hubble/kqal`) went live in production.

**A known failure mode to watch for:** DeepSeek has, at least once, invoked a headless Claude Code session mid-conversation and then written the resulting content into an Antigravity-internal artifact copy (`C:\Users\Shadow\.gemini\antigravity\brain\<conversationId>\.system_generated\...`) instead of the real project file. If a user references a finding or handoff doc that doesn't match what's actually in the repo, check whether it landed in the right place before assuming the work wasn't done — search the brain folder, not just this project directory.

---

## Running the App

```bash
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Production deploys to Render at kabroda.com on port 10000. `pytest tests/` is a real, growing suite (170+ tests as of 2026-08-31) — run it, plus a live server + real routes for anything touching live behavior. (This line used to say "there is no test suite" — stale; keep it truthful as the suite grows.)

## Required Environment Variables

```
ANTHROPIC_API_KEY     # Powers all 6 CrewAI agents and the Operator Commlink
SESSION_SECRET        # Cookie signing key
DATABASE_URL          # Default: sqlite:///./kabroda.db (prod: PostgreSQL)
PUBLIC_BASE_URL       # Used to auto-detect HTTPS for secure cookies
ADMIN_EMAIL           # Bootstrap admin on first boot
ADMIN_PASSWORD        # Bootstrap admin password
COINALYZE_API_KEY     # Optional — open interest fuel multiplier
GATE_LOG_EXPORT_API_KEY  # Optional — required only to use GET /api/export/gate-log.csv
                         # (the Kabroda AI Brain's SS9 forward-test log pull; X-API-Key header)
```

---

## What This System Actually Does

Kabroda is a session-based crypto trading intelligence system. It does not give generic buy/sell signals. It mathematically derives a "battle zone" at the start of each trading session, then monitors whether price has earned permission to trade out of it.

### The Core Concept: Session Anchors and Triggers

At the open of each trading session (defined in `session_manager.py`), the system enters a **30-minute calibration window**. During this window, the highest high and lowest low form the **30M Range** (`r30_high`, `r30_low`). These bounds are the raw material for trigger calculation.

After 30 minutes, `sse_engine.py` computes two permanent levels for the session:

- **Breakout Trigger (`bo`)** — the price where a confirmed long trade becomes valid. Derived from `max(r30_high, 24h TPO Value Area High)`, then pushed a minimum distance from the anchor to prevent false triggers.
- **Breakdown Trigger (`bd`)** — the price where a confirmed short trade becomes valid. Derived from `min(r30_low, 24h TPO Value Area Low)`, same logic inverted.

**The 24h value area is time-based (TPO), not volume-based (VRVP), as of 2026-08-30.** `sse_engine.py`'s `_calculate_tpo_value_area()` counts bars *touching* each price row over the trailing 24h — Steidlmayer's original Market Profile method — instead of accumulating volume per row. This drops the exchange volume-feed dependency entirely. Validated in the `Kabroda AI Brain` repo (`KABRODA_REBUILD_SPEC.md` §10, `LEVEL_METHODOLOGY.md`, `compare_levels.py`) against kabroda.com's own 123 real VRVP locks: 88% same-side, 78% same-outcome, 1.00x median box ratio — the two methods pick essentially the same levels, so the swap is a reliability upgrade (no volume-feed dependency), not a strategy change. The output field names (`f24_poc`/`f24_vah`/`f24_val`) are unchanged; only how they're computed changed. `sse_engine.py`'s port of the algorithm (`_calculate_tpo_value_area`) is verified bit-for-bit identical to Brain's `brain/engine/repro_levels.py::_tpo_value_area()` on randomized synthetic data — do not let the two drift; if Brain's algorithm changes, port the change here too.

These two triggers are the **Single Source of Truth (SSOT)** for the entire session. They are frozen into a `SessionLock` database record and never recomputed. Every downstream calculation — targets, stops, the calibrated gate — derives from them.

### The Calibrated Gate — v1 (2026-08-30 → 2026-09-10, RETIRED) then v2 (rebuilt 2026-09-11, CURRENT, itself authorized for retirement 2026-09-23 — see "Strategic Direction" at the top of this file)

**This whole section describes what's still live in code today, not the strategic direction.** V2 Crown (everything below) is authorized for retirement in favor of the Traveler — see the top of this file and `V2_RETIREMENT_MAP.md` before building anything new on top of this gate.

The site's original target formula (1×/1.618×/2.618× of the bo–bd distance, "Measured Move") was never backtested against real outcomes at scale, and when it finally was — a 1,913-trigger-break backtest, 2021–2026, `Kabroda AI Brain` repo — it lost money on kabroda.com's own real filled trades (71 trades, 29.8% win, −0.30R avg, −21.4R total). Andy authorized a full replacement, not a patch.

**This section was stale for 11 days (2026-09-11 → 2026-09-22) before this correction** — it kept describing v1 (below) as current after v2 had already shipped and a real production bug from the gap had already been found and fixed (the radar silently displayed every real TAKE as PASS/gray/inactionable from 2026-09-11 to 2026-09-15, because the UI still checked for the retired `TAKE_PREMIUM`/`TAKE_STANDARD` strings — site commit `df75f3c`). Flagged and fixed 2026-09-22 while writing the v2 D1/D2/D3 reference doc for DeepSeek (`Kabroda AI Brain` repo, `V2_D1_D2_D3_MECHANICS.md`) — this is exactly the "constant drift" failure mode this file exists to prevent; if you find another section like this, fix it the same way: correct the doc, log the correction, don't just work around the staleness silently.

**v1 (2026-08-30, tier logic rebuilt 2026-09-06) — RETIRED 2026-09-11, kept here only as history.** Fuel-based (`fuel_gate.py`, push volume ≥0.8× a 24h baseline) with a PREMIUM/STANDARD tier split (`PREMIUM`: fuel FUELED + both HTF timeframes + box/ATR≤0.40; `STANDARD`: `STANDARD_FUEL_RATIO_FLOOR`=1.1×), a `PROMOTED_PUSH_FLOOR`=1.8 on the NO_PLAN-promotion path, a dead-tape/live-hour veto, T1/T2/T3 = trigger ± 0.618×/1.0×/1.618×box, and three outcomes (`TAKE_PREMIUM`/`TAKE_STANDARD`/`PASS`). **Retired wholesale, not patched**, per `decision_engine.py`'s own header comment: v1's fuel signal was found to be measured using ~60 minutes of data AFTER the entry fill — not decision-time computable at all — so every number built on it (the tier split, both floors) inherited the same defect. Full numbers/evidence trail for this retired system: `Kabroda AI Brain` repo, `GATE_REBUILD_SPEC.md`, `AGENT_LOG.md` 2026-09-06 through 2026-09-10, git history of this file before 2026-09-22.

**v2 (rebuilt 2026-09-11, site commit `90c09e2`, CURRENT) — Krown Cross + 4H RSI.** Source of truth: `CC_PACKAGE.md` (§1) and `CANON.md` §8 in the `Kabroda AI Brain` repo, Andy's direct lock ("lock it for cc to look at it", 2026-09-11 22:29 CT). Live implementation: `decision_engine.py`, `reachability.py`, `htf_fuel.py`, `market_regime.py`, `micro_regime.py`. `fuel_gate.py` is untouched but **no longer a decision input** — same "real tool, not wired in" treatment as the Gravity Map, kept only in case something else reads it.

**The gate.** Evaluated once, on the first 5m close beyond BO or BD. FOUR conditions, all must pass, no partial credit and no tier:

1. **Reachability** — `box / dailyATR14 ≤ 0.55` (`box = bo − bd`, `reachability.py`, unchanged since 2026-08-30). The single strongest signal in the original backtest; still the one v2 condition fully knowable at the lock (box and ATR are both frozen then).
2. **HTF aligned ≥ 1** — the original 9/21 EMA read (`htf_fuel.py::timeframe_trend`), ≥1 of {1H, 4H} backs the side. Pre-existing, carried over from v1 unchanged — and load-bearing on its own, not redundant with condition 3 below (the two EMA pairs can and do disagree on some crosses).
3. **Krown Cross votes == 2** — a SEPARATE, stricter condition (`htf_fuel.py::krown_cross_votes`): a 21/55 EMA stack + 6-bar fast-EMA slope, and BOTH 1H and 4H must agree with the trade side (no partial credit, votes must be 2 of 2).
4. **4H RSI(14) Wilder in the control zone, frozen at the 13:00 UTC lock** (`decision_engine.py`: `RSI_ZONE_LONG = (62, 80)`, `RSI_ZONE_SHORT = (20, 38)`) — evaluated once at lock via `battlebox_pipeline.py`, **not recomputed at cross/fill time**. Deliberate asymmetry: Krown Cross/HTF-aligned use "live" candles at cross time, RSI uses the frozen lock read. Do not "simplify" this into a fresh at-cross RSI computation — that changes what was actually measured (`n=136`, avgR +0.5216 leg-1 / +0.6709 with SPLIT management, all 5 years positive, CC-reproduced by directly running the measurement scripts, not just reading a printout).

**What v1 had that v2 drops entirely** (measured out, not just unused): fuel/push-volume as a decision input, the PREMIUM/STANDARD tier split (`STANDARD_FUEL_RATIO_FLOOR`, `PROMOTED_PUSH_FLOOR` — both gone, the constants no longer exist in `decision_engine.py`), and the dead-hour/dead-tape/counter-trend veto stack (measured against the real v2 candidate 2026-09-11, `brain/audit_evidence/d0_veto_stack_on_candidate.py`: counter-trend never fires on this population at all, dead-hour has only 4 samples — too few to read either way, dead-tape vetoes 26 real trades averaging a still-positive +0.35R to raise the kept population's avgR from +0.52 to +0.57, a real but modest tradeoff Andy chose to skip rather than keep for marginal gain). `market_regime.py`/`micro_regime.py` are still computed and surfaced on the decision dict for display/diagnostic purposes — same "real tool, not a decision input" treatment as the Gravity Map — they just no longer veto anything.

**Targets** — box multiples, no gravity dependency, no tier dependency:

```
box = breakout_trigger − breakdown_trigger

Entry = the trigger itself (bo for long, bd for short)
T1 = trigger ± 1.0×box     (moved from 0.618× 2026-09-11 -- measured better on both legs of SPLIT management)
T2 = trigger ± 1.0×box     (== T1's own price; kept as a key only for schema/display back-compat, not a distinct management trigger any more)
T3 = trigger ± 1.618×box   (unchanged)
```

**Stop — ONE formula, for every trade, no tier distinction.** The old "three distinct roles for the r30/zone formulas" split (a risk-basis stop plus a separate PREMIUM zone-stop) is gone along with the tier system: `stop_planner.py::plan_stop()` (the 24h core-zone stop) **has no live call site left at all** (`trade_plan.py` says so explicitly in its own comments) — kept in the file, unused, same "real tool, not wired in" treatment as `fuel_gate.py`. Every trade, every tier-that-no-longer-exists, uses:

```
stop = r30_low − 0.12×box   (long)
stop = r30_high + 0.12×box  (short)
```

(`decision_engine.py`, `STOP_BUFFER_BOX = 0.12` — the same constant, same formula, that used to be only the R-bookkeeping basis before 2026-09-08 and then STANDARD-only before 2026-09-11). This is both the R-multiple bookkeeping basis AND the real order stop sent to the exchange (`set_position_tpsl`, `executor_live_engine.py`), for every trade.

**Management — SPLIT 50/50, stop never moves, for anyone, at any point.** 50% off at T1 (`1.0×box`), stop stays at the original level. The other 50% (the runner) rides to T3 (`1.618×box`) or that same original stop — it never moves, not even at T2 (there is no T2 mechanically distinct from T1 any more). This replaced v1's PREMIUM-only mechanical breakeven-move-at-T2 (verified against a real 31-trade PREMIUM corpus: 13 touched T2, 10 rode on to T3 at zero cost, 3 stopped after T2 for +1.50R saved) — that mechanism was **confirmed dead code once the tier split was retired** (`order_row.tier` can never be `"PREMIUM"` under v2's gate) and **deleted outright, not left dormant**, per Andy/DeepSeek's explicit "delete outright" ruling (`CC_QUESTION_T2_BREAKEVEN.md`, Kabroda AI Brain repo, 2026-09-15; site commit `6a8f1b4`, 11 minutes after `df75f3c` above, same evening) — `CC_PACKAGE.md` §1 already states this as an already-measured result, not an open design question: *"stop never moves"* and, separately, *"NO BE stop (measured harmful)."* The `T1_FILLED_BE_PENDING`/`BE_MOVED` management_state values and the `t2_reval_fuel_verdict`/`t2_reval_micro_regime` observation columns went with it — a future T2-reval-observation feature, if ever wanted, is a fresh design decision, not a resurrection of this. `ledger_closing_engine.py`/`CampaignLog` still runs the OLD 30/70 rule as a labeled-deprecated shadow simulation only (radar-vs-backtest drift tracking) — never the real rule; see that file's own header, and note it hasn't been updated to even reflect the 2026-09-08 50/50 rule, let alone v2's stop-never-moves rule.

**One outcome now, not three: `TAKE` / `PASS`.** No PREMIUM, no STANDARD, no grades, no score — one population, sized and managed the same way for every trade (`executor_sizing.py`'s banded rule already didn't care about tier). The `ALMOST`/NEEDS-CONFIRMATION limbo state remains retired (removed 2026-09-06, folded into what's now just a wider single-population gate). This is what `decision_engine.evaluate_15m_decision()` returns, and it's the same function **three** real call sites all use — `run_mas_analysis()` (`kabroda_mas_flow.py:180`), the live radar's `market_radar.py::_build_dossier()` (`market_radar.py:261`), and `trade_plan_engine.py::_run_full_gate()` (`trade_plan_engine.py:145`, shared by the opposite-break enrichment and the anticipated-side cross-confirmation/NO_PLAN-promotion paths) — they can never silently disagree. (`market_radar.py`'s own comment at line 254-256 already flags this as "two (now three, with trade_plan_engine.py)" — this file's older "two places" framing was never updated to match; corrected here 2026-09-22.) Note for anything reading `GateLog`/`gate_log.csv` historically: the real column names are `gate_tier`, `fuel_state`, and `push_vol_ratio` (`database.py:1896-1908`) — kept for schema back-compat but always `None`/`NULL` on any row from a v2 decision (2026-09-11 onward); a row with a real tier or fuel value predates the rebuild. (`TradePlan` separately has its own `fuel_verdict` column, same always-`None`-now status, but it's a different table.)

Every gate evaluation, TAKE or PASS alike, is logged to the `gate_log` table (`database.py`) — this is the forward-incubation record the Kabroda AI Brain reads to confirm live results track the backtest.

**Division of labor (locked 2026-08-31, `Kabroda AI Brain` repo AGENT_LOG.md commit `c5487a6`): kabroda.com is the recorder, the Brain is the auditor.** The site writes complete `gate_log` rows (locks, plans, state transitions — mechanical facts only, including the additive TradePlan-execution columns backfilled by `_backfill_gate_log_execution()` in `ledger_closing_engine.py`) and exposes them via `GET /api/export/gate-log.csv` (`X-API-Key` header, `GATE_LOG_EXPORT_API_KEY` env var; `since`/`symbol` query params). The site does **not** run the monthly drift check, does **not** do plan-ID reconciliation, and does **not** compute `gate_log.pressure` or `gate_log.would_have_r` — those three are explicitly Brain-side (the Brain's own closure pass fills `pressure`/`would_have_r` directly into the same table). Displaying a drift verdict or running that analysis on the site would be a "new opinion" — exactly what SS5's state-machine design forbids.

### The Gravity Map

**As of 2026-08-30, this is a standalone reference page, not a decision input.** Andy's explicit call: gravity has real merit as a tool to look at, but it doesn't belong influencing the trade call. The calibrated gate's stop/target formula doesn't reference it at all. Kept exactly as described below — the computation is unchanged — just no longer wired into `decision_engine.py`.

The gravity system is a two-layer price memory model:

**Layer 1 — Macro Beams (Class 0, `permanence_class=0`)**: Multi-year Elliott Wave pivots mapped by `kabroda_macro_engine.py`. These are re-scanned on boot and every 24 hours. They carry a `heat_multiplier=15.0` and a `+15.0` KDE weight boost — the heaviest levels in the system. They represent structural cycle origins, wave tops, wave bottoms. In `gravity_math.py`, Class 0 levels receive a `+15.0` kinetic friction multiplier on top of their heat multiplier, making them massively visible in the density curve.

**Layer 2 — Kabroda Bedrock (Class 1/2)**: Intraday and session-level pivots logged by the gravity engine loop every 15 minutes. 4H pivots are Class 1 (`+3.0` KDE weight). Session-locked levels (triggers, daily S/R, 30m extremes) are Class 2 (`+1.5` weight via `7_DAY_KABRODA` source).

The `calculate_gravity_kde()` function transforms all stored pivots into a continuous Gaussian density wave (Bookmap-style). Each pivot emits a bell curve of influence with sigma = 15 bps of the mid-price. Overlapping pivots compound. The resulting peaks are the `kde_peaks` list injected into the MAS payload.

**Macro Fibs** (`calculate_macro_fibs()`): Separately derived from the 30-day daily swing high/low. Produces Fibonacci retracements (0.5, 0.618, 0.786) and extensions (1.272, 1.618, 2.0) in both directions for blue-sky breakout and price-discovery targets.

### The Macro Engine (Elliott Wave Scanner)

`kabroda_macro_engine.py` runs as a subprocess (not an asyncio task) on boot and every 24 hours. It:
1. Fetches up to 1500 days of daily candles for BTC, ETH, SOL from MEXC.
2. Runs a ZigZag pivot algorithm with 20% deviation threshold to strip noise.
3. Validates the resulting pivots against strict Elliott Wave rules (W4 cannot overlap W1 territory; W2 cannot break origin; etc.).
4. Writes confirmed wave levels (CYCLE_ORIGIN, BULL_WAVE_1 through _4, BEAR_WAVE_3_LOW, etc.) to `gravity_memory` as `permanence_class=0`.

These are the levels that create the heavy gravity walls the Liquidity Scavenger agent is trained to identify.

### Session AUTO Mode

`session_manager.resolve_current_session()` with `mode="AUTO"` is hardcoded to `us_ny_futures` (NY Futures, 8:30 AM ET). There is no dynamic session detection. Seven sessions are defined; manual override is passed via `manual_session_id` in the `/api/dmr/live` payload.

---

## The Decision Layer — What's Actually Live (no LLM agents anymore)

This section used to describe a 6-agent CrewAI/LLM crew (Macro Structural Architect, Micro Liquidity Scavenger, Kinematic Momentum Quant, Chief Risk Officer, Chief Content Officer, Intel Auditor). That crew was disabled 2026-08-17 (it was an LLM reading free text with no enforced precedence) and its replacement — a hand-coded graded-conviction model — was itself fully replaced 2026-08-30 by the calibrated gate described above. Both are gone from the decision path, not just superseded in spirit; the `crewai`/`langchain-anthropic` packages were removed from `requirements.txt` since nothing imports them anymore.

**What actually runs now, per 15M decision:**
- `decision_engine.evaluate_15m_decision()` — the calibrated gate. Deterministic, zero LLM calls, zero cost. See "The Calibrated Gate" above for the full logic.
- Called from **three** places that must never disagree (corrected 2026-09-22 — this used to say "two," missing the third; `market_radar.py`'s own code comment already flagged the gap): `kabroda_mas_flow.run_mas_analysis()` (fires at session lock, writes the official `CampaignLog`/`GateLog` record), `market_radar._build_dossier()` (the live public radar/API, recomputes fresh on every call), and `trade_plan_engine.py::_run_full_gate()` (shared by the opposite-break enrichment and the anticipated-side/NO_PLAN-promotion cross-confirmation paths).

**Intel Auditor** — removed 2026-08-30 (Andy's call: gone entirely). It used to take a foreign signal (MetaSignals format) and have an LLM compare it against Kabroda's SSOT — gravity walls as a BLOCKED/HIGH_RISK/CLEAR gate, plus a third, different measured-move formula. Both had gone stale under the calibrated-gate rebuild, and it was the last LLM-based tool left in the codebase (a paid `agent_core._call_agent()` call per use). `IntelAuditReport`, `INTEL_AUDITOR_SYSTEM_PROMPT`, `audit_foreign_intel_pipeline()` (`kabroda_mas_flow.py`), the `POST /api/research/audit-intel` route (`main.py`), and the "External Intel Injection" panel (`templates/macro_war_room.html`) are all gone, not archived.

---

## The Symbol Format Rule

**All DB operations must use `BTC/USDT` format (slash-separated).** Raw API inputs arrive as `BTCUSDT`. `_normalize_symbol()` in `battlebox_pipeline.py` converts them. Use it before any DB write or MAS trigger. The War Room normalizes via `.replace("USDT", "/USDT")`. Inconsistency here causes CampaignLog and SessionLock queries to silently miss — this was the original cause of the CCO brief stuck on PENDING.

The `gravity_memory` table is an exception: `kabroda_macro_engine.py` stores symbols as `BTCUSDT` (no slash), because it strips the slash via `.replace("/", "")`. The `calculate_gravity_kde()` function also strips the slash when querying. Do not change this — it is consistent within the gravity subsystem.

---

## CampaignLog Lifecycle

**This section describes retired V2 machinery, kept only as history.** `CampaignLog`, `ledger_closing_engine.py`, and `executor_live_engine.py` (Domain 2) are all deleted as of 2026-09-24 (V2 Crown retirement, Step 3f). The Traveler (`TravelerPlan`/`traveler_plan_engine.py`/`executor_live_e1_engine.py`) is the sole live decision/execution system now — see "Strategic Direction" at the top of this file.

`CampaignLog` was not created by any user-facing route. It was created by `_inject_brief_to_database()` in `kabroda_mas_flow.py` (also deleted) as an **upsert** — if no record existed for `(symbol, session_id, date_key)`, it created one using the `ExecutiveBrief` output.

`ledger_closing_engine.py` used to monitor all records where `mas_approval_status == 'APPROVED'` and `closed_at IS NULL`, on a 60-second loop, implementing a 30%-at-T1/fixed-runner-stop/70%-to-T3 management rule in `CampaignLog`'s own `status`/`realized_pnl` fields. **This rule was DEPRECATED as of 2026-09-07** (it predated the real audit, `CLEAN_REPORT.md` in the `Kabroda AI Brain` repo, and was never updated to match the later v2 50/50 rebuild) — it was always a shadow simulation, never the source of truth for real money. `CampaignLog`/`GateLog`/`DecisionJournal`/`SessionAuditLog`/`DecisionGaugeReading` are now write-frozen (their only writers are gone) — the tables themselves are left in place per this file's "no migration framework, leave the table" convention, not dropped.

---

## What Must Never Be Changed

1. **The calibrated gate's formulas (v2, current since 2026-09-11).** `T1/T3 = trigger ± 1.0×/1.618×box` (T2 == T1's price, kept only for schema compat); `stop = r30 ∓ 0.12×box` (one formula, every trade, no tier); `MAX_BOX_ATR = 0.55`; Krown Cross votes==2 (21/55 EMA + slope, both 1H and 4H); HTF aligned≥1 (9/21 EMA); 4H RSI(14) Wilder in the lock-frozen control zone (62-80 LONG / 20-38 SHORT). These trace to a real, measured backtest (`CC_PACKAGE.md`/`CANON.md` §8, `Kabroda AI Brain` repo — v1's fuel-based numbers, `VOL_FUELED`/tier floors/0.618×T1, are retired, see "The Calibrated Gate" above) — do not retune them here without evidence from that repo's calibration process. This rule itself is not permanent in the sense the old Measured Move Rule claimed to be — the whole point of the `gate_log` table (§ below) is that these numbers get revisited as forward data comes in — but they are not a local guess to tweak casually either.

2. **The 30-minute session lock.** The calibration window is exactly 1800 seconds from `anchor_time`. Levels computed during this window are the SSOT. They are never recomputed mid-session once locked, regardless of how much price moves. Unchanged by the 2026-08-30 rebuild — this is the "core pieces stay" part.

3. **The gate evaluates on the first 5m close beyond BO/BD**, not a close count. This replaced the old 2-consecutive-close acceptance requirement (2026-08-30, Andy's explicit call) — the 4-condition gate itself is the false-breakout filter now. (This item said "which includes real volume confirmation" until 2026-09-22 — stale v1 language; v2's four conditions, current since 2026-09-11, are reachability, HTF aligned≥1, Krown Cross votes==2, and 4H RSI-at-lock-in-zone, with no volume/fuel component at all — see "The Calibrated Gate" above.) Do not reintroduce a close-count requirement in front of the gate; that would mean running behavior that was never actually backtested.

4. **Class 0 KDE weighting.** `permanence_class=0` levels receive `+15.0` kinetic friction in the KDE calculation. Gravity is decoupled from the trade decision (2026-08-30) but this weighting still governs the gravity map itself, which stays as its own reference page. Do not reduce this multiplier.

5. **The stop loss has no gravity dependency anymore, and (as of 2026-09-11) no tier dependency either.** `r30 ∓ 0.12×box` — no ATR, no gravity-wall snapping, no PREMIUM/STANDARD split. `trade_structure_analyst.py` (the old ATR+gravity-wall stop) is archived, not a reference implementation to fall back to. `stop_planner.py`'s separate, ATR-buffered 24h zone stop (v1's PREMIUM-only execution stop) has no live call site left at all — this one r30 formula is now both the risk-basis AND the real execution stop, for every trade, sent straight to the exchange. See "The Calibrated Gate" above for the full citation trail.

6. **`_inject_brief_to_database` as an upsert.** It must create a new `CampaignLog` if one doesn't exist. If you change it back to update-only, decision output is silently discarded.

7. **Symbol normalization before DB writes.** Always call `_normalize_symbol()` or equivalent before writing to `session_locks` or `campaign_logs`. The `gravity_memory` table uses the no-slash format — do not change that either.

8. **Log every gate evaluation.** `_inject_gate_log()` writes to `GateLog` on every call to `run_mas_analysis()` — TAKE or PASS alike, not just approved trades. This is the forward-incubation record (`KABRODA_REBUILD_SPEC.md` §9); do not make this conditional on the outcome.

---

## Database Schema Notes

Schema changes are raw `ALTER TABLE` statements wrapped in `try/except` inside `init_db()` in `database.py`. There is no migration framework. Add new columns there using the same pattern. The `try/except` silently skips if the column already exists, making it safe to re-run on existing databases.

## Background Tasks

Two tasks start on app boot via `lifespan()` in `main.py`:

- **Gravity Ingestion Loop** (`gravity_engine.py`) — scans 4H/1H/1D pivots for BTC, ETH, SOL every 15 minutes. Logs supply/demand pivots to `gravity_memory`. Also triggers `kabroda_macro_engine.py` as a subprocess on boot and every 24 hours (~96 loop iterations).
- **Ledger Closing Loop** (`ledger_closing_engine.py`) — checks live MEXC prices against open APPROVED campaigns every 60 seconds.

The macro engine (`kabroda_macro_engine.py`) runs as a **subprocess**, not an asyncio task — it has its own event loop and fetches 1500 days of daily data, which would block the main loop.

## The Unauthenticated Endpoint

`GET /api/gravity/scan` requires no login. The War Room JS polls it every 60 seconds to update the gravity map and KPI cards. Do not add sensitive position data or user-specific data to its response.

---

## Cross-Agent Handoff

This project uses AGENT_LOG.md for asynchronous handoff notes between Claude
Code and DeepSeek/Antigravity. Read it before starting work; append entries,
never edit past ones. Full convention: ~/.claude/CLAUDE.md (global).
