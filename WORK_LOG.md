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

### W-7 ◐ EXHAUSTION BUG FIX — Steps 0+1+2 DONE (deployed 80b1d79 · 2026-06-04); Fix 3 OPEN

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

**As planned (original approach — superseded):**
- Add `adx_15m = _calc_adx(raw_15m)` and gate on `adx_15m["rising"] and adx_15m["adx"] >= 20`.

**As implemented (deviation from plan — confirmed necessary):**
- During build, replay revealed the 15M ADX decays rapidly after the initial directional
  move: Jun 3 session-open 15M ADX=14.3 (not rising) despite 4H ADX=57.2. A 15M-only gate
  would have re-blocked Jun 3 exactly as the original bug did, just via a different path.
  The direction-aware DI check (minus_di > plus_di + price below SMA200 = trend) was also
  tried but proved too permissive: Apr 28 (ranging, 4H ADX=12.2) had a short intraday 15M
  drop that gave -DI dominance, which would incorrectly suppress OVEREXTENDED on a range day.
- **Final implementation:** `_build_synthetic_jewel` accepts `adx_4h: Optional[Dict] = None`.
  Gate: `adx_4h_strong = adx_4h rising AND adx_4h.adx >= 25`.
  OVEREXTENDED fires only when `deviation > 1.5% AND NOT adx_4h_strong`.
  `_build_fuel_gauge` passes `adx_4h=_calc_adx(raw_4h)` to the call.
  *(CALIBRATION CHOICE B: threshold 25 for 4H. Correctly separates ranging intraday spike
  (Apr 28: 4H ADX=12.2 → gate=False → OVEREXTENDED preserved) from trend continuation
  (Jun 3: 4H ADX=57.2 → gate=True → protected). Revisit after 2–3 live sessions.)*
- `exit_warning` tautology removed:
  simplified from `(deviation > 1.5 AND grade == "OVEREXTENDED")` to `(grade == "OVEREXTENDED")`.

**Step 3 — Fix 3 (CONDITION 2(a) direction-awareness) — DEFERRED, separate session**

- CONDITION 2(a) "4H Momentum is NEGATIVE" is auto-true in any downtrend (MACD hist < 0 is
  the definition of a bearish trend). It should read: NEGATIVE *against the trade direction*
  being evaluated. For a SHORT in a downtrend, NEGATIVE is confirmation, not failure.
- This is a prompt change to the SA system prompt — judgment layer, not math layer.
- **Do not bundle with Steps 0–2.** Validate Steps 0–2 live first. Fix 3 in its own session
  with SA output comparison before and after.

---

#### Pre-deploy checklist

- [x] Step 0: Fix `_wilder` init in `_calc_adx`. Two changes (seed + recurrence formula). Synthetic DX=30 → ADX=30.0 ✓
- [x] Spot-check Step 0: May30 stored 4H ADX 463.46 ÷ 14 = 33.1 — matches plan's ~33 prediction ✓
- [x] Step 1: Fix 1 (`_calculate_harmonic_matrix`). Replay confirmed: Jun2/3 → SWEET_ZONE_BEAR, May29/Jun1 unchanged ✓
- [x] Step 2: Fix 2 (`_build_synthetic_jewel`). 4H-ADX gate (threshold 25) — see implementation note above ✓
- [x] Combined CONDITION 2 table: all four days at 1/3 (COND2(a) only). May29 unchanged ✓
- [x] Negative test (3 non-trend days, identical candles, old vs new): Apr28 SD preserved; May15/May27 unchanged (pre-existing, not regressions) ✓
- [x] Deployed as single commit **80b1d79** (2026-06-04) — one file, 23 insertions / 13 deletions ✓
- [ ] Watch first live session post-deploy — see NEXT SESSION below.
- [ ] Step 3 (CONDITION 2a direction-awareness) — separate session after live validation.

- **Status:** Steps 0+1+2 DEPLOYED 2026-06-04 (commit 80b1d79). Fix 3 OPEN.
- **Positive validation:** Jun1/Jun2/Jun3 2026 unblocked (OLD=SD → NEW=no SD on all three).
- **Negative validation:** Apr28/May15/May27 2026 — no regressions (Apr28 SD preserved; May15/27 unchanged pre-existing behavior).
- **Depends on:** nothing (pure math layer — shipped)
- **Blocks:** Part 2 mean-reversion mode (Suggestion Box 2026-06-03) — build Part 2 only
  after Steps 0–2 are proven live.

---

#### NEXT SESSION — post-deploy live watch (2026-06-04 onwards)

Watch the first live NY Futures session that runs through the fixed code on Render.

**If the SA brief still cites exhaustion / OVEREXTENDED labels:**
- The Render instance may not have picked up commit 80b1d79. Check deploy logs.
- Verify the running process has `_wilder` seeding with `/ period` (quick health check: hit
  `/api/gravity/scan` and inspect the `fuel_gauge.4H.jewel.adx` value — must be in 0–100).

**If the SA brief stands down citing only "4H Momentum NEGATIVE":**
- Steps 0+1+2 are working as expected. This is Fix 3 territory.
- CONDITION 2(a) "4H Momentum NEGATIVE" is structurally always true in a bearish trend
  (MACD hist < 0 is the definition of the trend, not a failure signal). The SA is correctly
  down to 1/3, but COND2(a) alone is still triggering STAND_DOWN per the prompt threshold.
- Build Fix 3 next session: SA system prompt change — "4H Momentum NEGATIVE against the
  trade direction is a stand-down signal; for a SHORT in a downtrend, NEGATIVE is
  confirmation." Judgment layer only, no math layer changes.

**If the SA brief issues APPROVED on a trending day:**
- Steps 0+1+2 are working. Monitor the brief for correct level math and measured-move targets.

---

### W-8 ◐ "FEED THE SENIOR ANALYST" — front-of-river audit → reconnection phase (2026-06-04)

**What:** Read-only stress-test of the signal computation and flow-through layer —
indicators computed at session lock that feed the SA brief. Replay harness run against
all 7 known sessions (May29/Jun1/Jun2/Jun3 trending + Apr28/May15/May27 choppy),
identical MEXC candles, code-review of five source files. Owner framing decided
2026-06-04: the audit finding reframes what the next phase of work IS.

**Headline finding:** NO second ADX-class bug. Front-of-river math (RSI, MACD
calculation, BO/BD triggers, EMA formulas, VRVP) computes correctly. The dominant
theme is **dropped information** — correct values computed, then discarded or
flattened before reaching the SA — not wrong values.

---

#### Strategic framing — owner decision 2026-06-04

The SA is currently reasoning on a **starved picture.** Signals that are correctly
computed never arrive: MACD magnitude is flattened to a sign, the sse_engine's
direction/confidence signal is wired to nothing, BBWP vanishes when the MTF
interpreter fails. Despite this, the SA made **defensible trading calls** on Mon/Tue
(trade-to-T1 on confirmed downtrend sessions). That is evidence the SA's reasoning
layer is strong. The bottleneck is feeding it, not its judgment.

**Therefore: the next phase of work is FEED THE SENIOR ANALYST, not
gravity-map expansion or agent-tuning.** There is no point enriching downstream
interpretation while upstream signals are silently missing. Front-of-river fully
connected first. Gravity map expansion and agent-level tuning come after.

**Hard gate: live ADX validation first.** Do not start any reconnection work before
the first live session on Render confirms 80b1d79 is running (Steps 0+1+2 working,
exhaustion labels gone). Clean baseline before new wires.

**Guardrail: more info ≠ better.** Feed DECISION-RELEVANT signals cleanly. Route
through the Junior Analyst / interpreters where the signal needs digestion before
the SA sees it. The Junior Analyst's job is to pre-process, not to firehose the SA.
This is the same principle behind the MTF interpreter — a good interpreter reduces
SA load, it does not add raw rows to an already long context.

---

#### Work plan (after live ADX validation clears)

**Tier A — RECONNECTIONS** (low-risk, high-value)
These are wires never connected or signals silently dropped. Connecting a correctly
working signal to a blind decision layer cannot break a working signal. Low blast radius.

- **A1. Wire sse_engine bias_model into `_build_senior_analyst_context`** — the
  `daily_lean` dict (direction, score, confidence from slope + VRVP location +
  trigger asymmetry) is stored in the packet but never passed to the SA context
  builder. One parameter addition + one section in the SA brief. See finding #3.
- **A2. Add BBWP to SA fallback section** — when `mtf_read=None`, BBWP is currently
  absent. Add `bbwp_value` and `bbwp_compressed` from `fuel_gauge["4H"]` to the
  fallback lines 814–826 in `kabroda_mas_flow`. Low-risk one-liner. See finding #5.
- **A3. Pass MACD magnitude through / Fix 3** — `analyze_tf` flattens hist to
  "POSITIVE"/"NEGATIVE". Fix 3 may need to be a DATA change (pass the normalised
  hist value through to SA context) AND a PROMPT change (teach the SA to read
  direction-qualified momentum), not just a prompt change. This is the highest-weight
  reconnection because it also closes CONDITION 2(a)'s structural always-true-in-downtrend
  issue. See finding #1. Plan both layers before building.

**Tier B — ONE REAL FIX** (requires careful validation, same protocol as W-7)
- **B1. PMARP direction-blind threshold** — `rank > 75` fires for upside overextension
  only. Downside extremes (Jun 2: rank=0.0, most extreme below EMA21 in 252-bar history)
  read as "not overextended." Fix: flag BOTH `rank > 75` (upside) AND `rank < 25`
  (downside) as overextended in their respective directions. Same ADX-class structural
  pattern as W-7's abs() bugs. Validate with the same positive/negative test protocol:
  downtrend days must now show downside-overextended; ranging days must still not
  misfire. See finding #2.

---

#### Findings, ranked by decision weight

**1. MACD MAGNITUDE DROP** — `battlebox_pipeline._build_fuel_gauge` / `analyze_tf`
- **Bug:** MACD histogram is correctly computed but `momentum = "POSITIVE" if hist > 0 else "NEGATIVE"` discards the value before the SA sees it. Replay: Jun 3 hist=−410 (hard accelerating downtrend) and Jun 1 hist=−24 (barely negative) are indistinguishable to the SA — both arrive as "NEGATIVE".
- **Why it matters:** This is the ROOT of CONDITION 2(a) / Fix 3. The sign-only compression is what makes "4H Momentum NEGATIVE" always true in a downtrend — and also always equally "negative" whether momentum is building or collapsing. Fix 3 may not be a pure prompt change. It may require passing the raw hist (or a normalised magnitude) through to the SA context so the prompt can distinguish "confirming downtrend" (large negative hist) from "barely negative, momentum absent" (hist near zero).
- **Severity:** Decision-logic. Highest weight — sits directly in CONDITION 2(a).
- **Same class as ADX?** No. ADX gave numerically impossible values (14× reality). MACD gives the correct sign; the bug is magnitude suppression before the decision layer.

**2. PMARP DIRECTION-BLIND threshold** — `mtf_confluence_scanner._calc_pmarp`
- **Bug:** `pmarp_overextended = rank > 75` fires only for upside extremes (price historically high vs EMA21). Replay: Jun 2 PMARP rank=0.0 (most extreme downside reading in the 252-bar history) → `pmarp_overextended=False`. Jun 3 rank=2.81 → same. A price that has NEVER been this far below EMA21 is labeled "not overextended."
- Secondary: short-history path (<50 bars) returns `abs(current_ratio)` as pmarp_value (a raw percentage magnitude); full path returns a percentile rank 0–100. Different scales from the same field — any threshold on pmarp_value changes meaning depending on data depth.
- **Severity:** Decision-logic. Same structural class as W-7's direction-blind abs() bugs — but in the MTF interpretation layer, not the CONDITION 2 gate directly. Fix after MACD/Fix 3.
- **Same class as ADX?** Yes — direction-blind threshold is the same pattern.

**3. SSE bias_model SILENTLY DROPPED** — `sse_engine` / `kabroda_mas_flow._build_senior_analyst_context`
- **Bug:** `sse_engine.compute_sse_levels` produces a `bias_model.daily_lean` dict containing direction (long/short/neutral), score, and confidence — derived from slope (daily SMA20/SMA50), VRVP opening location (above/below/in value area), and trigger asymmetry (distance to BO vs BD). This is stored in `packet["bias_model"]` and is visible in the battlebox JSON. But `_build_senior_analyst_context` never receives `bias_model` as a parameter — the function signature takes `levels` and `context` only. The SSE's quantitative direction signal is **computed and discarded**. It is a wire that was never connected.
- **Severity:** Flow-through gap. Moderate weight — the signal incorporates real structural information (VRVP positioning, trigger asymmetry) that the SA currently cannot access.
- **Same class as ADX?** No — this is a routing gap, not a wrong-value bug.

**4. VRVP zero-volume silent degradation** — `sse_engine._calculate_vrvp`
- **Bug:** If `total_volume=0` across all VRVP input candles, `target = 0 * 0.70 = 0`. The value-area expansion loop exits immediately (`curr < target` = `0.0 < 0.0` = False). Result: `POC = VAH = VAL = min_price`. The trigger logic degrades gracefully — BO falls back to R30H, BD to R30L — but no warning is logged, no error raised. Failure is entirely silent.
- **Fix:** Log a warning when total_volume=0 after VRVP computation so the issue is visible in Render logs.
- **Severity:** Medium. Unlikely on MEXC (always has volume), but a silent correctness gap.
- **Same class as ADX?** No.

**5. BBWP silent absence in fallback** — `mtf_confluence_scanner._calc_bbwp`
- **Issue:** BBWP flows to the SA via the MTF interpretation string (`mtf_read`). When `mtf_read=None` (interpreter disabled or failed), BBWP is completely absent from the SA context — the fallback section (lines 814–826 in `kabroda_mas_flow`) covers RSI/MACD label/ADX/kinematic_grade but not BBWP. Replay confirmed BBWP was a correct and useful signal: Jun 1 BBWP=4.47 (compression before the breakout), Jun 2 BBWP=99.44 (maximum width during the selloff).
- **Severity:** Medium. Good compression-timing signal with a silent drop path.

**6. EMA dual-period inconsistency** — `battlebox_pipeline`
- `analyze_tf` uses ema30/ema50 for the `trend` label ("BULLISH"/"BEARISH"). `_build_jewel_reading` uses ema21/ema55 for `ema_state` ("BULLISH_EXPANDING" etc.). Both reach the SA brief. Near a crossover they can disagree — no documented rationale for which to trust. Raw EMA price levels do not appear in the SA brief at all; SA gets labels but cannot reference "price is $1,600 below ema50" as a structural anchor.
- **Severity:** Cosmetic / precision. All 7 replay sessions showed directional agreement between the two pairs.

**7. Daily S/R: 1H pivot always silenced** — `sse_engine._select_daily_levels`
- Hardcoded strength scores: 4H pivot = 0.8, 1H pivot = 0.6. `_select_daily_levels` always picks the highest-strength shelf → 4H always wins. The 1H pivot is computed, stored in `htf_shelves`, but never used in `ds`/`dr` (the values that feed BO/BD and the SA brief) whenever a 4H pivot exists.
- **Severity:** Cosmetic. The 4H level is still meaningful structural reference.

**8. JEWEL "EXTENDED" catch-all label direction-blind** — `battlebox_pipeline._build_jewel_reading`
- `signal="EXTENDED"` fires as the catch-all for any state not matching BOUNCE_PRIMED, TRENDING_STRONG, or VALUE_ZONE_NEUTRAL — covering both RSI<20 (extreme oversold) and RSI>80 (extreme overbought) with the same label.
- **Severity:** Cosmetic. The `rsi_zone` label (OVERSOLD_EXTREME / OVERBOUGHT_EXTREME) appears alongside the signal in the SA brief; the SA has the direction context it needs.

**9. RSI** — Clean. Wilder formula correct: `(avg × (period-1) + new) / period`. Output 0–100 confirmed across all 7 sessions. Raw values and zone labels reach SA brief directly.

---

- **Status:** ◐ Audit complete; work plan decided 2026-06-04. Waiting on live ADX validation (80b1d79) before any code changes.
- **Next action:** Watch first live Render session. If 80b1d79 confirmed working → start Tier A reconnections in order A1 → A2 → A3/Fix3. Then Tier B (PMARP) with W-7-protocol validation.
- **Sequencing:** Tier A first (reconnections, low blast radius). Tier B only after Tier A is live and validated. Gravity map expansion after front-of-river is fully connected.
- **Blocks:** W-3 backtest validity (pointless to replay a starved SA). Gravity expansion (downstream — feed front-of-river first).

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
| 2026-06-05 | **FEAR & GREED INDEX — low-weight sentiment CONTEXT, not a decision driver (owner, 2026-06-05).** Belongs in the public newsletter (readers expect it). Internally: available as awareness/context the SA can note, but explicitly LOW weight — it's directionally ambiguous (extreme fear can precede either a dip-buy bounce or a capitulation flush). Do NOT let it move the decision; it's color, like the owner uses it himself. Pin for when sentiment feeds are wired. | owner, 2026-06-05 | TBD — when sentiment feeds are wired |

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
| 2026-06-04 | GROWTH PLAYBOOK — entity, Wikidata & AI-citation strategy (Kabroda_Entity_Citation_Playbook.docx) | 4-layer strategy for building Kabroda as a citable entity AI systems reference by default. Layer 1: public-facing hub on kabroda.com — canonical identity (name/logo/description consistent everywhere), proof of work, publication + YouTube offer, cross-links to all owned profiles. Layer 2: entity chain via schema.org structured data — Organization schema (Kabroda), Person schema (SpiritMaker/@Grossmonkey as founder/analyst), Article schema per published piece, sameAs links to every profile. Layer 3: Wikidata reconciliation — establish notability footprint first (third-party mentions, body of public work), then create/claim Wikidata item, wire QID into schema sameAs. This closes the trust loop for Google Knowledge Graph and AI citation. Medium-term goal, not week-one. Layer 4: cited-everywhere flywheel — TradingView track record (timestamped, public), genuine presence in trader communities (Reddit/Discord/X), repurpose into content system (publication → YouTube → TradingView → social all reinforce same entity). Sequence: hub → schema → cross-profile consistency → publishing cadence → communities → Wikidata. Hard gates: (1) notability must precede Wikidata attempt; (2) attorney compliance review before publishing any public performance stats (see EDUCATIONAL FRAMING pin). |
| 2026-06-05 | INTEL REPORTER CoinGecko 429 — reliability / graceful fallback (2026-06-05) | Render log 2026-06-05 showed `[INTEL REPORTER] CoinGecko global fetch failed: HTTP 429 Too Many Requests`. Brief still received F&G=12 (cached or fallback path fired), so no impact today. Confirm intel reporter has a graceful fallback when CoinGecko rate-limits so sentiment data doesn't silently vanish on a future session. Low priority, reliability — the kind of "little thing sliding through" the audit exists to catch. |
| 2026-06-04 | EDUCATIONAL FRAMING — design principle for all public/paid output (owner, 2026-06-04) | Everything published or sold is framed as EDUCATIONAL / opinion / "this is what we see" — never as financial advice, never with claims about profit or returns. Users make their own decisions and interpretations. Standard disclaimer language (not financial advice, educational purposes, our opinion, trade at your own discretion) on all public-facing material. CRITICAL CAVEAT: the disclaimer is necessary but NOT sufficient — regulators judge substance, not just the label. Publishing specific entry/stop/target levels + performance stats + charging can read as a signal service regardless of disclaimer. The framing AND the format must be designed together. HARD GATE (already pinned): a qualified securities/financial-services attorney must review the actual framing, format, disclaimers, and performance presentation for the owner's jurisdiction (US/TX) and subscriber base BEFORE any public launch or paid subscription. "Other sites do it this way" is not a compliance basis. Claude is not a lawyer and cannot adjudicate this. |
