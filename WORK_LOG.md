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
