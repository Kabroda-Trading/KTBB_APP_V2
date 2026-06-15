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

## ► NEXT SESSION START
*End-of-session marker: 2026-06-15*

**2026-06-15 — Read-only investigations + behavioral fix: architecture honest-picture, time-coherence gap found + shipped**

**Confirmed today:**
- **MAS architecture is a fixed sequential pipeline, not an orchestrator** (read-only, no code touched) — `run_mas_analysis()` fires each module in a hardcoded Python call order: TSA → DB reads → MTF interpreter → gravity interpreter → junior analyst → context assembly → SA → DB writes → publisher. No LLM decides routing. No feedback loops. The SA is the terminal stage; it receives a pre-assembled frozen string and produces JSON. The one "retry" is a JSON FORMAT correction (append "[CORRECTION: return valid JSON]" + re-call), not a semantic re-evaluation. The Senior Analyst is not a conductor; Python is. Full report delivered to owner.
- **ENERGY/LEVEL TIME-COHERENCE GAP — found, scoped, and shipped (commit `d9a4a92`)** — Option 2 chosen: `main.py` scheduler now fires at `lock_end_ts` instead of a hardcoded 14:00 UTC. DST-aware `_seconds_until_lock_end()` helper uses `session_manager` pytz logic (EDT: 13:00 UTC, EST: 14:00 UTC). Boot-time check uses `now.timestamp() >= _boot_lock_end_ts` (not `now.hour >= 14`). `date_key` comes from `session["date_key"]` in both paths. Page-visit double-fire guard is unchanged — `_CACHE_LOCK` + existing lock in DB prevents re-fire. **Verification checkpoint: tomorrow 9:00 AM ET — brief should be sitting there on arrival, not triggered by page-visit.** Suggestion Box 2026-06-15 pin marked SHIPPED; W-12 status updated (scheduler now PRIMARY trigger).

**Carry forward:**
2. **[W-9 PASSIVE]** Forward verification only: next real no-fill APPROVED session must run through Phase 1 → EXPIRED/pnl=null correctly. Cannot be forced.
3. **[BUG]** Intel Reporter: CoinGecko 429 — not recurred on 06-13; continue to monitor.
4. **[COSMETIC]** Cumulative performance chart x-axis out of chronological order (values correct, sort wrong).
5. **[BOARD REVIEW]** 15M core status: W-6, W-7 Fix 3, B1, W-10, W-1 — true current state reported in Part 2 read-only pass (2026-06-14). This is the menu for what to work on next.
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

### W-9 ☑ OUTCOME-TRACKING DATA INTEGRITY — FUNCTIONALLY CLOSED (2026-06-14)

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

- **Status:** ☑ Steps 1–5 DONE. W-9 is functionally closed. Only remaining item is passive forward verification: the next real no-fill APPROVED session must be observed running through Phase 1 and correctly stamping `EXPIRED / realized_pnl=null`. Cannot be forced — confirms itself on the next untriggered setup.
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
| 2026-06-05 | **SSE LEAN — FUTURE ADX-GATING ENHANCEMENT (owner, 2026-06-05).** Owner history: the SSE lean is strong/accurate in clean trends but gets fooled in chop (calls continuation while price is actually bottoming/bouncing). The corrected ADX is the tool to fix this: eventually gate the lean's own confidence by ADX — high ADX = lean in its reliable zone, weight normally; low/falling ADX = choppy, discount the lean's directional call. This addresses the lean's known weakness at its source. Build as an SSE-engine refinement AFTER the bias_model wiring is proven live. Ties to the ADX fix (W-7). | owner decision, bias_model wiring session 2026-06-05 | TBD — after bias_model proven live; build in sse_engine.py, ties to W-7 |
| 2026-06-06 | **FEED LIVE INTERPRETER OUTPUT BACK TO CC WHEN TUNING AGENTS (owner, 2026-06-06).** CC tunes agent prompts from the code (intent), but hasn't seen what the agents actually PRODUCE live. When working on any agent's prompt, paste its real output from `/admin/interpreter-log` so CC tunes against reality, not just intended behavior. Closes a blind spot: prompt intent ≠ prompt output — the two can diverge silently over sessions. Concrete step: before any prompt edit, pull the last 2–3 live outputs for that agent from the interpreter log and include them in the session context. | owner, A3 scope session 2026-06-06 | TBD — adopt as standing practice from next agent-prompt session |
| 2026-06-06 | **AUDIT TOOLING AS PERMANENT SITE FEATURE (owner, 2026-06-06).** The verify-before-build loop keeps finding things the static audit missed (allocation rule, BBWP data-path). Make audit / stress-test capability on-demand in the app — e.g. replay-harness-on-demand view, interpreter-output history with per-agent diff. So checking flow-through is a button, not a rebuild. Extends the existing `/admin/interpreter-log` page + cost monitor (already built). **GATED:** build after A3 + B1 are done — this is tooling, downstream of fixing the actual signal flow. | owner, A3 scope session 2026-06-06 | TBD — after A3 + B1 live; extend /admin/interpreter-log |
| 2026-06-06 | **AUDIT IS A LOOP, NOT A SNAPSHOT (owner, 2026-06-06).** Lesson confirmed across W-8: a one-time audit gives leads; verifying each finding while building surfaces the next layer (the allocation rule was invisible until A3 scoping; BBWP absence was a false assumption). This is healthy, not a failure — the audit list is a STARTING POINT, not a complete inventory. Keep the verify-first protocol on every remaining finding. Don't trust the audit list as exhaustive before building. | owner, end of A3 scope session 2026-06-06 | Standing protocol — not a build item |
| 2026-06-06 | **STAND-DOWN BRIEF — INTERNAL vs PUBLIC are different products (owner, 2026-06-06).** Internal brief on a no-trade day CAN be terse — the trader needs "no action, here's why" and no narrative is required. But the PUBLIC publication on a stand-down day must NOT be cut down: it should explain what is happening in the market (e.g. price at a major support floor, stop-hunt/chop dynamics, why both sides are positioning), AND must ALWAYS include a forward-looking / higher-timeframe section ("what to watch next"). A no-trade day is when a reader most wants the "what's going on?" read. Publisher-agent needs a STAND-DOWN TEMPLATE distinct from the internal one — different prompt path, different output structure. This is the differentiator between a real publication and a signal feed. Publisher prompt tuning, publication phase — NOT now. | owner, Jun6 STAND_DOWN session 2026-06-06 | TBD — publication phase |
| 2026-06-06 | **B1 PMARP — MONITORING + DESIGN ITEM, NOT a build now (owner, 2026-06-06).** Bug confirmed: `pmarp_overextended = rank > 75` is blind to downside extremes (Jun2 rank=0.00, pmarp_overextended=False). BUT: naive `rank < 25` fix fires on ALL 8 sessions in current dataset — the 252-bar 4H history is entirely inside the downtrend, so every session reads rank < 25. This fix would re-create an always-on T1 cap (exactly what A3 removed from MACD). Even rank < 5 fires on Jun2/Jun3 (A3-unblocked sessions). Full fix is A3-class: (1) threshold decision, (2) STRONG+with-trend override (same override deferred for exit_warning), (3) MTF interpreter prompt to distinguish downside extreme on SHORT vs LONG. Data is currently unfit for threshold calibration (one-sided history). Build gate: market ranges/rallies → balanced PMARP history AND exit_warning override is scoped (B1 shares that layer). Verify-first protocol prevented a fix that would have re-broken A3. | owner, B1 verification session 2026-06-06 | DO NOT build — monitoring only; build gate: balanced market + exit_warning override scoped |
| 2026-06-06 | **HIGHER-TIMEFRAME STRUCTURAL ANTICIPATION — major capability project, GATED (owner, 2026-06-06).** The system reads intraday structure well but does NOT anticipate the bigger board. It reacts to major structural levels (e.g. the $60K MAXIMUM wall) as price ARRIVES, not days ahead. Owner's vision: the system should call out major higher-timeframe support/resistance levels 3–4 days BEFORE price reaches them, so price stalling/chopping/bouncing at those levels is EXPECTED rather than surprising — and so a trade that fires AT a pre-flagged level (e.g. a short rejecting a zone already called) is recognized as higher-probability because the interaction was anticipated, not just reactively matched. Reference model: Mafioso 8H signal calls forward pullback targets (T1/T2/T3 on the way UP) and likely rejection zones ahead of time. Kabroda's gravity map already knows WHERE the walls are but does not narrate the JOURNEY toward them or anticipate interaction. Two open design questions before scoping: (1) Should the gravity map be enriched with liquidity/order-book data to strengthen the structural read, or kept as the higher-timeframe structural map it is and connected to a new anticipation layer above it? (2) How does anticipated-level-interaction feed the trade decision — does a setup firing AT a pre-flagged level earn a higher allocation tier, a stronger SA conviction label, or a different target structure? Likely connects to the parked B1 (PMARP extreme = "we're AT the wall now") and the SA higher-timeframe narrative gap. **BUILD GATE:** front-of-river solid (done) AND A3 confirmed live across varied sessions. Own scoped project when gates clear. **CONCRETE REFERENCE EXAMPLE — Mafioso 4H call at the $60K floor (2026-06-06):** At the identical juncture where Kabroda stood down (15M tangled, choked target at $60,025.76 MAXIMUM wall), Mafioso issued a LONG bounce call with forward targets 63,850 / 66,504 / 66,701 and a stop on 4H-close-below 59,617. Both systems recognized the $60K floor as the pivotal decision point; they resolved it differently — Kabroda: stand down, bearish bias intact; Mafioso: long bounce. What Mafioso did that Kabroda cannot: mapped the full BOUNCE PATH forward — specific levels where price would likely stall, reject, and potentially set up a high-probability re-entry short. This is the anticipation-narration gap in concrete form. "Kabroda knows where the walls are but doesn't say: if we bounce, here's where we'd stall and reject, and that rejection is a high-prob short setup." CAVEATS (owner's framework — do not blur): (1) Mafioso's long is COUNTER-TREND within the bearish structure owner has mapped — a bounce-then-reject read, not a trend turn; owner's structural view (short the pullback) is intact. (2) Mafioso's "4H close below" stop methodology is the wide candle-close approach owner has already flagged as dangerous — price can spike far through the level intrabar before the close is confirmed; note the methodology difference, do not adopt it. (3) Mafioso is a reference/mirror only — NOT a direction source, NOT a tiebreaker against Kabroda's own logic. Use his forward-target structure as the design template for what HTF anticipation output should look like, not as a signal to follow. **SECOND CONCRETE EXAMPLE — weekly-close timing blindness (2026-06-07):** Owner's read on Sunday Jun 8: price is coiling into the weekly candle close (~00:00 UTC tonight) at a major decision level ($60,055 Wave-5 trigger). Expect chop and intraday drawdown today; real directional resolution waits on the weekly close. If the level holds → likely bounce/pullback up next week; if it fails → breakdown continuation later. Kabroda has ZERO awareness of this context. It evaluated today's intraday setup in isolation and approved a SHORT — blind to "this is a wait-for-the-close day where the intraday snapshot is noise relative to the bigger event resolving tonight." The approved short is technically valid by intraday logic but poor R:R in context — the system traded the detail while missing the frame. **What the HTF-anticipation layer needs to do here:** reason about UPCOMING higher-timeframe events (weekly/daily closes at key levels) and contextualize the session accordingly. Example output: "Price approaching weekly close at $60,055 Wave-5 decision level — expect indecision and potential intrabar whipsaw today; consider standing aside until the close resolves." This is a TIME-awareness gap, not just a level-awareness gap. The gravity map knows WHERE the walls are; it does not know WHEN a higher-timeframe close is imminent or that "close at a key level = low-quality intraday action." **Connects to the R:R gate pin** — both represent the system missing bigger-context that says "don't trade today regardless of what the intraday snapshot shows." The HTF-anticipation layer and an R:R gate are two expressions of the same missing capability: session-level context that overrides or qualifies intraday trigger logic. | owner, 2026-06-06 / 2026-06-07 | GATED — expansion tier; own scoped project after A3 confirmed live |
| 2026-06-06 | **ACCOUNT SIMULATOR / R-TO-DOLLARS TRANSLATION (owner, 2026-06-06).** The system measures in R — correct, account-agnostic. But R is the "GB" most people don't intuit; the validating view is "start $X, risk $Y/trade, 30 days → where's the account." Pure arithmetic on the existing closed-trade R record (1R = chosen risk amount), replayed forward into an equity curve. **TWO distinct uses, only one gated:** (1) INTERNAL (behind the password wall, for the owner's own comprehension — "5R = $500 at $100/trade") — NOT gated, build freely, it's a private validation tool. The password wall is the line: inside = free. (2) EXTERNAL (any public/marketing/newsletter/paid-facing page showing $ performance to attract subscribers) — GATED behind the securities/financial-services attorney review (performance representation + hypothetical-results disclaimers). The whole Kabroda system is currently fully password-protected, zero forward-facing — so everything built now is internal by definition. When any page leaves the wall, the external gate applies. | owner, 2026-06-06 | Internal: build anytime. External: attorney review first |
| 2026-06-06 | **EXIT_WARNING — LIVE MONITORING ITEM (NOT a fix now) (owner, 2026-06-06).** The `exit_warning` condition in the ALLOCATION RULE is a blunt T1-cap — it fired on Jun3 (15M grade=TANGLED at session open) and held a move that ran to T2/T3. Owner reviewed the live chart and confirmed T1 was still the correct call (15M tangled at a structural floor / light weekly-level — high-conflict zone, conservative exit right). But the pattern is the same class as the MACD veto A3 just removed: a single condition capping allocation without regard to trend strength or fuel quality. **Question for live monitoring over coming weeks:** is exit_warning ever capping clean strong-trend moves it shouldn't? Do NOT fix reactively to one day — observe across many sessions. Only scope a fix (e.g. a STRONG-with-trend override: exit_warning vetoed when 4H MACD=STRONG AND trade direction matches 4H trend) IF a real pattern emerges in data. This is the audit loop working: A3 removed the MACD veto, revealing exit_warning as the next layer — expected and healthy, not a regression. | owner, A3 validation session 2026-06-06 | TBD — observe live; scope fix only if pattern confirmed |
| 2026-06-07 | **R:R / TRADE-QUALITY GATE (owner insight, 2026-06-07).** The system approves on DIRECTION + STRUCTURE but does NOT evaluate whether the resulting trade is a good risk:reward proposition. A "structurally valid trade" and a "good trade" are different things; Kabroda currently only checks the former. **Concrete example (2026-06-07):** approved SHORT — entry $60,508, stop $62,120 (~$1,612 risk), T1 $60,025 (~$483 reward) = **~0.3:1 R:R**. Directionally valid by every intraday gate, but a trade no disciplined trader takes: risking 3+ to make 1, because T1 sits against the $60K MAXIMUM wall only $480 away while the stop is $1,600 away. The wall proximity that caused the T1-only cap (choked target) also makes the R:R structurally unfavorable — both are symptoms of the same geometry, the system just doesn't say so. **Future layer:** after computing triggers, targets, and stop, assess R:R and flag/downgrade setups where reward is poor relative to risk. Example output: "Valid SHORT structure but T1 is only 0.3R from a 1R stop — wall too close, R:R unfavorable, consider standing aside." Threshold to determine (e.g. minimum 0.8:1 or 1:1 before APPROVED; below that → STAND_DOWN or CAUTION flag). **NOT TODAY** — do not bolt onto a working system mid-validation. **Connects to:** (1) VET-A-TRADE / timing pin — both are cases where the direction is right but the geometry makes the entry suboptimal; (2) HTF-anticipation pin — weekly-close timing blindness is the same failure class (bigger context says "don't trade today"); (3) allocation logic — R:R gate likely lives near or inside the allocation rule layer, alongside the MACD-strength and exit_warning caps. **Gates:** same as HTF-anticipation and VET-A-TRADE — front-of-river solid (done), A3 confirmed across varied sessions, design the three connected features together before building any of them. | owner, 2026-06-07 | GATED — do not build in isolation; design with VET-A-TRADE + HTF-anticipation as a connected evaluation layer |
| 2026-06-07 | **VET-A-TRADE — ENTRY TIMING + DRAWDOWN AVOIDANCE TOOL (owner insight, 2026-06-07).** Core value is NOT "is this a good trade direction?" — it's "given an external signal's direction and targets, WHERE and WHEN is the lower-drawdown entry, and where's the real invalidation." **Concrete reference (2026-06-07):** Mafioso 4H LONG — entry $61,671, stop "4H close below 59,617" — concedes ~$2,054 of downside room before invalidation. Taking the entry at $61,671 means sitting in drawdown while price potentially tests $60K first. Kabroda's same-day read: the bounce is a decelerating counter-trend pullback likely to revisit the $60,025.76 MAXIMUM wall before any sustained move. **Synthesis the tool should produce:** "The long direction may be valid but Kabroda's near-term path says price likely tests $60K first — don't enter at $61,671 now. Wait for the pullback to the wall, confirm it holds, THEN enter with far less drawdown and a tighter stop." This solves **right-direction-wrong-timing**, the most painful trader failure mode: you have the thesis right, you lose money on the entry. **The two-system model:** external signal = direction + destination; Kabroda = immediate structural path + optimal timing. They are complementary, not competing. The external signal does NOT override Kabroda's direction read — Kabroda uses it only to identify destination targets and map the lower-drawdown entry point along the path it already sees. **Output structure the feature needs to produce:** (1) direction alignment check (does external signal direction match or oppose Kabroda's bias?); (2) near-term path read (what does Kabroda expect price to do in the next session before reaching the external signal's destination?); (3) recommended entry timing ("wait for X level, confirm Y condition, then enter"); (4) drawdown comparison (entering now vs. waiting = estimated max drawdown difference); (5) real invalidation level (Kabroda's trigger/structural level, not the external signal's wide candle-close stop). **CAVEATS:** (a) Mafioso's "4H close below" stop methodology is the wide candle-close approach owner has flagged as dangerous — always substitute Kabroda's structural level as the real invalidation; (b) external signal is reference only — direction and target levels are inputs, not overrides. **GATES (same as HTF Anticipation):** front-of-river solid (done) AND A3 confirmed across varied sessions; Version-A structural-read framing only (internal tool); any external/paid-facing use = attorney gate first. | owner, 2026-06-07 | GATED — build after A3 confirmed live across varied sessions; see HTF Anticipation pin for shared design questions |
| 2026-06-07 | **MULTI-TIMEFRAME SSE ENGINES — MAJOR architectural project, GATED (owner, 2026-06-07).** The current SSE/battlebox engine analyzes ~24h of data to produce levels + bias for the 15M intraday trade. Idea: replicate the engine for 1H, 4H, daily, and weekly — each with its own VRVP / value-area / trigger levels / bias — so Kabroda becomes a multi-timeframe system ("I want to trade the 4H setup — what do I do?") rather than 15M-only. Matches what external reference systems (Mafioso) do with 1H/4H/8H signals. **Why this is high-leverage:** it reuses the existing core engine; point it at different candle sets rather than inventing new math. **Why this is likely the MECHANISM for HTF-anticipation:** a native 4H/weekly SSE engine would give Kabroda the higher-timeframe structural awareness it currently lacks natively — the "see the bigger board" capability the HTF-anticipation pin describes is probably not a separate feature but the natural output of a 4H/weekly engine running alongside the 15M one. Scope this jointly with the HTF-anticipation pin; they may resolve into one "multi-timeframe architecture" design project, not two. **SCOPE CAUTIONS — do not underestimate:** (1) NOT a copy-paste — each timeframe needs its own lookback calibration, threshold tuning, and live validation (a weekly wall ≠ a 15M wall in geometry, noise characteristics, or how close price can trade to it without triggering); (2) the HANDSHAKE between engines — does weekly bias override 15M? how do they reconcile on disagreement? what does a "STAND_DOWN on 4H but APPROVED on 15M" mean operationally? — this is its own real design problem and is where most of the complexity lives; (3) this is a PROJECT, likely the largest on the board, not a feature weave-in. **HARD GATES before even scoping:** (a) the 15M core must be proven SOLID across MANY live sessions — A3 is only 2 sessions old; B1/PMARP direction-blindness is still parked; replicating an unproven engine 4× is "build on sand" at maximum scale, copying any undiscovered bug into 4 more engines simultaneously; (b) dashboard must be legible so each engine's output is verifiable as sessions accumulate; (c) scope jointly with HTF-anticipation — do not design either in isolation. **The sequence:** 15M proven → dashboard legible → HTF-anticipation + multi-TF scoped together → then build. This is the highest-value future expansion on the board, and exactly why it must wait for a provably solid foundation. | owner, 2026-06-07 | HARD GATED — biggest project on the board; scope jointly with HTF-anticipation after 15M core proven across many sessions |
| 2026-06-07 | **PERFORMANCE AUDITOR — FROM REPORT TO COACH (owner vision, 2026-06-07).** Tonight's first scheduled run is a thin, caveat-heavy SNAPSHOT — 5–7 sessions, sparse outcomes. It confirms the scheduler fires and produces honest output. That is all tonight is. The VISION is a separate, future capability: **(1) MEMORY** — the auditor accumulates and reasons over its OWN past audit notes, so it can say "I've watched three weeks, here's a recurring issue" that a daily eye forgets. A one-shot audit cannot see a pattern; a coach that reads its own history can. Requires audit-history storage (the `SystemAuditLog` rows are the raw material — they exist now per Principle 5) plus a cross-audit pattern-detection pass in the prompt ("given the last N audit notes, what is the persistent theme?"). **The capture already starts tonight — the pattern-finding becomes possible weeks from now.** **(2) DECISION-LEVEL REVIEW** — not just "win rate was X%" but "it called T1 on this configuration, MACD read STRONG, RSI was 61 — did that characterization hold up over the following 4H?" Requires joining `DecisionJournal` signal snapshots to realized outcomes and reviewing the specific reasoning, not just the count. **Granularity refinement (2026-06-14):** this review should operate at the per-indicator level, not just the interpreter level — "was the MACD characterization specifically accurate this session? The ADX? The MA read?" — confirming or flagging where each indicator's read held up against actual price. Same concept, one layer deeper: interpreter-level accuracy is the current unit of review; per-indicator accuracy is the target state once data volume supports it. **(3) RESEARCH-TRIGGERING** — auditor flags a recurring gap (e.g. "CoinGecko 429 has fired 3 consecutive weeks") and initiates a prior-art pass: how do comparable systems handle rate-limited sentiment feeds? Returns "here is our current approach, here is theirs, here is a proposed tweak." Connects to the prior-art research passes already noted in the suggestion box. **Sequencing:** tonight = does it fire + produce sane output. Week 3–4 = is the output honest with thin data. Month 2+ = cross-audit memory becomes meaningful. Decision-level review and research-triggering are Phase 3+ builds, gated behind sufficient `DecisionJournal` + `SystemAuditLog` history. Do not scope them until the plain weekly audit has accumulated 4+ runs. **Job 2 dependency (2026-06-14):** the deep stress-test audit (sub-item 2) is currently shallow because replayable per-session data is not assembled — `full_context_json` is write-only, interpreter reasoning is not joined to outcomes at query time. Job 2 capture + W-3 join is the prerequisite that unblocks the depth. This reprioritizes Job 2: it is not just "backtest plumbing" — it is the prerequisite for the Performance Auditor doing its real job. Without Job 2, the auditor measures outcome tallies only; with Job 2, it can interrogate each session's reasoning against what actually happened. | owner, 2026-06-07 | GATED — tonight = fire check only; memory + decision-review + research-triggering are month-2+ builds after audit history accumulates. Depth gate: Job 2 capture + W-3 join. |
| 2026-06-10 | **MD-REFACTOR BUILD RULES — VERBATIM EXTRACTION IS NON-NEGOTIABLE (owner, 2026-06-10).** The MD refactor moves WHERE each prompt lives (Python → Markdown), never WHAT it says. **RULE 1 — VERBATIM ONLY:** per-agent procedure: (1) copy the EXACT current Python prompt string into the MD body — zero rewording, zero reorganization, zero "improvements"; section-header nav labels may be added ONLY as navigation aids that sit above the existing rule blocks without moving or altering any rule text; (2) DIFF the MD body against the original Python string (accounting for `\` line-continuation stripping) — prove character-identical content before proceeding; (3) wire the agent to load from MD; (4) run a live session and confirm output is unchanged; (5) ONLY THEN delete the Python constant. One agent at a time. Any reorganization of rule text (e.g. the CC-proposed SA template that moved banned-words into a separate section) is a SEPARATE, later, validated change — NOT part of this refactor. The refactor must not introduce the drift it exists to cure. **RULE 2 — SAFEST AGENT FIRST:** do NOT start with the senior_analyst (most critical, most complex, most stakes). Start with a simple lower-stakes agent (`mtf_interpreter` or `performance_auditor`) to prove the loader mechanism and the verbatim-diff-validate process end-to-end. Migrate the SA only AFTER the process is proven on something low-risk. **NOTE ON CC'S SA TEMPLATE (2026-06-10):** the worked example in the prior design session was a REORGANIZATION, not a verbatim copy — it promoted banned words/time projections out of WRITING RULES into a standalone section and added Role/Inputs sections that don't exist in the original. That reorganization is a future validated change, not part of the initial migration pass. The initial migration pass is purely verbatim. | owner, 2026-06-10 | STANDING BUILD RULE — applies to every agent migration in the MD-refactor; enforce per-agent, no exceptions. **CRITICAL LESSON FROM FIRST MIGRATION (2026-06-10):** manual transcription introduced 36 chars of drift that the diff caught — the `\` line-continuation artifacts (extra spaces at join points) were the culprit. **MANDATORY METHOD:** generate the MD body directly from the Python constant: `python -c "from X import PROMPT; write frontmatter + PROMPT"`. Never transcribe by hand. The verify script (`verify_prompt_mtf.py`) catches both exact mismatches and whitespace-normalization differences — formatting-artifact whitespace differences (multi-space joins) are the only acceptable diff. Any non-whitespace mismatch is a hard stop. |
| 2026-06-10 | **AGENT MD-FILE SPECS — per-agent Markdown job descriptions (owner + friend's suggestion, 2026-06-10).** Idea: extract each agent's job description / responsibilities / rules / banned-behaviors / anti-drift instructions out of its `.py` file into a dedicated Markdown file per agent (e.g. `agents/senior_analyst.md`). **Why it fits Kabroda — three reasons:** (1) directly fights the #1 recurring pain — **agent drift** (false-certainty regression, brief-too-technical) — by making each agent's rules a clean single-source-of-truth doc that is easy to audit and correct without parsing Python; (2) **auditing an agent becomes trivial** — read the MD, compare against live output, edit one file; (3) **de-commingles logic (Python) from job-description (MD)**, matching the existing SYSTEM_FLOW.md / WORK_LOG.md source-of-truth-docs philosophy — the same reason those docs exist. **Key design decision to resolve before building:** does the agent LOAD its MD as its runtime system prompt (powerful — the doc literally IS the behavior; one source of truth that cannot drift from what the agent does) OR is the MD documentation that mirrors the hardcoded Python prompt (safer short-term, but guaranteed to drift over time)? The former is better if done carefully: `agent_core._call_agent()` reads the MD file at call time as the `system_prompt` argument — any edit to the MD takes effect on the next call with no code change. **Pairs with the model-assignment work:** the MD could also declare which model each agent uses (e.g. a `model:` frontmatter field), making model overrides a doc edit rather than a code change. **Scope:** a refactor touching all 9 LLM agents — `senior_analyst`, `junior_analyst`, `mtf_interpreter`, `gravity_interpreter`, `intel_auditor`, `publisher_agent`, `performance_auditor`, `elliott_wave_specialist`, `senior_analyst_commlink`. **Gate:** not urgent enough to jump W-9, but HIGH VALUE and worth doing before the big gated builds (HTF-anticipation, multi-TF) because it serves the core anti-drift/legibility/auditability values those builds will depend on. Do this in the near-term window between W-9 and the expansion tier. **PROGRESS (2026-06-10) — LOADER BUILT, FIRST AGENT WIRED:** `agent_core` gained `load_agent_spec()` + `_call_from_spec()` (reads frontmatter for model/max_tokens; `FileNotFoundError` on missing spec — never silent). `agents/mtf_interpreter.md` generated from Python constant (not transcribed — manual transcription introduced 36-char drift, caught by diff; generate-from-constant is now mandatory). `verify_prompt_mtf.py` confirms character-identical (6592 chars). `mtf_interpreter.py` call site wired to `_call_from_spec()`; Python constant retained pending live confirmation. **SA template noted as REORGANIZATION** — parked as a separate later validated change. **Known wrinkles to handle in rollout:** (1) `kabroda_mas_flow.py` hosts 3 prompts (`senior_analyst`, `senior_analyst_commlink`, `intel_auditor`) — each becomes its own MD, three deletions from one file; (2) retry paths in `senior_analyst` and `publisher_agent` pass a modified context to the same prompt — need a `_call_from_spec_with_prompt()` variant that accepts a pre-loaded spec + modified context so the file is only read once. **Rollout order** (safest first): mtf_interpreter ✓ → gravity_interpreter → junior_analyst → performance_auditor → elliott_wave_specialist → intel_auditor → senior_analyst_commlink → publisher_agent → senior_analyst (last — most critical). | owner + friend, 2026-06-10 | ◐ LOADER PROVEN — `mtf_interpreter` wired + diff-verified; next gate = live session validation, then continue rollout in safest-first order; full rollout gated on W-9 for SA (needs clean outcomes to verify no regression) |
| 2026-06-10 | **AGENT MODEL-ASSIGNMENT AUDIT (owner, 2026-06-10).** Fable 5 launched 2026-06-09 — frontier model above Opus 4.8, strongest on long-horizon + analytical/finance reasoning ($10/$50 per M tokens; free on plans only through June 22, then metered). Owner's question: is each agent on the optimal model for its job? **CORRECT FRAMING — not "upgrade all to Fable":** match each agent to the CHEAPEST model that does its job well, per Principle 1 (clerk vs. judgment). Clerk/mechanical agents (jewel_specialist, structured packaging) → likely Haiku (cheaper, fine). Judgment agents (senior_analyst, junior_analyst, interpreters) → MIGHT benefit from Opus 4.8 or Fable 5, but only if measurable. **HARD DEPENDENCY:** cannot measure whether a stronger model improves the SA's decisions until outcome data is trustworthy (W-9) — otherwise there's no clean signal to A/B against. Also note cost: full pipeline is ~$0.18/day now; Fable would multiply it. **SEQUENCING:** inventory current model-per-agent now (read-only, free — just "what model is each agent using?"). The actual re-assignment/A-B testing is GATED behind W-9 (clean outcome data to measure against). Do the inventory now; defer the changes. | owner, 2026-06-10 | GATED — inventory now (free); re-assignment deferred until W-9 resolved and clean outcome data available |
| 2026-06-07 | **STAND-DOWN GATE OVER-FIRING — FIRST DATA CORROBORATION (auditor finding, 2026-06-07).** The performance auditor's first scheduled run produced a concrete data point for the exit_warning monitoring item: 38 stand-down fires in the trailing window, 70.3% accuracy (correctly avoided bad days), 29.7% overcautious (valid setups missed). This is the first time the "may be overcautious" concern has numbers behind it rather than just a one-day observation. Treat as a STRONG LEAD with two caveats: (1) computed on outcome data with known integrity issues (W-9 — phantom losses + binary R), so the 70.3%/29.7% split is provisional; (2) auditor output was TRUNCATED mid-sentence (600-token limit hit — see W-10), so the full recommendation on this finding was cut off. **Action when W-9 resolved:** re-run the accuracy analysis on clean outcome data and check whether the split changes materially. If the ~30% overcautious rate persists on clean data, this corroborates scoping the exit_warning STRONG+with-trend override (already described in the exit_warning pin). Connects to: exit_warning Suggestion Box pin (2026-06-06) + B1/PMARP parked item. | auditor finding, 2026-06-07 | MONITORING — re-evaluate on clean outcome data after W-9 resolved |
| 2026-06-14 | **NEWS/EVENT CALENDAR AWARENESS — forward weekly read enrichment (2026-06-14).** The HTF STRUCTURAL ANTICIPATION pin captures price-structure anticipation: where are the walls, what level is price approaching, what does a weekly close at a key level mean. It does NOT capture **scheduled macro events** — Fed rate decisions, CPI prints, FOMC minutes, regulatory announcements, major macro dates — "what's on the calendar this week that could override technical structure?" Price at a key level during a quiet week and price at that same level on a CPI print day are structurally identical but practically different. The calendar dimension is distinct from the price-structure dimension, and both belong in the forward weekly read. **Two downstream consumers:** (1) the weekly audit's forward read — "next week has Wednesday CPI; elevated volatility risk, consider reduced-target posture on setups that day"; (2) the publication's weekly narrative — "watch Wednesday's CPI; if above expectations, anticipate volatility at the $X structural level." Discrete addition to the HTF-anticipation cluster (W-14 14c) — plugs into the same weekly narrative output, not a separate build. **Gate:** same as 14c (15M core solid, W-14 cluster unblocked). Also feeds W-4 publication phase. | expanding W-14 / 14c scope analysis (2026-06-14) | TBD — add to 14c scope when W-14 cluster graduates from GATED; also feeds W-4 |
| 2026-06-14 | **AUDITOR THIN-DATA LEGIBILITY BUG — near-term, not gated (2026-06-14).** This week's audit reported "0% directional accuracy / every configuration failed" when the correct read was: zero closed trades, nothing resolved yet, metric has no denominator. The auditor conflates "no resolved data" with "0% / everything failed." **Distinct from the existing "provisional numbers due to W-9 integrity" caveat** — that caveat is about data quality on resolved outcomes; this is about generating false failure signal from an empty dataset. **Correct behavior:** when resolved outcome count is zero (or below a meaningful minimum), do NOT render a 0% accuracy figure — report "INSUFFICIENT DATA — N resolved sessions, metric not yet computable." A 0% that reads as systemic failure makes every thin-data week's audit cry wolf. **Scope:** `performance_auditor.py` prompt or pre-check logic — if `resolved_outcomes == 0` (or below threshold), substitute "INSUFFICIENT DATA" text in the accuracy blocks. No schema changes, no new tables. Not gated behind W-3 or Job 2. **Priority:** near-term — fix before next Sunday's scheduled run. | this week's auditor output (2026-06-14) | FIX SOON — one session, no dependencies; before next Sunday's scheduled run |
| 2026-06-15 | **ENERGY/LEVEL TIME-COHERENCE GAP — data-integrity finding, fix not yet scoped (2026-06-15).** Read-only trace of `battlebox_pipeline.get_live_battlebox()` confirmed the following: the MAS pipeline fires on the FIRST PAGE-VISIT after lock time — there is no auto-scheduler that fires `run_mas_analysis()` at 9:30 AM ET. **Two-timestamp problem:** the breakout/breakdown LEVELS are always correctly bounded to the 9:00–9:30 AM calibration window (filtered in `_compute_sse_packet()` as `int(c["time"]) < lock_end_ts`). BUT the energy reads — `fuel_gauge` (RSI, MACD, EMA trends, ADX, JEWEL on 1H/4H/15M), `micro_state`, `1h_fuel_status`, `kde_peaks`, `macro_environment` (SPX/DXY/VIX) — are sampled FRESH at whatever wall-clock moment `get_live_battlebox()` first executes after lock time. Once the packet is created and persisted to `session_locks`, both the levels AND these energy reads are frozen. But they were sampled at different moments. **The coherence gap:** if the first page-visit is ~9:31 AM, levels and energy are approximately coherent. If the first page-visit is 10:00 AM, the agents analyze 9:30 AM level geometry against 10:00 AM momentum data — a 30-minute mismatch with NO warning flag in the brief or DB. The brief reads internally consistent but stitches two timestamps. Concrete example: RSI 55 at lock → 64 at 10:00; agents see "PRIMED / fuel confirms" on data the trade levels never saw. **Same integrity-risk family as the phantom-loss bug (W-9):** both look coherent from the outside; inputs are silently mismatched. **Timestamp facts traced:** no `analysis_fired_at` field exists anywhere; `CampaignLog.created_at` = wall-clock at write (fire time + LLM latency, not lock time); `battlebox.lock_time` holds the correct 9:30 AM unix timestamp; the gap IS reconstructable by comparing `CampaignLog.created_at` against `battlebox.lock_time` stored in `full_context_json`, but nothing in the code detects or flags it. **Candidate fixes (do NOT choose yet — scope later):** (1) **Freeze energy reads at lock time** — sample all data at 9:30 AM so levels and energy are always co-sampled; brief is always "as of lock." Fully coherent, requires a scheduled 9:30 AM data snapshot. (2) **Auto-fire the pipeline at lock time via scheduler** — don't wait for a page-visit; fire `get_live_battlebox()` (and therefore `run_mas_analysis()`) at `lock_end_ts` on a background task. Fully coherent AND solves the "brief should be waiting for me when I arrive" UX problem the owner has noted. **Likely the highest-value option — one change, two wins.** Connects directly to W-12: W-12 confirmed the 14:00 UTC scheduler is a fallback (page-visit always races ahead); the fix would promote the 9:30 lock-fire to the primary trigger, not a fallback. (3) **Stamp + flag the gap** — label the energy reads with their actual sample timestamp in the context string; let agents and the brief note "energy reads sampled N minutes after lock." Cosmetic mitigation, not a fix. (4) **Late-arrival awareness** — detect a late fire (compare `now_utc` to `lock_end_ts` at packet creation time) and surface a warning in the brief: "NOTE: energy snapshot is N minutes post-lock." Same class as (3). **Connections:** W-12 (page-visit-triggers-first is the same root mechanism; the W-12 scheduler is the structural hook the fix would use); the owner's recurring observation that "the brief isn't waiting for me when I arrive" is the user-facing symptom of this same root cause. | read-only architecture + timing trace (2026-06-15) | ☑ SHIPPED (commit `d9a4a92`, 2026-06-15) — Option 2 chosen: `_seconds_until_lock_end()` added to `main.py`; scheduler fires at DST-aware `lock_end_ts` from `session_manager`. Boot check uses `now.timestamp() >= _boot_lock_end_ts`. `date_key` from `session["date_key"]`. Page-visit double-fire guard unchanged. **Verification checkpoint: tomorrow 9:00 AM ET — brief should be waiting on arrival.** |

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
| 2026-06-04 | EDUCATIONAL FRAMING — design principle for all public/paid output (owner, 2026-06-04) | Everything published or sold is framed as EDUCATIONAL / opinion / "this is what we see" — never as financial advice, never with claims about profit or returns. Users make their own decisions and interpretations. Standard disclaimer language (not financial advice, educational purposes, our opinion, trade at your own discretion) on all public-facing material. CRITICAL CAVEAT: the disclaimer is necessary but NOT sufficient — regulators judge substance, not just the label. Publishing specific entry/stop/target levels + performance stats + charging can read as a signal service regardless of disclaimer. The framing AND the format must be designed together. HARD GATE (already pinned): a qualified securities/financial-services attorney must review the actual framing, format, disclaimers, and performance presentation for the owner's jurisdiction (US/TX) and subscriber base BEFORE any public launch or paid subscription. "Other sites do it this way" is not a compliance basis. Claude is not a lawyer and cannot adjudicate this. |
