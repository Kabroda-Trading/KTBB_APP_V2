# E2E Test Suite Design and Analysis Report

## Executive Summary
This document provides a comprehensive test plan for the opaque-box End-to-End (E2E) testing of the **Kabroda Diagnostic Command Center**. The testing track (milestone `M_TEST`) aims to validate the **AI API Layer** (`/api/v1/system/*`), the **Human Dashboard Layer** (`/suite/dashboard`), and the **AI Analysis Loop** background tasks.

The test suite operates as an independent opaque-box client that evaluates the system state via HTTP network boundaries, maintaining complete separation from the production database and runtime processes.

---

## 1. Test Architecture & Environment Isolation

### 1.1 Server Execution and Network Layer
To achieve true opaque-box validation, tests are executed against a running server instance over HTTP. The test runner:
- Spawns the FastAPI application in a background subprocess using `uvicorn main:app --host 127.0.0.1 --port 8001`.
- Port `8001` is used to prevent port conflicts with any active production instance running on `8000`.
- The runner uses Python's `requests` or `httpx` to send HTTP requests to `http://127.0.0.1:8001`.
- A startup hook polls the server’s `/login` page up to 30 times with a 100ms interval to verify readiness before starting test execution.
- A teardown hook sends a termination signal to the subprocess and waits for exit.

### 1.2 Database State Isolation
The database configuration in `database.py` defaults to `sqlite:///./kabroda.db` but checks for a `DATABASE_URL` environment variable:
```python
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kabroda.db")
```
To isolate state during test runs:
1. The test runner sets `os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test.db"`.
2. Before the server starts and prior to each test suite execution, the runner checks for the existence of `kabroda_test.db` and deletes it.
3. The runner imports `database` and runs `database.init_db()` in-process, which executes SQLAlchemy's `Base.metadata.create_all` to build the isolated tables.
4. During server subprocess spawning, the `DATABASE_URL` environment variable is passed in, ensuring the app reads and writes to `kabroda_test.db`.
5. Upon teardown, the test database file is deleted.

### 1.3 Authentication & Session Management
Starlette's `SessionMiddleware` manages sessions using signed cookies. The session key is defined in `auth.py`:
```python
SESSION_KEY = "kabroda_user_id"
```
To authenticate opaque-box test calls:
1. **Seed Users**: The test client inserts test credentials directly into the `users` table in `kabroda_test.db` using an in-process SQLAlchemy session.
   - **Admin User**: Email `admin@kabroda.com`, password `adminpass123`, `is_admin=True`.
   - **Basic User**: Email `user@kabroda.com`, password `userpass123`, `is_admin=False`.
2. **Obtain Cookie**: The test client instantiates a `requests.Session` object and performs a `POST /login` request:
   - Path: `/login`
   - Content Type: `application/x-www-form-urlencoded`
   - Payload: `email=admin@kabroda.com&password=adminpass123`
3. **Follow Redirect**: The `requests.Session` follows the 303 redirect to `/suite` and stores the signed session cookie in its cookie jar.
4. **Authorized Requests**: All subsequent HTTP calls made using that `Session` instance will automatically present the cookie, maintaining authentication context.
5. **Unauthorized/Forbidden Tests**: Tests targeting authentication guards send requests using a clean session object or a session logged in as the basic user.

---

## 2. Feature Scope Analysis

Tests are structured around the 7 primary features (F1 to F7) as defined in the scope:

| Feature | Description | Main Database Tables | Key Endpoints |
|---|---|---|---|
| **F1** | System State Snapshot | `campaign_logs`, `session_locks`, `macro_narrative_log` | `GET /api/v1/system/state` |
| **F2** | Trade History & Metrics | `campaign_logs` | `GET /api/v1/system/trades` |
| **F3** | Parameter Registry | `parameters`, `parameter_changes` | `GET /api/v1/system/parameters` |
| **F4** | Error & Alert Monitoring | `system_audit_log`, `monitor_event_log` | `GET /api/v1/system/errors` |
| **F5** | AI Analysis Query | `daily_audit_log`, `audit_suggestion_log` | `POST /api/v1/system/analysis` |
| **F6** | Human Dashboard Layer | Template files, static JS assets | `GET /suite/dashboard` |
| **F7** | AI Analysis Loop | `campaign_logs`, `audit_suggestion_log` | Background worker (scheduler) |

---

## 3. Comprehensive 4-Tier Test Case Registry (84 Cases)

### Tier 1: Feature Coverage (35 cases)

#### Feature 1: System State Snapshot (GET /api/v1/system/state)
1. **`test_t1_f1_state_success`**
   - **Pre-condition**: Admin user is logged in.
   - **Action**: `GET /api/v1/system/state`
   - **Expectation**: Status 200 OK. Response is valid JSON. Structure contains keys: `active_sessions`, `active_runners`, `scheduler_health`, `macro_engine`, `recent_errors`.
2. **`test_t1_f1_state_unauthorized`**
   - **Pre-condition**: Client has no session cookie.
   - **Action**: `GET /api/v1/system/state`
   - **Expectation**: Status 401 Unauthorized (or redirect to `/login`).
3. **`test_t1_f1_state_active_sessions_data`**
   - **Pre-condition**: Seed one `CampaignLog` with status `ACTIVE`, symbol `BTC/USDT`, bias `LONG`, entry `95000.0`.
   - **Action**: `GET /api/v1/system/state`
   - **Expectation**: Status 200. `active_sessions` list has length 1 with matching symbol, bias, and entry price.
4. **`test_t1_f1_state_shadow_runners_data`**
   - **Pre-condition**: Seed `CampaignLog` with `shadow_runner_active=True`, symbol `BTC/USDT`.
   - **Action**: `GET /api/v1/system/state`
   - **Expectation**: Status 200. `active_runners` list contains the active shadow runner trade details.
5. **`test_t1_f1_state_scheduler_health_list`**
   - **Pre-condition**: Schedulers have registered initial start times.
   - **Action**: `GET /api/v1/system/state`
   - **Expectation**: Status 200. `scheduler_health` has keys/status for all 6 scheduled tasks.

#### Feature 2: Trade History & Metrics (GET /api/v1/system/trades)
6. **`test_t1_f2_trades_success`**
   - **Pre-condition**: User logged in.
   - **Action**: `GET /api/v1/system/trades`
   - **Expectation**: Status 200 OK. JSON contains `trades` list and `metrics` object.
7. **`test_t1_f2_trades_unauthorized`**
   - **Pre-condition**: No session cookie.
   - **Action**: `GET /api/v1/system/trades`
   - **Expectation**: Status 401.
8. **`test_t1_f2_trades_window_7d`**
   - **Pre-condition**: Seed two canonical closed trades: one dated 2 days ago, one dated 10 days ago.
   - **Action**: `GET /api/v1/system/trades?window=7d`
   - **Expectation**: Status 200. `trades` list contains only the trade from 2 days ago.
9. **`test_t1_f2_trades_window_30d`**
   - **Pre-condition**: Seed trades dated 15 days ago and 45 days ago.
   - **Action**: `GET /api/v1/system/trades?window=30d`
   - **Expectation**: Status 200. Returns only the 15-day-old trade.
10. **`test_t1_f2_trades_metrics_winrate`**
    - **Pre-condition**: Seed 3 `CLOSED_WIN` and 1 `CLOSED_LOSS` canonical trades.
    - **Action**: `GET /api/v1/system/trades`
    - **Expectation**: Status 200. `metrics.win_rate` equals `75.0` and `metrics.approval_rate` equals `100.0`.

#### Feature 3: Parameter Registry (GET /api/v1/system/parameters)
11. **`test_t1_f3_parameters_success`**
    - **Pre-condition**: User logged in.
    - **Action**: `GET /api/v1/system/parameters`
    - **Expectation**: Status 200 OK. JSON contains `parameters` and `dependencies`.
12. **`test_t1_f3_parameters_unauthorized`**
    - **Pre-condition**: No session cookie.
    - **Action**: `GET /api/v1/system/parameters`
    - **Expectation**: Status 401.
13. **`test_t1_f3_parameters_fields`**
    - **Pre-condition**: Seed default parameters in the config table.
    - **Action**: `GET /api/v1/system/parameters`
    - **Expectation**: Status 200. Each parameter has `name`, `current_value`, `description`, `last_changed`.
14. **`test_t1_f3_parameters_dependency_map`**
    - **Pre-condition**: Default parameter dependencies set in registry.
    - **Action**: `GET /api/v1/system/parameters`
    - **Expectation**: Status 200. `dependencies` contains maps matching variables to their affected entry gates.
15. **`test_t1_f3_parameters_changelog`**
    - **Pre-condition**: Seed a historical parameter change in registry.
    - **Action**: `GET /api/v1/system/parameters`
    - **Expectation**: Status 200. Change history contains the reason for change and author.

#### Feature 4: Error & Alert Monitoring (GET /api/v1/system/errors)
16. **`test_t1_f4_errors_success`**
    - **Pre-condition**: User logged in.
    - **Action**: `GET /api/v1/system/errors`
    - **Expectation**: Status 200 OK. JSON contains `errors`, `alert_history`, `health_summary`.
17. **`test_t1_f4_errors_unauthorized`**
    - **Pre-condition**: No session cookie.
    - **Action**: `GET /api/v1/system/errors`
    - **Expectation**: Status 401.
18. **`test_t1_f4_errors_recent_logs`**
    - **Pre-condition**: Seed three system warning records in audit log.
    - **Action**: `GET /api/v1/system/errors`
    - **Expectation**: Status 200. `errors` list returns the warning logs.
19. **`test_t1_f4_errors_active_alerts`**
    - **Pre-condition**: Seed a critical alert with pending resolution.
    - **Action**: `GET /api/v1/system/errors`
    - **Expectation**: Status 200. `alert_history` lists the pending alert.
20. **`test_t1_f4_errors_uptime_data`**
    - **Pre-condition**: Server has been running.
    - **Action**: `GET /api/v1/system/errors`
    - **Expectation**: Status 200. `health_summary` contains a positive `uptime` float and `last_crash` status.

#### Feature 5: AI Analysis Endpoint (POST /api/v1/system/analysis)
21. **`test_t1_f5_analysis_success`**
    - **Pre-condition**: User logged in.
    - **Action**: `POST /api/v1/system/analysis` with body `{"query": "analyze PMARP values"}`.
    - **Expectation**: Status 200 OK. Returns `query`, `analysis_id`, and `report` JSON object.
22. **`test_t1_f5_analysis_unauthorized`**
    - **Pre-condition**: No session cookie.
    - **Action**: `POST /api/v1/system/analysis`
    - **Expectation**: Status 401.
23. **`test_t1_f5_analysis_validation`**
    - **Pre-condition**: User logged in.
    - **Action**: `POST /api/v1/system/analysis` with empty query `{}`.
    - **Expectation**: Status 422 Unprocessable Entity.
24. **`test_t1_f5_analysis_report_structure`**
    - **Pre-condition**: User logged in.
    - **Action**: `POST /api/v1/system/analysis` with valid query.
    - **Expectation**: Status 200. The returned `report` contains key findings and suggestion details.
25. **`test_t1_f5_analysis_db_logging`**
    - **Pre-condition**: User logged in.
    - **Action**: `POST /api/v1/system/analysis` with query.
    - **Expectation**: Check database directly after action; a record has been added to the analysis logs database table.

#### Feature 6: Upgraded Dashboard UI (GET /suite/dashboard)
26. **`test_t1_f6_dashboard_success`**
    - **Pre-condition**: User logged in.
    - **Action**: `GET /suite/dashboard`
    - **Expectation**: Status 200 OK. Returns HTML content.
27. **`test_t1_f6_dashboard_unauthorized`**
    - **Pre-condition**: No session cookie.
    - **Action**: `GET /suite/dashboard`
    - **Expectation**: Status 303 Redirect to `/login`.
28. **`test_t1_f6_dashboard_tabs_html`**
    - **Pre-condition**: User logged in.
    - **Action**: `GET /suite/dashboard`
    - **Expectation**: Status 200. HTML string contains tab selectors like `id="tab-overview"`, `id="tab-live"`, `id="tab-parameters"`, `id="tab-errors"`, `id="tab-analysis"`.
29. **`test_t1_f6_dashboard_assets`**
    - **Pre-condition**: User logged in.
    - **Action**: `GET /suite/dashboard`
    - **Expectation**: Status 200. HTML body contains links to static assets: `/static/css/dashboard.css` and JS scripts.
30. **`test_t1_f6_dashboard_empty_tables`**
    - **Pre-condition**: DB tables are empty. User logged in.
    - **Action**: `GET /suite/dashboard`
    - **Expectation**: Status 200 OK. Skeleton UI displays without crashing.

#### Feature 7: AI Analysis Loop (Background worker)
31. **`test_t1_f7_loop_initialization`**
    - **Pre-condition**: Server boots.
    - **Action**: Inspect active tasks list on application state.
    - **Expectation**: Loop task is successfully registered on lifespan context.
32. **`test_t1_f7_loop_reads_trades`**
    - **Pre-condition**: Seed canonical closed trades.
    - **Action**: Wait or trigger analysis loop iteration.
    - **Expectation**: Loop reads database trades without database locks.
33. **`test_t1_f7_loop_reads_parameters`**
    - **Pre-condition**: Seed parameters in registry.
    - **Action**: Trigger analysis loop iteration.
    - **Expectation**: Loop reads default thresholds.
34. **`test_t1_f7_loop_suggestions_creation`**
    - **Pre-condition**: Seed 30 failed trades with high BBWP values.
    - **Action**: Trigger analysis loop.
    - **Expectation**: Loop writes a suggestion to `AuditSuggestionLog` table.
35. **`test_t1_f7_loop_log_integrity`**
    - **Pre-condition**: Trigger loop.
    - **Action**: Inspect `AuditSuggestionLog` written record.
    - **Expectation**: `n_supporting` field is greater than or equal to 30, containing full suggestion text.

---

### Tier 2: Boundary & Corner Cases (35 cases)

#### Feature 1: System State Snapshot (GET /api/v1/system/state)
36. **`test_t2_f1_state_empty_tables`**
    - **Pre-condition**: Clean test database. Logged in.
    - **Action**: `GET /api/v1/system/state`
    - **Expectation**: Status 200. `active_sessions` and `active_runners` are empty arrays `[]`.
37. **`test_t2_f1_state_null_session_fields`**
    - **Pre-condition**: Seed `CampaignLog` with status `ACTIVE`, but `entry_price` and `stop_loss` are `None`.
    - **Action**: `GET /api/v1/system/state`
    - **Expectation**: Status 200 OK. Response maps null values safely in JSON without server crash.
38. **`test_t2_f1_state_extreme_shadow_pnl`**
    - **Pre-condition**: Seed `CampaignLog` shadow runner with `shadow_runner_blended_r = 99999.9` (extreme profit) and `shadow_runner_active = True`.
    - **Action**: `GET /api/v1/system/state`
    - **Expectation**: Status 200 OK. Value serializes without floating point errors or precision crashes.
39. **`test_t2_f1_state_null_scheduler_runs`**
    - **Pre-condition**: Schedulers have not run yet (database state empty).
    - **Action**: `GET /api/v1/system/state`
    - **Expectation**: Status 200 OK. Scheduler `last_run` returned as `null`.
40. **`test_t2_f1_state_recent_errors_cap`**
    - **Pre-condition**: Seed 60 errors in `SystemAuditLog`.
    - **Action**: `GET /api/v1/system/state`
    - **Expectation**: Status 200. `recent_errors` contains exactly the last 50 records.

#### Feature 2: Trade History & Metrics (GET /api/v1/system/trades)
41. **`test_t2_f2_trades_no_trades_metrics`**
    - **Pre-condition**: Empty trades database table.
    - **Action**: `GET /api/v1/system/trades`
    - **Expectation**: Status 200. `metrics.win_rate = 0.0`, `metrics.net_r = 0.0`, `metrics.approval_rate = 0.0`.
42. **`test_t2_f2_trades_division_by_zero_guard`**
    - **Pre-condition**: Seed 0 wins and 0 losses in `CampaignLog`.
    - **Action**: `GET /api/v1/system/trades`
    - **Expectation**: Status 200 OK. Win rate returns `0.0` rather than throwing a division by zero error.
43. **`test_t2_f2_trades_invalid_window`**
    - **Pre-condition**: Seed canonical trades.
    - **Action**: `GET /api/v1/system/trades?window=invalid_text`
    - **Expectation**: Status 400 Bad Request or fallback to "all" window configuration safely.
44. **`test_t2_f2_trades_extreme_negative_r`**
    - **Pre-condition**: Seed trade with `realized_pnl = -100.5` (large slippage loss).
    - **Action**: `GET /api/v1/system/trades`
    - **Expectation**: Status 200. Net R displays `-100.5`.
45. **`test_t2_f2_trades_null_status_filtration`**
    - **Pre-condition**: Seed trade with status `None` or an empty string.
    - **Action**: `GET /api/v1/system/trades`
    - **Expectation**: Status 200. Returns trades list, but the malformed status trade is omitted from metrics count.

#### Feature 3: Parameter Registry (GET /api/v1/system/parameters)
46. **`test_t2_f3_parameters_special_char_values`**
    - **Pre-condition**: Seed parameter with description containing quotes, HTML tags, and emojis.
    - **Action**: `GET /api/v1/system/parameters`
    - **Expectation**: Status 200. JSON string matches seeded value exactly, showing proper character escaping.
47. **`test_t2_f3_parameters_null_changelog_date`**
    - **Pre-condition**: Seed parameter change log with `updated_at = None`.
    - **Action**: `GET /api/v1/system/parameters`
    - **Expectation**: Status 200. Handles null date gracefully, rendering `null` in the JSON response.
48. **`test_t2_f3_parameters_cyclic_dependency`**
    - **Pre-condition**: Seed dependencies where `A` depends on `B` and `B` depends on `A`.
    - **Action**: `GET /api/v1/system/parameters`
    - **Expectation**: Status 200. Graph serializes correctly as lists of dependencies without causing stack overflow recursion.
49. **`test_t2_f3_parameters_empty_descriptions`**
    - **Pre-condition**: Seed parameters where `description` is NULL.
    - **Action**: `GET /api/v1/system/parameters`
    - **Expectation**: Status 200. Returns parameters successfully with description field set to `null` or empty string.
50. **`test_t2_f3_parameters_duplicate_keys`**
    - **Pre-condition**: Seed two database rows containing the same parameter name.
    - **Action**: `GET /api/v1/system/parameters`
    - **Expectation**: Status 200. Returns unique parameter list, using the latest entry or raising a clear data integrity warning in audit logs.

#### Feature 4: Error & Alert Monitoring (GET /api/v1/system/errors)
51. **`test_t2_f4_errors_empty_logs`**
    - **Pre-condition**: Empty system audit tables.
    - **Action**: `GET /api/v1/system/errors`
    - **Expectation**: Status 200 OK. Returns empty arrays `[]` for errors and alert history.
52. **`test_t2_f4_errors_missing_severity`**
    - **Pre-condition**: Seed audit log with NULL severity.
    - **Action**: `GET /api/v1/system/errors`
    - **Expectation**: Status 200. Record returns with severity set to a fallback string like `INFO` or `UNKNOWN`.
53. **`test_t2_f4_errors_uptime_drift`**
    - **Pre-condition**: Mock system clock to simulate negative time drift (server clock goes backwards).
    - **Action**: `GET /api/v1/system/errors`
    - **Expectation**: Status 200. Health summary uptime is capped at `0.0` instead of showing a negative value.
54. **`test_t2_f4_errors_unresolved_alerts`**
    - **Pre-condition**: Seed alert with NULL resolution status and NULL resolved_at date.
    - **Action**: `GET /api/v1/system/errors`
    - **Expectation**: Status 200. Returns the alert in the list with `is_resolved = false`.
55. **`test_t2_f4_errors_extreme_alerts_volume`**
    - **Pre-condition**: Seed 5,000 alerts in the DB.
    - **Action**: `GET /api/v1/system/errors`
    - **Expectation**: Status 200. Response completes quickly, capped at the most recent 100 alerts to prevent out-of-memory errors.

#### Feature 5: AI Analysis Endpoint (POST /api/v1/system/analysis)
56. **`test_t2_f5_analysis_empty_query`**
    - **Pre-condition**: Logged in.
    - **Action**: `POST /api/v1/system/analysis` with `{"query": ""}`.
    - **Expectation**: Status 400 Bad Request or validation error.
57. **`test_t2_f5_analysis_excessive_payload`**
    - **Pre-condition**: Logged in.
    - **Action**: `POST /api/v1/system/analysis` with a 2MB query string.
    - **Expectation**: Status 413 Payload Too Large (or 400 Bad Request depending on standard FastAPI size limits).
58. **`test_t2_f5_analysis_malformed_json_body`**
    - **Pre-condition**: Logged in.
    - **Action**: `POST /api/v1/system/analysis` with invalid JSON raw text.
    - **Expectation**: Status 400 Bad Request.
59. **`test_t2_f5_analysis_sql_injection`**
    - **Pre-condition**: Logged in.
    - **Action**: `POST /api/v1/system/analysis` with query containing `' OR 1=1; --`.
    - **Expectation**: Status 200 OK. Text is processed purely as a string query parameter; no SQL injection is executed.
60. **`test_t2_f5_analysis_openai_fail`**
    - **Pre-condition**: Mock AI completion engine to raise a connection timeout exception.
    - **Action**: `POST /api/v1/system/analysis` with valid query.
    - **Expectation**: Status 502 Bad Gateway (or 500 with a structured JSON error body outlining the service timeout).

#### Feature 6: Upgraded Dashboard UI (GET /suite/dashboard)
61. **`test_t2_f6_dashboard_invalid_timezone`**
    - **Pre-condition**: Set user model timezone in DB to `America/Non_Existent_City`.
    - **Action**: `GET /suite/dashboard`
    - **Expectation**: Status 200. Dashboard falls back to rendering times in `UTC` and page loads successfully.
62. **`test_t2_f6_dashboard_null_user_fields`**
    - **Pre-condition**: User has email but `username` is `None` in the database.
    - **Action**: `GET /suite/dashboard`
    - **Expectation**: Status 200. Dashboard displays email address or "Operative" as fallback name in the header.
63. **`test_t2_f6_dashboard_missing_component_fallback`**
    - **Pre-condition**: Simulate a missing sub-panel template file.
    - **Action**: `GET /suite/dashboard`
    - **Expectation**: Status 500 Internal Server Error. The exception handler catches the template error and returns a clean autopsy HTML instead of a blank white page.
64. **`test_t2_f6_dashboard_xss_protection`**
    - **Pre-condition**: Set user username to `<script>alert("hack")</script>`.
    - **Action**: `GET /suite/dashboard`
    - **Expectation**: Status 200. HTML response has escaped the script tag, displaying it as text: `&lt;script&gt;`.
65. **`test_t2_f6_dashboard_security_headers`**
    - **Pre-condition**: Request dashboard page.
    - **Action**: Inspect HTTP response headers.
    - **Expectation**: `X-Frame-Options` is set to `DENY` or `SAMEORIGIN`, and the session cookie uses `HttpOnly` and `SameSite=Lax`.

#### Feature 7: AI Analysis Loop (Background worker)
66. **`test_t2_f7_loop_null_realized_pnl`**
    - **Pre-condition**: Seed resolved trades but with `realized_pnl = None` in the database.
    - **Action**: Trigger analysis loop execution.
    - **Expectation**: Analysis completes successfully; null pnl trades are safely skipped from statistics calculations.
67. **`test_t2_f7_loop_insufficient_sample_size`**
    - **Pre-condition**: Only 5 trades are in the database.
    - **Action**: Trigger analysis loop.
    - **Expectation**: Loop completes without action; does not generate suggestions in `AuditSuggestionLog` because N is below the threshold of 30.
68. **`test_t2_f7_loop_broken_llm_chain`**
    - **Pre-condition**: Mock LLM generation service to return garbage text.
    - **Action**: Trigger analysis loop.
    - **Expectation**: The loop catches parser exceptions internally, increments `missed_runs` or logs warnings in `SystemAuditLog`, and continues running without crashing the main application thread.
69. **`test_t2_f7_loop_concurrent_invocations`**
    - **Pre-condition**: Start two analysis loops in parallel.
    - **Action**: Observe database access logs.
    - **Expectation**: Transaction locks prevent dirty writes; second scheduler call exits gracefully.
70. **`test_t2_f7_loop_stale_parameters_cleanup`**
    - **Pre-condition**: Seed parameters table with outdated/inactive variables.
    - **Action**: Run analysis loop.
    - **Expectation**: Inactive parameters are ignored in the evaluations.

---

### Tier 3: Cross-Feature Combinations (7 cases)

71. **`test_t3_trade_outcome_tracker_propagates_to_errors`**
    - **Concept**: Verifies database write outcomes update system health logs.
    - **Setup**: Seed trade in `CampaignLog` with status `ACTIVE`.
    - **Action**: Trigger outcome tracker tick (mocking price hitting `STOP` price, trade status becomes `CLOSED_LOSS`).
    - **Expectation**: A new record is added to `SystemAuditLog` documenting the trade exit. `GET /api/v1/system/errors` shows the updated error log, and `GET /api/v1/system/state` no longer lists the trade in active sessions.
72. **`test_t3_parameter_changes_affect_ai_loop_calculations`**
    - **Concept**: Parameter updates alter AI evaluations immediately.
    - **Setup**: Update BBWP threshold parameter in `parameters` table from 85 to 90.
    - **Action**: Run the AI Analysis Loop.
    - **Expectation**: The loop evaluates the historical trade metrics using the new 90 threshold value, resulting in updated counterfactual counts in `AuditSuggestionLog`.
73. **`test_t3_analysis_api_call_renders_on_dashboard_analysis_tab`**
    - **Concept**: User actions in the API reflect in the dashboard.
    - **Setup**: Make an authenticated call to `POST /api/v1/system/analysis` to run custom analysis.
    - **Action**: Request the dashboard HTML via `GET /suite/dashboard`.
    - **Expectation**: The page returns 200. Inspecting the JSON data loaded for the "Analysis" tab shows the generated analysis ID and report summary.
74. **`test_t3_scheduler_run_updates_state_narrative`**
    - **Concept**: Scheduled workers update the system state snapshot.
    - **Setup**: Trigger the Senior Analyst scheduler to generate a daily narrative.
    - **Action**: Query `GET /api/v1/system/state`.
    - **Expectation**: The `macro_engine` section returns the timestamp of the run, and the recent narrative matches the output written by the Senior Analyst.
75. **`test_t3_suggestion_approval_modifies_parameter_registry`**
    - **Concept**: AI analysis recommendations close the loop to parameter tuning.
    - **Setup**: Seed an open entry in `AuditSuggestionLog` with a suggestion to reduce sizing risk to 1.5%.
    - **Action**: Send `POST /admin/apply-suggestion` (mocking an admin clicking "Approve" on the dashboard UI).
    - **Expectation**: Sizing risk parameter in `parameters` table updates to `1.5%`, and the change log captures the source Suggestion ID.
76. **`test_t3_active_sessions_resolve_and_recalculate_metrics`**
    - **Concept**: Active trades transition to trade history, updating metrics.
    - **Setup**: `GET /api/v1/system/state` lists 1 active session. `GET /api/v1/system/trades` win rate is `0.0`.
    - **Action**: Resolve the trade as `CLOSED_WIN`. Query both endpoints again.
    - **Expectation**: `/state` active sessions is now empty. `/trades` shows the trade in history and win rate updates to `100.0`.
77. **`test_t3_consecutive_scheduler_errors_triggers_stand_down`**
    - **Concept**: System errors feed back into execution safety.
    - **Setup**: Trigger 5 consecutive task failures in the background scheduler.
    - **Action**: Query `GET /api/v1/system/state`.
    - **Expectation**: Uptime health summary shows warning/degraded status, and the system automatically transitions active shadow runners to stand down status (`shadow_runner_active=False` and exit reason `SYSTEM_HEALTH_DEGRADED`).
77b. **`test_t3_session_lock_creation_updates_state`**
    - **Concept**: Live database state changes affect state API outputs.
    - **Setup**: Insert a new `SessionLock` row in the database representing today's session.
    - **Action**: Query `GET /api/v1/system/state`.
    - **Expectation**: The active sessions list is updated instantly to display the locked session levels and anchor price.

---

### Tier 4: Real-World Application Scenarios (5 cases)

78. **`test_t4_admin_user_lifecycle_and_audit`**
    - **Sequence**:
      1. Login to the system as admin (`POST /login` with admin credentials).
      2. Call `POST /admin/create-user` to create a basic operator account.
      3. Verify the user appears in the database roster.
      4. Call `POST /admin/delete-user` to revoke access for that operator.
      5. Request `GET /api/v1/system/errors` and verify that the creation and deletion events are logged in the audit trail with timestamps.
    - **Objective**: Validates user access provisioning and security audit tracking.
79. **`test_t4_trade_lifecycle_and_live_dashboard_updates`**
    - **Sequence**:
      1. Log in and request `/suite/dashboard`.
      2. Seed an active session lock (`SessionLock`) and a corresponding trade setup (`CampaignLog`) in the database.
      3. Query `GET /api/v1/system/state` and verify the trade appears in "Active Sessions".
      4. Simulate price crossing the target `T1` price. The outcome tracker runs and closes the trade as `CLOSED_WIN`.
      5. Query `GET /api/v1/system/trades` and verify that `metrics.win_rate` and `metrics.net_r` are updated.
      6. Reload `/suite/dashboard` Overview tab and check that charts and KPI cards reflect the completed trade.
    - **Objective**: Asserts correct end-to-end data flow from active execution to resolution to UI representation.
80. **`test_t4_automated_parameter_tuning_cycle`**
    - **Sequence**:
      1. Seed 45 historical trades in `CampaignLog` where the entry was triggered with BBWP > 85%, resulting in 36 losses.
      2. Run the AI Analysis Loop background task.
      3. Verify the engine detects the pattern, runs a counterfactual sweep, and writes a suggestion to `AuditSuggestionLog` to raise the threshold to 90%.
      4. Log in as admin, visit `/suite/dashboard` Analysis tab, and fetch suggestions.
      5. Approve the recommendation.
      6. Query `GET /api/v1/system/parameters` and verify the current value of the BBWP threshold is updated to 90 with a change log reason.
    - **Objective**: Tests the full closed-loop audit cycle: data collection -> pattern detection -> suggestion -> approval -> parameter update.
81. **`test_t4_system_crash_recovery_telemetry`**
    - **Sequence**:
      1. Force a fatal database read error (mocking database lock) in a background thread.
      2. The system fails and restarts.
      3. Request `GET /api/v1/system/errors` to verify the fatal crash was captured in the permanent error log.
      4. Request `GET /api/v1/system/state` to verify that all background scheduler threads restarted successfully, are currently executing, and show no missed cycles.
    - **Objective**: Validates the application's self-healing capabilities and ensures crash forensic logs are preserved.
82. **`test_t4_cross_timeframe_narrative_continuity`**
    - **Sequence**:
      1. Trigger the weekly Elliott Wave Specialist scheduler (writes EW wave labels to `MacroNarrativeLog`).
      2. Trigger the daily Senior Analyst scheduler (reads last week's EW structures and writes the daily market brief).
      3. Request `GET /api/v1/system/state` and verify the macro narrative payload returns both the EW wave count and the daily tactical narrative.
      4. Verify that `/suite/dashboard` loads and displays the narrative text in the "Live System" tab.
    - **Objective**: Confirms Elliott Wave analysis and daily brief generation synchronize and propagate correctly to the public state APIs and dashboard layers.
82b. **`test_t4_lti_confluence_checkpoints`**
    - **Sequence**:
      1. Trigger the monthly KULTI LTI scheduler.
      2. Verify that the engine reads the weekly narrative and writes an LtiCheckpoint row and an InterpreterLog row.
      3. Query `GET /api/v1/system/state` and verify that the monthly scheduler status is updated.
      4. Verify that the dashboard LTI data is loaded, and check that once written, the checkpoint row cannot be altered.
    - **Objective**: Asserts that long-term advisory checkpoints generate audit logs correctly on their monthly boundary.
