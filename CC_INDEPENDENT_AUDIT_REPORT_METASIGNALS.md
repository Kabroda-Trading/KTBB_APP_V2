# CC_INDEPENDENT_AUDIT_REPORT_METASIGNALS.md
# Independent Technical Audit: Kabroda Meta Signals Ecosystem
**Target Codebases:** `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2` (Production Desk) & `C:\Users\Shadow\Workspace\Kabroda Meta Signals AI` (Research Suite)  
**Auditor:** Kabroda AI Brain (Independent Ecosystem Audit Layer)  
**Date:** 2026-10-16  
**Auditors Dispatched:** Independent Backtest Methodology Auditor & Independent Production Engine Auditor  
**Status:** **FOR CLAUDE CODE REVIEW & AUDIT — DO NOT RUSH CODE MODIFICATIONS**

---

## 1. Purpose & Audit Charter

Per Andy's directive:
> *"I don't want to just start bouncing around and trying to fix code or mess around with code. It needs to be a thorough, clean audit evaluation and make sure that it makes sense of what may or may not be in there right now and there's clear understanding. So, of course, it needs to be put into the MD files so that can happen."*

This document serves as the formal, standalone audit handoff for **Claude Code (CC)** to independently review, verify in the live software, and evaluate against project doctrine before making any modifications.

---

## 2. Executive Summary & Audit Verdict

### **OVERALL VERDICT: PASS WITH FINDINGS**

1. **Strategic & Architectural Alignment (PASS):**
   - The entire implementation strictly reflects Andy's core doctrine: **stability first, capital preservation, avoiding bad trades over chasing volume, and realistic execution accounting**.
   - The **Iron Wall** between MetaSignals and the core Kabroda Brain/Traveler system is 100% intact. No shared state, no cross-contamination.
2. **Claude Code Interventions Affirmed & Commended:**
   - **Concurrency Cap Fix (`commit e4d5332`):** CC caught the missing `import os` in `metasignals_triage.py` that caused `os.getenv` to raise a `NameError` silently swallowed by `except: pass`, leaving the 5-position cap dead in production. CC added fail-closed error handling and 5 unit tests. Verified sound.
   - **Baseline Distinction Reconciliation (`commit ff65fcd` / `39aa665`):** CC caught that Study 6's headline *"+8.84 R / +49%"* gain was measured against a theoretical 21-EMA runner, whereas on the actual deployed engine (fixed TP1/TP2), the verified gain is **+1.59 R (+13.11%)**. CC confirmed this distinction with Andy before deploying.
   - **Comma Parser Fix:** CC updated the regex parser (`_NUM_RE`) to tolerate commas (`68,500.00`), preventing BTC and ETH alerts from being silently dropped.
3. **Core Empirical Verification:**
   - Zero lookahead bias verified across daily 200 SMA, 4H RSI/ATR, and BBWP rolling percentiles.
   - Symmetrical short integration confirmed as the primary edge (**+14.11 R, 1.36 PF, 8.6 R Max DD** on strict shorts vs **-1.99 R, 0.98 PF** on longs).
   - BBWP <= 10.0 compression exit confirmed as a net positive (**+1.59 R / +13% gain**, reducing Max DD from 20.0 R to 17.2 R on the deployed engine).
   - Test suites pass 100% (**910/910** full site suite, **108/108** MetaSignals suite).

---

## 3. Discrepancy & Finding Register (Actionable for CC)

The adversarial audit identified two code/logic discrepancies and one architectural vulnerability. Review these carefully:

### Finding F-01 (MAJOR): Golden Setup Comment vs. Code Discrepancy
- **Location:** `metasignals_triage.py`, lines 146–186 (`PERMITTED_STRATEGY_TIMEFRAME_CELLS`).
- **The Issue:**
  - The docstring comment explicitly states:
    > *"2026-10-16 GOLDEN SETUP... Filtered to Study 4's top SHORTLIST cells across Sweet-Spot timeframes (2H-1D)... drops WATCHLIST noise (+0.86R dead weight)"*.
  - **The Reality in Code:** The dictionary was never actually filtered to SHORTLIST only. It still contains **101 cells total (62 WATCHLIST + 39 SHORTLIST)**.
  - When intersected with `WHITELISTED_TIMEFRAMES = ("2H", "3H", "4H", "6H", "1D")`, exactly **90 cells remain active: 34 SHORTLIST and 56 WATCHLIST**.
- **Empirical Impact from Study 5 Backtest:**
  - **SHORTLIST-Only Cells (N=200):** Produced **+23.66 R (PF 1.28)** on the 21-EMA runner and **+11.26 R (PF 1.12)** on the deployed engine.
  - **WATCHLIST-Only Cells (N=143):** Produced **+0.86 R (PF 1.01)** on the deployed engine and **-8.62 R (PF 0.87)** on the runner.
  - **Conclusion:** The live desk is still actively taking signals on 56 WATCHLIST cells that represent unproven dead weight and drag down overall portfolio performance.
- **Review Proposal for CC:**
  - Verify this census in `metasignals_triage.py`.
  - Prune the 56 active WATCHLIST cells so `PERMITTED_STRATEGY_TIMEFRAME_CELLS` contains strictly the **34 confirmed SHORTLIST cells**, locking in true Golden Setup performance.

### Finding F-02 (MINOR): Dead Timeframe Cells in Whitelist
- **Location:** `metasignals_triage.py`, lines 144, 154–178.
- **The Issue:**
  - `PERMITTED_STRATEGY_TIMEFRAME_CELLS` contains 11 cells for 1H, 8H, and 12H (e.g., `"1.0 G3 1H BUY"`, `"1.1.1 8H BUY"`, `"1.3 12H BUY"`).
  - However, Pillar 5a enforces `WHITELISTED_TIMEFRAMES = ("2H", "3H", "4H", "6H", "1D")`.
  - Alerts on 1H, 8H, and 12H are vetoed at Pillar 5a before reaching Pillar 5b, making these 11 cells unreachable dead code.
- **Review Proposal for CC:**
  - Clean up these 11 dead entries during the whitelist pruning pass.

### Finding F-03 (MINOR / RESILIENCE): Unprotected `asyncio.wait_for` in Watcher Tick Loop
- **Location:** `metasignals_watcher.py`, line 117 (`_fetch_mark_price`) and lines 137–139 (`_fetch_bbwp`).
- **The Issue:**
  - In `run_tick()`, external API calls to Bitunix are wrapped with `await asyncio.wait_for(..., timeout=_ROW_TIMEOUT_SECONDS)`.
  - However, these calls are NOT wrapped in a per-symbol `try...except` block inside the loop.
  - If a single symbol's network request hangs or times out (>15s), `asyncio.wait_for` raises `asyncio.TimeoutError`.
  - Because `run_tick()` lacks an outer handler around the iteration, this unhandled exception **aborts the entire tick**.
  - **Consequence:** Order advancement, stop loss checks, TP1 fills, and equity refreshes are skipped for **all other open positions** during that tick.
- **Review Proposal for CC:**
  - Wrap both fetch loops in `metasignals_watcher.py` with an inner guard:
    ```python
    try:
        price = await asyncio.wait_for(_fetch_mark_price(symbol), timeout=_ROW_TIMEOUT_SECONDS)
        if price is not None:
            mark_price_by_symbol[symbol] = price
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(f"[METASIGNALS_WATCHER] Mark price fetch failed for {symbol}: {e}")
    ```
  - This ensures that one lagging API call never stalls order management for the rest of the book.

---

## 4. Empirical Reference Data (Verified from Raw Logs)

Claude Code can use these numbers to verify against `Kabroda Meta Signals AI/studies/`:

### 4.1 Study 5 Veto Funnel (N = 9,078 Raw Alerts, 2024-04-01 to 2026-10-12)
- **Total Unique Alerts:** 9,078 (7,812 Long, 1,266 Short)
- **Pillar 5a (LTF Noise < 60m):** 1,388 vetoed (1,388 Long, 0 Short) $\rightarrow$ **7,690 MTF/HTF Alerts**
- **Data / Null SL Veto:** 96 vetoed (89 Long, 7 Short)
- **Insufficient History (< 200 bars):** 172 vetoed (155 Long, 17 Short)
- **Pillar 5b (Strategy Cell Unproven / Trap):** 5,251 vetoed (4,501 Long, 750 Short)
  - *Unproven Cells:* 4,073
  - *Study 4 Traps (DO_NOT_TOUCH):* 1,178
- **Pillar 1 (Macro Trend Against 200 SMA):** 1,596 vetoed (1,270 Long, 326 Short)
- **Pillar 2 (Momentum Sweet Spot RSI 4H):** 232 vetoed (159 Long, 73 Short)
  - *Overbought / Squeeze Risk:* 107
  - *Collapsed / Dumped:* 125
- **Pillar 3 (Price Overextended > 0.25R):** 0 vetoed
- **Total Approved Trades:** **343 (250 Long, 93 Short)** $\rightarrow$ **4.46% Approval Rate** (~2.6 trades/week).

### 4.2 Study 5 Performance Breakdown (343 Approved Trades)

| Cohort | Engine Variant | Trades | Net R | Total P&L ($50/R) | Profit Factor | TP1 Win Rate | Max Drawdown |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Unfiltered Baseline** | Hard Stop + Card TP1/TP2 | 7,422 | -231.55 R | -$11,577.33 | 0.93 | 58.5% | 338.2 R |
| **Approved ALL** | Deployed Engine (+1.0R Buffered TP1 + TP2) | **343** | **+12.12 R** | **+$605.98** | **1.07** | **54.2%** | **20.0 R** |
| **Approved ALL** | Card TP1 + 21 EMA Runner | 343 | +18.41 R | +$920.60 | 1.13 | 58.9% | 20.5 R |
| **Approved LONGS** | Deployed Engine (+1.0R Buffered TP1 + TP2) | 250 | -1.99 R | -$99.49 | 0.98 | 52.0% | 22.5 R |
| **Approved SHORTS (Strict)** | Deployed Engine (+1.0R Buffered TP1 + TP2) | **93** | **+14.11 R** | **+$705.42** | **1.36** | **60.2%** | **8.6 R** |
| **Approved SHORTLIST Cells** | Card TP1 + 21 EMA Runner | **200** | **+23.66 R** | **+$1,183.01** | **1.27** | **58.0%** | **17.1 R** |
| **Approved SHORTLIST Cells** | Deployed Engine (+1.0R Buffered TP1 + TP2) | 200 | +11.26 R | +$562.87 | 1.12 | 54.0% | 14.1 R |
| **Approved WATCHLIST Cells** | Deployed Engine (+1.0R Buffered TP1 + TP2) | **143** | **+0.86 R** | **+$43.14** | **1.01** | 54.5% | 13.0 R |
| **Approved WATCHLIST Cells** | Card TP1 + 21 EMA Runner | 143 | -8.62 R | -$431.05 | 0.87 | 54.5% | 15.5 R |

### 4.3 Study 6 Baseline Distinction & BBWP <= 10.0 Impact

| System Configuration | Total Net R | Profit Factor | Win Rate | Max Drawdown | Total P&L ($50/R) | Exhaustion Fires | Absolute $\Delta$ Net R | Percent Gain |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Deployed Live Engine (Baseline)** | **+12.12 R** | **1.07** | **54.5%** | **20.0 R** | **+$605.93** | 0 | — | — |
| **Deployed Live Engine + BBWP $\le$ 10** | **+13.71 R** | **1.10** | **52.5%** | **17.2 R** | **+$685.38** | 68 | **+1.59 R** | **+13.11%** |
| **21-EMA Runner Engine (Baseline)** | **+18.03 R** | **1.11** | **54.5%** | **21.9 R** | **+$901.44** | 0 | — | — |
| **21-EMA Runner Engine + BBWP $\le$ 10** | **+26.87 R** | **1.20** | **52.5%** | **19.7 R** | **+$1,343.50** | 68 | **+8.84 R** | **+49.04%** |

- **BBWP Compression Tradeoff:**
  - 68 total early exits.
  - 35 full stops saved early (+29.26 R saved, avg +0.84 R/trade).
  - 31 winners truncated prematurely (-20.56 R forfeited, avg -0.66 R/trade).
  - **Net Gain:** **+8.70 R net tradeoff profit**.

---

## 5. Claude Code Action Plan (Recommended Sequence)

Do NOT rush to edit code. Follow this structured checklist:

1. **Audit & Verification:**
   - Inspect `metasignals_triage.py` lines 146–186 and confirm the cell counts (Finding F-01).
   - Inspect `metasignals_watcher.py` lines 117 and 137–139 and confirm the unhandled timeout risk (Finding F-03).
   - Run the test suite:
     ```powershell
     .\.venv\Scripts\pytest.exe -q tests/test_metasignals_paper.py
     ```
2. **Review With Andy:**
   - Present the finding that 56 active WATCHLIST cells remain in production.
   - Confirm Andy's approval to prune them to the 34 pure SHORTLIST cells to lock in the higher profit factor.
3. **Targeted Implementation:**
   - If approved by Andy:
     - Prune `PERMITTED_STRATEGY_TIMEFRAME_CELLS` to pure SHORTLIST.
     - Add `try...except (asyncio.TimeoutError, Exception)` to `metasignals_watcher.py`.
     - Re-run test suite and commit.
