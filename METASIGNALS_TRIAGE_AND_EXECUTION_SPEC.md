# METASIGNALS_TRIAGE_AND_EXECUTION_SPEC.md
## MetaSignals Alert Watcher, Kabroda.com YES/NO Triage Gate & Operator Execution Spec

**Document Status:** Architecture & Implementation Specification for Claude Code (CC)  
**Authority:** Andy Directive (2026-10-12 16:13 CT)  
**Target Repository:** `KTBB_app_v2` (`kabroda.com`)  
**Parent Methodology:** `Kabroda AI Brain` & `Kabroda Meta Signals AI`  
**Standing Rule:** **DOCUMENTATION ONLY IN THIS PASS.** No live site code is modified until Andy issues the Phase 2 build directive.

---

## 1. Executive Summary & The Core Concept

Andy is designing an auxiliary alert-evaluation and execution system to ingest MetaSignals trade alerts, run them through an automated **Kabroda.com Triage Gate (YES/NO Decision Layer)**, and allow Andy to review and take the trade with one click on a dedicated, isolated sub-account.

### The Problem It Solves:
* **The Manual Friction Problem**: MetaSignals alerts require manual geometry recomputation, checking 200 SMAs, funding checks, and position sizing.
* **The Risk Gap**: MetaSignals uses soft candle-close stops that leave leveraged perpetuals vulnerable to 3 AM liquidation wicks.
* **The Solution**: `kabroda.com` automatically ingests the alert, evaluates whether the market conditions justify the trade (**YES or NO**), pre-calculates the exact risk geometry with an orderbook hard stop, and surfaces a clean card for Andy to take the trade.

---

## 2. The Three-Stage Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│ STAGE 1: INGESTION / WATCHER                                           │
│ - Alert received via Webhook (POST /api/alerts/metasignals) or         │
│   parsed payload entered via Admin UI card.                            │
│ - Inputs: Symbol, Side, Strategy, Timeframe, Entry, Soft SL, Targets.  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ STAGE 2: KABRODA.COM TRIAGE GATE (YES / NO DECISION)                   │
│ Evaluates deterministic conditions against live Bitunix market data:   │
│ 1. Macro Regime Gate:  Daily Close > 200 SMA (Bullish for Long)        │
│ 2. Strategy Tier:      Matches validated shortlist (e.g. 2.0.1 8H)     │
│ 3. Entry Viability:    Price <= Alert Entry + 0.25R (not run away)     │
│ 4. Funding Check:      Bitunix 8H Funding Rate < +0.05% (not crowded)  │
│                                                                        │
│                ┌──────────────────┴──────────────────┐                 │
│                ▼                                     ▼                 │
│         [ VERDICT: YES ]                      [ VERDICT: NO ]          │
│         All gates pass.                       Vetoed with reason:      │
│         Pre-computes exact geometry:          - VETO_MACRO_BELOW_200SMA│
│         • Risk: $50 fixed dollar budget       - VETO_PRICE_EXTENDED    │
│         • Size: Exact contract qty            - VETO_FUNDING_CROWDED   │
│         • Hard Stop: SL - 0.25x ATR14         Logged in triage history │
│         • TP1: 50% @ +1.0R (auto-BE ratchet)  Zero capital at risk.    │
│         • TP2: 50% runner (8H 21 EMA trail)                            │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ STAGE 3: OPERATOR DECISION & SUB-ACCOUNT EXECUTION                     │
│ - kabroda.com displays active [YES - READY] card on dedicated tab.     │
│ - Andy reviews the pre-computed setup and clicks:                      │
│   [ ✅ TAKE TRADE ON SUB-ACCOUNT ] or [ ❌ PASS ]                      │
│ - On "Take": Reused executor places Entry + Hard Stop + TP limits      │
│   exclusively on the DEDICATED BITUNIX SUB-ACCOUNT.                    │
│ - Background 30s watcher ratchets stop to BE on TP1 fill.              │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Four Non-Negotiable Protection Rules for Claude Code

When this system is built, CC must strictly adhere to these four architectural boundaries:

### Rule 1: Absolute Capital & API Key Isolation (Sub-Account Mandatory)
* MetaSignals orders must **never** share API keys, margin, or balance with Bitcoin Traveler or Alt Matrix.
* The executor connects exclusively using environment variables:
  * `METASIGNALS_BITUNIX_API_KEY`
  * `METASIGNALS_BITUNIX_API_SECRET`
* Bound to a dedicated sub-account (e.g., $500–$1,000 balance). It is physically impossible for a MetaSignals trade to pull margin from Account 1.

### Rule 2: Zero Modification of Core Systems
* **Strictly Forbidden:** Zero code edits to:
  * `gate_traveler.py`
  * `traveler_plan_engine.py`
  * `battlebox_pipeline.py`
  * `alt_matrix_engine.py` / `alt_matrix_market.py`
  * Existing `ExecutorAccount` sizing presets.
* All MetaSignals logic lives in dedicated, isolated modules:
  * `app/models/metasignals_models.py`
  * `app/services/metasignals_triage.py`
  * `app/services/metasignals_executor.py`
  * `app/routers/metasignals_router.py`

### Rule 3: Hard Stop-Market Orderbook Translation
* Never submit a soft candle-close stop to the exchange.
* When placing orders, the hard stop price is calculated as:
  $$\text{Hard Stop Price} = \text{Alert Soft SL} - (0.25 \times \text{ATR}_{14})$$
  *(For shorts: $\text{Alert Soft SL} + 0.25 \times \text{ATR}_{14}$)*
* Position sizing is strictly locked to Andy's fixed risk unit ($50):
  $$\text{Position Size} = \frac{\$50.00}{|\text{Entry Price} - \text{Hard Stop Price}|}$$

### Rule 4: Reusing Traveler's Battle-Tested Watcher (`executor_live_engine.py`)
* Do not write new exchange socket or polling infrastructure.
* Reuse Traveler's 30-second polling pattern:
  * Places Entry + Hard Stop-Market + TP Limits atomically.
  * When TP1 fills $\rightarrow$ auto-amend exchange Stop-Market order to Breakeven (`entry_price`).
  * When Stop hits $\rightarrow$ auto-cancel all open TP limit orders.

---

## 4. Database Schema Specification

CC will add two new isolated tables inside `init_db()` in `database.py` (wrapped in raw SQL `try/except` per site convention):

### Table 1: `metasignals_triage_plans`
```sql
CREATE TABLE IF NOT EXISTS metasignals_triage_plans (
    id SERIAL PRIMARY KEY,
    plan_id VARCHAR(64) UNIQUE NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,
    strategy VARCHAR(32) NOT NULL,
    timeframe VARCHAR(16) NOT NULL,
    alert_price NUMERIC(16, 4) NOT NULL,
    soft_stop NUMERIC(16, 4) NOT NULL,
    tp1_price NUMERIC(16, 4) NOT NULL,
    tp2_price NUMERIC(16, 4) NOT NULL,
    triage_verdict VARCHAR(16) NOT NULL,       -- 'YES', 'NO', 'EXPIRED'
    triage_reason VARCHAR(255),                 -- 'APPROVED' or named veto cause
    macro_200sma NUMERIC(16, 4),
    current_price NUMERIC(16, 4),
    hard_stop NUMERIC(16, 4),
    risk_usd NUMERIC(10, 2) DEFAULT 50.00,
    position_size NUMERIC(16, 4),
    operator_action VARCHAR(16) DEFAULT 'PENDING', -- 'PENDING', 'TAKEN', 'PASSED'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### Table 2: `metasignals_orders`
Tracks the Bitunix order lifecycle (Entry, Hard Stop, TP1, TP2) on the dedicated sub-account, identical in structure to `traveler_orders`.

---

## 5. UI Dashboard Concept on kabroda.com

A clean, segregated tab on `kabroda.com`: **"MetaSignals Triage & Watcher"**

* **Status Banner**: Connected to Sub-Account (Balance: `$500.00` | Risk Unit: `$50.00`).
* **Active Triage Card**:
  * Shows latest incoming alert (e.g. `ETHUSDT LONG @ $2,534.63 | 8H 2.0.1`).
  * **Large Verdict Badge**:
    * 🟢 **YES — APPROVED** (Macro Bullish > 200 SMA | Low Funding | In Range)
    * 🔴 **NO — VETOED** (VETO: Price below Daily 200 SMA)
  * **If YES, displays pre-computed geometry**:
    * Entry: `$2,534.63 – $2,545.00`
    * Hard Stop: `$2,388.65` ($50 max loss)
    * TP1: `$2,662.29` (Take 50% + Move Stop to BE)
    * TP2: `$2,793.82` (Trail 8H 21 EMA)
  * **Interactive Action Buttons**:
    * `[ 🚀 TAKE TRADE ]` $\rightarrow$ calls `POST /api/metasignals/execute/{id}`, submits bracket to Bitunix sub-account.
    * `[ ✕ PASS ]` $\rightarrow$ marks plan as `PASSED`, closes card.

---

## 6. Implementation Checklist for CC (Phase 2 When Ready)

When Andy authorizes implementation:
1. [ ] Add `metasignals_triage_plans` and `metasignals_orders` to `init_db()`.
2. [ ] Add `app/services/metasignals_triage.py` implementing the 4 triage checks.
3. [ ] Add `app/services/metasignals_executor.py` implementing bracket placement and 30s watcher for sub-account.
4. [ ] Add `POST /api/metasignals/ingest` and `POST /api/metasignals/take` routes.
5. [ ] Render the UI card on `kabroda.com/admin/metasignals`.
6. [ ] Full test suite verifying that no Traveler or Alt Matrix code paths are touched.
