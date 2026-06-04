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

### W-7 ◐ EXHAUSTION BUG FIX — direction-blind abs() stack (Fix 1 + Fix 2)

- **What:** Three direction-blind `abs()` computations stack inside CONDITION 2 of the
  SA gate, causing the system to read a strong clean bearish trend as "exhausted" and
  issuing STAND_DOWN through confirmed downtrending sessions. Fix 1 and Fix 2 are pure
  math-layer changes (no LLM, no prompt). Fix 3 is a separate SA-prompt change deferred
  until after Fix 1+2 are proven live.

- **Why:** 4-day live replay (May 29 / Jun 1 / Jun 2 / Jun 3) confirmed the owner's
  read. On the three stand-down days the 4H ADX (corrected) was 29–57 with -DI dominant
  (11.5/28.1 → 5.8/36.2 → 4.8/35.6) — an unambiguous, accelerating bearish trend that
  should have been tradeable on the short side.

#### Validated findings (2026-06-03 replay)

**Three stacked bugs, not one:**

1. **`battlebox_pipeline._calculate_harmonic_matrix` — CONDITION 2(b) source**
   `spread_1h = abs(ema20_1h - ema50_1h) / ema50_1h` is direction-blind. A bear trend
   with a wide 1H EMA spread gets labeled `EXHAUSTION / OVEREXTENDED` identically to a
   bullish overextension. The `SWEET_ZONE_BEAR` path is unreachable whenever the trend
   has extended the EMAs (which any multi-day directional move will do).

2. **`battlebox_pipeline._build_synthetic_jewel` — CONDITION 2(c) source**
   `deviation_from_mean = abs(current_price - sma200) / sma200 * 100` is direction-blind.
   Price 4.38% below SMA200 on Jun 2 (strong bear run) labeled `OVEREXTENDED` identically
   to price 4.38% above SMA200 (exhausted long). This fed CONDITION 2(c) on all three
   stand-down days.

3. **CONDITION 2(a) — "4H Momentum NEGATIVE" is structurally always true in a downtrend**
   Driven by `MACD hist < 0`. In a bearish trend the MACD histogram IS negative — that is
   the definition of the trend, not a failure signal. On Jun 2 the histogram was −327; on
   Jun 3 it was −410. These indicate an accelerating downtrend, not an exhausted one. The
   gate checks for sign only, not magnitude or direction context. This is Fix 3 (prompt change).

**What Fix 1 alone produces (harmonic matrix only):**

| Session | Current COND2 | After Fix 1 only | Outcome |
|---------|--------------|-----------------|---------|
| Fri May 29 | 1/3 → no SD | 1/3 → no SD | Unchanged (spread was only 0.64%, fix irrelevant) |
| Mon Jun 1 | 2/3 (a+c) → SD | 2/3 (a+c) → **still SD** | Fix 1 has no effect — Jun 1 harmonic was already SWEET_ZONE_BEAR |
| Tue Jun 2 | 3/3 (a+b+c) → SD | 2/3 (a+c) → **still SD** | Removes b; a+c remain |
| Wed Jun 3 | 3/3 (a+b+c) → SD | 2/3 (a+c) → **still SD** | Same |

**Fix 1 alone changes nothing for any stand-down session. Must pair with Fix 2.**

**What Fix 1 + Fix 2 produces (both abs() bugs corrected, ADX-gated):**

| Session | After Fix 1+2 | COND2 met | Outcome |
|---------|--------------|-----------|---------|
| Fri May 29 | SWEET_ZONE_BEAR (unchanged) | 1/3 (a only) | No SD — correct |
| Mon Jun 1 | SWEET_ZONE_BEAR (unchanged) | 1/3 (a only) | No SD — correct |
| Tue Jun 2 | SWEET_ZONE_BEAR (fixed) | 1/3 (a only) | No SD — correct, was SD |
| Wed Jun 3 | SWEET_ZONE_BEAR (fixed) | 1/3 (a only) | No SD — correct, was SD |

All three stand-down sessions unblocked. Remaining CONDITION 2(a) (4H Momentum NEGATIVE)
is structurally always met in a downtrend — addressed in Fix 3 (deferred).

#### Build order (owner decision 2026-06-03 — foundation-first, no workarounds)

**Rationale:** the original plan used `adx / 14.0` to work around the `_calc_adx` Wilder init
bug while building Fix 1+2. Owner rejected this: Fix 1+2 are decision-logic that the whole
trade gate will run on — they must be built on rock, not on a known-broken function with a
fudge factor on top. Correct the foundation first, then build on it.

---

**Step 0 — Fix `_calc_adx` (Wilder init bug) — DO FIRST**

The bug: `_wilder()` at `battlebox_pipeline.py:126–130` initialises with
`s = [sum(vals[:period])]` instead of `s = [sum(vals[:period]) / period]`.
This makes the steady-state output `period × correct_ADX` (~14× true value).
Confirmed: synthetic test (constant DX=30 → output 420, not 30); stored session data
(4H ADX logged as 463, 1H as 159 — both impossible for a 0–100 indicator).

Fix: one character change — add `/ period` to the initial line. After this fix,
`_calc_adx` returns true 0–100 ADX values and `rising` behaviour is unchanged.

**Before deploying the `_calc_adx` fix — audit every consumer:**

All callers currently compare the raw (~14×) value against thresholds calibrated to the
inflated scale. Once the fix lands, every `adx > X` threshold in every caller becomes
wrong (comparing a true 0–100 value against an inflated threshold). Known consumers:

| File | Function | Uses ADX how | Threshold to recalibrate |
|------|----------|-------------|--------------------------|
| `battlebox_pipeline.py` | `_build_jewel_reading` | `adx > 25` → `adx_trending`; `adx_rising` | `> 25` → stays `> 25` (now correct) |
| `mtf_confluence_scanner.py` | `_analyze_timeframe` | `adx_val > 25` → `adx_strength = "STRONG"` | `> 25` → stays `> 25` (now correct) |
| Fix 1 (Step 1 below) | `_calculate_harmonic_matrix` | new — `adx >= threshold` | calibrate from replay (target ~20–25) |
| Fix 2 (Step 2 below) | `_build_synthetic_jewel` | new — `adx >= threshold` | calibrate from replay (target ~20–25) |

The existing `> 25` thresholds in `_build_jewel_reading` and `_analyze_timeframe` are
currently comparing against ~14× values. Once fixed, `> 25` is the correct Wilder
"trending" threshold — no change needed to those lines after the fix. Verify by spot-check
on a known trending session (Jun 2/3 should read ADX ~44/57 corrected, confirming strong trend).

**Step 0 output:** `_calc_adx` returns true 0–100. All existing `> 25` thresholds correct.
Stage but do not deploy until Steps 1+2 are written and validated together.

---

**Step 1 — Fix `battlebox_pipeline._calculate_harmonic_matrix` (lines 306–340)**

Built on real ADX (no `/14` workaround — Step 0 fix must already be applied).

- Compute `adx_4h = _calc_adx(candles_4h)` (same file, same candles — no new dependency).
- `trend_is_strong = adx_4h["rising"] and adx_4h["adx"] >= 20`
  *(CALIBRATION CHOICE A: threshold 20 vs 25 — validated from replay: Jun2 true ADX ~44,
  Jun3 ~57, both clear; May29 ~29, Jun1 ~32 — any threshold 20–30 passes all four days.
  Pick 20 for now; mark as calibration to revisit after 2–3 live sessions.)*
- In both aligned branches (bull+bull, bear+bear): replace bare `if is_exhausted` with
  `if spread_wide and not trend_is_strong`.
- `SWEET_ZONE_BEAR` is now reachable even with wide EMA spread, provided ADX confirms trend.

**Step 2 — Fix `battlebox_pipeline._build_synthetic_jewel` (lines 220–260)**

Built on real ADX (Step 0 applied).

- The function does not currently call `_calc_adx`. Add it:
  `adx_15m = _calc_adx(raw_15m)` — `raw_15m` is the same candle list already in scope.
- `trend_is_strong_15m = adx_15m["rising"] and adx_15m["adx"] >= 20`
  *(CALIBRATION CHOICE B: 15M ADX is noisier than 4H. May need a lower threshold, e.g. 18,
  or require more bars for stability. Validated in replay: Jun2 15M dev=4.38%, Jun3 dev=2.97%
  — both should flip to PRIMED/SWEET_ZONE not OVEREXTENDED on strong-trend days.)*
- Replace `if deviation_from_mean > 1.5: kinematic_grade = "OVEREXTENDED"` with
  `if deviation_from_mean > 1.5 and not trend_is_strong_15m: kinematic_grade = "OVEREXTENDED"`.
- Clean up the `exit_warning` tautology at the same time:
  `exit_warning` currently checks `deviation > 1.5 and kinematic_grade == "OVEREXTENDED"` —
  since the grade is assigned two lines above, the `and kinematic_grade` clause always
  equals the first clause. Simplify to just `kinematic_grade == "OVEREXTENDED"`.

**Step 3 — Fix 3 (CONDITION 2(a) direction-awareness) — DEFERRED, separate session**

- CONDITION 2(a) "4H Momentum is NEGATIVE" is auto-true in any downtrend (MACD hist < 0 is
  the definition of a bearish trend). It should read: NEGATIVE *against the trade direction*
  being evaluated. For a SHORT in a downtrend, NEGATIVE is confirmation, not failure.
- This is a prompt change to the SA system prompt — judgment layer, not math layer.
- **Do not bundle with Steps 0–2.** Validate Steps 0–2 live first. Fix 3 in its own session
  with SA output comparison before and after.

---

#### Pre-deploy checklist

- [ ] Step 0: Fix `_wilder` init in `_calc_adx` (one line: `/ period`). Run synthetic test — constant DX=30 must return ~30, not ~420.
- [ ] Spot-check Step 0 against stored May30 session: recalculate 4H ADX from stored candles and confirm it now reads in 0–100 range matching the corrected values (~33 for May30).
- [ ] Step 1: Write Fix 1 (`_calculate_harmonic_matrix`). Replay May29/Jun1/Jun2/Jun3 — confirm labels.
- [ ] Step 2: Write Fix 2 (`_build_synthetic_jewel`). Replay same 4 days — confirm 15M grade.
- [ ] Confirm Steps 0–2 together: all 3 stand-down days drop to 1/3 CONDITION 2 (a only). Fri May29 stays 1/3.
- [ ] Validate on 2 non-trending / reversal days (find a choppy or V-reversal session in the last 30 days). Confirm the gate STILL fires STAND_DOWN on those — the fix must not unblock everything, only strong-trend days.
- [ ] Deploy Steps 0–2 as a single commit (all three are coupled — Step 0 is the foundation, Steps 1+2 build on it).
- [ ] Watch first live session after deploy. Confirm `micro_state` and `kinematic_grade` appear correctly in the SA context (SWEET_ZONE_BEAR on a trending day, EXHAUSTION/TANGLED on a choppy day).
- [ ] Step 3 (CONDITION 2a direction-awareness) — separate session after live validation.

- **Status:** ◐ Validated — build order decided. Next action: fresh session, Step 0 first.
- **Depends on:** nothing (pure math layer)
- **Blocks:** Part 2 mean-reversion mode (Suggestion Box 2026-06-03) — build Part 2 only
  after Steps 0–2 are proven live.

---

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
| 2026-06-03 | **BUG: `_calc_adx` returns ADX ~14× true value** — `_wilder()` initialises with `sum(vals[:period])` instead of `sum(vals[:period]) / period`. Confirmed: synthetic test (constant DX=30 → 420, not 30); stored session data (4H ADX 463, 1H 159 — impossible for 0–100 indicator). `rising` flag and `+DI`/`−DI` correct. **Promoted to W-7 Step 0** (owner decision 2026-06-03): fix this FIRST, before building the ADX-gated exhaustion fix — build on rock, not a workaround. See W-7 build order for full consumer audit and recalibration plan. | `_calc_adx` audit during exhaustion-fix scoping 2026-06-03 | PROMOTED TO W-7 STEP 0 |
| 2026-06-03 | **EXHAUSTION FIX — PART 2 (conservative mean-reversion mode):** The ADX fix (Part 1) distinguishes strong-trend (continuation, full targets) from no-trend. Owner's framework for the no-trend case: when ADX is low/flat but RSI shows stretched (oversold/overbought), there IS a small mean-reversion move available — trade it CONSERVATIVELY, T1 only, no runner, because it is a small move on low-timeframe momentum, not a trend push. This matches the BTC chop pattern seen across the last 2–3 weeks. Three-state model: (1) strong trend + ADX high/rising = ride it, T1/T2/T3 full targets; (2) no-trend + ADX low/flat + RSI stretched = quick conservative T1 only, no extension; (3) genuinely unclear (no trend, RSI neutral) = STAND DOWN. Part 2 implementation = wire the ADX-low + RSI-stretched condition to a conservative-target posture in the fuel/allocation path. Build only after Part 1 is validated in live sessions. | owner framework, exhaustion-fix diagnostic session 2026-06-03 | TBD — build after Part 1 (ADX-gated harmonic matrix) validated live |

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
