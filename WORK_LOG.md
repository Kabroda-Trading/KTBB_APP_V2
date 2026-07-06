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

## ✅ END-OF-TASK CHECKLIST — run after EVERY commit before declaring done
*(Added 2026-06-14. The previous standing instruction wasn't firing reliably — the 06-13 NEXT SESSION START stayed stale until the owner caught it. This checklist is the fix.)*

**After any commit, before closing the task, verify all four:**

1. **NEXT SESSION START marker** — does it show today's date and today's actual accomplishments? If a session's work just landed in a commit, update the marker to reflect it. Never leave it showing a prior day. This is the orientation block a fresh context reads first; if it's stale, tomorrow starts disoriented.

2. **W-item status** — every W-item touched this task: is its checkbox (`☐` / `◐` / `☑`) and status line current? Check off what's done, note the commit hash. If a W-item moved from blocked to unblocked as a side effect of this task, note that too.

3. **SYSTEM_FLOW CHANGE LOG** — does every code change have a corresponding CHANGE LOG row? One row per meaningful change, keyed to the commit hash. If the task was docs-only or a config change with no behavioral effect, note "docs-only — no CHANGE LOG row needed."

4. **Surface, don't silently decide** — if reconciling reveals an ambiguity or a judgment call (a status that's unclear, something that might be done but isn't confirmed), **flag it to the owner and ask** rather than guessing. Updating a clear fact = just do it. Resolving an ambiguity = ask first.

**Mutual accountability loop:** Claude Code reconciles the docs after commits. The conversation-side Claude cross-checks that reconciliation happened. The owner sits above both. Neither AI is the sole guardian — they check each other. If Claude Code skips the checklist, the conversation-side Claude catches it (as it did today). If both miss it, the owner is the final backstop and should call it out.

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

### Principle 4 — The River Flow: fix upstream, downstream shrinks (owner, 2026-06-06)
The system is a flow: signals/levels (source) → interpreters → Junior Analyst → Senior Analyst → brief → dashboard → publication. Fixing upstream auto-fixes downstream for free. Proven this week: the ADX fix revived dead downstream threshold checks; the MACD magnitude fix corrected the allocation logic. **Rule:** when tempted to fix something downstream (dashboard, publication), first ask "is this a downstream SYMPTOM of an upstream cause?" Fixing upstream is cheaper and shrinks the downstream work. Corollary: the publication (furthest downstream) will be relatively easy to build BECAUSE the internal foundation is solid and audited. **Don't push downstream production work before the upstream flow is clean** — but DO move steadily down the river, monitoring each stage as data volume grows.

### Principle 5 — INSTRUMENT EVERYTHING NOW; THE COST OF NOT-TRACKING IS ASYMMETRIC (owner, 2026-06-06, strengthened 2026-06-07)

**The asymmetry is absolute.** The cost of tracking something you don't need = trivial (storage). The cost of NOT tracking something you later need = WEEKS — because the data must accumulate from the start, and history cannot be created retroactively. There is no fix for a gap in the past. You can always reduce tracking detail later once core signals are known; you can never recover un-captured history.

**Therefore: "should this be tracked?" is a CONSTANT, ACTIVE question for BOTH owner and Claude, and the default answer is YES.** Log every decision point, every condition fired, every non-obvious outcome NOW — before we know what we'll do with it — because the dataset is the foundation all downstream capabilities (auditing, validation, simulator, publication track record) depend on.

**CAPTURE and FEATURE are separate things.** Capture comes FIRST and IMMEDIATELY — it is cheap and time-lagged. The feature that reads the capture can come later. Do not wait for a feature to be scoped before turning on its data collection. The auditor, the coach vision, the publication track record, and the account simulator are all blocked — not by code complexity — by the absence of historical data that should have been accumulating from the moment each gap was spotted.

**Proven pain (2026-06-07):** the performance auditor and the publication track record are both blocked behind "get the basics first." Both blocks exist because data-capture was not turned on the moment the gap was spotted — only when the feature got built. That delay is unrecoverable.

**No dark crevice left un-instrumented.** If a decision point fires, log it. If a condition is evaluated, log the outcome. If a setup is approved but never filled, log the NO_FILL, log the reason, log the session context. If an agent produces a read, log the read — not just whether it succeeded.

**Concrete trigger (2026-06-07):** approved-but-never-filled trades are currently vanishing from the record (see W-9 — phantom CLOSED_LOSS on an untriggered Jun7 setup). The RIGHT response is not just fixing the mislabel — it is logging NO_FILL / EXPIRED with the reason and session context FROM NOW, so "how often does this happen, and what predicts it?" is answerable in two weeks instead of "we never tracked it, start now." The fix and the capture are both required; the capture is the more important of the two.

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

## 🗺 ROADMAP — consolidated 2026-07-03, full read-through of every W-item, the Suggestion Box, and the Parking Lot

*Purpose: answer "where are we, are we on track, what's next, does anything conflict" in one place, so no future fix gets built in isolation from what else is planned. Not a rewrite of history — a status snapshot with stale gates corrected where found. Re-consolidate whenever the picture gets muddy again, same as this pass.*

### TIER 0 — FOUNDATION, CONFIRMED SOLID (don't re-litigate)
- **W-9 Outcome-tracking data integrity** — ☑ FULLY CLOSED. Phantom losses, binary-R, entry-fill detection, three-phase lifecycle monitor — all fixed and live. Every downstream number (dashboard, auditor, RAG memory) can trust `CampaignLog` outcomes now.
- **W-11 Auditor dataset contamination** — ☑ DONE. Auditor only sees real MAS decisions, radar page-view noise filtered out. 4-value decision tagging (`MAS_STAND_DOWN` etc.) shipped — stand-down accuracy is now computable.
- **W-12 MAS scheduler autonomy** — ☑ CLOSED. Fires at lock_end_ts, DST-aware, autonomous even with zero page visits.
- **W-7 Exhaustion bug (Fix 1+2, the ADX/harmonic-matrix direction-blind bugs)** — ☑ ALL STEPS CLOSED.
- **W-5 Auditor-wire break** — ☑ DONE.

### TIER 1 — ACTIVE / URGENT (today's punch list — see above, not repeated here)
4H/1H stop/target construction, 1-minute wick execution, SA missing current price. Three real live examples now confirm the stop/target break; nothing here conflicts with anything below — this is genuinely the current bottleneck.

### TIER 2 — IN PROGRESS, NOT YET CLOSED (worth knowing these are still open)
- **W-1 Interpreter layer (organize/decide split)** — ◐ MTF Interpreter deployed and live; the broader "which Bucket A modules deserve their own interpreter" question was left open pending measurement, never revisited.
- **W-2** — ◐ Same open question as W-1, dependent on it.
- **W-6 Dashboard audit** — ◐ Read-only audit done, fix pass never happened.
- **W-8 "Feed the Senior Analyst"** — ◐ A1/A2/A3 done and live (Jun-7 confirmed session). Tier B rescoped, B1 (PMARP direction-blindness) was explicitly PARKED — **worth checking: Crown Surgery Cut 2 (2026-07-01) made PMARP the PRIMARY kinematic_grade signal, direction-aware on both LONG and SHORT. This may have quietly resolved B1 without anyone updating its status — flag for verification, don't assume.**
- **W-10 Audit output surfacing** — ◐ Partially resolved. Auditor output visible; token-limit truncation, Elliott Wave reasoning view, and nav links were still open as of last note.

### TIER 3 — GATED behind "15M core proven solid across many live sessions" (the big one)
This is the **W-14 STRENGTHENING PHASE cluster** — Multi-Timeframe SSE Engines (14b, "the biggest project on the board"), HTF Structural Anticipation (14c), and the signal-timing/VET-A-TRADE tool (14a). All three explicitly share one primary gate, stated plainly in W-14: *"A3 is 2 sessions old. W-7 Fix 3... is still OPEN. B1/PMARP direction-blind is parked."*

**That gate description is from 2026-06-14 and is now stale in at least one respect:** B1/PMARP may be resolved by Crown Surgery (see Tier 2 above, needs verification, not confirmed). W-7 Fix 3 (SA prompt direction-awareness for CONDITION 2(a)) — status not reconfirmed in this pass, worth checking before assuming still open. **Net: this cluster is probably closer to its gate clearing than the last time anyone looked, but "closer" is not "cleared" — A3 needs many more live sessions regardless of the other two sub-conditions, and that's a time-based gate, not a code-fix gate. Don't rush it.**

**Runner Mechanic** (master plan Component 4) is gated behind the Tier 1 stop/target fix specifically (needs N>0 real 4H trades with sound exit data) — narrower gate, not the same as the W-14 cluster, correctly sequenced right after Tier 1.

### TIER 4 — GATED, gate partially stale (found this pass — worth re-opening the conversation, not silently closing it)
- **Stand-Down Re-Arm Alerter** (2026-06-16) — gate was "15M-solid + notification infra (W-4)." W-4 (public Ghost/newsletter delivery) still doesn't exist. But a *different*, smaller piece of infrastructure now does — `notify.py`, the owner-facing SMTP email system built 2026-07-01 for 4H/1H candidate alerts. The alerter only ever needed to email the *owner*, not subscribers — this may functionally satisfy the notification half of its gate already. Still needs 15M-solid.
- **Live Exhaustion Monitor** (2026-06-18) — same "W-4 notification infra" gate, same reasoning applies. Also gated behind W-9 Phase 2 (✅ actually done, confirmed above) — that part of its gate IS cleared.
- **Neither of these is being unblocked right now** — flagging that the gate language is outdated, not declaring them ready to build. Worth a real conversation about whether `notify.py` genuinely covers what each one needs before either gets picked up.

### TIER 5 — PUBLICATION / NARRATIVE LAYER, does not touch trade construction
RSI divergence for narrative, dominant-trend classifier, the Fibonacci EMA ribbon question, potential Revin Ribbons integration (external build, confirmed real bugs as of 2026-07-03, not ready regardless of anything else). None of these block getting a trustworthy trade out the door.

### TIER 6 — NEEDS A DECISION, NOT SCHEDULED
- **W-13** — does a radar-page-view `DecisionJournal` row need a `session_id`? Low priority, no feature currently reads it.
- **Single vs. staged Fibonacci target for 4H/1H** — real open tension surfaced during the Tier 1 investigation, needs resolving as part of that design, not separately.
- **W-3 Backtest** — owner-flagged HIGH priority, parked "until structure is settled." Worth revisiting once Tier 1 lands, since that's exactly the kind of structural settling this was waiting on.

### TIER 7 — COMPLIANCE / BUSINESS, separate track from engineering
Attorney review hard-gate before any public launch (Educational Framing pin, 2026-06-04) — not started, not urgent until publication phase. Entity/citation growth playbook (Kabroda_Entity_Citation_Playbook.docx) — medium-term, sequenced hub → schema → Wikidata, not urgent now.

### What this pass changed
No code touched. Two stale gate descriptions caught and flagged (Tier 4) rather than left silently outdated. One "may already be resolved" flag raised for B1/PMARP (Tier 2/3) pending actual verification — not claimed as fixed without checking. Nothing here contradicts or requires undoing anything in the Tier 1 punch list — confirmed clean before starting that work.

---

## ► NEXT SESSION START
*End-of-session marker: 2026-07-06*

**Item #5 (macro/weekly bias check) shipped, record-only per-timeframe as backtested.** Full detail in the "PUNCH-LIST ITEM #5" entry below.

**Gravity-wall investigation (item #6's redirect) — root cause found and fixed same session.** Real production data (`campaign_logs.structure_reasoning` + `gravity_memory`, via psql, not synthetic reconstruction) confirmed the owner's instinct: the 15M system's HEAVY/MAXIMUM "wall" mechanism has a genuine, systemic bug — three periodic-logging functions in `gravity_engine.py` (`log_radar_anchors()`'s `168H_MICRO_ANCHOR`/`1W_MACRO_ANCHOR`, `log_kabroda_bedrock()`'s `7_DAY_KABRODA`) write a new `gravity_memory` row every time they run but never deactivate the one they superseded — unlike `4H_PIVOT`/`1H_PIVOT`/`DAILY_PIVOT`, which `_update_zone_touches()` already invalidates correctly. Real levels ($60,025.76, $62,296.54) were confirmed intercepting sessions as "walls" for nearly a month each, despite BTC moving tens of thousands of dollars in between — the density weight was artificially inflated by duplicate-counting the same rolling snapshot dozens of times, not genuine multi-source structural agreement. Fixed: all three writers now deactivate superseded rows before inserting new ones, matching the existing `_update_zone_touches()` convention. Full detail in the "GRAVITY-WALL AUDIT" entry below. **Whether to port wall-snapping to 4H/1H (the original item #6 question) stays deferred** — revisit once this cleanup has run for a while and produces genuinely-persistent (not artificially-inflated) wall data to evaluate.

**Item #1 (4H/1H stop/target construction) implemented as v4 — commit pending owner go-ahead to push/deploy.** `mtf_backtest_lab.py` extended with windowed validation tooling (found + fixed a real MEXC pagination bug in the same pass — every prior run of this tool, including 2026-07-03's, was silently working from ~500 candles regardless of `--days`). Grid-tested both TFs × both tie-break orderings on real, correctly-paginated history: recency-ordering plateaus cleanly (1H at 48 bars/2 days, 4H at 30 bars/5 days) and beats the whole-history baseline on both; price-proximity ordering never stabilizes on either TF — same "expanding pool" artifact as the original bug. Chose recency ordering + those two windows. Rewrote `_detect_4h_bos()`/`_detect_1h_bos()` in `gravity_engine.py`: single shared `_nearest_pivot_in_window()` helper replaces the two `_qualified_4h`/`_qualified_1h` closures (heat/touch/departure gate dropped entirely for stop selection), Fibonacci-staged T1/T2/T3 (1.0x/1.618x/2.618x of entry-to-stop leg) replaces the single equal-leg `opp_row` target, `_calc_atr` now excludes the still-forming candle (was inconsistent with `_scan_for_pivots`), tagged `target_logic_version='v4'`. `database.py` comment block updated to document v4's shape. `ledger_closing_engine.py` confirmed needs zero changes — `_observe_targets()` is already shape-agnostic, will auto-populate `t2_reached`/`t3_reached` on v4 rows. Full detail and grid-test numbers logged below under "PUNCH-LIST ITEM #1 — STOP-WINDOW VALIDATION + DECISION (2026-07-04)". **Committed and pushed (commit `fc8edfc`, 2026-07-04) — confirmed synced via `git log origin/main..HEAD` (empty output).** **DEPLOY VERIFIED (2026-07-04 15:10 UTC):** Render boot log confirmed live — `>>> GRAVITY ENGINE: Initializing background loop (v4 target logic, STRICT SSOT MODE)...` — the exact verification anchor from the plan. Build succeeded, service live at kabroda.com. **Still outstanding, carry-forward:** once a live 4H/1H candidate actually fires, run the row-shape check (`t2`/`t3` non-NULL, `htf_anchor_type` in `{STOP_PIVOT, ATR_FALLBACK}`) and manually confirm the stop sits inside the chosen window — the direct behavioral regression check against the diagnosed bug (boot log only proves the code is live, not that it produces correct output yet). **Item #1 is now fully shipped.**

**Item #2 (execution on wick vs. close) — ABANDONED, premise was wrong (2026-07-04).** Traced the bug to `ledger_closing_engine.py`'s Phase 2 (15M) and Phase 4 (4H/1H), both triggering stop/T1 on a single 1-minute wick touch. Built `verify_close_vs_wick.py` (standalone, read-only) to quantify the flip rate before deploying a close-confirmation fix. While preparing to run it, discovered the real historical closed-trade population is only 3 rows (all 15M, `is_canonical=True`), and separately discovered Kraken's public OHLCV API caps history depth per interval (1m: hours only; 15m: ~7 days; 1h/4h: 2+ weeks) — all 3 available rows are outside the 1m/15m window, making a historical wick-vs-close comparison impossible with live-fetched data. **More importantly, the owner clarified real trades execute via resting stop-loss/take-profit orders on the exchange — not discretionary close-confirmed manual exits.** A resting order fires on the wick, full stop; it has no concept of a candle close. That means the ORIGINAL wick-based mechanism was already correctly modeling real execution — it was never a bug. **No code change made to `ledger_closing_engine.py`'s execution-detection mechanism.** `verify_close_vs_wick.py` is left in the repo (harmless, standalone, unused) but its premise doesn't apply.

**Real finding that emerged instead — stop-hunt vulnerability + a pre-existing R-accounting bug, both fixed (2026-07-04).** The resting-order clarification led to the real concern: a resting stop sitting exactly at an obvious technical level (a pivot, a trigger) is a classic stop-hunt-wick target. Tracing this for the 15M system surfaced two real, pre-existing issues, unrelated to anything shipped today: **(1)** CLAUDE.md's documented rule ("stop loss is always the opposing trigger, no exceptions") is stale — `trade_structure_analyst.py`'s `_structural_stop_long()`/`_structural_stop_short()` have actually been computing the stop as `r30_low − ATR×0.5` (long) / `r30_high + ATR×0.5` (short), wall-snapped a further `ATR×0.25`, for some time. The raw trigger is kept only as an unused `original_stop` audit field. **(2)** Because of (1), `ledger_closing_engine.py` hardcoding `realized_pnl = 1.0` on every T1 hit (both Phase 2 and Phase 4) has been silently wrong whenever entry-to-stop distance ≠ entry-to-target distance — which is the common case once the ATR/wall-adjusted stop is in play, and also affects the newly-shipped 4H/1H v4 stop whenever its own ATR floor/cap on the target leg diverges from the actual stop distance (`target_too_small_flag` cases, or the >5×ATR cap). One code path (`CLOSED_AT_EXPIRY`) already computed this correctly; the ordinary T1-hit path never did.

**Fixed, in one coordinated pass, per owner decision:** (a) `ledger_closing_engine.py` gained a shared `_frac_r(entry, stop, exit, is_long)` helper — true fractional R (`move/risk`, zero-risk floored at 0.01 with a logged warning) — wired into both Phase 2 and Phase 4's T1-hit branches and Phase 2's `CLOSED_AT_EXPIRY` branch (which was missing the zero-guard Phase 4's equivalent already had). Stop-hit branches stay hardcoded `-1.0` — correct by definition, R is defined relative to your own actual stop, unaffected by this bug. (b) `gravity_engine.py`'s v4 `STOP_PIVOT` branches (all four: 4H/1H × LONG/SHORT) gained a `0.25×ATR` stop-hunt buffer pushed beyond the raw pivot price — reusing the exact coefficient `trade_structure_analyst.py` already uses for its own wall-snap buffer, rather than inventing a new magnitude. `htf_anchor_price` still records the raw, unbuffered pivot (audit trail of the level itself); `stop_loss` carries the buffered, executable value. Because `raw_leg` is computed from `stop_price` *after* the buffer is applied, target math stays internally consistent automatically — no separate rebasing needed. The 15M system gets NO additional buffer (it already effectively has one via the ATR×0.5+wall adjustment; no evidence it's insufficient). (c) CLAUDE.md's "What Must Never Be Changed" item 5 corrected — owner explicitly confirmed: ratify the ATR/wall-adjusted stop as the real documented rule, not a revert to the literal trigger. **Deliberately NOT touched, per two separate inviolable-rule considerations:** the Measured Move formula itself (`T1 = Entry ± (bo-bd)`, CLAUDE.md rule #1 — "Distance" stays pinned to the raw trigger distance, not rebased to the actual stop, even though that would have been a philosophically cleaner single fix) and `trade_structure_analyst.py`'s stop-computation logic itself (already correct, not being changed, just documented accurately and buffered downstream for 4H/1H only).

**Verified via `verify_r_accounting.py`** (new, standalone, synthetic — sidesteps the Kraken retroactive-history limit entirely since it's pure arithmetic against hand-computed cases, not live data replay): 8 cases (symmetric LONG/SHORT where old and new agree, floored-leg and capped-leg LONG/SHORT where the old formula was silently wrong, a real 15M-style ATR-adjusted-stop case, and the zero-risk anomaly case) — all 8 pass against hand-calculated expected R. 5 of 8 cases confirm the old hardcoded formula was already producing a wrong number before this fix.

**Committed (3 commits: `bb007ed`, `604f6ea`, `2368b71`) and pushed 2026-07-04.** **DEPLOY VERIFIED (2026-07-04 17:01 UTC):** Render build succeeded, no boot errors, `TRADE-LIFECYCLE MONITOR: Initializing` line confirms `ledger_closing_engine.py` loaded cleanly, service live at kabroda.com.

**LIVE VERIFICATION CLOSED OUT (2026-07-05) — three real production events confirm the fix behaves correctly.** 1H SHORT CLOSED_LOSS at -1.0000R (stop hits are tautologically -1R, unaffected by the fix — correct). 1H LONG CLOSED_WIN at +1.0000R — hand-checked: risk = $63052.10-$62300.30 = $751.80, reward = $63803.90-$63052.10 = $751.80, genuinely symmetric, so +1.0R is the real answer (doesn't distinguish old vs. new formula, but confirms no regression). **Most useful data point: a still-open 4H LONG candidate** (entry $63565.30, stop $57706.77, T1 $65036.84) where risk (9.2% of price) is far larger than the T1 leg (2.3%) — working backward this is consistent with the v4 ATR cap firing (`leg > 5×ATR14` capped to `3×ATR14`), exactly the scenario the R-accounting fix targets. **Flagged to check when this row resolves:** if it closes at T1, `realized_pnl` should read ≈`+0.25R` (1471.54/5858.53), not the old system's false `+1.0R` — the first live, non-synthetic confirmation the fix is working end to end, not just in `verify_r_accounting.py`'s hand-computed cases.

Sequence moves to item #4 (energy-grade enforcement) per the original agreed order.

### PUNCH-LIST ITEM #4 — ENERGY-GRADE: RECORD-ONLY, ENFORCEMENT DEFERRED (2026-07-05)

**Re-ran the energy-grade subgroup backtest using v4-consistent trade construction before building anything** — the 2026-07-03 numbers already logged above used the old, broken v1-v3 stop/target construction and shouldn't be trusted for this decision now that v4 has shipped. Used the already-built `build_trade_plan_windowed()` (item #1's validation tooling) with the chosen production windows (48 bars/2 days for 1H, 30 bars/5 days for 4H, recency ordering). Real results, N=167 (1H, 90 days) and N=177 (4H, 400 days) — both clear this project's own N≥30 bar:

- **1H:** the current crude formula (`_compute_energy_grade`, EMA30/50+MACD+PMARP-cap) never once grades STRONG in 90 days (N=0) — a hard block on anything but WEAK would have shut the 1H system down entirely. WEAK (N=102) and MODERATE (N=65) perform almost identically (+0.119R vs +0.140R avg). The 15M-style `kinematic_grade` formula shows TANGLED (N=82) *outperforming* PRIMED (N=59): +0.165R vs +0.103R — backwards from what the label implies.
- **4H:** current-formula STRONG (N=11) does look better — 63.6% win, +0.273R — than MODERATE (N=58, 48.3%, −0.003R) — but N=11 is below this project's own reliability bar. The 15M-style formula is backwards again: OVEREXTENDED (N=36) performs *best* (+0.184R), not worst, PRIMED (N=68) sits at +0.160R, TANGLED (N=73) worst at +0.039R. This now CONFIRMS, with a much larger sample than the original 2026-07-03 test (N=18 then), that this formula's 252-bar lookback (42 real days on 4H vs. 2.6 days on 15M) genuinely doesn't transfer across timeframes — not a small-sample fluke.

**Decision (owner, 2026-07-05): neither formula gates candidate creation.** Record-only. Ported the 15M JEWEL's `kinematic_grade` formula (PRIMED/TANGLED/OVEREXTENDED, direction-agnostic) into `gravity_engine.py` as `_compute_kinematic_grade()`, wired into both `_detect_4h_bos`/`_detect_1h_bos` alongside the existing `_compute_energy_grade`, writing to a new nullable `CampaignLog.kinematic_grade` column (NULL on 15M rows — they don't go through these detectors). Zero conditionals read either value to block anything — purely additive. This matches the master plan's own organizing principle ("log everything first, suggest softly, act only when data clears the threshold") more precisely than the original punch-list lean ("hard block to start") did. Revisit enforcement only once real production data (not backtest) clears N≥30 per timeframe with a clean, stable signal on one formula.

**Not touched:** `_compute_energy_grade()` itself (unchanged, still computed and stored exactly as before); no gating conditional added anywhere; no new config/feature-flag system built (none exists in this codebase today — confirmed via repo-wide search, every behavioral parameter here is a hardcoded literal requiring a code change + redeploy to alter, and this fix doesn't change that pattern).

### PUNCH-LIST ITEM #5 — MACRO/WEEKLY BIAS CHECK: HARD-GATED ON 1H, RECORD-ONLY ON 4H (2026-07-06)

**Researched the exact mechanisms before designing anything.** `macro_bias` (`battlebox_pipeline._calculate_weekly_force()`) is a cheap, purely-daily-OHLCV-derived BULLISH/BEARISH/NEUTRAL scalar (21-day vs. 7-day daily SMA vs. current price) — no database-state dependency, fully backtestable with existing tooling. `weekly_200sma_position` is a `gravity_memory` row read (written every 24h by the macro engine subprocess) — cheap to compute in production but would need ~1400+ days of daily history fetched fresh to backtest rigorously, not done tonight.

**Backtested macro-bias alignment before building** (same v4-consistent windowed construction as items #1/#4), N=167 (1H, 90d) and N=177 (4H, 400d) — both clear N≥30:
- **1H:** aligned-with-daily-macro_bias signals clearly outperform — 58.3% win, +0.257R (N=84) vs. counter-trend 46.4%, **−0.028R** (N=69). Clean, strong separation — stronger evidence than what supported energy-grade enforcement (correctly left unenforced in item #4).
- **4H: inverted.** Counter-trend outperforms (55.3%, +0.212R, N=76) vs. aligned (47.3%, **−0.024R**, N=74). Same cross-timeframe non-transfer pattern already seen with `kinematic_grade` (item #4) — except here the direction fully flips rather than just losing signal. Blocking on "must align" uniformly would have removed the currently-winning 4H subset.

**Decision (owner, 2026-07-06): hard-gate 1H, record-only 4H.** `_detect_1h_bos()` now rejects (never writes a `CampaignLog` row for) any candidate whose bias counters the daily `macro_bias` — `LONG` when `macro_bias == BEARISH`, or `SHORT` when `macro_bias == BULLISH`. `NEUTRAL` does not block (only N=14 in the backtest, not clearly bad either direction at that sample size — left permissive). `_detect_4h_bos()` computes and stores `macro_bias` on every row but gates nothing. Both detectors now also thread `candles_1d` (30 daily candles, already fetched once per gravity-ingestion-loop cycle but previously discarded after pivot scanning — zero new API calls) and record `weekly_200sma_position` (`ABOVE`/`BELOW`/`AT`, same ±0.5% threshold `_compute_mtf_structural_snapshot()` already uses) on both timeframes, unenforced.

**Real bug caught before shipping:** first wiring attempt passed pre-extracted `List[float]` closes into `battlebox_pipeline._calculate_weekly_force()`, which internally does its own `c["close"]` extraction and expects raw candle dicts — crashed immediately on a smoke test (`TypeError: 'float' object is not subscriptable`). Fixed by passing `candles_1d` straight through unmodified. Confirmed the standalone backtest script's own `weekly_force()` helper uses a different, self-consistent convention (pre-extracted floats by design) — the bug was isolated to the new production wiring, the backtest numbers above are unaffected.

**Committed (`18b425e`) and pushed 2026-07-06 — confirmed synced.** **DEPLOY VERIFIED (2026-07-06 14:38 UTC):** Render build succeeded, no boot errors, `TRADE-LIFECYCLE MONITOR`/`GRAVITY ENGINE` both initialized fine, macro engine's `WEEKLY 200 SMA || BTCUSDT | 62861.76` log line confirms the exact `gravity_memory` row `_fetch_weekly_200sma()` reads is populated and fresh. Service live. **Still outstanding, carry-forward:** once the next 1H candidate fires, confirm no counter-trend row is ever written (query `campaign_logs WHERE session_id='1h_system'` and manually check `bias` against `macro_bias` on each — they should never be BEARISH-macro+LONG or BULLISH-macro+SHORT going forward); once a 4H candidate fires, confirm `macro_bias`/`weekly_200sma_position` are both non-NULL.

**Not touched:** the BOS trigger/break-level detection, stop/target construction (item #1), `energy_grade`/`kinematic_grade` (item #4) — all unchanged. No new config/feature-flag system (none exists; same as item #4).

**Item #6 reframed, not built:** porting the 15M's proven `trade_structure_analyst._snap_long()`/`_snap_short()` HEAVY/MAXIMUM wall-snap logic to 4H/1H targets was the natural next move — it's a structural-correctness fix (don't aim a target at a known wall), not a probabilistic filter needing a win-rate backtest, same reasoning as the item #1 stop-hunt buffer. Owner pushed back: not confident the mechanism itself is well-calibrated on the 15M system it's already live on — "many times the gravity walls are not that strong and get taken out with the strong energy in the chart." **Redirected to a new investigation, not yet scoped:** does 15M's wall-snap actually improve outcomes, or does strong momentum blow through supposedly HEAVY/MAXIMUM walls, capping winners that an un-snapped Fibonacci target would have captured? Answer that on the system it's already live on before deciding whether to port it, or whether the intensity thresholds/heat-multiplier weights need recalibrating first.

### GRAVITY-WALL AUDIT — ROOT CAUSE FOUND: STALE, NEVER-DEACTIVATED ROWS INFLATING WALL WEIGHT (2026-07-06)

**Investigated the item #6 redirect using real production data (psql), not a synthetic backtest** — `campaign_logs.structure_reasoning` already stores a full JSON audit trail per session (original Fibonacci target vs. wall-adjusted one), so real wall-snap history could be pulled directly.

**First finding: not enough ledger data to judge the mechanism from outcomes alone.** Filtering to genuinely-labeled executed trades (`bias IN ('LONG','SHORT')`, excluding rows mislabeled `BULLISH`/`BEARISH`/`NEUTRAL` — a separate, minor data-vocabulary inconsistency noted but not chased), the entire production history contains only **11 real sessions total**. Of those, exactly **1** had a wall-snap on T1 (id=86, 2026-06-07) — and that one was never even entered (`entry_filled_at IS NULL`, expired unfilled). No resolved, live-traded wall-snap case exists to check "did price respect the wall or blow through it."

**Second, much bigger finding: a real, systemic code bug — three functions never deactivate superseded rows.** Cross-referencing `gravity_memory` against the wall prices recurring in `structure_reasoning` ($60,025.76, $62,296.54) showed both levels intercepting sessions across **nearly a full month each** ($60,025.76: 2026-06-06 through 2026-07-02; $62,296.54: 2026-06-19 through 2026-06-25) — despite BTC moving tens of thousands of dollars in between. Traced to source: `log_radar_anchors()` (`168H_MICRO_ANCHOR`, hourly; `1W_MACRO_ANCHOR`, weekly) and `log_kabroda_bedrock()` (`7_DAY_KABRODA`, 6 rows every session lock) all write a new row every time they run but **never set `active=False` on the one they superseded** — unlike `4H_PIVOT`/`1H_PIVOT`/`DAILY_PIVOT`, which `_update_zone_touches()` already invalidates correctly (price-through or 60-day rolling cutoff). Confirmed directly in `gravity_memory`: 13 simultaneously-active `168H_MICRO_ANCHOR` rows clustered in one ~$160 band, spanning 2026-06-05 to 2026-06-23. `7_DAY_KABRODA` is the worst offender — it also gets an *extra* +1.5 KDE weight bonus specifically for that source name (`gravity_math.py`), and accumulates 6 rows per session, forever.

`calculate_gravity_kde()` sums a density contribution from every `active=True` row with no time-decay — so dozens of duplicate snapshots of what's conceptually a single rolling reference value compound into an artificially tall, persistent peak. **The MAXIMUM/HEAVY label on these walls reflected duplicate-counting of the same signal, not genuine independent structural agreement** — a fully sufficient explanation for "walls get taken out with strong energy": an artificially inflated wall never earned the strength its label implied.

**Fixed (2026-07-06):** applied the same deactivation pattern `_update_zone_touches()` already establishes to all three writers — before inserting a new row, deactivate prior active row(s) of the same `(symbol, source)` (scoped to `(symbol, source, level_type)` for `7_DAY_KABRODA`, since one session lock writes 6 different level types at once and only the matching-level_type predecessor should be superseded). Smoke-tested the deactivate-then-insert pattern directly against a real SQLAlchemy session — confirmed both prior rows flip to `active=False`, only the newest stays `True`. Confirmed via repo-wide grep that neither `168H_MICRO_ANCHOR` nor `1W_MACRO_ANCHOR` is read anywhere else by name — safe to deactivate with no other consumer depending on historical rows persisting.

**Effect is gradual and self-correcting, not a big-bang cutover:** existing stale rows only get cleaned up the next time each specific writer runs (next hourly tick for `168H_MICRO_ANCHOR`, next session lock for `7_DAY_KABRODA`, next Sunday for `1W_MACRO_ANCHOR`).

**Not touched:** `_update_zone_touches()` itself (already correct); `calculate_gravity_kde()`'s weighting formula and intensity thresholds (no evidence yet that the thresholds themselves are wrong once duplicate-row inflation is removed — revisit only if walls still look miscalibrated after this cleanup has run for a while); `trade_structure_analyst.py` (unchanged). **Whether to port wall-snapping to 4H/1H stays deferred**, per the original item #6 redirect — now with a cleaner mechanism to actually evaluate once enough post-fix data accumulates.

## ★ CORE-SOLIDITY PUNCH LIST — consolidated 2026-07-03, ranked by how directly each item blocks "clean, solid, actionable trades." Start here next session.

Owner directive: stop pure investigation, start systematically fixing toward a rock-solid core. This list pulls together everything confirmed broken or unresolved across every session so far — nothing new, just consolidated and sequenced. Sequence agreed with owner before any code gets touched.

**1. 4H/1H stop/target construction — THE core fix, everything else sits on top of this. ☑ IMPLEMENTED (v4, 2026-07-04) — see NEXT SESSION START marker above and detailed entry below. Pending commit/push/deploy confirmation.**
Confirmed broken: `_detect_4h_bos()`/`_detect_1h_bos()` in `gravity_engine.py` pull stop/target from the *nearest historical `gravity_memory` zone* — can be weeks old, unrelated to the current move. Real tested example (candidate 112): R:R of 1:1.03, a coin flip. Full diagnosis and everything tested lives in the 2026-07-01 session-4 investigation entry (below) and its "bold-hubble re-read" and "new reference material" addenda. **Design direction identified but not formalized or built:** a properly-scoped swing high/low window (empirically ~48-72 1H candles tested well; 4H equivalent untested) — matches both the real documented Krown rule (swing-pivot stop, not a fixed window or historical zone) and the entry style our BOS detector actually uses (breakout/momentum confirmation, Crown's "Strategy 1" family, not the pullback "Strategy 2/3" family). Target = measured move or Fibonacci extension from that range, not a rolling-window max/min. **Next step: formalize this into an actual design (what defines the range boundary precisely, does 4H need its own tested window size, single vs. staged Fibonacci target — still an open tension from the session-4 research) before writing code.**

**Third real live example (2026-07-03, first real 4H data point) — isolates that the TARGET math is often fine; it's specifically STOP SELECTION that's broken.** Real 4H LONG fired: entry $62,246.30, stop $57,829.40, target $66,252.70. Confirmed via real MEXC 4H candles: the stop is a genuine structural level (the actual July 1 00:00 UTC candle low, $57,820.00 — matches to within $9.40, so the zone-lookup found something real) — but it's the **origin of the entire multi-day move**, 2+ days and $4,417 (7.1%) away from entry. Meanwhile a real, recent pullback low sat at $61,100.00 (07-02 16:00 UTC, ~18h before entry) — much more relevant to the current leg. Swapping only the stop (target unchanged) moves R:R from **1:0.91 (losing-shaped) to 1:3.5 (clears the 1:2 floor)**. Separately, computed an independent measured-move target from a genuine, stabilized swing-low/swing-high pair (confirmed stable across 20/30/42-candle lookbacks: low $57,820, high $62,400) — landed at $66,826, only $573 off the system's own $66,252.70. **Conclusion: the target-construction side of the current mechanism is closer to correct than the stop-selection side — the fix should prioritize "nearest RELEVANT swing pivot," not just widen everything uniformly.** Combined with candidate 112 (1H, too-tight-symmetric) and the 1H LONG from earlier today (1:0.088, wildly stop-too-wide-target-too-close), this is now three real examples confirming the mechanism fails inconsistently — sometimes tight, sometimes wide, sometimes each side broken independently — which is itself evidence it's grabbing *whatever's nearest* rather than *whatever's relevant*, exactly as diagnosed.

**2. Stop/target execution triggers on a single 1-minute wick, not a confirmed close on the trading timeframe. ⚠ ABANDONED (2026-07-04) — premise was wrong, see NEXT SESSION START marker + detailed entry above.**
Confirmed via code trace (`ledger_closing_engine.py` Phase 4, `_fetch_1m_since` — 1-minute OHLC checked against stop/target on every candle). Contradicts real trading discipline from two independent bold-hubble sources (Mafioso's stated rule, the break-and-retest research). Compounds #1 — even a correctly-sized stop from #1 could still get clipped by intrabar noise under the current execution model. **Fix candidate, not built:** check the stop/target condition against the trading-timeframe (1H/4H) candle's own close, not raw 1-minute OHLC. **RESOLVED 2026-07-04: owner confirmed real trades use resting stop/take-profit orders on the exchange, which fire on wick touch by nature — the original mechanism was already correctly modeling real execution, not a bug. No change made.** The real, adjacent finding (stop-hunt vulnerability + a pre-existing R-accounting bug) is fixed and logged above instead.

**3. Senior Analyst never receives an explicit "current price."**
Confirmed via direct code trace: `battlebox_pipeline.get_live_battlebox()` computes live price but never threads it into the `context` dict passed to `_build_senior_analyst_context()` in `kabroda_mas_flow.py` — no `"price"`/`"current_price"` key anywhere in that function. Caused a real wrong price citation ($63,808 vs. real ~$61,600) in a live 2026-07-02 STAND_DOWN brief. Doesn't affect 4H/1H trade construction (computed independently in `gravity_engine.py`), but affects whether the 15M narrative/brief can be trusted. **Fix candidate, not built:** thread the existing `price` value into `context["current_price"]`, add one explicit labeled line in the SA's context.

**Owner asked (2026-07-03) whether the system has the actual fuel/energy to justify these moves at all, before continuing to refine stop/target geometry — this uncovered three more real gaps, expanding the punch list from 3 items to 6.**

**4. `energy_grade` is computed but never enforced — and it's a separate, cruder formula than the 15M's proven one. ☑ RESOLVED as record-only (2026-07-05) — see "PUNCH-LIST ITEM #4" entry above. A larger re-test (N=167/177 vs. N=17/18 here) showed neither formula has a reliable signal yet; enforcement deferred, kinematic_grade added as a second observational column instead of a hard block.** Traced in code: `_compute_energy_grade()` (`gravity_engine.py`) computes STRONG/MODERATE/WEAK using EMA30/50 (matches no other Kabroda convention — not the JEWEL's 9/21/35/55, not Krown's real 5/21/55/377 ribbon found in the transcripts) + MACD magnitude + a PMARP cap only at the extremes (≥95/≤5). **Nothing anywhere reads this value to block a candidate from being written** — confirmed via repo-wide grep, zero hits on any conditional check of `energy_grade`. Computed and tested the system's own exact formula against all three real live examples using real MEXC historical candles: **all three graded WEAK.** Candidate 112 and today's 4H LONG were entering *against* their own EMA30/50 trend read; today's 1H LONG had PMARP 97.22 — deep into "parabolic, stop chasing" territory. Cross-validated with the 15M's own proven `kinematic_grade` formula (BBWP + PMARP + EMA9/21/35/55 ribbon spread) on the same real data: candidate 112 = TANGLED, today's 1H = OVEREXTENDED, today's 4H inconclusive (insufficient fetched history for BBWP/PMARP, not a production limitation). **Two independently-sourced formulas agree: none of the three real trades this session tested had genuine PRIMED energy.** Directly connects to a second finding: 1H's own code has a comment reading `# GATE: 4H trend alignment logged in energy_grade. Misalignment = WEAK energy` — a real, already-computed check that inherits the exact same "never enforced" problem. Fixing the enforcement gap fixes this one for free.

  **Other options considered for the fix, not just the first idea:**
  - *(A) Reuse the 15M's exact `kinematic_grade` formula as-is* — simplest, most consistent, matches Krown's real Strategy 1 entry condition (BBWP ≤15% expanding), matches everything the Fibonacci-ribbon/BBWP-ADX research pointed at.
  - *(B) Build a genuinely separate 4H/1H-tuned version* — worth flagging a real subtlety: the 252-bar lookback that BBWP/PMARP use means a completely different real-world time span per timeframe (252 15m-bars ≈ 2.6 days, 252 1H-bars ≈ 10.5 days, 252 4H-bars ≈ 42 days). Blind copy-paste of the formula doesn't guarantee the *lookback* is equally meaningful across timeframes — worth testing, not assuming.
  - *(C) Hard block vs. soft degrade* — should WEAK/TANGLED energy hard-block candidate creation entirely, or just downgrade confidence/size (matching how the master plan's daily regime already softly influences 15M rather than hard-vetoing)? Genuine judgment call, not decided.
  - **Leaning toward (A) as the base with (C) as a hard block to start** (simplest, most testable, easiest to loosen later if it's too strict) — not committed, open for the actual design conversation.

**5. No macro/weekly bias check exists for 4H/1H at all. ☑ RESOLVED (2026-07-06) — see "PUNCH-LIST ITEM #5" entry below. Hard-gated on 1H, record-only on 4H, per backtested per-timeframe evidence.** Confirmed via repo-wide grep: zero references to `macro_bias` or `weekly_200sma` anywhere in `_detect_4h_bos()`/`_detect_1h_bos()`. The 15M system's Macro Structural Architect checks this before ever approving a trade (per CLAUDE.md); 4H/1H has no equivalent, not even computed. This is not the same gap as #4 — it's a genuinely separate, currently-nonexistent check, not something that exists-but-isn't-gated.

**6. No KDE wall / airspace obstruction check exists for 4H/1H. ◐ REFRAMED (2026-07-06), not built.** Confirmed via repo-wide grep: zero references to `kde_peak`/`gravity_kde`/`calculate_gravity_kde` in the BOS detectors. The 15M's Micro Liquidity Scavenger checks whether a heavy gravity wall sits between entry and target before calling the runway clear; 4H/1H can set a target directly into a wall and never know. Also genuinely separate from #4, currently nonexistent. **Owner pushback before building:** porting the 15M's proven `trade_structure_analyst._snap_long()`/`_snap_short()` wall-snap mechanism to 4H/1H was the natural move (it's a structural-correctness fix, not a probabilistic filter needing a backtest — same reasoning as the stop-hunt buffer). But the owner isn't confident the mechanism is well-calibrated on the 15M system it's already live on — walls often appear to get "taken out with strong energy in the chart." **New next step: audit whether 15M's HEAVY/MAXIMUM wall-snap actually improves outcomes there** (does a snapped target hold up against real subsequent price action, or does strong momentum blow through it, costing real profit an un-snapped Fibonacci target would have captured?) before deciding whether to port it anywhere, or whether it needs recalibrating first. Not scoped or built yet.

**Sequence agreed:** #1 first (the actual foundation), then #2 (natural follow-on, still touching stop/target logic), then #4 (cheap — the computation already exists, just needs reuse + enforcement — and directly informs whether #1's candidates should have fired at all), then #3 (contained, different file), then #5 and #6 (genuinely new builds, lowest priority of the six since they add filtering on top of a foundation that needs to exist first).

**Testing/verification methodology — owner asked how we'll actually be sure any of this works, not just assume it.** Every fix in this list gets checked the same way tonight's diagnosis was done: real historical MEXC candles, real past BOS-firing moments, compute what the new logic would have produced, compare against what actually happened. This connects directly to the already-parked **W-3 Backtest** item (owner-flagged HIGH priority, parked "until structure is settled" — that condition is close to being met by this same punch list). Concrete proposal, not yet built: a small, reusable backtest script — not the full W-3 generic backtester, just enough to replay each fix against every real 4H/1H BOS moment we can find in history and report what would have happened, before any of it touches the live system. Same rigor as the manual one-off checks done tonight, just made repeatable instead of hand-run each time.

**BUILT (2026-07-03): `mtf_backtest_lab.py`** — standalone, not wired into main.py, not production code. Fetches real MEXC candles, scans for momentum/breakout signals (close breaking a confirmed pivot — Crown Strategy 1 style), builds a trade plan (pivot stop + 1x/1.618x/2.618x staged target), walks forward through real subsequent candles to see what actually happened, reports aggregate stats sliced by energy grade. **Explicit, honest scope limit documented in the file's own header:** this validates the NEW logic against real price history — it cannot replay what the OLD gravity_memory-zone-lookup system would have picked historically, since that depends on production database state not reconstructable from OHLCV alone. Run via `python mtf_backtest_lab.py --tf 1h --days 60` (or `--tf 4h`).

**First real run — honest results, including the parts that complicate the plan, not spun:**
- **1H (60 days, N=17 signals):** ALL signals win_rate=52.9%, avg_R=+0.118. Current crude energy formula: STRONG/MODERATE bucket avg_R=+0.000 (N=4), WEAK bucket avg_R=+0.154 (N=13) — **the current formula's "good" grade underperformed its "bad" grade.** 15M-style formula: PRIMED avg_R=+0.333 (N=6, best), TANGLED avg_R=+0.250 (N=8), OVEREXTENDED avg_R=−0.667 (N=3, worst, 0% win rate) — a clean, sensible gradient.
- **4H (200 days, N=18 signals):** ALL signals win_rate=27.8%, avg_R=**−0.333** — negative expectancy across every bucket, not just an energy-filtering question. 15M-style formula showed the **opposite pattern from 1H**: PRIMED was the *worst* performer here (N=2, avg_R=−1.000, both stopped out), TANGLED relatively best (still negative, −0.250R).
- **The honest read:** WEAK-beats-STRONG on the current crude formula was consistent across both timeframes — a real, uncomfortable signal that formula may pick the wrong side. But the 15M-style formula's PRIMED/TANGLED/OVEREXTENDED read did NOT transfer consistently between 1H and 4H — directly matching the already-logged concern that the 252-bar lookback spans a completely different real-world time span per timeframe (2.6 days on 15m vs 10.5 days on 1H vs 42 days on 4H), so blind copy-paste of one formula across timeframes may not be valid. **Sample sizes (N=17, N=18, energy-split subgroups down to N=2-6) are nowhere near this project's own established N≥30 trust threshold — this is a real first data point, not a basis for committing to any energy-gating rule yet.**

**Owner directive (2026-07-03): stay open-minded on the energy/fuel signal itself, not just default to reusing what's already in the codebase — this is exactly why the Crown research happened.** Alternative signals surfaced across the full research arc, not yet tested, worth running through the same harness before committing:
- **Volume confirmation** — cited independently by the external break-and-retest research ("a price break without volume is a fake-out far more often than a genuine move") — currently used by *zero* energy formula in Kabroda (neither the current crude one nor the 15M-style one reads `volume` at all), despite every fetched candle already carrying it.
- **BBWP descending + ADX elevated/rising specifically** ("energy building," the exact nuance found testing candidate 112 two nights ago) — distinct from both the current formula (no BBWP/ADX at all) and the 15M-style formula's static `BBWP≤30` snapshot check — this is a directional/trend read on BBWP, not a threshold snapshot.
- **RMO (Revin Momentum Oscillator)** — now genuinely verified working code (63/63 tests passing for real, 2026-07-03) — a 5-vector composite momentum score, structurally unlike anything in Kabroda.
- **Dominant trend classifier** (HH/HL/LH/LL swing structure + MA alignment, `bold-hubble/indicators/trend_volatility.py`) — a different trend-confirmation philosophy than EMA crossover.
- **Krown's actual Strategy 1 entry condition** — "BBWP expanding *from* ≤15%" specifically (the trend of BBWP recovering from a squeeze), not a static compression snapshot.

**Extended `mtf_backtest_lab.py` (2026-07-03) with volume confirmation and BBWP-descending+ADX-elevated, re-ran both timeframes. Honest results, not spun positive:**
- **Volume confirmation barely discriminates in this sample** — 16 of 17 (1H) and 16 of 18 (4H) signals had above-average volume on the breakout candle. Nearly every breakout candle has above-average volume by nature of being a breakout — the binary threshold doesn't separate much at this sample size. The "false" bucket (N=1 and N=2) is too small to read anything from either way.
- **BBWP-descending + ADX-elevated — the specific "energy building" pattern from the candidate 112 case study two nights ago — does NOT hold up against a broader sample.** 1H: True bucket avg_R=+0.000 (N=6) vs. False bucket avg_R=+0.182 (N=11) — the pattern's presence correlated with *worse* outcomes, not better. 4H: True and False buckets were **literally identical**, avg_R=−0.333 both (N=3 vs N=15) — zero discriminating power. One promising case study does not generalize; this is exactly why testing against a broader sample matters more than trusting a single compelling example.
- **Of five signals tested tonight (current crude formula, 15M-style kinematic_grade, volume confirmation, BBWP-descending+ADX-elevated, PRIMED+volume combined), only the 15M-style formula showed a clean, sensible gradient — and only on 1H, inverting on 4H.**
- **Two honest, currently-indistinguishable explanations, not yet resolved:** (a) N=17-18 signals is genuinely too small to see any real pattern — pure noise at this sample size, matching this project's own N≥30 discipline being violated by every subgroup tested tonight; or (b) the breakout-detection method itself (a simple pivot-break proxy, not necessarily representative of what makes a genuinely well-formed setup) isn't capturing good setups regardless of what energy filter sits on top. **Cannot tell which without a much bigger sample — that's the honest next step, not picking a signal from what's been tested so far.**

**GAP FOUND (2026-07-03, owner caught this — hadn't been checked before `mtf_backtest_lab.py` was built): Crown has a dedicated course section specifically about backtesting methodology, and it was never checked.** `bold-hubble/extract/course_map.json` section "Strategy Creation & Quantitative Testing" contains: "Strategy Back-Testing Template" (text lecture — possibly an actual downloadable template), "How To Use The Strategy Back-Testing Template," "Strategy Optimization Tactics," and **"Forwards Walk Testing."** **No transcript exists for any of these** — only the daily YouTube stream commentary was ever pulled into `youtube_streams_analysis.json`; this course section's actual content is a real, unexplored gap. Pulling it would need the same `download_hls_vtt.py` process already flagged as friction (needs the real `.m3u8` stream URL, not the Teachable page URL, requires being logged into the course).

**Separately, and actionable right now without needing that content:** the title "Forwards Walk Testing" points at a discipline `mtf_backtest_lab.py` did NOT apply tonight, and it isn't even new — **Kabroda's own original master plan already states it**, under ANTI-OVERFITTING DISCIPLINE: *"Walk-forward discipline: when data allows (N≥60), optimize on first 60%, validate on next 20%, never re-optimize on holdout."* Tonight's five signal tests ran each one as a single combined sample (aggregate stats across the whole fetched window) — no optimize/validate split, no check for whether a pattern found in one period held up in a later, separate period it wasn't tuned on. **This is a real methodological gap in the tool as built, worth fixing before trusting any future signal-selection result from it — independent of whether Crown's specific transcript ever gets pulled.**

### PUNCH-LIST ITEM #1 — STOP-WINDOW VALIDATION + DECISION (2026-07-04)

**Cross-checked the punch list against the pre-existing master plan before building — no drift found.** The master plan's Component 1/2 (4H/1H detection) turned out to already be built and live (`_detect_4h_bos`/`_detect_1h_bos`, `target_logic_version='v3'`). Confirmed the punch list is the direct, current continuation of that work, not duplicate or stale — item #1 is specifically about those same two functions, still broken as of the 2026-07-03 real examples. Planned the fix in plan mode (see `idempotent-doodling-iverson.md` appendix) before touching any code, per standing discipline.

**Real bug found and fixed in `mtf_backtest_lab.py` itself before trusting any validation output from it:** `fetch_candles()`'s pagination stopped after one page whenever a batch came back smaller than the requested `limit=1000` — but MEXC's public klines API silently caps every response at 500 regardless of the requested limit. `--days 90` was silently returning only ~21 days of 1H candles (500 candles), and every prior run of this tool (2026-07-03 included) was unknowingly working from a much smaller real sample than requested. Fixed by removing the incorrect early-exit condition; confirmed via re-fetch: `--days 90` on 1H now correctly returns 2160 candles (90 days), `--days 400` on 4H returns 2400 candles (400 days).

**Extended the tool with `find_confirmed_pivots_windowed()`, `calc_atr()` (built ATR-slice-correct from the start), `build_trade_plan_windowed()`, and a `--window-test` grid driver** to empirically validate the production fix (windowed nearest-pivot stop, dropping the heat/touch/departure qualification gate) before writing any `gravity_engine.py` change, per the approved plan.

**Grid-test results, real MEXC history, pagination bug fixed:**

| TF | Order-by | N | Behavior across windows |
|---|---|---|---|
| 1H (90d, N=170 signals) | recency | 166→170 | Stabilizes cleanly from window=48 bars (2 days) onward: win_rate 52.4%, avg_R +0.119, flat through 96 bars. Beats the whole-history baseline (51.2%, +0.089). |
| 1H (90d) | price | 166→170 | **Never stabilizes** — win_rate climbs monotonically 54.8%→65.3% as window widens 24→96 bars with no plateau. |
| 4H (400d, N=176 signals) | recency | 141→176 | Stabilizes cleanly from window=30 bars (5 days) onward: win_rate 52.8%, avg_R +0.121, identical through 42 and 60 bars. Beats the whole-history baseline (48.3%, +0.044). |
| 4H (400d) | price | 141→176 | **Never stabilizes** — win_rate climbs monotonically 53.9%→63.1% as window widens 10→60 bars with no plateau. |

**Decision: order-by = RECENCY (nearest confirmed pivot in time), not price-proximity.** This is a real, cross-timeframe-confirmed finding, not a coin flip: price-proximity ordering shows the exact same "expanding pool produces better-looking numbers with no natural stopping point" dynamic on BOTH 1H and 4H — this is the same failure shape as the original bug (an unbounded or loosely-bounded search grabbing whatever's most flattering rather than whatever's genuinely relevant), just without the confounding heat/touch/departure filter on top of it. Recency ordering, by contrast, plateaus cleanly and quickly on both timeframes, and the plateaued value beats the current whole-history-nearest baseline on both — a legitimate improvement, not an artifact of a widening search.

**Decision: `STOP_WINDOW_1H` = 48 bars = 2 calendar days. `STOP_WINDOW_4H` = 30 bars = 5 calendar days.** Chosen as the first window size at which each timeframe's recency-ordered results reach full signal coverage (N) and stop changing on wider windows — the honest "plateau point," not a cherry-picked peak. N=170 (1H) and N=176 (4H) both clear this project's own N≥30 bar; neither clears the sourced N≥100 standard with full statistical confidence, but both are large enough that the recency-vs-price divergence and the plateau-vs-no-plateau pattern are a real directional finding, stated as such — not overclaimed.

**Next:** port these two window sizes + recency ordering into `gravity_engine.py` per the plan (`_nearest_pivot_in_window()`, Fibonacci-staged T1/T2/T3, `target_logic_version='v4'`), replacing `_qualified_4h`/`_qualified_1h` and the `opp_row` target lookups.

### MASSIVE BOLD-HUBBLE EXPANSION (2026-07-03, later this session) — owner's other agent team extracted full course catalogs; several open tensions now resolved with real sourcing

Owner had a separate agent team dig through the full training catalog and drop it into `bold-hubble/`. Two new directories, read in full: **`krown_courses/`** (11 complete course modules — Revin Suite, R-Squared Suite, KTB Bootcamp, Meta Signals Playbook, Core Indicators Masterclass, CT Indicator Suite, Quant Prime AI, Central Command Pro, Long Term Investor Tool, Price Action Pivots — skipped `options_101` as out of scope, Kabroda doesn't trade options) and **`external_traders_curriculum/`** (4 *other* traders: Tone Vays, Kyle Doops/Whale Room, Benjamin Cowen/Cryptoverse, Crypto Cred). This is genuinely sourced material (each doc carries its own "Strict Sourcing Transparency Note" where a parameter is undocumented rather than guessed) — a large step up from the earlier Discord-transcript fragments.

**RESOLVED — the target/exit methodology question, with a canonical worked example, triple-corroborated:**
The single-target-vs-staged tension from earlier tonight is answered, and it's neither of the two options as originally framed. Found in THREE independent documents (Meta Signals candle-close module, Core Indicators Masterclass Strategy #1, and a complete worked example in Central Command Pro):
```
Entry: 4H candle closes above 21 EMA AND BBWP <= 15.0% and rising.
Stop: lowest low of previous 3 candles (worked example) / most recent structural swing low (Strategy #1 doc) -- see tension noted below.
Exit: Close 50% at 1.0 R:R, move stop to breakeven. Trail remaining 50% along the 21 EMA.
      Exit entirely if a candle closes below the 21 EMA OR PMARP >= 95.0%.
Position size: risk 2% of portfolio equity per trade.
```
This is a genuine hybrid — one fixed measured-move target for a partial exit, then a **dynamic trailing exit** (not a fixed T2/T3 price) for the remainder. Directly completes the Runner Mechanic already sitting unbuilt in the master plan (Component 4). Also gives Kabroda's still-open master-plan question **OQ-1 (position sizing)** a real, sourced default: 2% risk per trade.

**RESOLVED — items #4 (energy gate) and #5 (macro/weekly bias check), with the simplest possible sourced rule:**
Meta Signals' own "Mandatory Confluence Filters" (`module_01_signal_grading_confluence.md`): **Long only if price trades above the 4H EMA 21; Short only if below it** (directly answers #5 — no macro/weekly check exists today). **Reject Long if PMARP >= 95% or BBWP >= 95%; reject Short if PMARP <= 5%** (directly answers #4 — simpler than porting the full 15M `kinematic_grade` gradient, just two hard rejection thresholds). Benjamin Cowen's **Bull Market Support Band** (20 WMA + 21 WEMA on the **Weekly** chart) is a separate, genuinely complementary macro-regime signal at a higher timeframe than the 4H EMA 21 check — worth having both, not either/or.

**RESOLVED — item #2 (execution on candle close, not wick), now sourced from a fourth independent place:** Tone Vays' weekly-close discipline states the same principle one timeframe up — *"a structural breakdown or breakout is only valid if the Weekly candle physically closes across the critical level."* Reinforces that this rule should apply hierarchically (weekly-level decisions need weekly-close confirmation, not just the trading timeframe's own close), not just as a single-timeframe rule.

**REFINED — volume confirmation shouldn't have tested as a binary snapshot.** Confirmed across multiple documents (Strategy #1 checklist, Price Action Pivots break-alert confluence rule): the real rule is volume **expanding** alongside BBWP emerging from a squeeze — a trend condition, not "is this one candle's volume above its own rolling average" (which is what got tested tonight and showed no edge — almost every breakout candle clears that bar trivially). Worth re-testing with the correct operationalization before concluding volume doesn't matter.

**A real, honest, UNRESOLVED tension found in Crown's own material — flagging plainly, not picking a side:** the stop rule for Strategy #1 (our entry family) is stated two different ways in two different Crown-sourced documents. `Core Indicators Masterclass` says *"placed strictly below the most recent structural swing low."* The `Central Command Pro` worked example says *"lowest low of previous 3 candles."* These often coincide but aren't the same rule — worth testing both against real data rather than assuming one is authoritative.

**A second real tension, worth naming honestly:** Strategy #1 (new trend emerging from a squeeze) uses the dynamic trailing exit described above. Strategy #5 (`Momentum Breakdown Short` — structural support break + BBWP shooting up from below 30%) uses a **fixed 1.618 Fibonacci extension** target instead, with a tight single-candle stop. Both entry conditions resemble what our BOS detector fires on — but Crown treats them as two different tools for two different market contexts (a brand-new trend forming vs. a breakdown of already-established support), with genuinely different exit philosophies. Our current 4H/1H detector does not distinguish between these two scenarios at all. Worth a real design conversation, not silently picking one.

**Genuinely new signal categories surfaced, not yet tested — real candidates for the "stay open-minded" directive, roughly in order of how directly they'd matter:**
- **Swing Failure Pattern (SFP)** (`crypto_cred_ta/module_02`) — price wicks through a swing high/low (sweeping retail stops) but the candle **closes back on the original side**. Entry is in the *reversal* direction, stop at the tip of the sweep wick. This is a genuinely different entry-timing philosophy than our current "close through and continue" breakout model — potentially the actual fix for whipsaw-prone setups where what looked like a breakout was really a stop-hunt. Worth testing as an alternative entry trigger, not just a stop/target refinement.
- **Order Blocks** (`crypto_cred_ta/module_01`) — stop placed at the low/high of the *specific candle* that preceded the breakout (the "last down-candle before the up-move"), not a generic swing pivot. A more precise candidate-selection rule than plain pivot detection.
- **Order-book liquidity wall absorption state** (`kyle_doops_whale_room/module_01`) — directly refines punch-list item #6: don't just check whether a wall exists between entry and target, check whether it's been *absorbed* (price already traded through it on volume without breaking) vs. still resting and unconsumed.
- **CVD (Cumulative Volume Delta) and Open Interest purge cycles** (`kyle_doops_whale_room/module_02`) — directly reconnects the already-orphaned `live_telemetry.py` (Coinalyze OI) and `liquidity_oracle.py` (L2 depth) flagged in the Suggestion Box weeks ago as "may contain real signal" — now with real, sourced methodology for how to actually use them (OI at a local peak + tight BBWP squeeze = imminent liquidation flush; CVD diverging from price = absorption, a reversal tell).
- **MRI / TD Sequential 9-13 counting** (`tone_vays/module_01`) — a mechanical, independent exhaustion-counting system (consecutive closes vs. N bars back), suggested explicitly for use as a **partial-profit trigger** on an existing trend-following position, not just a standalone signal.
- **Phantom Divergence** (`ct_indicator_suite/module_01`) — divergence checked against non-consecutive historical pivots (skipping minor noise), a more sophisticated version of the classical divergence already sitting unused in `bold-hubble/indicators/rsi_divergence.py`.
- **HPDR / HPAS** (`ct_indicator_suite/module_02`, `module_03`) — empirical (non-Gaussian) probability bands, forward-looking probability cones, and day-of-week/hour-of-day seasonality statistics. A genuinely new dimension — Kabroda has no seasonality/time-of-day awareness anywhere today.

**New, currently-nonexistent portfolio-level risk controls found — a different category than anything on the punch list, worth its own conversation:**
- Tone Vays: **40% maximum portfolio drawdown → hard stop, all trading ceases** until a new strategy audit.
- Central Command Pro worked example: **5% maximum daily drawdown → halts agent execution** for the day; **3x maximum leverage cap**.
- Kabroda has neither today. These are catastrophic-tier and daily-tier circuit breakers respectively — genuinely different from anything currently in the punch list, which is all about signal/construction quality, not account-level capital protection.

**Backtest validation standard, now sourced precisely (`quant_prime_ai/module_03`), stricter than what was cited earlier tonight:** Profit Factor >= 1.40, Maximum Drawdown < 20%, and **never evaluate strategy robustness on fewer than 100 completed trades spanning both bull and bear market cycles.** Tonight's N=17/18 test results are nowhere close by this standard either — worth holding `mtf_backtest_lab.py`'s future results to this bar specifically, not just Kabroda's own N>=30 minimum.

**Not yet read:** the 7 YouTube stream transcripts already sitting in `youtube_streams_analysis.json` (flagged two nights ago, still not mined), and the `.agents/` build-process files (their own internal orchestration artifacts, not trading content — correctly skipped).

**Explicitly lower priority — enhancement, not core solidity, do not pull forward:** the runner/staged-profit mechanic (gated behind #1 being fixed and proven — can't build a runner on a broken foundation), RSI divergence for narrative, the Fibonacci EMA ribbon question, potential Revin Ribbons integration (external build now genuinely fixed and honestly labeled as of 2026-07-03, still Tier 5 — doesn't touch trade construction).

**Already fixed and deployed, not open anymore:** the t2/t3 NOT NULL constraint bug (commit `e1b9f7e`, confirmed live via deploy log same session).

---

**2026-07-03 — EXTERNAL: separate "teamwork_preview" multi-agent project (owner's other system) attempted a Revin Ribbons Python/Pine Script replica in `C:\Users\Shadow\Documents\antigravity\bold-hubble\`. Their own "Victory Auditor" reported "VICTORY CONFIRMED — 63/63 tests passing" based on *static code reading*, never actually executing the test suite** (their own report: "execution command was attempted but timed out... static analysis validates all 63 tests"). Ran the real suite directly (`python -m unittest tests/test_revin_suite.py`) — no permission issue in this environment, 0.034s. **Real result: 59 passing, 4 failing** — a genuine math bug in RMO's duration-vector scaling (off by 4x), two NaN-propagation crashes (`high`/`low` arrays), one missing input-validation guard. Wrote two review files directly into their project folder: `EXTERNAL_REVIEW_FROM_KTBB_SESSION.md` (architecture cross-check — midline/bands/RWP well-sourced and correct, RMO's specific internal parameters are the build's own unsourced invention) and `URGENT_VICTORY_CLAIM_IS_FALSE.md` (the false-verification callout, exact failing output, what needs to happen, an offer to help). Owner is following up directly with that team. **Not connected to KTBB_app_v2 in any way yet — nothing ported, nothing will be until that team produces a genuinely re-verified result.** Logged here only because it consumed real session time and the outcome (don't trust unverified "done" claims, from any source, ours or external) is directly relevant to how the punch list above should be executed — verify every fix the same way, with a real run, not a confident-sounding claim.

---

**2026-07-03 — CRITICAL FIX: t2/t3 NOT NULL constraint silently blocked every 4H/1H candidate write since the v3 single-target deploy (commit `e1b9f7e`).**

### WHAT HAPPENED

Owner pasted a live production error log (2026-07-03): a 1H SHORT BOS on BTC/USDT (entry $61,302.00) hit `psycopg.errors.NotNullViolation: null value in column "t2" of relation "campaign_logs" violates not-null constraint`. Root cause: the single-target (v3) refactor from 2026-07-01 (`gravity_engine.py`, commit `2328dac`) intentionally writes `t2=None, t3=None` for 4H/1H candidates — that's the whole point of the single-target design — but the `campaign_logs.t2`/`t3` columns were still `NOT NULL` at the database level. Every INSERT since that deploy failed and rolled back.

**Scope of the miss: the v3 deploy went live 2026-07-01 ~15:08 UTC (confirmed via Render boot log that same session). This error surfaced 2026-07-03 ~00:38 UTC and recurred again at 00:52 UTC (retried on the next gravity-loop iteration, same failure).** Between those two points — roughly a day and a half — **zero 4H/1H candidates recorded successfully.** Any real BOS that fired in that window is unrecoverable from `campaign_logs` (the row was rolled back, never committed; the id sequence just skipped ahead, e.g. id 127 was consumed and burned). Not catastrophic (no live capital was on these CANDIDATE-only rows, and the 15M system was unaffected — it keeps its own T1/T2/T3, always populated), but real audit/observation data was lost for that window.

### THE FIX (`database.py`)

- `ALTER TABLE campaign_logs ALTER COLUMN t2 DROP NOT NULL` / same for `t3` — added to the existing `init_db()` ALTER-TABLE-in-try/except pattern (idempotent, safe to re-run, matches every other schema change in this file).
- ORM model: `t2`/`t3` changed from `nullable=False` to `nullable=True`, with a comment explaining why (v3 candidates write NULL by design; v1/v2 rows still populate all three).
- Verified every reader of `.t2`/`.t3` is safe: `ledger_closing_engine.py` already null-guards (`if c.t2 is not None`), `kabroda_mas_flow.py` and `publisher_crew.py` only touch the 15M system (unaffected — always populates t2/t3), `main.py`'s dict passthrough serializes `None` to JSON `null` safely.

### CARRY FORWARD
- **Confirm the fix is live and a real 4H/1H candidate writes successfully post-deploy** — watch for the next BOS and check `campaign_logs` for a clean insert with `t2=NULL, t3=NULL, target_logic_version='v3'`, no error in the Render log.
- **This gap should have been caught earlier** — the single-target refactor (2026-07-01) removed `t2`/`t3` from the `CampaignLog()` constructor calls but nobody checked whether the column itself allowed NULL. Worth a quick self-check habit for future nullable-field changes: grep the actual `Column(...)` definition, not just the code writing to it.

---

**2026-07-01 (session 4) — 4H/1H stop/target construction: deep investigation, NO CODE CHANGED. Real conclusion: the current mechanism is confirmed broken, and we know why, but do not have a validated replacement yet.**

### WHAT TRIGGERED THIS

Candidate 112 (1H LONG, entry $58,982.70) became the concrete test case. Full row: entry $58,982.70, stop $58,565.00, T1 $59,413.20 (legacy v2 staged targets — T2 $59,949.30, T3 $60,485.40), `htf_anchor_type=STRUCTURAL_MEASURED_MOVE`. Owner flagged this trade as "flat out stupid" after watching it — price ran to ~$61,600 while T1 sat $430 away from entry.

### THE DIAGNOSIS (confirmed, not speculative)

**Risk = $417.70 (entry−stop). Reward to T1 = $430.50. R:R ≈ 1:1.03.** Computed directly from the real DB row — this is not an opinion, it's the actual geometry. A near-coin-flip R:R on a 1H BTC trade, with a stop close enough that ordinary post-breakout retest noise can clip it.

**Root cause, traced to the code:** `_detect_4h_bos()` / `_detect_1h_bos()` in `gravity_engine.py` set stop = "nearest qualified DEMAND/SUPPLY zone in `gravity_memory`" and target = "break level ± distance to nearest opposing zone." Both numbers come from a **historical zone-lookup with a 7-20 day (1H) / 15-60 day (4H) recency window** — completely disconnected from the actual, current, immediate price structure the breakout is happening inside. A zone from 2+ weeks ago can be "nearest" by pure price-distance and become the stop or target, regardless of relevance. This is a genuine architecture bug, not a threshold-tuning issue.

### WHAT WAS TESTED AGAINST REAL HISTORICAL DATA (MEXC BTCUSDT 1H candles, June 10 – July 1 2026, 515 candles, fetched via public API — no credentials needed)

1. **Crown's actual S2/S3 strategy code** (`bold-hubble/strategies/strategy_2_uptrend_pullback_long.py`, `strategy_3_...`): `stop = min(low[-3:])*0.99`, `target = max(high[-15:])`. Tested against real candles at the exact detection moment: stop = $57,918.85 (2.5x wider than actual — solves the whipsaw problem), target = $59,455.00 (barely different from actual T1). **R:R = 1:0.44 — worse on paper.** Diagnosed why: S2/S3 assumes a *pullback entry* near the range low (Crown's strategy waits for price to retrace into the value zone before entering); our BOS detector enters immediately at breakout confirmation, already elevated above the range low. Applying his stop math to our entry timing produces a mismatched geometry. **Formula and entry style are one connected system in Crown's method — we've been using half of it.**

2. **Naive VRVP (Volume Profile) over fixed windows** (12h/24h/48h/72h, using `_calculate_vrvp()` — already exists in `sse_engine.py`, confirmed reusable/timeframe-agnostic): produced broken or poor results at every window (12h: negative reward; 72h: negative risk; 24h/48h: R:R 1:0.19 / 1:0.38, worse than actual). **Root cause:** every fixed window swallowed part of the breakout move itself, contaminating the value area. Fixed lookback windows can't distinguish "still consolidating" from "already moving."

3. **BBWP + ADX consolidation detection** (the "detect the regime instead of guessing a window" idea, sourced from external research on algorithmic range detection): computed real BBWP (252-bar percentile) and real Wilder's ADX(14) from the historical series. **Finding: this candidate was NOT a textbook squeeze.** In the 30 hours before breakout, BBWP was descending toward 30 but never crossed it (43.2 → 35.7 → 32.9), and ADX sat elevated at 31-32 (well above the "low trend" 20 threshold cited in research), not flat. The most recent *textbook* compression run (BBWP≤30 AND ADX<20 simultaneously) ended 62 hours earlier, at a price level entirely above entry — useless as an anchor. **Real finding: "BBWP descending + ADX already elevated/rising" (energy building) is a different, later-stage regime than "BBWP low + ADX low" (dead calm) — and it's closer to Kabroda's own existing PRIMED concept than to generic consolidation-detection literature.**

4. **Simple swing high/low over 12h/24h/48h/72h/96h/120h windows (no volume weighting, no database zones — just literal max(high)/min(low) over N hours):** **This produced the cleanest result of the entire investigation.**
   - Swing low was **identical across every window from 12h to 120h**: $57,820.00 — a genuine, stable structural low, resolved almost immediately regardless of lookback length.
   - Swing high **grew until it plateaued at 48-72h**: $59,455 (12h/24h, too short) → $60,775 (48h) → $60,820 (72h) → $60,940 (96h/120h, plateaus — going further back adds nothing new).
   - Resulting R:R: 12h/24h = 1:1.41 → **48h = 1:2.54 → 72h = 1:2.58** → 96h/120h = 1:2.68 (diminishing).
   - **Target at the 48-72h window ($61,938–$61,983) landed within a few hundred dollars of where price actually topped (~$61,600).** By far the best match of anything tested.

### EXTERNAL RESEARCH — THREE CONVERGENT FINDINGS

1. **Structure/range-detection tools commonly default to ~50-bar lookback** — independently matches the 48-72 candle empirical result above. Not us fitting a number to one example; an established convention landing in the same place.
2. **Fibonacci extension staging is the norm, not a single hard target:** 100% (measured move — what we computed) is described as a common *stall point* where partial profit is typically taken, not an unreasonable full-extreme ask. 127.2% = conservative first target; 161.8% = the most-cited significant extension; common practice scales out across levels (partial at 127.2%, hold to 161.8%, trail a runner to 261.8%) rather than one fixed exit. **This reopens the single-target (T1-only) decision made earlier this session for 4H/1H** — that decision was grounded in reading Crown's S2/S3 code (single structural exit); this new evidence doesn't cleanly support it. Not reversed — flagged as a real, unresolved tension, not silently dropped.
3. **The 1-hour chart's established real-world role is precision entry timing nested inside a 4-hour or daily setup — not an independent system with its own target.** One source explicitly warned that traders who treat 1H as standalone start "behaving like day traders, watching candles forming, reacting to noise" — close to what the owner described experiencing. Correct usage per this research: daily (or weekly, for 4H) sets bias and target; 1H (or 4H) is used only to get a tighter entry within that already-decided trade, producing a naturally smaller stop and better R:R because the entry is refined, not invented independently. **This directly matches the owner's own long-standing multi-timeframe philosophy** (already logged in this file under the HTF STRUCTURAL ANTICIPATION and MULTI-TIMEFRAME SSE ENGINES suggestion-box pins, 2026-06-06/06-07) — tonight's research is independent, external confirmation of a design instinct that's been on the board for weeks, not a new idea.

### BOLD-HUBBLE RE-READ (same session, later) — Crown has TWO distinct entry philosophies, and we've been mixing them

Second, closer read of `bold-hubble/strategies/` — this time hunting for full entry-condition logic, not just the stop/target lines quoted earlier in this session. Found something that directly explains why Test 1 (Crown's S2/S3 formula) produced a bad R:R.

**Strategy 2/3 (declared "15m, 1H, 4H"):** entry is a **pullback into a value zone** — `in_value_zone = curr_low <= sma_20*1.01 and curr_close >= sma_50` (price's low dips within 1% of the 20 SMA while close holds above the 50 SMA), plus RSI resetting toward neutral (S2 long: 40-53; S3 short: 47-60 — asymmetric, direction-specific ranges. **Confirmed already correctly handled in our own code** — `battlebox_pipeline.py` line 377 has an explicit comment documenting both original ranges and the deliberate decision to unify them into one 40-60 VALUE_ZONE band. Not a bug, already known.) This is NOT a breakout-moment entry — it's "wait for the dip inside an established trend." That's why S2/S3's stop can sit close: entry is already near the range low by construction.

**Strategy 1 (declared "4H, Daily, 3D"):** entry is **momentum/breakout confirmation** — uptrend confirmed, price above the 20 SMA, volatility either expanding out of a squeeze or already running hot (`curr_bbwp >= 70`). Enter immediately on confirmed momentum, no waiting for a retest. **This is what our current 4H/1H BOS detector actually does.**

**The mismatch, precisely stated:** our system enters like Strategy 1 (breakout-moment, momentum-confirmed) but Test 1 in this same session borrowed stop/target math from Strategy 2/3 (pullback-entry family) — two different strategies from two different timeframe families, stitched together. That's a coherent, specific explanation for why the S2/S3 formula test produced R:R 1:0.44: wrong strategy family for our entry style. It also explains why the wide 48-72h swing window (Test 4) worked so well — that's much closer to Strategy 1's own philosophy (ride the bigger structural move) that our entry style actually belongs to.

**Important caveat — this is NOT "switch to copying Strategy 1's formula instead."** Strategy 1's own stop (`low_prices[-2]`, a single prior candle — even tighter than what we have now, would make whipsaw worse) and target (fixed 15%) still fail our own rules (Measured Move Rule bans fixed-% targets). The useful part isn't the formula — it's recognizing which entry family our trigger already belongs to, so any future testing compares against the right strategy's logic instead of the wrong one.

### NEW REFERENCE MATERIAL ADDED (same session, later — owner-added, Discord-sourced, not GitHub)

Owner added five new files to `bold-hubble/` plus a YouTube-stream extraction batch, pulled from Discord (Krown's own technical-analysis channel, the separate Meta Signals/Mafioso server, and Krown's `#streams` YouTube broadcast archive). Full inventory, with provenance flagged per source since trust level differs:

**`KROWN_TRADING_MASTER_REFERENCE.md` + `krown_settings_and_rules.json` — Krown's own Discord, high trust.** This is the actual documented rule set, and it reveals **the Python code in `strategies/*.py` is a lossy, partly-wrong port of it** — not equivalent, as assumed all session. Direct comparison:

| | Code (`strategies/*.py`) | Actual documented rule |
|---|---|---|
| S1 stop | `low_prices[-2]` (single prior candle) | **"Previous swing low"** (a real pivot) |
| S1 target | Fixed `+15%` | **Dynamic trailing exit**: close below 20 SMA OR PMARP≥95% — not a hard target at all |
| S2 target | `max(high[-15:])` (rolling window) | **1.272 Fibonacci extension** of the actual impulse leg |
| S3 target | `min(low[-15:])` (rolling window) | **"Previous swing low"** (structural, not a fixed window) |
| S4 target | Fixed `-6%` | **"Mean reversion toward the 20 SMA"** — a real, moving level |
| S5 target | Fixed `-10%` | **1.618 Fibonacci extension** |

Every one of the five strategies was simplified when coded into Python, in ways that matter: real swing pivots became fixed-N-bar windows, real Fibonacci extensions and dynamic MA-based exits became arbitrary fixed percentages. The real rule (swing-low stop) is much closer to what Test 4 found empirically working best tonight — this is convergent, not contradictory, evidence. **All testing done earlier tonight against the S2/S3 Python code should be understood as testing a flawed proxy, not Crown's actual rule.** The real rule (Fibonacci extension target computed from the actual impulse leg, swing-pivot stop) has not been tested yet.

**`META_SIGNALS_MASTER_PLAYBOOK.md` + `META_SIGNALS_SHORT_MTF_PLAYBOOK.md` + `strategies/mafioso_mtf_signals.py` — Meta Signals/Mafioso Discord server, separate third-party service, lower trust.** This is NOT Krown's methodology — it's a different algorithmic signal provider that references Krown's indicators as a confirmation filter. **Same standing caveat already in this file from weeks ago applies: "Mafioso is a reference/mirror only — NOT a direction source, NOT a tiebreaker against Kabroda's own logic"** (see HTF STRUCTURAL ANTICIPATION pin, 2026-06-06). Still useful as an independent real-world pattern: live alerts show **partial profit at T1 (~1.0 RR, take 30-50%, move stop to breakeven), trail the remainder toward T2/T3 along the 20 SMA or 4H EMA 21** — same Fibonacci-staging philosophy the external research found earlier tonight, from a third independent source. Also states explicitly: **"SL Close Below/Above"** — never stop out on an intrabar wick, only a confirmed candle close past the level. `mafioso_mtf_signals.py` is just a regex text parser for their alert format — no calculation logic, not directly useful to us.

**`BTC_LIVE_MARKET_OUTLOOK_JULY.md` — Krown's own Discord streams, June 23–July 1 2026 (exactly our current window).** Krown's entire current bias hinges on one indicator we don't have: the **"Revin Ribbons Midband."** Per his own quote: bearish bias holds while BTC is below it; he flips bullish immediately if reclaimed. Current key level cited: **$62,000–$62,100 reclaim + daily close above the midband** as the confirmation trigger for a trend reversal. This is macro-bias/narrative-layer content (closer to the earlier publication-content audit than tonight's stop/target investigation) but is genuinely current and dated — worth noting for whenever the publication-layer suggestion-box items (weekly RSI divergence, dominant-trend classifier) get picked up.

**`extract/youtube_streams_analysis.json` + `extract/print_highlights.py` — 7 of Krown's YouTube streams (June 23–July 1), already extracted into structured highlights (17-27 per video), not yet read in depth.** Video titles: *Bitcoin Just Reclaimed $60K* (Jul 1), *Bitcoin Hovers Below $60K as S&P 500 Prints Best Quarter* (Jun 30), *While Retail Panics on Bitcoin, I'm Loading Up On These 2 Q3 Stocks* (Jun 29), *Micron's Record Earnings Couldn't Save the AI Trade* (Jun 26), *Hot Inflation Just Hit, Bitcoin's at Yearly Lows* (Jun 25), *Bitcoin, Chips, and Gold Are All Falling Together* (Jun 24), *Chips Just Got Routed, Bitcoin Hit My First Target* (Jun 23). **Not processed tonight — flagged as a real, rich resource for next time this area is opened, not rushed through now.**

**Owner suggestion, not executed tonight:** clean up/convert the lossy Python strategy files into text-based documentation instead, given the code has now been shown to diverge from the actual rules in several places. Reasonable idea, not urgent — pinned here rather than acted on; the `KROWN_TRADING_MASTER_REFERENCE.md` file the owner already added essentially *is* this for the 5 core strategies already. Would matter more if/when the YouTube stream highlights get mined and need the same treatment.

### WHERE THIS LEAVES US — HONEST STATE, NO FINAL ANSWER

**Confirmed, not in question:** the current gravity_memory nearest-zone stop/target mechanism is broken — wrong data source, produces near-coin-flip R:R, unrelated to actual current structure. This should not stay live as-is.

**Not yet answered, needs a real design pass before any code changes:**
- Exact window size to lock in for 1H (48-72h tested well on this one example; needs validation against more historical breakouts before trusting as a rule — explicitly flagged mid-session, not skipped).
- The equivalent question for 4H (untested tonight — same investigation needs to run against 4H candles and a proportionally longer window).
- Single-target vs. staged Fibonacci-extension targets for 4H/1H — real tension between this session's two research passes, not resolved.
- **The bigger architectural fork:** does 1H/4H stay as independent parallel systems (current design), or does this investigation's own evidence mean they should be rebuilt as entry-timing refinement layers nested inside 4H→Daily and 1H→4H structural reads? This is the same question as the already-gated HTF-anticipation / multi-TF SSE Engines pins — tonight didn't resolve it, but gave it real data-backed weight for when that gate opens.
- **New from the bold-hubble re-read:** our 4H/1H entry style matches Crown's Strategy 1 (breakout/momentum) family, not Strategy 2/3 (pullback) family — any future stop/target testing should be evaluated against that same family's philosophy (ride the bigger structural move), not against S2/S3's tight scalp math. Doesn't hand us a formula (S1's own formula still breaks our rules) but narrows what "the right comparison" even means going forward.
- **TOP PRIORITY NEXT TIME — untested:** the *actual* documented Krown rule (real swing-low pivot stop + real Fibonacci extension target computed from the real impulse leg, per `KROWN_TRADING_MASTER_REFERENCE.md`) has never been tested against real data — everything tested tonight against "Crown's method" was actually testing the lossy Python-code proxy. This is the natural next test, same rigor as everything else tonight (real MEXC historical candles, same candidate 112 example, honest reporting either way).

**Connects to:** HTF STRUCTURAL ANTICIPATION pin (2026-06-06), MULTI-TIMEFRAME SSE ENGINES pin (2026-06-07) — both already gated behind "15M core proven solid." Tonight's findings should be read alongside those pins next time this area is opened, not as a separate thread.

**No code was touched this session.** Pure investigation — real historical data, real formulas, real external research, a second close read of Crown's own strategy code, new Discord-sourced reference material, honestly reported including the parts that didn't work (Crown's S2/S3 *code* formula, naive VRVP, textbook BBWP+ADX squeeze detection all tested and found insufficient on their own) and the parts not yet tested (the real documented rule, just discovered).

---

**2026-07-01 (session 3) — Resolved-candidate display fix + admin email notifications (4H/1H open/close).**

### ✅ COMPLETED THIS SESSION (2026-07-01, session 3)

**Root cause: candidate 112 (1H LONG) closed CLOSED_WIN at 04:45 UTC — a real +1R win — but the radar kept rendering it as live for hours, with COPY/COCKPIT buttons still active, until the owner found out by accident.**

**Fix 1 — Resolved-candidate display (`market_radar.py`, `templates/market_radar.html`)**
`_get_tf_system_verdicts()` was setting `status: "BOS_ACTIVE"` unconditionally whenever a 4H/1H `CampaignLog` row existed for today — it never checked `closed_at`. This is the same engine-vs-body pattern as the earlier lifecycle fix: Phase 4 in `ledger_closing_engine.py` already knew the correct answer (the candidate IS closed, correctly recorded with `status='CLOSED_WIN'` and `closed_at` populated) — the bug was the display never reading that answer. Fixed the query, not the data:
- New helper `_tf_candidate_verdict(c)` in `market_radar.py` — returns `status: "RESOLVED"` (with `outcome` and `realized_pnl`) when `c.closed_at is not None`, else `status: "BOS_ACTIVE"` as before.
- `_which_tf_today()` already only grants TRADE THIS on `== "BOS_ACTIVE"` — confirmed this naturally excludes RESOLVED with no additional change needed; added an explicit comment documenting both suppression paths (price-drift via `_candidate_is_live()`, and resolved-state via the status check) so this isn't accidentally broken by a future edit.
- `templates/market_radar.html`: `tfRow()` now renders a third state — RESOLVED shows "RESOLVED — WIN +1.00R" (green) / "LOSS −1.00R" (red) / "EXPIRED (...)" (orange/gray) in place of live Entry/Stop/Target, and disables COPY/COCKPIT (shows "NO ACTION AVAILABLE" instead). `window.tfCandidateMemory` seeding now explicitly deletes the entry when a candidate is not BOS_ACTIVE, so a stale live-looking memory entry can't be triggered even via manual console access.

**Fix 2 — Admin email on 4H/1H candidate open AND close (`notify.py` new, wired into `gravity_engine.py` + `ledger_closing_engine.py`)**
- New module `notify.py`: `send_admin_email(subject, body)` via stdlib `smtplib` (no new dependency). Recipient is `SMTP_DEST` — already provisioned in Render (`SMTP_USER`/`SMTP_PASS`/`SMTP_DEST` existed as env vars but were unused anywhere in the codebase, confirmed via repo-wide grep) — used directly rather than building a new admin-query/notification-preference system, matching the "don't over-build" instruction. Non-blocking: returns `False` and logs on any failure (missing config, connection error, auth error), never raises.
- **Trigger 1 (open):** wired into both `_detect_4h_bos()` and `_detect_1h_bos()` in `gravity_engine.py`, immediately after the `CampaignLog` row commits. One email: symbol, timeframe, bias, entry, stop, target, `target_logic_version`.
- **Trigger 2 (close):** new helper `_notify_candidate_closed(c)` in `ledger_closing_engine.py`, called at all four Phase 4 resolution branches (CLOSED_WIN, CLOSED_LOSS via the shared `if closed:` block, CLOSED_AT_EXPIRY, and the no-candles EXPIRED edge case). One email: outcome, realized PnL (as `+X.XXXXR`), time-to-resolve (hours between `entry_filled_at` and `closed_at`).
- Rate limiting: none added — the existing daily dedup gate (one candidate row per symbol/timeframe/day) already caps this to at most 2 emails per timeframe per day.

### DEPLOY VERIFICATION — CONFIRMED (real end-to-end evidence, not description)

Three commits this session, all pushed and confirmed live via Render boot logs (no traceback, `Application startup complete`, `Your service is live` on each):
- `fd9e723` — Fix 1 (resolved-candidate display) + Fix 2 wiring (notify.py + open/close triggers)
- `6cfa2bb` — `POST /api/admin/test-notify` admin-gated endpoint added

**Real SMTP delivery confirmed end-to-end.** Local testing hit friction (owner initially ran the test script with literal placeholder text instead of real credentials, then a stray `<` character from copy-pasting instructional brackets, then a Gmail regular-password-vs-App-Password question) — rather than keep debugging masked local env vars, pivoted to a production-side test: added the admin-only `/api/admin/test-notify` route so the test runs inside the already-correctly-configured Render process, no credentials ever re-entered anywhere. Owner ran `fetch("/api/admin/test-notify", {method:"POST"})` from the browser console while logged in as admin:
```
{ok: true, smtp_host: 'smtp.gmail.com', smtp_port: 587, smtp_user_configured: true, smtp_dest_configured: true}
```
Owner then confirmed the actual email arrived in the `SMTP_DEST` inbox (`spiritmaker79@gmail.com`) — pasted back the literal email body text, matching what `notify.send_admin_email()` sent. **This is real inbox confirmation, not just "the API call didn't throw."** Both fixes are fully proven live:
1. A resolved 4H/1H candidate will render as RESOLVED, not live — confirmed by code path (query fix) and deploy log (no import/boot error from the changed files).
2. Admin email notifications on candidate open/close are confirmed working end to end on the exact SMTP config Render already has provisioned.

**Still genuinely open (not yet observed, correctly flagged):** no real 4H/1H candidate has opened or closed since this deploy, so the *actual* open/close trigger emails (as opposed to the manual test-notify send) haven't fired in production yet. The mechanism is proven — the same `send_admin_email()` call, same SMTP path — but the first real trigger-fired email is still pending the next live candidate.

---

**2026-07-01 (session 2) — Single-target 4H/1H, unified lifecycle check, radar parity (commit `2328dac`).**

### ✅ COMPLETED THIS SESSION (2026-07-01, session 2)

**Decision: Single structural target for 4H/1H (no T2/T3)**
Crown's strategy code confirmed — S2 exits at `max(high[-15:])`, S3 exits at `min(low[-15:])` — one structural level, no ladder. Kabroda's own reasoning lands the same place: T1 (equal-leg measured move from the broken structural zone) is the only anchored target. T2/T3 were extrapolations with no structural basis in `gravity_memory`. Dropped from both `_detect_4h_bos()` and `_detect_1h_bos()` in `gravity_engine.py`. CampaignLog `t2`/`t3` now NULL for 4H/1H candidates — ledger engine already guards `if c.t2 is not None` so no downstream break.

**Version-tag fix — v2/v3 discriminator (prevents old/new logic pooling in audit)**
Candidate 112 (the original measured-move proof candidate) has T2/T3 populated under the OLD staged-target logic, tagged `target_logic_version='v2'`. Every candidate from this deploy forward has T2/T3 NULL under the NEW single-target logic — but was about to get the SAME `'v2'` tag, which would let old-shape and new-shape rows get silently pooled in any future audit query. **Fixed by bumping the tag to `'v3'`** for all candidates written under single-target logic going forward (`gravity_engine.py` lines writing `CampaignLog`, both 4H and 1H detectors). Chose `v3` over an ad-hoc `v2b` because it continues the existing sequence cleanly (`v1`=broken Class0/DAILY_PIVOT cascade, `v2`=corrected staged targets, `v3`=corrected single target) rather than inventing new ambiguous notation. Documented as a permanent discriminator in `database.py` CampaignLog comment block — not something to remember, it's written down at the column definition. **Audit rule going forward: `v2` rows = legacy staged shape (T1/T2/T3 populated), `v3` rows = current single-target shape (T1 populated, T2/T3 NULL by design, not missing data). Never average `v2` and `v3` rows together — they measure different trade constructions.**

**Unified candidate lifecycle (`market_radar.py`)**
Replaced `_is_bos_stale()` (favorable drift only) with `_candidate_is_live()` — one function, two conditions:
- Favorable drift: price ≥75% from entry toward target → entry window closed
- Adverse drift: price ≥75% from entry toward stop → setup invalidated by market
`_which_tf_today()` updated to call `_candidate_is_live()`. Single function, single owner (market_radar.py), no scattered patches.

**Radar parity — 4H/1H panels now match 15M interactivity (`templates/market_radar.html`)**
- COPY TRIGGERS button per panel: copies `BIAS|TF|Entry:X|Stop:X|Target:X` to clipboard
- OPEN COCKPIT button per panel: opens mission cockpit modal populated with 4H/1H candidate levels
- `window.tfCandidateMemory` seeded on every TF stack render (Phase 1, Phase 2, full grid)
- `openTfCockpit()` and `copyTfLevel()` functions added
- CSS: `.tf-btn-copy`, `.tf-btn-cockpit`, `.tf-actions` styles added

### DEPLOY VERIFICATION (2026-07-01, confirmed via Render deploy log for commit `6fae865`)

```
2026-07-01T14:09:20.362975896Z ==> Build successful 🎉
2026-07-01T14:10:12.535238360Z INFO:     Application startup complete.
2026-07-01T14:10:13.173495106Z >>> GRAVITY ENGINE: Initializing background loop (v3 target logic, STRICT SSOT MODE)...
2026-07-01T14:10:13.173501796Z >>> TRADE-LIFECYCLE MONITOR: Initializing (W-9 engine, OHLC detection, Phase 4 candidates)...
2026-07-01T14:10:27.688115426Z || MACRO ANCHORS LOCKED (SPOT) || BTCUSDT | Exact Waves Mapped: 10
2026-07-01T14:10:15.939999726Z ==> Your service is live 🎉
```
No traceback, no crash-loop. The `(v3 target logic...)` boot line is the literal print statement changed in commit `6fae865` — direct proof the exact commit is running in production, not just that *some* deploy succeeded. **Committed ✅ · Pushed ✅ · Deployed & booted clean ✅ (evidence above).**

### CARRY FORWARD

- **Verify BBWP/PMARP recording after next NY session:** `SELECT date_key, bbwp_15m, bbwp_state, pmarp_15m, pmarp_state FROM session_audit_log ORDER BY created_at DESC LIMIT 3;`
- **Watch `adx_pmarp_agree` and `overextended_trigger`** — ADX secondary gate retained pending data proving PMARP covers the Jun-3 scenario.
- **Phase 2 RSI Divergence** — deferred. Column pre-reserved as `rsi_divergence_type='NONE'`. Build after N≥20 sessions with outcomes.
- **v3 single-target check:** `SELECT id, session_timeframe, target_logic_version, t1, t2, t3, htf_anchor_type FROM campaign_logs WHERE target_logic_version='v3' ORDER BY created_at DESC LIMIT 10;` — expect t2/t3 NULL on all rows (this is the v3 shape, not missing data). **Not yet run — no 4H/1H BOS has fired since deploy.**
- **Confirm no `v2` rows are written after 2026-07-01 14:10 UTC (deploy timestamp)** — any `v2` row after that means the old code path is still live somewhere.
- **Watch for the next real 4H/1H BOS candidate post-deploy** — confirm T1 populated / T2,T3 NULL / `target_logic_version='v3'` on the actual row, and confirm the radar panel renders "Target" with working COPY/COCKPIT buttons.
- **Symmetric lifecycle check confirmed by code trace, not yet by live example:** `_candidate_is_live()` in `market_radar.py` was verified symbolically for both LONG and SHORT × both favorable and adverse drift — all four branches suppress correctly at the 75% threshold (LONG: favorable suppresses at price≥entry+0.75×(target-entry), adverse suppresses at price≤entry-0.75×(entry-stop); SHORT is the exact mirror). Confirm against a real adverse-drift candidate once one occurs.

---

**2026-07-01 (session 1) — Crown Surgery (Cuts 1–5) deployed + stand-down audit complete + two production fixes shipped.**

### ✅ COMPLETED THIS SESSION (2026-07-01, session 1)

**Crown Surgery — real Crown specs replace guessed kinematic thresholds (commits `ee84e56`, `2e81ae9`, `1c57962`, `f1f0477`)**

- **Cut 1:** `_calc_bbwp()` and `_calc_pmarp()` added as module-level functions in `battlebox_pipeline.py`. BBWP = BB(20,2) width / SMA, percentile rank over 252 bars. PMARP = (close/SMA50) percentile rank over 252 bars. Helper labels `_bbwp_state_label()` and `_pmarp_state_label()` added.
- **Cut 2:** kinematic_grade thresholds replaced — PMARP≥85% = OVEREXTENDED (primary); ADX secondary retained for audit comparison (`overextended_trigger` field records which path fired, `adx_pmarp_agree` bool when both agree); BBWP≤30 + ribbon_spread>0.05 = PRIMED (direction-agnostic); else TANGLED.
- **Cut 3:** VALUE_ZONE RSI bounds 38.2–61.8 → 40–60 (Crown's real S2/S3 spec).
- **Cut 4:** 5 new columns on `session_audit_log` (`bbwp_15m`, `bbwp_state`, `pmarp_15m`, `pmarp_state`, `rsi_divergence_type`). `audit_writer.write_decision_record()` and `kabroda_mas_flow.py` extraction wired. `rsi_divergence_type` defaults to `"NONE"` — Phase 2 placeholder.
- **Cut 5:** PMARP cap in `_compute_energy_grade()` in `gravity_engine.py`. LONG + PMARP≥95 → WEAK; LONG + PMARP≥85 + STRONG → MODERATE; SHORT + PMARP≤5 → WEAK; SHORT + PMARP≤15 + STRONG → MODERATE.

**PRIMED direction bug fixed (commit `f1f0477`)**
Original PRIMED condition `bbwp_val <= 30.0 and ema9 > ema35` only fired with bullish ribbon. In DAILY_BEAR (bearish ribbon, EMA9 < EMA35), SHORT setups fell through to TANGLED → stand-down. Fixed to `ribbon_spread > 0.05` (any established direction). Root cause caught during 10-session consecutive stand-down audit.

**Stand-down audit — June 28–30 (database investigation)**
- June 28: kinematic_grade = TANGLED → genuine kinematic failure. Crown Surgery addresses this.
- June 29: PRIMED + SWEET_ZONE_BEAR + STRONG → "Box Too Narrow" — Condition 3 (KDE peak choked T1 within 0.35% of entry).
- June 30: PRIMED + SWEET_ZONE_BEAR + STRONG → two vetoes: (1) RSI divergence at Weekly+Daily — valid; (2) Box 0.97% < 1.0% floor — agent escalated low-N audit data into hard policy (spurious).
- Root: `system_audit_log.audit_md` is passed as `PERFORMANCE AUDITOR NOTE` to SA. Weekly audit contained valid finding (MEDIUM boxes 0/3) but SA treated it as a policy rule. Jun 21–27 data not in audit log (predates infrastructure).

**Two production fixes (commit `b896c51`)**
1. **1H TRADE THIS staleness gate** (`market_radar.py`): suppresses badge when price has moved ≥75% of entry-to-T1 distance. `_is_bos_stale()` helper in `_which_tf_today()`.
2. **Audit note policy escalation guard** (`kabroda_mas_flow.py`): `PERFORMANCE AUDITOR NOTE` injection now labelled "low-N observational context only — not hard thresholds or gates."

### CARRY FORWARD

- **Verify BBWP/PMARP recording after next NY session:** `SELECT date_key, bbwp_15m, bbwp_state, pmarp_15m, pmarp_state FROM session_audit_log ORDER BY created_at DESC LIMIT 3;`
- **Watch `adx_pmarp_agree` and `overextended_trigger`** — ADX secondary gate retained pending data proving PMARP covers the Jun-3 scenario.
- **Phase 2 RSI Divergence** — deferred. Column pre-reserved as `rsi_divergence_type='NONE'`. Build after N≥20 sessions with outcomes.
- **v2 equal-leg check:** `SELECT id, session_timeframe, target_logic_version, t1, t2, t3, htf_anchor_type FROM campaign_logs WHERE target_logic_version='v2' ORDER BY created_at DESC LIMIT 10;`

---

*End-of-session marker: 2026-06-30*

**2026-06-30 — Target logic v2 CORRECTED: measured-move equal-leg projections replace the Class 0 / DAILY_PIVOT cascade.**

### ✅ COMPLETED THIS SESSION (2026-06-30, third commit — pending push)

**TARGET LOGIC v2 — ROOT FIX: Measured-move targets sized to the trade's own structural range.**

**The original v2 bug (caught before any v2 rows fired):** T2/T3 used `_class0_above/below()`
(Elliott Wave macro pivots) and 1H used `_daily_pivot_above/below()`. Both produced targets
unreachable in the trade's holding window — e.g., SHORT from $58K with T2 at $25,247 (BULL_WAVE_1
from a prior cycle). Same fundamental error as v1, just in the opposite direction: target not
sized to what price can realistically reach in the trade's window.

**The fix — equal-leg measured move (same principle as the 15M system's bo−bd):**
- `base` = distance from the break level to the nearest opposing 4H/1H zone on the other side.
  - SHORT BOS broke demand at $D: nearest SUPPLY above $D → base = SUPPLY − $D
  - LONG BOS broke supply at $S: nearest DEMAND below $S → base = $S − DEMAND
- T1 = break_level ± base (1× leg from the break level, not from entry)
- T2 = T1 ± base (2× leg)
- T3 = T2 ± base (3× leg)
- ATR safety rails (secondary, not primary):
  - base < 1.5×ATR14 (4H) / 1.0×ATR14 (1H) → floor base, set `target_too_small_flag=True`
  - base > 5×ATR14 → cap at 3×ATR14 (opposing zone is too old/wide)
  - no opposing zone found → `base = 2×ATR14`, `htf_anchor_type='ATR_FALLBACK'`
- `htf_anchor_type` now records `'STRUCTURAL_MEASURED_MOVE'` or `'ATR_FALLBACK'` — never a wave label.
- Macro / Class 0 levels are NEVER trade targets. Context, KDE friction, and directional bias only.

**Removed from gravity_engine.py:**
- `_class0_above()` / `_class0_below()` helper functions (were only used for targets — now gone)
- `_daily_pivot_above()` / `_daily_pivot_below()` inner functions in `_detect_1h_bos()` (same)
- `Optional` from `typing` import (was only used by the removed helpers)

**No schema changes this commit** — all five v2 audit columns already exist from the prior commit.
`htf_anchor_type` column already exists; it now stores `STRUCTURAL_MEASURED_MOVE` / `ATR_FALLBACK`
instead of a wave label — same column, corrected values.

**Dedup gate:** v1 rows 109/110 hold 2026-06-30 date_key. First v2 candidate fires at UTC midnight
rollover to 2026-07-01. This fix landed before any v2 data was written — 'v2' tag is clean from row 1.

**Per-TF stop/target/exit logic (FINAL — matches 15M principle):**

| TF | Stop | T1 | T2 | T3 |
|---|---|---|---|---|
| **4H** | Nearest qualified 4H zone (heat≥2.0, touch≤2, depart≥1.5%), 60-day; fallback 1.5×ATR | break_level + base | T1 + base | T2 + base |
| **1H** | Nearest qualified 1H zone (heat≥2.0, touch≤2, depart≥0.8%), 20-day; fallback 1.0×ATR | break_level + base | T1 + base | T2 + base |
| **15M** | UNCHANGED — opposing trigger | UNCHANGED — measured move (bo−bd) | Same | Same |

Where `base` = range between break level and nearest opposing structural zone (ATR-floored/capped).

**Prior session (2026-06-30 second commit) — schema + v2 scaffold:**
- `gravity_memory`: `departure_move_pct FLOAT`, `touch_count INTEGER DEFAULT 0`
- `campaign_logs`: `target_logic_version`, `target_too_small_flag`, `htf_anchor_type`, `htf_anchor_price`, `energy_grade`
- `_calc_atr`, `_compute_energy_grade`, `_update_zone_touches`, daily pivot scan, zone touch tracking — all live

**Audit data separation:**
- `target_logic_version='v1'` — old rows, broken targets, exclude from signal analysis
- `target_logic_version='v2'` — corrected measured-move targets, use for audit-AI N-counting

---

**2026-06-30 — Phase 4 candidate monitoring live (commit `129837c`); Job 1/2/3 directive completed.**

*(Earlier session — kept for reference)*

### ✅ COMPLETED EARLIER THIS SESSION (2026-06-30)

**Job 1 — CRITICAL GAP FIXED: 4H/1H candidate outcomes were never recorded.**

The gap: `_detect_4h_bos()` and `_detect_1h_bos()` in `gravity_engine.py` wrote
`campaign_log` rows with `entry_filled_at=NULL` and `session_expires_at=NULL`.
The ledger engine's three phases all filter `mas_approval_status == 'APPROVED'` —
CANDIDATE rows (status=4H_CANDIDATE or 1H_CANDIDATE) were NEVER processed. After
3-6 weeks, ALL 4H/1H candidates would have `closed_at=NULL`, `status=NULL`,
`realized_pnl=NULL` — completely unauditable.

**Fix:** Phase 4 added to ledger_closing_engine.py. BOS detectors now set
`entry_filled_at=now` and `session_expires_at` at write time.

### CARRY FORWARD
- **Forward verification:** next gravity engine loop should print `|| 4H BOS v2 ||` or `|| 1H BOS v2 ||` — confirm v2 log line on Render.
- **Verification SQL:** `SELECT id, session_timeframe, bias, entry_price, stop_loss, t1, t2, htf_anchor_type, energy_grade, target_logic_version, target_too_small_flag FROM campaign_logs WHERE target_logic_version='v2' ORDER BY created_at DESC LIMIT 10;`
- **Zone strength data accumulation:** departure_move_pct will be NULL on all gravity_memory rows until new pivots are detected post-deploy. The NULL-allowed filter means selection still works in the interim; quality improves as new pivots accumulate.
- **Daily pivot forward check:** confirm `DAILY_PIVOT` rows appear in gravity_memory after next gravity loop run: `SELECT source, level_type, price, departure_move_pct FROM gravity_memory WHERE source='DAILY_PIVOT' ORDER BY timestamp DESC LIMIT 5;`
- Panel 02 HIGH CONVICTION vs MODERATE mismatch (carry-forward, no urgency)
- CoinGecko 429 recurring fix (carry-forward, publication blocker)

---

*End-of-session marker: 2026-06-27*

**2026-06-27/28 — All 5 gates passed. Phase 1 audit subsystem LIVE on production. First clean session row written 2026-06-28.**

### GATE LOG — Phase 1 Production Deploy (2026-06-27)

**Root cause confirmed:** 16 commits (from `3fc2593` "Add session_audit_log and trials_log tables" through `711d58c` "health_check.py") were LOCAL ONLY — never pushed to remote. Render was running the pre-audit codebase. `session_audit_log`, `monitor_event_log`, `monitor_config`, and `trials_log` did not exist on production. Every audit write since the subsystem was built failed with `ImportError: cannot import name 'SessionAuditLog' from 'database'`, caught silently by the try/except in `run_mas_analysis()`. Zero audit rows have ever been written to production. This is now fixed by the push.

**GATE 1 — PASSED (2026-06-27 ~18:00 UTC)**
Pushed all 16 commits + Gate 5 heartbeat commit (`00b8840`) to `origin/main`.
Evidence: `git log origin/main..HEAD` returned empty — remote is fully caught up.
Push target: `https://github.com/Kabroda-Trading/KTBB_APP_V2.git` (b9d60dd → 711d58c → 00b8840).

**GATE 2 — PASSED (2026-06-27, inferred from Gate 3)**
No deploy log captured directly, but Gate 3 confirms the new code ran: `create_all()` only executes inside `init_db()` inside `lifespan()` — the tables exist, therefore the deploy completed and the app booted successfully.

**GATE 3 — PASSED (2026-06-27)**
All four tables confirmed present on production PostgreSQL:
```
    table_name
-------------------
 monitor_config
 monitor_event_log
 session_audit_log
 trials_log
(4 rows)
```
Evidence pasted directly from psql session on ktbb_postgres.

**GATE 4 — PASSED (2026-06-28)**

`session_audit_log` — confirmed WRITING with real session data:
```
 date_key  | approval_status | kinematic_grade | bo_trigger | bd_trigger | daily_21ema_direction | weekly_200sma_position | weekly_200sma_test_count
 2026-06-28 | STAND_DOWN     | TANGLED         | 60791.175  | 60193.2722 | SLOPING_DOWN          | BELOW                  | 0
(1 row)
```
All Phase 1 MTF columns populated on first post-deploy session. `weekly_200sma_test_count=0` = no consecutive daily closes within 1% of weekly 200 SMA in last 20 sessions (correct given SLOPING_DOWN / BELOW). `weekly_200sma_position=BELOW` = BTC is currently trading below its weekly 200 SMA — significant structural context.

`monitor_event_log` — confirmed WRITING with real poll data:
```
 session_date | polls | max | sum
 2026-06-28   |     2 |   2 |   0
(1 row)
```
2 polls logged, max sequence=2, 0 transitions (expected on a STAND_DOWN session where trigger levels were not approached). Poll loop is running and writing correctly.

**GATE 5 — PASSED (2026-06-28)**
- `kabroda_mas_flow.py`: read-back heartbeat confirmed firing — `[HEARTBEAT] session_audit_log: YES (2026-06-28)` visible in Render logs (row exists = heartbeat said YES).
- `session_monitor.py`: `[MONITOR HEARTBEAT] monitor_event_log write: YES` fired on each of the 2 confirmed poll writes.
- `main.py`: `GET /api/health/audit-heartbeat` endpoint live — returns WRITING for both tables.
- `templates/admin.html`: admin page heartbeat card now shows green dots for both tables (first session post-deploy populated both).
Silent failure can no longer accumulate for days undetected — the heartbeat fires every session and every monitor poll.

**ALL 5 GATES PASSED. Phase 1 is LIVE on production as of 2026-06-28.**

---

### ✅ COMPLETED AND COMMITTED THIS SESSION (2026-06-22 → 2026-06-24)

**1. Phase C — Intraday Session Monitor (4 commits, prior to context compaction)**

Full observe-and-log infrastructure for intraday state transition tracking:
- `database.py`: `MonitorEventLog` ORM (one row per 15-minute poll) + `MonitorConfig` ORM (three-gate notification config). Both have ALTER TABLE migrations in `init_db()`.
- `session_monitor.py` (new file, ~370 lines): background async loop running during session window (8:30 AM–3:00 PM ET). Polls every 15 minutes. Tracks five discrete state variables (`kinematic_grade`, `micro_state`, `1h_fuel_status`, `4h_adx_strength`, `1h_adx_strength`). Detects transitions. Re-derives three STAND_DOWN conditions against the locked audit record. Checks three notification gates simultaneously (Gate A: 30+ resolved-session events; Gate B: human harness review; Gate C: explicit `notification_enabled` flip). Monitor cannot enable itself.
- `main.py`: `asyncio.create_task(session_monitor.run_session_monitor_loop())` in lifespan; graceful cancel on shutdown.
- `harness/README.md`: Write exception #2 documented (monitor write path, hard wall rules, three gate descriptions).

**2. micro_state_lock column (committed 2026-06-22)**
- `database.py`: `micro_state_lock = Column(String, nullable=True)` added to `SessionAuditLog` + ALTER TABLE migration.
- `harness/audit_writer.py`: `micro_state: Optional[str] = None` param + `micro_state_lock=micro_state` in row constructor.
- `kabroda_mas_flow.py`: `micro_state=context.get("micro_state")` passed to `_write_audit()`.

**3. Brief voice tuning (commit `6d24d43`)**
- `kabroda_mas_flow.py` `SENIOR_ANALYST_SYSTEM_PROMPT`: 7 edits — jargon leak table (HOSTILE_CEILING → "the market is stacked against clean entry"), interpretation clause ban, actionable verdict line before "## THE BIGGER PICTURE", TODAY'S ENERGY section, SELF-CHECK items 10-12. Voice changed; measured-move math, trigger levels, and STAND_DOWN conditions unchanged.

**4. Macro War Room UI redesign (committed standalone)**
- `templates/macro_war_room.html`: dark military-terminal aesthetic (black, cyan, purple), JetBrains Mono + Rajdhani fonts, multi-panel desk grid up to 1400px. Backend data bindings unchanged.

**5. Phase 1 MTF Structural Capture (3 commits: 076b34a → 3911b26 → 5c8ba4f)**

Build objective: capture structural timeframe state at session lock time. Zero change to decision path. Writes only to new session_audit_log columns and gravity_memory.

- **`database.py` (076b34a)**: 10 new nullable columns on `SessionAuditLog` + ALTER TABLE migrations in `init_db()`:
  - `daily_21ema_direction` (SLOPING_UP / FLAT / SLOPING_DOWN)
  - `daily_21ema_position` (ABOVE / AT / BELOW)
  - `daily_21ema_distance_pct` (float)
  - `tf4h_200sma_position` (ABOVE / AT / BELOW)
  - `tf4h_200sma_distance_pct` (float)
  - `tf1h_200sma_position` (ABOVE / AT / BELOW)
  - `tf1h_200sma_distance_pct` (float)
  - `weekly_200sma_position` (ABOVE / AT / BELOW)
  - `weekly_200sma_distance_pct` (float)
  - `weekly_200sma_test_count` (int — consecutive completed daily closes within 1% of weekly 200 SMA)

- **`kabroda_macro_engine.py` (3911b26)**: `_compute_weekly_200sma()` resamples 1500 daily candles to weekly via ISO week grouping; returns SMA of last 200 completed weekly closes. `run_macro_scan()` now writes one `WEEKLY_200_SMA` entry per symbol to `gravity_memory` (`active=False` so the KDE ignores it). Entry is deleted and re-written on every 24h scan cycle.

- **`battlebox_pipeline.py`, `harness/audit_writer.py`, `kabroda_mas_flow.py` (5c8ba4f)**:
  - `_fetch_weekly_200sma()`: reads `WEEKLY_200_SMA` row from gravity_memory at lock time (no new API call).
  - `_compute_mtf_structural_snapshot()`: computes all 10 fields from candle data already in memory. Daily 21 EMA uses `raw_daily[:-1]` (completed closes only; last candle is today's partial). 4H/1H 200 SMAs from last 200 bars. Weekly 200 SMA from gravity_memory. Test count from last 20 completed daily closes. Per-field try/except — any failure leaves that field None; never blocks the lock.
  - `get_live_battlebox()`: calls both functions immediately after `_compute_sse_packet()`, stores result in `pkt["context"]["mtf_structural_snapshot"]` before the packet is persisted to DB.
  - `write_decision_record()`: 10 new Optional keyword params, all forwarded to the ORM row.
  - `run_mas_analysis()`: extracts `_mtf = context.get("mtf_structural_snapshot", {})` and passes all 10 fields to `write_decision_record()`.

### ⚠ PENDING LIVE VERIFICATION (4 items — not yet confirmed against a live session)

1. **Monitor first rows** — `monitor_event_log` writes with sane values on the next live session (poll sequence incrementing, state snapshot populating, transition detection firing when states change).
2. **Brief voice clean render** — first live session post-deploy shows no leaked enums (no HOSTILE_CEILING, CHOP_RISK, "Condition N fires" in generated output); verdict line appears before "## THE BIGGER PICTURE."
3. **CHECK 1 (carry-forward)** — Production `session_audit_log` schema has all new columns (including `micro_state_lock` + 10 MTF columns). SQL query in Render Shell: `SELECT column_name FROM information_schema.columns WHERE table_name='session_audit_log' ORDER BY column_name;`
4. **MTF structural columns populate sanely** — After next session lock, new columns in `session_audit_log` row are non-null with plausible values: `daily_21ema_direction` matches chart, `weekly_200sma_distance_pct` is a plausible % from the weekly 200 SMA, `weekly_200sma_test_count` is a small integer.

**Weekly 200 SMA specifically**: populated on the next macro engine run (boot or 24h cycle). If the WEEKLY_200_SMA row is absent from gravity_memory, `_fetch_weekly_200sma()` returns None, all weekly fields stay NULL — no crash. Verify with: `SELECT symbol, level_type, price, active FROM gravity_memory WHERE source='WEEKLY_200_SMA';`

### NEXT (planned, not yet started)

**Phase 2 MTF Structure Memory** — deferred until 10+ sessions populate Phase 1 columns cleanly:
- Lookback function: query last N sessions from `session_audit_log`; compute directional frequency per field.
- Categorical `structural_alignment` state (NO numeric score — the design conversation rejected score-of-10 as unvalidatable at N<50). State = one of: `FULL_TAILWIND`, `PARTIAL_TAILWIND`, `MIXED`, `PARTIAL_HEADWIND`, `FULL_HEADWIND`.
- Senior Analyst injection: structural alignment block inserted into `_build_senior_analyst_context()` — same pattern as `mtf_read` (fail-open: if None, SA reads raw fields only).
- Gate: 10+ sessions minimum before Phase 2 build starts. Trigger = pattern clarity, not calendar.

**Carry forward (open, no urgency change):**
- Panel 02 HIGH CONVICTION vs MODERATE mismatch (GAP-1 class — JEWEL vs SA source; logged, not urgent)
- CoinGecko 429 recurring fix (publication blocker — coded dormant)
- CHECK 2 (carry-forward) — live MAS session writes a sane audit row with non-null RAG snapshot and agent chain `{"senior_analyst": ...}`. Pending next live session.

---

*End-of-session marker: 2026-06-22*

**2026-06-22 — Forward-audit loop subsystem deployed; analyst brief voice rewritten and wired into generation; data count corrected (~12 evaluable, not 17); three items PENDING LIVE VERIFICATION**

**Confirmed today:**
- **Forward-audit loop (harness)** — Complete subsystem built and hooked into production. `harness/audit_writer.py`: `write_decision_record()` captures frozen inputs at decision time (idempotent — skips if row exists, internal try/except swallows DB errors); `backfill_outcome()` writes outcome write-once. Production hook in `kabroda_mas_flow.py` Step 7: outer try/except around the call before `run_publisher()` — audit failure is logged and the MAS path continues unaffected. Three backfill hooks in `ledger_closing_engine.py`: after `db.commit()` on CLOSED_WIN/CLOSED_LOSS, CLOSED_AT_EXPIRY, and NO_TRIGGER paths — all try/except wrapped. Hard wall confirmed: audit tables (`session_audit_log`, `trials_log`) have no FK to `session_locks`, no write path to any live config or indicator column. `harness/README.md` documents the wall explicitly.
- **CHECK 3 PASS** — `harness/test_audit_safety.py`: 4 tests, 7 assertions, all PASS. Confirmed broken audit write cannot block or alter the trade decision path or trade close path. Two-layer protection: outer try/except in production callers + internal try/except inside audit_writer functions.
- **Brief voice rewrite (Phases 1–4 complete)** — June 22 STAND_DOWN brief diagnosed: HOSTILE_CEILING/CHOP_RISK labels throughout, "Condition 1 fires / Condition 2 fires simultaneously / Condition 3 fires" pattern. Phase 4 wired into `SENIOR_ANALYST_SYSTEM_PROMPT` (`kabroda_mas_flow.py`): 7 edits — WRITING RULES (register markers allowed, weak reads stay tentative), BEHAVIOR BEFORE LABEL + TRANSLATION TABLE, NAMED REASONS for veto headings, WHY THE SYSTEM STANDS DOWN format, VERDICT LINE (plain text before `## THE BIGGER PICTURE`), TODAY'S ENERGY, SELF-CHECK checks 10–12. Wave-context disclaimer eliminated — uncertainty woven into prose instead. Faithfulness verified against June 22 brief: every number survived ($65,196.60 trigger, $65,418.86 T1, $1,606.23 box, all three veto conditions present). Voice changed; facts unchanged.
- **Stale comment fixed** — `database.py` `agent_chain_json` column comment updated from dead 5-agent `{"msa":..,"mls":..,"kmq":..,"cro":..,"cco":..}` to live `{"senior_analyst": ...}` reality.
- **Current data count corrected** — ~12 evaluable events as of 2026-06-22 (not 17). `harness/deferred_tests.py` updated; N=30 PRELIMINARY_SIGNAL gate unchanged.

**PENDING LIVE VERIFICATION (3 items):**
1. **CHECK 1** — Production tables `session_audit_log` / `trials_log` created with correct schema, no hash-chain columns. SQL queries provided for Render Shell; not yet run on production Postgres.
2. **CHECK 2** — Live MAS session writes a sane audit row with non-null RAG snapshot, agent chain `{"senior_analyst": ...}`, and all frozen inputs. Pending next live session.
3. **Brief voice** — Renders clean on first live session post-deploy with no leaked enums (no HOSTILE_CEILING, CHOP_RISK, "Condition N fires" in generated output).

**Carry forward:**
- Panel 02 HIGH CONVICTION vs MODERATE mismatch (GAP-1 class — JEWEL vs SA source; logged, not urgent).
- CoinGecko 429 recurring fix (publication blocker — coded dormant).
- Checks 1/2/3 live verification (see PENDING LIVE VERIFICATION above).

---

*End-of-session marker: 2026-06-20*

**2026-06-20 — First APPROVED trade since 06-18; first LOSS in evaluation record (disclosed-marginal, not questionable); UI Tier 1 fix confirmed live on a real approved session; running tally 4 correct stand-downs + 1 winning approval + 1 disclosed-marginal loss**

**Confirmed today:**
- **2026-06-20 APPROVED SHORT — LOSS (−1R).** Entry 63,232.92 / Stop 63,778.93 / T1 62,602.87 (T1-only cap). 15M closed below entry (would have filled), then stopped out within ~3 fifteen-minute candles on upside spike. Price subsequently pushed toward the breakout trigger. First loss in the evaluation record. Not a questionable approval — the brief disclosed the weakness explicitly: MODERATE conviction, counter-momentum on both driving TFs (4H and 1H POSITIVE against BEARISH trend, weak ADX), unresolved weekly bullish divergence, Stand-Down-If named the exact failure mode. System called its own risk accurately. Data point logged for the watch hypothesis: "MODERATE conviction + counter-TF momentum = higher loss risk?"
- **UI Tier 1 fix (eecc6ae) confirmed live.** HUD key populated correctly on first real approved session: "SHORT | SA_APPROVED | 63232.92 | …", no "DATA MISSING." Copy-to-HUD fix verified. Minor flag: Panel 02 shows "HIGH CONVICTION" while brief says MODERATE — different source (JEWEL vs. SA). Same GAP-1 class; logged for a look.
- **Saturday session note.** Owner generally avoids weekend trading (thinner liquidity, chop-prone). Logged as watch item: do weekend approvals underperform weekday approvals? One data point.
- **15M 200 SMA at breakdown point** — possible same blind-spot class as SSE-into-TSA gap. One instance; hold loosely. Watch for recurrence.

**Open watch hypothesis (logged, not acted on):** "GATE OPEN / MODERATE conviction / counter-momentum-against-trade-direction" — does this correlate with losses? One data point. Need multiple instances before any gate adjustment.

**Carry forward:**
2. **[BUG — ACTIVELY RECURRING]** Intel Reporter: CoinGecko 429 firing again. Demo API key registration dormant fix is now worth doing. Not session-blocking but degrades the intel reporter on every stand-down day.
3. **[COSMETIC]** Cumulative performance chart x-axis out of chronological order (values correct, sort wrong).
4. **[☑ BUILT + VERIFIED — eecc6ae]** UI unification Tier 1 confirmed live on 2026-06-20 approved session. HUD key correct. Minor: Panel 02 "HIGH CONVICTION" vs. brief "MODERATE" — flag for a look.
5. **[CHECK — W-9 PHASE 2]** Confirm the 06-18 runner outcome recorded correctly in production (CLOSED_WIN or correct at-expiry handling). Cannot confirm until production DB checked. Also: confirm 06-20 LOSS records correctly as CLOSED_LOSS / −1R.
6. **[FIX — GATED STRENGTHENING]** SSE-into-TSA target wiring — first-need proven on 06-18; 15M 200 SMA blind-spot observation on 06-20 may be same class. Data already in pipeline.
7. **[BOARD REVIEW]** 15M core status: W-6, B1, W-10, W-1 — menu for what's next.
8. **[R1 — KNOWN MINOR]** Trades hitting stop/T1 between midnight UTC and next-session-open will have `closed_at` on the following calendar date. Grouping by `date_key` is correct; `closed_at::date` grouping misattributes those to the next day.

---

*End-of-session marker: 2026-06-16*

**2026-06-16 — W-9 Phase 2 fully resolved: OHLC detection + next-session-open window**

**Confirmed today:**
- **W-9 Phase 2 root cause found and fixed (OHLC upgrade, commit `3385a7b`)** — Root cause: Phase 2 checked `now_utc >= session_expires_at` BEFORE any price evaluation and `continue`d past all stop/T1 checks — a filled trade reaching 3 PM ET was stamped `EXPIRED/null` unconditionally, identical to an unfilled trade. Confirmed by id=94 (2026-06-15 LONG, entry filled 13:06 UTC, stop eventually hit 22:26 UTC, stamped EXPIRED at 19:00 UTC with `realized_pnl=NULL`). Secondary: `ticker["last"]` (MEXC snapshot every 60s) missed intrabar stop/T1 touches between polls. Fix: replaced snapshot detection with 1m Kraken OHLCV candle scan (`_fetch_1m_since`). Filled trades now run until candle `low ≤ stop` (LONG) or `high ≥ T1`, bounded by next-session-open (next day 8:30 AM ET via `_next_session_open_utc`), not 3 PM ET. Same-candle stop-first rule (conservative). Genuinely-unresolved case: `CLOSED_AT_EXPIRY / fractional R / target_hit="EXPIRY"`. Phase 1 (unfilled → EXPIRED at 3 PM) unchanged. Phase 3 (post-T1 observation) unchanged. SF-6 Rule 4 added.
- **id=94 data correction (separate step, owner-run)** — MEXC 1m scan confirmed stop ($66,131.62) first touched at **22:26 UTC June 15** (6:26 PM ET), 3h26m after session close, well within next-session-open window. Kraken in-session scan (13:06–19:00 UTC) confirmed stop was NOT hit during session — in-session low was $66,716.50. Under the corrected rule, id=94 is `CLOSED_LOSS / -1.0R / target_hit="STOP" / closed_at=2026-06-15 22:26 UTC`. UPDATE shown as separate step — owner to run in production terminal.
- **Coherence fix (d9a4a92) verified LIVE** — today's brief fired at 13:00 UTC from the `session_lock` trigger (not a page-visit). Scheduler-as-primary-trigger confirmed working end-to-end on its first real day post-deploy.
- **id=94 corrected live** — owner ran the UPDATE in production terminal. Trade History now shows `CLOSED_LOSS / -1.0R / STOP`, rendering correctly.
- **W-9 Phase 2 fix (3385a7b) live, forward verification pending** — first real day on production. Next gate: a FILLED trade that hits stop or T1 after 3 PM ET must record `CLOSED_WIN` or `CLOSED_LOSS` with `closed_at` at the actual candle timestamp, not `EXPIRED`. Cannot be forced — confirms on the next filled session that resolves outside the 9 AM–3 PM window.
- **RE-ARM ALERTER logged (b9f0e4b)** — strengthening-phase Suggestion Box pin. Gated behind 15M-solid + W-4 notification infra.
- **CONTINUOUS SESSION-EVALUATION DISCIPLINE locked in (docs)** — new OPERATING DISCIPLINE section in WORK_LOG; SF-7 operating rhythm section in SYSTEM_FLOW. Defines the bear-market-pullback evaluation mode: daily lightweight log, three honest outcome categories, longitudinal pattern-detection as Claude's standing job, trigger = pattern clarity not calendar.
- **W-15 ☑ AUDITOR THIN-DATA LEGIBILITY FIX (commit `cdd2425`)** — `_format_stats_block` now guards all three breakdown sections (Harmonic Energy, Kinematic Grade, Box Size) with `insufficient_data = (resolved_dir == 0)`. When true, each section emits one "INSUFFICIENT DATA — 0 resolved directional outcomes" line instead of per-row zero-count tables. Calibration task for the evaluation discipline: weekly auditor now reads honestly on thin-data weeks, making stand-down validation trustworthy as the automated half of the cross-check.

**Carry forward (archived — see 2026-06-17 carry forward above for current state):**
2. **[BUG]** Intel Reporter: CoinGecko 429 — not recurred on 06-13; continue to monitor.
3. **[COSMETIC]** Cumulative performance chart x-axis out of chronological order (values correct, sort wrong).
4. **[BOARD REVIEW]** 15M core status: W-6, B1, W-10, W-1 — true current state reported in Part 2 read-only pass (2026-06-14). W-7 ☑ closed 06-15. This is the menu for what to work on next.
5. **[AUDITOR BUG — ☑ CLOSED 2026-06-16]** W-15 shipped. See bullet above + CHANGE LOG.
6. **[R1 — KNOWN MINOR]** Trades that hit stop/T1 between midnight UTC and next-session-open will have `closed_at` on the following calendar date. Grouping by `date_key` (session label) is correct. Grouping by `closed_at::date` will misplace those outcomes into the next day's audit bucket. No code fix needed now — flag for future auditor improvement.

---

*End-of-session marker: 2026-06-15*

**2026-06-15 — Time-coherence gap fix + W-7 Fix 3 stale example residue shipped; architecture honest-picture**

**Confirmed today:**
- **MAS architecture is a fixed sequential pipeline, not an orchestrator** (read-only, no code touched) — `run_mas_analysis()` fires each module in a hardcoded Python call order: TSA → DB reads → MTF interpreter → gravity interpreter → junior analyst → context assembly → SA → DB writes → publisher. No LLM decides routing. No feedback loops. The SA is the terminal stage; it receives a pre-assembled frozen string and produces JSON. The one "retry" is a JSON FORMAT correction (append "[CORRECTION: return valid JSON]" + re-call), not a semantic re-evaluation. The Senior Analyst is not a conductor; Python is. Full report delivered to owner.
- **ENERGY/LEVEL TIME-COHERENCE GAP — found, scoped, and shipped (commit `d9a4a92`)** — Option 2 chosen: `main.py` scheduler now fires at `lock_end_ts` instead of a hardcoded 14:00 UTC. DST-aware `_seconds_until_lock_end()` helper uses `session_manager` pytz logic (EDT: 13:00 UTC, EST: 14:00 UTC). Boot-time check uses `now.timestamp() >= _boot_lock_end_ts` (not `now.hour >= 14`). `date_key` comes from `session["date_key"]` in both paths. Page-visit double-fire guard is unchanged — `_CACHE_LOCK` + existing lock in DB prevents re-fire. **Verification checkpoint: tomorrow 9:00 AM ET — brief should be sitting there on arrival, not triggered by page-visit.** Suggestion Box 2026-06-15 pin marked SHIPPED; W-12 status updated (scheduler now PRIMARY trigger).
- **W-7 ☑ FULLY CLOSED — Fix 3 stale SA prompt example fixed (0805bd4)** — CONDITION 2(a) was updated in `ff60c5a` (2026-06-06) to magnitude logic (STRONG NEGATIVE excluded; WEAK/DEPLETED fires). The adjacent WHY THE SYSTEM STANDS DOWN example still cited "4H Momentum NEGATIVE" — updated to "4H Momentum WEAK [DEPLETED]" to match. SA reads examples as format templates; stale example could teach it to cite old sign-only wording. W-7 header, checklist, and status line all closed. Unblocks: Part 2 mean-reversion mode (Suggestion Box 2026-06-03).

**Carry forward (archived — see 2026-06-16 carry forward above for current state):**
2. **[W-9 PASSIVE]** Forward verification only — superseded by Phase 2 fix 2026-06-16.
3. **[BUG]** Intel Reporter: CoinGecko 429 — not recurred on 06-13; continue to monitor.
4. **[COSMETIC]** Cumulative performance chart x-axis out of chronological order (values correct, sort wrong).
5. **[BOARD REVIEW]** 15M core status: W-6, B1, W-10, W-1 — true current state reported in Part 2 read-only pass (2026-06-14). W-7 ☑ closed this session. This is the menu for what to work on next.
6. **[AUDITOR BUG — NEAR-TERM]** Thin-data legibility fix in `performance_auditor.py`: zero resolved outcomes → "INSUFFICIENT DATA", not "0%". One session, no dependencies. Before next Sunday's run.

---

*End-of-session marker: 2026-06-14*

**2026-06-14 — W-9 top-priority blocker cleared + strengthening-phase vision pinned**

**Confirmed today:**
- **W-9 ☑ CLOSED** (commits `9ec43b1` + `cc49904`, verified 2026-06-14) — lifecycle monitor built, schema gate cleared on production (5/5 columns live), Step 5 confirmed complete-by-prior-cleanup. Four production queries: Q1=6 pre-monitor APPROVED rows with null expiry, Q2=0 open+unfilled, Q3=0 open, Q4=0 phantom CLOSED_LOSS — dataset already in the state Step 5 was meant to produce. IDs 86/89 were already reclassified EXPIRED by prior cleanup. W-9 was the declared top-priority blocker for the backtest, track record, and model-optimization work.
- **W-11 verified live** — today's MAS run wrote `MAS_STAND_DOWN` to `decision_journal.decision_type`, confirming the 4-value map works in production. Stand-down accuracy is now computable on next Sunday's auditor run.
- **Production DB terminal established** (Render `psql` session) — direct Postgres access confirmed operational. Eliminates the throwaway-diagnostic-route pattern; used today for W-9 schema gate check and all four Step 5 queries.
- **W-14 pinned** — strengthening-phase vision (multi-timeframe + signal-conviction cluster) logged as a thin connective node referencing MULTI-TIMEFRAME SSE ENGINES, HTF STRUCTURAL ANTICIPATION, and VET-A-TRADE Suggestion Box pins. Gate: 15M core solid across many live sessions.
- **Auditor gap analysis + Suggestion Box enrichment** (docs-only) — four genuine gaps from this week's auditor output logged: (1) Coach pin enriched with per-indicator granularity refinement to decision-level review; (2) Job 2 dependency chain drawn explicitly in Coach pin sequencing — reprioritizes Job 2 as prerequisite for deep audit, not just backtest plumbing; (3) NEWS/EVENT CALENDAR AWARENESS pinned as new item in W-14/14c cluster (macro event calendar dimension, distinct from price-structure anticipation); (4) AUDITOR THIN-DATA LEGIBILITY BUG logged as near-term fixable item — "0% / everything failed" on zero resolved outcomes is a cry-wolf output; fix before next Sunday.

**Carry forward (archived — see 2026-06-15 carry forward above for current state):**
2. **[W-9 PASSIVE]** Forward verification only: next real no-fill APPROVED session must run through Phase 1 → EXPIRED/pnl=null correctly. Cannot be forced.
3. **[BUG]** Intel Reporter: CoinGecko 429 — not recurred on 06-13; continue to monitor.
4. **[COSMETIC]** Cumulative performance chart x-axis out of chronological order (values correct, sort wrong).
5. **[BOARD REVIEW — see below]** 15M core status: W-6, W-7 Fix 3, B1, W-10, W-1 — true current state reported in Part 2 read-only pass (2026-06-14). This is the menu for what to work on next.
6. **[AUDITOR BUG — NEAR-TERM]** Thin-data legibility fix in `performance_auditor.py`: zero resolved outcomes → "INSUFFICIENT DATA", not "0%". One session, no dependencies. Before next Sunday's run.

---

*End-of-session marker: 2026-06-13*

**2026-06-13 — Phase A join key production-confirmed + null-PnL fixes verified live**
Job 2 Phase A item 1 confirmed live (commit `4e82934`): `session_id` join key on `DecisionJournal` writing correctly. Both null-PnL crash fixes confirmed live (`ba34e8f`, `32fc241`). W-11 shipped (`63d0c24`): source column + 4-value `decision_type` + historical backfill. W-12 closed: MAS scheduler confirmed autonomous (page-visit path fires first, 14:00 UTC scheduler is fallback). Session result: STAND_DOWN.

---

*End-of-session marker: 2026-06-07*

**A3 + A1 CONFIRMED LIVE 2026-06-07.** Session APPROVED a SHORT despite 4H/1H POSITIVE momentum on a BEARISH trend. System correctly classified the 24–48h bounce as a counter-trend pullback "decelerating into bearish continuation, not a reversal" — matches owner's own structural read. This is the exact ambiguity (positive momentum + bear trend) that would have tangled the old gate; A3's strength-aware logic handled it correctly. JA reconciled THREE inputs (energy + structure + bias_model/macro divergence cap) cleanly, no false-certainty, concluded "single-target at most." T1-only capped correctly for STRUCTURAL reason: all 3 short targets collapsed to the $60,025.76 MAXIMUM wall — nowhere to go but the wall. All 5 agents SUCCESS on new code. NOTABLE: Kabroda's read (short the pullback, bearish continuation) aligns with owner's structural view AND opposes Mafioso's long call — independent confirmation Kabroda's logic reflects intended framework.

**A2 status:** Deployed (b5b928d), NOT exercised — MTF interpreter hasn't failed yet, so the fallback render path hasn't fired. Confirm A2 rendering on first degraded session.

**W-6 T1 DONE (d644366, 2026-06-07):** chart renders + matches KPI; trade table renders. T2 legibility polish BLOCKED — see W-9.

**WEEKLY SCHEDULER — FIRST SCHEDULED RUN FIRED 2026-06-07 23:00 UTC. AUDITOR PASSED + FOUND 2 REAL BUGS.** Both agents ran SUCCESS. Performance Auditor: $0.0137. Elliott Wave Specialist: $0.0183. Elliott Wave: BEAR_WAVE_4_BOUNCE / IN_PROGRESS / 13.7% complete, invalidation $60,055 (not yet ZigZag-locked — needs 20% reversal for confirmation). 3rd independent confirmation of bearish structure. Auditor output IS visible on production dashboard (Internal System Audits collapsible). **Auditor findings (treat as strong leads — computed on outcome data with known integrity issues per W-9, so accuracy %s are provisional):** (1) MAJOR — kinematic pipeline failing: 86 of 93 resolved calls (92%) return UNKNOWN kinematic_grade, performing at 17.4%. Classification pipeline not assigning grades on the vast majority of sessions — systemic data-quality bug invisible to daily observation. (2) Stand-down gate over-firing: 38 fires, 70.3% accurate, 29.7% overcautious. Corroborates exit_warning-too-blunt concern with real data. (3) BUG — auditor output TRUNCATED mid-sentence at 600-token limit. Raise max_tokens or tighten prompt. Elliott Wave reasoning still has no readable view (gravity-map Wave Context panel only shows the label). See W-10 and W-11.

**NEXT ACTIONS:** (1) **W-11 KINEMATIC GRADE UNKNOWN — near top, read-only verify first.** Is the pipeline really failing 92% of sessions, or a counting artifact from outcome data integrity issues? Verify before any fix. (2) **W-9 OUTCOME-TRACKING INTEGRITY — TOP PRIORITY.** Read-only verification pass: how are outcomes assigned, how many rows mislabeled? (3) **W-10** — auditor output now visible; remaining: wave reasoning view + navigation + auditor token-limit fix. (4) W-6 T2 legibility polish — BLOCKED until W-9 resolved. (5) CoinGecko 429 fallback before publication.

---

## OPEN WORK ITEMS

Status: ☐ not started · ◐ in progress · ☑ done

### W-9 ☑ OUTCOME-TRACKING DATA INTEGRITY — FULLY CLOSED (Phase 2 OHLC fix 2026-06-16)

**Blocks:** W-6 T2 legibility polish, W-3 backtest, publication track record, auditor coach vision, agent model-optimization A/B testing. No dashboard number is trustworthy until this is resolved.

#### What was caught

W-6 T1 deployed and the now-visible trade table immediately exposed two data integrity bugs more serious than the display bugs that preceded them:

**Bug A — PHANTOM LOSSES (untriggered trades logged as CLOSED_LOSS)**
2026-06-07 session: logged SHORT / APPROVED / CLOSED_LOSS / −1.0R. The trade **never triggered** — price went sideways all session and never hit the $60,508 entry. An un-triggered setup cannot be a loss. It should be `NO_FILL` / `EXPIRED` / `NO_TRIGGER` with `realized_pnl = null`. The ledger closing engine apparently closed it as a loss despite no fill.

**Bug B — BINARY ±1R ONLY (true R not recorded)**
Every outcome in the table is exactly +1.0R or −1.0R — win (hit T1) or loss (hit stop). A trade that ran to T2 (+1.618R) or T3 (+2.618R) is logged as +1.0R. The true R achieved is never captured. Winners are systematically understated.

#### Consequence

The entire track record — KPI net R, win rate, cumulative chart, accuracy bars, CRO RAG memory bank — is built on these labels and is therefore **untrustworthy** until verified. Phantom losses penalize sessions where the system was correct but price never engaged. Binary R understates the value of multi-target winners. This is the single-source-of-truth-for-PnL problem made concrete.

**W-6 T1 headline finding must be revised:** "Data is TRUSTWORTHY" (the original W-6 audit conclusion) is now in question. The display was wrong; the underlying data may also be wrong. Treat as unverified until the read-only pass confirms scope.

#### ROOT CAUSE — VERIFIED 2026-06-10

Read-only pass complete. Root cause is confirmed and fully scoped.

**`ledger_closing_engine.py` has no entry-fill check.** The engine queries every `APPROVED / closed_at IS NULL` `CampaignLog` row and immediately begins monitoring live price against `stop_loss` and `t1`. It never asks whether price reached `entry_price`. Every APPROVED record is treated as an open live position from the moment of creation.

**Exact mechanism for Jun7 phantom loss:** MAS approved SHORT at $60,508 (breakdown trigger) / stop $62,120 (breakout trigger). Price on Jun7 went sideways — never broke below $60,508, so the short was never entered. The ledger engine kept the record open and watched live price. Days later, when price rallied through $62,120, the engine's `live_price >= campaign.stop_loss` condition fired → `CLOSED_LOSS / −1.0R`. A stop-out was scored on a trade that was never entered.

**Binary-R confirmed:** `pnl` is hardcoded `1.0` or `-1.0` at the moment of close ([ledger_closing_engine.py:61-83](ledger_closing_engine.py#L61-L83)). The engine reads `campaign.t1` but never reads `campaign.t2` or `campaign.t3`. No branch for `+1.618R` or `+2.618R` exists.

**W-9 and W-11 confirmed independent:** Different tables, different write paths. W-9 = `CampaignLog` / ledger closing engine. W-11 = `DecisionJournal` / radar contamination. Fix separately.

#### Design questions — ANSWERED 2026-06-10

**(a) Trade expiry — when does an untriggered setup expire?**
Owner's answer (2026-06-07): end of the NY Futures session (8:30 AM – ~3:00 PM ET), NOT rolling into London/Asia. A trigger that hasn't fired by session close is `EXPIRED`, not a loss and not carried forward.

**(b) True R measurement — how to record actual R achieved?**
Correct model: +1.0R (T1 hit), +1.618R (T2 hit), +2.618R (T3 hit), −1.0R (stop hit), 0.0R (expired/no fill).

**(c) Entry-fill detection — RESOLVED: real-time observation, not OHLC lookback.**
The existing engine polls live price every 60 seconds. The lifecycle monitor uses that same poll: if it observes `live_price >= entry_price` (LONG) or `live_price <= entry_price` (SHORT) during the session window, it marks the setup as entered and starts the stop/target phase. No OHLC history call needed — the monitor watches the crossing happen live. Caveat: if the server is down during the session window, fills can be missed; this is acceptable for current scale.

#### ARCHITECTURE — TRADE-LIFECYCLE MONITOR (owner framing 2026-06-10)

**This is not a patch. It is a real build.**

The engine must model a trade the way a trader does. A setup has three phases; the monitor tracks all three:

**Phase 1 — Pre-entry: watching for trigger**
A trade does not exist until the entry trigger actually fires. Price CAN wander in both directions first — the canonical example: price between triggers, breaks UP through breakout (no LONG entered — short side was the approved direction), rejects, then breaks DOWN through breakdown. The short only activates on the breakdown cross, which may happen hours after setup creation or not at all. If the NY session closes and entry was never crossed → `EXPIRED`, `realized_pnl = null`, `closed_at = session_end`.

**Phase 2 — In-trade: watching stop + all three targets**
Once entry is confirmed (price crossed `entry_price` during the session window), begin tracking `stop_loss`, `t1`, `t2`, `t3`. Record each target reached as it happens. Track the high-water mark — the furthest target price touched.

**Phase 3 — Post-close data capture (the target-optimization foundation)**
Even when the trade is exited at T1 (safe target), the monitor **keeps watching and logs** whether price subsequently reached T2 and T3. This is not just a label fix — it is the data foundation for future target-optimization: "system called T1, but price reached T3 on 80% of those sessions → conservative exit policy is leaving significant R on the table." Without this persistent observation, that pattern is invisible. Do not skip this phase.

**What "monitoring in the background" means architecturally:**
- A background asyncio loop (like the existing ledger engine) — NOT page-load-triggered, NOT a recompute on every dashboard refresh
- Session-window awareness: knows the NY session closes at ~3:00 PM ET; uses that boundary to expire untriggered setups (pull from `session_manager.py` session definitions)
- Per-`CampaignLog` row state machine: `PENDING_ENTRY` → `ACTIVE` → `CLOSED_WIN/LOSS` or `EXPIRED`
- New columns needed: `entry_filled_at` (timestamp when entry cross was observed), `max_target_reached` (highest R target touched, even post-exit), `t2_reached` / `t3_reached` (bool, for target-optimization query)

#### Protocol (DO NOT skip steps)

1. ~~Read-only verification pass.~~ **DONE 2026-06-10.** Root cause confirmed above.
2. ~~Resolve design questions (a)/(b)/(c).~~ **DONE 2026-06-10.** See above.
3. ~~**Schema additions**~~ — **COMMITTED 2026-06-10 (commit `9ec43b1`).** Five columns added to `CampaignLog` + five `ALTER TABLE` blocks in `init_db()`: `entry_filled_at` (TIMESTAMP nullable), `session_expires_at` (TIMESTAMP nullable), `max_target_reached` (VARCHAR nullable), `t2_reached` (BOOLEAN DEFAULT FALSE), `t3_reached` (BOOLEAN DEFAULT FALSE). Also noted: `activated_at` exists as a dead orphaned column (never read/written anywhere) — left untouched. `status` is plain VARCHAR with no DB constraint — `EXPIRED` is a valid value without any schema change. Pushed to Render. **✓ GATE CLEARED 2026-06-14** — all 5 columns confirmed live on production Postgres (`SELECT column_name FROM information_schema.columns WHERE table_name='campaign_logs'` returned 5/5 rows). App booted clean.
4. ~~**Build the lifecycle monitor**~~ — **DONE 2026-06-11 (commit `cc49904`).** `ledger_closing_engine.py` replaced with three-phase state machine. Phase 1 entry-fill check is airtight: `entry_filled_at IS NULL` + `session_expires_at IS NOT NULL` guard ensures no APPROVED record reaches stop/target evaluation without a confirmed fill. Legacy rows (all null `session_expires_at`) are untouched until Step 5 backfill.
5. ~~**Validate full dataset**~~ — **COMPLETE-BY-PRIOR-CLEANUP (verified 2026-06-14).** No action required. Four production queries run against Postgres confirmed the dataset is already in the state Step 5 was meant to produce:

   **5a — `session_expires_at` backfill:** Q1 found 6 canonical APPROVED rows with `session_expires_at IS NULL` (the 6 real approved trades written pre-monitor: IDs 74, 79, 80, 84, 86, 89). Q2 found **0** of those are still open + un-filled → the 5a backfill has nothing to act on. All 6 are already resolved (WIN / LOSS / EXPIRED) by the earlier canonical separation + phantom correction work. Q3 (open rows confirmed) = **0**.

   **5b — phantom-CLOSED_LOSS correction:** Q4 hunted for canonical APPROVED rows still stamped `CLOSED_LOSS` with no confirmed fill (`entry_filled_at IS NULL`). Result = **0**. IDs 86 and 89 were the phantom losses; both were already reclassified to `EXPIRED` in the prior `/admin/correct-phantoms` cleanup. No stragglers remain.

   *Why it needed no action:* the phantom correction and canonical separation done before the monitor was built happened to resolve every row that Step 5 would have touched. The queries are the evidence that closes the item.

- **Status:** ☑ Steps 1–5 DONE + Phase 2 OHLC fix shipped 2026-06-16. W-9 is fully closed. Phase 1 (unfilled → EXPIRED at 3 PM) confirmed correct and unchanged. Phase 2 (filled trade expiry-override bug) resolved: filled trades now run until stop/T1 on 1m Kraken OHLCV or next-session-open, never clock-EXPIRED at 3 PM. id=94 confirmed CLOSED_LOSS at 22:26 UTC via MEXC 1m scan; manual correction staged. SF-6 Rule 4 documents the invariant. Passive Phase 1 forward verification (no-fill APPROVED → EXPIRED) still valid but no longer the only remaining item — the Phase 2 fix is the structural close.
- **Blocks:** W-6 T2 (legibility polish is pointless on wrong numbers), W-3 backtest, publication track record, auditor RAG memory bank reliability, **agent model-optimization A/B testing** (Suggestion Box 2026-06-10 — cannot measure model quality until outcome data is trustworthy).
- **Does NOT block:** daily session monitoring, A3 live watch, exit_warning observation.
- **MD-refactor gate (same session):** `mtf_interpreter` is wired to load from `agents/mtf_interpreter.md` and diff-verified character-identical. Python constant `MTF_INTERPRETER_SYSTEM_PROMPT` must NOT be deleted until a live NY session confirms identical output in `/admin/interpreter-log`. Both gates (schema + mtf_interpreter validation) clear independently — neither blocks the other.

#### Monitor Validation Fixture — LIVE TEST CASE (pinned 2026-06-10)

Concrete real-world case for validating Step 4 (the lifecycle monitor) once built. This is the live twin of the Jun-7 phantom loss.

**Setup:** SHORT APPROVED, 2026-06-10 NY Futures session.
- Entry / breakdown trigger: **$61,039.90**
- Stop (breakout trigger): **$61,922.70**
- T1: $60,157.10 | T2: $59,611.50 | T3: $58,728.60
- At 2:22 PM CST snapshot: live price ~$61,710 — between entry and stop, drifted **up** toward the stop; entry NEVER triggered (price never reached $61,039).
- Brief's own stand-down line: "reclaims $61,500 on 15M close." Price was above $61,500 at snapshot → setup was already compromised by the brief's own terms.

**Correct monitor outcomes to confirm at session close:**

**(a) Price never hit $61,039 by NY session expiry:** → `EXPIRED` / `realized_pnl = null`. NOT a loss.

**(b) CANONICAL PHANTOM-LOSS TRAP — price hit the stop $61,922 WITHOUT first hitting entry $61,039:** → still `EXPIRED` / `realized_pnl = null`. NOT `CLOSED_LOSS −1R`. The current engine gets this exactly wrong: it sees `live_price >= stop_loss` and fires `CLOSED_LOSS` regardless of whether entry was ever crossed. The lifecycle monitor must check `entry_filled_at IS NOT NULL` before entering the stop/target evaluation phase. If `entry_filled_at` is null and price hits the stop → `EXPIRED`, close the record, `pnl = null`.

**(c) Price dropped through $61,039 first:** → trade went live (`entry_filled_at` populated), real outcome follows normal stop/target logic.

**ACTION:** Confirm the actual session-close price path and record the outcome below. This locked answer becomes the regression test for Step 4 — "did the monitor correctly score 2026-06-10?"

> **Session-close outcome — CONFIRMED 2026-06-11:** Price never reached entry $61,039.88. The current engine logged the setup as **CLOSED_LOSS / −1.0R** — a confirmed phantom loss (second after Jun-7). Scenario (b) is the actual case: price drifted up toward the stop region without ever triggering the short. Correct lifecycle-monitor outcome: **EXPIRED / realized_pnl = null**. This is the locked before/after regression test for W-9 Step 4. Additional impact: today's published newsletter Performance Ledger reported "−1.00R most recent, 2 losses" — the bug is already corrupting the published track record visible to readers.

---

#### Agent → Model inventory (pinned here — Suggestion Box 2026-06-10)

Read-only pass also completed the model-assignment inventory requested by the Suggestion Box pin. All nine LLM agents use `claude-sonnet-4-6` via the single `_MODEL` constant in `agent_core.py:26` — no overrides anywhere.

| Agent | max_tokens | Optimization candidate |
|---|---|---|
| `senior_analyst` | 4096 | UP → Opus 4.8 / Fable 5 — the trade decision; highest stakes |
| `junior_analyst` | 500 | UP secondary — synthesizes interpreters for SA |
| `mtf_interpreter` | 600 | DOWN → Haiku — mechanical digest, tight budget |
| `gravity_interpreter` | 600 | DOWN → Haiku — mechanical digest, tight budget |
| `intel_auditor` | 1024 | DOWN → Haiku — structured audit, no judgment |
| `publisher_agent` | 6000 | Monitor — narrative, quality-sensitive |
| `performance_auditor` | 600 | Monitor — weekly, not latency-critical |
| `elliott_wave_specialist` | 1024 | Monitor — weekly, not latency-critical |
| `senior_analyst_commlink` | 512 | Monitor — reactive Q&A |
| `jewel_specialist` | — | **No LLM** (pure Python extraction) |

**Gate:** re-assignment and A/B testing require clean outcome data to measure against. W-9 thus unblocks both the track record AND the model-optimization question simultaneously.

---

### W-10 ☐ AUDIT OUTPUT SURFACING + NAVIGATION — BLOCKING (2026-06-07)

**What:** The weekly scheduler fired successfully on 2026-06-07 23:00 UTC (first scheduled run). Both Performance Auditor ($0.0137) and Elliott Wave Specialist ($0.0183) completed with status SUCCESS. The Elliott Wave output is known (BEAR_WAVE_4_BOUNCE / IN_PROGRESS / 13.7%) because it's visible in the cost log. The Performance Auditor produced ~600 tokens of findings and calibration recommendations. **Neither output is reachable by the owner.** The interpreter-log admin page shows only MTF Interpreter, Gravity Interpreter, and Junior Analyst — it does not surface the auditor or wave specialist outputs. A successful-but-invisible audit is functionally the same as one that never ran.

**Two gaps:**

**(1) Output persistence + display.** Verify where the auditor output was written. The code writes to `SystemAuditLog` — confirm the row exists on Render (the local SQLite doesn't have this table). Then surface it: the dashboard "Internal System Audits" collapsible section (`/api/dashboard/audits`) is supposed to display `SystemAuditLog` — confirm whether it now shows "1 report" or still "0 reports." Same for the Elliott Wave output (`MacroNarrativeLog` where `authored_by='elliott_wave_specialist'`). If the rows exist but the dashboard section is broken or unreachable, that's the fix. If the rows don't exist (write failed silently), that's a different bug.

**(2) Navigation.** The interpreter-log page (`/admin/interpreter-log`), audit page, and wave specialist output are currently bookmark-only — not linked from any menu. Owner cannot reach them during or after a live audit without knowing the direct URL. This was "polish" before tonight; it is **blocking** now that the system produced output the owner needs to read. A feature that ran and cannot be found is not a working feature.

**Scope of fix:**
- Step 1: Read-only — confirm what `SystemAuditLog` + `MacroNarrativeLog` contain on Render for tonight's run. Check `/api/dashboard/audits` response.
- Step 2: If data exists but isn't displayed — fix the dashboard audits section render.
- Step 3: If data is missing — trace the write path in `performance_auditor.py` and `elliott_wave_specialist.py`.
- Step 4: Navigation — add links to `/admin/interpreter-log`, audit view, and wave specialist output to the admin menu or dashboard so they're reachable in one click.

**Connects to:** W-9 (outcome integrity) — both are "the system must be legible and its data trustworthy before building forward." Also connects to the Suggestion Box "audit tooling as permanent site feature" pin (2026-06-06).

**Status update (2026-06-07):** Gap 1 (output display) PARTIALLY RESOLVED — auditor output IS visible in the production dashboard's "Internal System Audits" collapsible. Elliott Wave reasoning still has no dedicated view; only the gravity-map Wave Context panel surfaces the label. Additional bug found: auditor output TRUNCATED mid-sentence (hit 600-token `max_tokens` ceiling — last word was "This isol—"). Quick fix: raise `max_tokens` in `performance_auditor.py` `_call_agent()` call (600 → 900 is sufficient; prompt targets ~300 words = ~400 tokens, leaving headroom). Navigation gap remains open.

**Remaining scope:**
- Raise auditor `max_tokens` 600 → 900 (one-line fix, high priority — next audit is 7 days away)
- Surface Elliott Wave `wave_reasoning` text in a readable view (gravity-map panel or dedicated admin page)
- Navigation: add menu links to interpreter-log, audit view, wave reasoning

- **Status:** ◐ Partially resolved. Auditor output visible. Three items remain (token limit, wave reasoning view, navigation).
- **Blocks:** complete weekly audit review; future audits will keep truncating until token limit is raised.
- **Priority:** Token limit fix is a quick win before next Sunday.

---

### W-11 ☐ AUDITOR DATASET CONTAMINATION + DecisionJournal DATA-MODEL FLAW (reclassified 2026-06-07)

**Original finding:** "92% UNKNOWN kinematic_grade — pipeline bug." **RESOLVED via verify-first (2026-06-07): NOT a pipeline bug. Kinematic pipeline works correctly on real sessions.** This is the 4th time verify-first caught a false lead before a wasted fix (BBWP data-path, allocation rule, PMARP fix-as-trap, now this).

#### What verification found

Two separate writers feed `DecisionJournal` with no clean distinguisher:

| Writer | Trigger | `decision_type` | `kinematic_grade` |
|--------|---------|-----------------|-------------------|
| `kabroda_mas_flow._inject_brief_to_database()` | Once per session after MAS completes | `MAS_APPROVED` / `MAS_REJECTED` | **Always set** — from `fuel_gauge["15M_JEWEL"]["kinematic_grade"]` |
| `market_radar.scan_sector()` | Every `POST /api/radar/scan` — fires on each Market Radar page open/refresh | `STAND_DOWN` / `GRADE_A` / `GRADE_B` | **Never set** — absent from the constructor call by design |

The auditor queries ALL rows unfiltered. ~86 radar page-view events + ~7 real MAS sessions = 93 "calls." The 86 radar rows have `kinematic_grade = NULL`, which `d.kinematic_grade or "UNKNOWN"` converts to `"UNKNOWN"` at `performance_auditor.py:165`. The 7 MAS rows have real grades. Pipeline is fine; the denominator is wrong.

#### The real bug: auditor analyzes a contaminated dataset

**Every number the auditor produced tonight is mostly measuring radar page-views, not trade decisions:**
- "93 resolved directional calls" → ~86 radar scans + ~7 real sessions
- "92% UNKNOWN kinematic_grade" → 86 grade-less radar rows / 93 total
- "38 STAND_DOWN fires, 70.3% accurate" → radar's per-scan STAND_DOWN grades, not the MAS gate
- "17.4% accuracy on UNKNOWN" → accuracy of radar scan events, not meaningful as calibration

The accuracy stats are not yet valid. The auditor cannot calibrate Kabroda's decision quality until it looks at the right rows.

#### The root design flaw: DecisionJournal has no source field

`market_radar` and `kabroda_mas_flow` both write to the same table with no column distinguishing monitoring-page events from real trade decisions. The only difference is `decision_type` values (`MAS_APPROVED`/`MAS_REJECTED` vs. `STAND_DOWN`/`GRADE_A`/`GRADE_B`) — but the radar also writes `STAND_DOWN`, so filtering on `decision_type` is not sufficient to separate them cleanly.

#### Fix scope

**Part 1 — Auditor query (low blast radius, high impact):** Filter `DecisionJournal` to MAS-flow rows only. Cleanest option: add a `source` column (`"mas_flow"` vs. `"market_radar"`) to `DecisionJournal`, set it in both writers, filter the auditor query on `source = "mas_flow"`. Alternative (no schema change): filter `decision_type.in_(["MAS_APPROVED", "MAS_REJECTED"])` — this misses MAS stand-downs but is a safe starting point. The radar stand-downs are a separate metric and should be analyzed separately if at all.

**Part 2 — Stand-down analysis:** Once the auditor only sees MAS rows, the stand-down analysis should count rows where the system would have issued a stand-down verdict (e.g. `MAS_REJECTED` rows, or rows from the pre-MAS gate path). Currently the stand-down signal comes from the radar's `STAND_DOWN` labels, which is a different system entirely.

**Sequencing:** Part 1 can be built standalone (one query filter change + optionally a schema column). Do not build Part 2 until the auditor's base query is clean and a week of real data has accumulated.

#### 4-value decision_type tagging — PRIORITY BLOCKER for stand-down accuracy (2026-06-10)

**Verified during W-11 filter design:** `_inject_decision_journal` collapses all non-APPROVED MAS outcomes into `"MAS_REJECTED"` via a binary ternary. `REJECTED`, `STAND_DOWN`, and `WAITING_FOR_15M` are all written identically.

**W-11 filter applied 2026-06-10** (`IN ('MAS_APPROVED', 'MAS_REJECTED')`) correctly excludes all radar contamination — auditor now analyzes real MAS decisions only. **Side effect:** the auditor's stand-down-validation block (Block C) now reports 0. It counted rows where `decision_type == "STAND_DOWN"` — that was the radar's value, now filtered out. Real MAS stand-downs are tagged `MAS_REJECTED` and indistinguishable from real rejections. The 0 is honest (correct behavior — honest 0 beats the contaminated 70.3% it replaced), but it means stand-down accuracy cannot be computed at all.

**Stand-down accuracy is the owner's most-wanted metric** — "was the no-trade call right?" This metric is now the direct blocker between the current state and that answer.

**Fix:** change `_inject_decision_journal` in `kabroda_mas_flow.py` to write the actual `approval_status` value (4 values: `MAS_APPROVED` / `MAS_REJECTED` / `MAS_STAND_DOWN` / `MAS_WAITING`) instead of the binary. `decision_type` is a plain VARCHAR with no constraint — no migration needed. Old rows keep their binary labels; new rows get the 4-value label. Update the auditor query to `IN ('MAS_APPROVED', 'MAS_REJECTED', 'MAS_STAND_DOWN', 'MAS_WAITING')` at the same time. Update Block C filter from `d.decision_type == "STAND_DOWN"` to `d.decision_type == "MAS_STAND_DOWN"`.

**Sequencing:** do after W-9 (need clean outcome data first), before the big gated builds. Small change — one write site, one query filter, one Block C line.

- **Status:** ☑ DONE (2026-06-13). All six steps shipped. Auditor now sees only MAS rows via `source == "mas_flow"`; stand-down accuracy computable via `MAS_STAND_DOWN`; radar contamination eliminated. Pre-W-11 historical rows preserved via backfill.
- **Priority:** ~~High~~ — resolved.
- **Blocks:** ~~all auditor accuracy analysis, stand-down calibration, kinematic-grade calibration~~.
- **Does NOT block:** daily sessions, A3 live watch, W-9 outcome integrity work.

---

### W-13 ☐ RADAR DecisionJournal WRITE — session_id gap (needs-decision, not a bug) (2026-06-13)

`market_radar.scan_sector()` writes to `DecisionJournal` without `session_id` — the join key added in commit `4e82934` (Job 2 Phase A item 1). The MAS write (`_inject_decision_journal`) carries `session_id`; the radar write does not.

**Open question:** should radar rows carry the join key at all? Radar rows are per-page-load monitoring events, not session decisions. The join triple `(symbol, session_date, session_id)` was designed to link MAS decisions to their `InterpreterLog` and `CampaignLog` rows — radar rows have no corresponding `InterpreterLog` or `CampaignLog` entry. Adding `session_id` to radar rows would populate the column with whatever session is active at scan time, which is a different semantic than the MAS join key.

**Not bundled into W-11** — this is a deliberate design question, not an oversight. Decide separately.

- **Status:** ☐ Needs decision before any code change. Raised during W-11 pre-work (2026-06-13).
- **Priority:** Low — no existing feature reads `session_id` from radar rows. Relevant only if a future backtest or audit joins radar rows to session context.

---

### W-12 ☑ MAS SCHEDULER AUTONOMY — CLOSED, no action needed until W-4 (2026-06-13)

**Question:** Does the daily MAS run fire on the autonomous 14:00 UTC scheduler, or is the real trigger a page-visit?

**Observation:** Today's MAS run (`|| DECISION JOURNAL || BTC/USDT | MAS_REJECTED` at 13:55:36) fired 12 seconds after `GET /suite/radar + POST /api/radar/scan` at 13:55:24 — 4 minutes before the scheduled 14:00 UTC fire time.

#### Trigger chain trace (read-only, no changes)

**Path A — Page-triggered (what happened today):**
1. User visits `/suite/radar` → JS fires `POST /api/radar/scan` on page load
2. → `market_radar.scan_sector()` (`market_radar.py:60`)
3. → `battlebox_pipeline.get_live_battlebox("BTCUSDT", "MANUAL", manual_id="us_ny_futures")`
4. → No `SessionLock` for today → creates lock → `asyncio.create_task(run_mas_analysis(...))` (`battlebox_pipeline.py:556`)
5. MAS fires immediately — the 14:00 UTC fire is preempted

**Path B — Scheduler (14:00 UTC daily, `main.py:194`):**
1. `run_senior_analyst_scheduler()` wakes at 14:00 UTC
2. → `_fire_senior_analyst(date_key)` (`main.py:104`)
3. → Checks `MacroNarrativeLog` — if already written (Path A ran): skips entirely (`main.py:119-124`)
4. → If no narrative: calls `get_live_battlebox()` → finds existing lock → no second MAS fire
5. Restart-recovery fallback only: if lock exists but no CampaignLog PENDING → fires `run_mas_analysis()` directly (`main.py:183-188`)

**Path C — War Room page (`GET /suite/macro-war-room`, `main.py:547-573`):**
A third path: if a `CampaignLog` row has `mas_approval_status == 'PENDING'` and no `MacroNarrativeLog` exists for today, fires `run_mas_analysis()` via `asyncio.create_task()`. This is a legacy rescue path from before the scheduler existed.

#### Double-execution guard status
- `battlebox_pipeline.py:528`: if `existing_lock` found → skips `asyncio.create_task()`. Prevents double-fire from multiple `get_live_battlebox()` calls.
- `_fire_senior_analyst()` `main.py:117-124`: checks `MacroNarrativeLog` before proceeding. Prevents the scheduler from re-running after Path A succeeds.
- Both guards are effective — there is no double-brief risk.

#### Is the system autonomous?
**Yes — with a caveat.** If the owner never loads the radar page before 14:00 UTC, the scheduler fires at 14:00 UTC, creates the lock via `get_live_battlebox()`, and MAS runs unattended. The autonomous path works correctly. **The issue:** in normal use, the page-visit always wins the race, so the "14:00 UTC scheduler" is effectively the fallback, not the primary trigger. The brief timestamps will show the MAS run whenever the page was first loaded that day.

#### Publisher chain and jitter verdict (2026-06-13)
`publisher_crew.run_publisher()` is called synchronously at `kabroda_mas_flow.py:1199` — same call stack as MAS, no separate scheduler. Newsletter inherits MAS jitter. `NewsletterLog.date_key` is always correct regardless of wall-clock time. **DRAFT is the terminal state** — `publish_status` is written once as `"DRAFT"` and never promoted. No Ghost API, no email delivery, no downstream job exists. MAS-timing jitter is **irrelevant today** because nothing downstream expects a newsletter at a fixed time.

- **Status:** ☑ CLOSED — autonomous. **Updated 2026-06-15 (commit `d9a4a92`):** scheduler is now the PRIMARY lock-time trigger; fires at `lock_end_ts` (DST-aware, ~9:30 AM ET) via `_seconds_until_lock_end()`. Page-visit is now the concurrent fallback. Reopen when W-4 (Ghost/delivery) is built.
- **Constraint for W-4:** publish step must chain off DRAFT creation (`NewsletterLog.created_at` or MAS completion), NOT a fixed UTC offset. Prior caveat ("page-visit preempts the scheduler") no longer applies — scheduler now fires at lock_end_ts and typically precedes the first page-visit.
- **Connects to:** SYSTEM_FLOW node 1A (trigger-timing design), W-4 (publication delivery).

---

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
- **Constraint (W-12):** publish step must chain off DRAFT creation (`NewsletterLog.created_at` or MAS completion callback), NOT a fixed UTC offset — MAS fire time is variable (page-visit preempts the 14:00 UTC scheduler in normal use).

### W-5 ☑ Fix auditor-wire break — DONE

### W-7 ☑ EXHAUSTION BUG FIX — ALL STEPS DONE (80b1d79 · 2026-06-04; ff60c5a · 2026-06-06; example residue fixed 0805bd4 · 2026-06-15)

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
- [x] Watch first live session post-deploy — Jun7 confirmed (A3 / ff60c5a bundled Fix 3; magnitude approach). ✓
- [x] Step 3 (CONDITION 2a direction-awareness) — SHIPPED ff60c5a (2026-06-06): magnitude approach (WEAK/DEPLETED fires; STRONG NEGATIVE excluded). Functionally equivalent to direction-relative design for problem sessions. Stale WHY THE SYSTEM STANDS DOWN example fixed 0805bd4 (2026-06-15). ✓

- **Status:** ☑ ALL STEPS CLOSED. 80b1d79 (2026-06-04) + ff60c5a (2026-06-06) + 0805bd4 (2026-06-15). Magnitude approach supersedes direction-relative design note; functionally equivalent for problem sessions. Unblocks: Part 2 mean-reversion mode (Suggestion Box 2026-06-03).
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

- **A1. Wire sse_engine bias_model into `_build_senior_analyst_context`** — **CONFIRMED LIVE 2026-06-06.** Bias_model wired as third JA input. Cost log all SUCCESS; JA reconciled energy-vs-structure correctly; no false-certainty in output; SSE lean correctly stayed silent (mild/agreeing) per Version-a design. Done (c4222dd).
- **A2. Add EMA state/spread to SA fallback section** ✓ DONE (2026-06-05) — `ema_state`
  and `ema_spread_pct` were computed by `_build_jewel_reading` and in the jewel dicts
  but never rendered in the fallback block (lines 817–820). Added two lines —
  `EMA: <state> | Spread: <pct>%` — under the ADX line for both 4H and 1H. Purely
  additive. Originally scoped as "add BBWP to fallback" (finding #5) — investigation
  showed that was a false assumption; see updated finding #5 below.
  **NOT exercised Jun6** — MTF interpreter ran clean, so the fallback render path did not fire. Confirm A2 rendering when first degraded session occurs.
- **A3. MACD magnitude — VALIDATED, ready to commit/deploy (owner approved 2026-06-06)** — Verified real against
  actual code. Two-layer fix required.

  **STEP 3 VALIDATION RESULTS (2026-06-06):**

  | Session | MACD | Allocation outcome | Verdict |
  |---------|------|--------------------|---------|
  | Apr 28 (chop) | STRONG NEG | T1/T2/T3 (WITH-TREND) | WARN — NEEDS LIVE VALIDATION (1H/15M outside data window; old 'NEGATIVE' bullet confirmed gone) |
  | May 27 (chop) | WEAK NEG | T1 only [MACD:WEAK] | PASS — chop correctly restricted |
  | May 29 (trend bear) | WEAK NEG | T1 only [MACD:WEAK] | PASS — MACD genuinely weak, T1 correct |
  | Jun 1 (trend bear) | DEPLETED NEG | T1 only [MACD:DEPLETED] | PASS — MACD depleted, T1 correct |
  | Jun 2 (trend bear) | STRONG NEG | T1/T2/T3 (WITH-TREND) | **PASS** — unblocked (regression cleared) |
  | Jun 3 (trend bear) | STRONG NEG | T1 only [ExitWarn] | PASS — exit_warning fired (not MACD); owner reviewed live chart: Jun3 ran to T2/T3 but T1 was still the correct CALL — 15M was TANGLED at a structural floor (light weekly-structure level), high-conflict zone, conservative exit right; owner independently called same caution live that day |

  **Key confirmations:** Jun2 unblocked (the target regression — PASS). Old direction-blind "4H momentum NEGATIVE" bullet confirmed REMOVED from both CONDITION 2(a) and ALLOCATION RULE — deleted everywhere. Jun3 cap is via exit_warning (expected, correct); MACD correctly reads STRONG on Jun3. A3 ships as scoped — "no point correcting one day."

  **DISCOVERY (audit understated):** Two direction-blind impact points, not one:
  - **(1) CONDITION 2(a)** (known): "4H Momentum NEGATIVE" fires as sign-only →
    structurally always true in a downtrend, requiring only one of (b)/(c) to trigger
    STAND_DOWN. STRONG NEGATIVE (healthy trend) and DEPLETED NEGATIVE (exhausted) are
    indistinguishable.
  - **(2) ALLOCATION RULE** (`kabroda_mas_flow.py` ~L220–229, NEW): fires on "4H
    momentum NEGATIVE" as a **single sufficient condition** — no two-of-three gate.
    Every bearish day, even healthy trending SHORT sessions that reach APPROVED, is
    silently capped at T1-only. T2/T3 targets are computed but unreachable. Jun 5
    brief's T1-cap may have been mechanical from this rule, not purely the SA's
    divergence reasoning.

  **DATA LAYER** (`battlebox_pipeline.py` — `analyze_tf`):
  - Add `macd_hist` (rounded raw value) to return dict — for fallback render visibility.
  - Add `macd_strength` label: `STRONG` / `WEAK` / `DEPLETED`. Normalized as bps off
    ema50 (`hist / ema50 * 10000`): price-level-independent at any BTC price.
    Proposed thresholds: |bps| < 5 → DEPLETED; 5–20 → WEAK; >20 → STRONG.
    **OPEN: thresholds derived from only Jun3 (−39 bps, STRONG) and Jun1 (−2.2 bps,
    DEPLETED) — MUST validate all 7 replay sessions before finalizing. Wrong thresholds
    make it worse than the bug.**
  - Keep `momentum` sign string ("POSITIVE"/"NEGATIVE") unchanged — backward-compat
    with interpreter and jewel snapshots. Additive only.
  - Update fallback render: add `[{macd_strength}]` bracket after momentum label for
    both 4H and 1H lines.
  - Short-data path (line 275) needs `macd_hist: 0.0, macd_strength: "DEPLETED"` defaults.

  **PROMPT LAYER** (`kabroda_mas_flow.py`):
  - CONDITION 2(a): replace "4H Momentum is NEGATIVE" with "4H Momentum strength is
    WEAK or DEPLETED — histogram near-zero or fading. STRONG NEGATIVE is healthy trend
    energy, not exhaustion."
  - ALLOCATION RULE: "4H momentum NEGATIVE" → "4H momentum strength is WEAK or
    DEPLETED AND trade direction is LONG" (STRONG NEGATIVE + SHORT = confirming; STRONG
    NEGATIVE + LONG = still restricts; WEAK/DEPLETED = restricts both directions).
  - Add explicit note: "MACD strength is a FUEL/allocation signal only. Trade direction
    is determined by harmonic state and trigger position — not by MACD sign."

  **RISKS:**
  - Label collision: harmonic matrix already uses `micro_state = "EXHAUSTION"`. Use
    `DEPLETED` (not `EXHAUSTED`) to keep the two signals visually distinct in the brief.
  - Circular coupling: revised allocation rule references trade direction — SA must not
    choose direction based on MACD sign. Prompt note above guards this.

  **BUILD GATE:** Live A1+A2 confirmation first. Then:
  1. Calibrate thresholds — replay all 7 sessions, verify macd_strength labels are correct
  2. Finalize prompt text
  3. Validate: (+) Jun3 SHORT allows T2/T3; (−) Apr28/May27 chop still restricts;
     (regress) Jun2 SHORT still gets multi-target; Jun3 LONG still restricted
  4. Deploy → watch live session for correct allocation behavior

**Tier B — PARKED (see finding #2 for full re-scope)**
- **B1. PMARP direction-blind threshold** — VERIFIED + RE-SCOPED + PARKED (2026-06-06).
  Finding confirmed real but audit's proposed fix is a trap. See finding #2 for full
  detail. Do NOT build until: (a) market has ranged/rallied so the 252-bar PMARP history
  covers both sides, AND (b) the deferred exit_warning STRONG+with-trend override is
  scoped (B1 likely shares that override layer). Monitoring item only — observe whether
  PMARP ever correctly fires on an upside extreme or whether the current one-sided
  downtrend market means the signal is simply dormant. No build date set.

---

#### Findings, ranked by decision weight

**1. MACD MAGNITUDE DROP** — `battlebox_pipeline._build_fuel_gauge` / `analyze_tf` — VERIFIED 2026-06-06
- **Bug (confirmed):** `_calc_macd()` returns the full `{"macd", "signal", "hist"}` dict correctly.
  In `analyze_tf` (line 284–285): `macd_data = _calc_macd(closes)` then `momentum = "POSITIVE" if
  macd_data["hist"] > 0 else "NEGATIVE"`. `macd_data` is local — goes out of scope here. Return dict
  (line 288) contains only the string `momentum`. No secondary path: jewel_ctx snapshots also use a
  label-string `momentum` field (from MTF interpreter), not the raw hist.
- **TWO impact points (audit originally identified only one):**
  - **(1) CONDITION 2(a)** (`kabroda_mas_flow.py` lines 123–128): "4H Momentum is NEGATIVE" → sign-only
    → structurally always true in any bearish session. Jun 3 hist=−410 and Jun 1 hist=−24 both arrive as
    "NEGATIVE". Any single co-condition — (b) or (c) — is enough to force STAND_DOWN.
  - **(2) ALLOCATION RULE** (`kabroda_mas_flow.py` lines 220–229, discovered in A3 scope session):
    `"4H momentum is NEGATIVE"` is the FIRST condition in the allocation IF block — **no two-of-three
    gate, fires alone**. Every bearish day — including healthy trending SHORT sessions that earn APPROVED
    — is silently capped at T1-only. T2/T3 are computed and correct but unreachable by the operator.
    Jun 5 brief's single-target allocation may have been this mechanical cap, not purely the SA's
    divergence read.
- **Severity:** Decision-logic. Highest weight. Two separate downstream impact points. Fix scoped in A3.
- **Same class as ADX?** No. ADX gave numerically impossible values (14× reality). MACD gives the correct
  sign; the bug is magnitude suppression before two separate gate-and-allocation checks.

**2. PMARP DIRECTION-BLIND threshold** — `mtf_confluence_scanner._calc_pmarp` — VERIFIED + RE-SCOPED + PARKED (2026-06-06)
- **Bug confirmed:** `pmarp_overextended = rank > 75` fires only for upside extremes. `rank = sum(history_values <= current_ratio) / len(history) * 100`. When price is far BELOW EMA21, `current_ratio` is very negative → rank → 0 → `rank > 75.0 = False` always in a downtrend. Jun 2 verified live: rank=0.00, pmarp_overextended=False — 0 of 252 history bars were as low as Jun2's ratio. A historically extreme downside extension is completely invisible.
- **Short-history scale inconsistency confirmed:** short path (<50 bars) returns `abs(current_ratio)` (raw %, e.g. 4.03); full path returns percentile rank 0–100 (e.g. 0.00). Same field, incompatible scales. Low practical impact at 280 4H candles, but a real inconsistency.
- **Blast radius confirmed (5 layers, 3 agents):** `_calc_pmarp` → per-TF `pmarp_overextended` → `_build_jewel_signal` OR → `JewelSnapshotLog.jewel_exit_warning` → `_build_jewel_ctx` renders "!! EXIT WARNING: PMARP overextended" → MTF interpreter (overnight JEWEL history) + SA context (JEWEL block + per-TF PMARP table) + SA ALLOCATION RULE ("jewel_exit_warning is active → T1 only"). Not a one-file change.
- **CRITICAL — the proposed fix (rank < 25) is a trap:** Verified across 8 sessions — EVERY session Apr28 through Jun6 reads rank < 25 on the 4H. The 252-bar 4H lookback (~42 days) is entirely inside the current downtrend; the 2025 bull-run prices rolled off the window. Naive rank < 25 → `pmarp_overextended=True` every session → `jewel_exit_warning=True` every session → T1 cap on every approved trade — an always-on direction-blind veto, exactly the class A3 just removed from MACD. Even rank < 5 still fires on Jun2/Jun3 (the A3-unblocked sessions), so any threshold requires a STRONG+with-trend override to not re-cap them. That override is the same one pinned-but-deferred for exit_warning.
- **Session-by-session PMARP rank table (4H, all 8 sessions):**

  | Session | Close | EMA21 | Ratio | Rank | OE_up (>75) | OE_dn (<25) |
  |---------|-------|-------|-------|------|------------|------------|
  | Apr 28 (chop) | 76,230 | 77,414 | -1.53% | 15.0 | False | True |
  | May 27 (chop) | 75,759 | 76,514 | -0.99% | 19.4 | False | True |
  | May 29 (trend) | 73,473 | 74,554 | -1.45% | 14.3 | False | True |
  | Jun 1 (trend) | 72,474 | 73,706 | -1.67% | 8.7 | False | True |
  | Jun 2 (trend) | 69,466 | 72,382 | -4.03% | 0.0 | False | True |
  | Jun 3 (trend) | 67,060 | 69,994 | -4.19% | 2.0 | False | True |
  | Jun 5 (approved) | 61,990 | 65,568 | -5.46% | 3.2 | False | True |
  | Jun 6 (SD) | 60,990 | 63,728 | -4.30% | 7.9 | False | True |

- **Full fix scope (A3-class, not a one-liner):** Requires (1) threshold decision (rank < 25 too wide, even rank < 5 fires on Jun2/Jun3), (2) direction-aware allocation override — "downside extreme on a SHORT is mean-reversion risk, same as STRONG+with-trend override for exit_warning; downside extreme on a LONG is a different signal entirely", (3) MTF interpreter prompt change to distinguish "PMARP BELOW extreme on a short" from "PMARP BELOW extreme on a long" — `pmarp_direction` (ABOVE/BELOW) is already in the rendered context but the interpreter has no instruction to interpret it directionally.
- **Data currently unfit for threshold calibration:** PMARP history is one-sided (all-below-mean). No threshold can be honestly calibrated until the market ranges/rallies and the 252-bar window covers both sides of EMA21.
- **Severity:** Real gap, but effect is dormant in the current one-sided downtrend (upside extreme never fires either — `rank > 75` also hasn't fired because prices have been falling). The signal is structurally silent in both directions in this market regime.
- **Same class as ADX?** Reclassified: NOT the same. ADX was numerically impossible (14× reality), one-character fix, no design decision. PMARP requires a threshold decision, a direction-aware override layer, and a prompt change — and the data is currently unfit for calibration. Different scope class entirely.
- **Status: PARKED as monitoring + design item.** Build gate: (a) market ranges/rallies → balanced PMARP history, AND (b) exit_warning STRONG+with-trend override is scoped (B1 shares that layer). Verify-first protocol confirmed its value a 3rd time: the audit's "simple symmetric fix" would have re-broken A3.

**3. SSE bias_model SILENTLY DROPPED** — `sse_engine` / `kabroda_mas_flow._build_senior_analyst_context`
- **Bug:** `sse_engine.compute_sse_levels` produces a `bias_model.daily_lean` dict containing direction (long/short/neutral), score, and confidence — derived from slope (daily SMA20/SMA50), VRVP opening location (above/below/in value area), and trigger asymmetry (distance to BO vs BD). This is stored in `packet["bias_model"]` and is visible in the battlebox JSON. But `_build_senior_analyst_context` never receives `bias_model` as a parameter — the function signature takes `levels` and `context` only. The SSE's quantitative direction signal is **computed and discarded**. It is a wire that was never connected.
- **Severity:** Flow-through gap. Moderate weight — the signal incorporates real structural information (VRVP positioning, trigger asymmetry) that the SA currently cannot access.
- **Same class as ADX?** No — this is a routing gap, not a wrong-value bug.

**4. VRVP zero-volume silent degradation** — `sse_engine._calculate_vrvp`
- **Bug:** If `total_volume=0` across all VRVP input candles, `target = 0 * 0.70 = 0`. The value-area expansion loop exits immediately (`curr < target` = `0.0 < 0.0` = False). Result: `POC = VAH = VAL = min_price`. The trigger logic degrades gracefully — BO falls back to R30H, BD to R30L — but no warning is logged, no error raised. Failure is entirely silent.
- **Fix:** Log a warning when total_volume=0 after VRVP computation so the issue is visible in Render logs.
- **Severity:** Medium. Unlikely on MEXC (always has volume), but a silent correctness gap.
- **Same class as ADX?** No.

**5. BBWP silent absence in fallback** — INVESTIGATED + LARGELY FALSE (2026-06-05)
- **Original claim:** BBWP absent from fallback when `mtf_read=None`; fallback only covers RSI/MACD/ADX/kinematic_grade.
- **Investigation result:** The audit's assumed data path (`fuel_gauge["4H"]["bbwp_value"]`) was wrong. BBWP reaches the SA via the `jewel_ctx` history block, which is present in both the interpreted and fallback paths. It is NOT absent in the fallback — it arrives via a different route than assumed.
- **Real gap found instead:** `ema_state` and `ema_spread_pct` ARE in the jewel dicts (output of `_build_jewel_reading`) but were never rendered in the fallback block. Fixed in A2 (2026-06-05): two new lines added under the 4H and 1H ADX lines.
- **Lesson:** Verify data-path assumptions against actual code before treating an absence as a confirmed gap. The audit was wrong once here.

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

- **Status:** ◐ A1 done + **CONFIRMED LIVE 2026-06-06** (c4222dd). A2 done (b5b928d), not yet exercised. **A3 COMMITTED ff60c5a (2026-06-06) — CONFIRMED LIVE 2026-06-07.** Jun7 session: APPROVED SHORT, positive momentum + bearish trend, T1-only for structural reason ($60,025.76 wall), all 5 agents SUCCESS. Tier B re-scoped: B1 PARKED (see finding #2).
- **Next action:** W-6 T1+T2 fix pass (A3 confirmed, proceed). B1 monitoring-only; no build until market ranges and PMARP history is balanced.
- **Sequencing:** A3 deployed. B1 parked (data unfit, design question open). W-6 (dashboard audit) next build session when ready. Gravity expansion after front-of-river fully connected.
- **Blocks:** W-3 backtest validity (pointless to replay a starved SA). Gravity expansion (downstream).
- **Audit note:** Verify-first protocol confirmed its value three times: (1) finding #5 BBWP was a false assumption; (2) A3 scope found the allocation rule impact that the audit missed; (3) B1 re-scope found that the audit's proposed fix would have re-broken A3. Audit findings are leads, not confirmed fixes. Always verify against actual code and live data before building.

---

### W-6 ◐ DASHBOARD AUDIT — READ-ONLY COMPLETE (2026-06-06); fix pass next

#### Headline finding

**~~Data is TRUSTWORTHY~~ — REVISED 2026-06-07 (see W-9).** Original finding: display bugs only, underlying data correct. Revised: T1 display fixes deployed and the now-visible trade table exposed data integrity bugs in the outcome-tracking layer (phantom losses on untriggered trades; binary ±1R instead of true R). "Trustworthy data" must be treated as unverified until W-9 read-only pass confirms scope. **W-6 T2 legibility polish is BLOCKED on W-9.**

The alarming numbers (−6R chart, "Error/Other" largest slice, "80% incorrect" accuracy bar, trade table stuck) are NOT evidence of a broken system. Three of four "problems" are display artifacts or data gaps. Owner was confused and alarmed even knowing the system — if it confuses the builder, it misrepresents the system to anyone. LEGIBILITY is the real problem, not just the 2 bugs.

---

#### Panel inventory & data-source map

| # | Panel | Source | Endpoint |
|---|-------|--------|----------|
| 1–6 | KPI cards (Total Sessions, Approved Rate, Win Rate, Net R, 7-Day Spend, Cache Hit Rate) | CampaignLog (1–4) + AgentRunLog (5–6) | `/api/dashboard/overview` |
| 7 | Cumulative Performance line chart | CampaignLog WHERE `closed_at IS NOT NULL`, sorted chronologically, cumulative +1/−1 | `/api/dashboard/mas-history` |
| 8 | MAS Approval Distribution donut | CampaignLog grouped by `mas_approval_status`; "Error/Other" = MAS_ERROR + PENDING | `/api/dashboard/mas-history` |
| 9–10 | Directional Accuracy by Kinematic Grade + by Confluence Score bars | DecisionJournal WHERE `outcome_direction_correct IS NOT NULL AND kinematic_grade / confluence_score IS NOT NULL` | `/api/dashboard/accuracy` |
| 11 | Agent Cost 7-Day Stack *(admin-gated)* | AgentRunLog last 7d by agent name | `/api/dashboard/costs` |
| 12 | JEWEL Gate vs Trade Outcome donut | JewelSnapshotLog (NY_OPEN) joined to CampaignLog by `date_key` | `/api/dashboard/jewel` |
| 13 | Internal System Audits *(collapsible)* | SystemAuditLog last 5 | `/api/dashboard/audits` |
| 14 | Newsletter Archive | NewsletterLog last 30 | `/api/dashboard/newsletters` |
| 15 | Trade History (Last 50) | CampaignLog last 50, all statuses | `/api/dashboard/mas-history` |

---

#### Verified bug classifications

**Bug 1 — "Loading trade history…" never populates — DISPLAY BUG (1-line fix)**
The API returns `realized_pnl` as a pre-formatted string (`"+1.0R"` for CLOSED_WIN, `"-1.0R"` for CLOSED_LOSS). The JS renderer calls `pnl.toFixed(2)` on it — strings have no `.toFixed()` → TypeError crashes the entire `d.trades.map()` call → `tbody.innerHTML` never writes → table stuck at the initial placeholder. Data is written, queried, and returned correctly — the crash is purely in the renderer. Fix: return `realized_pnl` as a float from the API and let JS format it, OR just use `t.realized_pnl` directly as the display string (it's already formatted).

**Bug 2 — "+1R KPI vs −6R chart" — DATA BUG in the CHART only (1-clause fix)**
KPI formula: `COUNT(CLOSED_WIN) − COUNT(CLOSED_LOSS)`. **KPI is correct; real track record ≈ the KPI.** Chart formula: for all rows with `closed_at IS NOT NULL`, applies `+1.0 if status='CLOSED_WIN' else -1.0`. The `else -1.0` fires on CLOSED_LOSS rows (correct) AND any row where `closed_at` was set but `status` wasn't updated atomically (error state, partial close) — each counting as −1 in the chart but 0 in the KPI. Enough of those rows produces the −6R artifact. Fix: add `if row.status in ('CLOSED_WIN', 'CLOSED_LOSS')` guard; skip rows with unexpected statuses.

Both bugs are in the same file (`main.py`) — one small commit.

**Not-bugs (verified):**
- **Accuracy bars (Grade + Confluence) — DATA GAP, self-populates.** `DecisionJournal.outcome_direction_correct` filled by outcome tracker 4H after each session. `confluence_score` confirmed written (`kabroda_mas_flow.py:1445`). Charts will populate as sessions accumulate. No fix needed.
- **JEWEL Gate donut — DATA GAP locally.** Requires NY_OPEN JewelSnapshotLog joined to same-date closed CampaignLog. Zero closed rows locally. Should work on production with enough aligned data.
- **SystemAuditLog / NewsletterLog missing locally — LOCAL SCHEMA ONLY.** Both exist on production Render. Local SQLite not re-migrated. Not a code bug.
- **"Error/Other = 11 vs Approved = 8" — REAL DATA, mostly historical pre-fix CCO parse failures.** Not a categorization bug. But: lumps PENDING (never-completed runs) with MAS_ERROR (explicit parse failures) — two different problems, indistinguishable on the dashboard. And the all-time view with no time axis means the historical scar looks identical to current reliability.

---

#### Legibility failures (beyond the 2 bugs)

Owner — who built and best understands the system — was confused and alarmed by the dashboard. An illegible/alarming dashboard is **worse than none** for validation purposes.

1. **Accuracy bars draw alarming shapes from tiny samples.** 5-session data with 2 misses produces "80% incorrect" for one confluence bucket. This is statistically empty — not a verdict. Needs sample-size guards and "insufficient data" labels so a 2-miss bar doesn't read as a calibrated signal.

2. **"Error/Other = 11" is real data (pre-fix CCO failures) but looks like current unreliability.** All-time view, no time axis. Can't distinguish current reliability from historical scar. Needs: PENDING-vs-ERROR split + time axis = "MAS reliability % trending."

3. **No hover tooltips explaining each metric** (owner's idea). "What is Net R Lifetime?" "What counts as a session?" Every metric needs a one-sentence tooltip. Owner can read the code; general readers cannot.

---

#### Design observations (owner decision required)

**Denominator problem — headline stats are flattering:**
"Total Sessions / Approved Rate / Win Rate" have three different denominators. A reader naturally multiplies them ("22 sessions × 36% approved × 57% win rate") but gets a wrong answer — Win Rate is computed only over `CLOSED_WIN + CLOSED_LOSS`, a much smaller pool. Add `(of N completed)` qualifier to the Win Rate card at minimum.

**No single source of truth for PnL — matters for W-3 and publication:**
`realized_pnl` Float column is written to the DB but never read by any dashboard query. KPI computes `wins − losses` (count). Chart cumulates `+1/−1` (count). Trade table returns `"+1.0R"` (Python-formatted string). Three representations, none derivable from the others. When a reader asks "what is the system's actual PnL?", there is no single authoritative answer in the DB. This must be resolved before W-3 backtest and before any publication track record is published.

---

#### Missing capabilities (new features — scope as T3)

1. **Stand-down accuracy panel** — "when system stood down, did price move against the vetoed bias?" — THE core health metric for a discipline-based system. Matches the SUCCESS METRIC framing above. Currently entirely absent from the dashboard. Owner has asked this verbally all week.
2. **MAS reliability % over time** — how often does the 6-agent chain complete without CCO parse failure? Currently proxied by the illegible "Error/Other" donut. Needs a dedicated trending metric.
3. **Date filters** — all metrics are lifetime, no time-window selector. Can't answer "how has performance trended since A3 deployed?"
4. **Session drill-down** — clicking a trade history row should open the full SA brief, trigger levels, CRO verdict, and conditions that fired. Currently no drill-down.
5. **Interpreter-log visibility panel** — JA + MTF + gravity interpreters are running but there's no panel showing firing frequency, cost trend, or output quality. Will matter more as that layer matures.

---

#### Build tiers

| Tier | What | Effort |
|------|------|--------|
| T1 | Bug 1 (JS `.toFixed` crash) + Bug 2 (chart clause) — one commit | ~30 min |
| T2 | Honest-numbers polish: denominator qualifiers, sample-size guards on accuracy bars, PENDING-vs-ERROR split, time axis on reliability, tooltips | Small, same template |
| T3 | Missing capabilities: stand-down accuracy panel, MAS reliability trending, date filters, session drill-down, interpreter visibility | Own scoped project |

**T1 before judging A3 live** — the trade history table being broken means we can't read historical outcomes. T2 in the same session while the template is open. T3 is its own project.

- **Status:** ◐ Read-only audit COMPLETE (2026-06-06). T1 + T2 fix pass next.

---

### W-14 ☐ STRENGTHENING PHASE — multi-timeframe + signal-conviction cluster (GATED, 2026-06-14)

A connective node for three Suggestion Box items that belong together and share the same primary gate. They were already cross-linked in the Suggestion Box ("scope jointly — they may resolve into one multi-timeframe architecture design project, not two"). This entry names the cluster and preserves that linkage.

**The three components — reference the Suggestion Box pins, do not re-state:**

- **14b — Per-TF independent engines:** see **MULTI-TIMEFRAME SSE ENGINES pin (2026-06-07)**. Hard gate reasoning, scope cautions, and handshake-protocol design problem all live there.
- **14c — Cross-week anticipation narrative:** see **HTF STRUCTURAL ANTICIPATION pin (2026-06-06)**. Elliott Wave Specialist partially covers this; the gap is time-axis path narration ahead of price arrival.
- **14a — Signal-tracking / timing-conviction tool:** extends the already-deployed Intel Auditor (`POST /api/research/audit-intel`) — see also **VET-A-TRADE pin (2026-06-07)**. See "What's genuinely new" below.

**What's genuinely new — not in any existing pin:**

*The "good-till-close trap":* a signal that is directionally correct but mistimed leaves the trader underwater — not because the setup was wrong but because the entry was premature. Signal services solve "is a setup valid?"; they do not solve "is NOW the right moment?" Kabroda's edge in this cluster is judging WHEN to act, not just WHETHER a setup exists.

*Stateful Intel Auditor extension:* the deployed Intel Auditor makes a one-shot CONFIRMED/REJECTED/HIGH_RISK call on a foreign signal. 14a adds a time dimension: carry a signal in memory across polls, monitor for TF alignment to arrive, green-light when conditions are met. That tracking loop does not exist yet. The one-shot call is real; the persistent monitoring is new.

**Gate — what this cluster actually depends on:**

Primary gate: **the 15M core proven solid across many live sessions.** A3 is 2 sessions old. W-7 Fix 3 (CONDITION 2(a) direction-awareness, SA prompt change) is still OPEN. B1/PMARP direction-blind is parked. Any bug in the 15M foundation is inherited by every per-TF engine that replicates it.

Job 2 / replay harness: **validation aid for 14b** (stress-test per-TF trigger math against history before live deployment), NOT a construction dependency for 14a or 14c.

**Per-TF trigger math — why 14b is not an interpreter extension:**

W-1's MTF Interpreter adds an interpretive layer within the existing 15M pipeline. 14b is different: it needs independent triggers per timeframe. The 30M Range (`r30_high` / `r30_low`, 8:30–9:00 AM ET calibration window) is 15M-session-specific — there is no equivalent calibration window for a 4H engine. The VRVP / VAH / VAL derivation and the trigger-distance minimums all need fresh design per TF. That is why the Suggestion Box pin calls 14b the largest project on the board, not a feature weave-in.

**Phase 3 record correction:**

W-9 Phase 3 (`ledger_closing_engine.py`) captures per-target booleans (`t2_reached`, `t3_reached`, `max_target_reached`) — raw data only. The per-target WR% stat ("T2 reached on X% of T1-exit sessions") is a future Performance Auditor query over accumulated booleans. The monitor does not generate the stat. Phase 3 has not yet fired in production (no T1 WIN observed post-cc49904); data accumulation has not started.

**Prior art / research notes** *(to be filled when this cluster graduates from GATED)*
*(placeholder — Mafioso 4H/8H signal methodology, multi-TF SSE prior art, timing-conviction model approaches)*

- **Status:** ☐ GATED. 14b: scope jointly with HTF-anticipation pin when 15M-core gate clears (Suggestion Box's own instruction). 14a: most buildable near-term sub-item (extends existing Intel Auditor infrastructure, no per-TF engine required).
- **Does NOT block anything currently.** Expansion-tier work.
- **References:** MULTI-TIMEFRAME SSE ENGINES pin (2026-06-07) · HTF STRUCTURAL ANTICIPATION pin (2026-06-06) · VET-A-TRADE pin (2026-06-07) · Intel Auditor (`POST /api/research/audit-intel`, CLAUDE.md) · SYSTEM_FLOW nodes 1C, Q3, Q4

---

## OPERATING DISCIPLINE — CONTINUOUS SESSION-EVALUATION (☑ active, 2026-06-16)

*The operating mode for the current phase. Replaces any "timed watch phase" framing.*

### Context

Core build phase is largely complete. System is fundamentally sound (W-9 integrity fixed, coherence fix live, A3 core confirmed). Current market = bear-market pullback: daily/weekly bearish, 1H/4H bullish on indicators/momentum only (not trend structure), pushing into HTF ceilings → chop in low timeframes, micro-bull moves that don't reach target (id=94, 2026-06-15 was exactly this). High stand-down rate is **correct behavior** for this regime — not a bug. When HTFs roll over and realign down to 15M, approvals + clean movement return ("trend is your friend until the end"). This is a "keep earnings in the bank" regime.

### The daily discipline (lightweight)

1. **Log the call:** APPROVED / STAND_DOWN + the brief's key reasoning in one line (including any conditional "tradeable IF X").
2. **Watch it resolve** against actual price within the **8–11 AM CST tradeable window** (NOT PM / overnight / weekends — outside owner's trading frame; see entry window definition in SYSTEM_FLOW node 1A).
3. **Evaluate honestly.** Three outcomes:
   - **(a) Correct stand-down** — no clean opportunity existed. Terse note ("stand-down, correct, chop").
   - **(b) Questionable stand-down** — a clean pullback setup the system vetoed. **PRIORITY SIGNAL — over-conservatism is the current top risk.** Flag day with detail.
   - **(c) Approval-quality** — did it resolve as the read suggested?

**Where notes live:** a dated evaluation log keyed by `date_key`. Lightweight markdown for now. DB-attachment to the session record is DEFERRED — `date_key` is the join key that makes later migration trivial if volume justifies it. **Do NOT build a notes-capture feature now — we are in a don't-tinker phase.**

### Hard-line principle

Evaluation is evidence-based, not feeling-based. "I feel / I hope / it should" = emotion = distrust. The system's value IS its black-and-white parameter discipline; the evaluation audits that discipline against reality with a trader's eye as a CHECK, never an override. Any core change must earn its way through accumulated logged evidence — never a reaction to one chop day.

### Longitudinal pattern-detection (Claude's standing job)

As the owner shares daily logs/files, conversation-Claude reads them for recurring cross-session threads and flags emerging patterns. The daily eye forgets; cross-day memory holds the signal. When a pattern is clear (e.g. "3 questionable stand-downs in 2 weeks, all clean pullback longs → ADX/MACD gate may be too tight in pullback regimes"), investigate together, THEN bring Claude Code in for a targeted, evidence-backed parameter look. **Trigger to act = pattern clarity, not trade count or calendar.** Could be weeks or months.

### Owner's personal trading frame (current operating ASSUMPTIONS — tested by the evaluation, not hard-coded)

- Morning session only; out ~2h before session close (~7 PM CST); no overnight holds; no weekends
- AM over PM (prior AM/PM audit found little PM value)
- Trades 15M with 5M execution

### Dual accountability

The weekly auditor's stand-down validation is the automated half; the daily manual eval is the human half. Both must read honestly — which is why the auditor thin-data legibility fix (W-15) is the one near-term code task paired with this discipline.

- **Status:** ☑ Operating mode for this phase. Defined plan — NOT a vague "wait and see."
- **Regime gate:** more approvals expected when daily/weekly structure realigns bearishly and HTF ceilings stop blocking upside. No arbitrary timeline.

---

### SESSION EVALUATION LOG
*(Reverse-chronological. Add new entries at the top. One-line terse for clean days; flag days get full detail. Keyed by `date_key` = session anchor date.)*

---

**2026-06-20 | APPROVED SHORT | Outcome: LOSS (−1R, stopped out) | Marginal-but-disclosed setup — system named its own failure risk accurately | Running tally: 4 correct stand-downs + 1 winning approval + 1 losing approval (disclosed-marginal, not questionable)**

*Call:* APPROVED SHORT. Entry 63,232.92 | Stop 63,778.93 | T1 62,602.87 (T1-only cap applied by system). 15M closed below entry — would have filled. Within ~3 fifteen-minute candles, the third spiked up and tagged the stop. −1R loss. Price subsequently pushed UP into the breakout trigger (opposite direction).

*Evaluation — LOSS, not a questionable approval:* The brief explicitly disclosed the setup's weakness at call time: MODERATE conviction (not STRONG), capped at T1 only, and named the exact failure mode — "both driving timeframes show POSITIVE momentum against their BEARISH trend" (live counter-trend bounce), weak ADX (4H 13.27, 1H 20.03), unresolved weekly bullish divergence. The system's own Stand-Down-If condition even named the scenario that played out: "positive momentum overwhelms the bearish setup." The stop-out was caused by exactly the disclosed risk. System did not misjudge — it took a disclosed marginal trade that lost. First loss in the evaluation record, expected and healthy. A system that never loses isn't taking trades.

*KEY WATCH HYPOTHESIS (one data point — not acted on):* Does "GATE OPEN / MODERATE conviction / counter-momentum-against-trade-direction" correlate with losses? This is one instance. If several moderate-conviction-with-counter-momentum approvals accumulate as losses, that pattern suggests the gate should stand down when driving timeframes show counter-trend momentum against the trade direction. Logged here for longitudinal tracking — no design change until the pattern is confirmed across multiple sessions.

*Owner observations (watch, don't act):*
1. **15M 200 SMA at the breakdown point** — possibly the same class of blind spot as the SSE-into-TSA gap: the system's target/gravity math may not see the 15M 200 SMA as an obstacle near the trigger. Hold loosely — one instance. If MA levels keep appearing at reversal points across sessions, join this observation to the SSE-into-TSA blind-spot item.
2. **Saturday session** — weekend = thinner liquidity, weaker trends, chop-prone. Consistent with the brief's own weak-ADX / counter-momentum read. Owner generally avoids weekend trading (personal frame). Watch: do approvals on weekend sessions underperform relative to weekday approvals? Possible future session-type filter if the pattern emerges. Not acted on.

*UI fix verified live (first approved day since eecc6ae):* HUD key now populates correctly ("SHORT | SA_APPROVED | 63232.92 | …") with no "DATA MISSING." Copy-to-HUD Tier 1 fix confirmed working on a real approved session.

*MINOR — Panel 02 label mismatch flagged:* Panel 02 shows "HIGH CONVICTION SETUP" while the brief says MODERATE conviction. Panel 02 is likely reading JEWEL conviction, not SA conviction — different source, different moment, different scale. Same display-authority class as the earlier Panel 02 / GATE OPEN divergence (GAP-1). Logged for a look; not urgent.

---

**2026-06-19 | STAND_DOWN | Outcome: CONFIRMED CORRECT (stronger: correctly declined FAKE breakout) | Brief's 15M PRIMED gate held through actual breakout trigger cross — no fuel, no follow-through | 4-session stretch: 4 correct stand-downs + 1 winning approval, zero questionable**

*Call:* STAND_DOWN. Condition for long entry: "wait for 15M TANGLED → PRIMED." Short structurally disqualified (targets collapse onto HEAVY wall at $62,296, 0.09% clearance).

*Morning structural read (unchanged from brief):*
1. **Short side structurally disqualified.** All three short targets collapse onto HEAVY wall at $62,296, only 0.09% below the breakdown trigger. Zero measured-move runway — same disqualification logic as the 06-07 and 06-17 short blocks. System is consistent.
2. **Long not primed.** 15M TANGLED (0.14% ribbon, no kinematic velocity); 4H JEWEL EXTENDED into OVERSOLD. No fuel for a breakout. Wait for 15M to prime.
3. **Weekly bullish divergence active** — owner's structural awareness layer; logged for context. Not a trade signal on its own, but flags the long side is not dead, just needs confirmation.

*Context — 06-18 trade fully resolved:* Runner from the 06-18 APPROVED SHORT held overnight between T1 (~62,883) and T2 (~62,118). Owner closed the 60% runner manually this morning in profit below T1, reading the 06-19 setup as undecisive (same 24h range, BO ~62,894 / BD ~62,354, no directional resolution). Correct read — the setup confirmed no clean direction. The 06-18 trade is a WINNING TRADE in full: 40% banked at T1 + runner closed in profit.

*End-of-day resolution — STRONGER VALIDATION THAN A PURE-CHOP STAND-DOWN:*

Price DID cross the breakout trigger (~$62,894). This was not a nothing day — the trigger fired. But price ran only to a high of ~$63,410 (~500 pts), with no kinematic fuel, then died and drifted back to ~$63,080, coiling in the 15M EMA ribbon. The 200 SMA (~$63,400, descending) capped the move as overhead resistance and price stalled beneath it. The 15M ribbon never resolved TANGLED → PRIMED — velocity stayed flat (~0.14% spread) throughout.

This is exactly the "false breakout into a stalled execution engine — not a trade" scenario the brief's condition explicitly gated against. The system stood down through a breakout that had no follow-through.

*Significance:* The harder call. The system did not simply avoid a choppy day — it correctly distinguished a REAL breakout cross from a FAKE one on the same price event. The gate that mattered today was not "wait for the trigger" (price crossed it) but "wait for 15M PRIMED after the cross" (it never primed). That second gate is doing real work. Owner assessment: even if caught, the ~500pt move into a descending 200 SMA with no fuel was probably not worth taking — poor quality, high reversion risk. Confirms the permission protocol is correctly filtering fuel-less breakouts, not just chop.

*Owner TA note (end-of-day read):* 200 SMA (~$63,400) pressing onto price from above as descending overhead resistance. Price coiling beneath it in the EMAs — the micro read (no 15M fuel) and the macro read (200 SMA ceiling) independently agree: no long here. Consistent with a bear-pullback-into-ceiling structure where the bounce exhausted at a major moving average.

*Running tally: 4 correct stand-downs + 1 winning approval, zero questionable.* Previous tally was 3+1; this session adds the fourth stand-down. The gate is correctly filtering both pure chop AND fuel-less breakouts with trigger crosses — the harder class of no-trade call.

*CoinGecko 429 recurring:* Publisher INTEL REPORTER: "HTTP Error 429: Too Many Requests" fired again today. Third or fourth occurrence over recent sessions. Upgraded from "continue to monitor" to actively recurring. The coded-but-dormant fix (Demo API key registration) is now worth doing — not a session blocker, but degrades the intel reporter on every stand-down day.

*Tier 4 DJ audit-integrity concern — CLOSED (scope trace today confirmed W-11 already resolved it):* Full code trace confirmed three facts: (1) `market_radar.py` writes `source="market_radar"` DJ rows on every scan; (2) `kabroda_mas_flow.py` `_inject_decision_journal()` writes `source="mas_flow"` DJ rows with a correct mapping (line 1487: "APPROVED"→"MAS_APPROVED", "STAND_DOWN"→"MAS_STAND_DOWN"); (3) `performance_auditor.py` queries `DecisionJournal.source == "mas_flow"` only — radar rows are invisible to the auditor. A `database.py` backfill (lines 182-187) labeled pre-W-11 historical rows correctly. W-11 (2026-06-13, confirmed in WORK_LOG line 453) built and shipped this fix. The "Tier 4 contamination" raised on 2026-06-18 was raised without awareness of W-11. Nothing to build. Tier 4 is CLOSED.

*Forward:* Owner is flat. Cleared to build non-core fixes. Priority: UI Tier 1 (HUD key + Panel 02 label, ~15 lines, `market_radar.html`). SA chat prompt fix (near-term, no dependencies). W-9 Phase 2 runner outcome — check production DB to confirm manual close was recorded correctly.

---

**2026-06-18 | APPROVED SHORT (regime turn) | Outcome: WINNING TRADE (T1 banked 40%; runner closed manually 2026-06-19 AM in profit below T1) | First with-trend approval after 3-day stand-down stretch | W-9 Phase 2 live test: runner outcome confirmation pending**

*Call:* APPROVED SHORT. Entry 64,121.60 | Stop 64,465.36 | T1 62,883.31 | T2 62,118.04 | T3 60,879.75 | 40/40/20 scale-out. GATE OPEN / BEARISH / STRONG at lock.

*Regime context:* Forward watch-item from the 06-16/06-17 rollover thread resolved: first with-trend (short) approval arrived. System waited out three consecutive stand-downs (two-sided chop / FOMC whipsaw / multi-TF exhaustion) and engaged when 4H aligned bearishly and JEWEL gate was open at lock. Owner took the trade; price moved into profit (~63,800–63,900 range vs. 64,121.60 entry) before pullback at session end. Exactly the behavior the evaluation discipline was watching for.

*JEWEL gate clarification (resolved, clean approval):* Panel 02 showed "GATE CLOSED — STAND DOWN" while Panel 03 showed APPROVED SHORT. Confirmed NOT a conflict. JEWEL gate was OPEN at lock time (09:00 UTC LONDON_OPEN JewelSnapshotLog row — what `_read_jewel_context()` read at 13:00 UTC lock end). Gate closed in the 14:00 UTC NY_OPEN snapshot — one hour post-decision. Panel 02 reads `JewelSnapshotLog.order_by(id.desc()).first()` (always most recent/live); Panel 03 reads frozen CampaignLog. Different tables, different moments. SA approved on valid open-gate conditions; no override anomaly. The apparent contradiction is a display-layer design gap (GAP-1 extension), not a logic error.

*Target reasoning — AUDITED, system stands behind its math:*
- Distance = bo − bd = 1,238.29 (session box)
- T1 = bd − distance = 62,883.31 / T2 = bd − distance × 1.618 = 62,118.04 / T3 = bd − distance × 2.618 = 60,879.75 (exact Fibonacci multiples, verified to 4 decimal places)
- All three targets computed by `_compute_targets()` in Python and copied verbatim by SA — LLM never computes targets; that principle held.
- Stop = 64,465.36 = TSA structural placement: r30_high + ATR × 0.5. NOT the raw bo trigger (actual bo ≈ 65,359.89). Gives ~3.6:1 R:R to T1.
- 40/40/20 allocation: conditional default rule (fuel clean + WITH-TREND on 4H confirmed) — not a probability estimate.
- No HEAVY/MAXIMUM KDE walls snapped between entry and any target. "Clear airspace" statement is TSA verified output from code, not SA narrative.

*THE FINDING — confirmed architectural blind spot (SSE-vs-TSA):* SSE `daily_support` (~63,700) sits 34% of the way from entry to T1 — inside the measured-move path. It is NOT wired into the Trade Structure Analyst's target-snapping pipeline. TSA only scans `kde_peaks` (gravity KDE); SSE S/R levels go into the SA's narrative context string as named text only, never into the target-computation code path. T1 was set through 63,700 because the formula projects the measured distance regardless of SSE levels in the way — the system did not actively reason "63,700 will give way"; it simply never saw that level in the math that sets targets. Owner's trader's-eye instinct (questioning the 63,700 level) caught a real gap between the gravity engine and the SSE engine, confirmed by full code trace. Verdict: targets are mathematically sound and Fibonacci-correct, but structurally incomplete — they ignore one class of real levels. Fix logged as gated strengthening-phase pin: "SSE-into-TSA target wiring" (Suggestion Box 2026-06-18). `daily_support`/`daily_resistance` already live in the `levels` dict passed to `apply_trade_structure()` — data is there, just not read.

*THE FINDING — in-site SA chat / stale-brief confident advisor:* Owner asked the in-site SA chat for live trade-management guidance mid-afternoon. The SA confidently said "price is 5 minutes from tagging T1, do not close early, hold for T1" — when T1 had already tagged hours earlier and the owner had already taken 40% off. The SA was reading the frozen lock-time brief (entry/T1-not-yet-hit state) and speaking as if live. When corrected, it admitted: "I operate exclusively on the Market Brief. I have no live feed. I cannot tell you where price is now." Same class of problem as Panel 02 / radar-grade / DecisionJournal: a surface presenting stale/secondary data with authoritative/live confidence. The directive language ("hold," "do not close early," "let the math complete") sounds like a live advisor, but the SA is completely blind to current price, partial fills, time elapsed, or trade progress. Actively misleading for an in-trade decision — worse than silence. Design implication: near-term correct behavior is for the in-site SA to refuse or caveat live trade-management questions rather than answer confidently from stale data. Fix logged as gated pin (Suggestion Box 2026-06-18).

*Cross-session resolution:* Validates the 06-16/06-17 stand-down stretch as correct patience — system waited out FOMC chop and took the with-trend trade when the regime turned. New forward watch: how the trade resolves. Also first live test of W-9 Phase 2 OHLC fix for a FILLED trade — outcome should write CLOSED_WIN or CLOSED_LOSS with real candle timestamp, not EXPIRED.

*UI divergence map (separate from trade — read-only investigation):* Full architectural map completed today. Authority principle established by owner: agents/SA CampaignLog = single source of truth post-lock; the UI displays that verdict uniformly everywhere; radar independent scoring is demoted to pre-lock pre-check + auditor background data only. Tier 1 fixes (HUD key sourced from CampaignLog + Panel 02 label when APPROVED + JEWEL post-lock closed, ~15 lines total in `market_radar.html`) scoped, NOT built. Trade active. Tier 4 audit-integrity concern — CORRECTED 2026-06-19 (already closed by W-11): radar DJ rows use `source="market_radar"`; MAS DJ rows use `source="mas_flow"` with correct APPROVED→MAS_APPROVED mapping; `performance_auditor.py` filters `source == "mas_flow"` exclusively. Radar STAND_DOWN rows cannot reach the auditor. W-11 (2026-06-13) built and backfilled this. The concern was raised without awareness of W-11. Nothing to build. See Suggestion Box 2026-06-18 for Tier 1/2 UI fixes (still valid).

*Status notes:* No code changes today. Owner had active trade throughout — all fix work gated until flat.

*Trade status (final resolution — updated 2026-06-19):* T1 (62,883.31) tagged — 40% banked, clean win locked. Price hit a light gravity wall ~62,232 (bounced; T2 @ 62,118 did not tag — wall absorbed it). Price ground back up to light wall near T1 ~62,800. Owner closed the remaining 60% manually on 2026-06-19 AM, just below T1, in profit. Runner did NOT return to breakeven; stayed between T1 (~62,883) and T2 (~62,118) overnight. Fully profitable trade.

*Owner reasoning (verbal observation, not a system input, logged for the record):* closed because 2026-06-19 showed the same undecisive 24h range (BO ~62,894 / BD ~62,354) with no directional resolution — chose not to hold a profitable runner waiting for bears to reassert while risking a spike back toward yesterday's entry/stop. Explicitly an owner read based on reading the 06-19 setup; does NOT alter system design.

*Data points for the entry/exit-mechanics record:* (1) Runner stalled between T1 and T2 — consistent with the gravity-wall reads from 2026-06-18 (light wall ~62,232 absorbed the move). Reinforces the SSE-into-TSA target-path blind spot: the wall that stopped the runner was in the gravity engine's map, not the SSE, but a T2 adjustment would have been appropriate if the pipeline knew about it. (2) Owner had no system live-read to consult on the hold/close decision — the live exhaustion monitor would have been directly useful here (Suggestion Box 2026-06-18). (3) Entry mechanics: trigger-order entry ate 45 min of retest chop; the wait-for-15M-close path would have filled on the retest (confirmed first data point for the entry-mechanics tracked variable). (4) W-9 Phase 2 runner outcome — confirm that the ledger engine recorded the manual close correctly (CLOSED_WIN at real candle timestamp, or CLOSED_AT_EXPIRY if it ran past session boundary).

*FINDING — live exhaustion monitor (highest owner-interest live feature):* Once inside a trade holding a runner (e.g. 40% banked at T1, 60% held for T2/T3), the owner currently has no system read for "is the move getting tired?" Owner manually agonized over the 60% tonight with no system guidance available; the in-site SA was blind to live price state entirely. The capability needed: monitor intraday timeframes for exhaustion signals while holding a runner — 15M reversal forming? 1H/4H ADX/MACD/RSI rolling? Price stalling at a named gravity wall? — and flag "the move is getting tired, consider banking the runner." This is NOT an entry signal; it is an in-trade exit-assist. The decision it resolves: hold for T2/T3 vs. bank now before a bounce grinds back to breakeven or stop (where "trade emotion" lives — "did I bank 40% then let the 60% reverse to a $20 net after fees?"). Foundation already exists: W-9 OHLC live trade monitor already reads live candles every 60s — this extends it to read exhaustion indicators and alert. Distinct from both the stand-down re-arm alerter (which is pre-entry, watching for confluence to form) and the live trade monitor (which watches price vs. stop/T1/T2/T3) — this is specifically the "is it done?" read for a runner in an active position. Logged as new Suggestion Box pin (2026-06-18). Gated: same as stand-down re-arm alerter (15M core solid + notification infra / W-4). High owner interest — flag explicitly when scope board opens.

*FINDING — entry mechanics (unresolved, to be settled by data, NOT a fixed rule):* Three entry methods exist, each with an opposite failure mode: (1) **Trigger order at breakdown** (what owner did today — placed ~08:08 CST before breakdown, filled on the break, then ate the retest bounce ~45 min of chop before the run continued): never misses the move; risk = filled on a fake-out wick that reverses to stop. (2) **Wait for 15M close below breakdown, then limit-on-retest at breakdown level**: best price + confirmation; risk = retest never comes, trade is missed entirely ("too greedy" trap — worst outcome because the call was right and earned nothing). (3) **Wait for 15M close below breakdown, then enter at-market near the close**: confirmation + actually gets in; risk = worse entry price, wider spread, slightly worse R:R. **Owner's key insight:** missing a correctly-called trade (method 2's failure) is arguably the worst outcome — the "limit-on-retest" is NOT a safe default. Price often breaks down and runs without retesting, or pulls back partway, exhausts, and runs to target with the limit unfilled. Do NOT treat "wait for close then limit at breakdown" as the system's rule. **Resolution path — tracked variable, empirical data decides:** entry behavior becomes a TRACKED item in the evaluation log. For each approved trade, log what price did at entry: did it retest the trigger level after the 15M close, or run without looking back, and how far did it pull back before continuing? After several approved trades, the empirical pattern indicates which method fits this system's setups — retest-prone (wait + limit works) vs. run-prone (trigger order needed). Today's data point: broke down → 15M closed below → retested 30M low ~45 min later → then ran (a wait-for-confirmation entry WOULD have filled today). One data point; need several. **System gap flagged:** the system calls the setup (levels) but does not model entry execution (trigger vs. confirm vs. retest). A live breakdown-candle monitor (related to the live exhaustion monitor and W-9 OHLC engine) could eventually advise "closed below, now retesting — fill here" vs. "closed below and running — enter now." Gated future item. Logged as new Suggestion Box pin (2026-06-18).

---

**2026-06-17 | STAND_DOWN | Outcome: CORRECT (confirmed by full-day price action) | NEWS-DRIVEN CHOP (FOMC) | Part of 2-day rollover thread**

*Call:* STAND_DOWN.

*Confirmed outcome (end-of-day):* Price broke out, tagged the breakout area (~$66k), reversed, came down to the breakdown area (~$64.5k), chopped flat at ~$64.2k. Triggered BOTH sides, resolved NEITHER — textbook whipsaw. No real trade existed in the day's action. Confirmed correct.

*Root cause of the chop:* **FOMC meeting today.** The system stood down WITHOUT news awareness — its structural read (multi-TF exhaustion + collapsed short targets, per the brief) independently produced the correct stay-out call on a day a news-aware trader would also have avoided. Notable: structural logic reached the right answer through a different door than news calendar would have. First concrete proof-of-value for the NEWS/EVENT CALENDAR AWARENESS pin (Suggestion Box 2026-06-14).

*Trading rule surfaced (owner/trader-community observation):* FOMC and similar high-impact events inject untrackable two-sided volatility — price can run either way then chop, not following structure, until it settles a day or two later. Rule: **be OUT of any position before such meetings hit.** This is the kind of friction the system currently can't see. A calendar-aware layer would explain the chop in advance in the brief ("high-impact event today — expect friction, distrust breakouts").

*Evaluation at brief time (structural reasons — both still valid):*
1. **Multi-timeframe exhaustion** — 1H fully flipped BEARISH with rising ADX (23.73). 4H bullish on NEGATIVE momentum + JEWEL EXTENDED (running on fumes). 15M OVEREXTENDED with active exit warning. TF coherence 1/5.
2. **Short side structurally disqualified** — all three short targets collapse onto a single HEAVY wall at $64,283.47, 0.39% below the breakdown trigger. No valid measured-move R. Same collapsed-target-into-wall logic that correctly capped the 06-07 short — system is consistent.

Not a questionable stand-down. Third correct stand-down in the current bear-pullback regime.

*2-day rollover thread (06-16 → 06-17):*
- **06-16:** stood down on two-sided chop; brief flagged a conditional long. Owner watched the upside potential. Price instead failed and broke down — upper-$65k → $64k-something on 06-17. The watched-for long never triggered; standing down was correct. In hindsight 06-16's stand-down is MORE correct, not less — the one tempting directional lean (long) was the wrong way.
- **06-17:** 1H now fully bearish with rising ADX confirms the rollover that 06-16's chop was beginning. FOMC chop sits inside the broader rollover the system is correctly waiting out. These two sessions are one continuous event, not two isolated stand-downs.

*Owner TA observation (trader's-eye layer):* Daily dropped hard (~$10k), needed a rest; the pullback traveled back toward the ~$66–68k area, tagged ~$66.9k, showed exhaustion near that level, and is now rotating back down. If correct: the recent chop was NOT missed opportunity — it was the pullback completing at level, and the trend is now resuming downside. The payoff trades (short, with-trend) should appear once the 4H rolls from "bullish on fumes" to bearish and aligns down through 1H to 15M.

*Forward watch-item (Claude holds cross-session):* When does the 4H flip from bullish-on-fumes to bearish-aligned? That realignment is what should restore approvals. Watch for the **first with-trend (short) approval** — that's the regime turning. If the system catches a clean with-trend short that runs → validates this entire stand-down stretch as correct patience. If the trend resumes and the system KEEPS standing down through clean with-trend setups → that becomes the over-conservative flag. Either outcome lands in this log with the setup already captured.

*Status notes:* Brief fired clean at lock time again (13:05 UTC) — coherence fix holding day 2. No fill today — W-9 Phase 2 forward-verification still pending first live filled trade. Watch for Sunday's auditor run: confirm the CHOP_RISK / zero-correct-calls stat is computed on real resolved data under the W-15 legibility code, not a thin-data artifact.

---

### W-15 ◐ AUDITOR THIN-DATA LEGIBILITY FIX (near-term, before next Sunday's run)

**What breaks:** `performance_auditor.py` renders harmonic / kinematic-grade / box-size breakdown tables even when all directional outcomes are unresolved (`direction_correct = 0, direction_wrong = 0`). Each row shows `correct:0  wrong:0  unresolved:N  accuracy:unresolved`. The LLM sees zero-correct rows and synthesizes "0% accuracy / every configuration failed" — treating "no data" as "total failure." This makes every thin-data week's audit a false-alarm cry.

**Distinct from the "provisional numbers due to W-9 integrity" caveat** — that caveat is about data quality on resolved outcomes. This is about generating a false failure signal from an *empty* denominator.

**Root cause:** the three breakdown sections in `_format_stats_block` have no guard for `resolved_dir == 0`. The `win_rate` and `dir_accuracy` lines already handle `None` correctly ("Insufficient closed trades" / "No resolved calls yet"). The breakdown tables do not.

**Fix (scoped read-only, confirmed before build):** add `resolved_dir = direction_correct + direction_wrong` and `insufficient_data = (resolved_dir == 0)` after the stats extraction in `_format_stats_block`. Gate each of the three breakdown sections: when `insufficient_data`, replace the per-row table with a single `INSUFFICIENT DATA — N resolved directional outcomes this week. Metric not computable.` line. STAND_DOWN VALIDATION already handles the empty case correctly (`if sd["total"] > 0:` guard). No schema changes. No new tables. ~6 lines net across 1 file.

- **File:** `performance_auditor.py` — `_format_stats_block()` only
- **Status:** ☑ Closed 2026-06-16 (commit `cdd2425`). 8 lines added across 3 guards.
- **Calibration note:** makes the weekly auditor's stand-down validation trustworthy during the bear-market-pullback watch phase — the automated half of the dual-accountability evaluation rhythm can now be trusted on thin-data weeks.

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
| 2026-07-03 | **STOP/TARGET EXECUTION TRIGGERS ON 1-MINUTE WICK TOUCH, NOT TRADING-TIMEFRAME CANDLE CLOSE — likely compounding the whipsaw problem independently of the stop-distance issue already found.** Full cross-reference audit of bold-hubble against live Kabroda code. `ledger_closing_engine.py` Phase 4 (`_fetch_1m_since`) fetches **1-minute** candles and closes a 4H/1H candidate the instant any 1-minute candle's high/low touches the stop or target price. This directly contradicts a rule stated independently by TWO separate bold-hubble sources: Mafioso's Meta Signals playbook ("Never exit on an intra-bar wick touch... wait for the specified timeframe candle to physically close past the stop") and the external break-and-retest research from the 2026-07-01 investigation session (same rule, independent source). **Even a correctly-widened stop (the still-unbuilt swing-low/Fibonacci fix from the session-4 investigation) could still get triggered by a brief 1-minute wick that would never have closed a 1H or 4H candle beyond the level.** This may be a second, independent contributing cause to the whipsaw complaints that started the whole stop/target investigation — not a replacement for the stop-distance fix, an additional layer on top of it. **Connects directly to:** the 2026-07-01/02 session-4 investigation (candidate 112, stop/target construction), and is a genuinely new finding not previously surfaced in that thread. | full bold-hubble-vs-Kabroda cross-reference audit, code confirmed via direct read of `_fetch_1m_since` call sites | TBD — real, code-confirmed, high-relevance to the still-open stop/target work; not built. Candidate fix (not applied): check against the trading-timeframe candle's own close, not 1-minute OHLC, when confirming a stop-out. |
| 2026-07-03 | **RUNNER / STAGED-PARTIAL-PROFIT MECHANIC NOW HAS THREE INDEPENDENT CONFIRMATIONS — worth prioritizing once the stop/target construction itself is fixed, not before.** (1) External research from the 2026-07-01 investigation: Fibonacci extension staging (100%=stall/first partial, 127.2%=conservative target, 161.8%=golden-ratio significant target, scale out across levels). (2) Mafioso's real alert examples (Meta Signals playbook): partial profit at T1 (~1.0 RR, 30-50%), move stop to breakeven, trail remainder toward T2/T3 along the 20 SMA or 4H EMA 21. (3) The original Kabroda master plan already specified this exact mechanic as **Component 4 — Runner Mechanic** (two-stage stop: close 50-60% at T1, trail remainder using structural 4H zones) — currently unbuilt, gated behind Component 1 (4H system) having real trade data. Three independently-sourced descriptions converging on the same shape (bank a first target, protect the rest at breakeven, trail structurally) is unusually strong evidence for something already on the roadmap. **Still correctly gated** — can't build a runner on top of the stop/target mechanism already confirmed broken (session-4 investigation); this is evidence for prioritization once that foundation is fixed, not a reason to build early. | full bold-hubble-vs-Kabroda cross-reference audit | TBD — strong convergent evidence, correctly gated behind the stop/target fix; not built |
| 2026-07-02/03 | **SA CONTEXT MISSING "CURRENT PRICE" — likely root cause of a wrong price citation in the 2026-07-02 STAND_DOWN brief (both newsletter and raw cockpit brief said "$63,808" while real price stayed $60,700–$62,200 across three chart snapshots spanning 07-02 into 07-03, and the brief's own narrative elsewhere describes price "pressing against the $61,600 breakout trigger from below" — internally contradictory).** Traced in code, confirmed (not yet fixed — watching, not patching): `battlebox_pipeline.get_live_battlebox()` computes a real live price (`"price": float(raw_5m[-1]["close"])`) in BOTH the pre-lock (CALIBRATING) and post-lock (OK) return branches, but in both branches that key sits at the OUTER dict level, sibling to `"battlebox"` — never threaded into the `"context"` sub-dict that actually gets passed to `_build_senior_analyst_context()` in `kabroda_mas_flow.py`. Confirmed via direct read of that function's full context-building block (lines 871-925): no `"price"`/`"current_price"`/`"live_price"` key anywhere, and no explicit "Current Price:" label exists in the constructed prompt. The SA has session trigger levels (bo/bd) and a list of Elliott Wave macro-structure levels with their own prices, but nothing telling it plainly "this is where price is right now." Working hypothesis: the LLM pulled a nearby structural number (possibly an intermediate wave pivot) into the "current price" sentence because it was never given an unambiguous one. Not yet confirmed against the actual raw session prompt (would need DB access to `DecisionJournal`/`full_context_json` for that specific session, not available in this environment) — code-level absence is real and independently strong evidence either way. **If confirmed, this may not be new to 07-02 — every SA brief ever generated may have had this same gap.** Candidate fix, NOT applied: thread the existing `price` value from `get_live_battlebox()`'s outer dict into `context["current_price"]` and add one explicit labeled line in `_build_senior_analyst_context()`. **Not acted on — watching, per owner instruction, while the broader bold-hubble audit continues; may get folded into that work rather than fixed standalone.** | tracing the 2026-07-02 brief's price discrepancy, owner-shared brief + 3 chart screenshots | WATCHING — real, code-confirmed finding; fix candidate identified but not applied; may connect to the broader audit pass, not a standalone patch |
| 2026-07-03 | **KROWN'S REAL EMA RIBBON IS A FIBONACCI STACK (5/21/55/377), NOT THE CODED 20/50 SMA.** Raw read of `bold-hubble/extract/youtube_streams_analysis.json` (7 of Krown's own YouTube streams, June 23–July 1, transcribed) shows him consistently trading a 5 EMA (red) / 21 EMA (yellow) / 55 EMA (green) / 377 EMA (blue) stack — all genuine Fibonacci numbers. This is different from BOTH `bold-hubble/strategies/*.py` (20 SMA/50 SMA — already known to be a simplified port) AND Kabroda's own JEWEL ribbon (9/21/35/55) — only 21 and 55 overlap across all three. The 377 EMA specifically comes up repeatedly as a major long-term reversal magnet ("hitting the daily 377 EMA is very likely to produce at least some impetus of a bounce") — nothing in Kabroda has an EMA that long. Same class of finding as the earlier code-vs-real-rules mismatch (2026-07-01/02), one level deeper: even `KROWN_TRADING_MASTER_REFERENCE.md` simplified this down to 20/50 SMA. | reading raw YouTube stream transcripts, cross-referencing against the coded EMA lengths | TBD — real, specific, well-evidenced across 7 independent streams; not built |
| 2026-07-03 | **"THREE DRIVES" — Krown requires a repeated divergence pattern, not a single hit, before treating a reversal as real.** Same transcript source: "three drives of regular bearish divergence," "three drives of hidden bullish divergence" — a specific pattern-counting confirmation rule. `bold-hubble/indicators/rsi_divergence.py` detects individual divergence occurrences with no concept of counting a repeated pattern before signalling. Directly relevant if the parked "weekly/daily RSI divergence for narrative" suggestion-box item (2026-07-01) ever gets built — "three drives" would be the real confirmation bar, not a single hit. | same YouTube transcript read | TBD — connects to the parked RSI-divergence-for-narrative item; not built |
| 2026-07-03 | **REVIN RIBBONS — real reverse-engineered architecture now available (owner added `REVIN_RIBBONS_AI_BUILD_PROMPTS.md` to bold-hubble from a separate project), previously flagged as an unreplicatable paid third-party indicator.** Document claims sourcing from Krown's own official Teachable curriculum + Discord `#indicators` hub — a 3-part suite: (1) **Revin Ribbons core** — 21-period EMA/SMA midline (matches the "yellow 21 EMA" from the transcripts — same number, strong cross-confirmation) as the bullish/bearish bias anchor, with inner bands at ±1.0 StDev/ATR and outer bands at ±2.5/±3.5 StDev; the "gray dots" constantly referenced in streams are almost certainly the lower ±1.0 band. (2) **RWP (Revin Width Percentile)** — percentile rank of band width over a 252-period lookback — structurally the *same calculation* as BBWP, which Kabroda already has fully built, just applied to different band multipliers. This is the most immediately buildable piece — closest to existing code. (3) **RMO (Revin Momentum Oscillator)** — composite −100/+100 score from 5 vectors (duration, magnitude, ribbon separation, oscillator level, combined) — genuinely novel, nothing like it exists in Kabroda, would need to be built from scratch. **Trust caveat:** the document itself frames this as "AI build prompts" to generate an "open quantitative replica" — even its own source calls it a best-effort reconstruction, not a confirmed-exact decompile of the real paid indicator. Real and useful, treat as a strong hypothesis, not verified ground truth. **Testable next step, not yet run:** if midline = 21 EMA, Kabroda already computes a 21 EMA — could directly check it against the specific "midband" price levels Krown cited across multiple streams (62,200 on 06-30; 64,000 and 63,450 on different days; 69,311 on a longer timeframe) to see if they match, which would either strongly confirm or rule out the midline=21EMA hypothesis with real numbers. | owner added `REVIN_RIBBONS_AI_BUILD_PROMPTS.md`, cross-referenced against the YouTube transcript read same session | TBD — most substantial finding of this pass; RWP module nearly free to build (existing BBWP code), midline overlaps existing EMA infra, RMO would be new work. Not built — verification test (midline vs. real cited levels) is the natural first step before any code. |
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
| 2026-06-05 | **SSE LEAN — FUTURE ADX-GATING ENHANCEMENT (owner, 2026-06-05).** Owner history: the SSE lean is strong/accurate in clean trends but gets fooled in chop (calls continuation while price is actually bottoming/bouncing). The corrected ADX is the tool to fix this: eventually gate the lean's own confidence by ADX — high ADX = lean in its reliable zone, weight normally; low/falling ADX = choppy, discount the lean's directional call. This addresses the lean's known weakness at its source. Build as an SSE-engine refinement AFTER the bias_model wiring is proven live. Ties to the ADX fix (W-7). | owner decision, bias_model wiring session 2026-06-05 | TBD — after bias_model proven live; build in sse_engine.py, ties to W-7 |
| 2026-06-06 | **FEED LIVE INTERPRETER OUTPUT BACK TO CC WHEN TUNING AGENTS (owner, 2026-06-06).** CC tunes agent prompts from the code (intent), but hasn't seen what the agents actually PRODUCE live. When working on any agent's prompt, paste its real output from `/admin/interpreter-log` so CC tunes against reality, not just intended behavior. Closes a blind spot: prompt intent ≠ prompt output — the two can diverge silently over sessions. Concrete step: before any prompt edit, pull the last 2–3 live outputs for that agent from the interpreter log and include them in the session context. | owner, A3 scope session 2026-06-06 | TBD — adopt as standing practice from next agent-prompt session |
| 2026-06-06 | **AUDIT TOOLING AS PERMANENT SITE FEATURE (owner, 2026-06-06).** The verify-before-build loop keeps finding things the static audit missed (allocation rule, BBWP data-path). Make audit / stress-test capability on-demand in the app — e.g. replay-harness-on-demand view, interpreter-output history with per-agent diff. So checking flow-through is a button, not a rebuild. Extends the existing `/admin/interpreter-log` page + cost monitor (already built). **GATED:** build after A3 + B1 are done — this is tooling, downstream of fixing the actual signal flow. | owner, A3 scope session 2026-06-06 | TBD — after A3 + B1 live; extend /admin/interpreter-log |
| 2026-06-06 | **AUDIT IS A LOOP, NOT A SNAPSHOT (owner, 2026-06-06).** Lesson confirmed across W-8: a one-time audit gives leads; verifying each finding while building surfaces the next layer (the allocation rule was invisible until A3 scoping; BBWP absence was a false assumption). This is healthy, not a failure — the audit list is a STARTING POINT, not a complete inventory. Keep the verify-first protocol on every remaining finding. Don't trust the audit list as exhaustive before building. | owner, end of A3 scope session 2026-06-06 | Standing protocol — not a build item |
| 2026-06-06 | **STAND-DOWN BRIEF — INTERNAL vs PUBLIC are different products (owner, 2026-06-06).** Internal brief on a no-trade day CAN be terse — the trader needs "no action, here's why" and no narrative is required. But the PUBLIC publication on a stand-down day must NOT be cut down: it should explain what is happening in the market (e.g. price at a major support floor, stop-hunt/chop dynamics, why both sides are positioning), AND must ALWAYS include a forward-looking / higher-timeframe section ("what to watch next"). A no-trade day is when a reader most wants the "what's going on?" read. Publisher-agent needs a STAND-DOWN TEMPLATE distinct from the internal one — different prompt path, different output structure. This is the differentiator between a real publication and a signal feed. Publisher prompt tuning, publication phase — NOT now. | owner, Jun6 STAND_DOWN session 2026-06-06 | TBD — publication phase |
| 2026-06-06 | **B1 PMARP — MONITORING + DESIGN ITEM, NOT a build now (owner, 2026-06-06).** Bug confirmed: `pmarp_overextended = rank > 75` is blind to downside extremes (Jun2 rank=0.00, pmarp_overextended=False). BUT: naive `rank < 25` fix fires on ALL 8 sessions in current dataset — the 252-bar 4H history is entirely inside the downtrend, so every session reads rank < 25. This fix would re-create an always-on T1 cap (exactly what A3 removed from MACD). Even rank < 5 fires on Jun2/Jun3 (A3-unblocked sessions). Full fix is A3-class: (1) threshold decision, (2) STRONG+with-trend override (same override deferred for exit_warning), (3) MTF interpreter prompt to distinguish downside extreme on SHORT vs LONG. Data is currently unfit for threshold calibration (one-sided history). Build gate: market ranges/rallies → balanced PMARP history AND exit_warning override is scoped (B1 shares that layer). Verify-first protocol prevented a fix that would have re-broken A3. | owner, B1 verification session 2026-06-06 | DO NOT build — monitoring only; build gate: balanced market + exit_warning override scoped |
| 2026-06-06 | **HIGHER-TIMEFRAME STRUCTURAL ANTICIPATION — major capability project, GATED (owner, 2026-06-06).** The system reads intraday structure well but does NOT anticipate the bigger board. It reacts to major structural levels (e.g. the $60K MAXIMUM wall) as price ARRIVES, not days ahead. Owner's vision: the system should call out major higher-timeframe support/resistance levels 3–4 days BEFORE price reaches them, so price stalling/chopping/bouncing at those levels is EXPECTED rather than surprising — and so a trade that fires AT a pre-flagged level (e.g. a short rejecting a zone already called) is recognized as higher-probability because the interaction was anticipated, not just reactively matched. Reference model: Mafioso 8H signal calls forward pullback targets (T1/T2/T3 on the way UP) and likely rejection zones ahead of time. Kabroda's gravity map already knows WHERE the walls are but does not narrate the JOURNEY toward them or anticipate interaction. Two open design questions before scoping: (1) Should the gravity map be enriched with liquidity/order-book data to strengthen the structural read, or kept as the higher-timeframe structural map it is and connected to a new anticipation layer above it? (2) How does anticipated-level-interaction feed the trade decision — does a setup firing AT a pre-flagged level earn a higher allocation tier, a stronger SA conviction label, or a different target structure? Likely connects to the parked B1 (PMARP extreme = "we're AT the wall now") and the SA higher-timeframe narrative gap. **BUILD GATE:** front-of-river solid (done) AND A3 confirmed live across varied sessions. Own scoped project when gates clear. **CONCRETE REFERENCE EXAMPLE — Mafioso 4H call at the $60K floor (2026-06-06):** At the identical juncture where Kabroda stood down (15M tangled, choked target at $60,025.76 MAXIMUM wall), Mafioso issued a LONG bounce call with forward targets 63,850 / 66,504 / 66,701 and a stop on 4H-close-below 59,617. Both systems recognized the $60K floor as the pivotal decision point; they resolved it differently — Kabroda: stand down, bearish bias intact; Mafioso: long bounce. What Mafioso did that Kabroda cannot: mapped the full BOUNCE PATH forward — specific levels where price would likely stall, reject, and potentially set up a high-probability re-entry short. This is the anticipation-narration gap in concrete form. "Kabroda knows where the walls are but doesn't say: if we bounce, here's where we'd stall and reject, and that rejection is a high-prob short setup." CAVEATS (owner's framework — do not blur): (1) Mafioso's long is COUNTER-TREND within the bearish structure owner has mapped — a bounce-then-reject read, not a trend turn; owner's structural view (short the pullback) is intact. (2) Mafioso's "4H close below" stop methodology is the wide candle-close approach owner has already flagged as dangerous — price can spike far through the level intrabar before the close is confirmed; note the methodology difference, do not adopt it. (3) Mafioso is a reference/mirror only — NOT a direction source, NOT a tiebreaker against Kabroda's own logic. Use his forward-target structure as the design template for what HTF anticipation output should look like, not as a signal to follow. **SECOND CONCRETE EXAMPLE — weekly-close timing blindness (2026-06-07):** Owner's read on Sunday Jun 8: price is coiling into the weekly candle close (~00:00 UTC tonight) at a major decision level ($60,055 Wave-5 trigger). Expect chop and intraday drawdown today; real directional resolution waits on the weekly close. If the level holds → likely bounce/pullback up next week; if it fails → breakdown continuation later. Kabroda has ZERO awareness of this context. It evaluated today's intraday setup in isolation and approved a SHORT — blind to "this is a wait-for-the-close day where the intraday snapshot is noise relative to the bigger event resolving tonight." The approved short is technically valid by intraday logic but poor R:R in context — the system traded the detail while missing the frame. **What the HTF-anticipation layer needs to do here:** reason about UPCOMING higher-timeframe events (weekly/daily closes at key levels) and contextualize the session accordingly. Example output: "Price approaching weekly close at $60,055 Wave-5 decision level — expect indecision and potential intrabar whipsaw today; consider standing aside until the close resolves." This is a TIME-awareness gap, not just a level-awareness gap. The gravity map knows WHERE the walls are; it does not know WHEN a higher-timeframe close is imminent or that "close at a key level = low-quality intraday action." **Connects to the R:R gate pin** — both represent the system missing bigger-context that says "don't trade today regardless of what the intraday snapshot shows." The HTF-anticipation layer and an R:R gate are two expressions of the same missing capability: session-level context that overrides or qualifies intraday trigger logic. **UPDATE (2026-07-01, session 4):** real historical-data investigation into the 4H/1H stop/target mechanism independently arrived at the same conclusion externally-sourced research called out explicitly — 1H/4H should likely be entry-timing refinement nested inside Daily/Weekly structure, not independent parallel systems. See the 2026-07-01 session 4 write-up for real data (R:R comparisons, window-size testing, external research citations) backing this — this pin is no longer just design theory, there's now concrete evidence behind it. | owner, 2026-06-06 / 2026-06-07 | GATED — expansion tier; own scoped project after A3 confirmed live |
| 2026-06-06 | **ACCOUNT SIMULATOR / R-TO-DOLLARS TRANSLATION (owner, 2026-06-06).** The system measures in R — correct, account-agnostic. But R is the "GB" most people don't intuit; the validating view is "start $X, risk $Y/trade, 30 days → where's the account." Pure arithmetic on the existing closed-trade R record (1R = chosen risk amount), replayed forward into an equity curve. **TWO distinct uses, only one gated:** (1) INTERNAL (behind the password wall, for the owner's own comprehension — "5R = $500 at $100/trade") — NOT gated, build freely, it's a private validation tool. The password wall is the line: inside = free. (2) EXTERNAL (any public/marketing/newsletter/paid-facing page showing $ performance to attract subscribers) — GATED behind the securities/financial-services attorney review (performance representation + hypothetical-results disclaimers). The whole Kabroda system is currently fully password-protected, zero forward-facing — so everything built now is internal by definition. When any page leaves the wall, the external gate applies. | owner, 2026-06-06 | Internal: build anytime. External: attorney review first |
| 2026-06-06 | **EXIT_WARNING — LIVE MONITORING ITEM (NOT a fix now) (owner, 2026-06-06).** The `exit_warning` condition in the ALLOCATION RULE is a blunt T1-cap — it fired on Jun3 (15M grade=TANGLED at session open) and held a move that ran to T2/T3. Owner reviewed the live chart and confirmed T1 was still the correct call (15M tangled at a structural floor / light weekly-level — high-conflict zone, conservative exit right). But the pattern is the same class as the MACD veto A3 just removed: a single condition capping allocation without regard to trend strength or fuel quality. **Question for live monitoring over coming weeks:** is exit_warning ever capping clean strong-trend moves it shouldn't? Do NOT fix reactively to one day — observe across many sessions. Only scope a fix (e.g. a STRONG-with-trend override: exit_warning vetoed when 4H MACD=STRONG AND trade direction matches 4H trend) IF a real pattern emerges in data. This is the audit loop working: A3 removed the MACD veto, revealing exit_warning as the next layer — expected and healthy, not a regression. | owner, A3 validation session 2026-06-06 | TBD — observe live; scope fix only if pattern confirmed |
| 2026-06-07 | **R:R / TRADE-QUALITY GATE (owner insight, 2026-06-07).** The system approves on DIRECTION + STRUCTURE but does NOT evaluate whether the resulting trade is a good risk:reward proposition. A "structurally valid trade" and a "good trade" are different things; Kabroda currently only checks the former. **Concrete example (2026-06-07):** approved SHORT — entry $60,508, stop $62,120 (~$1,612 risk), T1 $60,025 (~$483 reward) = **~0.3:1 R:R**. Directionally valid by every intraday gate, but a trade no disciplined trader takes: risking 3+ to make 1, because T1 sits against the $60K MAXIMUM wall only $480 away while the stop is $1,600 away. The wall proximity that caused the T1-only cap (choked target) also makes the R:R structurally unfavorable — both are symptoms of the same geometry, the system just doesn't say so. **Future layer:** after computing triggers, targets, and stop, assess R:R and flag/downgrade setups where reward is poor relative to risk. Example output: "Valid SHORT structure but T1 is only 0.3R from a 1R stop — wall too close, R:R unfavorable, consider standing aside." Threshold to determine (e.g. minimum 0.8:1 or 1:1 before APPROVED; below that → STAND_DOWN or CAUTION flag). **NOT TODAY** — do not bolt onto a working system mid-validation. **Connects to:** (1) VET-A-TRADE / timing pin — both are cases where the direction is right but the geometry makes the entry suboptimal; (2) HTF-anticipation pin — weekly-close timing blindness is the same failure class (bigger context says "don't trade today"); (3) allocation logic — R:R gate likely lives near or inside the allocation rule layer, alongside the MACD-strength and exit_warning caps. **Gates:** same as HTF-anticipation and VET-A-TRADE — front-of-river solid (done), A3 confirmed across varied sessions, design the three connected features together before building any of them. | owner, 2026-06-07 | GATED — do not build in isolation; design with VET-A-TRADE + HTF-anticipation as a connected evaluation layer |
| 2026-06-07 | **VET-A-TRADE — ENTRY TIMING + DRAWDOWN AVOIDANCE TOOL (owner insight, 2026-06-07).** Core value is NOT "is this a good trade direction?" — it's "given an external signal's direction and targets, WHERE and WHEN is the lower-drawdown entry, and where's the real invalidation." **Concrete reference (2026-06-07):** Mafioso 4H LONG — entry $61,671, stop "4H close below 59,617" — concedes ~$2,054 of downside room before invalidation. Taking the entry at $61,671 means sitting in drawdown while price potentially tests $60K first. Kabroda's same-day read: the bounce is a decelerating counter-trend pullback likely to revisit the $60,025.76 MAXIMUM wall before any sustained move. **Synthesis the tool should produce:** "The long direction may be valid but Kabroda's near-term path says price likely tests $60K first — don't enter at $61,671 now. Wait for the pullback to the wall, confirm it holds, THEN enter with far less drawdown and a tighter stop." This solves **right-direction-wrong-timing**, the most painful trader failure mode: you have the thesis right, you lose money on the entry. **The two-system model:** external signal = direction + destination; Kabroda = immediate structural path + optimal timing. They are complementary, not competing. The external signal does NOT override Kabroda's direction read — Kabroda uses it only to identify destination targets and map the lower-drawdown entry point along the path it already sees. **Output structure the feature needs to produce:** (1) direction alignment check (does external signal direction match or oppose Kabroda's bias?); (2) near-term path read (what does Kabroda expect price to do in the next session before reaching the external signal's destination?); (3) recommended entry timing ("wait for X level, confirm Y condition, then enter"); (4) drawdown comparison (entering now vs. waiting = estimated max drawdown difference); (5) real invalidation level (Kabroda's trigger/structural level, not the external signal's wide candle-close stop). **CAVEATS:** (a) Mafioso's "4H close below" stop methodology is the wide candle-close approach owner has flagged as dangerous — always substitute Kabroda's structural level as the real invalidation; (b) external signal is reference only — direction and target levels are inputs, not overrides. **GATES (same as HTF Anticipation):** front-of-river solid (done) AND A3 confirmed across varied sessions; Version-A structural-read framing only (internal tool); any external/paid-facing use = attorney gate first. | owner, 2026-06-07 | GATED — build after A3 confirmed live across varied sessions; see HTF Anticipation pin for shared design questions |
| 2026-06-07 | **MULTI-TIMEFRAME SSE ENGINES — MAJOR architectural project, GATED (owner, 2026-06-07).** The current SSE/battlebox engine analyzes ~24h of data to produce levels + bias for the 15M intraday trade. Idea: replicate the engine for 1H, 4H, daily, and weekly — each with its own VRVP / value-area / trigger levels / bias — so Kabroda becomes a multi-timeframe system ("I want to trade the 4H setup — what do I do?") rather than 15M-only. Matches what external reference systems (Mafioso) do with 1H/4H/8H signals. **Why this is high-leverage:** it reuses the existing core engine; point it at different candle sets rather than inventing new math. **Why this is likely the MECHANISM for HTF-anticipation:** a native 4H/weekly SSE engine would give Kabroda the higher-timeframe structural awareness it currently lacks natively — the "see the bigger board" capability the HTF-anticipation pin describes is probably not a separate feature but the natural output of a 4H/weekly engine running alongside the 15M one. Scope this jointly with the HTF-anticipation pin; they may resolve into one "multi-timeframe architecture" design project, not two. **SCOPE CAUTIONS — do not underestimate:** (1) NOT a copy-paste — each timeframe needs its own lookback calibration, threshold tuning, and live validation (a weekly wall ≠ a 15M wall in geometry, noise characteristics, or how close price can trade to it without triggering); (2) the HANDSHAKE between engines — does weekly bias override 15M? how do they reconcile on disagreement? what does a "STAND_DOWN on 4H but APPROVED on 15M" mean operationally? — this is its own real design problem and is where most of the complexity lives; (3) this is a PROJECT, likely the largest on the board, not a feature weave-in. **HARD GATES before even scoping:** (a) the 15M core must be proven SOLID across MANY live sessions — A3 is only 2 sessions old; B1/PMARP direction-blindness is still parked; replicating an unproven engine 4× is "build on sand" at maximum scale, copying any undiscovered bug into 4 more engines simultaneously; (b) dashboard must be legible so each engine's output is verifiable as sessions accumulate; (c) scope jointly with HTF-anticipation — do not design either in isolation. **The sequence:** 15M proven → dashboard legible → HTF-anticipation + multi-TF scoped together → then build. This is the highest-value future expansion on the board, and exactly why it must wait for a provably solid foundation. **UPDATE (2026-07-01, session 4):** a live 1H candidate (candidate 112) surfaced this pin's core question directly, ahead of schedule — the current 4H/1H stop/target mechanism is confirmed broken (near-coin-flip R:R, stale unrelated zone lookups) and a real historical-data investigation plus external research both point toward the same architecture this pin describes (per-timeframe structure, nested entry timing). No code changed, no gate lifted — but this is no longer purely theoretical scoping, there's now a real worked example with real numbers behind it. See the 2026-07-01 session 4 write-up. | owner, 2026-06-07 | HARD GATED — biggest project on the board; scope jointly with HTF-anticipation after 15M core proven across many sessions |
| 2026-06-07 | **PERFORMANCE AUDITOR — FROM REPORT TO COACH (owner vision, 2026-06-07).** Tonight's first scheduled run is a thin, caveat-heavy SNAPSHOT — 5–7 sessions, sparse outcomes. It confirms the scheduler fires and produces honest output. That is all tonight is. The VISION is a separate, future capability: **(1) MEMORY** — the auditor accumulates and reasons over its OWN past audit notes, so it can say "I've watched three weeks, here's a recurring issue" that a daily eye forgets. A one-shot audit cannot see a pattern; a coach that reads its own history can. Requires audit-history storage (the `SystemAuditLog` rows are the raw material — they exist now per Principle 5) plus a cross-audit pattern-detection pass in the prompt ("given the last N audit notes, what is the persistent theme?"). **The capture already starts tonight — the pattern-finding becomes possible weeks from now.** **(2) DECISION-LEVEL REVIEW** — not just "win rate was X%" but "it called T1 on this configuration, MACD read STRONG, RSI was 61 — did that characterization hold up over the following 4H?" Requires joining `DecisionJournal` signal snapshots to realized outcomes and reviewing the specific reasoning, not just the count. **Granularity refinement (2026-06-14):** this review should operate at the per-indicator level, not just the interpreter level — "was the MACD characterization specifically accurate this session? The ADX? The MA read?" — confirming or flagging where each indicator's read held up against actual price. Same concept, one layer deeper: interpreter-level accuracy is the current unit of review; per-indicator accuracy is the target state once data volume supports it. **(3) RESEARCH-TRIGGERING** — auditor flags a recurring gap (e.g. "CoinGecko 429 has fired 3 consecutive weeks") and initiates a prior-art pass: how do comparable systems handle rate-limited sentiment feeds? Returns "here is our current approach, here is theirs, here is a proposed tweak." Connects to the prior-art research passes already noted in the suggestion box. **Sequencing:** tonight = does it fire + produce sane output. Week 3–4 = is the output honest with thin data. Month 2+ = cross-audit memory becomes meaningful. Decision-level review and research-triggering are Phase 3+ builds, gated behind sufficient `DecisionJournal` + `SystemAuditLog` history. Do not scope them until the plain weekly audit has accumulated 4+ runs. **Job 2 dependency (2026-06-14):** the deep stress-test audit (sub-item 2) is currently shallow because replayable per-session data is not assembled — `full_context_json` is write-only, interpreter reasoning is not joined to outcomes at query time. Job 2 capture + W-3 join is the prerequisite that unblocks the depth. This reprioritizes Job 2: it is not just "backtest plumbing" — it is the prerequisite for the Performance Auditor doing its real job. Without Job 2, the auditor measures outcome tallies only; with Job 2, it can interrogate each session's reasoning against what actually happened. | owner, 2026-06-07 | GATED — tonight = fire check only; memory + decision-review + research-triggering are month-2+ builds after audit history accumulates. Depth gate: Job 2 capture + W-3 join. |
| 2026-06-10 | **MD-REFACTOR BUILD RULES — VERBATIM EXTRACTION IS NON-NEGOTIABLE (owner, 2026-06-10).** The MD refactor moves WHERE each prompt lives (Python → Markdown), never WHAT it says. **RULE 1 — VERBATIM ONLY:** per-agent procedure: (1) copy the EXACT current Python prompt string into the MD body — zero rewording, zero reorganization, zero "improvements"; section-header nav labels may be added ONLY as navigation aids that sit above the existing rule blocks without moving or altering any rule text; (2) DIFF the MD body against the original Python string (accounting for `\` line-continuation stripping) — prove character-identical content before proceeding; (3) wire the agent to load from MD; (4) run a live session and confirm output is unchanged; (5) ONLY THEN delete the Python constant. One agent at a time. Any reorganization of rule text (e.g. the CC-proposed SA template that moved banned-words into a separate section) is a SEPARATE, later, validated change — NOT part of this refactor. The refactor must not introduce the drift it exists to cure. **RULE 2 — SAFEST AGENT FIRST:** do NOT start with the senior_analyst (most critical, most complex, most stakes). Start with a simple lower-stakes agent (`mtf_interpreter` or `performance_auditor`) to prove the loader mechanism and the verbatim-diff-validate process end-to-end. Migrate the SA only AFTER the process is proven on something low-risk. **NOTE ON CC'S SA TEMPLATE (2026-06-10):** the worked example in the prior design session was a REORGANIZATION, not a verbatim copy — it promoted banned words/time projections out of WRITING RULES into a standalone section and added Role/Inputs sections that don't exist in the original. That reorganization is a future validated change, not part of the initial migration pass. The initial migration pass is purely verbatim. | owner, 2026-06-10 | STANDING BUILD RULE — applies to every agent migration in the MD-refactor; enforce per-agent, no exceptions. **CRITICAL LESSON FROM FIRST MIGRATION (2026-06-10):** manual transcription introduced 36 chars of drift that the diff caught — the `\` line-continuation artifacts (extra spaces at join points) were the culprit. **MANDATORY METHOD:** generate the MD body directly from the Python constant: `python -c "from X import PROMPT; write frontmatter + PROMPT"`. Never transcribe by hand. The verify script (`verify_prompt_mtf.py`) catches both exact mismatches and whitespace-normalization differences — formatting-artifact whitespace differences (multi-space joins) are the only acceptable diff. Any non-whitespace mismatch is a hard stop. |
| 2026-06-10 | **AGENT MD-FILE SPECS — per-agent Markdown job descriptions (owner + friend's suggestion, 2026-06-10).** Idea: extract each agent's job description / responsibilities / rules / banned-behaviors / anti-drift instructions out of its `.py` file into a dedicated Markdown file per agent (e.g. `agents/senior_analyst.md`). **Why it fits Kabroda — three reasons:** (1) directly fights the #1 recurring pain — **agent drift** (false-certainty regression, brief-too-technical) — by making each agent's rules a clean single-source-of-truth doc that is easy to audit and correct without parsing Python; (2) **auditing an agent becomes trivial** — read the MD, compare against live output, edit one file; (3) **de-commingles logic (Python) from job-description (MD)**, matching the existing SYSTEM_FLOW.md / WORK_LOG.md source-of-truth-docs philosophy — the same reason those docs exist. **Key design decision to resolve before building:** does the agent LOAD its MD as its runtime system prompt (powerful — the doc literally IS the behavior; one source of truth that cannot drift from what the agent does) OR is the MD documentation that mirrors the hardcoded Python prompt (safer short-term, but guaranteed to drift over time)? The former is better if done carefully: `agent_core._call_agent()` reads the MD file at call time as the `system_prompt` argument — any edit to the MD takes effect on the next call with no code change. **Pairs with the model-assignment work:** the MD could also declare which model each agent uses (e.g. a `model:` frontmatter field), making model overrides a doc edit rather than a code change. **Scope:** a refactor touching all 9 LLM agents — `senior_analyst`, `junior_analyst`, `mtf_interpreter`, `gravity_interpreter`, `intel_auditor`, `publisher_agent`, `performance_auditor`, `elliott_wave_specialist`, `senior_analyst_commlink`. **Gate:** not urgent enough to jump W-9, but HIGH VALUE and worth doing before the big gated builds (HTF-anticipation, multi-TF) because it serves the core anti-drift/legibility/auditability values those builds will depend on. Do this in the near-term window between W-9 and the expansion tier. **PROGRESS (2026-06-10) — LOADER BUILT, FIRST AGENT WIRED:** `agent_core` gained `load_agent_spec()` + `_call_from_spec()` (reads frontmatter for model/max_tokens; `FileNotFoundError` on missing spec — never silent). `agents/mtf_interpreter.md` generated from Python constant (not transcribed — manual transcription introduced 36-char drift, caught by diff; generate-from-constant is now mandatory). `verify_prompt_mtf.py` confirms character-identical (6592 chars). `mtf_interpreter.py` call site wired to `_call_from_spec()`; Python constant retained pending live confirmation. **SA template noted as REORGANIZATION** — parked as a separate later validated change. **Known wrinkles to handle in rollout:** (1) `kabroda_mas_flow.py` hosts 3 prompts (`senior_analyst`, `senior_analyst_commlink`, `intel_auditor`) — each becomes its own MD, three deletions from one file; (2) retry paths in `senior_analyst` and `publisher_agent` pass a modified context to the same prompt — need a `_call_from_spec_with_prompt()` variant that accepts a pre-loaded spec + modified context so the file is only read once. **Rollout order** (safest first): mtf_interpreter ✓ → gravity_interpreter → junior_analyst → performance_auditor → elliott_wave_specialist → intel_auditor → senior_analyst_commlink → publisher_agent → senior_analyst (last — most critical). | owner + friend, 2026-06-10 | ◐ LOADER PROVEN — `mtf_interpreter` wired + diff-verified; next gate = live session validation, then continue rollout in safest-first order; full rollout gated on W-9 for SA (needs clean outcomes to verify no regression) |
| 2026-06-10 | **AGENT MODEL-ASSIGNMENT AUDIT (owner, 2026-06-10).** Fable 5 launched 2026-06-09 — frontier model above Opus 4.8, strongest on long-horizon + analytical/finance reasoning ($10/$50 per M tokens; free on plans only through June 22, then metered). Owner's question: is each agent on the optimal model for its job? **CORRECT FRAMING — not "upgrade all to Fable":** match each agent to the CHEAPEST model that does its job well, per Principle 1 (clerk vs. judgment). Clerk/mechanical agents (jewel_specialist, structured packaging) → likely Haiku (cheaper, fine). Judgment agents (senior_analyst, junior_analyst, interpreters) → MIGHT benefit from Opus 4.8 or Fable 5, but only if measurable. **HARD DEPENDENCY:** cannot measure whether a stronger model improves the SA's decisions until outcome data is trustworthy (W-9) — otherwise there's no clean signal to A/B against. Also note cost: full pipeline is ~$0.18/day now; Fable would multiply it. **SEQUENCING:** inventory current model-per-agent now (read-only, free — just "what model is each agent using?"). The actual re-assignment/A-B testing is GATED behind W-9 (clean outcome data to measure against). Do the inventory now; defer the changes. | owner, 2026-06-10 | GATED — inventory now (free); re-assignment deferred until W-9 resolved and clean outcome data available |
| 2026-06-07 | **STAND-DOWN GATE OVER-FIRING — FIRST DATA CORROBORATION (auditor finding, 2026-06-07).** The performance auditor's first scheduled run produced a concrete data point for the exit_warning monitoring item: 38 stand-down fires in the trailing window, 70.3% accuracy (correctly avoided bad days), 29.7% overcautious (valid setups missed). This is the first time the "may be overcautious" concern has numbers behind it rather than just a one-day observation. Treat as a STRONG LEAD with two caveats: (1) computed on outcome data with known integrity issues (W-9 — phantom losses + binary R), so the 70.3%/29.7% split is provisional; (2) auditor output was TRUNCATED mid-sentence (600-token limit hit — see W-10), so the full recommendation on this finding was cut off. **Action when W-9 resolved:** re-run the accuracy analysis on clean outcome data and check whether the split changes materially. If the ~30% overcautious rate persists on clean data, this corroborates scoping the exit_warning STRONG+with-trend override (already described in the exit_warning pin). Connects to: exit_warning Suggestion Box pin (2026-06-06) + B1/PMARP parked item. | auditor finding, 2026-06-07 | MONITORING — re-evaluate on clean outcome data after W-9 resolved |
| 2026-06-14 | **NEWS/EVENT CALENDAR AWARENESS — forward weekly read enrichment (2026-06-14).** The HTF STRUCTURAL ANTICIPATION pin captures price-structure anticipation: where are the walls, what level is price approaching, what does a weekly close at a key level mean. It does NOT capture **scheduled macro events** — Fed rate decisions, CPI prints, FOMC minutes, regulatory announcements, major macro dates — "what's on the calendar this week that could override technical structure?" Price at a key level during a quiet week and price at that same level on a CPI print day are structurally identical but practically different. The calendar dimension is distinct from the price-structure dimension, and both belong in the forward weekly read. **Two downstream consumers:** (1) the weekly audit's forward read — "next week has Wednesday CPI; elevated volatility risk, consider reduced-target posture on setups that day"; (2) the publication's weekly narrative — "watch Wednesday's CPI; if above expectations, anticipate volatility at the $X structural level." Discrete addition to the HTF-anticipation cluster (W-14 14c) — plugs into the same weekly narrative output, not a separate build. **Gate:** same as 14c (15M core solid, W-14 cluster unblocked). Also feeds W-4 publication phase. **FIRST CONCRETE EXAMPLE (2026-06-17 — FOMC day):** FOMC today produced exactly the untrackable two-sided chop this feature is meant to flag. Price broke out to ~$66k, reversed, came back to ~$64.5k, chopped flat at ~$64.2k — triggered both sides, resolved neither. The system stood down correctly WITHOUT news awareness (structural read: multi-TF exhaustion + short targets collapsed into wall). But a calendar-aware layer would have explained the chop IN ADVANCE in the brief: "high-impact event today — expect friction, distrust breakouts, be flat before the meeting." The structural logic reached the right answer through a different door; news awareness would have surfaced the REASON rather than leaving it opaque. This is the proof-of-value the pin previously lacked. **RESEARCH (2026-06-17):** Economic-calendar APIs with impact ratings (high/med/low) and structured JSON are widely available and low-cost. Filter: HIGH-impact US events only (FOMC, CPI, NFP, rate decisions) — ignore low/medium noise. Candidate APIs fitting Kabroda's pattern (Python pulls structured JSON): Financial Modeling Prep economic calendar API, Fin2Dev, FXStreet (offers webhooks — subscribe vs. poll), ForexFactory scraper (low-cost). FXStreet webhook model is worth noting: subscribe to HIGH-impact events and receive a push when they appear on the calendar, rather than polling. **Two design surfaces once built:** (a) brief context layer — "event awareness: FOMC today, expect friction" injected into SA context at session open; (b) eventual publication feature — "what's coming this week" narrative block. Surface (a) is buildable standalone without the full publication stack. **Owner trading rule surfaced 06-17:** be OUT of any position before high-impact events hit. The calendar layer would enforce this rule at the system level (flag it in the brief before entry, not after). | expanding W-14 / 14c scope analysis (2026-06-14); enriched with first concrete example + research 2026-06-17 | TBD — add to 14c scope when W-14 cluster graduates from GATED; also feeds W-4. Gate unchanged: strengthening-phase, gated behind 15M-solid + brief/publication infra. NOT building now — evaluation phase. Pin enrichment only. |
| 2026-06-14 | **AUDITOR THIN-DATA LEGIBILITY BUG — near-term, not gated (2026-06-14).** This week's audit reported "0% directional accuracy / every configuration failed" when the correct read was: zero closed trades, nothing resolved yet, metric has no denominator. The auditor conflates "no resolved data" with "0% / everything failed." **Distinct from the existing "provisional numbers due to W-9 integrity" caveat** — that caveat is about data quality on resolved outcomes; this is about generating false failure signal from an empty dataset. **Correct behavior:** when resolved outcome count is zero (or below a meaningful minimum), do NOT render a 0% accuracy figure — report "INSUFFICIENT DATA — N resolved sessions, metric not yet computable." A 0% that reads as systemic failure makes every thin-data week's audit cry wolf. **Scope:** `performance_auditor.py` prompt or pre-check logic — if `resolved_outcomes == 0` (or below threshold), substitute "INSUFFICIENT DATA" text in the accuracy blocks. No schema changes, no new tables. Not gated behind W-3 or Job 2. **Priority:** near-term — fix before next Sunday's scheduled run. | this week's auditor output (2026-06-14) | FIX SOON — one session, no dependencies; before next Sunday's scheduled run |
| 2026-06-16 | **STAND-DOWN RE-ARM ALERTER — multi-condition confluence alert for latent stand-down setups (owner, 2026-06-16).** On days where the brief identifies a specific transition that would flip the session tradeable (e.g. 2026-06-16: "becomes tradeable when 15M flips TANGLED→PRIMED with ribbon >0.40% AND 1H momentum turns POSITIVE"), the system watches for those conditions to converge and sends a notification (email) so the owner doesn't have to babysit the screen. **Critical design principle — must NOT cry wolf.** It watches the WHOLE picture converging (momentum shift + kinematic load + approach to trigger), not a single "price crossed breakout" event. A false "winding up… just kidding" alert trains the owner to ignore it — worse than no alert. **Research-backed design (prior art 2026-06-16):** established "multi-condition / confluence alert" pattern. Key principles: (1) fire on BAR CLOSE, not intrabar — avoids premature signals on incomplete bars; (2) 3–5 mandatory conditions (all must be true) + optional filters to strengthen confidence; avoid alert fatigue with too many triggers; (3) balance quickness vs. precision — too loose = ignored, too late = useless. **Kabroda-specific design:** ARMS only on days the brief flags latent setup potential — NOT every day. The brief's own "WHAT WOULD CHANGE THIS" section defines the arm conditions. Bounded to ENTRY WINDOW: 8–11 AM CST (9 AM–12 PM ET). After noon ET the setup is stale; alerter goes quiet. Fire condition = the brief's stated transition conditions converging on bar close. **Dependencies / gates:** (1) 15M core proven solid across many live sessions — same gate as W-14; the alerter is only as trustworthy as the conditions it watches. (2) Notification / email infrastructure does not exist yet (W-4 territory — DRAFT is currently the terminal state, no email pipeline). (3) Connects to: W-14 strengthening cluster; stateful Intel Auditor pin (similar carry-condition-in-memory pattern); W-4 publication/delivery infra. **Confirmed high owner interest:** "impactful, definitely something we need to do" — but build on validated foundations, not as a patch. | owner, 2026-06-16 stand-down session — system identified conditions for flip but no mechanism to alert when they arrived | ☐ GATED — behind 15M-solid + notification infra (W-4). High priority in expansion tier. |
| 2026-06-15 | **ENERGY/LEVEL TIME-COHERENCE GAP — data-integrity finding, fix not yet scoped (2026-06-15).** Read-only trace of `battlebox_pipeline.get_live_battlebox()` confirmed the following: the MAS pipeline fires on the FIRST PAGE-VISIT after lock time — there is no auto-scheduler that fires `run_mas_analysis()` at 9:30 AM ET. **Two-timestamp problem:** the breakout/breakdown LEVELS are always correctly bounded to the 9:00–9:30 AM calibration window (filtered in `_compute_sse_packet()` as `int(c["time"]) < lock_end_ts`). BUT the energy reads — `fuel_gauge` (RSI, MACD, EMA trends, ADX, JEWEL on 1H/4H/15M), `micro_state`, `1h_fuel_status`, `kde_peaks`, `macro_environment` (SPX/DXY/VIX) — are sampled FRESH at whatever wall-clock moment `get_live_battlebox()` first executes after lock time. Once the packet is created and persisted to `session_locks`, both the levels AND these energy reads are frozen. But they were sampled at different moments. **The coherence gap:** if the first page-visit is ~9:31 AM, levels and energy are approximately coherent. If the first page-visit is 10:00 AM, the agents analyze 9:30 AM level geometry against 10:00 AM momentum data — a 30-minute mismatch with NO warning flag in the brief or DB. The brief reads internally consistent but stitches two timestamps. Concrete example: RSI 55 at lock → 64 at 10:00; agents see "PRIMED / fuel confirms" on data the trade levels never saw. **Same integrity-risk family as the phantom-loss bug (W-9):** both look coherent from the outside; inputs are silently mismatched. **Timestamp facts traced:** no `analysis_fired_at` field exists anywhere; `CampaignLog.created_at` = wall-clock at write (fire time + LLM latency, not lock time); `battlebox.lock_time` holds the correct 9:30 AM unix timestamp; the gap IS reconstructable by comparing `CampaignLog.created_at` against `battlebox.lock_time` stored in `full_context_json`, but nothing in the code detects or flags it. **Candidate fixes (do NOT choose yet — scope later):** (1) **Freeze energy reads at lock time** — sample all data at 9:30 AM so levels and energy are always co-sampled; brief is always "as of lock." Fully coherent, requires a scheduled 9:30 AM data snapshot. (2) **Auto-fire the pipeline at lock time via scheduler** — don't wait for a page-visit; fire `get_live_battlebox()` (and therefore `run_mas_analysis()`) at `lock_end_ts` on a background task. Fully coherent AND solves the "brief should be waiting for me when I arrive" UX problem the owner has noted. **Likely the highest-value option — one change, two wins.** Connects directly to W-12: W-12 confirmed the 14:00 UTC scheduler is a fallback (page-visit always races ahead); the fix would promote the 9:30 lock-fire to the primary trigger, not a fallback. (3) **Stamp + flag the gap** — label the energy reads with their actual sample timestamp in the context string; let agents and the brief note "energy reads sampled N minutes after lock." Cosmetic mitigation, not a fix. (4) **Late-arrival awareness** — detect a late fire (compare `now_utc` to `lock_end_ts` at packet creation time) and surface a warning in the brief: "NOTE: energy snapshot is N minutes post-lock." Same class as (3). **Connections:** W-12 (page-visit-triggers-first is the same root mechanism; the W-12 scheduler is the structural hook the fix would use); the owner's recurring observation that "the brief isn't waiting for me when I arrive" is the user-facing symptom of this same root cause. | read-only architecture + timing trace (2026-06-15) | ☑ SHIPPED (commit `d9a4a92`, 2026-06-15) — Option 2 chosen: `_seconds_until_lock_end()` added to `main.py`; scheduler fires at DST-aware `lock_end_ts` from `session_manager`. Boot check uses `now.timestamp() >= _boot_lock_end_ts`. `date_key` from `session["date_key"]`. Page-visit double-fire guard unchanged. **Verification checkpoint: tomorrow 9:00 AM ET — brief should be waiting on arrival.** |
| 2026-06-18 | **SSE-INTO-TSA TARGET WIRING — high-value structural fix, gated (2026-06-18).** The SSE engine produces `daily_support` and `daily_resistance` levels (from VRVP / daily structure). These go into the SA's narrative context string as named text only — they are NEVER seen by the Trade Structure Analyst (`trade_structure_analyst.py`), which only scans `kde_peaks` (gravity KDE) when adjusting targets. A daily support or resistance sitting inside the measured-move path is invisible to the target-snapping math. **Today's concrete proof-of-need (2026-06-18 SHORT):** SSE `daily_support` ~63,700 sits 34% of the way from entry (64,121.60) to T1 (62,883.31) — directly in the path — and T1 was set through it without any wall-snap or warning flag. TSA's `_snap_short()` / `_snap_long()` only intercept HEAVY/MAXIMUM KDE peaks between entry and target; SSE levels are a completely separate data stream that never enters the TSA. **Proposed fix (two options — scope when flat):** (a) snap T1/T2/T3 to the nearest SSE level when one falls within N% of the Fibonacci target (same intercept logic as KDE snapping — adds to `_snap_short()`/`_snap_long()`); (b) lighter first step: inject SSE S/R as a warning flag in `structure_notes` when a level falls between entry and T1. Option (b) has zero risk of moving targets incorrectly and gives the SA/owner the information to decide. **Key code fact:** `daily_support`/`daily_resistance` are already in the `levels` dict passed to `apply_trade_structure()` in `trade_structure_analyst.py` — the data is THERE, just not read. No schema changes needed. **Connects to:** W-14 multi-TF structural anticipation cluster (the SSE-gravity integration gap is the same structural-awareness gap the HTF layer is meant to address); SSOT principle (a level the SA calls by name in the brief but that the target math ignores is a coherence failure). | confirmed by full code trace during 2026-06-18 target-logic audit | ☐ GATED — scope and build when flat. High-value, data already in pipeline. Option (b) as first step; option (a) as follow-on if (b) validates. |
| 2026-06-18 | **UI SOURCE-OF-TRUTH UNIFICATION — radar independent scoring vs SA authority (2026-06-18).** Full divergence map completed (read-only, active trade). **Authority principle (established by owner, 2026-06-18):** agents/SA CampaignLog = single source of truth post-lock; the UI displays that verdict uniformly everywhere; the market-radar layer's independent scoring is demoted to pre-lock pre-check + auditor background data only — never a competing verdict against the SA. **Confirmed divergence surfaces (all rooted in `market_radar.html` + `market_radar.py`):** (1) **HUD DATA MISSING (Tier 1, ~12 lines)** — `renderSnapshotGrid` hardcodes `key: ''` (line 948); Phase 2 `scan_sector()` overwrites with radar's own key, but Phase 1 never builds `key` from CampaignLog data → HUD shows "-- DATA MISSING --" until radar scan completes. Fix: build `key` from CampaignLog fields in Phase 1 when `snap.mas_status === 'APPROVED'`. (2) **Panel 02 temporal label (Tier 1, ~3 lines)** — when SA is APPROVED and JEWEL later closes, `loadPanel02Intel` `else` branch shows "GATE CLOSED — STAND DOWN" — true for live JEWEL state but misleading as a trade-management directive (SA decision was made on an open gate). Fix: when `masStatus === 'APPROVED'`, show "SA APPROVED — JEWEL POST-LOCK CLOSED" not "GATE CLOSED — STAND DOWN." (3) **DecisionJournal write contamination (Tier 4) — CLOSED (W-11, 2026-06-13).** Code trace 2026-06-19 confirmed: `market_radar.py` writes `source="market_radar"`; `mas_flow` writes `source="mas_flow"` with correct APPROVED→MAS_APPROVED mapping; `performance_auditor.py` filters `source == "mas_flow"` only. Radar STAND_DOWN rows are permanently invisible to the auditor. No build needed. (4) War Room JEWEL bar (live) vs tactical text (lock-time) can show contradictory state side-by-side on same page (Tier 2 — temporal labeling, lower priority). Tier 3 (store lock-time JEWEL state in CampaignLog) — defer. **Also connects to in-site SA chat pin (2026-06-18):** the SA chat is another surface presenting lock-time data as live-authoritative — same theme, different surface. | full divergence map completed 2026-06-18 read-only investigation; owner authority principle established | ☑ **Tier 1 BUILT (commit `eecc6ae`, 2026-06-19):** HUD key sourced from CampaignLog on APPROVED sessions (Phase 1 `renderSnapshotGrid` + Phase 2 merge guard). Panel 02 label: APPROVED+JEWEL-closed now shows "JEWEL GATE CLOSED" (amber) not "GATE CLOSED — STAND DOWN." **Tier 4: CLOSED** (W-11 already resolved — source filter isolates radar rows from auditor). **NOTE:** HUD key is 7 fields (`SA_APPROVED` grade label) vs 9-field radar key — unverified against TradingView Pine parser; confirm on next approved-day copy test; pad tail with `\|\|` if paste breaks. Tier 2 next pass. Tier 3 defer. |
| 2026-06-18 | **LIVE EXHAUSTION MONITOR — in-trade runner exit-assist, highest owner-interest live feature (2026-06-18).** Distinct from both the stand-down re-arm alerter (pre-entry, watches for confluence to form) and the W-9 OHLC live trade monitor (watches price vs. stop/T1/T2/T3 levels). This is specifically the "is the move getting tired?" read for a runner held in an active position. **The problem it solves:** once inside a trade with 40% banked at T1 and 60% held for T2/T3, the owner currently has zero system guidance on when to bank the runner. The decision is entirely manual and emotion-driven: "hold for T2/T3" vs. "bank now before it bounces back to breakeven/stop." This is where trade emotion lives — the scenario where a trader banks 40%, holds the runner, watches a grind-back chop eat the profit on the runner, and ends up with a $20 net after fees on a correct call. Today (2026-06-18): owner manually agonized over the 60% runner overnight with no system read available; the in-site SA was blind to live price entirely. **What the monitor needs to flag:** (a) 15M timeframe showing reversal signals (RSI turning, ribbon crossing, momentum fading); (b) 1H/4H exhaustion (ADX declining from a peak, MACD histogram rolling, RSI overbought/oversold on the higher TF); (c) price stalling at or inside a named gravity wall (KDE peak between current price and T2). When two or more of these converge, alert: "move showing exhaustion — consider banking runner." **Foundation already exists:** W-9 OHLC live trade monitor already reads live candles from MEXC every 60s and knows trade state (entry, T1, T2, T3, open size). This extends it to read exhaustion indicators from the same data stream and add an alert condition. No new data source — new computation layer on existing infrastructure. **Connects to:** stand-down re-arm alerter pin (2026-06-16) — same notification infrastructure (W-4); in-site SA chat fix (a live-state feed from the ledger monitor could eventually power a genuine SA response); W-9 Phase 2 (runner-state tracking is the prerequisite). **Gates:** 15M core proven solid + notification infra (W-4) + W-9 Phase 2 confirmed working. Flag as highest owner-interest feature when expansion scope opens. | owner manually agonized over runner exit with no system read available (2026-06-18); in-site SA blind | ☐ GATED — 15M solid + W-4 notification infra + W-9 Phase 2 confirmed. Highest owner-interest live feature. Foundation in W-9 OHLC engine. |
| 2026-06-18 | **ENTRY MECHANICS — unresolved, settled by data NOT a fixed rule (2026-06-18).** Three entry methods, each with an opposite failure mode. **(1) Trigger/stop order at the breakdown level** (placed before the breakdown fires): never misses the move; risk = filled on a fake-out wick that reverses to stop. **(2) Wait for 15M candle close below breakdown, then limit-on-retest at the breakdown level**: best price + confirmation; risk = retest never comes, trade is missed entirely — the "too greedy" trap. Missing a correctly-called trade is arguably the worst outcome. **(3) Wait for 15M close below breakdown, then enter at-market near the close price**: confirmation + actually gets in; risk = worse entry price, slightly wider effective stop, worse R:R. **Owner's key insight:** method 2's failure mode is catastrophic — price often breaks down and runs without retesting, or pulls back partway, exhausts, and continues to target with the limit unfilled. Do NOT treat "wait for close then limit at breakdown" as the system's rule. **What actually happened today (2026-06-18 data point):** owner placed a trigger order ~08:08 CST before the breakdown; filled on the break; ate ~45 min of retest chop before price ran. A wait-for-15M-close entry WOULD have filled on the retest (method 2 would have worked today). One data point. **Resolution path — empirical tracking, not dogma:** entry behavior becomes a TRACKED VARIABLE in the evaluation log. For each approved trade, log: (a) did price retest the trigger level after the 15M close, or run without looking back? (b) how far did it pull back before continuing? (c) which entry method would have yielded the cleanest fill? After several approved trades, the pattern tells us what method fits this system's setups. **System gap flagged:** Kabroda calls the setup (levels + acceptance gate) but does not model entry execution. The 2-consecutive-closes acceptance gate confirms the move is real, but it does NOT specify whether to enter at market on the close or wait for a retest limit. A live breakdown-candle monitor (related to the live exhaustion monitor) could eventually advise "closed below + retesting now — fill here" vs. "closed below and running — enter at market now." Gated future item; requires several sessions of entry-behavior data first. | owner entered via trigger order, ate retest chop (2026-06-18); system design clarity gap surfaced | ☐ TRACKING — log entry behavior per approved trade in evaluation log. No build yet. System-guidance scope gated behind several sessions of empirical data + live breakdown-candle monitor design. |
| 2026-07-01 | **WEEKLY/DAILY RSI DIVERGENCE FOR NARRATIVE — publication-only, not trade-engine (bold-hubble audit, 2026-07-01).** Second bold-hubble audit pass specifically scoped to "does anything here belong in the publication/content layer, not the trade engine" (owner reframe: macro/Class 0 levels are context/bias for the trade engine, never targets — the same logic applies to multi-week/month indicators; none of this belongs in 15M/1H/4H entries/stops/targets). Three candidates surfaced; only this one recommended. **The gap:** the narrative brief already asserts claims like "Weekly timeframe is flashing bullish divergence" as prose (the SA's read) with no computed value behind it — a credibility risk given `publisher_crew.py`'s own editorial standard ("conviction comes from structural evidence, not enthusiasm"). `bold-hubble/indicators/rsi_divergence.py` has a complete, working `detect_rsi_divergences()` (regular + hidden, bullish + bearish, ~150 lines, zero dependencies) sitting unused. Kabroda already has an equivalent pivot-finder (`_scan_for_pivots()` in `gravity_engine.py`) so the underlying primitive isn't even new. **Scope recommendation:** reference-computed narrative fact only — run against weekly/daily candles already fetched elsewhere (weekly 200 SMA, daily 21 EMA), pass one more labeled value into whatever context builder feeds `macro_bias` today. Zero touch to trade engine (no gating, no target math). **Effort estimate:** small, well under a session. | bold-hubble audit round 2, publication-scope reframe (owner, 2026-07-01) | TBD — worth doing when a content/publication session is scheduled; not competing with open trade-engine work (v3 confirmation, email-trigger live proof, 1H VWAP/session-cap) |
| 2026-07-01 | **DOMINANT-TREND CLASSIFIER — cheap bundle-on, not standalone (bold-hubble audit, 2026-07-01).** `bold-hubble/indicators/trend_volatility.py` has `evaluate_dominant_trend()` — classical HH/HL/LH/LL swing-structure + MA-alignment trend read, producing a −100/+100 score and a 5-tier regime label. Independent lens from Kabroda's existing trend reads (EMA-slope `daily_21ema_direction`, Elliott Wave macro engine). Individually medium-to-low value — narrative already synthesizes multiple signals, unclear how often a fourth read would actually disagree with what's already said rather than just being redundant. **Only worth building bundled with the RSI-divergence item above** — same weekly/daily candle fetch, same pivot-detection style, ~30 extra lines once that plumbing exists. Not worth its own standalone effort. | bold-hubble audit round 2 (owner, 2026-07-01) | TBD — bundle with weekly/daily RSI divergence item only; not standalone |
| 2026-07-01 | **RATE OF CHANGE / FIB EXTENSIONS LECTURES — likely skip, transcript friction confirmed (bold-hubble audit, 2026-07-01).** Two Crown course lectures ("Rate Of Change - Generating Price Targets," "Price Discovery & Generating Targets Via Fib Extensions") reference multi-week target-generation methodology, but were never transcribed — confirmed no `.vtt` files exist anywhere in `bold-hubble/`, `download_hls_vtt.py` exists as capability only, never run. The Fib-extension half is likely already redundant: `gravity_math.py`'s `calculate_macro_fibs()` already computes 30-day swing high/low with standard 1.272/1.618/2.0 extensions both directions — open question is only whether that existing output is surfaced in the newsletter prose today (a 2-minute check, not a build). The Rate-of-Change half is a genuine unknown — title only, can't evaluate without the transcript. **Friction flagged:** `download_hls_vtt.py` needs a raw `.m3u8` stream URL, not the Teachable lecture-page URL in `course_map.json` — getting the real stream URL requires being logged into the course and pulling it from browser devtools network tab per lecture, not a one-command run against known URLs. **Recommendation:** skip pulling the transcript; check first whether `calculate_macro_fibs()` output is already in the newsletter (cheap), and only revisit ROC if that check reveals a real gap. | bold-hubble audit round 2 (owner, 2026-07-01) | LIKELY SKIP — check calculate_macro_fibs() newsletter surfacing first (cheap); transcript pull is real effort (manual per-lecture URL extraction), only worth it if the cheap check reveals a gap |
| 2026-07-06 | **KULTI LONG-TERM INVESTING MODULE — new bold-hubble course, owner wants built into kabroda.com eventually, explicitly deferred until current punch-list cleanup finishes.** `bold-hubble/krown_courses/kulti/` (7 modules, Eric Crown's "Ultimate Long Term Investing Beginner Program") — a multi-decade buy-and-hold BTC/Gold/SPX/QQQ framework: 6 mandatory asset criteria, 4-phase cycle mechanics (Accumulation/Markup/Distribution/Markdown), an 11-component signal-stacking conviction system (BBWP, RSI, PMARP, Krown Cross 21/55 EMA, Hash Ribbons, Pesto F&G, percent-below-high, weekly/bi-weekly EMA, low-month-days, moon phases, strong-buy meta-filter), and a "One-Page Protocol" with three personal rules (no leverage on long-term holds, monthly-audit/quarterly-rebalance vs. SPX, never-fully-out 5% cash floor). Verified independently (not just trusted the other team's audit report, given the earlier Revin Ribbons false-claim incident) — ran `tests/verify_course_extraction.py` myself, genuinely passes, 47 files across 12 courses confirmed clean. **Philosophically distinct from Kabroda's core product**: multi-decade/monthly-cadence buy-and-hold vs. Kabroda's session-based 15M-4H active trading — should be its own module/page with its own conviction-scoring table, not folded into the existing trade-construction pipeline. **Feasibility check (informal, not yet scoped):** most ingredients already exist in Kabroda (BBWP/PMARP/RSI/EMA-series in `battlebox_pipeline.py`, 1500-day daily BTC history already fetched by `kabroda_macro_engine.py`, just needs weekly/monthly resampling) — but Hash Ribbons (on-chain miner data) and Pesto F&G (third-party sentiment index) need new external data sources not currently wired in; moon phases/low-month-days are cheap but low-rigor. **Owner directive: not now — finish and confirm everything currently in progress first.** Revisit scoping (which of the 11 components are buildable today vs. need new APIs) once the current punch-list work is clean. | owner shared the course + asked directly whether it could run inside kabroda.com (2026-07-06) | PARKED — owner wants this built eventually as a real feature, explicitly not started; revisit after current cleanup finishes |
| 2026-06-18 | **IN-SITE SA CHAT — confident live directives off stale lock-time data (confirmed finding, 2026-06-18).** Same class of problem as Panel 02 / radar-grade / DecisionJournal: a surface presenting stale/secondary data as authoritative and live. Owner asked the in-site SA chat for live trade-management guidance mid-afternoon on an active position. The SA confidently said "price is 5 minutes from tagging T1, do not close early, hold for T1" — when T1 had already tagged hours earlier and the owner had already taken 40% off at T1. The SA was reading the frozen lock-time brief (entry/T1-not-yet-hit state) and speaking with live-advisor confidence. When corrected, it admitted: "I operate exclusively on the Market Brief. I have no live feed. I cannot tell you where price is now." **Why this is dangerous:** the directive language ("hold," "do not close early," "let the math complete") sounds like a live advisor. The SA has zero knowledge of live price, partial fills, time elapsed since lock, or trade progress. Confidently giving live trade-management advice off a stale morning brief — for a position that is already partially or fully resolved — is actively misleading for an in-trade decision. Worse than silence. **Two-stage fix:** (a) **Near-term prompt addition (build any session, no dependencies):** when the in-site SA detects a live-management question (hold/close/exit/price-now framing), it explicitly refuses and caveats: "I have no live price feed. I can only discuss the lock-time brief. For current price relative to targets, check the live monitor or the cockpit." Single addition to the SA system prompt or operator commlink instructions. (b) **Longer-term architecture:** the W-9 Phase 1/2 ledger-closing engine already fetches live prices from MEXC every 60s and knows trade state (entry filled, T1 hit, trailing). A genuine live-management read could be piped from that state. Requires a new commlink context-injection path — Phase 2 scope. **Connects to:** UI source-of-truth unification pin (2026-06-18) — the same principle applies to chat as to panels: every surface should know what it knows, say so, and refuse questions it can't answer honestly. | owner asked in-trade for live management guidance, received confidently wrong answer (2026-06-18) | ☑ Part (a) BUILT (commit `eecc6ae`, 2026-06-19): `COMMLINK_SYSTEM_PROMPT` now opens with explicit scope declaration — operates from lock-time brief only, no live price feed; SA must state its limitation and defer live trade-management questions rather than give confident directives off stale data. Prompt-string only, no capability added. Part (b) (live-state feed from ledger engine): gated behind W-9 Phase 2 proven + ledger state API design. The big live-analysis SA capability remains a separate gated future item (live exhaustion monitor pin). |

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
| 2026-06-05 | INTEL REPORTER CoinGecko 429 — **recurring reliability item (Jun5/6/7 — 3 consecutive days, CONFIRMED PERSISTENT)** | Fired Jun5, Jun6, AND Jun7 — three consecutive days confirms this is a persistent rate-limit, not a transient spike. Brief survived all three days (fallback/cache fired each time). Fix needed before publication phase: ensure intel reporter has a graceful fallback AND a logged warning when CoinGecko rate-limits so sentiment data doesn't silently vanish. **Priority: publication blocker** — must be resolved before any public launch. Pin for publication phase. |
| 2026-06-16 | ENTRY WINDOW — settled parameter (owner, 2026-06-16) | Fresh entry is valid from session open to **~11:00 AM CST (12:00 PM ET / noon)**. After noon ET, the calibration window is several hours stale and the setup is too old for a clean entry — the RE-ARM ALERTER (Suggestion Box 2026-06-16) goes quiet at that point regardless of confluence state. **Note for Phase 1:** the current lifecycle monitor expires unfilled trades at `session_expires_at` (3:00 PM ET), not noon ET — the entry window is a policy parameter not yet enforced as the Phase 1 cutoff. Future refinement: tighten Phase 1 expiry to noon ET. Filled trades are unaffected — once `entry_filled_at` is set, Phase 2 runs to stop/target/next-session-open regardless of time (W-9 Phase 2 fix, 2026-06-16). |
| 2026-06-04 | EDUCATIONAL FRAMING — design principle for all public/paid output (owner, 2026-06-04) | Everything published or sold is framed as EDUCATIONAL / opinion / "this is what we see" — never as financial advice, never with claims about profit or returns. Users make their own decisions and interpretations. Standard disclaimer language (not financial advice, educational purposes, our opinion, trade at your own discretion) on all public-facing material. CRITICAL CAVEAT: the disclaimer is necessary but NOT sufficient — regulators judge substance, not just the label. Publishing specific entry/stop/target levels + performance stats + charging can read as a signal service regardless of disclaimer. The framing AND the format must be designed together. HARD GATE (already pinned): a qualified securities/financial-services attorney must review the actual framing, format, disclaimers, and performance presentation for the owner's jurisdiction (US/TX) and subscriber base BEFORE any public launch or paid subscription. "Other sites do it this way" is not a compliance basis. Claude is not a lawyer and cannot adjudicate this. |
