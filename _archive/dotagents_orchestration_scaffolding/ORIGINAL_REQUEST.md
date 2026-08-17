# Original User Request

## Initial Request — 2026-07-16T01:16:05Z

Build a **diagnostic command center** for the Kabroda trading system — like a mechanic's OBD2 scanner for a high-performance engine. The system has two layers:

1. **AI API Layer** (`/api/v1/system/*`) — JSON endpoints that the AI (Antigravity) calls programmatically to read live system state, query trade history, check parameters, and run analysis. This is the "engine diagnostic port."
2. **Human Dashboard Layer** (upgraded `/suite/dashboard`) — the visual UI you see, consuming the same API endpoints.

Both layers read from the same live data. The AI can query the system at any time, run analysis, and report back with tuning suggestions — without needing a browser.

**Quality bar:** Production-grade, polished, trustworthy — internal-only (not client-facing).

Working directory: `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2`
Integrity mode: development

## Architecture

```
Kabroda Server (main.py)
  ├── /suite/dashboard          ← Human UI (upgraded)
  ├── /api/dashboard/*          ← Existing endpoints (audit & fix)
  └── /api/v1/system/*          ← NEW: AI diagnostic API
        ├── /state              → Full system snapshot (sessions, runners, schedulers)
        ├── /trades             → Trade history with outcomes
        ├── /parameters         → All tunable values + change log
        ├── /errors             → Recent errors and alerts
        └── /analysis           → AI-generated analysis reports
```

## Requirements

### R1. Thorough Audit of Existing Dashboard
Audit the existing Executive Dashboard (`/suite/dashboard` page, `suite_dashboard.html`, and all `/api/dashboard/*` endpoints in `main.py`) for correctness. Verify:
- KPI calculations (win rate, net R, approval rate, spend) match the actual database data
- Trade history table shows correct data with proper formatting
- Charts (PnL, approval distribution, grade accuracy, confluence accuracy) render correctly with real data
- Newsletter archive and audit logs display properly
- All API endpoints handle edge cases (empty DB, null values, missing data)
- Document any bugs, incorrect calculations, or data inconsistencies found

### R2. AI Diagnostic API (`/api/v1/system/*`)
Build a set of JSON API endpoints that the AI can call programmatically:

**`GET /api/v1/system/state`** — Full system snapshot:
- Active sessions with current status, bias, entry, stop, targets
- Active shadow runners (Phase 3B/4B) with current P&L, time remaining, stop distance
- Scheduler health — last run time for all 6 schedulers, next scheduled run, any missed runs
- Macro engine last cycle result (timestamp, signals found, actions taken)
- Recent system errors/warnings (last 50 from SystemAuditLog)

**`GET /api/v1/system/trades`** — Trade history with outcomes:
- Last N trades with full details (entry, stop, targets, outcome, P&L)
- Win rate, net R, approval rate over configurable windows (7d, 30d, all)
- Performance by session type, bias, timeframe

**`GET /api/v1/system/parameters`** — Parameter registry:
- All tunable values: PMARP thresholds, BBWP levels, RSI periods, position sizing risk %, ATR multipliers, session timeouts, EMA periods, etc.
- Current value, description, last changed date, change reason
- Dependency map: "PMARP threshold affects S4 exhaustion entries"
- Read from a structured source (database table or config file)

**`GET /api/v1/system/errors`** — Error & alert monitoring:
- Recent system errors with timestamps, severity, component
- Alert history with resolution status
- System health summary (uptime, last crash, scheduler failures)

**`POST /api/v1/system/analysis`** — AI analysis endpoint:
- Accepts a query from the AI (e.g., "analyze PMARP threshold performance over last 30 days")
- Returns structured analysis with data, findings, and suggestions
- Can be called on a schedule or on-demand

### R3. Upgraded Dashboard UI
Upgrade the existing `/suite/dashboard` page to consume the new API endpoints and add sub-views/tabs:
- **Overview tab** — existing KPI cards + charts (audited and fixed)
- **Live System tab** — active sessions, runners, scheduler health (from `/api/v1/system/state`)
- **Parameters tab** — parameter registry with change log (from `/api/v1/system/parameters`)
- **Errors tab** — error monitoring with alert history (from `/api/v1/system/errors`)
- **Analysis tab** — AI analysis reports (from `/api/v1/system/analysis`)

### R4. AI Analysis Loop
Build an automated analysis system that:
- Periodically calls `/api/v1/system/state` and `/api/v1/system/trades`
- Reviews recent trade outcomes against current parameter settings
- Checks parameter performance against market conditions
- Produces structured analysis reports: "BBWP threshold of 85% has triggered 12 times in the last week with 8 false positives — consider raising to 90%"
- Suggests tuning adjustments based on observed patterns
- Stores reports in a format the dashboard can display

## Acceptance Criteria

### Dashboard Audit
- [ ] Every KPI on the dashboard is verified against raw database queries and matches
- [ ] All edge cases (empty database, null values, missing data) handled without crashes
- [ ] Documented list of bugs found and fixed

### AI Diagnostic API
- [ ] All `/api/v1/system/*` endpoints return valid JSON with correct schema
- [ ] `/state` returns live system snapshot with all required fields
- [ ] `/trades` returns trade history with correct calculations
- [ ] `/parameters` returns all tunable values with change history
- [ ] `/errors` returns error log with severity and timestamps
- [ ] `/analysis` accepts queries and returns structured analysis

### Upgraded Dashboard
- [ ] All existing dashboard features work correctly (audited)
- [ ] New tabs/views display data from the new API endpoints
- [ ] UI is polished, professional, and consistent with Kabroda's design

### AI Analysis
- [ ] AI produces periodic analysis reports
- [ ] Reports are displayed in the dashboard
- [ ] Suggestions are actionable and specific

## Verification

### Programmatic Verification
- Run the existing dashboard API endpoints and compare results against raw SQL queries on the same database
- Verify all API endpoints return valid JSON with correct schema
- Test edge cases by querying the database when it has no data, null values, or unusual states
- Call each `/api/v1/system/*` endpoint and verify the response structure

### Agent-as-Judge Verification
- An independent agent reviews the dashboard UI against the requirements
- Checks that all tabs/views are present and functional
- Verifies that the AI analysis produces coherent, actionable output
