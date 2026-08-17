# Kabroda Diagnostic Command Center — E2E Test Plan and Architecture Design

## Executive Summary
This document defines the End-to-End (E2E) test plan and design for the **Kabroda Diagnostic Command Center**. The testing track (Milestone `M_TEST`) evaluates the system in an opaque-box manner across the **AI API Layer** (`/api/v1/system/*`) and the **Human Dashboard Layer** (`/suite/dashboard`). 

To ensure safety and reliability, this plan establishes:
1. A **process-isolated E2E test runner** to avoid concurrency issues with background schedulers.
2. A **session-cookie-based authentication harness** that mirrors production login mechanics.
3. Strict **database state isolation** using `kabroda_test.db` to prevent live production database pollution.
4. An exhaustive registry of **91 test cases** mapped across 4 tiers.

---

## 1. E2E Test Runner and Execution Architecture

To test FastAPI in an opaque-box manner, we evaluate two execution patterns:

### Method A: Process-Isolated Testing (Recommended)
The test suite spawns the FastAPI server in a dedicated subprocess (e.g. `uvicorn main:app --port 8000`) before running the tests. The tests run in a separate process, using Python’s `unittest` or `pytest` frameworks and the standard `requests` library.

*   **Pros**:
    *   **True Opaque-Box**: Exercises the actual TCP network socket, CORS middleware, cookie serialization, and Uvicorn thread pool.
    *   **No Event Loop Contention**: Avoids sharing the asyncio event loop between the test client and the server, preventing deadlock issues.
    *   **Subprocess Environment Isolation**: Environment variables (such as `DATABASE_URL` and `SESSION_SECRET`) are isolated to the server process.
*   **Cons**: Requires subprocess lifecycle management (spawning, port checking, and clean termination).

### Method B: In-Process ASGI Testing (`fastapi.testclient.TestClient`)
The test suite imports the FastAPI application object (`main.app`) and routes calls in-process via Starlette's mock ASGI adapter.

*   **Pros**: Faster execution; does not require socket binding; easily captures coverage.
*   **Cons**:
    *   **Background Tasks Conflict**: `main.py` registers an async `lifespan` handler that starts 8 background loops (gravity ingestion, ledger audits, schedulers, etc.) that poll live exchanges and write to the database. These tasks will run concurrently on the same event loop as the test suite, polluting test assertions.
    *   **Direct Database References**: Several backend modules use `database.SessionLocal()` directly rather than route-injected `Depends(get_db)`. In-process overrides are prone to leakages if `database.py` is imported before the environment variables are set.

### Architecture Selection
We select **Method A (Process-Isolated)** as the standard E2E testing architecture. The test harness will spawn `python main.py` in a separate shell with isolated configuration.

---

## 2. Authentication Handling

The Kabroda Server implements cookie-based session authentication using Starlette's `SessionMiddleware`.
- **Session Key**: `kabroda_user_id` (contains the integer primary key of the logged-in user in the `users` table).
- **Session Security**: Cookies are cryptographically signed using a hash-based message authentication code (HMAC) via the `SESSION_SECRET` environment variable (defaults to `kabroda_prod_key_999`).

### E2E Test Auth Flow
The test client handles authentication programmatically:
1.  **Direct Database Seeding**: Before launching the server (or via direct SQLite connection), the test runner seeds the test database `users` table with an admin user:
    *   `email`: `admin@kabroda_test.com`
    *   `password_hash`: Generated using `auth.hash_password("admin_test_pass")` (PBKDF2-SHA256).
    *   `is_admin`: `True`
    *   `tier`: `admin`
2.  **Session-Based Client HTTP requests**:
    *   The test script instantiates a `requests.Session()` object, which automatically manages and persists cookies.
    -   The script calls `POST /login` with form parameters:
        ```http
        POST /login HTTP/1.1
        Content-Type: application/x-www-form-urlencoded

        email=admin@kabroda_test.com&password=admin_test_pass
        ```
    -   The server verifies the password against the seeded database, assigns `request.session["kabroda_user_id"] = user.id`, issues a `Set-Cookie` header containing the signed session cookie, and returns a `303 Redirect` to `/suite`.
    -   The `requests.Session` object stores this cookie, presenting it on all subsequent calls to `/api/v1/system/*` and `/suite/dashboard`.

---

## 3. Database Isolation Strategy

`database.py` constructs its connection engine using:
`DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kabroda.db")`

To isolate the test state and guarantee that no real data is overwritten, the test runner implements the following protocol:

### The Isolation Protocol
1.  **Define Environment Variables**: Before starting the server subprocess, set:
    *   `DATABASE_URL=sqlite:///./kabroda_test.db`
    *   `SESSION_SECRET=test_secret_key_12345`
    *   `INTEGRITY_MODE=development`
2.  **Database Lifecycle Hook**:
    ```python
    def setup_database():
        # 1. Delete old test DB if it exists
        if os.path.exists("kabroda_test.db"):
            os.remove("kabroda_test.db")
        
        # 2. Run schema initialization and migrations
        from database import init_db
        init_db()  # Creates all tables, runs ALTER TABLE patches
        
        # 3. Seed baseline users and configuration parameters
        seed_baseline_data()
    ```
3.  **Active Scheduler Suppression**: Since background schedulers (e.g. `run_outcome_tracker`, `run_weekly_scheduler`) auto-start on boot and query live API endpoints (e.g. Binance BTC/USDT price), the test runner can run the application in **Offline Testing Mode** by setting:
    *   `OFFLINE_TEST=True`
    This environment variable is checked inside `main.py` (or mocked out via standard Python import hooks) to bypass live exchange queries and prevent background loops from making real API calls during E2E test runs.

---

## 4. Comprehensive Test Suite Enumeration (91 Test Cases)

### Tier 1: Feature Coverage (35 Cases)
*Happy-path tests for each of the 7 features in isolation.*

#### Feature 1: GET /api/v1/system/state (System Snapshot)
1.  **F1-T1-1: Retrieve Active Sessions**: Call `/api/v1/system/state` with an admin session. Verify it returns a list of active sessions matching the `CampaignLog` rows with `status="ACTIVE"`.
2.  **F1-T1-2: Retrieve Active Shadow Runners**: Call `/api/v1/system/state`. Verify it successfully returns shadow runners with `shadow_runner_active=True`, checking for keys: `shadow_runner_stop`, `shadow_runner_ema21`, and `shadow_runner_leg2_r`.
3.  **F1-T1-3: Retrieve Scheduler Health**: Call `/api/v1/system/state`. Check that the `scheduler_health` section lists all 6 standard schedulers (Senior Analyst, Jewel, Weekly, Daily, Outcome, Session Monitor) with last/next scheduled times.
4.  **F1-T1-4: Retrieve Macro Engine Status**: Call `/api/v1/system/state`. Verify the `macro_engine` section returns correct signals and action flags for the current day.
5.  **F1-T1-5: Retrieve System Errors Summary**: Call `/api/v1/system/state`. Verify that `recent_errors` returns the last 50 entries from `SystemAuditLog`.

#### Feature 2: GET /api/v1/system/trades (Trade History & Metrics)
6.  **F2-T1-1: Retrieve Last N Trades**: Call `/api/v1/system/trades?limit=5`. Verify it returns exactly the last 5 resolved trades from `CampaignLog` with realized PnL and targets.
7.  **F2-T1-2: 7-Day Window Metrics Calculation**: Call `/api/v1/system/trades?window=7d`. Verify that the returned `win_rate`, `net_r`, and `approval_rate` are calculated only using canonical trades created within the last 7 days.
8.  **F2-T1-3: 30-Day Window Metrics Calculation**: Call `/api/v1/system/trades?window=30d`. Verify metrics match the 30-day window.
9.  **F2-T1-4: All-Time Metrics Calculation**: Call `/api/v1/system/trades?window=all`. Verify metrics match all trades marked `is_canonical=True` in the database.
10. **F2-T1-5: Performance Breakdown Summary**: Call `/api/v1/system/trades`. Verify the response contains a breakdown of win rate and net R grouped by `session_timeframe` (15M, 1H, 4H) and `bias` (LONG, SHORT).

#### Feature 3: GET /api/v1/system/parameters (Parameter Registry)
11. **F3-T1-1: Retrieve Parameter Registry**: Call `/api/v1/system/parameters`. Verify that all expected tunable parameters (BBWP, PMARP, RSI, position sizing, ATR multipliers) are returned in the response.
12. **F3-T1-2: Parameter Details Verification**: Call `/api/v1/system/parameters`. Verify that each parameter object has a `key`, `current_value`, `description`, and `last_changed` field.
13. **F3-T1-3: Parameter Change Log Verification**: Call `/api/v1/system/parameters`. Verify that the change log contains a history of values, authors, and change reasons.
14. **F3-T1-4: Dependency Map Extraction**: Call `/api/v1/system/parameters`. Verify that the `dependencies` key accurately maps parameters to their downstream trading components.
15. **F3-T1-5: Filter Parameters by Component**: Call `/api/v1/system/parameters?component=indicators`. Verify only indicator-related parameters are returned.

#### Feature 4: GET /api/v1/system/errors (Error & Alert Logs)
16. **F4-T1-1: Retrieve System Error Log**: Call `/api/v1/system/errors`. Verify that the list contains details of logged system errors (timestamps, severity, component, traceback).
17. **F4-T1-2: Retrieve Alert History**: Call `/api/v1/system/errors`. Verify it returns active and resolved alerts with current resolution status.
18. **F4-T1-3: Retrieve System Health Summary**: Call `/api/v1/system/errors`. Verify the response contains system uptime, crash count, and scheduler status flags.
19. **F4-T1-4: Filter Errors by Severity**: Call `/api/v1/system/errors?severity=WARNING`. Verify that only errors of warning severity are returned.
20. **F4-T1-5: Limit Error Count Retrieval**: Call `/api/v1/system/errors?limit=10`. Verify the list returns exactly the 10 most recent error entries.

#### Feature 5: POST /api/v1/system/analysis (AI Analysis Endpoint)
21. **F5-T1-1: Submit PMARP Performance Query**: Send `POST /api/v1/system/analysis` with `{"query": "analyze PMARP threshold performance over last 30 days"}`. Verify it returns a structured analysis report with suggestions.
22. **F5-T1-2: Submit BBWP Squeeze Query**: Send `POST /api/v1/system/analysis` with `{"query": "analyze BBWP squeeze signals"}`. Verify response contains a statistical overview of squeeze setups.
23. **F5-T1-3: Retrieve Historical Reports**: Call `GET /api/v1/system/analysis`. Verify it returns a list of previously saved reports.
24. **F5-T1-4: Query Specific Timeframe Report**: Send a query targeting the 4H timeframe. Verify that the response contains metrics segmented by the 4H timeframe.
25. **F5-T1-5: Retrieve Specific Report by ID**: Call `GET /api/v1/system/analysis/{id}`. Verify it returns the exact report generated in case F5-T1-1.

#### Feature 6: Human Dashboard Layer UI (Dashboard HTML Template)
26. **F6-T1-1: Overview Page Load**: Call `GET /suite/dashboard`. Verify it returns `200 OK` and contains the base HTML tags.
27. **F6-T1-2: KPIs Rendering Elements**: Verify that the HTML page contains the element IDs or class names used to bind KPI card values (win rate, net R, spend).
28. **F6-T1-3: Navigation Tabs Structure**: Verify the page has container divs for `#overview`, `#live-system`, `#parameters`, `#errors`, and `#analysis` tabs.
29. **F6-T1-4: Chart Containers Presence**: Verify the page contains canvas or SVG container elements for PnL and approval distribution charts.
30. **F6-T1-5: Script Imports Verification**: Verify that the HTML contains references to dashboard Javascript controllers (`static/js/...` or template scripts).

#### Feature 7: AI Analysis Loop (Background Evaluator)
31. **F7-T1-1: Trigger Analysis Loop**: Execute the daily audit scheduler cycle. Verify that a new row is written to the `DailyAuditLog` table.
32. **F7-T1-2: Trade Performance Evaluation**: Verify the loop evaluates resolved trades in `CampaignLog` against the current BBWP/PMARP indicators.
33. **F7-T1-3: Generate Tuning Recommendation**: Seed trades showing low performance at specific thresholds. Run the loop. Verify it writes a tuning suggestion to `AuditSuggestionLog`.
34. **F7-T1-4: Save Output Report**: Verify the loop saves a structured markdown brief in `SystemAuditLog` labeled `AUDIT-AI WEEKLY LEDGER`.
35. **F7-T1-5: Update Run Logs**: Verify that the execution is logged in `AgentRunLog` with tokens used, cost, and `SUCCESS` status.

---

## 5. Boundary & Corner Cases (35 Cases)
*Testing edge cases, empty databases, malformed inputs, and authorization blocks.*

#### Feature 1: GET /api/v1/system/state (State Edges)
36. **F1-T2-1: Empty Database Response**: Clear `campaign_logs` and `session_locks`. Call `/api/v1/system/state`. Verify response returns empty lists instead of crashing.
37. **F1-T2-2: Unauthenticated Request Block**: Call the endpoint without session cookies. Verify it returns `401 Unauthorized`.
38. **F1-T2-3: Standard User Access Verification**: Authenticate as a non-admin user. Call the endpoint. Verify that access is allowed (read-only telemetry is standard for all operatives).
39. **F1-T2-4: Missing Scheduler Log Records**: Delete all entries in the scheduler health status table. Call the endpoint. Verify that the response returns `"NEVER"` or `null` for last run times without crashing.
40. **F1-T2-5: Corrupted JSON in Session Lock**: Insert a corrupted JSON string into `session_locks.packet_data`. Call the endpoint. Verify it catches the parsing exception, logs a warning, and returns empty levels, without throwing a 500 error.

#### Feature 2: GET /api/v1/system/trades (Trade Edges)
41. **F2-T2-1: Empty Trade History Calculations**: Clear `campaign_logs`. Call `/api/v1/system/trades`. Verify calculations return `0.0` for rates and PnL, and an empty list of trades.
42. **F2-T2-2: Net R with Highly Fractional PnL**: Seed resolved trades with fractional values (e.g. `+1.3456R`, `-0.8712R`). Call the endpoint. Verify `net_r` equals the exact sum (`0.4744`) and is rounded correctly.
43. **F2-T2-3: Exclude Non-Canonical Trades**: Seed trades with `is_canonical=False`. Call the endpoint. Verify they are omitted from both calculations and list.
44. **F2-T2-4: Malformed Window Parameter**: Call `/api/v1/system/trades?window=invalid_string`. Verify the API returns `400 Bad Request` or defaults safely to `all` with a validation warning.
45. **F2-T2-5: Trades with Null realized_pnl**: Seed resolved trades (CLOSED_WIN/LOSS) with null realized_pnl. Verify the metrics ignore these rows or handle them as 0.0 without crashing.

#### Feature 3: GET /api/v1/system/parameters (Parameter Edges)
46. **F3-T2-1: Empty Parameters Configuration**: Clear the parameter table. Call `/api/v1/system/parameters`. Verify it handles the empty table by returning an empty list, or falls back to hardcoded defaults in code without throwing a 500 error.
47. **F3-T2-2: Unauthenticated Parameters Request**: Call the endpoint without session cookies. Verify it returns `401 Unauthorized`.
48. **F3-T2-3: Parameter with Empty Change Log**: Seed a parameter with no entries in `AuditSuggestionLog`. Call the endpoint. Verify it returns the parameter successfully with an empty `change_history` list.
49. **F3-T2-4: Parameter Values Out of Bounds**: Seed a parameter value that exceeds constraints (e.g., position risk % = 150%). Call the endpoint. Verify it returns the value but flags a validation warning status.
50. **F3-T2-5: Cyclic Dependencies in Map**: Seed a cyclic dependency (A -> B -> A). Verify the endpoint parses the dependency map correctly as a list of edges without entering an infinite loop.

#### Feature 4: GET /api/v1/system/errors (Error Edges)
51. **F4-T2-1: Empty Error Log Tables**: Clear all error tables. Call `/api/v1/system/errors`. Verify the response returns empty lists and a healthy summary without crashes.
52. **F4-T2-2: System Crash Recovery State**: Simulate a crash state (mock a last crash event). Call the endpoint. Verify that the `last_crash` timestamp shows the correct failure timestamp.
53. **F4-T2-3: Unauthenticated Errors Request**: Call the endpoint without session cookies. Verify it returns `401 Unauthorized`.
54. **F4-T2-4: Massive Traceback String**: Seed an error log entry with a 50KB traceback. Call the endpoint. Verify that the response returns successfully (or truncates the text safely) without database memory errors.
55. **F4-T2-5: Invalid Severity Filter**: Call `/api/v1/system/errors?severity=UNKNOWN_VAL`. Verify it handles the invalid severity gracefully by returning all errors or raising a clear validation error.

#### Feature 5: POST /api/v1/system/analysis (Analysis Edges)
56. **F5-T2-1: Empty Request Payload**: Send a request with an empty body or missing query string. Verify it returns `422 Unprocessable Entity` or `400 Bad Request`.
57. **F5-T2-2: Unauthenticated Analysis Request**: Call the endpoint without session cookies. Verify it returns `401`.
58. **F5-T2-3: Request Analysis with Empty Database**: Clear all trades and logs. Send a valid analysis query. Verify it returns a report with status `"INSUFFICIENT_DATA"` rather than crashing.
59. **F5-T2-4: Query Injection Attack**: Send a prompt containing SQL injection payload (e.g. `'; DROP TABLE users; --`). Verify inputs are treated as literal strings and no database execution occurs.
60. **F5-T2-5: Timeout Handling for Large Datasets**: Seed 10,000 campaigns, and call the analysis endpoint. Verify the system handles the request safely without HTTP timeout.

#### Feature 6: Human Dashboard Layer UI (Dashboard Edges)
61. **F6-T2-1: Dashboard Load with Empty Tables**: Clear the database. Call `GET /suite/dashboard`. Verify it renders successfully with `0.0` or `"N/A"` values without template errors.
62. **F6-T2-2: Unauthenticated Page Request**: Call `/suite/dashboard` without session cookies. Verify it returns a `303 Redirect` to `/login`.
63. **F6-T2-3: User Session Timeout**: Pass an expired session cookie to the dashboard route. Verify it redirects to `/login`.
64. **F6-T2-4: Missing User Record in Session**: Inject a valid session cookie representing a non-existent user ID. Call the route. Verify it redirects to `/login` or logs out instead of throwing a 500 error.
65. **F6-T2-5: Invalid Asset Paths**: Call `/suite/dashboard`. Parse output to ensure all static assets (`/static/css/...`) resolve to valid routes and return correct content-types.

#### Feature 7: AI Analysis Loop (Analysis Loop Edges)
66. **F7-T2-1: Run Loop with Zero Historical Trades**: Clear `campaign_logs`. Execute the loop. Verify it completes successfully with an `"insufficient data"` status log.
67. **F7-T2-2: Database Write Lock Contention**: Simulate a DB write lock. Trigger the loop. Verify it handles retry logic or logs a database exception gracefully instead of crashing the server process.
68. **F7-T2-3: Missing LLM API Keys**: Unset the OpenAI/Anthropic API keys. Trigger the loop. Verify that the system falls back to a deterministic rule-based evaluator (or writes an error alert to `SystemAuditLog`) without stopping the scheduler loop.
69. **F7-T2-4: Out of Bounds Parameters**: Set corrupted parameters in the database. Trigger the loop. Verify the analysis loop ignores or sanitizes them and logs a warning.
70. **F7-T2-5: Duplicate Execution Prevention**: Trigger the loop twice on the same calendar day. Verify it checks `DailyAuditLog.date_key` and skips the second run.

---

## 6. Tier 3: Cross-Feature Combinations (7 Cases)
*Dynamic interactions across multiple features and tables.*

71. **CF-1: Error Generation and Error Log Propagation**:
    *   *Scenario*: Cause a simulated scheduler crash or API failure (e.g. in the AI Analysis Loop).
    *   *Validation*: Call `GET /api/v1/system/errors` and `GET /api/v1/system/state`. Verify that the failure is captured in `recent_errors` (from `SystemAuditLog`) and that the scheduler health shows a missed/failed run.
72. **CF-2: Trade Close and Performance Metrics Update**:
    *   *Scenario*: Create an active trade in the database. Call `/api/v1/system/trades` and verify win rate/net R. Then simulate a price touch that closes the trade (updating `CampaignLog` status to `CLOSED_WIN` and setting `realized_pnl`).
    *   *Validation*: Call `GET /api/v1/system/trades` again. Verify that the win rate, net R, and trades list are updated dynamically and accurately.
73. **CF-3: Parameter Update and State Snapshot Alignment**:
    *   *Scenario*: Modify a tunable parameter (e.g. BBWP threshold) via a database update or mock admin API call.
    *   *Validation*: Call `GET /api/v1/system/parameters` to verify the parameter value and change history. Then call `GET /api/v1/system/state` and verify that the macro engine or active session configurations reflect the updated parameter.
74. **CF-4: AI Analysis Generation and Dashboard Tab Integration**:
    *   *Scenario*: Send a POST request to `/api/v1/system/analysis` to generate a report, or trigger the AI Analysis Loop.
    *   *Validation*: Call `GET /suite/dashboard` and request the analysis JSON endpoint. Verify that the newly generated report appears on the Analysis Tab of the dashboard.
75. **CF-5: Real-Time Price Update and Session Monitor Outcome Tracking**:
    *   *Scenario*: Seed a trade candidate with status `PENDING` and a specific entry price. Trigger the `outcome_tracker` task (or call the price tick endpoint). Simulate a price update that crosses the entry price.
    *   *Validation*: Verify that `CampaignLog.entry_filled_at` is updated, and the trade status moves to `ACTIVE`. Then simulate a price target hit and verify that status becomes `CLOSED_WIN` and `target_hit` is set to `T1`.
76. **CF-6: Shadow Runner Triggering on Canonical Trade Close**:
    *   *Scenario*: Simulate the close of a canonical trade at target `T1` (`status` = `CLOSED_WIN`, `is_canonical` = `True`).
    *   *Validation*: Verify that the system automatically seeds a corresponding shadow runner in `campaign_logs` with `shadow_runner_active=True`, copying the parameters and trailing stop. Then verify that `GET /api/v1/system/state` includes this active shadow runner.
77. **CF-7: Weekly Audit Run and Suggestion Log Rendering**:
    *   *Scenario*: Seed at least 30 resolved trades in `SessionAuditLog`. Trigger the Audit-AI weekly ledger run (using `POST /api/admin/run-audit`).
    *   *Validation*: Verify that new suggestions are written to `AuditSuggestionLog` (for hypotheses meeting N>=30). Verify that calling `GET /api/v1/system/parameters` includes these suggestions in the parameter registry metadata, and the dashboard displays them.

---

## 7. Tier 4: Real-World Application Scenarios (14 Cases)
*End-to-end user workflows and complex operational cycles.*

78. **RW-1: Admin Bootstrapping and Standard User Creation Workflow**:
    *   *Flow*: Set `ADMIN_EMAIL` and `ADMIN_PASSWORD` env vars. Boot the system. Call `POST /login` with admin credentials. Authenticate. Call `POST /admin/create-user` to invite a standard user. Log out. Log in as the new standard user.
    *   *Validation*: Verify successful authentication, session cookie creation, admin panel access restricted for the standard user, but read access allowed for the main suite views.
79. **RW-2: End-to-End Trade Lifecycle Audit and Reporting**:
    *   *Flow*: Seed a session lock for BTC/USDT. A new campaign log is created (`status` = `PENDING`). The trade gets filled (`entry_filled_at` set). The trade is closed at `T1`. The shadow runner is activated. The shadow runner exits after trailing stop hits. The weekly Performance Auditor runs.
    *   *Validation*: Call `GET /api/v1/system/state` to verify active state at each step. Verify the final `cumulative_pnl` on the dashboard overview API. Verify the weekly audit markdown brief contains this trade's data.
80. **RW-3: Parameter Tuning and Counterfactual Hypothesis Audit Loop**:
    *   *Flow*: Admin updates the PMARP exhaustion threshold. The system logs the change. The AI analysis loop runs a counterfactual analysis on the last 30 trades using the new parameter. It generates a suggestion.
    *   *Validation*: Verify the parameter registry contains the change log. Verify the counterfactual result is saved in `TrialsLog` with `candidate_status='UNDER_REVIEW'`. Verify the dashboard parameters tab displays the tuning suggestion.
81. **RW-4: System Outage, Fail-Open, and Recovery Monitoring**:
    *   *Flow*: Simulate an exchange API outage (fetch price returns 0.0 or throws exception). The system fails open (runs and logs a `FAIL-OPEN` state in `InterpreterLog` instead of crashing). The email notifier triggers an alert. The database connection is restored.
    *   *Validation*: Verify that the server did not crash (remains responsive). Verify that the error is logged in `SystemAuditLog` and visible via `GET /api/v1/system/errors`. Verify that once the connection is restored, the next poll runs successfully with state `OK`.
82. **RW-5: AI-Driven Auto-Tuning and Human-in-the-Loop Review**:
    *   *Flow*: The AI Analysis loop runs and detects that the current BBWP threshold is sub-optimal. It writes a suggestion to `AuditSuggestionLog` with status `OPEN`. The admin views the suggestion on the dashboard. The admin clicks "Act On" (simulated via database/admin endpoint). The parameter is updated, and the suggestion status changes to `ACTED_ON`.
    *   *Validation*: Verify that the parameter registry shows the new value and the change history lists the AI suggestion as the reason. Verify that `GET /api/v1/system/state` shows the updated parameter value in the active macro engine config.
83. **RW-6: Active Session Expiration Scenario**:
    *   *Flow*: Seed a pending setup with `session_expires_at` set to a past time. Trigger the `outcome_tracker` loop.
    *   *Validation*: Verify that the status of the setup in `CampaignLog` shifts to `EXPIRED` and `realized_pnl` is set to null.
84. **RW-7: Single-Target 4H/1H Candidate Creation and Validation**:
    *   *Flow*: Trigger a mock 4H BOS breakout. Generate a candidate setup.
    *   *Validation*: Verify the candidate writes `target_logic_version='v3'` or `v4` with `t2=None` and `t3=None` (for single-target v3 setups) or populated values (for v4), and that the insert succeeds without NotNullViolation errors.
85. **RW-8: Macro-Bias Alignment Gate Bypass**:
    *   *Flow*: Trigger a 1H candidate that is counter-trend to the daily weekly crossover bias, and a 4H candidate that is counter-trend.
    *   *Validation*: Verify that the 1H counter-trend candidate is blocked (never written to `CampaignLog`), while the 4H counter-trend candidate is successfully written (since bias gating is only enforced on 1H).
86. **RW-9: Binomial Replay Stated Hypothesis Validation**:
    *   *Flow*: Trigger a binomial replay sweep with a non-empty hypothesis string, and another with a null hypothesis string.
    *   *Validation*: Verify that the first run logs to `TrialsLog` with `candidate_status='UNDER_REVIEW'`, while the second (null hypothesis) run auto-labels the status as `DATA_MINED`.
87. **RW-10: Multi-Timeframe Structural Snapshot Capture**:
    *   *Flow*: Create a new MAS verdict.
    *   *Validation*: Verify that the corresponding `SessionAuditLog` row captures the frozen daily 21-EMA direction, 4H 200-SMA position, 1H 200-SMA position, and weekly 200-SMA distance.
88. **RW-11: Daily Per-Trade "Why" Digest Compilation**:
    *   *Flow*: Run daily trades across 15M, 1H, and 4H. Trigger the daily 4H/1H auditor scheduler.
    *   *Validation*: Verify a new `DailyAuditLog` row is created, and the `digest_json` field aggregates the correct fields (e.g. `mas_executive_brief`, `structure_reasoning`, `macro_bias`, etc.) for each trade.
89. **RW-12: Session Monitor Cooldown Enforcement**:
    *   *Flow*: Trigger a monitor notification. Immediately trigger a second notification event within the cooldown window (e.g., 2 hours, where the config cooldown is 4 hours).
    *   *Validation*: Verify the second notification is blocked and not sent.
90. **RW-13: Reset DB Safe Execution Lifecycle**:
    *   *Flow*: Run the `reset_db_safe.py` script.
    *   *Validation*: Verify that the script cleanly resets tables, preserves seed data, and does not deadlock the SQLite database.
91. **RW-14: Dashboard KPI Audit Reconciliation**:
    *   *Flow*: Seed 5 wins, 5 losses, and 2 expired setups in the test DB. Call `/api/dashboard/overview`.
    *   *Validation*: Verify that the calculated win rate matches exactly 50% (5 wins out of 10 resolved) and that expired setups are excluded. Verify `net_r` matches the exact sum of the 10 resolved setups.

---

## 8. Harness Implementation Blueprint

The E2E test harness will be structured under `harness/e2e/` as a modular Python test package:

```
harness/e2e/
├── __init__.py
├── conftest.py             # Pytest fixtures for DB setup and server subprocess spawner
├── test_f1_state.py        # E2E tests for Feature 1 (System State)
├── test_f2_trades.py       # E2E tests for Feature 2 (Trades & Metrics)
├── test_f3_parameters.py   # E2E tests for Feature 3 (Parameters Registry)
├── test_f4_errors.py       # E2E tests for Feature 4 (Errors & Alerts)
├── test_f5_analysis.py     # E2E tests for Feature 5 (AI Analysis Endpoint)
├── test_f6_dashboard.py    # E2E tests for Feature 6 (Dashboard HTML UI)
├── test_f7_loop.py         # E2E tests for Feature 7 (AI Analysis Loop)
├── test_combinations.py    # Tier 3: Cross-Feature combination tests
└── test_scenarios.py       # Tier 4: Real-World workflow scenarios
```

### Pytest Fixture Blueprint (`conftest.py`)
```python
import os
import subprocess
import time
import socket
import pytest
from database import init_db, SessionLocal, UserModel, Base, engine
from auth import hash_password

@pytest.fixture(scope="session", autouse=True)
def test_server():
    # 1. Clean and setup test database
    if os.path.exists("kabroda_test.db"):
        os.remove("kabroda_test.db")
    
    # Set env vars for the server subprocess
    env = os.environ.copy()
    env["DATABASE_URL"] = "sqlite:///./kabroda_test.db"
    env["SESSION_SECRET"] = "test_secret_key_12345"
    env["INTEGRITY_MODE"] = "development"
    env["OFFLINE_TEST"] = "True"
    
    # Run migrations
    init_db()
    
    # Seed default admin user
    db = SessionLocal()
    admin = UserModel(
        email="admin@kabroda_test.com",
        password_hash=hash_password("admin_test_pass"),
        tier="admin",
        is_admin=True
    )
    db.add(admin)
    db.commit()
    db.close()
    
    # 2. Spawn FastAPI server
    server_process = subprocess.Popen(
        ["uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8005"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for server to become responsive
    timeout = 10
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection(("127.0.0.1", 8005), timeout=1):
                break
        except OSError:
            time.sleep(0.5)
    else:
        server_process.terminate()
        raise RuntimeError("FastAPI test server failed to start within timeout.")
        
    yield "http://127.0.0.1:8005"
    
    # 3. Shutdown server subprocess
    server_process.terminate()
    server_process.wait()
    
    # Cleanup database
    if os.path.exists("kabroda_test.db"):
        os.remove("kabroda_test.db")
```

This design guarantees complete database state isolation, full authentication compatibility, and a robust, scalable framework to run 91 tests verifying every requirement of the Kabroda Diagnostic Command Center.
