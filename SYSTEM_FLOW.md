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

### 1A — System wake / trigger `[ ? ]`
- **SUPPOSED:** On the locked schedule, the system fires up and kicks every data
  agent into action. Pulls fresh market data for the symbol(s).
- **ACTUAL:** _(audit)_ — What triggers the run? What time? Is data fresh at the
  moment of decision, or already stale by the time the brief is read?
- **Feeds into:** 1B, 1C, 1D...

### 1B — Market Radar (V15) `[ ? ]`
- **SUPPOSED:** First-pass scanner. Produces the early directional read
  (e.g. "BULLISH 3/5 / BUILDING"). A *filter*, not a decision.
- **ACTUAL:** _(audit)_ — What inputs? What does the 0–5 score mean and how is it
  computed? Does it bias everything downstream toward one direction?

### 1C — Timeframe / indicator agents (1H, 4H, Daily, Weekly) `[ ~ ]`
- **SUPPOSED:** Each timeframe is evaluated. Indicators (RSI, MACD, Bollinger,
  etc.) read per timeframe. Output is a *structured* call per TF, e.g. "4H bearish
  but showing exhaustion," "1H flipped bullish," so it can actually weight the
  decision — not just be narrated.
- **ACTUAL:** _(audit)_ — Which agent owns which timeframe? Are outputs structured
  (gateable) or free-text (narration only)? Is higher-TF allowed to *veto* the 15m?
- **NOTE:** This is the #1 suspect zone (Q3). Owner sees higher-TF context
  *mentioned* but not clearly *controlling* the 15m call.

### 1D — Gravity Map (KDE structural density) `[ ? ]`
- **SUPPOSED:** Structural/anchor layer. Flags density zones, macro anchors, and
  "KINETIC FRICTION → NO TRADE" when price collides with a Class 0 anchor. A
  *veto / context* layer.
- **ACTUAL:** _(audit)_ — Does a NO-TRADE flag here actually block the trade
  decision in 2D, or is it advisory only?

### 1E — Macro War Room `[ ? ]`
- **SUPPOSED:** Higher-level macro context for the day/week.
- **ACTUAL:** _(audit)_ — What does it produce, and does anything downstream consume it?

### 1F — Any other gathering agents `[ ? ]`
- **SUPPOSED:** _(unknown — there may be 5–6 agents total; exact roster TBD)_
- **ACTUAL:** _(audit — list every agent that produces intel in this phase)_

---

## 2. RECONCILIATION & DECISION — Senior Analyst
*Everything from Section 1 is pushed up here. This node has the biggest lift.
This is also where the owner most suspects things go wrong (Q1, Q2).*

### 2A — Intake / collect `[ ? ]`
- **SUPPOSED:** Senior Analyst receives every agent's output. Knows which agent
  produced what.
- **ACTUAL:** _(audit)_ — Does it actually receive *all* outputs, or do some agents
  report late / get missed?

### 2B — Agent management & job-description check `[ ? ]`
- **SUPPOSED:** Confirms each agent did its defined job and stayed in its lane.
  Catches an agent that pops in out of sequence ("oh by the way, that won't work")
  *after* a decision is forming.
- **ACTUAL:** _(audit)_ — Is there real enforcement of agent job descriptions, or
  is this informal? Do agents report in a fixed order or can they interrupt?

### 2C — Conflict reconciliation / send-back-for-clarity `[ ~ ]`
- **SUPPOSED:** If outputs disagree (targets, stop, wave count, TF conflict), the
  SA sends the question *back down* to the relevant agent for clarity BEFORE
  committing. E.g. "1H bullish vs 4H bearish — which governs the 15m today?"
- **ACTUAL:** _(audit)_ — Does the send-back loop actually exist in code, or does
  the SA just average/narrate the conflict and move on? **Suspected weak point.**

### 2D — Trade GATE decision (take / stand down) `[ !! suspected ]`
- **SUPPOSED:** Decide whether there is a genuine high-probability 15m setup. Must
  be allowed to conclude **"no trade today"** when higher-TF structure, density
  anchors, or TF conflict say so. Should NOT manufacture a setup to have something
  to show.
- **ACTUAL:** _(audit)_ — **THE CORE QUESTION (Q1).** Is there an explicit gate /
  no-trade path? Or does the flow always terminate in "here's today's trade +
  levels"? If there's no real no-trade branch, that's the bug.

### 2E — Decision finalized & handed to writer `[ ? ]`
- **SUPPOSED:** Once 2D commits, the decision + supporting facts are packaged and
  passed to the brief writer (3A). SA should NOT necessarily write it itself.
- **ACTUAL:** _(audit)_ — Does the SA hand off, or does it also write? (Q2 — is the
  SA doing too much?)

---

## 3. BRIEF WRITING — turning the decision into the daily brief
*Open design question: should this be a SEPARATE writer agent (LLM) so the Senior
Analyst only decides, not writes?*

### 3A — Brief writer (LLM) `[ ? ]`
- **SUPPOSED:** Takes the finalized decision + facts from 2E and writes the daily
  brief in clear language. Articulates the trade, levels, invalidation/stop zones,
  and the "stand down if 15m closes above X" guidance.
- **ACTUAL:** _(audit)_ — Does a dedicated writer exist, or is the SA writing? Does
  the writer have authority to *add* reasoning, or only to phrase what 2E decided?
- **DESIGN NOTE:** Owner's proposed split — SA decides & packages (2), separate LLM
  writes (3). Keeps the SA from being overloaded (Q2).

### 3B — Store to database `[ ? ]`
- **SUPPOSED:** The decision + brief are stored for the weekly audit and for
  Phase 2 to read.
- **ACTUAL:** _(audit)_ — What is stored, where, in what format?

---

## 4. PRESENTATION — UI to the user
### 4A — UI render `[ ? ]`
- **SUPPOSED:** The brief and trade are presented cleanly on the suite UI
  (Market Radar / cockpit / dashboard) so the user understands it at a glance.
- **ACTUAL:** _(audit)_ — Is what the UI shows the SAME as what the pipeline
  decided, or does the UI re-derive / re-summarize and introduce drift?

---

# PHASE 2 — PUBLICATION DEPARTMENT
*This is the sellable product. A whole separate "department" downstream of the
Phase 1 decision. Treated as its own flow.*

### 5A — Publish agent intake `[ ? ]`
- **SUPPOSED:** Receives everything from Phase 1 (the decision, the brief, stored
  data). Can read the database for weekly / bigger-timeframe / last-week /
  coming-week context.
- **ACTUAL:** _(audit)_ — What does it read, and does it re-analyze or just reformat?

### 5B — Newsletter / publication build `[ ? ]`
- **SUPPOSED:** Builds the final public-facing newsletter for paying subscribers.
- **ACTUAL:** _(audit)_ — Is there a publication auditor that checks it before it
  goes out? (Owner mentioned a "publication auditor" — confirm it exists.)

### 5C — Publication auditor `[ ? ]`
- **SUPPOSED:** Reviews the published product for accuracy/consistency with the
  Phase 1 decision before release.
- **ACTUAL:** _(audit)_ — Confirm existence and what it checks.

### 5D — Release to subscribers `[ ? ]`
- **SUPPOSED:** Final product delivered to the public/paying audience.
- **ACTUAL:** _(audit)_ — Channel, timing, who triggers it.

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

# CHANGE LOG
*Record every change to the system here so we can trace when a working flow broke.*

| Date | Node(s) | What changed | Who | Why | Result |
|------|---------|--------------|-----|-----|--------|
| _e.g. 2026-05-26_ | _2D_ | _adjusted short trigger SOB_ | _owner_ | _too many entries_ | _net R worse_ |
| | | | | | |
