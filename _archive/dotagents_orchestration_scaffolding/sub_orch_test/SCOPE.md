# Scope: E2E Testing Track (M_TEST)

## Architecture
- The E2E Test Suite acts as an opaque-box test client that interacts with the Kabroda Diagnostic Command Center.
- It targets the AI API Layer (`/api/v1/system/*`) and the Human Dashboard UI (`/suite/dashboard`).
- Data flow: Test Client → HTTP Requests → FastAPI Application (main.py) → SQLite Database (kabroda.db).
- The test suite operates independently of the application source code and evaluates it through public network/HTTP interfaces.

## Features under Test
1. **GET /api/v1/system/state** (F1) - Full system snapshot (sessions, runners, schedulers, macro engine, system errors).
2. **GET /api/v1/system/trades** (F2) - Trade history with outcomes, win rate, net R, approval rate.
3. **GET /api/v1/system/parameters** (F3) - Tunable parameter registry, current values, dependency map, change log.
4. **GET /api/v1/system/errors** (F4) - Error log, alert history, health summary.
5. **POST /api/v1/system/analysis** (F5) - AI analysis query input and structured report output.
6. **Human Dashboard Layer UI** (F6) - HTML dashboard `/suite/dashboard` upgraded with tabbed navigation and API integration.
7. **AI Analysis Loop** (F7) - Background worker running periodic checks and producing structured analysis.

## E2E Testing Methodology
- A 4-tier test suite using Python's `unittest` framework to execute HTTP queries against the active server.
- **Tier 1 - Feature Coverage**: Happy-path tests for each of the 7 features in isolation (at least 35 test cases total, 5 per feature).
- **Tier 2 - Boundary & Corner Cases**: Empty databases, null values, malformed inputs, limit boundaries, database failures (at least 35 test cases total, 5 per feature).
- **Tier 3 - Cross-Feature Combinations**: Dynamic interaction between features (e.g. error logs updating after failures, parameter updates affecting state, analysis loop reading trades) (at least 7 test cases total).
- **Tier 4 - Real-World Application Scenarios**: End-to-end user and AI workflows, e.g. login → parameter audit → query analysis → verify report on UI (at least 5 test cases total).

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| T1 | Test Harness & Environment Setup | Configure E2E test environment, launcher scripts, and verify connectivity | None | PLANNED |
| T2 | Tier 1 E2E Test Implementation | Code 35 happy-path test cases covering F1-F7 | T1 | PLANNED |
| T3 | Tier 2 E2E Test Implementation | Code 35 edge/boundary test cases covering F1-F7 | T2 | PLANNED |
| T4 | Tier 3 E2E Test Implementation | Code 7 cross-feature interaction test cases | T3 | PLANNED |
| T5 | Tier 4 E2E Test Implementation | Code 5 real-world workflow scenario test cases | T4 | PLANNED |
| T6 | Documentation & Publication | Write TEST_INFRA.md and publish TEST_READY.md at project root | T5 | PLANNED |

## Interface Contracts
### E2E Test Runner ↔ FastAPI Server
- Host: `localhost` / `127.0.0.1`
- Port: `8000` (or dynamically allocated)
- Protocol: HTTP / JSON and HTML Responses
- Credentials: Admin/User session login mock or credentials from test db configuration.
