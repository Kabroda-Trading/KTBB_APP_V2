# KABRODA — Work Log & Suggestion Box

**Why this file exists.** To stop *drift*. The pattern we are killing: starting on
one thing, a question comes up, we chase it, and by end of day we never finished
the thing we started and can't remember what the next step was. This file is the
answer to "What were we doing? What's next? What did we agree to leave alone?"

**Companion to:** `SYSTEM_FLOW.md` (the source-of-truth map). This file is the
*to-do and decisions*; that file is the *system description*. Update both.

**Rules of the road:**
- We finish what's IN PROGRESS before starting something new.
- New ideas mid-task go in the SUGGESTION BOX — not acted on now, not lost.
- When a task is done, check it off and note the commit hash.
- Every code change also gets a row in the SYSTEM_FLOW.md CHANGE LOG.
- **Standing instruction (2026-06-01):** Claude Code maintains this file on every
  task — pins ideas in SUGGESTION BOX, pins decisions in PARKING LOT, updates
  DONE and W-status as work completes. Do not wait to be asked.

---

## CORE PRINCIPLES (decided 2026-06-01)

### Principle 1 — Two layers, opposite treatment
**Do not blur them.**
- **MATH / FACTS layer** — levels, 30M high-low, Fib targets, Elliott Wave counts,
  indicator readings. *Deterministic. Can be hard-coded. This is "reading the cards
  on the table."*
- **JUDGMENT layer** — given the facts, take / fold / stand down / size the trade.
  *Probabilistic. Stays with the LLM. This is "playing the hand."* Poker, not a
  vending machine. We never hard-code the decision to take or skip a trade.

The job of every structural fix is to make the FACTS layer cleaner and better
organized so the JUDGMENT layer decides on well-sorted information — NOT to
replace judgment with rules.

### Principle 2 — Clerk vs Interpreter (Bucket A / Bucket B)
Every module in the system belongs to one of two buckets. See AGENT BUCKETS
section in SYSTEM_FLOW.md for the full classification.

- **Bucket A — CLERK:** produces a locked-in fact (a level, a raw number, a
  structured dict) by applying deterministic math or a fixed rule. Fetching and
  packaging only. No LLM required, no interpretation.
- **Bucket B — INTERPRETER:** the same raw input means different things in
  context — requires digestion into a judgment before the SA sees it. LLM
  required.

**The test:** if a module could be replaced with a lookup table or a formula, it
is Bucket A. If its output changes meaning based on what else is true today, it
is Bucket B.

### Principle 3 — SA reads only digested reads
Every new connection to the Senior Analyst must **reduce** the SA's cognitive
load by digesting its domain first. The SA must never receive a raw data dump
from a reconnected or new agent. If a connection would cause the SA to read raw
numbers rather than a judgment, it must go through an Interpreter (Bucket B) first.

---

## SUCCESS METRIC (owner framing, 2026-06-03)

The win rate that matters is **NOT** raw % of all possible trades — it's the system's
**WEATHER-READING accuracy**, measured two ways:

**(1) SELECTIVITY — when KABRODA STANDS DOWN, would trading have lost?**
Stand-downs that avoided bad days are "wins" (capital protected), not missed trades.
The Performance Auditor already tracks this: `STAND_DOWN accuracy (saved / resolved)`
in the weekly audit. A STAND_DOWN that fires on a day price moved against the indicated
direction = veto worked. A STAND_DOWN where price moved in the indicated direction = veto
may have been overcautious. Over time, this ratio is the weather-reading score.

**(2) ACCURACY WHEN IT ACTS — win rate AND expectancy on greenlight days.**
A high win rate with bigger losses still loses money. The real measure is:
`avg_win × win_rate − avg_loss × loss_rate > 0` (positive expectancy).
CampaignLog has `realized_pnl` on closed trades. The Performance Auditor's Net R
already approximates this. Goal is positive expectancy — a win-rate number alone
is not meaningful without the loss-size context.

**Owner's framing (two-trader model):** the edge is in knowing when NOT to trade
(sit out the storms). Win rate naturally rises when you only act on clear-weather
days. The system's weather-reading is validated by:
- STAND_DOWN accuracy rate (avoided bad days vs. overcautious vetoes)
- Win rate + expectancy on APPROVED sessions only (not all sessions)

**FUTURE — tiered position sizing (post-validation):**
Once the junior analyst is proven reliable in InterpreterLog, use agreement strength
to drive position size: strong agree = clear weather = full size; partial agree = murky
= reduced size; conflict = storm = stand down or minimal exposure. This is why the
junior analyst earns its seat: it can eventually quantify HOW clear the day is, not
just whether to act.

**VALIDATION PATH (W-3):**
Join `interpreter_log` stand-down/greenlight sessions to `campaign_logs` + subsequent
price action: "does the system read the weather correctly?" Requires several weeks of
logged InterpreterLog data (including `interpreter_name = 'junior_analyst'` rows).
This is the W-3 backtest target — not a generic backtester, but a weather-reading audit.

---

## ► NEXT SESSION START
*End-of-session marker: 2026-06-03*

**WATCH FIRST:** Read the next post-9:00 AM ET brief and verify BOTH interpreter reads.

**(a) MTF interpreter** — second+ live brief. Confirm still sharp.

**(b) Gravity interpreter** — FIRST live brief. Confirm `=== GRAVITY LANDSCAPE (INTERPRETED) ===` section appears, reads as a characterization not a list, covers both directions, and that the SA visibly reasons about momentum (MTF) AND structure (gravity) together.

Bring both reads here for review before acting on them.

**THEN:** GAP-3 (junior analyst) becomes buildable — its precondition (two live interpreters) is now met, but its design should be informed by how MTF and gravity agree/conflict in live briefs, so gather a few sessions of evidence first.

**Open design question still pending (GAP-3):** junior analyst as new module vs. MTF-pattern generalized.

---

## OPEN WORK ITEMS

Status: ☐ not started · ◐ in progress · ☑ done

### W-1 ◐ Separate "organize/deal" from "decide" — interpreter layer build
- **What:** The Senior Analyst currently organizes data AND decides AND writes,
  all in one LLM call. Split so the facts are cleanly pre-organized (deterministic
  where possible), the decision is a focused judgment call, and writing is separate.
- **Why:** Owner's "it always finds me a trade" problem + SA overload. The player
  is doing math in their head while deciding.
- **NOT doing:** hard-coding the take/skip decision. Judgment stays probabilistic.
- **Touches nodes:** 1C, 2A, 2D, 3A. **Depends on:** auditor-wire fix (W-5).

#### Progress log

**2026-06-01 — Feasibility confirmed** (commit dedf145)
- Design direction confirmed: insert Interpreter agents between the Python math
  layer and the Senior Analyst.
- CrewAI fully removed; all agents use `agent_core._call_agent()` — correct
  interface for new interpreters.
- MTF Interpreter identified as proof-of-concept target (largest uninterpreted
  block SA currently processes).
- Insertion point: `run_mas_analysis()` between Trade Structure Analyst call
  (~line 985) and `_build_senior_analyst_context()` (~line 994). Fail-open.

**2026-06-01 — Connection audit completed** (commit 756abd6)
- Full wiring map built (see CONNECTION MAP in SYSTEM_FLOW.md).
- Found 2 full orphans: `live_telemetry.py`, `liquidity_oracle.py` (CrewAI
  casualties — no callers).
- Found 1 broken wire: `performance_auditor` writes `SystemAuditLog.audit_md`;
  SA reads `MacroNarrativeLog.performance_note` (never written). SA never sees
  the auditor note. Fix identified — awaiting approval (see W-5).
- JEWEL snapshot path confirmed intact.

**2026-06-01 — Agent bucket classification completed** (commit 303f838)
- All 25 modules classified: 19 Clerks (Bucket A), 5 Interpreter roles (Bucket B).
- Confirmed LLM footprint is appropriately narrow: 4 files, 6 call sites.
- MTF Interpreter will be a new Bucket B sitting between `mtf_confluence_scanner`
  (A) and Senior Analyst (B).

**Current status:** DEPLOYED live on commit ae45a71. Interpreter fires on next
session lock (9:00 AM ET 2026-06-02). NEXT ACTION: read the first live brief,
confirm the MTF interpretation appears and reads sharper — bring the actual
output for review before closing W-1.

### W-2 ◐ Architecture question largely answered by bucket work
- **What:** Whether 1B–1F should become real agents or stay Python functions.
- **Answer (from bucket classification):** Facts stay Python (Bucket A) —
  no LLM needed. Interpretation needs LLM (Bucket B). The architecture is:
  Bucket A modules feed Bucket B interpreters, which feed the SA. See Principle 2.
- **Remaining open question:** which Bucket A modules deserve a Bucket B
  interpreter layer above them (beyond MTF, which is W-1)? Gravity? Macro
  context? Discuss after W-1 is live and we can measure the improvement.
- **Status:** partially resolved — close fully after W-1 ships.

### W-3 ☐ Backtest the system on TradingView-connected software
- **What:** Owner has software that connects to TradingView and can backtest.
  Run the system's logic against history to get real results.
- **Why:** Validate whether the edge is real or the losing streak is variance.
- **Depends on:** clarity from W-1/W-2 so we know what we're testing.
- **Status:** parked until structure is settled — HIGH priority to owner.

### W-4 ☐ (Phase 2, deferred) Publication delivery + auditor (nodes 5C, 5D)
- **What:** Build the publication auditor and the delivery mechanism (Ghost is a
  candidate platform). Newsletter must be a *forward-facing public voice* — intro,
  context, website + X links, engagement — NOT a copy of the internal brief.
- **Why:** This is the money. But it can't be trusted until Phase 1 is reined in.
- **Status:** deferred by design. Keep DRAFTS generating to learn the voice.

### W-5 ☑ Fix auditor-wire break — DONE

### W-6 ☐ DASHBOARD ACCURACY AUDIT
- **What:** Read-only audit of the performance dashboard — trace every displayed
  number to its source query, verify correctness, then fix. The dashboard must be
  an auditably correct scorecard before it can be trusted as the system's primary
  feedback loop.
- **Why (observed 2026-06-03):** Five specific issues found:
  1. **Contradiction:** stat box shows "Net R lifetime +1R" but the cumulative chart
     ends at approximately −6R. One is mislabeled. Both cannot be correct.
  2. **Missing time windows:** "22 total sessions / 31.8% approved / 57.1% win rate"
     with no stated date range. Every metric needs its period labeled.
  3. **Bug:** "Loading trade history…" table never populates.
  4. **"Error/Other" slice:** largest category in the MAS approval distribution. Need
     to know what records count as Error/Other and whether they are real errors or
     non-approved sessions miscategorized. This could be masking the real approval rate.
  5. **No tooltips/labels:** no hover text explaining what each metric measures or
     which table/column it reads from.
- **This is the analytics equivalent of the cockpit contradiction** — a scorecard that
  contradicts itself cannot be used to evaluate the system or validate the SUCCESS METRIC
  framing (see above). It must be correct before the weather-reading audit (W-3) is meaningful.
- **Protocol:** read-only audit FIRST (trace each number to its query and flag every
  discrepancy). Fix pass SECOND. Do NOT bundle into another build — this is a focused,
  standalone session.
- **Status:** not started — own focused session when ready.

---

## DONE
*(move items here with date + commit hash when complete)*

- ☑ 2026-06-01 — Built SYSTEM_FLOW.md source of truth (blank template). commit 22bdc36
- ☑ 2026-06-01 — Ran read-only codebase audit; filled all ACTUAL fields. commit 6627dfe
- ☑ 2026-06-01 — Set up git + pushed to GitHub.
- ☑ 2026-06-01 — Created WORK_LOG.md.
- ☑ 2026-06-01 — Added MISSION/CORE THESIS to SYSTEM_FLOW; anchored SF-5 MTF Interpreter output spec to graduated-judgment mandate. commit 2affbe8
- ☑ 2026-06-01 — W-1 feasibility study: CrewAI audit, insertion point identified, SF-5 architecture written. commit dedf145
- ☑ 2026-06-01 — Connection map audit: 2 orphans + broken auditor wire found; JEWEL confirmed. commit 756abd6
- ☑ 2026-06-01 — Agent bucket classification: 19 Clerks, 5 Interpreters. commit 303f838
- ☑ 2026-06-01 — W-5: Fixed broken auditor wire. SA now receives Performance Auditor note via SystemAuditLog query. commit 65fe7e8
- ☑ 2026-06-02 — W-1 MTF Interpreter: DEPLOYED live. commit ae45a71 (built) + a596909 (prompt refinements)
- ☑ 2026-06-02 — GAP-1/GAP-2: Cockpit authority fix. Gray row border on STAND_DOWN, "STAND DOWN — SYSTEM INACTIVE" in Panel 02, blank trade card verified on screen on stand-down session. commit 8153553
- ☑ 2026-06-02 — GAP-4 Phase 1: Gravity Interpreter DEPLOYED live — running alongside MTF interpreter as of 2026-06-02. Prompt reviewed and refined (both-directions coverage, decisively probabilistic rule, 6–8 sentences, max_tokens 600). commits 5ebbc2b (build) + 27cd466 (prompt refinements)

---

## SUGGESTION BOX (pin it, don't chase it)
*Ideas that came up mid-task. We do NOT act on these now. When current work is
done, we review this list and decide what graduates to OPEN WORK ITEMS.*

| Date | Idea | Came up while | Worth doing? |
|------|------|---------------|--------------|
| 2026-06-01 | Outside "researcher" agent that studies other trading styles / market approaches and evaluates what we're doing against them | discussing SA roles | TBD — review after W-1 |
| 2026-06-01 | Re-evaluate whether the 30-minute opening-range model is the right foundation (node 1A / Q4) | discussing "late to party" | TBD — strategy question |
| 2026-06-01 | `live_telemetry.py` (Coinalyze OI fuel) and `liquidity_oracle.py` (Binance L2 depth) are orphaned but may contain real signal — decide whether open-interest and order-book depth belong in the trade read at all before reconnecting or deleting | connection audit | TBD — only reconnect if data earns a place; Bucket A feed to a future Bucket B interpreter |
| 2026-06-01 | The "self-audit / learning loop" we discussed wanting IS already partially real — `performance_auditor.py` already runs weekly, reads closed-trade outcomes, and synthesises calibration recommendations. It was just disconnected from the SA. Once W-5 is merged, the loop exists. The open question is whether to make it more granular (post-session feedback, not just weekly). | connection audit | PARTIALLY REAL — do not build from scratch; extend what exists after W-5 |
| 2026-06-01 | `crewai` and `langchain-anthropic` still install via `requirements.txt` even though the code no longer uses them (confirmed by audit — all agent calls go through `agent_core._call_agent()`). Dead dependencies — clean out of `requirements.txt` eventually to avoid confusion and unnecessary build weight. Not urgent. | audit confirmed CrewAI fully removed | TBD — low priority housekeeping |
| 2026-06-02 | **HIGH — GRADING SYSTEM REDESIGN (owner insight):** The current single 'Grade A/B/STAND DOWN' score is opaque and can mislead — example: 2026-06-01 brief graded a short 'Grade A' but then capped it at T1-only due to 15M exit warning + negative 4H momentum (an A setup shouldn't need timid management — the grade and the trade plan contradicted each other). PROPOSED: a COMPOSITE/GRADIENT grade where each interpreter agent grades its OWN domain (e.g. MTF interpreter: alignment strength score; future liquidity interpreter: sweep-risk score; structure interpreter: its own confidence), and the OVERALL grade is composed from those domain grades. Benefits: (1) the grade becomes inspectable — when a trade goes wrong you can see which domain over/under-graded it; (2) the Performance Auditor can then calibrate per-domain, not just overall. This is a natural extension of the interpreter architecture — each interpreter emits a confidence/grade alongside its read. | owner observation post 2026-06-01 brief | TBD — review after MTF interpreter is proven live |
| 2026-06-02 | Minor UI bug — BTC Mission Cockpit, Panel 00 (Analyst Brief): the collapse/expand arrow (top-right) animates/rotates on click but the brief content does not actually expand or collapse. Intended behavior: arrow toggles the full analyst brief open/closed so the user can jump straight to the trade or expand to read. Likely a JS handler that toggles the arrow state but doesn't show/hide the content div. Low priority — cosmetic, no safety impact. Fix opportunistically when next in `market_radar.html`. | verifying GAP-1/GAP-2 cockpit fix | Low priority — fix opportunistically |
| 2026-06-02 | GAP-5 — retire `MtfReading` table from `/api/radar/snapshot` Phase 1 display (currently drives the cached MTF direction badge); replace with Junior Analyst package output once GAP-3 is built. No action needed until GAP-3 exists. | SYSTEM_FLOW.md gap list | TBD — after GAP-3 |
| 2026-06-02 | GAP-4 Phase 2 — orphan reconnection: (a) `live_telemetry.py` (OI delta) low-risk — endpoint looks current, fails safe if API key absent, only unknown is sort-order on Coinalyze response; (b) `liquidity_oracle.py` (L2 depth) harder — depends on `BINANCE_PROXY_URL` that may be dead on Render, plus raw output is 2,000 number-pairs needing a Python wall-detection math layer before it feeds an interpreter. Phase 2 only after Phase 1 proven live and orphan status verified. | GAP-4 scoping 2026-06-02 | TBD — Phase 2, after Phase 1 proven |
| 2026-06-03 | **JA v2 (post-data)** — once InterpreterLog shows the junior analyst's synthesis is reliably complete over several sessions: (a) consolidate SA context so the JA package REPLACES the raw interpreter reads — reduces SA cognitive load per MAP 2 / Principle 3; (b) tune the JA prompt from the outcome record — join `interpreter_log` (junior_analyst rows) to `campaign_logs` outcomes to see which syntheses preceded wins vs. losses. This is the "senior track-record trains the junior" loop — the Performance Auditor runs it weekly once data exists. | GAP-3 build 2026-06-03 | TBD — after several sessions of InterpreterLog data |
| 2026-06-03 | **`/api/dmr/run-raw` endpoint is broken** — calls `battlebox_pipeline.get_session_review()` which does not exist anywhere in the codebase. Throws `AttributeError` on every call. Dead operator endpoint — no user-visible feature depends on it. Low priority: fix (wire to a real function) or remove when convenient. Found during GAP-4 gravity-interpreter diagnosis 2026-06-03. | GAP-4 diagnosis | Low priority — fix or remove |
| 2026-06-03 | **PRODUCT NAMING** — "newsletter" is a placeholder the owner dislikes; consumers ignore newsletters. The product is closer to a market weather/conditions report (ties to the weather analogy: "is today clear or stormy for trading?"). Rework naming + the publication's framing when Phase 2 (publication delivery) is activated. The stand-down communication especially should use the weather framing — "storm coming, stay off the water" — so that stand-downs read as protection, not missed trades. Not now — Phase 2 branding work. | owner, 2026-06-03 | TBD — Phase 2 |
| 2026-06-03 | **Mean-reversion trader benchmark** — once we have enough closed CampaignLog records, run Kabroda's win-rate/R-multiple against a naive mean-reversion baseline (e.g., fade every breakout, take profit at the opposing trigger). If Kabroda doesn't beat the fade, the breakout thesis needs scrutiny. Belongs in W-3 backtest scope. | owner discussion, end-of-session 2026-06-03 | TBD — after W-3 backtest setup |

---

## PARKING LOT — answered questions / decisions made
*So we don't re-litigate things we already settled.*

| Date | Question | Decision |
|------|----------|----------|
| 2026-06-01 | Should the trade gate be hard-coded? | NO. Facts can be coded; the take/skip judgment stays probabilistic (poker, not vending machine). |
| 2026-06-01 | Phase 1 or Phase 2 first? | Phase 1 structure first; keep Phase 2 drafts generating in parallel. |
| 2026-06-01 | Is the system broken? | Mostly no — most nodes are [OK]. The gap was mental-model vs. code, plus prompt-only enforcement of the gate. |
| 2026-06-01 | Do we need CrewAI back to build smart/interpreter agents? | NO. Smart-agent behavior is a role and wiring upgrade on the existing `agent_core` pattern — not a framework problem. CrewAI was removed deliberately; do not reintroduce it. |
| 2026-06-01 | Won't reconnecting more agents overload the Senior Analyst? | Only if they dump raw data at it. GOVERNING RULE (Principle 3): the SA reads ONLY digested/interpreted reads, never raw feeds. Every new or reconnected agent must reduce the SA's cognitive load by digesting its domain first. A connection that would send raw numbers is not ready — it needs a Bucket B interpreter in front of it first. |
