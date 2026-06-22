# KABRODA — System Flow & Source of Truth

**Purpose of this document.** This is the single shared map of how the KABRODA
trading suite produces a daily trade decision and a published product. It exists
so that the owner, Claude, Claude Code, Gemini, or any other tool can point at a
*specific numbered node* and say "the problem is at 2C" instead of guessing and
"just fixing" something — which is how workflows get broken.

**How to use it.** Every node has a stable ID (1A, 1B, 2A...). Each node lists
what it is **SUPPOSED** to do. The **ACTUAL** field is blank until we run the
Claude Code audit (Section 4) and fill in ground truth. **Where SUPPOSED and
ACTUAL disagree, that is the breakdown.** Do not edit the SUPPOSED column to
match reality — that hides the bug. Fix the system, then update ACTUAL.

**Status legend:** `[ ? ]` unknown / needs audit · `[ OK ]` confirmed working ·
`[ !! ]` confirmed broken · `[ ~ ]` partial / suspect

**Two macro phases:**
- **PHASE 1 — Intelligence Pipeline:** wake up → agents gather → Senior Analyst
  reconciles → trade decided → presented on the UI.
- **PHASE 2 — Publication Department:** the decided brief is handed off → publish
  agent pulls weekly/macro context → builds the sellable newsletter.

---

## MISSION / CORE THESIS

KABRODA's mission: each trading day, assess whether a tradeable 15-minute edge
exists. If it does, characterize how strong the edge is and how to manage it
accordingly — entry, stop placement, target selection, and profit-taking — matched
to that day's conditions.

The goal is selective, high-quality participation: neither over-trading (taking
noise / low-edge setups) nor paralysis (waiting for a "perfect" setup that never
comes). The default posture is **"no trade UNLESS there is a defensible edge"**
— but when an edge exists, even a modest one, it should be traded at the size and
aggression the edge justifies:

- **Weaker-but-real setup** → take 100% at T1; do not run a runner.
- **Strong, fully-aligned setup** → scale out, let a runner go to a further target.
- **Choppy day / no sane stop placement** → stand down explicitly, because entering
  carries negative expected value AND an opportunity cost — capital locked out of a
  better setup forming later.

Every "no-trade" and every trade must be articulated with its reasoning in the
daily brief — **the system teaches, not just signals.** The system audits its own
prior calls to refine what "an edge" looks like over time (indicator thresholds,
timeframe-alignment rules, etc.).

**This is NOT a hard-coded checkbox gate.** It is a graduated, probabilistic
judgment. Smart / interpreter agents exist to make and articulate this judgment on
pre-digested domain data — not to produce a binary pass/fail signal.

---

## OPEN DESIGN QUESTIONS (the things we are actually trying to resolve)

- **Q1 — Hunt vs. Gate.** Does the system *gate* (willing to say "higher
  timeframes disagree — stand down / no clean 15m setup") or does it *hunt*
  (always finds a trade and reverse-justifies levels)? Owner's read: it leans
  toward hunting. → see node **2D**.
- **Q2 — Senior Analyst overload.** The Senior Analyst is currently believed to
  (a) manage/audit agents, (b) reconcile conflicts, AND (c) write the brief.
  That may be too much for one role. → see nodes **2A–2E**.
- **Q3 — Timeframe coverage.** Are 1H / 4H / Daily / Weekly genuinely audited and
  weighted before the 15m trade is issued, or does higher-TF context get
  mentioned but not actually *gate* the decision? → see nodes **1C** and **2C**.
- **Q4 — Timing / "late to the party."** By the time the brief is read at ~08:00,
  the chart has often already made the suggested move. Is the pipeline producing
  the call too late, or is it describing a move already in progress? → see **1A**, **2E**.

---

# PHASE 1 — INTELLIGENCE PIPELINE

## 1. GATHERING — every agent collects its own intel
*All agents work in parallel here. Nobody is making the trade decision yet; each
is just producing its piece and pushing it "up" to the Senior Analyst.*

### 1A — System wake / trigger `[ OK ]`
- **SUPPOSED:** On the locked schedule, the system fires up and kicks every data
  agent into action. Pulls fresh market data for the symbol(s).
- **ACTUAL:** `run_senior_analyst_scheduler()` in `main.py` fires daily at
  **`lock_end_ts` (9:00 AM ET, DST-aware)** — the exact moment the 30-minute
  calibration window closes. `_seconds_until_lock_end()` computes the delay via
  `session_manager.resolve_current_session()` with pytz — EDT: 13:00 UTC,
  EST: 14:00 UTC, never hardcoded. On server boot, if `now.timestamp() >= _boot_lock_end_ts`
  and no brief exists for today, it fires immediately (restart-recovery path).
  The MAS trigger is inside `battlebox_pipeline.get_live_battlebox()`: the moment a
  new `SessionLock` is written to the DB, `asyncio.create_task(asyncio.to_thread(run_mas_analysis, ...))` 
  fires at `battlebox_pipeline.py:538`. Candle data (5M / 15M / 1H / 4H / Daily)
  is fetched fresh at call time — data is coherent because the scheduler fires at
  lock_end_ts, not at an arbitrary later page-visit. A page-visit arriving after the
  scheduler fires finds the existing lock, loads from `_LOCKED_PACKETS`, and does
  NOT re-fire MAS — the double-fire guard at `battlebox_pipeline.py:528` holds.
  **Design note (Q4):** The 30-minute calibration window (8:30–9:00 AM ET) is a
  deliberate design choice — the brief is produced at lock_end_ts and is waiting on
  arrival. **Prior state (pre-2026-06-15):** hardcoded 14:00 UTC (incorrect in EDT);
  page-visits raced the scheduler; energy reads sampled at page-visit time, not lock
  time — a time-coherence gap. Resolved by commit `d9a4a92`.
  **Entry window (confirmed 2026-06-16):** Fresh entry is only valid during
  **8:30–11:00 AM CST (9:00 AM–12:00 PM ET)**. After ~noon ET the calibration
  context is several hours stale and a new entry is considered stale regardless of
  intraday price movement. The RE-ARM ALERTER (Suggestion Box 2026-06-16) respects
  this boundary and goes quiet at noon ET. **Phase 1 note:** the current lifecycle
  monitor expires unfilled trades at `session_expires_at` (3:00 PM ET) — the noon
  entry cutoff is a policy parameter not yet enforced as the Phase 1 expiry. Future
  refinement: tighten Phase 1 to noon ET. Filled trades (Phase 2) are unaffected —
  once entered, they run to stop/target/next-session-open per the W-9 Phase 2 fix.
- **Feeds into:** 1B, 1C, 1D...

### 1B — Market Radar (V15) `[ ~ ]`
- **SUPPOSED:** First-pass scanner. Produces the early directional read
  (e.g. "BULLISH 3/5 / BUILDING"). A *filter*, not a decision.
- **ACTUAL:** `market_radar.scan_sector()` in `market_radar.py:321` runs for
  `["BTCUSDT"]` only. Calls `_build_dossier()` which scores the setup as GRADE A /
  GRADE B / STAND DOWN based on two criteria: macro bias alignment (6 pts) and
  airspace clearance to T1 (4 pts). Also calls `get_mtf_brief()` which runs
  `mtf_confluence_scanner.run_mtf_confluence_scan()` — the 0–5 score is a count
  of how many of the 5 timeframes (15M/1H/4H/Daily/Weekly) have their EMA21 above
  EMA55. Results are stored to `MtfReading` and `DecisionJournal` tables.
  **MISMATCH:** Market Radar runs completely independently of the main MAS
  pipeline. Its output is NOT consumed by `run_mas_analysis()` and the Senior
  Analyst never reads it. It is a dashboard display tool, not a gating filter for
  the daily brief.

### 1C — Timeframe / indicator agents (1H, 4H, Daily, Weekly) `[ ~ ]`
- **SUPPOSED:** Each timeframe is evaluated. Indicators (RSI, MACD, Bollinger,
  etc.) read per timeframe. Output is a *structured* call per TF, e.g. "4H bearish
  but showing exhaustion," "1H flipped bullish," so it can actually weight the
  decision — not just be narrated.
- **ACTUAL:** All five timeframes are evaluated, but by **Python math functions —
  not separate LLM agents.** 15M: `_build_synthetic_jewel()` in
  `battlebox_pipeline.py:220` — EMA9/21/35/55, SMA200, kinematic_grade
  (PRIMED / OVEREXTENDED / TANGLED), ribbon_spread, deviation_from_mean,
  exit_warning. 1H and 4H: `_build_fuel_gauge()` → `analyze_tf()` in
  `battlebox_pipeline.py:265` — EMA30/50 trend (BULLISH/BEARISH), MACD momentum
  (POSITIVE/NEGATIVE), RSI, JEWEL signal. Daily / Weekly: `_calculate_weekly_force()`
  in `battlebox_pipeline.py:289` — 21-day SMA comparison. Full 5-TF JEWEL scanner
  (StochRSI, ADX, BBWP, PMARP, RSI divergence): `mtf_confluence_scanner.run_mtf_confluence_scan()`
  fires 6x daily via `jewel_specialist.py`, stores history in `JewelSnapshotLog`,
  and feeds the Senior Analyst via `_read_jewel_context()` in
  `kabroda_mas_flow.py:520`. Outputs are **structured JSON** (not free text) and
  are injected into the Senior Analyst context block. **TF veto (Q3):** YES, but
  prompt-enforced only. STAND_DOWN CONDITION 1 and CONDITION 2 in
  `SENIOR_ANALYST_SYSTEM_PROMPT` (`kabroda_mas_flow.py:111`) instruct the LLM to
  issue STAND_DOWN on TF conflict. There is **no Python gate** in the MAS pipeline
  that reads the TF data and independently blocks or validates the decision.
- **NOTE:** This is the #1 suspect zone (Q3). Owner sees higher-TF context
  *mentioned* but not clearly *controlling* the 15m call.

### 1D — Gravity Map (KDE structural density) `[ ~ ]`
- **SUPPOSED:** Structural/anchor layer. Flags density zones, macro anchors, and
  "KINETIC FRICTION → NO TRADE" when price collides with a Class 0 anchor. A
  *veto / context* layer.
- **ACTUAL:** KDE computed by `gravity_math.calculate_gravity_kde()`, called
  inside `battlebox_pipeline.get_live_battlebox()`. Injected as
  `context["kde_peaks"]`. The Trade Structure Analyst (`trade_structure_analyst.py:133`)
  then scans HEAVY/MAXIMUM peaks: (a) adjusts structural stops away from walls,
  (b) snaps Fibonacci targets to intercepting walls. This adjusted data is passed
  to the Senior Analyst. STAND_DOWN CONDITION 3 in `SENIOR_ANALYST_SYSTEM_PROMPT`
  (`kabroda_mas_flow.py:128`): if adjusted T1 is less than 0.35% from entry (a
  gravity wall has choked the measured move), output STAND_DOWN. The Market Radar
  display (`market_radar.py:122`) has a hard Python airspace-clear gate, but that
  applies to the dashboard panel only. **In the MAS pipeline, the NO-TRADE signal
  from gravity is prompt-instructed via CONDITION 3 — not enforced by Python code.**
  Advisory in practice; only fires as a hard block if the LLM applies CONDITION 3.

### 1E — Macro War Room `[ OK ]`
- **SUPPOSED:** Higher-level macro context for the day/week.
- **ACTUAL:** Two sources, both Python — no dedicated LLM. (1) SPX / DXY / VIX
  fetched by `market_context_oracle.get_global_macro_context()` from Yahoo Finance,
  called concurrently in `battlebox_pipeline.get_live_battlebox():446`. Injected
  as `context["macro_environment"]` and rendered in the Senior Analyst context as
  `=== MACRO ENVIRONMENT (TRADITIONAL FINANCE) ===`. (2) Elliott Wave structure
  fetched by `_fetch_macro_structure()` in `battlebox_pipeline.py:362`, which reads
  `gravity_memory` rows where `source == "MACRO_ENGINE_CLASS_0"`. Injected as
  `context["macro_structure"]` and rendered as `=== MACRO STRUCTURE (ELLIOTT WAVE
  — CLASS 0 LEVELS) ===`. Both are consumed by the Senior Analyst on every run.

### 1F — Any other gathering agents `[ OK ]`
- **SUPPOSED:** _(unknown — there may be 5–6 agents total; exact roster TBD)_
- **ACTUAL:** Three additional agents complement the morning pipeline. None run
  *in parallel with* the morning brief — they are pre-computed inputs that feed
  the **next** Senior Analyst run. (1) **JEWEL Specialist** (`jewel_specialist.py`):
  fires 6x daily at session transitions (Asia Open, Asia Midday, London Open,
  NY Open, NY Midday, NY Close) via `run_jewel_scheduler()` in `main.py:243`.
  Runs a full 5-TF JEWEL scan and writes to `JewelSnapshotLog`. The last 6
  snapshots are injected into the Senior Analyst context via `_read_jewel_context()`
  in `kabroda_mas_flow.py:520`. (2) **Elliott Wave Specialist**
  (`elliott_wave_specialist.py`): fires Sunday 23:00 UTC via `run_weekly_scheduler()`
  in `main.py:274`. LLM call that writes wave label / status / targets /
  invalidation to `MacroNarrativeLog` (`authored_by="elliott_wave_specialist"`).
  Fed to Senior Analyst via `_read_narrative_context()`. (3) **Performance
  Auditor / Systemic Adviser** (`performance_auditor.py`): fires Sunday 23:00
  UTC, immediately after Elliott Wave Specialist. LLM call that analyses closed
  trade outcomes and writes a calibration note to `SystemAuditLog`. Injected as
  `PERFORMANCE AUDITOR NOTE` in the Senior Analyst context the following week.

---

## 2. RECONCILIATION & DECISION — Senior Analyst
*Everything from Section 1 is pushed up here. This node has the biggest lift.
This is also where the owner most suspects things go wrong (Q1, Q2).*

### 2A — Intake / collect `[ OK ]`
- **SUPPOSED:** Senior Analyst receives every agent's output. Knows which agent
  produced what.
- **ACTUAL:** All data is bundled into a single context string by
  `_build_senior_analyst_context()` in `kabroda_mas_flow.py:689` before the LLM
  is called. The context block contains: session levels (breakout / breakdown
  triggers, daily S/R, 30M range, VRVP), pre-computed targets (both LONG and SHORT
  rows), Trade Structure Analyst notes (ATR-adjusted stops, gravity-snapped
  targets), fuel gauge (1H / 4H / 15M_JEWEL), gravity walls (oriented and labeled
  relative to each target zone), macro structure (Elliott Wave Class 0 levels),
  macro environment (SPX / DXY / VIX), performance RAG memory (last 5 closed
  trades via `_fetch_cro_memory()`), overnight JEWEL snapshots (last 6 session
  transitions), prior-day narrative paragraph, Elliott Wave specialist note, and
  Performance Auditor note. All data is frozen at call time — no agent reports
  late, nothing is missed.

### 2B — Agent management & job-description check `[ !! ]`
- **SUPPOSED:** Confirms each agent did its defined job and stayed in its lane.
  Catches an agent that pops in out of sequence ("oh by the way, that won't work")
  *after* a decision is forming.
- **ACTUAL:** **Does not exist in code.** There is no mechanism for the Senior
  Analyst to verify that any data module did its defined job or stayed in its lane.
  Agents cannot interrupt — all data is collected by Python before the LLM is
  called and passed in a single frozen context block. The closest analog is a
  9-item SELF-CHECK in `SENIOR_ANALYST_SYSTEM_PROMPT` (`kabroda_mas_flow.py:262`)
  that verifies the LLM's own output format — it checks the Senior Analyst's
  response, not upstream inputs. No enforcement of agent job descriptions exists.

### 2C — Conflict reconciliation / send-back-for-clarity `[ !! ]`
- **SUPPOSED:** If outputs disagree (targets, stop, wave count, TF conflict), the
  SA sends the question *back down* to the relevant agent for clarity BEFORE
  committing. E.g. "1H bullish vs 4H bearish — which governs the 15m today?"
- **ACTUAL:** **No send-back-for-clarity loop exists anywhere in code.** The
  pipeline is strictly single-pass: Senior Analyst receives all data in one context
  block, applies the STAND_DOWN conditions from `SENIOR_ANALYST_SYSTEM_PROMPT`,
  and either issues STAND_DOWN (if CONDITION 1 — direct 4H/1H conflict + CHOP_RISK
  — is met) or narrates the conflict and proceeds to APPROVED/REJECTED. The LLM
  has no mechanism to route a question to another module and receive a new answer.
  **Suspected weak point confirmed (Q2C):** reconciliation is "narrate conflict +
  apply STAND_DOWN conditions," not a clarification loop.

### 2D — Trade GATE decision (take / stand down) `[ ~ ]`
- **SUPPOSED:** Decide whether there is a genuine high-probability 15m setup. Must
  be allowed to conclude **"no trade today"** when higher-TF structure, density
  anchors, or TF conflict say so. Should NOT manufacture a setup to have something
  to show.
- **ACTUAL:** An explicit STAND_DOWN path exists. `approval_status = "STAND_DOWN"`
  is a valid output alongside APPROVED, REJECTED, and WAITING_FOR_15M. Three
  conditions in `SENIOR_ANALYST_SYSTEM_PROMPT` (`kabroda_mas_flow.py:112`) trigger
  it: **CONDITION 1 — CHOP:** Harmonic State CHOP or HOSTILE_CEILING + 4H/1H
  trend in direct conflict + Kinematic Fuel CHOP_RISK. **CONDITION 2 — MULTI-TF
  EXHAUSTION:** two or more of: 4H Momentum NEGATIVE, Kinematic Fuel OVEREXTENDED
  or CHOP_RISK, 15M kinematic_grade OVEREXTENDED. **CONDITION 3 — CHOKED TARGET:**
  adjusted T1 less than 0.35% from entry (gravity wall has snapped the measured
  move). A STAND_DOWN brief replaces the trade-level sections with WHY THE SYSTEM
  STANDS DOWN, THE STRUCTURAL LANDSCAPE, and WHAT WOULD CHANGE THIS. The
  `LedgerClosingEngine` monitors only `mas_approval_status == 'APPROVED'` — a
  STAND_DOWN record is never auto-traded. **CRITICAL CAVEAT:** the gate is
  entirely prompt-enforced. Python does not read the STAND_DOWN conditions and
  independently validate or override the LLM's output. If the LLM fails to apply
  a condition, no code catches it. Status `[ ~ ]` because the path exists and is
  well-specified, but it is not code-guaranteed.

### 2E — Decision finalized & handed to writer `[ OK ]`
- **SUPPOSED:** Once 2D commits, the decision + supporting facts are packaged and
  passed to the brief writer (3A). SA should NOT necessarily write it itself.
- **ACTUAL:** After the Senior Analyst LLM call returns, `run_mas_analysis()` in
  `kabroda_mas_flow.py:1052` immediately writes the `ExecutiveBrief` to three DB
  tables (CampaignLog, DecisionJournal, MacroNarrativeLog), then passes the brief
  object directly to `publisher_crew.run_publisher()` at `kabroda_mas_flow.py:1058`.
  The handoff exists. **However (Q2):** the Senior Analyst does NOT stop at
  deciding — it both decides AND writes the full brief (including
  `formatted_newsletter_md` with all section headers) in its single LLM call. The
  Publisher receives a finished brief, not raw decision data. See node 3A.

---

## 3. BRIEF WRITING — turning the decision into the daily brief
*Open design question: should this be a SEPARATE writer agent (LLM) so the Senior
Analyst only decides, not writes?*

### 3A — Brief writer (LLM) `[ ~ ]`
- **SUPPOSED:** Takes the finalized decision + facts from 2E and writes the daily
  brief in clear language. Articulates the trade, levels, invalidation/stop zones,
  and the "stand down if 15m closes above X" guidance.
- **ACTUAL:** Two LLM passes exist, but the SA is doing both deciding and writing.
  **Pass 1 — Senior Analyst** (`kabroda_mas_flow.py`): produces the full
  `ExecutiveBrief` JSON including `formatted_newsletter_md` (complete Markdown
  brief with all `## SECTION` headers). This is the operational brief stored in
  `CampaignLog.formatted_newsletter` and shown on the dashboard. **Pass 2 —
  Publisher Agent** (`publisher_crew.py`): a separate LLM call with a completely
  different system prompt (Editor-in-Chief, institutional newsletter voice). It
  receives the SA's `ExecutiveBrief` plus external data (Fear & Greed index from
  alternative.me, CoinGecko global market cap / 24h volume / BTC dominance, 7-day
  performance W/L record) and produces a new `NewsletterBrief` {headline,
  newsletter_md} stored in `NewsletterLog`. The Publisher CAN add its own
  reasoning (jargon translation, sentiment context, allocation framing in plain
  language) but cannot contradict the SA's approval_status or price levels.
  **Q2 confirmed:** the SA is overloaded — it decides AND writes. The Publisher
  is a second-pass reformatter for the public product, not the primary writer.
- **DESIGN NOTE:** Owner's proposed split — SA decides & packages (2), separate LLM
  writes (3). Keeps the SA from being overloaded (Q2).

### 3B — Store to database `[ OK ]`
- **SUPPOSED:** The decision + brief are stored for the weekly audit and for
  Phase 2 to read.
- **ACTUAL:** Five storage locations written after each MAS run. (1) `CampaignLog`
  via `_inject_brief_to_database()` in `kabroda_mas_flow.py:1208` — approval_status,
  bias, entry, stop, t1/t2/t3, `mas_executive_brief` (tactical_brief),
  `formatted_newsletter` (full Markdown brief), `structure_reasoning` (JSON audit
  trail from Trade Structure Analyst). (2) `DecisionJournal` via
  `_inject_decision_journal()` in `kabroda_mas_flow.py:1263` — decision_type,
  confluence_score, energy_status, kinematic_grade, `full_context_json` (entire
  battlebox payload for backtesting). (3) `MacroNarrativeLog` via
  `_write_narrative_log()` in `kabroda_mas_flow.py:926` — `narrative_text` (The
  Bigger Picture paragraph extracted for cross-day continuity), `tactical_text`.
  (4) `NewsletterLog` via `_write_newsletter_log()` in `publisher_crew.py:441` —
  headline, newsletter_md, `publish_status="DRAFT"`. (5) `AgentRunLog` via
  `agent_core._log_run()` — token counts and estimated cost for every LLM call.

---

## 4. PRESENTATION — UI to the user
### 4A — UI render `[ OK ]`
- **SUPPOSED:** The brief and trade are presented cleanly on the suite UI
  (Market Radar / cockpit / dashboard) so the user understands it at a glance.
- **ACTUAL:** Dashboard displays `CampaignLog.mas_executive_brief` (tactical_brief)
  and `CampaignLog.formatted_newsletter` verbatim — no re-derivation. Radar and
  KPI cards read live data via `battlebox_pipeline.get_live_battlebox()`, which
  uses the session-lock shortcut `_try_locked_shortcut()` in `market_radar.py:20`
  when a lock exists — reads `SessionLock.packet_data` directly, bypassing the
  full candle fetch. No drift: stored decisions are rendered as stored. The brief
  the user sees is exactly what the Senior Analyst wrote.

---

# PHASE 2 — PUBLICATION DEPARTMENT
*This is the sellable product. A whole separate "department" downstream of the
Phase 1 decision. Treated as its own flow.*

### 5A — Publish agent intake `[ OK ]`
- **SUPPOSED:** Receives everything from Phase 1 (the decision, the brief, stored
  data). Can read the database for weekly / bigger-timeframe / last-week /
  coming-week context.
- **ACTUAL:** `publisher_crew.run_publisher()` in `publisher_crew.py:472` reads
  three sources. (1) `ExecutiveBrief` object passed directly from
  `run_mas_analysis()` — approval_status, bias, all levels, full brief Markdown.
  (2) `external_intel_reporter.fetch_market_intel()` — two HTTP GETs (Fear & Greed
  index from alternative.me; CoinGecko global data: total market cap, 24h volume,
  BTC dominance). (3) `_fetch_archivist_data()` in `publisher_crew.py:285` — DB
  reads of `CampaignLog` for last closed trade result and 7-day W/L/R record. Does
  NOT re-analyze the trade decision — reformats and contextualizes only.

### 5B — Newsletter / publication build `[ OK ]`
- **SUPPOSED:** Builds the final public-facing newsletter for paying subscribers.
- **ACTUAL:** Single LLM call via `agent_core._call_agent("publisher_agent", ...)`
  with `max_tokens=6000` in `publisher_crew.py:497`. System prompt is
  `PUBLISHER_SYSTEM_PROMPT` — Editor-in-Chief persona, Bloomberg Terminal voice,
  full jargon-translation table, banned-words list. Produces `{headline,
  newsletter_md}` stored in `NewsletterLog` with `publish_status="DRAFT"`. One
  JSON-parse retry on failure. The newsletter sits as DRAFT until manually
  promoted — there is no auto-publish trigger.

### 5C — Publication auditor `[ ~ ]`
- **SUPPOSED:** Reviews the published product for accuracy/consistency with the
  Phase 1 decision before release.
- **ACTUAL:** Not yet built. No auditor agent, no review code path, no mechanism
  to check newsletter accuracy against the Phase 1 brief before delivery. The
  newsletter is written and stored as DRAFT with no subsequent quality gate.
  **Deferred by design — Phase 2 track.**

### 5D — Release to subscribers `[ ~ ]`
- **SUPPOSED:** Final product delivered to the public/paying audience.
- **ACTUAL:** Not yet built. No delivery channel, no email/webhook/API publish
  path, and no trigger to change `NewsletterLog.publish_status` from "DRAFT" to
  any other state. The newsletter exists in the database and is accessible via the
  internal UI, but there is no mechanism to push it to external subscribers.
  **Deferred by design — Phase 2 track.**

---

# AUDIT PROMPT — paste this into Claude Code
*Run this against the actual codebase to fill in every ACTUAL field above. Do NOT
let it change any code — this is read-only discovery.*

> **Read-only audit. Do not modify any code or config. Report findings only.**
>
> I have a system-flow document with numbered nodes (1A–5D). For each item below,
> tell me what the code ACTUALLY does, cite the file/function, and flag any node
> where reality differs from the description I'll give you.
>
> 1. **Agent roster (nodes 1B–1F):** List every agent/module that runs in the
>    morning pipeline. For each: its name, the file it lives in, what inputs it
>    consumes, and what it outputs (and the output's format — structured object vs
>    free text).
> 2. **Trigger & timing (1A):** What starts the run, on what schedule, and is the
>    market data fresh at the moment the decision is made?
> 3. **Timeframe coverage (1C):** Which timeframes (1H/4H/Daily/Weekly) are
>    evaluated, by which agent, and which indicators per timeframe? Are these
>    outputs allowed to VETO the 15m trade, or are they only described in text?
> 4. **Senior Analyst role (2A–2E):** Show me exactly what the Senior Analyst
>    does. Does it (a) manage/enforce agent job descriptions, (b) reconcile
>    conflicts with a send-back-to-agent loop, (c) decide take/no-trade, and/or
>    (d) write the brief? Which of these are real in code vs. assumed?
> 5. **The GATE (2D) — most important:** Is there an explicit code path that can
>    output "NO TRADE today"? Or does the pipeline always produce a trade with
>    levels? Show me the branch. If higher-timeframe conflict or a Gravity Map
>    NO-TRADE flag is present, does it actually block the trade, or is it advisory?
> 6. **Conflict handling (2C):** When agents disagree (e.g. 1H bullish vs 4H
>    bearish), what does the code do — send back for clarity, average, pick one,
>    or just narrate both?
> 7. **Writer separation (3A):** Is the brief written by a separate LLM/agent or
>    by the Senior Analyst itself? Can the writer add its own reasoning?
> 8. **Storage (3B) & UI (4A):** What gets stored and where? Does the UI display
>    the stored decision verbatim or re-derive its own summary?
> 9. **Publication (5A–5D):** Map the publish agent and confirm whether a
>    publication auditor exists and what it checks.
>
> Output as a table keyed to my node IDs (1A, 1B, ...) with columns:
> NodeID | What the code actually does | File/function | Mismatch flag (Y/N) | Notes.

---

# STRUCTURAL FINDINGS
*Added 2026-06-01 after first full codebase audit. These are the systemic
observations that cut across multiple nodes.*

## SF-1 — The multi-agent parallel pipeline does not exist

The SYSTEM_FLOW document (and prior verbal descriptions) assumed that Nodes 1B
through 1F are separate agents running in parallel, each gathering their own intel
and pushing results "up" to the Senior Analyst for reconciliation. **This
architecture is not what the code does.**

The actual flow is:

```
Python data-gathering (battlebox_pipeline.py)
  → concurrent Kraken OHLCV fetches (5M/15M/1H/4H/Daily)
  → all indicator math in Python (fuel gauge, harmonic matrix, macro bias,
    KDE gravity peaks, Elliott Wave structure, macro environment)
  → Trade Structure Analyst (pure Python — ATR stops + gravity-snapped targets)
  → all data bundled into one context string
        ↓
Senior Analyst — single LLM call (claude-sonnet-4-6)
  receives everything, decides take/stand-down, AND writes the full brief
        ↓
Publisher Agent — single LLM call (claude-sonnet-4-6)
  receives SA brief + external intel, rewrites as institutional newsletter → DRAFT
```

There is no parallel gather phase. There are no discrete gathering agents. There
is no agent-to-agent messaging. The "agents" described in Nodes 1B–1F are Python
functions called sequentially inside `battlebox_pipeline.get_live_battlebox()`
before a single LLM ever runs.

## SF-2 — Two confirmed missing pieces (Nodes 2B and 2C)

**Node 2B (agent job-description enforcement):** No code exists that checks
whether any data module did its defined job. Not informal — literally not present.

**Node 2C (conflict send-back loop):** No code exists for routing a question back
to a data module and receiving a new answer. The pipeline is strictly single-pass.
Conflict resolution lives entirely in the STAND_DOWN conditions in the Senior
Analyst's system prompt.

## SF-3 — The GATE exists but is prompt-enforced, not code-enforced (Node 2D)

Three STAND_DOWN conditions are specified in `SENIOR_ANALYST_SYSTEM_PROMPT`
and the no-trade path is wired correctly through to `CampaignLog` and
`LedgerClosingEngine`. However, nothing in Python validates that the LLM applied
those conditions correctly. The gate is as strong as the model's instruction
following — which is observable at runtime but not deterministic from the code.

## SF-4 — Senior Analyst is doing two jobs (Nodes 2D + 3A)

The Senior Analyst decides the trade AND writes the complete brief in a single
LLM call. The Publisher is a second pass for the public newsletter product, not
the primary brief writer. If the goal is to separate "decide" from "write," the
code would need to split the Senior Analyst into two sequential calls with distinct
prompts and context packages.

## SF-5 — Proposed MTF Interpreter Layer (Node 1C → 2A interface) `[ ? ]`
*Added 2026-06-01. Output spec anchored to Mission / Core Thesis 2026-06-01.*
*Design direction confirmed by owner. Not yet built.*

**The gap:** The Senior Analyst currently receives multi-timeframe indicator data
as raw JSON-formatted text lines (`4H Trend: BULLISH | Momentum: POSITIVE | RSI:
62.3 ...`). It must interpret those numbers, identify conflicts, assess alignment,
and apply STAND_DOWN conditions — all while also making the trade decision and
writing the brief. That is three cognitive jobs in one LLM call.

**The proposed fix:** Insert a dedicated **MTF Interpreter** agent between the
Python math layer and the Senior Analyst. The interpreter reads the raw fuel gauge
data for its specific domain and produces a graduated, probabilistic read that the
SA uses as pre-digested intelligence.

**What the interpreter must output (anchored to Mission / Core Thesis):**
The interpreter does NOT produce a binary "aligned / not aligned" flag. Its job
is to report *how strong* the multi-timeframe alignment is, *where it conflicts*,
and *what that implies* — concretely — for stop placement, target reachability,
and conviction today. The SA uses this to calibrate aggression, not just direction.

Examples of the required output quality:
- *"4H/1H fully aligned BULLISH, 15M PRIMED, SWEET_ZONE harmonic. 4/5 TF
  direction vote. No exit warnings. Airspace to T2 is structurally supported by
  momentum — this is a full-scale setup. Stop below 30M low is defensible."*
- *"4H BULLISH but 1H has flipped BEARISH with NEGATIVE momentum — tide/wave
  disagreement. 15M kinematic_grade is OVEREXTENDED with ribbon spread 1.8%.
  This is a weaker-edge day: if BO triggers, T1 only, no runner. Stop placement
  is tighter than ideal — be aware of stop-hunt risk at 30M low."*
- *"4H and 1H in direct conflict, 15M TANGLED, HOSTILE_CEILING harmonic.
  No coherent directional energy across any timeframe. Stop cannot be placed at
  a level that gives the trade room without excessive R. Negative expected value —
  STAND_DOWN is the correct call."*

**Architecture (when built):**
```
battlebox_pipeline.py  →  fuel_gauge + harmonic_data (Python math, unchanged)
                                ↓
                    mtf_interpreter.py  (NEW — 1 LLM call)
                    Reads: fuel_gauge, micro_state, 1h_fuel_status,
                           last 6 JEWEL snapshots
                    Outputs: graduated alignment read — strength, conflicts,
                             implications for stop / target / conviction
                                ↓
                    _build_senior_analyst_context()  (MODIFIED)
                    Inserts interpreted read in place of raw energy block
                                ↓
                    Senior Analyst (unchanged prompt — receives cleaner input)
```

**Implementation rules (non-negotiable):**
- Output is graduated, not binary — strength + conflict + stop/target implication
- Fail-open: interpreter error → `mtf_read = None` → raw format used as
  fallback → SA context is never degraded below today's baseline
- No schema changes, no new DB tables
- No change to any node currently marked `[ OK ]`
- Follows the established `agent_core._call_agent()` pattern exactly
  (see `elliott_wave_specialist.py` as the template)

**Node 1C status change when built:** `[ ~ ]` → `[ OK ]` (TF data will be
genuinely interpreted — with strength, conflict, and implication — before the SA
sees it, not just narrated as raw numbers)

---

# AGENT BUCKETS (clerk vs interpreter)
*Added 2026-06-01. Conservative classification — Bucket B only where the same
raw input genuinely means different things in context and requires a judgment
before the SA sees it. The goal is to keep the LLM/cost footprint small.*

**Bucket A — CLERK:** produces a locked-in fact (a level, a raw number, a
structured dict) by applying deterministic math or a fixed rule. Fetching and
packaging only — no LLM required, no interpretation.

**Bucket B — INTERPRETER:** the same raw input means different things in
context; requires digestion into a judgment before the SA sees it. LLM required.

| Module | Bucket | One-line reason |
|--------|--------|-----------------|
| `battlebox_pipeline.py` | A | Fetches candles + runs all indicator math (EMA, MACD, RSI, harmonic matrix, fuel gauge) — deterministic formulas |
| `sse_engine.py` | A | Computes bo/bd/daily levels from VRVP math and 30M range — pure arithmetic |
| `structure_state_engine.py` | A | Counts consecutive 5M closes beyond the trigger — a counter, not a read |
| `gravity_engine.py` | A | Background pivot ingestion (4H/1H/1D supply/demand extremes) — fixed scanning algorithm |
| `gravity_math.py` | A | KDE Gaussian density + macro Fib arithmetic — pure math |
| `kabroda_macro_engine.py` | A | ZigZag + deterministic Elliott Wave rule validation — labeled outputs, fixed overlap rules |
| `trade_structure_analyst.py` | A | ATR stop placement + gravity wall snapping — deterministic rules applied to numbers |
| `market_context_oracle.py` | A | Fetches SPX/DXY/VIX + derives risk_posture via fixed if/else thresholds — no contextual reading |
| `mtf_confluence_scanner.py` | A | Computes 5-TF JEWEL indicators (EMA vote, StochRSI, ADX, BBWP, PMARP, divergence) — fixed formulas, structured output |
| `jewel_specialist.py` | A | Extracts JEWEL fields from mtf_confluence_scanner and writes to DB — pure packaging |
| `market_radar.py` | A | Fixed scoring matrix (bias alignment 6pts + airspace 4pts) → GRADE label — threshold scoring, not judgment |
| `external_intel_reporter.py` | A | HTTP fetches for F&G index + CoinGecko data — pure data retrieval |
| `ledger_closing_engine.py` | A | Compares live price vs T1/SL every 60s — pure comparison |
| `session_manager.py` | A | Session config + anchor-time math — deterministic calendar logic |
| `research_lab.py` | A | Reconstructs historical session data by replaying pipeline math — no interpretation |
| `market_simulator.py` | A | Applies radar scoring to historical date ranges — pure computation |
| `live_telemetry.py` | A | Fetches Coinalyze OI delta + 3-tier multiplier threshold — simple fetch *(orphaned)* |
| `liquidity_oracle.py` | A | Fetches Binance L2 order book depth — raw data retrieval *(orphaned)* |
| `agent_core.py` | A | Infrastructure: budget gate + LLM call wrapper — no domain function |
| `elliott_wave_specialist.py` | **B** | Reads labeled Class 0 levels + current price and judges which wave is active, its structural status (IN_PROGRESS vs QUESTIONABLE), and what the invalidation conditions mean today — same levels read differently depending on price behavior |
| `performance_auditor.py` | **B** | Reads 7-day stats across four tables and synthesizes a *specific calibration recommendation* — identical numbers produce different recommendations depending on pattern |
| `publisher_crew.py` | **B** | Translates SA brief into institutional voice with editorial framing, sentiment contextualization, and tone calibration — same levels/status produce different reads depending on narrative |
| `kabroda_mas_flow.py` — Senior Analyst | **B** | Receives all pre-digested facts and makes the probabilistic trade decision + writes the brief — the core judgment role |
| `kabroda_mas_flow.py` — Intel Auditor | **B** | Audits a foreign signal against Kabroda SSOT across three domains — contextual cross-checking |
| `kabroda_mas_flow.py` — Commlink | **B** | Answers operator questions about the current structural picture in real time — contextual response |

**Summary:** 19 Clerks, 5 Interpreter roles (across 4 files). Every data-collection
and math step is pure Python. The LLM footprint is appropriately narrow: four
files, six call sites, all budget-gated through `agent_core`.

**Where the proposed MTF Interpreter (SF-5) fits:** It is a new Bucket B role
sitting between `mtf_confluence_scanner.py` (A) and the Senior Analyst (B). It
takes structured Clerk output and produces a graduated judgment before the SA
sees it — exactly what an Interpreter is for.

---

# AUDITABILITY COVENANT
*Established 2026-06-03. Governing principle — applies to every current and
future Bucket B module.*

**Every Bucket B (Interpreter) module must persist its full output text to
`InterpreterLog` at build time. This is not optional and is not a follow-up task.**

A component that produces judgment without a DB record is invisible to the
Performance Auditor and unrecoverable after the session ends. The root cause
was confirmed on 2026-06-03: the gravity interpreter ran successfully (485 tokens,
$0.018) but its output was irrecoverable from the DB — the only record was a
token count in `AgentRunLog`. No calibration, no look-back, no cross-session
query was possible.

**The rule:** every Interpreter's write path is part of its initial implementation
spec, not an afterthought. If a Bucket B module produces output without an
`InterpreterLog` row, it is not complete.

**The table:** `InterpreterLog` — keyed by `(symbol, session_date, session_id,
interpreter_name)`. Joinable to `CampaignLog` and `DecisionJournal` on the same
triple. A row is written even on fail-open (`ran_successfully=False`,
`output_text=None`) so absences are auditable.

**Future interpreters this applies to:**
- Junior Analyst (GAP-3 — when built)
- Any reconnected OI / L2 depth interpreter (GAP-4 Phase 2)
- Any macro context interpreter
- Any domain interpreter added at any future build

**What this enables for the Performance Auditor:**
Once several weeks of `InterpreterLog` data exist, the weekly audit can add a
Block D: per-interpreter domain accuracy. Example: "gravity_interpreter said
BLOCKED on 3 sessions — 2 were CLOSED_LOSS, 1 was CLOSED_WIN. MTF said EXHAUSTED
on 2 sessions — both were STAND_DOWN. These are calibration signals." This
analysis requires the text data; without `InterpreterLog` it is permanently
impossible to construct.

---

# CONNECTION MAP (2026-06-01)
*Read-only audit of every agent/specialist/analyst/reporter module. Purpose: find
orphans, dead-end writes, and wiring severed when CrewAI was removed.*

**Key:** Decision = reaches `run_mas_analysis()` → SA context → trade brief.
Newsletter = reaches `run_publisher()` → NewsletterLog. Neither = DB-only or
UI-only, never influences either LLM decision chain.

## Full Module Table

| Module | Produces | Called by | Output goes to | Wired into | Orphaned? |
|--------|----------|-----------|----------------|------------|-----------|
| `market_radar.py` | GRADE A/B/STAND DOWN + MTF action sentence | `GET /api/dmr/radar`, `GET /api/radar/scan` (main.py) | `MtfReading` table + `DecisionJournal` table | **Neither** — dashboard display only; never read by `run_mas_analysis()` | N — serves UI, but disconnected from trade decision |
| `mtf_confluence_scanner.py` | 5-TF JEWEL (EMA vote, StochRSI, ADX, BBWP, PMARP, divergence) | `market_radar.get_mtf_brief()` AND `jewel_specialist.run_jewel_snapshot()` | Via jewel_specialist → `JewelSnapshotLog` → SA context (Decision); via market_radar → `MtfReading` (display only) | **Decision** (via JEWEL path) | N |
| `trade_structure_analyst.py` | ATR-adjusted stops + gravity-snapped Fib targets + structure_notes | `kabroda_mas_flow.run_mas_analysis()` line 983 | SA context block; `CampaignLog.structure_reasoning` | **Decision** | N |
| `market_context_oracle.py` | SPX / DXY / VIX prices + risk_posture string | `battlebox_pipeline.get_live_battlebox()` line 446 | `context["macro_environment"]` → SA context | **Decision** | N |
| `jewel_specialist.py` | 5-TF JEWEL snapshot: gate, direction, conviction, exit_warning | `run_jewel_scheduler()` in main.py (6× daily) | `JewelSnapshotLog` → `_read_jewel_context()` → SA context | **Decision** ✓ confirmed wired | N |
| `elliott_wave_specialist.py` | WaveAnalysis: label, status, origin, target, invalidation | `run_weekly_scheduler()` in main.py (Sunday 23:00 UTC) | `MacroNarrativeLog` (authored_by="elliott_wave_specialist") → `_read_narrative_context()` → SA context | **Decision** ✓ confirmed wired | N |
| `kabroda_mas_flow.py` — Senior Analyst | `ExecutiveBrief` JSON: approval_status, levels, full brief | `battlebox_pipeline.get_live_battlebox()` (new lock) + `run_senior_analyst_scheduler()` (main.py) | `CampaignLog`, `DecisionJournal`, `MacroNarrativeLog`; then passed to publisher | **Decision + Newsletter** | N |
| `kabroda_mas_flow.py` — Commlink | Plain-text operator response | `POST /api/research/chat-mas` | Client JSON response | **Neither** (on-demand UI chat) | N |
| `kabroda_mas_flow.py` — Intel Auditor | `IntelAuditReport`: gravity + momentum + measured-move audit | `POST /api/research/audit-intel` | Client JSON response | **Neither** (on-demand UI tool) | N |
| `publisher_crew.py` | `NewsletterBrief`: headline + newsletter_md | `kabroda_mas_flow.run_mas_analysis()` line 1058 | `NewsletterLog` (publish_status=DRAFT) | **Newsletter only** | N |
| `external_intel_reporter.py` | Fear & Greed index + CoinGecko market cap / volume | `publisher_crew.run_publisher()` | Publisher context → `NewsletterLog` | **Newsletter only** | N |
| `performance_auditor.py` | ~300-word weekly audit note (LLM) | `run_weekly_scheduler()` in main.py (Sunday 23:00 UTC) | `SystemAuditLog.audit_md` + `GET /api/dashboard/audits` (dashboard UI only) | **Neither** — see broken-connection note below | **PARTIAL ORPHAN** |
| `research_lab.py` | Historical session reconstruction with computed levels | `POST /api/admin/run-lab` (main.py) | Client JSON response — no DB write | **Neither** (admin backtesting tool) | N |
| `market_simulator.py` | Historical radar simulation (GRADE + targets across date range) | `POST /api/admin/simulate` (main.py) | Client JSON response — no DB write | **Neither** (admin backtesting tool) | N |
| `live_telemetry.py` | Coinalyze OI delta % + fuel_multiplier | **Nothing** — not imported by any other file | Nowhere | **Neither** | **YES — full orphan** |
| `liquidity_oracle.py` | Binance L2 order book depth (1000 bids + asks) | **Nothing** — not imported by any other file | Nowhere | **Neither** | **YES — full orphan** |

---

## Flags

### FLAG 1 — Full Orphans (two modules, likely CrewAI casualties)

**`live_telemetry.py`** — Fetches Coinalyze open-interest delta and produces a
`fuel_multiplier`. Not imported by any file. Under CrewAI, this was almost
certainly a tool input passed to a liquidity/momentum agent. When CrewAI was
removed, the caller disappeared and the file was left standing.

**`liquidity_oracle.py`** — Fetches Binance futures order-book depth (1000 levels)
via a proxy tunnel. Not imported by any file. Same pattern: a CrewAI-era tool
(named "Liquidation Magnets") whose consuming agent no longer exists. The proxy
infra (`BINANCE_PROXY_URL` env var) is still referenced; nothing reads the result.

### FLAG 2 — Broken Connection: Performance Auditor → Senior Analyst

This is the most consequential finding. The connection was **designed** to work
as follows:

```
performance_auditor.py  →  writes performance_note  →  MacroNarrativeLog
_read_narrative_context()  →  reads analyst_row.performance_note  →  SA context
```

**What actually happens:**

`performance_auditor.py` (v2) was upgraded to write to `SystemAuditLog.audit_md`
instead of `MacroNarrativeLog.performance_note`. The comment in the file says
explicitly: *"Output written to SystemAuditLog (permanent vault) instead of being
stapled to macro_narrative_log."*

The read side was never updated. `_read_narrative_context()` in
`kabroda_mas_flow.py:508` still reads:
```python
if analyst_row and analyst_row.performance_note:
    lines.append(f"\nPERFORMANCE AUDITOR NOTE: {analyst_row.performance_note}")
```
`analyst_row` is a `MacroNarrativeLog` row. `MacroNarrativeLog.performance_note`
is never written by any currently active code. The condition is always False.
**The Senior Analyst never receives the Performance Auditor's note.** The note
is visible on the dashboard UI (`GET /api/dashboard/audits` reads `SystemAuditLog`)
but it never enters the trade decision context.

### FLAG 3 — Market Radar disconnected from trade decision (known, not new)

`market_radar.py` computes GRADE A/B/STAND DOWN, runs the MTF confluence scan,
and writes to `MtfReading` and `DecisionJournal`. None of this is consumed by
`run_mas_analysis()`. Confirmed in audit commit 6627dfe (Node 1B). The radar
serves the UI dashboard correctly; the disconnect is intentional but worth keeping
flagged here for completeness.

### FLAG 4 — JEWEL snapshots confirmed wired (requested verification)

`jewel_specialist.py` → writes to `JewelSnapshotLog` 6× daily at session
transitions. `_read_jewel_context()` in `kabroda_mas_flow.py:520` queries
`JewelSnapshotLog`, formats the last 6 snapshots, and passes the block into the
Senior Analyst context via the `jewel_ctx` parameter at `kabroda_mas_flow.py:989`.
**This connection is intact and active.** The snapshot history reaches the SA on
every morning run.

---

## Plain-English Summary: Who Is Standing in the Hallway

Three employees are not contributing to the work:

1. **`live_telemetry.py`** — standing in the hallway holding an OI report nobody
   asked for. Its manager (a CrewAI agent) no longer exists. The report is never
   delivered. *Action when ready: reconnect as input to the MTF interpreter, or
   delete if OI data isn't wanted.*

2. **`liquidity_oracle.py`** — standing in the hallway holding a 1000-level order
   book. Same story as `live_telemetry` — the CrewAI consumer is gone. The proxy
   tunnel it depends on may also be defunct if `BINANCE_PROXY_URL` isn't set in
   prod. *Action when ready: reconnect or delete.*

3. **`performance_auditor.py`** (partial) — this one *is* doing its job (running
   every Sunday, writing its note), but it's handing the note to the wrong desk.
   The Senior Analyst has a mailslot labelled `PERFORMANCE AUDITOR NOTE` that is
   always empty. The note gets filed in a different vault (`SystemAuditLog`) that
   the dashboard reads, but the SA never sees it. *Action when ready: one-line fix
   — either write to `MacroNarrativeLog.performance_note` again, or update
   `_read_narrative_context()` to read from `SystemAuditLog` instead.*

---

---

# DATA FLOW — CURRENT vs TARGET
*Added 2026-06-02. Blueprint for W-6 and the junior-analyst architecture.*

---

## MAP 1 — CURRENT (the real, messy truth)

This is the actual wiring as of 2026-06-02. Traced from code — not from design intent.

### Layer 0 — Raw Inputs (external, no DB)

| Source | Fetched by | What it provides |
|--------|-----------|-----------------|
| MEXC exchange (OHLCV) | `battlebox_pipeline.py` (5M/15M/1H/4H/1D) | Candle history for all math |
| MEXC exchange (OHLCV) | `gravity_engine.py` (4H/1H/1D) | Pivot ingestion loop |
| MEXC exchange (1500D daily) | `kabroda_macro_engine.py` subprocess | Elliott Wave ZigZag source |
| Yahoo Finance | `market_context_oracle.py` → `battlebox_pipeline.py` | SPX / DXY / VIX |
| Alternative.me F&G | `external_intel_reporter.py` → `publisher_crew.py` | Fear & Greed index |
| CoinGecko global | `external_intel_reporter.py` → `publisher_crew.py` | Total market cap / volume / BTC dominance |
| Coinalyze OI | `live_telemetry.py` | OI delta + fuel multiplier — **ORPHANED, never read** |
| Binance L2 depth | `liquidity_oracle.py` | 1000-level order book — **ORPHANED, never read** |

---

### Layer 1 — Data House (`battlebox_pipeline.py` + `sse_engine.py`)

The only layer that talks to exchanges. Everything else reads from DB or is passed in-memory.

**`battlebox_pipeline.get_live_battlebox()`** — called by:
- `market_radar.scan_sector()` (live MTF scan path)
- `POST /api/dmr/live` and `POST /api/dmr/run-raw` (operator tools)
- `main.py run_senior_analyst_scheduler._fire_senior_analyst()` (restart-recovery path)

**What it produces (battlebox_payload):**
```
levels dict:
  breakout_trigger, breakdown_trigger       ← SSOT (frozen at lock, computed by sse_engine)
  range30m_high, range30m_low, anchor_price
  daily_resistance, daily_support, f24_poc
  atr (14-period from 15M candles)

context dict:
  macro_bias (21-day weekly force)
  micro_bias (168H rolling EMA)
  fuel_gauge (1H/4H EMA trend, MACD; 15M_JEWEL kinematic metrics)
  harmonic_data → micro_state (SWEET_ZONE/PULLBACK/HOSTILE_CEILING/EXHAUSTION/CHOP)
  kde_peaks (from gravity_math.calculate_gravity_kde — reads GravityMemory)
  macro_structure (Class 0 EW levels — reads GravityMemory directly)
  macro_environment (SPX/DXY/VIX from market_context_oracle)
  macro_fibs (30D swing Fib retracements/extensions)
```

**At lock moment (first call after 9:00 AM ET):**
1. Writes `SessionLock` to DB
2. Calls `gravity_engine.log_kabroda_bedrock()` → writes 7_DAY_KABRODA rows to `GravityMemory`
3. Fires `kabroda_mas_flow.run_mas_analysis()` as background task

**`sse_engine.py`** — called inside `_compute_sse_packet()` in `battlebox_pipeline.py`. Pure math. Computes bo/bd from 30M range extremes + VRVP VAH/VAL. Produces the `levels` dict. Never writes to DB directly.

**`structure_state_engine.py`** — called by `battlebox_pipeline.get_live_battlebox()` on every call after lock. Counts consecutive 5M closes beyond trigger. Produces `session_battle` state (HOLD FIRE / WAIT / GO). UI display only — not fed into MAS.

---

### Layer 2 — Background Writers (async loops, no user trigger)

| Module | Cadence | Reads | Writes to |
|--------|---------|-------|-----------|
| `gravity_engine.py` | Every 15 min | MEXC 4H/1H/1D via battlebox_pipeline | `GravityMemory` (4H/1H pivots, 1W/168H anchors) |
| `kabroda_macro_engine.py` | Boot + every 24h (subprocess) | MEXC 1500D daily | `GravityMemory` (permanence_class=0, MACRO_ENGINE_CLASS_0) |
| `ledger_closing_engine.py` | Every 60 sec | `CampaignLog` (APPROVED, is_canonical=True, unclosed) + MEXC live price + Kraken 1m OHLCV (Phase 2) | `CampaignLog` (status, realized_pnl, entry_filled_at, t2_reached, t3_reached, max_target_reached); `session_audit_log` (`backfill_outcome` after each close — non-blocking try/except). **W-9 three-phase engine** (2026-06-11): Phase 1 watches for entry fill (entry_filled_at IS NULL → EXPIRED on session close, never CLOSED_LOSS); Phase 2 monitors stop + T1 after confirmed fill via Kraken 1m candle scan; Phase 3 observes T2/T3 after T1 close. Only processes `is_canonical == True` records. |
| `gravity_engine.fill_decision_outcomes()` | Every 4h (inside gravity loop) | `DecisionJournal` rows >4h old + MEXC live price | `DecisionJournal` (outcome_price_4h, outcome_pct_move_4h, outcome_direction_correct) |

---

### Layer 3 — Scheduled Specialists (LLM agents with DB output)

| Module | Cadence | Reads | Writes to | Consumed by |
|--------|---------|-------|-----------|-------------|
| `jewel_specialist.py` | 6× daily at session transitions | MEXC candles via mtf_confluence_scanner | `JewelSnapshotLog` | SA context via `_read_jewel_context()`; Panel 02 cockpit display via `/api/narrative/latest` |
| `elliott_wave_specialist.py` | Sunday 23:00 UTC | `GravityMemory` (Class 0) + live BTC price | `MacroNarrativeLog` (authored_by="elliott_wave_specialist") | SA context via `_read_narrative_context()`; Macro War Room sidebar |
| `performance_auditor.py` | Sunday 23:00 UTC | `CampaignLog`, `DecisionJournal`, `JewelSnapshotLog`, `MacroNarrativeLog` | `SystemAuditLog` | SA context via `_read_narrative_context()` (wired W-5); Dashboard audits panel |

---

### Layer 4 — MAS Pipeline (`kabroda_mas_flow.run_mas_analysis()`)

Called once per session lock (fired by `battlebox_pipeline` at lock moment, or by scheduler restart-recovery).

**Reads (assembled before any LLM call):**
```
battlebox_payload (passed in — levels + context from Layer 1)
GravityMemory (via trade_structure_analyst → gravity_math — HEAVY/MAX peaks for stop/target snapping)
CampaignLog (last 5 closed APPROVED trades → RAG memory string for SA)
MacroNarrativeLog (prior SA narrative_text + Elliott Wave specialist wave data)
SystemAuditLog (most recent Performance Auditor note — post W-5 fix)
JewelSnapshotLog (last 6 session snapshots → jewel_ctx string)
```

**Sequential pipeline steps:**
```
1. _compute_targets()            Pure Python — measured move math (LLM never calculates)
2. trade_structure_analyst       Pure Python — ATR stops + gravity-snapped FIB targets
3. _fetch_cro_memory()           DB read — RAG memory string
4. _read_narrative_context()     DB read — prior narrative + EW wave + auditor note
5. _read_jewel_context()         DB read — 6x JEWEL snapshots
6. mtf_interpreter (LLM call)    Bucket B — graduated MTF characterization (fail-open)
   → _log_interpreter()          InterpreterLog write (fail-safe, row written even on fail-open)
7. gravity_interpreter (LLM call) Bucket B — wall/airspace characterization (fail-open)
   → _log_interpreter()          InterpreterLog write (fail-safe, row written even on fail-open)
8. _build_senior_analyst_context() String assembly
9. Senior Analyst (LLM call)     Bucket B — decision + full brief (APPROVED/REJECTED/WAITING/STAND_DOWN)
10. audit_writer.write_decision_record()  Non-blocking try/except — freezes decision-time inputs to
    session_audit_log; idempotent (skips if row exists); never blocks or alters MAS output if it fails
```

**Writes:**
```
InterpreterLog       interpreter_name, output_text, ran_successfully (steps 6+7, fail-safe)
CampaignLog          approval_status, bias, entry, stop, t1/t2/t3,
                     mas_executive_brief, formatted_newsletter, structure_reasoning
DecisionJournal      decision_type, confluence params, full_context_json
MacroNarrativeLog    authored_by="senior_analyst": narrative_text, tactical_text
session_audit_log    frozen-at-decision inputs (step 10, non-blocking try/except — Adj. 3)
→ publisher_crew:
  external_intel_reporter (HTTP: F&G + CoinGecko)
  Publisher Agent (LLM call)
  NewsletterLog        headline, newsletter_md, publish_status="DRAFT"
AgentRunLog          token counts + cost (every LLM call via agent_core)
```

---

### Layer 5 — On-Demand / UI Scan (`market_radar.scan_sector()`)

Called by `POST /api/radar/scan` (user-triggered Market Radar scan). Runs **independently** — no connection to MAS.

**Reads:** battlebox_pipeline (via locked shortcut or live) + mtf_confluence_scanner (live 5-TF JEWEL)

**Computes:** GRADE A/B/STAND DOWN via `_build_dossier()` — 2-criterion scoring (macro bias 6pts + airspace 4pts). Plan (entry/stop/t1/t2/t3) from measured move math.

**Writes:** `MtfReading` row + `DecisionJournal` row

**Outputs to UI:** grade, plan, mtf_brief, levels — stored in `window.radarMemory[symbol]`

**NOTE:** This grade and plan are NEVER read by `run_mas_analysis()`. They are UI-only.

---

### Layer 6 — UI Pages (what each page actually reads)

#### Market Radar (`/suite/radar`)
| UI element | Endpoint called | Table(s) read | Bypasses SA? |
|------------|----------------|---------------|-------------|
| Row MAS badge ("MAS: STAND_DOWN") | `/api/radar/snapshot` | `CampaignLog.mas_approval_status` | No — correct |
| Row JEWEL dot (gate open/closed) | `/api/radar/snapshot` | `JewelSnapshotLog.jewel_gate_open` | N/A — display only |
| Cockpit Panel 00 (SA brief text) | `/api/narrative/latest` | `MacroNarrativeLog` (narrative_text, tactical_text) | No — reads SA output |
| **Cockpit Panel 02 "HIGH CONVICTION SETUP" label** | `/api/narrative/latest` | **`JewelSnapshotLog.jewel_gate_open + jewel_conviction`** | **YES — reads JEWEL table directly, no reference to mas_approval_status** |
| **Cockpit trade card (entry/stop/T1/T2/T3)** | `/api/radar/snapshot` → `d.plan` | **`CampaignLog.entry_price/stop_loss/t1/t2/t3`** | **YES — renders whenever `entry_price != null`, no gate on approval_status** |
| Cockpit GRADE badge ("GRADE A") | `/api/radar/scan` → market_radar dossier | **market_radar._build_dossier() (in-memory, not from CampaignLog)** | **YES — independent scoring system** |

#### Macro War Room (`/suite/macro-war-room`)
| UI element | Endpoint | Table(s) read |
|------------|----------|---------------|
| Brief display | `/api/narrative/latest` | `MacroNarrativeLog` (SA narrative/tactical) |
| JEWEL sidebar | `/api/narrative/latest` | `JewelSnapshotLog` |
| Wave sidebar | `/api/narrative/latest` | `MacroNarrativeLog` (EW specialist) |
| Commlink chat | `/api/research/chat-mas` | `CampaignLog` (latest execution context) + LLM call |
| Intel Auditor | `/api/research/audit-intel` | `SessionLock` (SSOT levels) + LLM call |

#### Gravity Map (`/suite/gravity-map`)
| UI element | Endpoint | Table(s) read |
|------------|----------|---------------|
| KDE density curve | `/api/gravity/scan` | `GravityMemory` (all classes) |
| Macro Fibs | `/api/gravity/scan` | Computed from MEXC daily candles |
| Sidebar (wave state) | `/api/narrative/latest` | `MacroNarrativeLog` (EW wave) + `JewelSnapshotLog` |

#### Executive Dashboard (`/suite/dashboard`)
Reads exclusively from `CampaignLog`, `DecisionJournal`, `JewelSnapshotLog`, `AgentRunLog`, `NewsletterLog`, `SystemAuditLog`. All read-only historical views — no bypasses, no decisions.

---

### MAP 1 — Three Structural Problems Exposed

**Problem A — Two parallel, unconnected grading systems**

`market_radar._build_dossier()` grades GRADE A/B/STAND DOWN using its own 2-criterion scoring (macro bias + airspace). `kabroda_mas_flow.run_mas_analysis()` grades APPROVED/REJECTED/WAITING/STAND_DOWN using the full SA context. These two systems never speak to each other. The Market Radar cockpit displays BOTH grades side by side. A user sees "GRADE A" from the radar and "MAS: STAND_DOWN" from the brief — two different verdicts from two different algorithms with no defined precedence.

**Problem B — Panel 02 and the trade card bypass the SA verdict entirely**

The cockpit "HIGH CONVICTION SETUP" label reads `JewelSnapshotLog.jewel_gate_open + jewel_conviction` — an independent technical snapshot written 6× daily by `jewel_specialist`. It has no knowledge of `CampaignLog.mas_approval_status`. The trade card renders entry/stop/T1/T2/T3 whenever `entry_price` is not null — the only condition is a non-null price field, not the SA's verdict. Both panels can (and today did) display a full trade setup while the SA brief says STAND_DOWN.

**Problem C — No defined "who reports to whom" between the interpreters and the SA**

The MTF interpreter, JewelSnapshotLog, Market Radar, and the SA are all independent consumers of overlapping data. There is no layer that collects all interpreter outputs, resolves conflicts between them, and hands a single clean package to the SA. The SA receives raw data from multiple sources directly. An MTF interpreter contradicting the JEWEL specialist's gate state arrives at the SA without any resolution.

---

## MAP 2 — TARGET (owner's designed org chart)

```
═══════════════════════════════════════════════════════════════════════
TIER 0 — DATA HOUSE (battlebox_pipeline + sse_engine)
═══════════════════════════════════════════════════════════════════════
  Raw data source. Everything asks it. It asks nobody.
  Outputs: locked levels (bo/bd/r30/daily/ATR) + raw context dict
    (fuel_gauge, kde_peaks, macro_bias, micro_bias, harmonic_state,
     macro_structure, macro_environment)

  Feeds: ALL agents pull their slice from here and from DB tables
         written by background Clerks (GravityMemory, JewelSnapshotLog,
         MacroNarrativeLog, SystemAuditLog)

═══════════════════════════════════════════════════════════════════════
TIER 1 — INTERPRETER AGENTS (domain specialists, Bucket B)
═══════════════════════════════════════════════════════════════════════
  Each interpreter reads one slice of the data house, digests it
  into a plain-English domain read, and hands that read UP.
  No interpreter makes a trade decision. No interpreter reads another
  interpreter's output.

  MTF Interpreter (exists — mtf_interpreter.py):
    Reads: fuel_gauge, harmonic_state, JewelSnapshotLog (last 6)
    Outputs: graduated alignment characterization
             (strength, conflicts, stop/target/conviction implications)

  Gravity/Liquidity Interpreter (TO BUILD — W-?):
    Reads: kde_peaks (from GravityMemory via gravity_math)
           + live_telemetry OI delta (from live_telemetry.py — reconnect orphan)
           + liquidity_oracle order book depth (from liquidity_oracle.py — reconnect orphan)
    Outputs: sweep-risk read, airspace verdict, OI-backed momentum read

  Macro/Structure Interpreter (exists as Elliott Wave Specialist):
    Reads: GravityMemory Class 0 + live price
    Outputs: active wave label, structural conditions, invalidation levels
    Note: fires weekly — output cached in MacroNarrativeLog. Junior Analyst
    reads the cached output, not a live call.

  Performance/Calibration Adviser (exists as Performance Auditor):
    Reads: CampaignLog, DecisionJournal, JewelSnapshotLog, MacroNarrativeLog
    Outputs: weekly calibration note in SystemAuditLog
    Note: fires weekly — output cached. Junior Analyst (or SA directly) reads
    the cached note.

═══════════════════════════════════════════════════════════════════════
TIER 2 — JUNIOR ANALYST (single aggregation + conflict-resolution layer)
═══════════════════════════════════════════════════════════════════════
  Collects all interpreter outputs. Resolves conflicts between domains
  (e.g. MTF says FULL ALIGNMENT but Gravity says airspace blocked).
  Hands ONE clean package to the Senior Analyst.
  Does NOT make the trade decision — only prepares the package.

  ┌─────────────────────────────────────────────────────────────┐
  │ OPEN QUESTION (flag — do not decide yet):                   │
  │ Is the Junior Analyst a NEW dedicated module/prompt, OR is  │
  │ it the MTF Interpreter pattern generalized to N domains with │
  │ a combiner step at the end? Both architectures are valid.   │
  │ The question is whether a single LLM aggregation call is     │
  │ better than having each interpreter independently supply     │
  │ its read. Decide after MTF interpreter is proven live and    │
  │ the Gravity interpreter exists — can compare in practice.   │
  └─────────────────────────────────────────────────────────────┘

  Inputs: all interpreter reads (string outputs)
  Outputs: one structured briefing package (domain reads + conflict flags)

═══════════════════════════════════════════════════════════════════════
TIER 3 — SENIOR ANALYST (decision + articulation)
═══════════════════════════════════════════════════════════════════════
  Receives the Junior Analyst's package.
  Applies gate conditions. Decides APPROVED/REJECTED/WAITING/STAND_DOWN.
  Articulates the brief.
  Does NOT receive raw tables directly. Does NOT do interpretation.

═══════════════════════════════════════════════════════════════════════
TIER 4 — UI / PAGES
═══════════════════════════════════════════════════════════════════════
  Pull ONLY from Tier 3 outputs (CampaignLog.mas_approval_status,
  CampaignLog.entry/stop/targets, MacroNarrativeLog narrative/tactical).

  RULE: No UI page reaches past the Senior Analyst into raw tables
        (GravityMemory, JewelSnapshotLog, MtfReading) to render a
        verdict or trade card. Those tables are data-house-internal.

  The only exception: read-only historical views (Dashboard, Gravity Map
  visualization) — these display raw data for analysis, not for trade
  decision rendering.
```

---

## THE GAP LIST — current reality vs. target, ordered by what must come first

### GAP-1 `[ CLOSED — 2026-06-02 ]`: Close the two cockpit UI bypasses

**What it is:** Panel 02 ("HIGH CONVICTION SETUP") and the trade card render independently of `CampaignLog.mas_approval_status`. The JEWEL conviction label and trade levels show on a STAND_DOWN session.

**What correct looks like:**
- When `mas_approval_status == 'STAND_DOWN'`, Panel 02 top-line label overrides to "STAND DOWN — SYSTEM INACTIVE" regardless of JEWEL state.
- When `mas_approval_status == 'STAND_DOWN'`, `d.plan.valid` is forced to `false` so the trade card renders `--` for all price fields.
- The JEWEL gate/conviction still displays (it's valid data) but cannot be labeled "HIGH CONVICTION SETUP" when the SA has vetoed the session.

**Files:** `market_radar.html` (`loadPanel02Intel()`, `renderSnapshotGrid()`, `renderModal()`)

**Why first:** This is the most visible contradiction in the system today (diagnosed 2026-06-02). A STAND_DOWN session should not present a trade card to the operator.

---

### GAP-2 `[ CLOSED — 2026-06-02 ]`: Resolve the two parallel grading systems

**What it is:** Market Radar's GRADE A/B/STAND DOWN (2-criterion Python score from `market_radar._build_dossier()`) and the SA's APPROVED/REJECTED/STAND_DOWN (full LLM decision) are two independent verdicts with no defined precedence. Both appear in the cockpit simultaneously.

**What correct looks like:**
- Option A (simpler): The Market Radar grade becomes display-only metadata — a structural readiness indicator, not a verdict. The SA's `mas_approval_status` is the single authoritative verdict. Cockpit redesign labels the radar grade as "structural readiness" not "grade."
- Option B (fuller): The Market Radar dossier becomes an input to the Junior Analyst layer. It stops being a verdict and becomes a domain read.

**Why second:** Until this is resolved, any Junior Analyst layer design will be confused about whether the radar grade is an input or an output.

---

### GAP-3 (ARCHITECTURE): Build the Junior Analyst layer

**What it is:** No module currently collects all interpreter outputs, resolves cross-domain conflicts, and hands a unified package to the SA. The SA receives data from multiple independent streams with no coordination.

**What correct looks like:**
- A Junior Analyst module (new or generalized from MTF interpreter pattern) that receives: MTF interpreter read + Gravity/Liquidity interpreter read + cached EW wave state + cached audit note, and outputs a single structured briefing package.
- The SA's `_build_senior_analyst_context()` is rewritten to consume the Junior Analyst's package instead of assembling raw data independently.

**Preconditions before this can be built:**
1. MTF interpreter is proven live (confirm W-1 is working correctly in production)
2. Gravity/Liquidity interpreter is built (GAP-4 below)
3. Junior Analyst open question is answered (see MAP 2 open question box)

---

### GAP-4 Phase 1 `[ BUILT — awaiting prompt review 2026-06-02 ]` / Phase 2 `[ Open ]`: Gravity/Liquidity Interpreter + reconnect orphans

**What it is:** `live_telemetry.py` (OI delta) and `liquidity_oracle.py` (L2 order book) produce real signal but are full orphans — no caller, no consumer. In the target architecture, they feed a Gravity/Liquidity Interpreter that produces a sweep-risk read and airspace verdict for the Junior Analyst.

**What correct looks like:**
- New `gravity_interpreter.py` (Bucket B): reads kde_peaks + live OI delta + L2 depth → outputs sweep-risk read, airspace verdict, OI momentum confirmation.
- Called from the Junior Analyst layer (once it exists).
- `live_telemetry.py` and `liquidity_oracle.py` get a caller for the first time since CrewAI was removed.

**Preconditions:** Junior Analyst design must be decided first (GAP-3).

---

### GAP-5 (HYGIENE): Remove the MtfReading table dependency from the snapshot endpoint

**What it is:** `GET /api/radar/snapshot` reads `MtfReading` (written by `market_radar.scan_sector()`) to display the cached MTF direction badge in Phase 1. In the target, this cached read should come from the Junior Analyst's package, not from a table written by an independent radar scan.

**What correct looks like:**
- In the target, Phase 1 snapshot reads from a "Junior Analyst output" cache, not from `MtfReading`.
- `MtfReading` can be retired or repurposed for historical analysis only.

**Preconditions:** Junior Analyst layer must exist (GAP-3). This gap is low-priority until then.

---

### Summary table

| Gap | Description | Preconditions | Status |
|-----|-------------|---------------|--------|
| GAP-1 | Close cockpit UI bypasses (STAND_DOWN trade card + JEWEL label) | — | **CLOSED 2026-06-02** |
| GAP-2 | Resolve parallel grading systems (radar grade vs SA verdict) | — | **CLOSED 2026-06-02** |
| GAP-3 | Build Junior Analyst layer | W-1 live + GAP-4 exists | Open |
| GAP-4 Phase 1 | Gravity Interpreter (kde_peaks + macro_structure) | None | Built — awaiting prompt review |
| GAP-4 Phase 2 | Reconnect orphans (OI + L2 depth) | Phase 1 live + orphan verification | Open |
| GAP-5 | Retire MtfReading from snapshot endpoint | GAP-3 built | Open |

---

# CHANGE LOG
*Record every change to the system here so we can trace when a working flow broke.*

| Date | Node(s) | What changed | Who | Why | Result |
|------|---------|--------------|-----|-----|--------|
| 2026-06-01 | 1C, SF-5 | Feasibility study: MTF Interpreter layer. Confirmed CrewAI removed; all agents use agent_core pattern. Identified insertion point in run_mas_analysis(). Added SF-5 to SYSTEM_FLOW. Updated W-1 in WORK_LOG. | owner + Claude Code | W-1 design direction confirmed | No code changed — docs only. Ready to build on approval. |
| 2026-06-01 | MISSION | Added MISSION / CORE THESIS section (top of doc). Defines graduated-edge posture: weaker edge → T1 only; strong edge → scale/runner; no-sane-stop → stand down. Establishes probabilistic judgment mandate. | owner + Claude Code | Anchor the system's purpose before design questions | Docs only. |
| 2026-06-01 | SF-5 | Updated MTF Interpreter output spec: graduated alignment read (strength + conflict + stop/target/conviction implication) — NOT a binary flag. Anchored to Mission / Core Thesis. | owner + Claude Code | Prevent interpreter from producing a checkbox instead of a read | Docs only. |
| 2026-06-01 | CONNECTION MAP | Full wiring audit. Found 2 full orphans (live_telemetry, liquidity_oracle — CrewAI era, no callers). Found 1 broken connection (performance_auditor writes SystemAuditLog.audit_md; SA reads MacroNarrativeLog.performance_note — never written; SA never receives the note). Confirmed JEWEL path intact. | owner + Claude Code | Read-only discovery — no code changed | Docs only. |
| 2026-06-01 | AGENT BUCKETS | Classified all 25 modules into Clerk (A) vs Interpreter (B). 19 Clerks, 5 Interpreter roles. Notes where MTF Interpreter (SF-5) fits in the taxonomy. | owner + Claude Code | Establish vocabulary for W-1 build planning | Docs only. |
| 2026-06-01 | 1F, W-5 | Fixed broken auditor wire: `_read_narrative_context()` now queries `SystemAuditLog` (scoped to symbol, most recent by id desc) instead of reading `MacroNarrativeLog.performance_note` (never written). Added `SystemAuditLog` import. Performance Auditor vault write unchanged. SA will now receive weekly calibration note. | owner + Claude Code | W-5 — broken connection found in audit | kabroda_mas_flow.py: 2 lines changed. |
| 2026-06-01 | 1C, SF-5, W-1 | MTF Interpreter built (mtf_interpreter.py). Bucket B layer between Python math and SA. Graduated characterization: alignment strength, conflicts, stop/target/conviction implications. Bans APPROVED/REJECTED/STAND_DOWN — describes, never decides. Fail-open. AWAITING PROMPT REVIEW before live run. | owner + Claude Code | W-1 — first interpreter agent | mtf_interpreter.py new; kabroda_mas_flow.py: 4 edits. commit e3230dc |
| 2026-06-01 | 1C, SF-5, W-1 | MTF Interpreter prompt refinements: (1) hedging rule → decisively probabilistic (may express likelihood, may not hedge weakly); (2) sentence cap 5 → 5-7; (3) COMPLETENESS guard added (D/W signals must not be silently dropped); (4) max_tokens 400 → 600. | owner + Claude Code | Pre-deploy prompt review | commit a596909 |
| 2026-06-02 | GAP-4 Phase 1 | Gravity Interpreter built (gravity_interpreter.py). Bucket B layer reading kde_peaks + macro_structure + levels + post-TSA targets. Characterizes: nearest HEAVY/MAXIMUM obstacle to T1 (price, intensity, %, macro confluence by name), CLEAR/OBSTRUCTED/BLOCKED airspace verdict, T2/T3 viability, one-sentence opposing-direction note, overall structural picture. Completeness guard (same as MTF — must not silently drop decision-relevant walls). Fail-open pattern identical to mtf_interpreter. Wired into run_mas_analysis() as step 2c; gravity_read= param added to _build_senior_analyst_context(); replaces raw GRAVITY WALLS sections when present. AWAITING PROMPT REVIEW before live run. Phase 2 (OI + L2) deferred pending orphan verification. | owner + Claude Code | GAP-4 Phase 1 build | gravity_interpreter.py new; kabroda_mas_flow.py: 4 edits. commit TBD |
| 2026-06-02 | GAP-1, GAP-2 | Cockpit UI authority fix (W-6). GAP-2 Option C: Phase 2 scan now MERGES over Phase 1 snapshot instead of overwriting (preserves SA verdict + SA plan from CampaignLog); row border color driven by mas_approval_status (green=APPROVED, gray=STAND_DOWN) with fallback to radar grade when no SA verdict exists; HUD payload display renames "GRADE A/B" to "PRE-CHECK: A/B" (rawKey unchanged for TradingView). GAP-1: Panel 02 top-line label forced to "STAND DOWN — SYSTEM INACTIVE" when SA says STAND_DOWN, regardless of JEWEL gate/conviction (JEWEL badges/signal still render as context); trade card and position-size calc suppressed (renders "--") when mas_status === 'STAND_DOWN'. rrc-down CSS border changed from red to gray to match SA-muted semantics. | owner + Claude Code | Root cause: diagnosed 2026-06-02 — STAND_DOWN session showed HIGH CONVICTION SETUP + live trade card | market_radar.html: 7 edits. No Python changes. commit TBD |
| 2026-06-03 | 3B, AUDITABILITY COVENANT | InterpreterLog persistence (AUDITABILITY COVENANT). New `InterpreterLog` table in `database.py` — keyed by `(symbol, session_date, session_id, interpreter_name)`, stores full `output_text`, `ran_successfully` bool. Picked up by `Base.metadata.create_all()` on deploy — no ALTER TABLE. `_log_interpreter()` helper added to `kabroda_mas_flow.py`; two call sites inserted in `run_mas_analysis()` immediately after `mtf_interpreter` (step 6) and `gravity_interpreter` (step 7) returns, each in its own `try/except` so a write failure never breaks the session. A row is written even on fail-open so absences are auditable. AUDITABILITY COVENANT section added to SYSTEM_FLOW.md as a governing principle for all future Bucket B builds. Layer 4 pipeline diagram updated. | owner + Claude Code | Root cause: gravity_interpreter ran successfully 2026-06-03 but output was irrecoverable (token count only, no text). One session of data permanently unrecoverable without this. | database.py: 1 class added. kabroda_mas_flow.py: 1 import, 1 helper, 2 call sites. SYSTEM_FLOW.md: 1 section + CHANGE LOG row. |
| 2026-06-11 | 3B, SF-6 | **W-9 Lifecycle Monitor** — replaced `ledger_closing_engine.py` with a three-phase state machine. Phase 1: pre-entry watch (APPROVED records, `entry_filled_at IS NULL`); session close without fill → `EXPIRED / realized_pnl=NULL` — never `CLOSED_LOSS`. Phase 2: in-trade watch after confirmed entry fill; stop → `CLOSED_LOSS / -1.0R`; T1 → `CLOSED_WIN / +1.0R`. Phase 3: post-T1 observation (T2/T3 high-water mark). Five new columns: `entry_filled_at`, `session_expires_at`, `max_target_reached`, `t2_reached`, `t3_reached`. All three phases gate on `is_canonical == True`. | owner + Claude Code | Root cause (W-9): old engine had no entry-fill check — stamped `CLOSED_LOSS` on setups where price never crossed entry. Confirmed phantom losses on 2026-06-07 and 2026-06-10. | `ledger_closing_engine.py` rewritten; `database.py` +5 columns; commits `9ec43b1`, `cc49904`. |
| 2026-06-12 | 5A, 3B | **Archivist null-PnL crash fix** — `_fetch_archivist_data()` in `publisher_crew.py` queried all `closed_at IS NOT NULL` rows, then used `realized_pnl > 0` and `realized_pnl <= 0` comparisons directly. After EXPIRED rows received `realized_pnl = NULL` (W-9 + phantom correction), those rows appeared in `weekly` and `last_closed` with null PnL → `TypeError: '>' not supported between instances of NoneType and int` on every SA run. Fix: both queries now filter `status.in_(["CLOSED_WIN", "CLOSED_LOSS"])`. EXPIRED rows are "no trade" — they must not appear in wins/losses/net_pnl or as "last trade result" in the publisher context. Arithmetic also guarded with `is not None`. | Claude Code | Root cause: canonical separation (2026-06-11) nulled `realized_pnl` on EXPIRED rows but Archivist queries did not exclude them | `publisher_crew.py`: 4 lines changed. |
| 2026-06-12 | SF-6 Rule 1, 3B | **CRO RAG memory null-PnL crash fix** — `_fetch_cro_memory()` in `kabroda_mas_flow.py` queried on `mas_approval_status == "APPROVED"` and `closed_at.isnot(None)` without a `status` filter. Same root cause as the Archivist crash (row above): EXPIRED rows satisfy both predicates — `mas_approval_status` is never changed by the lifecycle monitor (it records the SA's pre-trade verdict), and `closed_at` is set at time of EXPIRED write. Both IDs 86 and 89 appeared in the last-5-closed RAG context with `realized_pnl = None` → `TypeError` on every SA run. Fix: query now filters `status.in_(["CLOSED_WIN", "CLOSED_LOSS"])`; redundant `mas_approval_status` and `closed_at` predicates removed. Arithmetic guarded with `is not None`. See SF-6 Rule 1 `mas_approval_status` trap note. | Claude Code | Root cause: same as node 3B / SF-6 Rule 1 — canonical separation nulled `realized_pnl` on EXPIRED rows; CRO memory query filtered on `mas_approval_status` (wrong column), not `status` | `kabroda_mas_flow.py`: 4 lines changed. |
| 2026-06-11 | 3B, SF-6, dashboard/all consumers | **Canonical Record Separation** — added `is_canonical` boolean to `CampaignLog`. Auto-set `True` at creation for BTC/USDT records (unconditional — covers APPROVED, STAND_DOWN, REJECTED, WAITING). Historical set: IDs 74–90 (13 rows, 2026-05-28 onward) marked via one-time `/admin/set-canonical`. Applied 16 `is_canonical == True` filters across all production consumers: dashboard overview/history/jewel, War Room latest_log/today campaign, Radar snapshot today-campaign, CRO RAG memory, Commlink latest context, ledger all 3 phases, publisher last_closed/weekly, performance auditor. `/admin/export-audit-ledger` intentionally unfiltered. Step 5 data correction: IDs 86 and 89 reclassified `CLOSED_LOSS → EXPIRED` (`realized_pnl = NULL`, `target_hit = NULL`); ID 86 received `diagnostic_data.close_note` documenting late-PM fill context. Verified track record: 4 WIN / 0 LOSS / 2 EXPIRED / 7 STAND_DOWN. | owner + Claude Code | Pre-74 rows (ETH/SOL/multi-symbol era, dollar-PnL format) were polluting all dashboard KPIs, CRO RAG memory, and publisher performance summary. | `database.py`, `main.py` (16 sites), `kabroda_mas_flow.py`, `ledger_closing_engine.py`, `performance_auditor.py`, `publisher_crew.py`. Commits `ac71e82`, `752677b`, `8abc215`, `cc49904`, `48f6fe7`. |
| 2026-06-12 | 3B, SF-6 Rule 3 | **Job 2 Phase A — DecisionJournal ↔ InterpreterLog join key** — `DecisionJournal` had no `session_id` column, so it could only be joined to `InterpreterLog` on `(symbol, session_date)`. Added `session_id VARCHAR` column to `decision_journal` via ALTER TABLE migration. Added `session_id: str` to `_inject_decision_journal()` signature and `session_id=session_id` to the constructor. Forwarded `session_id` at the call site in `run_mas_analysis()` (was already in scope, simply not passed). The join triple `(symbol, session_date, session_id)` now threads all three tables: `campaign_logs`, `interpreter_log`, and `decision_journal`. See SF-6 Rule 3 for the session_id trap note. | Claude Code | Root cause: `session_id` was passed to `_log_interpreter()` and `_inject_brief_to_database()` but not to `_inject_decision_journal()` — one-line omission at the call site | `database.py`: +1 column, +1 migration. `kabroda_mas_flow.py`: 3 lines changed. |
| 2026-06-12 | — | **Temp admin route cleanup** — removed five TEMPORARY-tagged routes that were scaffolded for canonical-separation and phantom-correction work (commits `ac71e82` / `752677b`): `/admin/schema-check` (W-9 schema gate check), `/admin/set-canonical` (one-time canonical backfill — DB write), `/admin/correct-phantoms` (one-time IDs 86/89 correction — DB write), `/admin/backfill-preview` (phantom candidate diagnostic, read-only), `/admin/table-audit` (full CampaignLog cross-tab, read-only). All five were marked DELETE-after-confirmation; data work is complete. Retained: `/admin/export-audit-ledger` (SF-6 Rule 1 intentional exception), `/admin/interpreter-log` (Auditability Covenant permanent tool). | Claude Code | Post-cleanup hygiene — one-time tools should not stay live in production | `main.py`: 372 lines removed. |
| 2026-06-15 | 2A, W-7 | **W-7 Fix 3 — SA prompt stale example residue corrected.** WHY THE SYSTEM STANDS DOWN example cited "4H Momentum NEGATIVE" as a stand-down trigger; updated to "4H Momentum WEAK [DEPLETED]" to match CONDITION 2(a)'s magnitude framing shipped in `ff60c5a`. SA reads examples as format templates — stale example could teach it to cite old sign-only wording. Fix 3's core (CONDITION 2(a) rewrite from sign to magnitude) shipped in `ff60c5a` (2026-06-06); this commit closes the residue. W-7 fully closed. | owner + Claude Code | Root cause: `ff60c5a` updated CONDITION 2(a) but the adjacent WHY THE SYSTEM STANDS DOWN example was not updated in the same commit — one sentence retained the old "4H Momentum NEGATIVE" trigger wording. | `kabroda_mas_flow.py`: 1-line change in WHY THE SYSTEM STANDS DOWN example. W-7 ☑ closed (all steps done). Commit `0805bd4`. |
| 2026-06-16 | 1F, SF-7 | **W-15 — Auditor thin-data legibility fix.** `_format_stats_block()` in `performance_auditor.py` now guards all three accuracy-breakdown sections (Harmonic Energy, Kinematic Grade, Box Size) with `insufficient_data = (direction_correct + direction_wrong == 0)`. When zero resolved directional outcomes exist, each section emits a single "INSUFFICIENT DATA — 0 resolved directional outcomes this week. Accuracy metric not computable." line in place of per-row zero-count tables. Previously the LLM saw rows like `correct:0  wrong:0  unresolved:N  accuracy:unresolved` and synthesized "0% / every configuration failed" — treating an empty denominator as total failure. STAND_DOWN VALIDATION and win-rate sections were already correct (their None-guards already produced "No resolved calls yet" / "Insufficient closed trades"). This is the calibration task for the continuous session-evaluation discipline (SF-7): the weekly auditor's stand-down validation must read honestly on thin-data weeks to be trustworthy as the automated cross-check. | owner + Claude Code | Root cause: the three breakdown sections had no guard for resolved_dir == 0; the LLM computed 0/0 as "0%" rather than recognizing "no denominator." Confirmed: thin-data audit week (all unresolved) produced "0% directional accuracy / every configuration failed" in the weekly audit output. | `performance_auditor.py`: `_format_stats_block()` only — 3 lines inserted + 3 `elif` replacements, 8 lines net. Commit `cdd2425`. |
| 2026-06-16 | 3B, SF-6 Rule 4 | **W-9 Phase 2 — OHLC detection + next-session-open window.** Replaced MEXC ticker-snapshot stop/T1 detection with 1m Kraken OHLCV candle scan. Filled trades now run until candle `low ≤ stop` (LONG) / `high ≥ T1`, bounded by next-session-open (next day 8:30 AM ET via `_next_session_open_utc`), not the 3 PM ET `session_expires_at`. Phase 2 expiry-override block removed — filled rows can no longer be stamped `EXPIRED/null` at session close. `_fetch_1m_since()` fetches Kraken 1m from `max(entry_filled_at, now−710min)` to ensure the scan always reaches now even after 12h. `_next_session_open_utc()` uses `session_manager.anchor_ts_for_utc_date` (+18h probe). Same-candle ambiguity: stop wins (conservative). Genuinely unresolved (neither stop nor T1 by next session open): `CLOSED_AT_EXPIRY / fractional R / target_hit="EXPIRY"`. Phase 1 (unfilled → EXPIRED at 3 PM) and Phase 3 (post-T1 snapshot observation) unchanged. Known limitation R1: closed_at for overnight stops lands on the next calendar date — group by `date_key`, not `closed_at::date`. | owner + Claude Code | Root cause: Phase 2 checked `now >= session_expires_at` before any price evaluation and `continue`d past all stop/T1 checks; confirmed by id=94 (filled 13:06 UTC, stop hit 22:26 UTC, wrongly stamped EXPIRED at 19:00 UTC with null PnL). Secondary: ticker["last"] missed intrabar moves between 60s polls. | `ledger_closing_engine.py`: Phase 2 rewritten; +`_fetch_1m_since()`, +`_next_session_open_utc()`; `ccxt.kraken` added as `_ohlc_exchange`; `from session_manager import …` added. |
| 2026-06-15 | 1A, W-12 | **Energy/level time-coherence gap fix — scheduler promoted to primary lock-time trigger.** Replaced `_seconds_until_utc(14, 0)` (hardcoded 14:00 UTC, DST-blind) with `_seconds_until_lock_end()` that reads `lock_end_ts` from `session_manager.resolve_current_session()` via pytz — EDT: 13:00 UTC, EST: 14:00 UTC. Boot-time check changed from `if now.hour >= 14` to `if now.timestamp() >= _boot_lock_end_ts`. `date_key` now comes from `session["date_key"]` in both boot and fire paths (no `strftime` drift). `import session_manager` added. Previously the scheduler fired at a fixed 14:00 UTC (an hour late in EDT) and page-visits consistently raced ahead — energy reads were sampled at page-visit time, not lock time. Now the scheduler is the PRIMARY trigger that fires at lock_end_ts; page-visit is the concurrent fallback. Double-fire guard in `battlebox_pipeline.py:528` is unchanged. | owner + Claude Code | Root cause: hardcoded UTC hour missed DST (fired at 10:00 AM EDT instead of 9:30 AM ET); page-visits raced ahead; energy reads sampled at page-visit time, not lock time — time-coherence gap with levels (always correctly bounded to the 9:30 AM calibration window). | `main.py`: +`import session_manager`, +`_seconds_until_lock_end()` helper, 3 logic changes in `run_senior_analyst_scheduler()`, 2 log-string updates in `_fire_senior_analyst()`. Commit `d9a4a92`. |
| 2026-06-19 | 1D (Panel 00) | **Panel 00 collapse toggle fix — dead CSS selector replaced.** The `▼/▶` button in the Analyst Brief header correctly toggled a `collapsed` class on `#analystBriefBody` and updated its icon, but the brief body never actually hid. Root cause: CSS rule targeted `#analystBriefBody.collapsed .ab-collapsible` (child elements with class `ab-collapsible`) but `loadAnalystBrief` renders `narrative-text` / `tactical-content` / `ab-line` elements — none with class `ab-collapsible`. The selector was dead from the start. Fix: replaced the child rule with `#analystBriefBody.collapsed { display: none; }`. The `.panel-head` (header row + toggle button) is a sibling of `#analystBriefBody`, so it stays fully visible and clickable in collapsed state. JS unchanged. Panel 02 and all other panels isolated (rule is ID-scoped). | Claude Code | Root cause: CSS `.ab-collapsible` child selector never matched any rendered element — the class was never added to the content divs. | `templates/market_radar.html`: 1 CSS line changed. Commit `b9d60dd`. |
| 2026-06-19 | 1D, 3B, Commlink | **UI source-of-truth honesty fixes — HUD key from CampaignLog, Panel 02 label, commlink scope declaration.** Three UI-only changes in `market_radar.html`: (1) `renderSnapshotGrid` now builds the HUD key from SA CampaignLog fields (`bias|SA_APPROVED|entry|stop|t1|t2|t3`) instead of the hardcoded empty string when `mas_status === 'APPROVED'` — fixes "DATA MISSING" in the cockpit HUD during the pre-radar-scan window. (2) `updateMtfOverlay` Phase 2 merge block protects `key` so a radar scan cannot blank a CampaignLog-sourced key on APPROVED sessions (added to the same guard block as `mas_status` and `plan`). (3) `loadPanel02Intel`: new branch — when SA is `APPROVED` or `WAITING_FOR_15M` and the JEWEL gate is closed post-lock, Panel 02 top line shows "JEWEL GATE CLOSED" (amber) instead of "GATE CLOSED — STAND DOWN" — removes false-veto language while keeping factual JEWEL state; all other branches (STAND_DOWN, HIGH CONVICTION, MODERATE SETUP, true-gate-closed fallback) untouched. One prompt-only change in `kabroda_mas_flow.py`: `COMMLINK_SYSTEM_PROMPT` now opens with an explicit scope declaration — commlink operates from the lock-time brief only, has no live price feed; when asked live trade-management questions (hold/close/where is price), the SA must state its data limitation and defer rather than give a confident directive off stale data. No trading logic, no indicators, no schema changes. **NOTE:** HUD key is 7 fields with `SA_APPROVED` grade label vs 9-field radar key — unverified against TradingView Pine parser; confirm on next approved-day copy test and pad tail with `||` if paste breaks. | owner + Claude Code | Root cause: (1/2) Phase 1 snapshot hardcoded `key: ''`; Phase 2 radar overwrote without guard; (3) Panel 02 `else` branch fired for APPROVED+JEWEL-closed state producing false "STAND DOWN" label; (4) commlink had no explicit scope boundary so it gave confident live directives from stale brief. Finding date: 2026-06-18 UI divergence investigation. | `templates/market_radar.html`: 5 lines changed. `kabroda_mas_flow.py`: prompt string only, 14 lines changed. Commit `eecc6ae`. |
| 2026-06-13 | 3B, W-11 | **W-11 — DecisionJournal source column + 4-value decision_type** — `DecisionJournal` had no `source` field; `kabroda_mas_flow` and `market_radar` both wrote to the same table, and the performance auditor queried all rows unfiltered. (1) Added `source VARCHAR` column to ORM + ALTER TABLE migration. (2) Historical backfill in `init_db()` (idempotent, `WHERE source IS NULL`): 30 MAS rows → `source='mas_flow'`; 393 radar rows → `source='market_radar'`. Backfill was required — switching the auditor to `source == "mas_flow"` without it would have orphaned all 423 pre-W-11 rows (NULL source after ALTER TABLE). (3) Both writers now set `source` at construction. (4) Auditor query switched from `decision_type.in_(["MAS_APPROVED", "MAS_REJECTED"])` workaround to `source == "mas_flow"`. (5) Binary ternary in `_inject_decision_journal()` replaced with 4-value map: `MAS_APPROVED / MAS_REJECTED / MAS_STAND_DOWN / MAS_WAITING`. Old binary collapsed STAND_DOWN and WAITING_FOR_15M into MAS_REJECTED — stand-down accuracy metric was uncomputable. (6) Auditor Block C filter changed from `decision_type == "STAND_DOWN"` (radar value, always 0 after W-11 query fix) to `decision_type == "MAS_STAND_DOWN"` (MAS value). Temp diagnostic route `/admin/decision-type-audit` deleted in same commit. | Claude Code | Root cause (W-11): two writers, no source field → auditor analyzed ~86 radar page-view events as trade decisions; 92% UNKNOWN kinematic_grade was radar contamination, not a pipeline bug. Workaround filter applied 2026-06-10 excluded radar but also excluded MAS stand-downs → Block C reported honest 0. | `database.py`: +1 ORM column, +1 migration, +1 backfill block. `kabroda_mas_flow.py`: ternary → 4-value map, +`source` arg. `market_radar.py`: +`source` arg. `performance_auditor.py`: query filter + 2× Block C filter. `main.py`: temp route deleted. |

| 2026-06-22 | 3B, SF-3 | **Forward-audit loop subsystem (harness).** `harness/audit_writer.py`: `write_decision_record()` captures frozen decision-time inputs to `session_audit_log` (idempotent — skips if row exists; internal try/except swallows DB errors); `backfill_outcome()` fills outcome write-once at resolution (skips if `outcome_type` already set). Production hook in `kabroda_mas_flow.py` Step 7: outer try/except around the call before `run_publisher()` — audit failure is logged, MAS path continues unaffected. Three backfill call sites in `ledger_closing_engine.py`: after `db.commit()` on CLOSED_WIN/CLOSED_LOSS, CLOSED_AT_EXPIRY, and NO_TRIGGER (EXPIRED) paths — all wrapped in try/except. Hard wall: `session_audit_log` and `trials_log` have no FK to `session_locks`, no write path to any live config or indicator column. `harness/README.md` documents the wall. `harness/test_audit_safety.py`: 4 tests / 7 assertions / all PASS — confirms broken audit write cannot block or alter trade decision path or close path (outer + inner two-layer protection). **PENDING LIVE VERIFICATION:** production tables created with correct schema / no hash-chain columns (Check 1 — SQL queries provided for Render Shell, not yet run); live session writes sane audit row with non-null RAG snapshot (Check 2 — pending next live session). | owner + Claude Code | Forward-audit discipline: capture decision-time inputs frozen at the moment of the call; back-fill outcomes write-once at resolution; non-blocking in both production call sites — trade path cannot be harmed by audit infrastructure. | `harness/audit_writer.py` (new). `harness/README.md` (new). `harness/test_audit_safety.py` (new). `kabroda_mas_flow.py`: +1 try/except block at Step 7. `ledger_closing_engine.py`: +3 try/except backfill blocks. |
| 2026-06-22 | 3A | **Analyst brief voice rewrite — verdict-first BLUF, behavior-before-label, named reasons, honesty calibration.** Seven edits to `SENIOR_ANALYST_SYSTEM_PROMPT` in `kabroda_mas_flow.py`: (1) WRITING RULES rewrite — lead with verdict, follow with rationale; register markers allowed ("the read is," "the lean is," "the structure favors" express calibrated uncertainty, not banned hedging); weak reads must stay tentative. (2) BEHAVIOR BEFORE LABEL — every system label in parentheses after plain-English description, never leading the sentence. (3) TRANSLATION TABLE — HOSTILE_CEILING, CHOP_RISK, PRIMED, OVEREXTENDED, REFUELING, TANGLED, SWEET_ZONE, DEPLETED/ACTIVE/BUILDING energy, and seven jargon terms ("Kinematic Grade," "Kinematic Fuel," "density cluster," etc.) mapped to behavior descriptions. (4) NAMED REASONS — each stand-down veto gets a plain-English heading ("Timeframe conflict," "Momentum is spent," "No room on the long side"); "Condition N fires" phrasing banned. (5) WHY THE SYSTEM STANDS DOWN format instruction. (6) VERDICT LINE — required plain-text first line before `## THE BIGGER PICTURE`: STAND_DOWN: "No trade today — [one-line reason]. Tradeable when [condition]."; APPROVED: "★ [direction]. Entry at $[price] on trigger acceptance. Stop: $[price]." (7) SELF-CHECK checks 10–12 (verdict line present; no untranslated labels; no "Condition N fires"). Wave-context caveat ("Note: Elliott Wave parameters pending weekly verification") eliminated — uncertainty woven into prose via language like "the structural map targets." Honesty calibration carve-out preserved: register markers allowed; weak evidence must not read with strong-evidence conviction. Faithfulness verified against June 22 STAND_DOWN brief — all price levels ($65,196.60 trigger, $65,418.86 T1, $1,606.23 box) and all three veto conditions present after voice change. **PENDING LIVE VERIFICATION:** first live session post-deploy renders with no leaked enums (no HOSTILE_CEILING, CHOP_RISK, "Condition N fires" in generated output). | owner + Claude Code | Root cause: production briefs led with rationale before verdict; used raw system-state labels (HOSTILE_CEILING, CHOP_RISK, "Condition 1 fires simultaneously") requiring prior-art knowledge to interpret; wave-context uncertainty bolted on as a disclaimer sentence instead of woven into prose. Diagnosed on June 22 STAND_DOWN brief. | `kabroda_mas_flow.py`: 7 edits to `SENIOR_ANALYST_SYSTEM_PROMPT`. No schema changes. No new tables. |

---

## SF-6 — CampaignLog Data-Model Invariants (added 2026-06-11)

Two permanent rules for anyone reading or writing `CampaignLog`. These are not implementation details — they are load-bearing constraints the lifecycle monitor, dashboard, and CRO RAG memory all depend on.

---

### Rule 1 — `is_canonical` gates all production reads

`CampaignLog.is_canonical` (BOOLEAN, default FALSE) distinguishes the production track record from legacy data (multi-symbol era, dollar-PnL format, pre-W-9 setup rows).

**Where it is set TRUE:**
- At creation in `_inject_brief_to_database()` for any `symbol == "BTC/USDT"` record — unconditional, covers every `approval_status` value (APPROVED, STAND_DOWN, REJECTED, WAITING_FOR_15M).
- Historically: IDs 74–90 were backfilled via `/admin/set-canonical` on 2026-06-11.

**Where it is filtered TRUE (16 sites):**
- All three lifecycle monitor phases (`ledger_closing_engine.py`)
- CRO RAG memory (`_fetch_cro_memory()`) and Commlink latest context
- Every dashboard consumer: `overview` (4 count queries), `mas-history` (approval_rows, pnl_rows, trades list), `jewel` trade lookup
- War Room `latest_log` and today's campaign query
- Radar snapshot today-campaign query
- Performance Auditor campaigns query
- Publisher `last_closed` and `weekly` archivist queries

**The one intentional exception:** `GET /admin/export-audit-ledger` sees ALL rows — it is an admin audit tool, not a production consumer.

**Why this matters:** any future query against `CampaignLog` must decide whether it is a production consumer (add `is_canonical == True`) or an admin/audit tool (see all). A new route that omits the filter silently inherits legacy garbage.

**The `mas_approval_status` trap:** Filtering on `mas_approval_status == "APPROVED"` does NOT exclude EXPIRED rows. `mas_approval_status` records the SA's pre-trade verdict and is never updated by the lifecycle monitor — a setup the SA approved can still become EXPIRED if entry never triggers. The `status` column is the lifecycle monitor's domain (`EXPIRED`, `CLOSED_WIN`, `CLOSED_LOSS`). Any query that uses `mas_approval_status` to find "actual completed trades" is on the wrong column — use `status.in_(["CLOSED_WIN", "CLOSED_LOSS"])` instead. This caused two independent crashes: `_fetch_archivist_data()` in `publisher_crew.py` and `_fetch_cro_memory()` in `kabroda_mas_flow.py`, both fixed 2026-06-12.

---

### Rule 2 — `entry_filled_at IS NULL` → `EXPIRED`, never `CLOSED_LOSS`

**The invariant:** A `CampaignLog` record cannot be `CLOSED_LOSS` if `entry_filled_at IS NULL`. A loss requires an entry. `entry_filled_at IS NULL` means the lifecycle monitor never observed price crossing the entry trigger — the trade was never opened.

**What the lifecycle monitor enforces (Phase 1):**
```
If session_expires_at IS NOT NULL AND now >= session_expires_at AND entry_filled_at IS NULL:
    status = "EXPIRED"
    realized_pnl = NULL
    closed_at = now
```

This fires whether or not price touched the stop. The stop can hit before entry is confirmed (price crossed entry trigger → reversed through stop trigger without first crossing entry) — this is the canonical phantom-loss trap. The monitor checks `entry_filled_at IS NOT NULL` before entering Phase 2 (stop/target evaluation).

**The historical phantom losses (corrected 2026-06-11):**
- ID 89 (2026-06-10): stop hit while `entry_filled_at IS NULL`. Reclassified `EXPIRED / realized_pnl=NULL`.
- ID 86 (2026-06-07): late PM trigger (~2:30 ET) outside AM session intent. Also `entry_filled_at IS NULL`. Reclassified `EXPIRED / realized_pnl=NULL`; `diagnostic_data.close_note` documents context.

**Job 2 (not yet built):** The lifecycle monitor enforces this rule going forward for new sessions. But it does not retroactively audit existing `CLOSED_LOSS` rows added before W-9 — those were corrected by hand for the 13 canonical rows. A future hardening pass could add a consistency check: `SELECT * FROM campaign_logs WHERE status='CLOSED_LOSS' AND entry_filled_at IS NULL` — any result is a data integrity violation.

---

### Rule 3 — `session_id` is a session-TYPE label, not a unique run identifier

`session_id` (e.g. `"us_ny_futures"`) comes from `SESSION_CONFIGS` in `session_manager.py`. It identifies the session *type*, not a specific daily run. The same string is written on every production run — today's `campaign_logs.session_id` and yesterday's are both `"us_ny_futures"`.

**The unique per-day key is the composite `(symbol, date_key, session_id)`.** This is the natural key `CampaignLog` uses as its upsert identity, and it is the correct join triple across all three tables:

```sql
campaign_logs.symbol        = interpreter_log.symbol
campaign_logs.date_key      = interpreter_log.session_date
campaign_logs.session_id    = interpreter_log.session_id

-- and now also:
decision_journal.symbol       = interpreter_log.symbol
decision_journal.session_date = interpreter_log.session_date
decision_journal.session_id   = interpreter_log.session_id
```

**The trap:** Any query filtering on `session_id = "us_ny_futures"` alone (without `date_key`) will return every row ever written — not a single session. A query joining on `session_id` alone will fan out into a Cartesian mess across dates. Always include `date_key` in the predicate when using `session_id`. Same class of column-misread as the `mas_approval_status` trap in Rule 1 — correct column, wrong assumption about its cardinality.

---

### Rule 4 — Filled trades resolve by stop/target/next-session-open, never 3 PM clock-EXPIRED

**The invariant:** Once `entry_filled_at IS NOT NULL`, a `CampaignLog` record cannot be closed with `status="EXPIRED"` and `realized_pnl=NULL`. A trade that has entered must exit via a defined outcome.

**What the lifecycle monitor enforces (Phase 2):**
```
Scan 1m Kraken OHLCV from entry_filled_at forward.
LONG: if any candle.low ≤ stop_loss → CLOSED_LOSS / -1.0R / target_hit="STOP"
      if any candle.high ≥ t1       → CLOSED_WIN  / +1.0R / target_hit="T1"
      stop-first on same-candle (conservative)

If no stop/T1 hit and now_utc ≥ next_session_open:
    → CLOSED_AT_EXPIRY / fractional R / target_hit="EXPIRY"

session_expires_at (3 PM ET) is the Phase 1 entry-window boundary ONLY.
It does NOT close filled trades.
```

**The converse of Rule 2:** Rule 2 says `entry_filled_at IS NULL → EXPIRED`. Rule 4 says `entry_filled_at IS NOT NULL → never EXPIRED`. These two rules together mean `EXPIRED` is exclusively the unfilled-trade outcome.

**Why `session_expires_at` cannot close filled trades:** A fill confirmed at 9:06 AM ET means an actual position was opened. That position does not cease to exist at 3 PM because the calibration session ended — the stop and target remain live until hit. The session clock bounds the setup window for new entries, not the lifecycle of an existing position.

**Root cause of the bug this rule prevents (W-9 Phase 2, confirmed 2026-06-15):** Phase 2 previously checked `now_utc >= session_expires_at` before fetching price, and `continue`d past all stop/T1 checks. A filled row at session close was stamped `EXPIRED/null` identically to an unfilled row — erasing the trade outcome. id=94 (2026-06-15 LONG, filled 13:06 UTC) was stamped EXPIRED at 19:00 UTC; its actual stop was hit at 22:26 UTC on MEXC 1m data.

---

## SF-7 — Operating Rhythm (Dual Accountability, active 2026-06-16)

The system's ongoing evaluation runs on two tracks:

**Automated half (weekly auditor):** `performance_auditor.py` runs each Sunday. Produces win-rate, directional accuracy, harmonic breakdown, and stand-down validation. Trustworthy only when resolved outcomes exist — thin-data weeks emit "INSUFFICIENT DATA" not 0% figures (W-15 fix). The auditor's stand-down validation is the longitudinal pattern-detector; it flags configs that are systematically over-cautious vs. over-aggressive.

**Human half (daily lightweight log):** Owner logs each session call (APPROVED / STAND_DOWN + one-line reasoning) and evaluates it against price in the 8–11 AM CST tradeable window. Three outcomes: correct stand-down, questionable stand-down (PRIORITY SIGNAL — flag with detail), approval-quality. One-line dated note for most sessions; flag days get detail. Notes keyed by `date_key` in a dated evaluation log (markdown for now; DB-attachment DEFERRED until volume justifies it).

**Longitudinal synthesis = conversation-Claude's standing job.** Reads cross-session threads in daily files and surfaces recurring patterns. Trigger to act = pattern clarity, not trade count or calendar.

**Full definition:** see WORK_LOG → OPERATING DISCIPLINE — CONTINUOUS SESSION-EVALUATION section.

---

## EXPANSION TIER — after 15M core solid

**W-14 (GATED):** the multi-timeframe + signal-conviction strengthening phase (per-TF engines, cross-week anticipation, signal-timing tool) follows the "15M core proven solid across many live sessions" gate. This system's nodes (1A–5D) describe the 15M pipeline only; W-14's per-TF engines would add parallel paths that do not yet exist in this map. See WORK_LOG W-14 for the component map, gate conditions, and Suggestion Box pin references.
