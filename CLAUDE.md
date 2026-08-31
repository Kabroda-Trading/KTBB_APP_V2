# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

Production deploys to Render at kabroda.com on port 10000. There is no test suite — validate by running the server and hitting routes.

## Required Environment Variables

```
ANTHROPIC_API_KEY     # Powers all 6 CrewAI agents and the Operator Commlink
SESSION_SECRET        # Cookie signing key
DATABASE_URL          # Default: sqlite:///./kabroda.db (prod: PostgreSQL)
PUBLIC_BASE_URL       # Used to auto-detect HTTPS for secure cookies
ADMIN_EMAIL           # Bootstrap admin on first boot
ADMIN_PASSWORD        # Bootstrap admin password
COINALYZE_API_KEY     # Optional — open interest fuel multiplier
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

### The Calibrated Gate (rebuilt 2026-08-30 — supersedes the old Measured Move Rule)

The site's original target formula (1×/1.618×/2.618× of the bo–bd distance, "Measured Move") was never backtested against real outcomes at scale, and when it finally was — a 1,913-trigger-break backtest, 2021–2026, `Kabroda AI Brain` repo — it lost money on kabroda.com's own real filled trades (71 trades, 29.8% win, −0.30R avg, −21.4R total). Andy authorized a full replacement, not a patch. Source of truth: `KABRODA_REBUILD_SPEC.md` + `CALIBRATION.md` in the `Kabroda AI Brain` repo. Live implementation: `decision_engine.py`, `reachability.py`, `htf_fuel.py`, `fuel_gate.py`, `market_regime.py`, `micro_regime.py`.

**The gate.** Evaluated once, on the first 5m close beyond BO or BD — not the old 2-consecutive-close acceptance count (that's what the backtest actually measured; the gate below, which includes real volume confirmation, is the false-breakout filter now). All four required for a TAKE:

1. **Reachability** — `box / dailyATR14 ≤ 0.55` (`box = bo − bd`). A wide box puts T1 out of reach; this is the single strongest signal in the backtest.
2. **5M fuel** — real push volume at the trigger cross, median push ≥ 0.8× the prior-24h baseline (`fuel_gate.py`).
3. **HTF carry** — ≥1 of {1H, 4H} trend (fresh 9/21 EMA read, `htf_fuel.py`) backs the side. This doesn't change whether T1 gets hit — it changes how far the winner runs (the runner's fuel).
4. **Live hour** — trigger hour not in the dead-tape set (`<12 UTC` or `18–21 UTC`).

**Tier.** `PREMIUM` when both HTF timeframes align AND box/ATR ≤ 0.40 (tighter, both timeframes carrying — size up, hold the runner to T3). `STANDARD` otherwise. Both are real TAKE signals; the difference is size, not management.

**Hard vetoes** (cap the result below TAKE even if the gate passes): ghost push (no real volume — ≥`NO_FUEL`), DEAD 15m regime (no participation), counter-trend on a GOOD daily table (don't fight a strong trend), 15M divergence against the side (spec's own caveat: weak evidence at 15M, kept as specified pending a 4H/daily upgrade).

**Targets and stop** — box multiples, no gravity dependency (gravity is a separate reference page now, not a decision input):

```
box = breakout_trigger − breakdown_trigger

Entry = the trigger itself (bo for long, bd for short)
Stop  = r30_low − 0.12×box (long)  /  r30_high + 0.12×box (short)
T1 = trigger ± 0.618×box
T2 = trigger ± 1.0×box   (the measured move)
T3 = trigger ± 1.618×box
Runner stop (after T1) = trigger ∓ 0.15×box
```

**Management, identical for both tiers**: 30% off at T1, stop moves to the runner-stop level, 70% rides to T3. Tested against alternatives (50/50 at T1/T2, 100%-at-T1) — this beat both.

**Four outcomes only, no grades, no score**: `TAKE_PREMIUM` / `TAKE_STANDARD` / `ALMOST` (one gate condition still missing) / `PASS` (always with a specific, stated reason). This is what `decision_engine.evaluate_15m_decision()` returns, and it's the same function `run_mas_analysis()` and the live radar (`market_radar.py`'s `_build_dossier()`) both call — they can never silently disagree.

Every gate evaluation, TAKE or PASS alike, is logged to the `gate_log` table (`database.py`) — this is the forward-incubation record the Kabroda AI Brain reads to confirm live results track the backtest.

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
- Called from two places that must never disagree: `kabroda_mas_flow.run_mas_analysis()` (fires at session lock, writes the official `CampaignLog`/`GateLog` record) and `market_radar._build_dossier()` (the live public radar/API, recomputes fresh on every call).

**Intel Auditor** — removed 2026-08-30 (Andy's call: gone entirely). It used to take a foreign signal (MetaSignals format) and have an LLM compare it against Kabroda's SSOT — gravity walls as a BLOCKED/HIGH_RISK/CLEAR gate, plus a third, different measured-move formula. Both had gone stale under the calibrated-gate rebuild, and it was the last LLM-based tool left in the codebase (a paid `agent_core._call_agent()` call per use). `IntelAuditReport`, `INTEL_AUDITOR_SYSTEM_PROMPT`, `audit_foreign_intel_pipeline()` (`kabroda_mas_flow.py`), the `POST /api/research/audit-intel` route (`main.py`), and the "External Intel Injection" panel (`templates/macro_war_room.html`) are all gone, not archived.

---

## The Symbol Format Rule

**All DB operations must use `BTC/USDT` format (slash-separated).** Raw API inputs arrive as `BTCUSDT`. `_normalize_symbol()` in `battlebox_pipeline.py` converts them. Use it before any DB write or MAS trigger. The War Room normalizes via `.replace("USDT", "/USDT")`. Inconsistency here causes CampaignLog and SessionLock queries to silently miss — this was the original cause of the CCO brief stuck on PENDING.

The `gravity_memory` table is an exception: `kabroda_macro_engine.py` stores symbols as `BTCUSDT` (no slash), because it strips the slash via `.replace("/", "")`. The `calculate_gravity_kde()` function also strips the slash when querying. Do not change this — it is consistent within the gravity subsystem.

---

## CampaignLog Lifecycle

`CampaignLog` is not created by any user-facing route. It is created by `_inject_brief_to_database()` in `kabroda_mas_flow.py` as an **upsert** — if no record exists for `(symbol, session_id, date_key)`, it creates one using the `ExecutiveBrief` output. Fields `grade` and `total_contracts` default to `"MAS_AUTO"` and `0.0`.

`ledger_closing_engine.py` monitors all records where `mas_approval_status == 'APPROVED'` and `closed_at IS NULL`, on a 60-second loop. Live-price polling (MEXC) only handles Phase 1 entry-fill detection; once filled, resolution is 1m Kraken OHLC candle scanning, not ticker snapshots. As of 2026-08-30 this implements the real, live 30%-at-T1 / fixed-runner-stop / 70%-to-T3 management rule (see "The Calibrated Gate" above) in the authoritative `status`/`realized_pnl` fields — a T1 touch is no longer terminal; it opens a runner (`runner_active`, `runner_stop`, `t1_leg_r` on `CampaignLog`) that resolves at a runner-stop touch (`CLOSED_LOSS`, blended R), a T3 touch (`CLOSED_WIN`, blended R), or the next session open with neither hit (`CLOSED_AT_EXPIRY`, blended R). A stop hit before ever reaching T1 is still a full, unblended `CLOSED_LOSS` at exactly -1R. See `tests/test_runner_mechanic.py` for the full scenario coverage. (The old "RAG memory bank fed to the CRO" this paragraph used to describe is gone along with the CRO/CrewAI agents themselves — see "The Decision Layer" above.)

---

## What Must Never Be Changed

1. **The calibrated gate's formulas.** `T1/T2/T3 = trigger ± 0.618×/1.0×/1.618×box`; `stop = r30 ∓ 0.12×box`; `MAX_BOX_ATR = 0.55`; `VOL_FUELED = 0.8`. These trace to a real, measured backtest (`KABRODA_REBUILD_SPEC.md`/`CALIBRATION.md`, `Kabroda AI Brain` repo) — do not retune them here without evidence from that repo's calibration process. This rule itself is not permanent in the sense the old Measured Move Rule claimed to be — the whole point of the `gate_log` table (§ below) is that these numbers get revisited as forward data comes in — but they are not a local guess to tweak casually either.

2. **The 30-minute session lock.** The calibration window is exactly 1800 seconds from `anchor_time`. Levels computed during this window are the SSOT. They are never recomputed mid-session once locked, regardless of how much price moves. Unchanged by the 2026-08-30 rebuild — this is the "core pieces stay" part.

3. **The gate evaluates on the first 5m close beyond BO/BD**, not a close count. This replaced the old 2-consecutive-close acceptance requirement (2026-08-30, Andy's explicit call) — the 4-condition gate itself, which includes real volume confirmation, is the false-breakout filter now. Do not reintroduce a close-count requirement in front of the gate; that would mean running behavior that was never actually backtested.

4. **Class 0 KDE weighting.** `permanence_class=0` levels receive `+15.0` kinetic friction in the KDE calculation. Gravity is decoupled from the trade decision (2026-08-30) but this weighting still governs the gravity map itself, which stays as its own reference page. Do not reduce this multiplier.

5. **The stop loss has no gravity dependency anymore.** As of 2026-08-30, stop = `r30 ∓ 0.12×box` (see rule 1) — no ATR, no gravity-wall snapping. `trade_structure_analyst.py` (the old ATR+gravity-wall stop) is archived, not a reference implementation to fall back to.

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
