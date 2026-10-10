# ALT MATRIX DEPLOYMENT & OPERATIONS RUNBOOK
**System:** High-Timeframe (4H / Daily) Swing Momentum Matrix (`SOLUSDT`, `ETHUSDT`)  
**Target Exchange:** Bitunix Perpetual Futures (`fapi.bitunix.com`)  
**Repository:** `KTBB_app_v2` (Site Engine) & `Kabroda AI Brain` (Research & Specs)  
**Date:** 2026-10-10  
**Status:** Ready for DRY_RUN / LIVE Enablement  

---

## 1. Executive Summary & Rollout Ruling

### The Question: Do We Need a Phased Live Rollout ("Phase A" / "Phase B")?
**Ruling: NO.** A prolonged, multi-phase live staging ritual (Phase A pilot sizing with real money $\to$ Phase B full sizing) is **not required**.

### Why DRY_RUN is Sufficient to Transition Straight to LIVE:
1. **Identical Execution Venue & Credentials:** The Alt Matrix operates on the exact same Bitunix account using the exact same API credentials and signing protocols already battle-tested by the Bitcoin Traveler system.
2. **Identical Order Mechanics:** The execution engine (`alt_matrix_executor.py`) copies the proven sequential order placement (`place_entry_market()` $\to$ `place_initial_stop()` $\to$ `amend_to_breakeven()`) directly from `executor_live_e1_engine.py`. Traveler has executed dozens of orders through this exact flow with zero execution defects.
3. **Verified Funding Endpoint:** The single new API surface (Bitunix funding rate veto) was empirically verified live against Bitunix production (`GET https://fapi.bitunix.com/api/v1/futures/market/funding_rate?symbol={symbol}`) as a public, unauthenticated endpoint returning a valid object payload.
4. **Clean Operational Path:** 
   - **Step 1 (DRY_RUN):** Toggle `AltMatrixConfig` on the account in `DRY_RUN` mode. The 4H UTC boundary clock and 30-60s watch loops run live market fetches, calculate signals, check concurrency against BTC, log transitions, and send notifications with **$0 capital risk**.
   - **Step 2 (LIVE):** Once DRY_RUN confirms the loops and radar display are healthy through a cycle, toggle the account to `LIVE`.

---

## 2. System Architecture & Constitutional Isolation

### The Iron Wall (Sovereign Priority #1)
The Alt Matrix runs parallel to Bitcoin Traveler. It is architected to ensure that **no Alt Matrix trade can ever block, delay, or starve Bitcoin Traveler**:
* **Zero Code Import:** No Alt Matrix module imports `gate_traveler.py`, `traveler_plan_engine.py`, or `executor_live_e1_engine.py`.
* **Zero Schema Mutation:** Alt Matrix uses independent tables (`AltMatrixPlan`, `AltMatrixOrder`, `AltMatrixTransition`, `AltMatrixConfig`). No alterations to existing Traveler or Executor tables.
* **Exchange-First Portfolio Checks:** Concurrency checks in `alt_matrix_portfolio.py` query the Bitunix account's real open positions and margin directly.

### Risk & Margin Rails
| Rail | Limit | Enforcement Point | Action on Breach |
|---|---|---|---|
| **Free Margin Floor** | $\ge 30\%$ Account Equity | `alt_matrix_portfolio.py` | Alt Matrix stands down (`CONCURRENCY_SKIPPED`). BTC always protected. |
| **Max Portfolio Risk** | $\le 20\%$ Account Equity | `alt_matrix_portfolio.py` | Alt Matrix stands down (`CONCURRENCY_SKIPPED`). |
| **Max Open Positions** | $\le 3$ Total Concurrent | `alt_matrix_portfolio.py` | Alt Matrix stands down (`CONCURRENCY_SKIPPED`). |
| **Unprotected Position Guard** | Zero unhedged positions | `alt_matrix_portfolio.py` | Any open position lacking a stop forces immediate Alt Matrix stand-down. |

---

## 3. Operational Rules & State Machine

### D1: Setup & Macro Gating
* **Universe:** Strictly `SOLUSDT` and `ETHUSDT` (ADA & LINK benched per empirical portfolio study).
* **Clock Boundaries:** Evaluated at `00:00`, `04:00`, `08:00`, `12:00`, `16:00`, `20:00 UTC` (staggered at `HH:00:05` UTC).
* **Macro Filter:** Daily Close $> 200\text{ SMA}$ evaluated on the prior fully-closed Daily candle at `00:00 UTC`.
* **4H Momentum Cross:** 4H $21\text{ EMA} > 55\text{ EMA}$ (Silver Cross).
* **Funding Rate Veto:** Current Bitunix 8H funding rate must be $< +0.05\%$. If $\ge +0.05\%$, setup is vetoed (`SKIPPED_FUNDING`).

### D2: Entry & Protective Stop
* **Direction:** Long-only.
* **Entry Execution:** Market entry placed at the open of the bar immediately confirming the D1 cross.
* **Stop Placement:** Initial protective Stop-Market order placed at $\text{Entry} - (1.5 \times \text{ATR}_{14})$.
* **ATR Formula:** Standard Wilder's 14-period True Range ATR.

### D3: Management & Trailing Exits
* **Breakeven Ratchet:** When Maximum Favorable Excursion (MFE) reaches $\ge +2.0\text{R}$, the live engine amends the exchange Stop-Market order to $\text{Entry} + 0.1\text{R}$ (locking in fees and small profit).
* **Trailing Exit:** Once in profit, position exits via market order on the first confirmed 4H bar closing below the 4H $21\text{ EMA}$.
* **Protective Stop Exit:** Full market exit if price hits the exchange stop-loss order.

---

## 4. Admin Configuration Guide

### Enabling an Account via Admin UI
1. Navigate to `https://kabroda.com/executor/admin` (or local development server).
2. Locate the account card for **Andy_Bitunix** (or target account).
3. Under **Step 6: Alt Matrix (SOL/ETH 4H swing)**:
   - Check **SOL Enabled** to trade Solana.
   - Check **ETH Enabled** to trade Ethereum.
   - Save changes via the per-account config form (`POST /api/executor/accounts/{id}/alt-matrix-config`).

### Configuration Parameters (`AltMatrixConfig`)
* `account_id` (Integer): The target `ExecutorAccount` ID.
* `sol_enabled` (Boolean): Enables SOL trading (default `True`).
* `eth_enabled` (Boolean): Enables ETH trading (default `True`).
* `paired_traveler_account_id` (Integer, Optional): ID of the paired BTC account (defaults to all LIVE `GATE_TRAVELER` accounts).
* `btc_margin_reserve_usd` (Float, Optional): Custom dollar margin floor if specified.
* `symbol_priority` (String): Priority order for simultaneous signals (default `"SOL,ETH"`).
* `catchup_window_seconds` (Integer): Allowed catch-up window after server restart (default `900`s = 15m).

---

## 5. Deployment & Rollout Sequence

### Step 1: Deploy & Verify DRY_RUN
1. Deploy `KTBB_app_v2` containing commits `5cba8a6` through `025d8c2`.
2. Confirm server boot: Background loops `run_alt_matrix_signal_loop` and `run_alt_matrix_watch_loop` start cleanly in `lifespan()`.
3. Create `AltMatrixConfig` row for Account 1 with target mode set to `DRY_RUN`.
4. Inspect Public Radar (`/api/radar/alt-matrix-snapshot`) and Admin Status (`/api/admin/alt-matrix-status`):
   - Confirm symbols `SOLUSDT` and `ETHUSDT` appear with `plan: null` or active market state.
   - Confirm no errors logged in `system_alert_log`.

### Step 2: Transition to LIVE
1. Once DRY_RUN executes through a clean evaluation cycle:
   - Update Account 1 mode to `LIVE`.
   - Ensure the Sizing Policy Wizard has approved base risk (e.g. banded risk starting at $70/trade or standard account base).
2. The engine will automatically place real orders on the next confirmed 4H Silver Cross meeting all D1 criteria.

### Optional Smoke Test (60 Seconds)
If an immediate manual confirmation of contract formatting is desired prior to the next natural 4H candle:
1. Place a minimum size test order on Bitunix: `0.1 SOL` ($\approx \$11$).
2. Confirm the exchange accepts the order, prints the stop, and cancels cleanly.
3. This is an optional 1-minute confidence check, not a blocking multi-day phase.

---

## 6. Emergency Procedures & Kill Switches

### Global Kill Switch
* Setting `live_orders_enabled = False` in site admin immediately suspends ALL automated order placement site-wide across both BTC Traveler and Alt Matrix.

### Per-Account Alt Matrix Kill Switch
* Unchecking both `SOL Enabled` and `ETH Enabled` in the Admin UI immediately halts new Alt Matrix plan evaluations for that account.
* Open positions will continue to be monitored by D3 until closed, or can be closed manually via Bitunix exchange UI.

### Manual Position Intervention
* If a position is closed or modified manually on Bitunix, the next 30-60s tick of `run_alt_matrix_watch_loop()` detects the missing exchange position via `get_position()` and cleanly transitions the local database record to `CLOSED_MANUAL`.
