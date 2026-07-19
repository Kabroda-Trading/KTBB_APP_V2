# Kabroda Audit Framework — CC Review Request

**For CC to review and respond.** This is the complete audit framework plan based on live production data, research, and codebase analysis. Please read, verify, and write your response below the `## CC Response` section at the bottom.

---

## What Changed Since Last Handoff

The Phase 1 unified audit system is live on Render. Three new tables are collecting data:

- **`decision_log`** — 97 rows on day one. Every decision (TRADE/STAND_DOWN) across all timeframes (15M/1H/4H), with computed `stop_distance_pct`, `target_distance_pct`, and `atr_pct_at_decision`.
- **`decision_gauge_reading`** — Normalized gauge readings per decision. One row per (decision, timeframe, gauge_name). Full gauge panels for every decision.
- **`candle_history`** — 338 candles captured. Every candle the system fetched, persisted forever.

The old H1-H10 hypotheses still query `SessionAuditLog` and `CampaignLog`. The new tables are capturing data but **nothing is reading them yet** (Gap 11).

---

## The Core Problem (Found Today)

The 1H trade from 2026-07-18 (campaign #164) was logged as a CLOSED_WIN. But:

| Field | Value |
|---|---|
| Entry | $64,022.50 |
| T1 | $64,205.50 |
| Move | **$183 (0.29%)** |
| ATR at decision | 0.24% |
| Target / ATR | ~3× (formula correct) |

The formula is mechanically correct (3× ATR). The problem is the ATR itself (0.24%) is below the professional minimum for a 1H trade (0.30%). The system produced a 15M-sized move wearing a 1H label.

**The question:** Is this a one-off or a systematic calibration issue? The audit framework is designed to answer this.

---

## Research Findings (BTC Timeframe Calibration)

Professional BTC traders use **ATR multiples**, not fixed percentages:

| Timeframe | Min ATR to Trade | Target (ATR×) | Stop (ATR×) | Min R:R | Duration |
|---|---|---|---|---|---|
| 15M | 0.10% | 2.0-3.0× | 1.5-2.0× | 1:1.5 | Min–hours |
| 1H | 0.30% | 2.0-3.0× | 1.5-2.5× | 1:2 | Hours–1 day |
| 4H | 0.80% | 2.0-3.5× | 2.0-3.0× | 1:2 (1:3 pref) | 1–3 days |

**BTC-specific adjustments:**
- Add ~0.5× to stop multipliers (liquidity sweeps are common)
- BTC volatility is structurally declining (3.4% daily ATR in 2025 vs 5-7%+ in earlier cycles)
- Square root of time rule: 4H ≈ 4× 15M, 1H ≈ 2× 15M (use as sanity check, not trading rule)

**Key insight:** The 1:2 R:R minimum is non-negotiable for professional trading. If market structure doesn't support it, pass on the trade.

---

## The 12 Gaps Found in Codebase Analysis

| # | Gap | Severity |
|---|---|---|
| 1 | No outcome backfill on `decision_log` — can't compute win rates | **BLOCKER** |
| 2 | `decision_full_view` SQL view not built | Deferred |
| 3 | 15M `jewel_gate_open`/`jewel_conviction` not captured as gauges | Phase 2 |
| 4 | No per-target outcome tracking (T1 vs T2 vs T3) | Deferred |
| 5 | No stand-down accuracy panel on dashboard | Phase 2 |
| 6 | No session drill-down from dashboard | UI work |
| 7 | No interpreter-log visibility panel | Separate |
| 8 | No date filters on dashboard | UI work |
| 9 | No auto-escalation when suggestions reach PROVISIONAL_FINDING | Future |
| 10 | Phase 2 cross-check (new tables vs old) not done | This framework |
| 11 | New tables not read by any hypothesis | **This framework fixes** |
| 12 | Manual session evaluation log not in DB | Phase 3 |

---

## The Proposed Audit Framework (H11-H26)

### Architecture
```
harness/comprehensive_audit.py     # Multi-mode entry point (daily/weekly/monthly/all)
harness/audit_checks.py            # 16 check functions (H11-H26)
harness/timeframe_calibration.py   # ATR-based thresholds
harness/ollama_summarizer.py       # Local llama3 → natural language summary
harness/outcome_backfill.py        # Backfills decision_log outcomes from campaign_logs
```

### Phase 1 Checks (No Outcome Data Needed — Build First)

| ID | Check | What It Flags |
|---|---|---|
| H11 | Target Calibration | `target_distance_pct / atr_pct` below timeframe minimum |
| H12 | Stop Calibration | `stop_distance_pct / atr_pct` outside stop multiplier range |
| H13 | ATR Health | `atr_pct_at_decision` below timeframe minimum |
| H14 | R:R Ratio | `target / stop` below timeframe minimum R:R |
| H15 | Decision Distribution | TRADE vs STAND_DOWN ratio per timeframe |
| H16 | Stand-Down Reason Distribution | Which reasons are most common? |

### Phase 2 Checks (Need Outcome Backfill First)

| ID | Check | What It Flags |
|---|---|---|
| H17 | Energy Grade vs Outcome | Does energy_grade predict win rate? |
| H18 | Kinematic Grade vs Outcome | Does kinematic_grade predict win rate? |
| H19 | Macro Bias vs Outcome | Does macro_bias alignment predict win rate? |
| H20 | Ribbon Zone vs Outcome | Does revin_ribbon_zone predict direction? |
| H21 | RMO State vs Outcome | Does rmo_state predict outcome? |
| H22 | Confluence Score vs Outcome | Does higher confluence = higher win rate? |
| H23 | Dominant Direction vs Outcome | Does dominant direction agree with bias? |
| H24 | Energy+Kinematic Combo | Which combos work and which don't? |
| H25 | Cross-TF Agreement | When 1H and 4H agree, does win rate improve? |
| H26 | Stand-Down Quality | Did stand-downs correctly avoid bad moves? |

### Multi-Mode
```
--mode daily    → Today only
--mode weekly   → Last 7 days
--mode monthly  → Last 30 days
--mode all      → All data
```

All checks write to the existing `audit_suggestion_log` table. Same tier-gating discipline (N-based, not auto-applied). Same "system recommends, owner decides" authority cap.

### Integration
Hooks into the existing scheduler in `main.py`:
- Daily 23:45 UTC → `comprehensive_audit.main(--mode daily)`
- Sunday 23:00 UTC → `comprehensive_audit.main(--mode weekly)`
- Monthly → `comprehensive_audit.main(--mode monthly)`

---

## Live Production Data (2026-07-19)

### Today's Decisions
| Timeframe | TRADE | STAND_DOWN | Notes |
|---|---|---|---|
| 15M | 0 | 1 | Stand-down was correct (price flat) |
| 1H | 1 | 8 | Trade #19 still PENDING, -0.23% from entry |
| 4H | 0 | 87 | All NO_BOS — no break of structure |

### Yesterday's Closed Trades
| Campaign | Bias | Entry | T1 | Result | Target % |
|---|---|---|---|---|---|
| 1H_system (#164) | LONG | $64,022.50 | $64,205.50 | CLOSED_WIN | 0.29% |
| us_ny_futures (#165) | LONG | $64,250.00 | $64,632.47 | CLOSED_WIN | 0.60% |
| 4H_system (#166) | LONG | $64,424.10 | $66,434.19 | PENDING | 3.12% |

### Gauge Panels (Key Decisions)

**1H Trade #19 (PENDING):**
- energy_grade=MODERATE, kinematic_grade=TANGLED, macro_bias=BULLISH
- dominant_direction=BULLISH, confluence_score=3
- revin_ribbon_zone=ABOVE_UPPER_1σ, rmo_state=STRONG_BULLISH
- ATR=0.24%, target=0.71%, stop=1.56%

**4H Trade #166 (PENDING, from yesterday):**
- energy_grade=MODERATE, kinematic_grade=TANGLED, macro_bias=BULLISH
- dominant_direction=BULLISH, confluence_score=3
- revin_ribbon_zone=AT_MIDLINE, rmo_state=NEUTRAL
- ATR=0.87%, target=3.12%, stop=3.12%

---

## Questions for CC

### 1. ATR Threshold Validation
The research says 1H needs ≥0.30% ATR to support a trade. Our 1H trade had 0.24%. Do these thresholds look right for BTC specifically, or should they be adjusted?

### 2. The 1H Calibration Problem
The 1H formula produces correct ATR multiples (3×) but the underlying ATR is too small. Is the fix:
- (a) Add a minimum ATR gate — if ATR < 0.30%, downgrade to 15M or stand down?
- (b) Increase the ATR multiplier for 1H targets (e.g., 4-5× instead of 3×)?
- (c) Something else?

### 3. Outcome Backfill Approach
The `decision_log` table needs outcome backfill from `campaign_logs`. Should this be:
- (a) A scheduled job that runs after the daily audit?
- (b) A database trigger?
- (c) A one-time migration + ongoing write-at-decision-time?

### 4. Gap Prioritization
Of the 12 gaps, which 3 would you prioritize first? Gap 1 (outcome backfill) and Gap 11 (new tables not read) are already in the plan. What's #3?

### 5. The Trust Threshold
The plan proposes: "A timeframe is trustable when its last 20 trades have median target above minimum AND win rate above 50%." Is 20 the right N? Should it be 30 (matching the tier system's PRELIMINARY_SIGNAL boundary)?

### 6. Missing Checks
Are there any audit checks missing from H11-H26 that you'd add? Specifically:
- Anything about the shadow runner mechanic?
- Anything about the CRO (Chief Risk Officer) approval quality?
- Anything about signal accuracy correlation with trade outcomes?

### 7. Ollama vs Cloud LLM
The plan uses local Ollama (llama3) for summaries — zero token cost. Is there a reason to use a cloud LLM instead for audit summaries, or is local sufficient?

---

## CC Response

*Checked directly against the live code before writing anything below — gravity_engine.py's actual v4 stop/target math, ledger_closing_engine.py's existing backfill pattern, harness/tier_labels.py's real boundaries, and the repo for any existing Ollama footprint. Production row counts (97/338/87/etc.) I could NOT verify myself — I don't have Render DB access in this session, only the code. Flagged below where that matters.*

### Headline

This is good, real work — the gauge panels and live numbers are clearly pulled from production, not invented. But the "Core Problem" example is very likely the **same bug already investigated on 2026-07-12** (the v4 stop/target asymmetry appendix in the master plan), not a new discovery, and the proposed framework risks standing up a **third parallel audit orchestrator** next to two that already exist and are already scheduled. Neither of those should block starting — H11-H16 and the backfill are real, needed work — but both need to be corrected before anything gets built, or this repeats a mistake this project has already made and corrected twice before (see the 2026-07-08 appendix's own "found a full harness/ ecosystem mid-implementation" correction).

### The "Core Problem" — connect it to 2026-07-12, and separate two different questions

Walked the actual math in `_detect_1h_bos()` (`gravity_engine.py`) against the numbers given for Trade #19: stop=1.56%, ATR=0.24% → `raw_leg / atr14` ≈ 6.5×. That's **above the `leg > 5.0 × atr14` cap threshold**, so `leg` gets force-capped to `3.0 × atr14` = 0.72% — which matches the reported target=0.71% almost exactly. That's not "the formula correctly producing 3× ATR" as a designed target multiple — 3× is what you get *whenever the cap fires*, because the stop (pivot-anchored, can sit anywhere) and the target (ATR-capped) are computed independently and then forced back together. That's exactly the mechanism the **2026-07-12 appendix ("v4 STOP/TARGET ASYMMETRY")** already found and backtested: real production examples of a far pivot-based stop pairing with a capped, much-smaller target. That appendix's Phase A backtest (N=22 affected-subset both timeframes) found a **symmetric-fallback fix actually performed worse** (-0.069R vs +0.277R on 1H, +0.021R vs +0.301R on 4H) — so Phase B was deliberately never shipped. Please read that appendix before H11/H12 get built — it's the same question, already tested once, with a real "don't fix this the obvious way" result attached. `mtf_backtest_lab.py`'s `--compare-symmetry` mode is still in the repo for re-running that comparison as more data accumulates.

That said, there's a **second, genuinely separate question** buried in the same example that the 2026-07-12 work never asked: is 0.24% ATR simply *too small an absolute move* for a 1H-labeled trade, independent of whether the stop/target ratio is symmetric? That's a real, new question — H13 (ATR Health) is worth building. Just don't conflate it with the cap-asymmetry mechanism above; they're two different mechanisms and this doc's single example happens to exhibit both at once.

**One more thing to check before treating this as settled: is campaign #164 (target 0.29%, from "The Core Problem") the same trade as "1H Trade #19" (target 0.71%, from the gauge panel)?** Both are given ATR=0.24% but two different target percentages. If they're the same trade, that's an internal contradiction in this doc worth resolving before it's used as the flagship example. If they're genuinely two different trades that happen to share an identical ATR reading, that's fine, but worth stating explicitly rather than leaving ambiguous — this is the same class of "check the numbers agree with each other" issue that's come up more than once this session.

### Architecture: extend the two existing engines, don't add a third

`harness/audit_runner.py` (weekly, H1-H6) and `audit_ai.py` (daily, H7-H10) are both already live and already wired into `main.py`'s scheduler (confirmed in the 2026-07-18 boot log — both started clean). A new `harness/comprehensive_audit.py` with its own `--mode daily/weekly/monthly/all` CLI and its own scheduler hooks duplicates infrastructure that already exists rather than extending it — the exact mistake the 2026-07-08 appendix caught itself making mid-build ("designed a new AuditSuggestionLog table and hypothesis-scanner from scratch, without first checking whether Component 6 already existed") and corrected. Recommendation: H11-H16 (no outcome data needed, mostly 4H/1H-shaped) go into `audit_ai.py`'s existing daily run alongside H7-H10; H17-H26 (need outcome backfill, cross-timeframe) can go there too once backfill exists, or split to the weekly engine if any of them are 15M-specific — but as new hypothesis functions inside the two files that already exist and already run on schedule, not a new file, a new CLI, and new scheduler registrations. `harness/timeframe_calibration.py` (the ATR-threshold constants) and `harness/outcome_backfill.py` are fine as their own small modules — that's normal decomposition, not orchestrator duplication.

### Ollama — verify feasibility before building, don't default to it

Checked: there's no existing Ollama reference anywhere in this codebase or `requirements.txt`. This app deploys to Render as a standard web service (per CLAUDE.md). Running local Ollama + llama3 there means either a full model (several GB) resident on the same instance running the FastAPI app, or a second always-on Render service — neither is free, and "zero token cost" doesn't account for that infra cost or whether the current Render plan can even hold the model in memory alongside everything else already running (gravity engine loop, ledger closing loop, session monitor, etc.). Every existing LLM call in this codebase goes through `agent_core._call_agent()` (Anthropic, cost-tracked, budget-gated) — that's the proven, already-working pattern. Before building `ollama_summarizer.py`, confirm it actually runs on the real Render plan Kabroda is on. If that's untested, default to reusing `agent_core` for audit summaries (it's already budget-gated, which a new unmetered local LLM path isn't) rather than adding a second, unverified LLM runtime.

### Answers to the 7 questions

1. **ATR thresholds** — can't verify externally-sourced professional numbers against anything in this codebase; they're not backtested against Kabroda's own data. Per this project's own anti-overfitting rule ("no parameter change without N≥30 AND a mechanistic reason, backtest alone isn't a reason" — CLAUDE.md-adjacent discipline already established in the master plan), treat 0.30%/0.80% as a **starting hypothesis to log and watch (H13, tier-gated)**, not a gate to enforce yet. Same "record first, act at PROVISIONAL_FINDING+" discipline as `energy_grade`/`kinematic_grade` already went through.
2. **The 1H calibration problem** — see above: this specific example is likely the already-known cap-asymmetry mechanism, not a new ATR-floor problem. Don't gate anything ((a) or (b)) until H11-H13 have real N behind them. Building the checks now is right; acting on them isn't yet.
3. **Outcome backfill** — neither (a) nor (b) as framed. `ledger_closing_engine.py` already calls `harness.audit_writer.backfill_outcome()` at its three real resolution points (session-expired/NO_TRIGGER, CLOSED_WIN/CLOSED_LOSS, CLOSED_AT_EXPIRY — confirmed at lines ~320, ~422, ~455). Add one more call at each of those same three points that also backfills `decision_log.outcome_status`/`realized_r`, looked up via the `campaign_log_id`/`session_audit_log_id` soft FK columns already on `decision_log` (built into Phase 1 specifically for this). That's your existing (a) done the codebase's own way — no new scheduled job, no DB trigger (this codebase has none anywhere; introducing the first one here would be inconsistent with everything else).
4. **Gap prioritization** — after #1 and #11: **the outcome backfill (above) unblocks #11 for real**, since H17-H26 can't run without it. So really #1 and #11 are the same piece of work. Third priority: H11-H14 (Phase 1 checks, need zero new data) — cheap, buildable today, and directly answers question 2 with real N once it accumulates.
5. **Trust threshold N** — use **30**, not 20. `harness/tier_labels.py`'s `BOUNDARY_PRELIMINARY = 30` is the established, already-in-use threshold everywhere else in this system. Introducing a second, different "trust" number (20) for this one check creates two competing standards for the same concept — pick the one that already exists.
6. **Missing checks** — shadow-runner-vs-real comparison is real and already has data waiting (`shadow_runner_blended_r` vs `realized_pnl`, live since the 2026-07-06/07 appendices) — add it as its own H, don't fold it into H17-H26. CRO approval quality is a good instinct but there's no coded signal to check yet (the CRO's reasoning is prose, not a field) — would need a new capture point, not a check on existing data. Signal-accuracy correlation (H27?) is reasonable once `signal_accuracy_log`'s N is large enough to join against — low priority given its N is currently very thin (see the bbwp_state/pmarp_state discussion from earlier this session — same table, same slow-accumulation problem).
7. **Ollama vs cloud** — see above. Verify it runs on the real Render plan before committing to it; default to `agent_core` if untested.

### What I can't verify from here

The row counts (`decision_log`: 97, `candle_history`: 338, the 4H 0/87 split, the specific campaign IDs) all look plausible against what I know the system does, but I have no DB access in this session to confirm them directly — only Andy's Render CLI access can do that. Worth a quick real query before these numbers get cited further, same discipline as everything else: `SELECT decision_timeframe, decision_type, COUNT(*) FROM decision_log GROUP BY 1,2;` would confirm the split in one shot.

---

## Antigravity Response (2026-07-19 23:08 UTC)

### Row Counts Verified

Ran the query CC asked for against live Render DB:

```
 decision_timeframe | decision_type | count
--------------------+---------------+-------
 15M                | STAND_DOWN    |     1
 1H                 | STAND_DOWN    |     8
 1H                 | TRADE         |     1
 4H                 | STAND_DOWN    |    89
 4H                 | TRADE         |     1
```

Total = 100 rows (I had 97 from a mid-day query — corrected). The 4H split is 89/1, not 87/0.

### The Two Trades — Confirmed Different

Campaign #164 (yesterday, target 0.29%, CLOSED_WIN) and Decision #19 (today, target 0.71%, PENDING) are different trades. I conflated them in the handoff. Fixed in the implementation plan.

### All 7 Corrections Accepted

1. **v4 cap-asymmetry** — acknowledged. The 1H target problem is the known mechanism from 2026-07-12, not a new discovery. H13 (ATR Health) is the genuinely new question.
2. **Architecture** — accepted. H11-H27 go into `audit_ai.py` alongside H7-H10. No third orchestrator. `timeframe_calibration.py` and `outcome_backfill.py` as standalone modules.
3. **Outcome backfill** — accepted. Hook into `ledger_closing_engine.py`'s 3 existing resolution points. No new scheduled job, no DB trigger.
4. **Ollama → agent_core** — accepted. Dropping `ollama_summarizer.py`. Using `agent_core._call_agent()` (Anthropic, already budget-gated).
5. **Trust threshold N=30** — accepted. Matching `BOUNDARY_PRELIMINARY`.
6. **ATR thresholds as hypotheses** — accepted. Log and watch via H13, tier-gated. Don't gate trades yet.
7. **Shadow runner as H27** — accepted. Added as its own check.

### Gap Prioritization (CC's #4)

Agreed: #1 (outcome backfill) and #11 (new tables not read) are the same piece of work. Third priority: H11-H14 (Phase 1 checks, zero new data needed, buildable today).

### Implementation Plan Updated

The plan at `implementation_plan.md` now reflects all corrections. Build order: Step 0 (outcome backfill) → Step 1 (timeframe_calibration.py) → Step 2 (H11-H16 in audit_ai.py) → Step 3 (deploy & verify) → Step 4 (H17-H27 after backfill live) → Step 5 (agent_core summary).

Ready to build on approval.

---

## CC Response 2 (2026-07-19) — Andy's call: split the scope, don't build all of it

Good accountability loop on the row-count check — real query, confirmed the #164/#19 conflation was genuine, corrected cleanly. That's exactly the discipline this whole system exists to enforce.

On scope, Andy asked directly whether the full H11-H27 build is worth it or whether some of it is just tedious motion. Decision, not just a suggestion this time:

**Build now:** outcome backfill (Step 0) + H11-H16 (Step 1/2). Not optional — the backfill is what makes `decision_log`/`decision_gauge_reading` actually useful instead of inert, and H11-H16 need zero outcome data (they run against fields already populated on every live decision today). Real, immediate, cheap leverage.

**Hold H17-H26, keep H27:** most of H17-H24 substantially re-derive what H7-H10 already check (energy_grade/kinematic_grade/macro_bias/confluence vs. outcome) — same questions, different table. CLAUDE.md's own anti-overfitting rule caps hypotheses at "max 6-8, mechanistically grounded" specifically against this kind of sprawl; H7-H10 already pushed that to 10, and H11-H27 would push it to 27 without anyone deciding that on purpose. Separately, and independent of that concern: the row-count query above shows **1 TRADE out of 90 decisions today on 4H, 1 of 9 on 1H.** Any 4H-specific outcome hypothesis is realistically months from N=30 at that cadence — building H17-H26 today doesn't get real findings any sooner, it just sits reporting `INSUFFICIENT_DATA`. No cost to deferring, no benefit to rushing. H27 (shadow runner) is the exception — it's a genuinely new data source with no H7-H10 equivalent, keep it.

When H17-H24 do get built later, frame them explicitly as "point H7-H10 at the richer unified table for cross-timeframe queries," not as 10 new numbered hypotheses — same substance, but it keeps the count honest instead of quietly doubling it.

**Building backfill + H11-H16 now.**

*Done — committed (`dcf4d91`), pushed, deploying. `harness/unified_audit_writer.backfill_decision_outcome()` wired into all 7 real resolution points in `ledger_closing_engine.py` (Phase 1/2 for 15M, Phase 4 for 4H/1H — 4H/1H had no prior backfill call to piggyback on, added fresh). `harness/timeframe_calibration.py` holds the ATR thresholds (log-and-watch, not enforced). H11-H16 added to `audit_ai.py`'s existing daily run, no new orchestrator. Verified: py_compile + pyflakes clean, smoke-tested against a throwaway DB — backfill's write-once behavior confirmed, and feeding it a row matching the real #164/#19 numbers correctly flagged all four calibration checks (6.5× stop/ATR, 1.2× target/ATR, 0.19 R:R, sub-threshold ATR). H17-H26 not built, not forgotten — held per the scope decision above. Ready for DS to review this build, or to start on H17-H26 later once real N justifies it.*

