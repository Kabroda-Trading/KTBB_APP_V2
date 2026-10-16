# CC_WORK_ORDER_METASIGNALS_GOLDEN_SETUP.md — Production Alignment & Golden Profile

**Target:** `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2` (`kabroda.com`)  
**Origin:** `Kabroda Meta Signals AI` (DeepSeek / Antigravity session 2026-10-16)  
**Status:** IMPLEMENTED & TEST VERIFIED (96/96 unit tests passing)  
**Reference Artifacts:** `Kabroda Meta Signals AI/studies/kabroda_gate_backtest/REPORT.md` (Commit `210d280`)

---

## 1. Executive Summary & Empirical Evidence

A comprehensive historical backtest (Study 5) was executed across **all 9,078 historical MetaSignals alerts** spanning 2.5 years (2023-01 to 2026-04) replaying the full 5-Pillar Triage Gate, Bitunix 4H/Daily warmup candles (zero lookahead), and realized order book execution geometry across **both Long and Short sides**.

### The Crucial Findings:
1. **Raw MetaSignals Alerts Are Toxic:** Unfiltered MTF/HTF alerts lost **-231.55 R (-$11,577 on $50/R)** with an catastrophic **338.2 R drawdown**.
2. **The 5-Pillar Gate Rescued Edge:** Filtered 95.5% of noise, approving 343 trades (250 Long, 93 Short) and flipping negative baseline to positive net expectancy.
3. **The 8H Timeframe Was Pure Drag:** 
   - 4H made **+15.70 R** (PF 1.36); 6H made **+22.09 R** (PF 2.12).
   - 8H alerts bled **-11.08 R** and generated **19.5 R of drawdown alone**. Dropping 8H immediately slashes drawdown and elevates net edge.
4. **Shorts Are the Highest-PF Side:** 
   - 93 approved Short trades delivered a **60.2% win rate**, **+14.11 R**, and **PF 1.36** with only **8.6 R max drawdown**. Short support is vital for altcoin drawdowns.
5. **Position Concurrency Kills Cluster Drawdown:**
   - Without concurrency caps, up to 20 simultaneous altcoin alerts triggered during mass market breaks, stacking correlated drawdown.
   - Imposing a **max concurrency cap of 5 open positions** elevated Profit Factor from **1.07 to 1.86**, while strictly bounding peak desk risk to $250.

---

## 2. Changes Implemented in `KTBB_app_v2`

### A. `metasignals_triage.py`
1. **Sweet-Spot Timeframe Whitelist:**
   - Updated `WHITELISTED_TIMEFRAMES = ("2H", "3H", "4H", "6H", "1D")`.
   - Excluded `8H`, `1H`, and `12H` to prevent toxic timeframe drift.
2. **Concurrency Veto (`VETO_CONCURRENT_CAP_REACHED`):**
   - Added `db: Optional[Any] = None` parameter to `evaluate_alert()`.
   - Before evaluating expensive external market data, queries `MetaSignalsPaperOrder` for states `("OPEN", "HALF_CLOSED_BE", "PENDING_LIMIT")`.
   - If `open_count >= METASIGNALS_MAX_CONCURRENT_POSITIONS` (default: 5), immediately vetoes with `reason="VETO_CONCURRENT_CAP_REACHED"`.
3. **Card TP1 Priority:**
   - Prioritizes printed `card_tp1` from the alert payload if valid (`tp1 > entry` for BUY, `tp1 < entry` for SELL), falling back to buffered `+1.0R` if zero or absent.

### B. `main.py`
- Line 1177: Passed `db=db` into `await metasignals_triage.evaluate_alert(alert, db=db)` so concurrency limits are enforced in live webhook ingestion.

### C. `tests/test_metasignals_paper.py`
- Line 1330: Updated legacy test payload fixture from `8H` / `2.0.1` to `4H` / `2.0 G2` to conform to the sweet-spot whitelist.
- **Verification:** Full suite executed — **96 passed, 0 failed** in 59.37s.

---

## 3. Recommended Production Environment Variables

Add to `.env` or system environment on `kabroda.com`:
```bash
# Maximum concurrent open MetaSignals paper positions (default: 5)
METASIGNALS_MAX_CONCURRENT_POSITIONS=5
```

---

## 4. Cross-Agent Alignment & Sizing Recommendation for Andy

- **Risk per Trade:** $50 fixed risk per trade (or 2.5% of dedicated sub-account).
- **Concurrency Cap:** 5 positions ($250 max active capital at risk).
- **Compounding Trajectory:**
  - With a 59.3% win rate and 1.86 Profit Factor, compounding at 2.5–3.0% equity per trade historically doubled account equity every 9–11 months with sub-20% equity drawdown.
- **Segregation:** Remind Andy that all paper order execution continues to run exclusively on `metasignals_paper_*` tables and never touches Bitcoin Traveler or Alt Matrix balances.
