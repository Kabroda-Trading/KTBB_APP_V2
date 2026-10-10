# LIVE_BEHAVIOR_BASELINE.md - the live stack's expected behavior, pinned from the study

**Created:** 2026-10-04 13:58 CT, at Andy's directive ("log this mindset in core MD files, not memory").  
**Updated:** 2026-10-10 14:32 CT, at Andy's directive (expanded to Unified Portfolio Baseline: Traveler + Alt Matrix).  
**Purpose:** the single cited reference for what the live Traveler system AND Alt Matrix are EXPECTED to produce, so live results are compared against the study's measured distribution - never against memory, mood, or a recent losing streak. Read alongside `EVALUATION_PROTOCOL.md` (the monitoring plan) and `ALT_MATRIX_DEPLOYMENT_RUNBOOK.md`.  
**Status:** binding reference for all live-vs-baseline comparisons (daily reviews, 30/90-day checks, CC site audits that touch outcome/exit-reason fields).  

---

## 1. The stack this baseline describes

1. **Bitcoin Traveler:** **E1 stream + F_A sizing gate + C5_T-M_EXIT + BBWP_BURN_70** (lab_shipprep_candidate.md §1; measured +0.1439 avgR, 29.58% maxDD, PASS 5/5 on the walk-forward).
2. **Alt Matrix:** **Daily 200 SMA macro filter + 4H 21/55 EMA Silver Cross + 1.5x ATR stop + +2.0R Breakeven Ratchet + 4H 21 EMA trail** (SOL + ETH, measured +42.29R, 38.4% WR, 3.38:1 payoff asymmetry).

---

## 2. Bitcoin Traveler Primary Stream: Expected Outcome Mix

Computed 2026-10-04 from `brain/audit_evidence/traveler_study/lab_composite_stack_rows.csv` (n=1,538 journeys, baseline stack E1+F_A+C5):

| Outcome | Share of all journeys | Meaning |
|---|---|---|
| **C5_EXIT (momentum-decay)** | **77.4%** (1,191/1,538) | the modal outcome; the system cuts the journey when fuel decays |
| — of which C5 exits that are WINS | 64.1% of C5 exits (764) | small wins, avg **+0.2239R** - decay usually fires after a move in favor |
| — of which C5 exits that are LOSSES ("nicks") | 35.8% of C5 exits (426) | **27.7% of ALL journeys** are nicks, avg **−0.1292R** |
| **T1 hit (full exit)** | **12.4%** (190) | the clean +1.0R win |
| **STOP (full −1R-class loss)** | **7.9%** (121) | the worst outcome; rare by design |
| **TIME exit** | **2.3%** (36) | journey cap reached |

**Headline: win rate 62.0% (954/1,538), loss rate 37.9% (583), 1 flat. avgR +0.1357** (lab_composite_stack_results.md:16; the shipped stack with BBWP overlays measures +0.1439).

Roughly **3 of 5 trades end in a quick momentum-decay exit near flat** - and most of those are small *wins*, not losses. Roughly **1 in 3.6 trades is a small nick** (avg −0.13R). Only **1 in 12 trades reaches T1**, and only **1 in 12.7 takes a full stop**. The edge is tail-carried and exit-managed: a day, a week, even a month of small nicks and near-flat C5 exits is NOT evidence of failure - it is the measured shape of the edge. A trade that followed the system and exited at exhaustion is a **successful trade** (Andy ruling, 2026-10-04 12:04 CT), regardless of sign.

---

## 3. Nick depth distribution (how deep is a normal nick)

From the 426 C5-loss journeys: median **−0.046R**, p25 −0.123R, worst −1.3744R. Nicks deeper than −0.4R are ~8.7% of study nicks - expected occasionally, not alarming.

---

## 4. The monitoring cadence (Andy-set, 2026-10-04)

| Checkpoint | Sample | What it answers |
|---|---|---|
| **Daily review** | every fill | behavior fidelity: did live match the spec for THIS trade (entry basis, C5 legs, sizing, emails)? - the standing audit, not a statistical read |
| **~30 filled journeys (~30 days at current ~0.75 fills/day)** | n=30 | first crude read: is the outcome mix in the right NEIGHBORHOOD? |
| **~90-100 filled journeys (~90 days)** | n=90+ | the percentile read: is live within the expected win-rate / nick-rate / stop-rate bands with real confidence? |

---

## 5. Flag thresholds (pre-committed; a flag triggers a re-look, never a verdict)

- Outcome mix shifted **>20 points** from §2's table (e.g., stop share ~28% instead of 7.9%).
- Live win rate materially below 62% at the 90-day checkpoint.
- Nick DEPTH distribution drifting deeper than §3 (median well below −0.05R, or nicks routinely worse than −0.4R).
- maxDD materially exceeding the shipped arm's **29.58%** (dollar equity).
- Fill rate <90% (D2/D2b predict 95-98%).

---

## 6. How the check is run (stateless, no memory)

1. Query prod DB: all filled traveler plans + their exit outcomes (exit reason, R realized, fill/exit times).
2. Classify each into T1 / C5-win / C5-loss / BBWP / STOP / TIME.
3. Compare shares against §2's table and depths against §3.
4. Cite this file + `lab_composite_stack_rows.csv` in the report.

---

## 7. Re-arm Stream Baseline Expectation (Arm A, same-lock-day — Andy ruling 2026-10-06)

Monitored separately from the primary stream (via `is_rearm: true` DB tag). Computed 2026-10-06 from `lab_rearm_after_c5_rows.csv` (n=488, ARM A):

| Metric | Measured Baseline (Arm A) | Monitoring Watch Threshold |
|---|---|---|
| **Pooled avgR** | **+0.4881** | Materially below +0.15R over 30 re-arm trades |
| **T1 hit rate** | **36.9%** (180/488) | — |
| **C5_EXIT rate** | **55.5%** (271/488) | — |
| **STOP rate** | **7.6%** (37/488) | **Materially above ~12% triggers kill-switch review** |
| **Churn (<1h re-exit)** | **28.5%** (139/488) | Expected; netted in avgR |
| **Clear / Retest timing**| **Median ~6h / ~6.1h** | — |

---

## 8. Alt Matrix Baseline Expectations (SOL & ETH 4H Swing — Andy directive 2026-10-10)

Monitored alongside Bitcoin Traveler as the second engine of the unified Kabroda portfolio. Computed 2026-10-10 from the 4.5-year walk-forward ledger (`walkforward_sol_ledger.csv`, `walkforward_eth_ledger.csv`, n=73 trades, 2022–2026):

| Metric | Measured Baseline (SOL + ETH) | Monitoring Watch Threshold |
|---|---|---|
| **Trade Frequency** | **~16.2 trades / year** combined (~1 trade every 3 weeks) | > 35 trades/year (indicates over-trading/filter failure) |
| **Combined Win Rate** | **38.4%** (SOL: 47.1%, ETH: 30.8%) | Materially below 28% over 15+ trades |
| **Pooled avgR** | **+0.579R** gross / **+0.821R** (SOL) & **+0.368R** (ETH) net | Trailing avgR dropping below +0.20R over 15+ trades |
| **Payoff Asymmetry** | **3.38:1** (avg win **+2.88R**, avg loss **−0.85R**) | Average win falling below 2.0x average loss |
| **Max Drawdown** | **6.15R / 14.6%** ($39,010 at $5,000 risk) | Drawdown exceeding 10.0R |
| **Breakeven Ratchet** | Stop moves to BE (+0.1R) on reaching >= +2.0R MFE | Failure to amend stop on exchange |
| **Trailing Exit** | 4H 21 EMA close exit once above +2.0R | Manual early exit or failure to close |

### What this means for the Unified Portfolio Review
1. **Low Frequency, High Asymmetry:** Bitcoin Traveler evaluates every 5 minutes and takes hundreds of trades; Alt Matrix evaluates every 4 hours and takes ~16 trades in an entire year. Variance on Alt Matrix plays out over months, not days.
2. **The Adjustment Core:** If live Alt Matrix performance deviates from this baseline, the core levers to inspect are:
   - D1 Macro timing: Did the 00:00 UTC Daily 200 SMA lookahead or boundary shift?
   - D1 Funding Veto: Did extreme funding (> +0.05%) veto valid trends, or did a high-funding trade slip through?
   - D2 Stop Geometry: Is 1.5x ATR(14) being clipped prematurely by wicks?
   - D3 Breakeven Ratchet: Is +2.0R MFE too tight (giving back winners) or too loose?
   - Portfolio Concurrency: Did the 30% margin floor or 20% risk cap unnecessarily block an alt entry when BTC was idle?
3. **Standing Rule:** A trade that enters per D1/D2 and exits per D3 is a **successful execution**, regardless of whether that specific trade won or lost. Adjustments are only ever made from statistically significant deviation against this pinned baseline, never from session memory.
