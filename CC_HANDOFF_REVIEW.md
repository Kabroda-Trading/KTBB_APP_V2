# Unified Trade Audit Log — Reviewed & Revised

*Original proposal: `cc_handoff_prompt.md` (Antigravity, 2026-07-18). This document is Claude Code's independent review, verified directly against the live codebase — not a rewrite of Antigravity's prose, a correction of its claims where they don't hold up, plus a tightened version of the plan.*

---

## Bottom line up front

**Don't build the new table yet.** The finding driving this whole proposal — "energy=WEAK + kinematic=TANGLED wins, MODERATE + PRIMED loses, the gauge is calibrated backwards" — is not a new discovery. It's a re-derivation, on roughly 5 trades with several fields missing, of something this project already found on a real backtest of N=167 (1H) and N=177 (4H), already decided how to handle, and already shipped code for. Building a ~70-column table and deprecating two existing, actively-used tables to chase a pattern this thin would repeat a mistake this exact project already made and corrected once before (see "The precedent" below).

That doesn't mean the underlying goal is wrong — "one place to see every gauge for every decision" is a real, worthwhile goal. It means the schema and the deprecation plan need to be scoped against what's already built, not designed from scratch as if `harness/` and `audit_ai.py` don't exist.

---

## 1. The core finding doesn't survive its own evidence

Look at the 1H wins table exactly as presented in the original doc:

| Date | Energy | Kinematic |
|---|---|---|
| 07/01 | WEAK | — |
| 07/03 | **MODERATE** | — |
| 07/04 | **MODERATE** | — |
| 07/14 | WEAK | TANGLED |
| 07/18 | WEAK | TANGLED |

The doc's own text says "Wins have in common: WEAK energy." Two of the five listed wins are **MODERATE**, not WEAK — that's stated as a clean pattern when it's actually 3-of-5. Kinematic grade is worse: it's blank ("—") for three of the five wins, so "TANGLED kinematic" as a common factor is really based on 2 confirmed cases out of 5, not 5.

The losses side is worse than thin — it's not shown at all. Nine losses are collapsed into a single row: "Various | Various | Various | Various | ❌ 9 LOSSES." The claim "Losses have in common: MODERATE energy, PRIMED kinematic, BULLISH everything" can't be checked against anything in the document, because the actual gauge values for those nine trades were never pulled out the way the five wins were. A pattern claim built on 5 cherry-picked wins (partially self-contradicting) versus 9 losses with zero shown detail isn't evidence of a miscalibrated gauge — it's an N=14 sample dressed up as a finding.

**Recommendation:** before anyone touches schema, pull the same table for all 9 losses with their actual energy/kinematic/dominant/macro values filled in. If the pattern still holds with real numbers on both sides, that's worth taking seriously. As presented, it isn't yet.

---

## 2. The precedent — this was already investigated, at real N, and a decision was already made

`IMPLEMENTATION_PLAN.md`'s master plan (this repo, appendix dated 2026-07-05, "PUNCH-LIST ITEM #4: ENERGY-GRADE") already ran this exact question through a proper backtest:

> Real results, N=167 (1H, 90 days) and N=177 (4H, 400 days)... The 15M-style `kinematic_grade` formula shows TANGLED *outperforming* PRIMED (+0.165R vs +0.103R) — backwards from what the label implies... 4H: The 15M-style formula is backwards again: OVEREXTENDED performs *best* (+0.184R), not worst.
>
> **Owner decision: neither formula gates candidate creation today.** Record-only — keep the current formula unchanged, add `kinematic_grade` as a second, purely observational column, block nothing... revisit enforcement once real accumulated production data (not backtest) clears N≥30 per timeframe with a clean, stable signal.

That's the same "the gauge reads backwards" finding, on a sample 30x larger, already implemented as a record-only observational field (`CampaignLog.kinematic_grade`, live since 2026-07-05), with an explicit, already-agreed threshold for when it's safe to act on (N≥30 per timeframe, clean and stable). The new proposal doesn't cite this appendix and reads as if the finding is novel. It isn't — the data collection this proposal wants already exists for exactly this hypothesis, it's just not yet at the N the project already agreed to wait for.

Also worth knowing: that same backtest found `energy_grade` and `WEAK`/`MODERATE` perform "almost identically" on 1H (+0.119R vs +0.140R) — i.e. at real N, energy grade wasn't a clean signal either direction. That complicates "WEAK energy = good setup" as a takeaway, on top of the small-sample issue above.

**This doesn't mean stop looking.** It means: check `harness/`'s existing tier system (`DIRECTIONAL_OBSERVATION` → `PRELIMINARY_SIGNAL` → `PROVISIONAL_FINDING` → `VALIDATED_EDGE`, N-gated, with real binomial significance testing in `harness/binomial_checkpoint.py`) before building a parallel investigation from scratch. That machinery was built for exactly this question.

---

## 3. The deprecation plan understates its own blast radius

Implementation step 5 says "Deprecate `session_audit_log` for audit purposes" and step 6 says "Keep `campaign_logs` for trade execution tracking only" — as if these are low-cost housekeeping moves. They aren't. I grepped both tables' actual usage:

- `session_audit_log` feeds `harness/audit_runner.py`'s H1–H6 hypotheses directly, the entire `harness/query_layer.py`/`tier_labels.py`/`binomial_checkpoint.py` statistical framework, and `audit_ai.py`'s daily digest.
- `campaign_logs.is_canonical` is read in at least 15 places across `main.py`, `ledger_closing_engine.py`, `gravity_engine.py`, `market_radar.py`, `publisher_crew.py`, `performance_auditor.py`, and the test suite — it's the join key for basically every dashboard KPI, the shadow-runner mechanic, and the ledger closing loop's win/loss detection.

Redefining what these tables mean without a plan for every one of those call sites is a much bigger project than "add a new table." If the goal is genuinely a unified view, the lower-risk path is: build the new table as a read model that's *populated from* the existing tables (or alongside them), prove it's useful, and only then talk about retiring anything — not deprecate first and migrate consumers after.

---

## 4. Schema-level issues, verified against the actual code

- **`tf_15m_jewel_state` / `tf_1h_jewel_state` / `tf_4h_jewel_state` don't correspond to any real field.** I checked `battlebox_pipeline.py` directly — the JEWEL reading returns `bbwp_state`, `pmarp_state`, `kinematic_grade`, and a nested `jewel: {signal: ...}` dict. There is no `jewel_state` key anywhere in the codebase. Either this means `jewel.signal` and should be named that, or it's aspirational and needs a real source before it goes in a schema.

- **Wide, fully denormalized table (~70 columns, `tf_15m_*`/`tf_1h_*`/`tf_4h_*` as parallel column families).** This is a real tradeoff, not a free choice: easy to query "everything about trade X," bad for "every 1H reading across all trades" (constant NULL-filtering) and bad for extensibility (every new indicator = 3-5 new columns, backfilled NULL on every historical row). Given this project's existing convention — incremental `ALTER TABLE` additions in `database.py`'s migration loop — a normalized `trade_audit_log` (one row per decision) + `trade_audit_gauge` (one row per timeframe reading, generic `timeframe`/`metric_name`/`value`/`state` columns) fits the established pattern better and doesn't require a schema migration every time a new indicator is added. Worth deciding deliberately, not by default.

- **Candle snapshots (`candle_snapshot_15m/1h/4h TEXT`, 50 candles each, on every row).** The doc's own Q3 already flags this as an open question, correctly. My answer: overkill as designed. 1H/4H scan every 15 minutes, continuously (confirmed directly in `gravity_engine.py` — this isn't once-a-day like 15M). If a row gets written on every scan tick including stand-downs, three JSON candle blobs per row at that frequency will bloat the table fast. Scope candle snapshots to `decision_type = 'TRADE'` rows only — the actual replay use case doesn't need it for the "nothing happened" rows.

- **Row frequency for 1H/4H stand-downs is undefined.** The schema allows `decision_type = 'STAND_DOWN'`, and Q6 asks about analyzing stand-down days — but doesn't say whether that means one row per 15-minute scan tick (a lot of rows, fast) or one row per detected non-event within a scan cycle. This is the same question already on the table from earlier in this same working session: 4H/1H currently has *no* stand-down logging at all (`_detect_4h_bos()`/`_detect_1h_bos()` just `return` silently on a no-signal tick), and the 15M system's existing pattern (`SessionAuditLog.approval_status = STAND_DOWN` with `outcome_type` in `STAND_DOWN_SAVED`/`STAND_DOWN_OVERCAUTIOUS`/`STAND_DOWN_UNRESOLVED`) is the shape to mirror for 4H/1H, not reinvent. This proposal should connect to that existing thread instead of designing stand-down tracking as a brand-new concept.

---

## 5. Answers to the doc's questions

1. **Schema review — is anything missing?** Wrong question until §1–§3 above are resolved. The schema is thorough on breadth; it's premature on justification.
2. **Wide unified table vs. fix existing tables?** Neither, exactly as framed. See the normalized-child-table alternative in §4, and see §3 on why "deprecate the old ones" is a much bigger job than stated.
3. **Candle snapshots — good idea or overkill?** Overkill for every row; reasonable scoped to actual trade fires only.
4. **Timing — gauges at execution time vs. lock time?** Real, legitimate problem, independent of everything else in this doc. Worth solving on its own regardless of what happens with the table.
5. **Market regime as a column?** Reasonable, and it already exists — `macro_bias`, `weekly_200sma_position`, and the daily-regime derivation logic in the master plan's Component 3 (`DAILY_BULL`/`DAILY_RECOVERY`/`DAILY_NEUTRAL`/`DAILY_DISTRIBUTION`/`DAILY_BEAR`) already covers this. Reuse it rather than adding a new "season" concept.
6. **Stand-down analysis vs. price action that moved anyway?** This is exactly what `outcome_type = STAND_DOWN_OVERCAUTIOUS` already measures for 15M (a stand-down that would have won). Extend that concept to 4H/1H — don't invent a second mechanism for the same question.
7. **Anything else alarming?** Yes — §3 (deprecation blast radius) and the fact that this entire investigation currently duplicates `harness/`/`audit_ai.py` rather than building on it.

---

## Recommended next step (instead of building the table)

1. Pull the real, complete gauge values for all 9 1H losses (not "Various") and re-run the win/loss comparison honestly. If it survives, it's worth acting on; if not, this was measurement noise at N=14.
2. Check what N the `kinematic_grade`/`energy_grade` observational columns have actually reached in production now (this connects directly to the N≥100 bar conversation already underway this session) — the infrastructure to answer "is this gauge really backwards" already exists and is already collecting data.
3. If, after that, a genuine gap remains that `harness/`/`audit_ai.py`/`session_audit_log`/`campaign_logs` truly can't answer, scope a *narrow* extension (most likely: the normalized gauge-reading child table from §4, or filling specific missing columns on the existing tables) rather than a full replacement.
4. Keep the execution-timing problem (Q4) and the 4H/1H stand-down logging problem (Q6, already on the table from earlier this session) as their own, smaller, independently-justified pieces of work — both are real regardless of how the bigger table question resolves.
