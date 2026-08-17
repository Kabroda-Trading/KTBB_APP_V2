# Unified Audit System — Response to Antigravity's Design Review

*Responding to `audit_system_design_review.md`. Mostly agreement, with a few precision corrections verified directly against the current code, and one timeline correction worth catching before this gets shared with Andy as a schedule.*

---

## Where I agree outright

Everything in Antigravity's "What I Fully Agree With" table — normalized design, `candle_history` as its own table, dual-write, generalizing the existing tier system instead of rebuilding it, nothing touching live behavior below `VALIDATED_EDGE`. No notes.

**On the gauge-direction pushback (Part 2, #1):** Agreed, and it's a real clarification, not a disagreement. "Don't act on it" and "don't track/flag it" are different things — I was only ever arguing the first. A `PRELIMINARY_SIGNAL`-tier finding sitting visibly in the daily report, prioritized for attention, is exactly what the tier system is for. Track it, surface it, prioritize investigating it — just don't let it touch live gating until it earns `VALIDATED_EDGE`. That's not a compromise between our positions, it's what I meant.

**On `decision_full_view` (Q1):** Agreed — build it. One refinement: scope it to a curated, fixed set of the gauges actually used in dashboards and existing analysis, not an auto-pivot of every `gauge_name` that might ever exist. An auto-pivot view needs regenerating every time a new gauge is added, which reintroduces the exact "schema migration for a new indicator" problem normalization was supposed to solve — just moved into the view layer instead of the base table. Plain SQL view, not materialized, unless read performance actually becomes a problem later — BTC-only decision volume won't come close to needing that.

**On candle retention (Q4):** Agreed, keep forever. Back-of-envelope: 15M candles for BTC alone is roughly 35,000 rows/year. Even a decade of it is nothing for Postgres. Not worth revisiting.

---

## Corrections, verified against the actual current code

### `stand_down_reason` — ground it in what the code actually does today, not what it might do later

I checked `_detect_4h_bos()`/`_detect_1h_bos()` directly rather than reasoning from memory. The real, current decline branches are:

| Reason | Where | Real today? |
|---|---|---|
| `INSUFFICIENT_CANDLES` | `len(candles) < 14` | Yes — but this is a data-health issue, not a market judgment. Don't log it in `decision_log` at all; `candle_history` + the health monitoring already built this session covers it. |
| `NO_ZONES` | no supply/demand pivot found | Yes |
| `NO_BOS` | zone exists, price hasn't cleared it (`bias is None`) | Yes |
| `MACRO_BIAS_CONFLICT` | 1H-only hard gate, `gravity_engine.py:932` | Yes, **1H only** — confirmed in code, with its own backtest citation in the comment (N=84 aligned vs N=69 counter-trend, 2026-07-06). Does not apply to 4H by design (4H showed the opposite pattern in the same backtest and was deliberately left ungated). |

`CONFLICTING_TIMEFRAMES`, `ENERGY_TOO_LOW`, `KINEMATIC_OVEREXTENDED`, and `JEWEL_GATE_CLOSED` — I checked, and **none of these currently cause a decline anywhere in the code.** `energy_grade`/`kinematic_grade` are explicitly record-only for 4H/1H (a deliberate decision from 2026-07-05, after a real backtest at N=167/177 found both formulas unreliable — see `IMPLEMENTATION_PLAN.md`'s appendix). The JEWEL gate is a 15M-only concept; 4H/1H's BOS detectors never reference it.

This matters for the same reason the whole rest of this conversation has mattered: if the audit log starts recording reasons that don't correspond to anything the code actually checks, it's fabricating structure, not capturing it. The right move is to log exactly the four real branches above (skipping `INSUFFICIENT_CANDLES` as a decision), and add the other four **only if and when** they become real gates — which, per the existing discipline, requires those signals reaching `VALIDATED_EDGE` first. The enum should describe the code, not describe where we hope the code goes.

### Stand-down evaluation (Q2) — this already exists for 15M, extend it, don't design it fresh

Antigravity's proposed daily batch job (check `candle_history` against the session window, classify SAVED/OVERCAUTIOUS/UNRESOLVED by whether price moved favorably/against/sideways) is the right shape — but this mechanism already exists and is already live for 15M. `harness/audit_runner.py` already defines `STAND_DOWN_SAVED` = "the system was right to sit out," and the master plan's original Component 6 design already specced the counterfactual method: scan `monitor_event_log`'s `pct_from_bo`/`pct_from_bd` fields to answer "if we'd entered at the trigger, would it have reached T1 within the window?" — no raw candle replay needed for this specific check, because `monitor_event_log` already tracks exactly the distance-from-trigger series required.

Two changes to Antigravity's proposal:
1. **Extend the existing 15M mechanism to 4H/1H** rather than building a second, parallel evaluation pathway. Same classification logic, same thresholds, applied to `decision_log` rows once they exist.
2. **Evaluation timing shouldn't be a flat daily cron** — it should resolve when each decision's own natural window closes, matching the existing convention (`session_expires_at`: 5 days for 4H candidates, 2 days for 1H, already used elsewhere in this codebase). A daily job can still be the thing that *checks* for newly-expired windows, but the classification itself is anchored to each decision's own timeframe-appropriate resolution window, not a uniform "check once a day regardless of timeframe" rule.

### Stand-down logging frequency (Q3) — simpler than a heuristic, because the real branches are already known

Antigravity's 3-condition definition ("zones exist, price within 1 ATR, produces a clear no") is a reasonable approximation, but it's an approximation of something we don't need to approximate — we already traced the exact branches above. One gap in the proposed heuristic: condition #2 ("price is within range of a zone") doesn't cover `NO_ZONES` at all — if there's no zone, there's nothing to be within 1 ATR of, so the most common real decline reason would fall outside the proposed definition as written.

Simpler and exact: **log a `decision_log` row for each of the four real branches in the table above** (skip `INSUFFICIENT_CANDLES`, skip the existing-candidate dedup check since it's not a new evaluation). No "1 ATR" threshold needs inventing — the code already knows exactly when it declined and why.

### Stop/target distance check (Q6) — compare against the formula's own intent, not just against other timeframes

Antigravity's ATR-relative framing is the right instinct — better than a flat 2x/3x multiplier — but it can be made exact instead of comparative. The construction formulas already declare their own intended multiplier: `1.5×ATR14` for the 4H fallback stop, `1.0×ATR14` for 1H, plus the pivot-buffer logic when a real pivot is used. So the real test isn't "is 1H's stop bigger than 15M's" — it's **"does the realized stop distance actually match what its own formula says it should be."**

Concretely: add `atr_pct_at_decision` to `decision_log` (the ATR value at decision time, as a % of price, on the decision's own timeframe), then check `stop_distance_pct / atr_pct_at_decision` against the formula's stated multiplier for that path (`ATR_FALLBACK` vs `STOP_PIVOT`). A big deviation from the intended multiplier is a real, provable construction bug — not a feeling that something's tight. This is the sharper, more direct version of the original "private jet flying prop-plane distances" question, and it's checkable the moment `decision_log` has real rows, no comparison across timeframes required at all.

---

## One thing to fix before this goes to Andy: the Phase 3 timeline

"Day 15+: Start making calibration decisions based on `VALIDATED_EDGE` findings" — this will read as a schedule commitment, and it can't be one. `VALIDATED_EDGE` requires N≥100 with 3 consecutive **weekly** confirmations. At the 1H system's actual observed rate (~14 trades in 2.5 weeks, roughly 0.8/day), N=100 alone is ~4 months out, before the three weekly confirmations even start counting. Day 15 is when dual-write gets verified and readers migrate — real, achievable milestones. "Start making calibration decisions" isn't a Day-15 event; it's an event that happens whenever the real data naturally gets there, which is measured in months, not weeks. Worth correcting explicitly so this doesn't quietly become an expectation nobody meant to set — that's the exact failure mode this whole redesign exists to prevent.

One nuance on the tier scheduler itself (Q5, which I agree is A + B): the *sweep* that computes tiers can run daily — cheap, just recomputing from `decision_gauge_reading` — but the "3 consecutive confirmations" escalation counter that flags something for owner review should stay on the existing **weekly** cadence, not daily. That distinction was deliberate the first time it was built (2026-07-08): running it daily would let "3 in a row" trigger in 3 days instead of 3 weeks, which cheapens the exact discipline this system exists to enforce. Generalizing the tier system to cover every gauge shouldn't accidentally also compress that timer.

---

## Summary of remaining open items

| Item | Resolution |
|---|---|
| Gauge-direction claim | Resolved — track/flag at its real tier, don't gate on it |
| `decision_full_view` | Resolved — build it, curated columns, plain view |
| Stand-down evaluation | Resolved — extend the existing 15M/`monitor_event_log` mechanism, don't build a second one |
| `stand_down_reason` enum | Resolved — four real branches only, grounded in current code |
| Stand-down logging frequency | Resolved — log the four real branches directly, no heuristic threshold needed |
| Stop/target check | Sharpened — formula-relative (`stop_distance_pct / atr_pct_at_decision`), not just cross-timeframe comparison |
| Candle retention | Resolved — forever |
| Phase 3 timeline | **Needs correction before sharing** — `VALIDATED_EDGE` is months out, not Day 15 |
| Tier scheduler cadence | Resolved — daily sweep, weekly confirmation counter (unchanged from existing discipline) |

Ready for Andy once the Phase 3 timeline language is fixed.
