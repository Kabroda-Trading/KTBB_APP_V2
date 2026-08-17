# E2E Test Design and Analysis Report

This document outlines the architecture, setup, and 82+ E2E test cases for verifying the Kabroda Diagnostic Command Center.

---

## 1. Opaque-Box E2E Testing Architecture

Opaque-box testing verifies the application solely through its public interfaces (HTTP requests, JSON payloads, HTML structures) without relying on internal function calls. For a FastAPI application, we have two primary options:

1. **Subprocess-managed Server (Recommended for true E2E)**:
   - Spawns a real `uvicorn main:app --host 127.0.0.1 --port <PORT>` process.
   - Communicates using the Python `requests` library over the network.
   - **Pros**: True production-like environment testing actual concurrency, ASGI middleware, and network serialization.
   - **Cons**: Slightly slower startup, requires managing server subprocesses and ports.

2. **In-process FastAPI `TestClient`**:
   - Uses `fastapi.testclient.TestClient` (built on `httpx` or `requests`) to mock requests directly against the app instance.
   - **Pros**: Fast, runs in the same process, easier dependency overriding.
   - **Cons**: Less opaque-box, potential state sharing between tests unless carefully reset.

### Recommended Harness Design
We recommend the **Subprocess-managed Server** approach to fulfill the E2E boundary requirements.
- **Port Allocation**: Dynamically allocate a free port using a temporary TCP socket (port `0`) to prevent port conflicts during parallel execution.
- **Polling Ready Check**: Prior to running any test case, the test suite polls the server's home or health check URL (e.g., `GET /login`) in a loop (up to 5 seconds timeout) to guarantee the server is up and responsive.

---

## 2. Authentication and Session Management

Kabroda utilizes Starlette `SessionMiddleware` for authentication, storing user identifiers in the session cookie.

- **Session Identification Key**: `kabroda_user_id`
- **Cookie Name**: `session` (Starlette default)
- **HTTPS Warning**: In local test environments (usually HTTP), set the environment variable `SESSION_HTTPS_ONLY=False` or `PUBLIC_BASE_URL=http://127.0.0.1:<PORT>` to prevent session cookies from being rejected by the browser/client.

### Seeding and Logging In
1. **Admin Bootstrap Mechanism**:
   - `auth.py` contains `ensure_bootstrap_admin(db)` called during the `POST /login` action.
   - It reads `ADMIN_EMAIL` and `ADMIN_PASSWORD` env vars. If set, it automatically creates an admin user in the DB.
2. **E2E Authenticated Client Flow**:
   - The test client initiates a `requests.Session()` object.
   - It performs a `POST /login` with form data: `email` and `password`.
   - The session client automatically captures and persists the signed `session` cookie in subsequent requests.

---

## 3. Database State Isolation

To ensure tests do not pollute or read from production data, complete state isolation is required.

- **Redirection**: Set `DATABASE_URL=sqlite:///./kabroda_test.db` in both the test runner process and the spawned uvicorn subprocess.
- **Lifecycle Setup**:
  1. Delete `kabroda_test.db` if it already exists from a crashed run.
  2. Call `init_db()` from `database.py` (which runs `Base.metadata.create_all`) to construct the tables.
  3. Seed initial state:
     - Populate the `users` table with an admin user and a basic user.
     - Populate configuration or parameter tables.
- **Lifecycle Teardown**:
  1. Terminate the server subprocess.
  2. Delete `kabroda_test.db` to leave a clean workspace.

---

## 4. Layout Compliance
All test files must reside within the main project testing layout.
- **Location**: Test cases should be implemented inside `tests/` or a dedicated `harness/` subdirectory, and never in `.agents/`.
- **Naming**: E2E test files should follow the pattern `test_e2e_*.py`.

---

## 5. 82+ Detailed Test Cases Across 4 Tiers

The 7 features under test are:
- **F1**: `GET /api/v1/system/state`
- **F2**: `GET /api/v1/system/trades`
- **F3**: `GET /api/v1/system/parameters`
- **F4**: `GET /api/v1/system/errors`
- **F5**: `POST /api/v1/system/analysis`
- **F6**: Human Dashboard Layer UI (`/suite/dashboard`)
- **F7**: AI Analysis Loop

### TIER 1: Feature Coverage (Happy Path) - 5 Cases per Feature (35 Cases Total)

#### Feature 1: GET /api/v1/system/state (F1)
1. **F1-T1-1: Successful State Snapshot Retrieval (Admin)**
   - **Description**: Verify that an admin can retrieve the system snapshot.
   - **Assertion**: `200 OK`, valid JSON containing `active_sessions`, `active_runners`, `scheduler_health`, `macro_engine`, and `recent_errors`.
2. **F1-T1-2: Active Sessions Population Verification**
   - **Description**: Seed database with one active session (CampaignLog: `status='PENDING'`), then retrieve state.
   - **Assertion**: `active_sessions` has length 1 with matching session properties.
3. **F1-T1-3: Active Shadow Runners Verification**
   - **Description**: Seed database with one active shadow runner (CampaignLog: `shadow_runner_active=True`, `status='CLOSED_WIN'`), then retrieve state.
   - **Assertion**: `active_runners` has length 1 with matching P&L and stop distance.
4. **F1-T1-4: Scheduler Health Verification**
   - **Description**: Check that scheduler health displays metrics for all 6 active tasks.
   - **Assertion**: `scheduler_health` lists all 6 schedulers with valid `last_run_time` or `next_run_time`.
5. **F1-T1-5: Macro Engine Last Cycle Verification**
   - **Description**: Inspect macro engine cycle results in the state response.
   - **Assertion**: `macro_engine` dictionary contains keys `timestamp`, `signals_found`, and `actions_taken`.

#### Feature 2: GET /api/v1/system/trades (F2)
6. **F2-T1-1: Successful Trade History Retrieval**
   - **Description**: Verify that the trade history endpoint resolves for authenticated clients.
   - **Assertion**: `200 OK`, valid JSON containing `trades` and `metrics`.
7. **F2-T1-2: Metrics Calculation Validation**
   - **Description**: Seed 2 canonical wins (PnL = +1.5R, +2.0R, status='CLOSED_WIN') and 1 canonical loss (PnL = -1.0R, status='CLOSED_LOSS').
   - **Assertion**: `win_rate` is `66.7`, `net_r` is `2.5`, and total trades equal 3.
8. **F2-T1-3: Time Window Filtering (7d)**
   - **Description**: Seed trades at 2 days ago and 10 days ago. Query with `window=7d`.
   - **Assertion**: Returns only the trade from 2 days ago.
9. **F2-T1-4: Time Window Filtering (30d)**
   - **Description**: Seed trades at 2 days, 15 days, and 45 days ago. Query with `window=30d`.
   - **Assertion**: Returns trades from 2 and 15 days ago.
10. **F2-T1-5: Time Window Filtering (all)**
    - **Description**: Seed trades at 2 days, 45 days, and 120 days ago. Query with `window=all`.
    - **Assertion**: Returns all 3 trades.

#### Feature 3: GET /api/v1/system/parameters (F3)
11. **F3-T1-1: Successful Parameter Registry Retrieval**
    - **Description**: Verify retrieving the registry returns parameters and dependencies.
    - **Assertion**: `200 OK`, contains lists `parameters` and `dependencies`.
12. **F3-T1-2: Tunable Parameter Structure Verification**
    - **Description**: Verify the keys of each parameter object in the response.
    - **Assertion**: Objects contain `key`, `value`, `description`, `last_changed`, and `change_reason`.
13. **F3-T1-3: Parameter Dependency Mapping**
    - **Description**: Ensure parameter dependencies are mapped out.
    - **Assertion**: Returns dependencies (e.g. "PMARP threshold affects S4 exhaustion entries").
14. **F3-T1-4: Parameter Change History**
    - **Description**: Verify the change log history contains previous modifications.
    - **Assertion**: Logs include author, modified timestamp, and reason for change.
15. **F3-T1-5: Core Parameters Presence**
    - **Description**: Ensure core indicators like BBWP and PMARP thresholds exist in output.
    - **Assertion**: Registry includes `bbwp_threshold`, `pmarp_threshold`, and `position_risk_pct`.

#### Feature 4: GET /api/v1/system/errors (F4)
16. **F4-T1-1: Successful Errors Log Retrieval**
    - **Description**: Verify errors list retrieval resolves.
    - **Assertion**: `200 OK`, contains lists `errors` and `alert_history`, and dictionary `health_summary`.
17. **F4-T1-2: Error Record Details Verification**
    - **Description**: Seed a system error (component='macro_engine', severity='CRITICAL').
    - **Assertion**: `errors` list contains the record with correct severity and message.
18. **F4-T1-3: Alert History Resolution Status**
    - **Description**: Seed one active and one resolved alert.
    - **Assertion**: `alert_history` reflects correct resolution statuses.
19. **F4-T1-4: Health Summary Parameters**
    - **Description**: Check health summary schema keys.
    - **Assertion**: Response includes `uptime_seconds`, `last_crash_timestamp`, and `scheduler_failure_flag`.
20. **F4-T1-5: Severity Filtering**
    - **Description**: Call `GET /api/v1/system/errors?severity=CRITICAL`.
    - **Assertion**: Returns only critical severity errors.

#### Feature 5: POST /api/v1/system/analysis (F5)
21. **F5-T1-1: Successful Analysis Query Processing**
    - **Description**: Submit a valid analysis request.
    - **Assertion**: `200 OK`, returns `query`, `analysis_id`, and a structured `report`.
22. **F5-T1-2: Structured Report Content Validation**
    - **Description**: Verify report properties.
    - **Assertion**: `report` has keys `date_range`, `findings`, and `suggestions`.
23. **F5-T1-3: Parameter Tuning Suggestions Verification**
    - **Description**: Verify loop generates suggestions based on data trends.
    - **Assertion**: `suggestions` contains at least one actionable parameter modification.
24. **F5-T1-4: Duplicate Query Handling**
    - **Description**: Call the same query twice.
    - **Assertion**: Returns success status code (200) for both.
25. **F5-T1-5: Check Analysis Report Saved to DB**
    - **Description**: Verify execution persists analysis data.
    - **Assertion**: Database contains a new record corresponding to the `analysis_id`.

#### Feature 6: Human Dashboard Layer UI (F6)
26. **F6-T1-1: Successful UI Render**
    - **Description**: Load `/suite/dashboard` as admin.
    - **Assertion**: `200 OK`, HTML response contains navigation bar and container.
27. **F6-T1-2: Overview Tab Elements Verification**
    - **Description**: Inspect HTML DOM for Overview tab components.
    - **Assertion**: Has elements with IDs corresponding to Win Rate, Net R, Approval Rate, and Spend.
28. **F6-T1-3: Live System Tab Elements Verification**
    - **Description**: Inspect HTML DOM for Live System elements.
    - **Assertion**: Has elements/tables for active sessions, active shadow runners, and scheduler health.
29. **F6-T1-4: Parameters Tab Elements Verification**
    - **Description**: Inspect HTML DOM for parameters registry.
    - **Assertion**: Has elements showing parameter tables and dependency charts.
30. **F6-T1-5: Errors Tab Elements Verification**
    - **Description**: Inspect HTML DOM for error logs.
    - **Assertion**: Has elements displaying errors list and alert history.

#### Feature 7: AI Analysis Loop (F7)
31. **F7-T1-1: Periodic Analysis Execution**
    - **Description**: Start the loop and wait for execution.
    - **Assertion**: Loop executes, reads state/trades from DB, and produces a report.
32. **F7-T1-2: Parameter Audit Record Written to DB**
    - **Description**: Verify execution triggers DB write.
    - **Assertion**: A new record is created in `SystemAuditLog` or `AuditSuggestionLog`.
33. **F7-T1-3: Identify BBWP Deviation**
    - **Description**: Seed trades showing 8 false breakouts under BBWP=85%.
    - **Assertion**: Suggestion generated to "raise BBWP threshold to 90%".
34. **F7-T1-4: Suggestions Saved to AuditSuggestionLog**
    - **Description**: Run loop with suggestions.
    - **Assertion**: `AuditSuggestionLog` has records with `n_supporting >= 30`.
35. **F7-T1-5: Analysis Report Displayed on Dashboard**
    - **Description**: Verify the generated report is exposed to the UI.
    - **Assertion**: The report is returned by `/api/v1/system/analysis` or `/api/dashboard/audits`.

---

### TIER 2: Boundary & Corner Cases - 5 Cases per Feature (35 Cases Total)

#### Feature 1: GET /api/v1/system/state (F1)
36. **F1-T2-1: Empty State (Zero Data)**
    - **Description**: Wipe database, call `GET /api/v1/system/state`.
    - **Assertion**: `200 OK`, empty arrays/dicts: `active_sessions: []`, `active_runners: []`, `recent_errors: []`.
37. **F1-T2-2: Unauthenticated State Request Blocked**
    - **Description**: Call `GET /api/v1/system/state` without session cookie.
    - **Assertion**: `401 Unauthorized`.
38. **F1-T2-3: Basic User Access Denied**
    - **Description**: Call endpoint with basic user session.
    - **Assertion**: `403 Forbidden`.
39. **F1-T2-4: Missed Scheduler Runs Detection**
    - **Description**: Seed scheduler last run time as 48 hours ago for a daily scheduler.
    - **Assertion**: `scheduler_health` flags the scheduler as unhealthy.
40. **F1-T2-5: Overloaded Recent Errors (50+ limit)**
    - **Description**: Seed database with 60 system errors in `SystemAuditLog`.
    - **Assertion**: Returns exactly the 50 most recent errors (sorted by timestamp descending).

#### Feature 2: GET /api/v1/system/trades (F2)
41. **F2-T2-1: Empty Trade Database**
    - **Description**: Wipe `campaign_logs`. Call `GET /api/v1/system/trades`.
    - **Assertion**: `200 OK`, `trades: []`, metrics have values `0.0`.
42. **F2-T2-2: Unauthenticated Trades Request Blocked**
    - **Description**: Call endpoint without session cookie.
    - **Assertion**: `401 Unauthorized`.
43. **F2-T2-3: Basic User Access Denied**
    - **Description**: Call endpoint with basic user session.
    - **Assertion**: `403 Forbidden`.
44. **F2-T2-4: Invalid Window Query Parameter**
    - **Description**: Call `GET /api/v1/system/trades?window=invalid_string`.
    - **Assertion**: `400 Bad Request` or fallback to default (`all`).
45. **F2-T2-5: Exclusion of Non-Canonical Trades**
    - **Description**: Seed 1 canonical trade (`is_canonical=True`) and 5 non-canonical trades (`is_canonical=False`).
    - **Assertion**: Response contains exactly 1 trade; metrics computed only from that 1 canonical trade.

#### Feature 3: GET /api/v1/system/parameters (F3)
46. **F3-T2-1: Unauthenticated Parameters Request Blocked**
    - **Description**: Call endpoint without session cookie.
    - **Assertion**: `401 Unauthorized`.
47. **F3-T2-2: Basic User Access Denied**
    - **Description**: Call endpoint with basic user session.
    - **Assertion**: `403 Forbidden`.
48. **F3-T2-3: Empty Parameter Storage**
    - **Description**: Clear all parameters in database/config.
    - **Assertion**: `200 OK`, returns empty parameters list or defaults without crashing.
49. **F3-T2-4: Parameters with Null Values**
    - **Description**: Seed a parameter with null description in DB.
    - **Assertion**: Endpoint handles null gracefully, serializing it without crashing.
50. **F3-T2-5: Very Large Dependency Map**
    - **Description**: Seed 100 parameter dependencies.
    - **Assertion**: Endpoint returns all dependencies without timeout or truncation.

#### Feature 4: GET /api/v1/system/errors (F4)
51. **F4-T2-1: Zero Errors State**
    - **Description**: Clear all system error records, call endpoint.
    - **Assertion**: `200 OK`, `errors: []`, `alert_history: []`, `health_summary` populated.
52. **F4-T2-2: Unauthenticated Errors Request Blocked**
    - **Description**: Call endpoint without session cookie.
    - **Assertion**: `401 Unauthorized`.
53. **F4-T2-3: Basic User Access Denied**
    - **Description**: Call endpoint with basic user session.
    - **Assertion**: `403 Forbidden`.
54. **F4-T2-4: Malformed Error Stack Traces**
    - **Description**: Seed an error log with binary-like stack trace content.
    - **Assertion**: Endpoint sanitizes and serializes the text correctly.
55. **F4-T2-5: System Crash Record Recovery**
    - **Description**: Seed a crash signature in the database.
    - **Assertion**: `health_summary.last_crash_timestamp` matches the seeded crash record.

#### Feature 5: POST /api/v1/system/analysis (F5)
56. **F5-T2-1: Unauthenticated Analysis Request Blocked**
    - **Description**: Call endpoint without session cookie.
    - **Assertion**: `401 Unauthorized`.
57. **F5-T2-2: Basic User Access Denied**
    - **Description**: Call endpoint with basic user session.
    - **Assertion**: `403 Forbidden`.
58. **F5-T2-3: Empty Query Payload**
    - **Description**: `POST /api/v1/system/analysis` with `{"query": ""}`.
    - **Assertion**: `422 Unprocessable Entity` or `400 Bad Request`.
59. **F5-T2-4: Malformed JSON Payload**
    - **Description**: Submit bad JSON syntax (e.g. `{query: }`).
    - **Assertion**: `400 Bad Request`.
60. **F5-T2-5: Insufficient Data for Analysis**
    - **Description**: Clear trade history and run analysis.
    - **Assertion**: `200 OK` with report stating "insufficient trade data".

#### Feature 6: Human Dashboard Layer UI (F6)
61. **F6-T2-1: Unauthenticated UI Request Redirects**
    - **Description**: `GET /suite/dashboard` without session cookie.
    - **Assertion**: Redirects (303/302) to `/login`.
62. **F6-T2-2: Basic User UI Render Blocked**
    - **Description**: `GET /suite/dashboard` with basic user session.
    - **Assertion**: Redirects to `/suite` or shows `403 Forbidden`.
63. **F6-T2-3: UI Renders Safely with Empty API Data**
    - **Description**: Wipe DB, load `/suite/dashboard`.
    - **Assertion**: UI does not crash; KPI cards show 0 or "N/A" and tables display empty states.
64. **F6-T2-4: UI Handles Malformed API Response Gracefully**
    - **Description**: Mock API endpoints called by UI to return invalid JSON.
    - **Assertion**: Page remains responsive; displays clean error messages in place of charts/tables instead of crashing.
65. **F6-T2-5: Responsive View Verification (Mobile/Desktop)**
    - **Description**: Verify CSS classes/viewport configuration in HTML head.
    - **Assertion**: `<meta name="viewport" content="...">` and tailwind/responsive classes are present.

#### Feature 7: AI Analysis Loop (F7)
66. **F7-T2-1: Loop Handles Database Downtime Gracefully**
    - **Description**: Close DB connection during loop execution.
    - **Assertion**: Loop catches exceptions, waits, and retries next cycle without crashing server.
67. **F7-T2-2: Zero New Trades to Analyze**
    - **Description**: No new trades since last analysis. Run loop.
    - **Assertion**: Loop exits early without writing redundant reports.
68. **F7-T2-3: Loop Handles Corrupt Parameter Data**
    - **Description**: Seed parameter registry with invalid values.
    - **Assertion**: Loop logs warning and skips processing instead of throwing uncaught exception.
69. **F7-T2-4: Duplicate Execution Prevention (Mutex/Locking)**
    - **Description**: Trigger loop while another instance is running.
    - **Assertion**: Second instance exits immediately or waits.
70. **F7-T2-5: Analysis Generation Uptime Continuity**
    - **Description**: Simulate long-running server uptime (e.g. mock multiple ticks).
    - **Assertion**: No memory leaks or thread accumulation in the scheduler task list.

---

### TIER 3: Cross-Feature Combinations (7 Cases)

71. **F_COMB-1: Dynamic Error Log Update After System Failure**
    - **Description**: Simulate a macro engine signal retrieval failure (e.g. mock database exception). Then call `GET /api/v1/system/errors` and check dashboard Errors tab.
    - **Assertion**: The failure is immediately logged; errors API and Errors tab both display the new entry.
72. **F_COMB-2: Parameter Audit Change Reflected in System State**
    - **Description**: Update a tunable parameter (e.g. PMARP threshold) via admin settings. Then call `GET /api/v1/system/state` and `GET /api/v1/system/parameters`.
    - **Assertion**: The new parameter value appears in both state snapshot and parameters registry immediately.
73. **F_COMB-3: Trade Close Triggers Outcome Audit and AI Analysis Loop Update**
    - **Description**: Change a `CampaignLog` status to `CLOSED_WIN` with realized P&L. Run the outcome tracker and then the AI analysis loop.
    - **Assertion**: Outcome fields in `DecisionJournal` are filled, metrics on `GET /api/v1/system/trades` update, and the analysis loop incorporates the trade.
74. **F_COMB-4: Active Session Expiry Transitions to System Error State**
    - **Description**: Seed a campaign that expires (current time > `session_expires_at` and not filled). Run `session_monitor`.
    - **Assertion**: Campaign status updates to `EXPIRED`, `GET /api/v1/system/state` shows zero active sessions, and a warning/audit log is created.
75. **F_COMB-5: Analysis Report Written by Loop Instantly Renders on UI Tab**
    - **Description**: Let the background loop generate a report. Query `GET /api/dashboard/audits` and load the HTML page `/suite/dashboard` on the Analysis tab.
    - **Assertion**: HTML UI displays the latest report data matching the DB record.
76. **F_COMB-6: Active Runner Stop Hit Resolves Runner and Updates System State**
    - **Description**: Seed an active shadow runner. Update price to cross stop level. Trigger the outcome tracker tick.
    - **Assertion**: Runner `shadow_runner_active` transitions to `False`, exit reason set to `STOP`, P&L recorded, and `GET /api/v1/system/state` shows no active runners.
77. **F_COMB-7: User Session Expiry Forces Logout Across API and UI**
    - **Description**: Clear the session cookie or expire session. Attempt to call `/api/v1/system/state` and `/suite/dashboard`.
    - **Assertion**: Both reject request with 401 or redirect to login.

---

### TIER 4: Real-World Application Scenarios (5 Cases)

78. **F_SCEN-1: Full Operator & AI Diagnostics Workflow**
    - **Step 1**: Log in as admin via `POST /login` with credentials.
    - **Step 2**: Query `/api/v1/system/state` to verify engine health.
    - **Step 3**: Query `/api/v1/system/trades?window=30d` to review recent wins/losses.
    - **Step 4**: Trigger AI analysis query via `POST /api/v1/system/analysis` to check BBWP levels.
    - **Step 5**: Load `/suite/dashboard` to verify that the report displays on the Analysis tab.
    - **Assertion**: Every step returns `200 OK` with valid data, and UI renders without warnings.
79. **F_SCEN-2: Clean System Onboarding & Initialization**
    - **Step 1**: Start with a completely empty database (`kabroda_test.db`).
    - **Step 2**: Set environment variables for admin bootstrap.
    - **Step 3**: Launch server and call `POST /login` to trigger user bootstrap.
    - **Step 4**: Query `/api/v1/system/state` and `/api/v1/system/parameters` to confirm default system setup.
    - **Assertion**: Database tables initialize, admin user is created, and default parameters are populated without manual intervention.
80. **F_SCEN-3: Intraday Session Setup & Trade Lifecycle Audit**
    - **Step 1**: Lock a session for today in `SessionLock` with level configurations.
    - **Step 2**: Query `/api/v1/system/state` to verify the session is active.
    - **Step 3**: Trigger a mock fill at entry price, transitioning campaign to active.
    - **Step 4**: Trigger a price move hitting target T1, closing the trade.
    - **Step 5**: Run outcome tracker and check `/api/v1/system/trades`.
    - **Assertion**: State snapshot updates dynamically at each step, and metrics include the resolved trade.
81. **F_SCEN-4: Parameter Calibration & Drift Management**
    - **Step 1**: Query `/api/v1/system/parameters` to record baseline parameters.
    - **Step 2**: Seed 30 failing trades that violate the baseline parameters.
    - **Step 3**: Run the daily/weekly audit AI loops.
    - **Step 4**: Query `/api/v1/system/parameters` to view the suggestions log.
    - **Assertion**: System flags the parameter drift and logs suggested corrections with `n_supporting >= 30` in the change registry.
82. **F_SCEN-5: Multi-User Concurrent Session Separation**
    - **Step 1**: Log in as admin (User A) and basic user (User B) in separate sessions.
    - **Step 2**: User A (admin) creates a new system analysis report.
    - **Step 3**: User B (basic user) tries to query `/api/v1/system/state` and `/api/v1/system/errors`.
    - **Step 4**: User A views the dashboard.
    - **Assertion**: User B is blocked with `403 Forbidden`, while User A successfully retrieves state and errors without cross-session pollution.
