# Project: Kabroda Diagnostic Command Center

## Architecture
The Kabroda Diagnostic Command Center is a two-layer system:
1. **AI API Layer** (`/api/v1/system/*`) — JSON endpoints programmatically accessible to retrieve live system state, trade history, parameters, error logs, and AI analysis reports.
2. **Human Dashboard Layer** (upgraded `/suite/dashboard`) — A tabbed web UI consuming the AI API endpoints to visualize live system telemetry, parameters registry, errors list, and AI reports.
3. **AI Analysis Loop** — A background worker that periodically evaluates system performance, reviews parameter metrics against trade outcomes, and writes recommendations.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Dashboard Audit & Fix | Audit and correct `/api/dashboard/*` and `/suite/dashboard` for calculations, formatting, and null/empty states | None | IN_PROGRESS (Conv ID: 51cfc87e-9770-47dc-b09a-f76e59729362) |
| M2 | AI Diagnostic API | Build `/api/v1/system/state`, `/trades`, `/parameters`, `/errors`, `/analysis` JSON endpoints | None | IN_PROGRESS (Conv ID: 698fd973-155a-4dd5-af9e-f19e690fbe5c) |
| M3 | Upgraded Dashboard UI | Revamp HTML/JS UI with Overview, Live System, Parameters, Errors, and Analysis tabs | M1, M2 | PLANNED |
| M4 | AI Analysis Loop | Implement periodic evaluator background thread/scheduler and save suggestions | M2 | PLANNED |
| M5 | Integration & Verification | Final E2E test verification, bug fixing, and Tier 5 adversarial hardening | M3, M4, M_TEST | PLANNED |
| M_TEST | E2E Testing Track | Build opaque-box E2E test harness and Tier 1-4 test cases independently | None | IN_PROGRESS (Conv ID: 13f5b853-cffd-414d-ae80-ed39d76bfeed) |

## Interface Contracts
### AI Diagnostic API ↔ Dashboard UI / AI Client
- `GET /api/v1/system/state`: Returns `{ active_sessions: [...], active_runners: [...], scheduler_health: [...], macro_engine: {...}, recent_errors: [...] }`
- `GET /api/v1/system/trades`: Returns `{ trades: [...], metrics: { win_rate, net_r, approval_rate } }` (supports query params `window=7d|30d|all`)
- `GET /api/v1/system/parameters`: Returns `{ parameters: [...], dependencies: [...] }`
- `GET /api/v1/system/errors`: Returns `{ errors: [...], alert_history: [...], health_summary: {...} }`
- `POST /api/v1/system/analysis`: Accepts `{ query: string }` and returns `{ query, analysis_id, report: {...} }`

## Code Layout
- `main.py` — Main entrypoint containing FastAPI route controllers and background task schedulers.
- `database.py` — Database ORM schemas and connection.
- `templates/suite_dashboard.html` — Executive dashboard HTML template.
- `static/` — Static assets for the dashboard web UI.
- `harness/` — Testing and validation framework tools.
- `.agents/` — Agents coordination and progress tracking directories.
