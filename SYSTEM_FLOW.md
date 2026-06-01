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
- **ACTUAL:** `run_senior_analyst_scheduler()` in `main.py:194` fires daily at
  **14:00 UTC (9:00 AM ET)**. On server boot, if it is already past 14:00 UTC and
  no brief exists for today, it fires immediately (restart-recovery path).
  The actual MAS trigger is inside `battlebox_pipeline.get_live_battlebox()`: the
  moment a new `SessionLock` is written to the DB (after the 30-minute calibration
  window closes at 9:00 AM ET), `asyncio.create_task(asyncio.to_thread(run_mas_analysis, ...))` 
  fires at `battlebox_pipeline.py:538`. All candle data (5M / 15M / 1H / 4H /
  Daily) is fetched fresh from Kraken (ccxt) concurrently at call time — data IS
  fresh at the moment of decision. **Design note (Q4):** The 30-minute calibration
  window (8:30–9:00 AM ET) is a deliberate design choice — the opening-range
  high/low cannot be known before 9:00 AM ET, so the brief cannot be produced
  before the lock. Brief is available minutes after 9:00 AM ET. **Design under
  review** — if the brief must reach users before the session, the architecture
  would need a pre-session draft mode.
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

# CHANGE LOG
*Record every change to the system here so we can trace when a working flow broke.*

| Date | Node(s) | What changed | Who | Why | Result |
|------|---------|--------------|-----|-----|--------|
| 2026-06-01 | 1C, SF-5 | Feasibility study: MTF Interpreter layer. Confirmed CrewAI removed; all agents use agent_core pattern. Identified insertion point in run_mas_analysis(). Added SF-5 to SYSTEM_FLOW. Updated W-1 in WORK_LOG. | owner + Claude Code | W-1 design direction confirmed | No code changed — docs only. Ready to build on approval. |
| 2026-06-01 | MISSION | Added MISSION / CORE THESIS section (top of doc). Defines graduated-edge posture: weaker edge → T1 only; strong edge → scale/runner; no-sane-stop → stand down. Establishes probabilistic judgment mandate. | owner + Claude Code | Anchor the system's purpose before design questions | Docs only. |
| 2026-06-01 | SF-5 | Updated MTF Interpreter output spec: graduated alignment read (strength + conflict + stop/target/conviction implication) — NOT a binary flag. Anchored to Mission / Core Thesis. | owner + Claude Code | Prevent interpreter from producing a checkbox instead of a read | Docs only. |
