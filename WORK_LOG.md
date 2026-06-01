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

---

## CORE PRINCIPLE (decided 2026-06-01)
**Two layers, opposite treatment. Do not blur them.**
- **MATH / FACTS layer** — levels, 30M high-low, Fib targets, Elliott Wave counts,
  indicator readings. *Deterministic. Can be hard-coded. This is "reading the cards
  on the table."*
- **JUDGMENT layer** — given the facts, take / fold / stand down / size the trade.
  *Probabilistic. Stays with the LLM. This is "playing the hand."* Poker, not a
  vending machine. We never hard-code the decision to take or skip a trade.

The job of every structural fix is to make the FACTS layer cleaner and better
organized so the JUDGMENT layer decides on well-sorted information — NOT to
replace judgment with rules.

---

## OPEN WORK ITEMS (the real findings from the 2026-06-01 audit)

Status: ☐ not started · ◐ in progress · ☑ done

### W-1 ◐ Separate "organize/deal" from "decide" — MTF Interpreter Layer (SF-3 + SF-4 combined)
- **What:** The Senior Analyst currently organizes data AND decides AND writes,
  all in one LLM call. Split so the facts are cleanly pre-organized (deterministic
  where possible), the decision is a focused judgment call, and writing is separate.
- **Why:** Owner's "it always finds me a trade" problem + SA overload. The player
  is doing math in their head while deciding.
- **NOT doing:** hard-coding the take/skip decision. Judgment stays probabilistic.
- **Touches nodes:** 1C, 2A, 2D, 3A. **Depends on:** nothing.

#### Feasibility findings (2026-06-01)

**Design direction confirmed:** Insert "smart analyst agents" between the Python
math layer and the Senior Analyst. Each agent takes hard deterministic data for
its domain, interprets it, and hands the SA a clean synthesized read instead of
raw numbers. SA then makes the probabilistic call on pre-digested intelligence.

**CrewAI status:** Fully removed (see `kabroda_mas_flow.py:4`). All agents use
`agent_core._call_agent()`. This is the correct interface for new interpreters —
budget gate, prompt caching, and cost logging are already wired.

**Proof-of-concept target:** MTF Interpreter (multi-timeframe indicator domain).
This is the highest-value first insertion because the raw fuel gauge + JEWEL
snapshots are the largest uninterpreted data block the SA currently processes.

**Insertion point (zero-risk):**
- New file: `mtf_interpreter.py` (follows `elliott_wave_specialist.py` pattern)
- Edit 1 in `kabroda_mas_flow.py`: call interpreter after Trade Structure Analyst
  (line ~985), before `_build_senior_analyst_context()` (line ~994)
- Edit 2 in `kabroda_mas_flow.py`: add `mtf_read=` param to context builder;
  replaces `=== MULTI-TIMEFRAME ENERGY ===` block when interpreter succeeds
- Fail-open: if interpreter errors → `mtf_read = None` → raw format used as
  fallback → SA never loses data. All [OK] nodes untouched.

**What the interpreter reads (already assembled in `battlebox_payload`):**
  `context["fuel_gauge"]` (1H/4H/15M_JEWEL), `context["micro_state"]`,
  `context["1h_fuel_status"]`, and the last 6 JEWEL snapshots (from
  `_read_jewel_context()` — already called in `run_mas_analysis()`).

**What the interpreter outputs:** a plain-English synthesized read:
  "4H and 1H aligned BULLISH with building ADX. 15M PRIMED — ribbon 0.42%, not
  overextended. SWEET_ZONE harmonic confirms tide/wave agreement. No exit
  warnings. One conflict: weekly BBWP compressed, gate condition met. Direction
  vote 4/5 BULLISH. This picture supports a long breakout with velocity."

**Status:** feasibility confirmed — ready to build on approval.

### W-2 ☐ Decide the architecture question (SF-1)
- **What:** Talk through whether 1B–1F should become *real* agents or stay Python
  functions. Owner's lean: facts can be code; judgment needs a "thinking" layer.
- **Why:** The documented multi-agent pipeline doesn't exist; decide if that's a
  problem or just a mental-model correction.
- **Status:** not started — discussion, not code yet.

### W-3 ☐ Backtest the system on TradingView-connected software
- **What:** Owner has software that connects to TradingView and can backtest.
  Run the system's logic against history to get real results.
- **Why:** Validate whether the edge is real or the losing streak is variance.
- **Depends on:** clarity from W-1/W-2 so we know what we're testing.
- **Status:** parked until structure is settled — but HIGH priority to owner.

### W-4 ☐ (Phase 2, deferred) Publication delivery + auditor (nodes 5C, 5D)
- **What:** Build the publication auditor and the delivery mechanism (Ghost is a
  candidate platform). Newsletter must be a *forward-facing public voice* — intro,
  context, website + X links, engagement — NOT a copy of the internal brief.
- **Why:** This is the money. But it can't be trusted until Phase 1 is reined in.
- **Status:** deferred by design. Keep DRAFTS generating to learn the voice.

---

## DONE
*(move items here with date + commit hash when complete)*
- ☑ 2026-06-01 — Built SYSTEM_FLOW.md source of truth (blank template).
- ☑ 2026-06-01 — Ran read-only codebase audit; filled all ACTUAL fields. commit 6627dfe
- ☑ 2026-06-01 — Set up git + pushed to GitHub.
- ☑ 2026-06-01 — Created this WORK_LOG.md.

---

## SUGGESTION BOX (pin it, don't chase it)
*Ideas that came up mid-task. We do NOT act on these now. When current work is
done, we review this list and decide what graduates to OPEN WORK ITEMS.*

| Date | Idea | Came up while | Worth doing? |
|------|------|---------------|--------------|
| 2026-06-01 | Outside "researcher" agent that studies other trading styles / market approaches and evaluates what we're doing against them | discussing SA roles | TBD — review after W-1 |
| 2026-06-01 | Re-evaluate whether the 30-minute opening-range model is the right foundation (node 1A / Q4) | discussing "late to party" | TBD — strategy question |
| | | | |

---

## PARKING LOT — answered questions / decisions made
*So we don't re-litigate things we already settled.*

| Date | Question | Decision |
|------|----------|----------|
| 2026-06-01 | Should the trade gate be hard-coded? | NO. Facts can be coded; the take/skip judgment stays probabilistic (poker, not vending machine). |
| 2026-06-01 | Phase 1 or Phase 2 first? | Phase 1 structure first; keep Phase 2 drafts generating in parallel. |
| 2026-06-01 | Is the system broken? | Mostly no — most nodes are [OK]. The gap was mental-model vs. code, plus prompt-only enforcement of the gate. |
