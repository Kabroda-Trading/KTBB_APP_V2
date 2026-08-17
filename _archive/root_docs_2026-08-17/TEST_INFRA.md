# Testing Infrastructure for Kabroda Diagnostic Command Center

This document outlines the testing philosophy, architecture, feature inventory, and application scenarios for the Kabroda Diagnostic Command Center E2E testing framework.

---

## 1. Testing Philosophy

The test suite uses an **opaque-box End-to-End (E2E) testing** philosophy. It simulates real client interactions (both programmatic API queries and human web dashboard interactions) by making HTTP requests to a running FastAPI server instance.

To achieve complete reliability, speed, and safety, we adhere to the following principles:

1. **State Isolation**: Every test run operates on a dedicated database state using an isolated database URL (`sqlite:///./kabroda_test.db`). The database schema is initialized from scratch using `init_db()` and is seeded with fresh records, then completely deleted on teardown. No tests leak state to or from the production database.
2. **Lifespan Bypassing**: To avoid starting real background workers, cron loops, price feeds, and scheduler loops (which would block the test suite, create threads, or perform real network requests), the application's lifespan context manager is dynamically bypassed at test startup. This is done by replacing `app.router.lifespan_context` with a dummy async context manager.
3. **Session and Privilege Isolation**: Rather than relying on a single test client, the test suite instantiates three separate `FastAPI TestClient` instances representing different user roles:
   - **Admin Client**: Fully authenticated admin account. Has read/write access to all state, parameter, analysis, and error endpoints.
   - **Basic Client**: Authenticated non-admin account. Permitted to load the dashboard web UI but blocked from programmatic administrative system endpoints.
   - **Anonymous Client**: Unauthenticated client. Blocked from all secure endpoints and redirected back to the login page.
4. **No Cheating**: All assertions reflect the actual JSON schemas and HTML responses expected from the fully upgraded system, without hardcoding outcomes or using dummy facades.

---

## 2. Feature Inventory

The test harness evaluates the Kabroda Diagnostic Command Center across seven core features (**F1–F7**):

- **F1: System State API (`/api/v1/system/state`)**
  - Programmatic endpoint returning live active sessions, active runners, scheduler health state, macro engine telemetry, and recent errors.
- **F2: Trade History & Metrics API (`/api/v1/system/trades`)**
  - Endpoint exposing the history of closed and expired sessions with aggregate calculations (`win_rate`, `net_r`, `approval_rate`), supporting filtering windows (`7d`, `30d`, `all`).
- **F3: Parameter Registry API (`/api/v1/system/parameters`)**
  - Registry of system configuration parameters, source systems (e.g., gravity, MAS), descriptions, last updated timestamps, and inter-parameter dependencies.
- **F4: Error Registry API (`/api/v1/system/errors`)**
  - Centralized log of system errors, stack traces, resolution flags, health alerts history, and health summary indicators.
- **F5: AI Analysis API (`/api/v1/system/analysis`)**
  - Programmatic AI agent endpoint accepting custom diagnostic queries and returning recommendations, structured findings, and analysis IDs.
- **F6: Upgraded Dashboard UI (`/suite/dashboard`)**
  - Human web dashboard containing tabbed telemetry views (Overview, Live System, Parameters, Errors, Analysis) dynamically consuming the API layer.
- **F7: AI Analysis Loop Background Worker**
  - Periodic background evaluator analyzing trade performance and parameters registry, writing suggestion logs to the database, and exposing status in state telemetry.

---

## 3. Test Architecture

The testing architecture is built entirely on the Python standard `unittest` framework, utilizing the `FastAPI TestClient` in-process runner.

```
                  +----------------------------------+
                  |       python -m unittest         |
                  +-----------------+----------------+
                                    |
                                    v
                  +-----------------+----------------+
                  |        tests/test_e2e.py         |
                  +--------+--------+--------+-------+
                           |        |        |
         +-----------------+        |        +-----------------+
         |                          |                          |
         v                          v                          v
+-----------------+        +-----------------+        +-----------------+
|   Admin Client  |        |   Basic Client  |        | Anonymous Client|
|  (Auth Session) |        |  (Auth Session) |        |  (No Session)   |
+--------+--------+        +--------+--------+        +--------+--------+
         |                          |                          |
         +------------------+       |       +------------------+
                            |       |       |
                            v       v       v
                  +-----------------+----------------+
                  |         FastAPI App Instance     |
                  |     (main.py / router / auth)    |
                  +-----------------+----------------+
                                    |
            [Bypasses Lifespan]     | [Uses SQLite Isolation]
            app.router.lifespan_    v
            context = dummy_        sqlite:///./kabroda_test.db
                                    (init_db / seeded / torn down)
```

### Key Techniques
- **Lifespan Bypassing**:
  ```python
  @asynccontextmanager
  async def dummy_lifespan(app_instance):
      yield
  app.router.lifespan_context = dummy_lifespan
  ```
- **Login Session Capture**:
  Using `TestClient` form posts:
  ```python
  client.post("/login", data={"email": email, "password": password})
  ```
  The Starlette session middleware sets signed cookies, which are stored automatically within the client instance session.

---

## 4. Application Scenarios (Tiers 1-4)

The test cases are organized into four execution tiers representing progressively deeper integration and complexity:

1. **Tier 1: Feature Coverage (35 test cases)**: Five happy-path test cases for each of the seven features (F1-F7). Verifies basic status codes, JSON payload schemas, filter query parameters, and HTML text renderings.
2. **Tier 2: Boundary & Corner Cases (35 test cases)**: Five tests per feature assessing unauthenticated blocks, privilege restrictions (basic user denials on admin API endpoints), empty database fallbacks, malformed query params, and excessive log truncation.
3. **Tier 3: Cross-Feature Combinations (8 test cases)**: Tests dynamic interactions across features:
   - Creating an error updates both the state telemetry and error registry.
   - Closed trades dynamically recalculate performance metrics and update dashboard elements.
   - Admin creating a user registers a new session credential that can login.
   - Logout clears credentials and blocks access.
4. **Tier 4: Real-World Application Scenarios (5 test cases)**: Multi-step E2E scenario workflows:
   - **Admin Audit Flow**: Login -> Check State -> Check Errors -> Query AI Analysis -> Check Dashboard.
   - **Trade Lifecycle Flow**: Seed Setup -> Fill & Exit Win -> Check Metrics Recalculation -> Query AI diagnostic on results.
   - **Parameter Tuning Flow**: Fetch active parameters -> Update database configuration -> Verify state changes -> Check dashboard.
   - **User Onboarding and Containment**: Admin creates basic user -> User logs in -> Blocked from State API -> Allowed on Dashboard UI.
   - **Scheduler Failure Alerting**: Simulate loop crash -> Verify state telemetry flag -> Verify error logs -> Verify dashboard rendering alert headers.
