
---

## NEW WORK ORDER — 2026-11-12 — CONTINUOUS PARITY AUDITOR & USER NOTIFICATION PREFERENCES MATRIX (Andy directive)

### A. Context & Directives
Andy has formally directed two structural platform upgrades to eliminate operational drift and notification fatigue:
1. **User Notification Matrix & Account-Bound Isolation:** Give users granular UI controls over how, when, and where they receive alerts. Stop broadcast email blasts that mix paper trading with live execution, and strictly isolate CME notifications to the account owner (Andy).
2. **Continuous Parity Auditor ("The Bigger Auditor"):** Build an automated, continuous verification agent directly into `KTBB_app_v2` that audits data feeds, D1 level generation, D2 order placement, and D3 exits against backtest expectations in real time, alerting on any divergence *before* or *as* it occurs rather than requiring end-of-day post-mortems.

### B. User Notification Matrix Specification (`notify.py` & `database.py`)
1. **Schema Extension (`notification_preferences`):**
   - `notify_via_email` (Boolean, default=True)
   - `notify_via_telegram` (Boolean, default=True)
   - `notify_live_trades` (Boolean, default=True)
   - `notify_paper_simulations` (Boolean, default=False) — *User must opt-in to paper trade alerts; off by default to avoid phantom-fill confusion.*
   - `notify_system_alerts` (Boolean, default=True) — *Rejections, cancellations, API errors.*
   - `crypto_desk_enabled` (Boolean, default=True)
   - `cme_desk_enabled` (Boolean, default=True)
2. **Account-Bound Routing Engine:**
   - **CME Isolation:** CME alerts (MNQ/MES) route exclusively to Andy (`andy@kabroda.com` / `spiritmaker79@gmail.com`). They must NEVER be broadcast to general crypto subscribers.
   - **Live Account Binding:** Account execution emails (order placed, filled, canceled) route exclusively to the account owner via `send_account_email()`.
   - **Notification Tagging:** All messages must explicitly declare origin:
     - `[LIVE] BTCUSDT SHORT - Entry Filled @ 72,410 [Acct #1]`
     - `[PAPER] BTCUSDT SHORT - Entry Triggered @ 72,410 [Simulation]`
   - **Phantom Fill Suppression:** If an account's live entry order was canceled or rejected on the exchange, the engine is strictly forbidden from broadcasting a subsequent entry email for that account.
3. **UI Preferences Panel:**
   - Mount a clean configuration card in the user dashboard (`templates/user_profile.html` or `templates/traveler_desk.html`) allowing checkboxes for channels (Email / Telegram), environments (Live / Paper), and desks (Crypto / CME).

### C. Continuous Parity Auditor Engine (`parity_auditor.py`)
A standing background auditor inside `KTBB_app_v2` running automated checks across three operational gates:
1. **Gate 1: Pre-Lock Sanity Check (T - 5 Minutes):**
   - Verifies candle feeds are active and non-stale (< 120s old).
   - Verifies account configuration: confirms `trailing_drawdown_limit_usd` is populated on prop accounts (preventing the Nov 12 CME rejection), confirms exchange credentials are valid.
   - If any check fails, writes to `system_alert_log` and dispatches an admin alert *before* the session locks.
2. **Gate 2: D2 Execution & Order Book Check (T + 0 to T + 60s post-trigger):**
   - Verifies that placed orders are confirmed on the exchange order book.
   - Catches `POST_ONLY` rejections or IOC cancellations within 5 seconds.
   - If a live order rejects, immediately updates `management_state = 'CLOSED_ENTRY_CANCELED'`, logs the exchange error, and halts any downstream simulation notifications.
3. **Gate 3: End-of-Session Daily Replay Parity (Post-Session Close):**
   - Ingests the day's completed 5m/1H candle stream and replays it through the canonical backtest logic (`replay_v3_5yr.py` for Crypto, CME replay for Futures).
   - Asserts: $|\text{Live Realized R} - \text{Backtest Expected R}| \le \text{Allowed Friction}$.
   - If divergence exceeds tolerance (e.g. missed entry due to slippage, premature close), records a structured discrepancy row in `daily_audit_log` detailing the exact candle timestamp and cause.

### D. Claude Code (CC) Implementation Steps
1. **Step 1:** Apply database migration in `database.py` for `notification_preferences` columns.
2. **Step 2:** Refactor `notify.py` to route messages through user preferences, enforce account isolation, tag `[LIVE]` vs `[PAPER]`, and eliminate phantom simulation broadcasts.
3. **Step 3:** Implement `parity_auditor.py` in `KTBB_app_v2` and wire into `main.py` startup loops to perform automated Pre-Lock sanity and Post-Trigger order verification.
4. **Step 4:** Add unit and integration tests in `tests/test_parity_auditor.py` and `tests/test_notification_routing.py`.
