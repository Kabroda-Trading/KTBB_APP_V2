# METASIGNALS_TRIAGE_AND_EXECUTION_SPEC.md
## MetaSignals Realistic Paper Trading Desk, Raspberry Pi Ingestion & Kabroda.com Triage Gate

**Document Status:** Complete Architecture & Implementation Work Order for Claude Code (CC)  
**Authority:** Andy Directive (2026-10-13 09:34 CT)  
**Target Repository:** `KTBB_app_v2` (`kabroda.com`)  
**Cross-Agent Contract:** `Kabroda AI Brain/CC_INTERFACE.md` Section 7  
**Guiding Mandate:** **100% Isolated Subsystem.** Zero touch on Bitcoin Traveler or Alt Matrix. Built as a **Realistic Paper Trading Desk first** with honest fee/fill accounting, live Bitunix price tracking, and a dormant switch for future live sub-account execution.

---

## 1. Executive Summary & Vision

Andy is integrating an automated alert-evaluation and paper trading system for **MetaSignals** alerts.
The system connects four distinct parts into a secure, automated pipeline:

1. **The Raspberry Pi ("The Air Gap Listener")**:
   - Runs a lightweight background Python daemon on Andy's local network.
   - Listens to Discord (`Mafioso_Majors`), parses incoming alerts (symbol, timeframe, entry, SL, targets), and fires an authenticated HTTPS POST request to `kabroda.com`.
   - **Why a Pi?** It completely protects `kabroda.com` and Render from Discord IP blocks or scraper flags.
2. **Kabroda.com Triage Gate (95% Deterministic YES/NO)**:
   - When the alert hits `kabroda.com`, it immediately evaluates the setup against live Bitunix market data across 5 quantitative pillars (Macro 200 SMA, 4H RSI energy, MAE extension limit, Funding rate, and Strategy whitelist).
   - If ANY gate fails $\rightarrow$ **VETO (NO)** with a named reason logged (e.g., `VETO_MACRO_BELOW_200SMA`). Zero capital at risk.
   - If ALL gates pass $\rightarrow$ **APPROVED (YES)**.
3. **Realistic Paper Trading Desk ($500 Starting Bankroll)**:
   - When approved, `kabroda.com` automatically opens a paper trade on an isolated paper ledger.
   - Simulates real exchange truth:
     - Fills at live Bitunix price (or limit if extended).
     - Orders a **Hard Orderbook Stop** at $\text{Alert SL} - (0.25 \times \text{ATR}_{14})$ (buffered against liquidation wicks).
     - Sizes strictly for **$50.00 fixed dollar risk**.
     - Simulates **TP1 at +1.0R (50% off)** with **automatic Stop-to-Breakeven ratchet**.
     - Simulates **TP2 runner** trailing the 8H 21 EMA (or fixed +2.0R).
     - Deducts realistic exchange trading fees (taker fee 0.06% on entry and market exits, maker fee 0.02% on limit TP fills).
     - 30-second background watcher updates unrealized P&L against live Bitunix mark prices so Andy can watch the account grow or lose money in real time.
4. **MetaSignals AI Workspace Alignment**:
   - The paper ledger and triage verdicts are surfaced via JSON API and a clean web UI so Andy can cross-examine every alert and compare live behavior against expected backtest stats.
5. **Dormant Live Sub-Account Ready**:
   - When Andy is ready down the road, flipping `METASIGNALS_EXECUTION_MODE = "LIVE"` and providing `METASIGNALS_BITUNIX_API_KEY`/`SECRET` routes these exact order brackets to the dedicated Bitunix sub-account without rewriting a line of strategy code.

---

## 2. The Anti-Breakage "Iron Wall" (Binding Rules for Claude Code)

Andy's non-negotiable instruction: **Do not entangle this with existing systems. If MetaSignals is ever edited or shut down, nothing else must break.**

### A. Strictly FORBIDDEN to Edit (The Untouchables):
CC must NOT modify, import from, or touch these files:
* ❌ `gate_traveler.py`
* ❌ `traveler_plan_engine.py` / `traveler_plan_notify.py` / `traveler_radar.py`
* ❌ `battlebox_pipeline.py`
* ❌ `executor_live_e1_engine.py` / `executor_plan_builder.py` / `executor_sizing.py`
* ❌ `alt_matrix_*.py` (All 9 Alt Matrix modules: engine, clock, executor, market, notify, portfolio, radar, signals, management)
* ❌ Existing `ExecutorAccount` rows, balances, or live API credentials.

### B. What CC Creates (Standalone MetaSignals Files):
CC creates completely isolated modules in the repository root (following the exact clean architectural pattern set by Alt Matrix):
* 🟢 `metasignals_triage.py`: The 5-Pillar quantitative evaluation gate.
* 🟢 `metasignals_paper_engine.py`: The paper account manager, order fill simulator, fee deduction, and BE ratcheting.
* 🟢 `metasignals_watcher.py`: Independent 30-second background polling loop for monitoring open paper positions against Bitunix mark prices.
* 🟢 `metasignals_radar.py`: Read-only data access layer for public/admin UI endpoints.
* 🟢 `templates/metasignals_desk.html`: Dedicated web dashboard for monitoring triage queue, paper account equity, and trade history.
* 🟢 `scripts/raspberry_pi_alert_forwarder.py`: The Python script for Andy's Raspberry Pi.
* 🟢 `tests/test_metasignals_paper.py`: Unit and regression test suite.

### C. The ONLY Existing Files CC Touches:
1. `database.py`: Add the 4 new SQLAlchemy models (`MetaSignalsPlan`, `MetaSignalsPaperOrder`, `MetaSignalsPaperAccount`, `MetaSignalsPaperLedger`) and `CREATE TABLE IF NOT EXISTS` blocks inside `init_db()` wrapped in try/except per site convention.
2. `main.py`: Add 3 isolated route handlers and mount the background watcher in the FastAPI lifespan.
3. `templates/base.html`: Add a link in the navigation menu ("MetaSignals Desk").

---

## 3. The 5-Pillar Triage Gate (`metasignals_triage.py`)

When an alert payload is received, `metasignals_triage.evaluate_alert(db, alert_payload)` executes these checks in under 100ms using live Bitunix data:

| Pillar | Gate Condition | Veto Reason If Failed | Action If Passed |
| :--- | :--- | :--- | :--- |
| **1. Macro Trend** | • BTC Daily Close > Daily 200 SMA<br>• Asset Daily Close > Daily 200 SMA (or Daily 55 EMA) | `VETO_MACRO_BELOW_200SMA` | Ensures long trades are not taken into macro bear flushes. |
| **2. Energy / Exhaustion** | • 4H RSI(14) between **40.0 and 62.0** at alert time | `VETO_MOMENTUM_OVERBOUGHT` (if >62)<br>`VETO_MOMENTUM_COLLAPSED` (if <40) | Ensures trade enters from a healthy base, not at the blow-off peak of a squeeze. |
| **3. Extension ("Don't Chase")** | • Extension $\le +0.25\text{R}$ from printed alert price | `VETO_PRICE_OVER_EXTENDED` | If extension $\le +0.10\text{R}$: Market entry.<br>If $+0.10\text{R} < \text{Ext} \le +0.25\text{R}$: Convert to resting limit order at Alert Price. |
| **4. Funding Crowding** | • Live Bitunix 8H Funding Rate $< +0.04\%$ | `VETO_FUNDING_OVERCROWDED` | Filters out crowded retail longs prone to liquidation wick hunts. |
| **5. Strategy Whitelist** | • Symbol in `["BTCUSDT", "ETHUSDT", "SOLUSDT"]`<br>• Timeframe in `["6H", "8H"]`<br>• Strategy in whitelisted set (e.g. `2.0.1`, `2.0 G2`) | `VETO_PAIR_OR_TIMEFRAME_NOT_WHITELISTED` | Rejects noisy 15m/1h scalps and low-cap alts. |

**Output**: Returns `(verdict: "YES" | "NO", reason: str, geometry: dict)`:
* If "NO": Stored in DB with status `REJECTED`, zero action taken.
* If "YES": Pre-computes exact geometry:
  - $\text{Hard Stop Price} = \text{Alert SL} - (0.25 \times \text{ATR}_{14})$
  - $\text{Position Size} = \frac{\$50.00}{|\text{Entry Price} - \text{Hard Stop Price}|}$
  - $\text{TP1} = \text{Entry Price} + 1.0 \times |\text{Entry Price} - \text{Hard Stop Price}|$ (50% size)
  - $\text{TP2} = \text{Alert TP2}$ (or trail 8H 21 EMA) (50% size)

---

## 4. The Realistic Paper Trading Engine (`metasignals_paper_engine.py`)

### A. Honest Exchange Simulation Rules:
1. **Initial Bankroll**: Seeded with `$500.00` (stored in `MetaSignalsPaperAccount`).
2. **Risk Budget**: Strictly `$50.00` fixed risk per trade (10% of $500 bankroll).
3. **Execution & Fills**:
   - Entry fills at live Bitunix ask price (or limit if resting).
   - Deducts **Taker Fee (0.06% / 0.0006)** on entry notional.
4. **Lifecycle Watcher (Every 30 Seconds)**:
   - Polls Bitunix ticker / 5m candle for the symbol.
   - **TP1 Hit** ($\text{High} \ge \text{TP1}$ for Long):
     - Fills 50% position size at TP1 limit price.
     - Deducts **Maker Fee (0.02% / 0.0002)** on closed notional.
     - Realizes $\approx +\$25.00$ gross profit.
     - **Automatically ratchets Hard Stop on remaining 50% size to Breakeven (`entry_price`)**.
     - Updates position state to `HALF_CLOSED_BE_PROTECTED`.
   - **Hard Stop Hit** ($\text{Low} \le \text{Hard Stop}$ for Long):
     - Fills remaining size at Hard Stop price.
     - Deducts Taker Fee (0.06%).
     - Realizes $-\$50.00$ loss (or $\approx \$0.00$ if already at Breakeven).
     - Closes position completely, updates cash balance.
   - **TP2 Hit** ($\text{High} \ge \text{TP2}$ for Long):
     - Fills remaining 50% at TP2 limit price.
     - Deducts Maker Fee (0.02%).
     - Closes position completely, logs final realized P&L and net R.

### B. Paper Account Ledger:
Every balance change (fee deduction, TP1 fill, stop fill, final exit) writes a row to `metasignals_paper_ledger` with `balance_after`, `equity_after`, and `notes`. Andy can see the exact equity curve in real time.

---

## 5. Database Schema Specification (`database.py`)

Add to `database.py` models and `init_db()`:

```sql
-- 1. Paper Account & Configuration Singleton
CREATE TABLE IF NOT EXISTS metasignals_paper_account (
    id SERIAL PRIMARY KEY,
    name VARCHAR(32) DEFAULT 'DEFAULT_PAPER_DESK',
    execution_mode VARCHAR(24) DEFAULT 'PAPER', -- 'PAPER', 'SUBACCOUNT_DRY_RUN', 'SUBACCOUNT_LIVE'
    linked_account_id INT REFERENCES executor_accounts(id) DEFAULT NULL,
    starting_balance NUMERIC(16, 4) DEFAULT 500.00,
    cash_balance NUMERIC(16, 4) DEFAULT 500.00,
    equity NUMERIC(16, 4) DEFAULT 500.00,
    risk_unit_usd NUMERIC(10, 2) DEFAULT 50.00,
    total_trades INT DEFAULT 0,
    winning_trades INT DEFAULT 0,
    losing_trades INT DEFAULT 0,
    total_realized_pnl NUMERIC(16, 4) DEFAULT 0.00,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 2. Triage Plans (All incoming alerts + triage results)
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
    triage_verdict VARCHAR(16) NOT NULL,        -- 'YES', 'NO'
    triage_reason VARCHAR(255) NOT NULL,       -- 'APPROVED' or named veto cause
    macro_200sma NUMERIC(16, 4),
    rsi_4h NUMERIC(8, 2),
    funding_rate NUMERIC(10, 6),
    hard_stop NUMERIC(16, 4),
    risk_usd NUMERIC(10, 2) DEFAULT 50.00,
    position_size NUMERIC(16, 4),
    status VARCHAR(32) DEFAULT 'PENDING',      -- 'REJECTED', 'OPEN', 'HALF_CLOSED', 'CLOSED'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 3. Paper Orders & Active Positions
CREATE TABLE IF NOT EXISTS metasignals_paper_orders (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(64) UNIQUE NOT NULL,
    plan_id VARCHAR(64) REFERENCES metasignals_triage_plans(plan_id),
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,
    entry_price NUMERIC(16, 4) NOT NULL,
    current_stop NUMERIC(16, 4) NOT NULL,
    hard_stop_initial NUMERIC(16, 4) NOT NULL,
    tp1_price NUMERIC(16, 4) NOT NULL,
    tp2_price NUMERIC(16, 4) NOT NULL,
    initial_qty NUMERIC(16, 4) NOT NULL,
    remaining_qty NUMERIC(16, 4) NOT NULL,
    state VARCHAR(32) NOT NULL,                -- 'OPEN', 'HALF_CLOSED_BE', 'CLOSED_STOP', 'CLOSED_TP2'
    realized_pnl NUMERIC(16, 4) DEFAULT 0.00,
    fees_paid NUMERIC(16, 4) DEFAULT 0.00,
    opened_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    closed_at TIMESTAMP WITH TIME ZONE
);

-- 4. Balance & Audit Ledger
CREATE TABLE IF NOT EXISTS metasignals_paper_ledger (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    event_type VARCHAR(32) NOT NULL,           -- 'INITIAL_DEPOSIT', 'ENTRY_FEE', 'TP1_FILL', 'STOP_FILL', 'EXIT'
    amount NUMERIC(16, 4) NOT NULL,
    cash_balance_after NUMERIC(16, 4) NOT NULL,
    notes TEXT
);
```

---

## 6. The Ingestion API & Endpoints (`main.py`)

### A. Ingestion Endpoint for Raspberry Pi:
```http
POST /api/metasignals/alert
Header: X-Kabroda-Secret: <METASIGNALS_INGEST_SECRET>
Body:
{
  "raw_text": "SOL | USDT @ $110.0400 - 6H - 2.0 G2...",
  "symbol": "SOLUSDT",
  "side": "BUY",
  "entry_price": 110.04,
  "soft_stop": 105.40,
  "tp1": 114.19,
  "tp2": 120.56,
  "timeframe": "6H",
  "strategy": "2.0 G2",
  "timestamp": "2026-10-13T14:30:00Z"
}
```
* **Behavior**:
  1. Validates `X-Kabroda-Secret` against `.env`.
  2. Runs `metasignals_triage.evaluate_alert()`.
  3. If YES $\rightarrow$ invokes `metasignals_paper_engine.open_paper_trade()`.
  4. Returns JSON: `{ "status": "ok", "verdict": "YES|NO", "reason": "...", "plan_id": "..." }`.

### B. Snapshot Endpoint (for Dashboard & MetaSignals AI):
```http
GET /api/metasignals/snapshot
```
* Returns:
  - Account stats: balance, equity, win rate, total P&L.
  - Active positions: live mark price, unrealized P&L, stop price.
  - Recent triage log (last 20 alerts with YES/NO verdicts).

### C. Sub-Account & Execution Mode Switch Endpoint:
```http
POST /api/metasignals/config
Header: Cookie (Admin Session Auth)
Body:
{
  "execution_mode": "PAPER",           // "PAPER", "SUBACCOUNT_DRY_RUN", "SUBACCOUNT_LIVE"
  "linked_account_id": null           // null for virtual paper desk, or ExecutorAccount.id
}
```
* **Behavior**:
  - Updates `metasignals_paper_account` singleton.
  - If `execution_mode == 'PAPER'`: All trades execute in the virtual paper ledger ($500 balance), 0 exchange calls.
  - If `execution_mode == 'SUBACCOUNT_DRY_RUN'`: Trades are simulated against the selected `ExecutorAccount`'s real Bitunix balance, but no real orders are sent to exchange.
  - If `execution_mode == 'SUBACCOUNT_LIVE'`: Real atomic brackets (Entry + Hard Stop + TPs) are placed directly onto the Bitunix sub-account using its decrypted API credentials! Requires the linked account to have mode == `LIVE`.

### D. Dedicated Web Route:
```http
GET /admin/metasignals
```
* Renders `templates/metasignals_desk.html` with full visual dashboard:
  * **Top Selector**: A dropdown menu displaying:
    - `[ 📝 Standalone Paper Trading Desk ($500 Virtual Balance) ]` (Default)
    - `[ 🔌 Account #X: {label} (DRY_RUN Sim) ]`
    - `[ 🚀 Account #X: {label} (LIVE Trading) ]`
  * One-click save button to instantly switch modes or link/unlink sub-accounts.
  * Live Paper / Sub-Account Balance & Equity card.
  * Real-time triage log and open position cards.

---

## 7. The Raspberry Pi Python Script (`scripts/raspberry_pi_alert_forwarder.py`)

This standalone script runs on Andy's Raspberry Pi to monitor Discord and forward alerts to `kabroda.com`.

```python
"""
Raspberry Pi Alert Forwarder for MetaSignals -> Kabroda.com
Run 24/7 as a systemd service on Andy's Raspberry Pi.
"""
import os
import re
import json
import logging
import requests
import discord
from discord.ext import commands

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

KABRODA_URL = os.getenv("KABRODA_URL", "https://kabroda.com/api/metasignals/alert")
KABRODA_SECRET = os.getenv("KABRODA_INGEST_SECRET", "YOUR_SHARED_SECRET_KEY")
TARGET_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))  # Mafioso_Majors channel ID

# Regex parser for MetaSignals alerts
ALERT_PATTERN = re.compile(
    r"(?P<symbol>[A-Z0-9]+)\s*\|\s*USDT\s*@\s*\$(?P<entry>[\d\.]+)\s*-\s*(?P<timeframe>[0-9A-Za-z]+)\s*-\s*(?P<strategy>[0-9A-Za-z\.\s]+)",
    re.IGNORECASE
)
SL_PATTERN = re.compile(r"SL Close Below:\s*(?P<sl>[\d\.]+)", re.IGNORECASE)
T1_PATTERN = re.compile(r"Target 1:\s*(?P<tp1>[\d\.]+)", re.IGNORECASE)
T2_PATTERN = re.compile(r"Target 2:\s*(?P<tp2>[\d\.]+)", re.IGNORECASE)

class AlertClient(discord.Client):
    async def on_ready(self):
        logging.info(f"Connected to Discord as {self.user} (Listening to channel {TARGET_CHANNEL_ID})")

    async def on_message(self, message):
        if message.channel.id != TARGET_CHANNEL_ID and TARGET_CHANNEL_ID != 0:
            return

        text = message.content or ""
        if not text and message.embeds:
            # Check embeds if alert is embedded
            for embed in message.embeds:
                if embed.description:
                    text += " " + embed.description
                if embed.title:
                    text += " " + embed.title

        match = ALERT_PATTERN.search(text)
        if not match:
            return

        symbol_clean = f"{match.group('symbol').upper()}USDT"
        entry_price = float(match.group("entry"))
        timeframe = match.group("timeframe").strip()
        strategy = match.group("strategy").strip()

        sl_match = SL_PATTERN.search(text)
        tp1_match = T1_PATTERN.search(text)
        tp2_match = T2_PATTERN.search(text)

        if not sl_match:
            logging.warning("Alert matched header but missing SL pattern. Skipping.")
            return

        payload = {
            "raw_text": text,
            "symbol": symbol_clean,
            "side": "BUY",  # Default long; can check for short indicators
            "entry_price": entry_price,
            "soft_stop": float(sl_match.group("sl")),
            "tp1": float(tp1_match.group("tp1")) if tp1_match else round(entry_price * 1.04, 4),
            "tp2": float(tp2_match.group("tp2")) if tp2_match else round(entry_price * 1.08, 4),
            "timeframe": timeframe,
            "strategy": strategy,
            "timestamp": message.created_at.isoformat(),
        }

        logging.info(f"Captured MetaSignals Alert: {payload['symbol']} @ {payload['entry_price']}")

        # Forward to Kabroda.com
        try:
            resp = requests.post(
                KABRODA_URL,
                json=payload,
                headers={"X-Kabroda-Secret": KABRODA_SECRET, "Content-Type": "application/json"},
                timeout=10,
            )
            logging.info(f"Forwarded to Kabroda: Status {resp.status_code} | Response: {resp.text}")
        except Exception as e:
            logging.error(f"Failed to forward alert to Kabroda: {e}")

if __name__ == "__main__":
    token = os.getenv("DISCORD_USER_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logging.error("Missing DISCORD_USER_TOKEN or DISCORD_BOT_TOKEN environment variable.")
    else:
        client = AlertClient()
        client.run(token)
```

---

## 8. Turnkey Implementation Steps for Claude Code

When Andy prompts Claude Code in `KTBB_app_v2`:

1. **Step 1: Database Migration (`database.py`)**:
   - Add the 4 models (`MetaSignalsPaperAccount`, `MetaSignalsPlan`, `MetaSignalsPaperOrder`, `MetaSignalsPaperLedger`).
   - Add SQL `CREATE TABLE IF NOT EXISTS` blocks in `init_db()` with default $500 balance seed row.
2. **Step 2: Core Modules Creation**:
   - Create `metasignals_triage.py` (5-pillar gate).
   - Create `metasignals_paper_engine.py` (paper order submission, fees, stop ratchets).
   - Create `metasignals_watcher.py` (30s polling loop against Bitunix mark prices).
   - Create `metasignals_radar.py` (read-only query helper).
3. **Step 3: Server Registration (`main.py`)**:
   - Add `POST /api/metasignals/alert`, `GET /api/metasignals/snapshot`, and `GET /admin/metasignals`.
   - In `main.py` lifespan startup, spawn: `app.state.metasignals_watch_task = asyncio.create_task(metasignals_watcher.run_metasignals_watch_loop())`.
   - In lifespan shutdown, cancel `app.state.metasignals_watch_task`.
4. **Step 4: UI & Templates**:
   - Create `templates/metasignals_desk.html`.
   - Add a navigation link to the top header in `templates/base.html`.
5. **Step 5: Testing & Zero-Contamination Verification**:
   - Run tests: confirm Traveler, Alt Matrix, and MetaSignals pass 100% of tests.
   - Verify zero imports from Traveler or Alt Matrix in `metasignals_*.py`.
