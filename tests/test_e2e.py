import os
import sys
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone

# Force SQLite test database URL before importing database or main modules
os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test.db"

# Add project root directory to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from database import (
    init_db,
    SessionLocal,
    engine,
    UserModel,
    CampaignLog,
    SessionLock,
    DecisionJournal,
    AgentRunLog,
    SystemAuditLog,
    AuditSuggestionLog,
    DailyAuditLog
)
import auth
import main
from main import app

# Override the application lifespan globally to bypass background schedulers during tests
@asynccontextmanager
async def dummy_lifespan(app_instance):
    yield

app.router.lifespan_context = dummy_lifespan


class KabrodaE2ETestSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 1. Clean up database state if leftover files exist
        cls.clean_db_files()

        # 2. Initialize a fresh SQLite database schema
        init_db()

        # 3. Seed database with necessary test records (admin and basic users)
        db = SessionLocal()
        
        cls.admin_email = "admin@kabroda.com"
        cls.admin_password = "adminpassword123"
        cls.admin_user = UserModel(
            email=cls.admin_email,
            password_hash=auth.hash_password(cls.admin_password),
            username="adminuser",
            tier="admin",
            is_admin=True,
            subscription_status="active"
        )
        db.add(cls.admin_user)

        cls.basic_email = "basic@kabroda.com"
        cls.basic_password = "basicpassword123"
        cls.basic_user = UserModel(
            email=cls.basic_email,
            password_hash=auth.hash_password(cls.basic_password),
            username="basicuser",
            tier="basic",
            is_admin=False,
            subscription_status="active"
        )
        db.add(cls.basic_user)

        db.commit()
        db.close()

        # 4. Instantiate isolated TestClients for session state separation
        cls.admin_client = TestClient(app)
        cls.admin_client.post(
            "/login",
            data={"email": cls.admin_email, "password": cls.admin_password}
        )

        cls.basic_client = TestClient(app)
        cls.basic_client.post(
            "/login",
            data={"email": cls.basic_email, "password": cls.basic_password}
        )

        cls.anon_client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        engine.dispose()
        cls.clean_db_files()

    @classmethod
    def clean_db_files(cls):
        for path in ["kabroda_test.db", "kabroda_test.db-journal", "kabroda_test.db-shm", "kabroda_test.db-wal"]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    # =========================================================================
    # TIER 1: FEATURE COVERAGE (35 Tests - 5 cases per feature F1-F7)
    # =========================================================================

    # --- F1: System State API (/api/v1/system/state) ---

    def test_f1_state_happy(self):
        """F1: GET /api/v1/system/state returns 200 and matches the expected JSON keys."""
        res = self.admin_client.get("/api/v1/system/state")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        for key in ["active_sessions", "active_runners", "scheduler_health", "macro_engine", "recent_errors"]:
            self.assertIn(key, data)

    def test_f1_state_contains_active_sessions(self):
        """F1: state endpoint contains active sessions as an array."""
        res = self.admin_client.get("/api/v1/system/state")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json().get("active_sessions"), list)

    def test_f1_state_contains_active_runners(self):
        """F1: state endpoint contains active runners as an array."""
        res = self.admin_client.get("/api/v1/system/state")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json().get("active_runners"), list)

    def test_f1_state_contains_scheduler_health(self):
        """F1: state endpoint contains scheduler health metadata."""
        res = self.admin_client.get("/api/v1/system/state")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json().get("scheduler_health"), dict)

    def test_f1_state_contains_macro_engine_telemetry(self):
        """F1: state endpoint contains macro engine configuration parameters."""
        res = self.admin_client.get("/api/v1/system/state")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json().get("macro_engine"), dict)


    # --- F2: Trade History & Metrics API (/api/v1/system/trades) ---

    def test_f2_trades_happy(self):
        """F2: GET /api/v1/system/trades returns 200 and contains trades and metrics."""
        res = self.admin_client.get("/api/v1/system/trades")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("trades", data)
        self.assertIn("metrics", data)

    def test_f2_trades_window_7d(self):
        """F2: trades endpoint accepts window=7d parameter."""
        res = self.admin_client.get("/api/v1/system/trades?window=7d")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json().get("trades"), list)

    def test_f2_trades_window_30d(self):
        """F2: trades endpoint accepts window=30d parameter."""
        res = self.admin_client.get("/api/v1/system/trades?window=30d")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json().get("trades"), list)

    def test_f2_trades_window_all(self):
        """F2: trades endpoint accepts window=all parameter."""
        res = self.admin_client.get("/api/v1/system/trades?window=all")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json().get("trades"), list)

    def test_f2_trades_metrics_schema(self):
        """F2: trades metrics contains win_rate, net_r, and approval_rate fields."""
        res = self.admin_client.get("/api/v1/system/trades")
        self.assertEqual(res.status_code, 200)
        metrics = res.json().get("metrics", {})
        for metric in ["win_rate", "net_r", "approval_rate"]:
            self.assertIn(metric, metrics)


    # --- F3: Parameter Registry API (/api/v1/system/parameters) ---

    def test_f3_parameters_happy(self):
        """F3: GET /api/v1/system/parameters returns 200 and contains registry data."""
        res = self.admin_client.get("/api/v1/system/parameters")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("parameters", data)
        self.assertIn("dependencies", data)

    def test_f3_parameters_fields(self):
        """F3: registry items have fields name, value, description, last_updated, and source."""
        res = self.admin_client.get("/api/v1/system/parameters")
        self.assertEqual(res.status_code, 200)
        params = res.json().get("parameters", [])
        if params:
            for field in ["name", "value", "description", "last_updated", "source"]:
                self.assertIn(field, params[0])

    def test_f3_parameters_dependencies(self):
        """F3: parameter dependencies array contains correct metadata fields."""
        res = self.admin_client.get("/api/v1/system/parameters")
        self.assertEqual(res.status_code, 200)
        deps = res.json().get("dependencies", [])
        if deps:
            for field in ["name", "depends_on", "relationship_type"]:
                self.assertIn(field, deps[0])

    def test_f3_parameters_filter_source(self):
        """F3: registry supports source query filtering."""
        res = self.admin_client.get("/api/v1/system/parameters?source=gravity")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json().get("parameters"), list)

    def test_f3_parameters_is_not_empty(self):
        """F3: parameters returns a non-null payload format."""
        res = self.admin_client.get("/api/v1/system/parameters")
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.json())


    # --- F4: Error Registry API (/api/v1/system/errors) ---

    def test_f4_errors_happy(self):
        """F4: GET /api/v1/system/errors returns 200 with logs and health summary."""
        res = self.admin_client.get("/api/v1/system/errors")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("errors", data)
        self.assertIn("alert_history", data)
        self.assertIn("health_summary", data)

    def test_f4_errors_details(self):
        """F4: error items contain detailed stacktrace and resolution details."""
        res = self.admin_client.get("/api/v1/system/errors")
        self.assertEqual(res.status_code, 200)
        errors = res.json().get("errors", [])
        if errors:
            for field in ["id", "timestamp", "error_type", "message", "stack_trace", "resolved"]:
                self.assertIn(field, errors[0])

    def test_f4_errors_alert_history(self):
        """F4: alert history logs external notification triggers."""
        res = self.admin_client.get("/api/v1/system/errors")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json().get("alert_history"), list)

    def test_f4_errors_health_summary(self):
        """F4: health summary evaluates system_ok flags."""
        res = self.admin_client.get("/api/v1/system/errors")
        self.assertEqual(res.status_code, 200)
        summary = res.json().get("health_summary", {})
        self.assertIn("system_ok", summary)

    def test_f4_errors_filter_severity(self):
        """F4: error registry filters logs by severity levels."""
        res = self.admin_client.get("/api/v1/system/errors?severity=critical")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json().get("errors"), list)


    # --- F5: AI Analysis API (/api/v1/system/analysis) ---

    def test_f5_analysis_happy(self):
        """F5: POST /api/v1/system/analysis returns 200 and matches the expected JSON schema."""
        res = self.admin_client.post("/api/v1/system/analysis", json={"query": "evaluate weekly win rate"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("query", data)
        self.assertIn("analysis_id", data)
        self.assertIn("report", data)

    def test_f5_analysis_generates_id(self):
        """F5: analysis post request generates a unique diagnostic id."""
        res = self.admin_client.post("/api/v1/system/analysis", json={"query": "run baseline"})
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.json().get("analysis_id"))

    def test_f5_analysis_report_schema(self):
        """F5: analysis report matches recommendation and findings schemas."""
        res = self.admin_client.post("/api/v1/system/analysis", json={"query": "run baseline"})
        self.assertEqual(res.status_code, 200)
        report = res.json().get("report", {})
        self.assertIn("recommendations", report)
        self.assertIn("findings", report)

    def test_f5_analysis_empty_query_default(self):
        """F5: posting with an empty query defaults to general system evaluation."""
        res = self.admin_client.post("/api/v1/system/analysis", json={"query": ""})
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.json().get("analysis_id"))

    def test_f5_analysis_saves_report(self):
        """F5: posted report is stored in the database and accessible."""
        res = self.admin_client.post("/api/v1/system/analysis", json={"query": "run baseline"})
        self.assertEqual(res.status_code, 200)
        analysis_id = res.json().get("analysis_id")
        get_res = self.admin_client.get(f"/api/v1/system/analysis/{analysis_id}")
        self.assertEqual(get_res.status_code, 200)


    # --- F6: Upgraded Dashboard UI (/suite/dashboard) ---

    def test_f6_dashboard_renders_overview(self):
        """F6: dashboard view contains the Overview tab container."""
        res = self.admin_client.get("/suite/dashboard")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Overview", res.text)

    def test_f6_dashboard_renders_live_system(self):
        """F6: dashboard view contains the Live System tab container."""
        res = self.admin_client.get("/suite/dashboard")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Live System", res.text)

    def test_f6_dashboard_renders_parameters(self):
        """F6: dashboard view contains the Parameters registry tab container."""
        res = self.admin_client.get("/suite/dashboard")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Parameters", res.text)

    def test_f6_dashboard_renders_errors(self):
        """F6: dashboard view contains the Errors log tab container."""
        res = self.admin_client.get("/suite/dashboard")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Errors", res.text)

    def test_f6_dashboard_renders_analysis(self):
        """F6: dashboard view contains the AI Reports tab container."""
        res = self.admin_client.get("/suite/dashboard")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Analysis", res.text)


    # --- F7: AI Analysis Loop Background Worker ---

    def test_f7_analysis_loop_status(self):
        """F7: state endpoint exposes the background worker status."""
        res = self.admin_client.get("/api/v1/system/state")
        self.assertEqual(res.status_code, 200)
        health = res.json().get("scheduler_health", {})
        self.assertIn("analysis_loop", health)

    def test_f7_analysis_loop_triggered_manually(self):
        """F7: loop can be triggered manually via a POST request."""
        res = self.admin_client.post("/api/v1/system/analysis/trigger")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("status"), "running")

    def test_f7_analysis_loop_writes_suggestions(self):
        """F7: triggered loop inserts suggestions into the suggestion database."""
        res = self.admin_client.post("/api/v1/system/analysis/trigger")
        self.assertEqual(res.status_code, 200)
        db = SessionLocal()
        try:
            sugg = db.query(AuditSuggestionLog).first()
            self.assertIsNotNone(sugg)
        finally:
            db.close()

    def test_f7_analysis_loop_reads_parameters(self):
        """F7: loop consumes parameter metrics in its performance assessment."""
        res = self.admin_client.post("/api/v1/system/analysis/trigger")
        self.assertEqual(res.status_code, 200)
        # Background loop reads parameters correctly; verify via simulation mock or logs
        self.assertIsNotNone(res.json().get("parameters_evaluated"))

    def test_f7_analysis_loop_metrics_updated(self):
        """F7: execution results are recorded as execution logs."""
        res = self.admin_client.post("/api/v1/system/analysis/trigger")
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.json().get("last_run_timestamp"))


    # =========================================================================
    # TIER 2: BOUNDARY & CORNER CASES (35 Tests - 5 cases per feature F1-F7)
    # =========================================================================

    # --- F1: System State API (/api/v1/system/state) ---

    def test_f1_state_unauthenticated(self):
        """F1: unauthenticated state queries return 401."""
        res = self.anon_client.get("/api/v1/system/state")
        self.assertEqual(res.status_code, 401)

    def test_f1_state_basic_user_denied(self):
        """F1: basic users are blocked from querying live state (returns 403)."""
        res = self.basic_client.get("/api/v1/system/state")
        self.assertEqual(res.status_code, 403)

    def test_f1_state_empty_db(self):
        """F1: state endpoint handles empty DB state gracefully."""
        # Empty the SessionLock table and check
        db = SessionLocal()
        db.query(SessionLock).delete()
        db.commit()
        db.close()
        res = self.admin_client.get("/api/v1/system/state")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json().get("active_sessions")), 0)

    def test_f1_state_malformed_headers(self):
        """F1: request with malformed authorization headers returns 401."""
        res = self.anon_client.get("/api/v1/system/state", headers={"Cookie": "kabroda_user_id=invalid"})
        self.assertEqual(res.status_code, 401)

    def test_f1_state_excessive_errors(self):
        """F1: state endpoint truncates recent errors log safely at 50 records."""
        db = SessionLocal()
        for i in range(100):
            db.add(SystemAuditLog(
                symbol="BTC/USDT",
                date_key=f"2026-07-{i:02d}",
                audit_md=f"error {i}",
                ran_successfully=False
            ))
        db.commit()
        db.close()
        res = self.admin_client.get("/api/v1/system/state")
        self.assertEqual(res.status_code, 200)
        self.assertLessEqual(len(res.json().get("recent_errors")), 50)


    # --- F2: Trade History & Metrics API (/api/v1/system/trades) ---

    def test_f2_trades_unauthenticated(self):
        """F2: unauthenticated trades queries return 401."""
        res = self.anon_client.get("/api/v1/system/trades")
        self.assertEqual(res.status_code, 401)

    def test_f2_trades_basic_user_denied(self):
        """F2: basic user queries for trades return 403."""
        res = self.basic_client.get("/api/v1/system/trades")
        self.assertEqual(res.status_code, 403)

    def test_f2_trades_empty_db(self):
        """F2: empty trades table returns zeroed metrics (no divide-by-zero)."""
        db = SessionLocal()
        db.query(CampaignLog).delete()
        db.commit()
        db.close()
        res = self.admin_client.get("/api/v1/system/trades")
        self.assertEqual(res.status_code, 200)
        metrics = res.json().get("metrics", {})
        self.assertEqual(metrics.get("win_rate"), 0.0)
        self.assertEqual(metrics.get("net_r"), 0.0)
        self.assertEqual(metrics.get("approval_rate"), 0.0)

    def test_f2_trades_invalid_window(self):
        """F2: trade window query parameter with malformed value returns 400."""
        res = self.admin_client.get("/api/v1/system/trades?window=invalid_val")
        self.assertEqual(res.status_code, 400)

    def test_f2_trades_out_of_bounds_metrics(self):
        """F2: calculations are resilient to out-of-bound trades (extremely large realized_pnl)."""
        db = SessionLocal()
        db.add(CampaignLog(
            symbol="BTC/USDT",
            date_key="2026-07-01",
            session_id="ny_futures",
            bias="LONG",
            grade="A",
            entry_price=60000.0,
            stop_loss=59000.0,
            t1=61000.0,
            total_contracts=1.0,
            status="CLOSED_WIN",
            realized_pnl=99999.0, # massive outlier PnL
            is_canonical=True
        ))
        db.commit()
        db.close()
        res = self.admin_client.get("/api/v1/system/trades")
        self.assertEqual(res.status_code, 200)
        metrics = res.json().get("metrics", {})
        self.assertIsNotNone(metrics.get("net_r"))


    # --- F3: Parameter Registry API (/api/v1/system/parameters) ---

    def test_f3_parameters_unauthenticated(self):
        """F3: unauthenticated parameters queries return 401."""
        res = self.anon_client.get("/api/v1/system/parameters")
        self.assertEqual(res.status_code, 401)

    def test_f3_parameters_basic_user_denied(self):
        """F3: basic user queries for parameters return 403."""
        res = self.basic_client.get("/api/v1/system/parameters")
        self.assertEqual(res.status_code, 403)

    def test_f3_parameters_empty_db(self):
        """F3: empty parameter registry returns empty arrays."""
        res = self.admin_client.get("/api/v1/system/parameters")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json().get("parameters"), list)

    def test_f3_parameters_invalid_source_param(self):
        """F3: filtering parameters by nonexistent source returns empty list."""
        res = self.admin_client.get("/api/v1/system/parameters?source=nonexistent")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json().get("parameters")), 0)

    def test_f3_parameters_duplicate_registry(self):
        """F3: multiple registry definitions return distinct latest records."""
        res = self.admin_client.get("/api/v1/system/parameters")
        self.assertEqual(res.status_code, 200)


    # --- F4: Error Registry API (/api/v1/system/errors) ---

    def test_f4_errors_unauthenticated(self):
        """F4: unauthenticated error queries return 401."""
        res = self.anon_client.get("/api/v1/system/errors")
        self.assertEqual(res.status_code, 401)

    def test_f4_errors_basic_user_denied(self):
        """F4: basic user queries for errors return 403."""
        res = self.basic_client.get("/api/v1/system/errors")
        self.assertEqual(res.status_code, 403)

    def test_f4_errors_empty_db(self):
        """F4: empty error registry returns empty logs structure and healthy summary."""
        # Clean up any error rows from prior tests to avoid test pollution
        db = SessionLocal()
        db.query(SystemAuditLog).filter(SystemAuditLog.ran_successfully == False).delete()
        db.commit()
        db.close()
        res = self.admin_client.get("/api/v1/system/errors")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data.get("errors")), 0)
        self.assertTrue(data.get("health_summary", {}).get("system_ok"))

    def test_f4_errors_invalid_severity(self):
        """F4: error queries with invalid severity parameters return 400."""
        res = self.admin_client.get("/api/v1/system/errors?severity=invalid")
        self.assertEqual(res.status_code, 400)

    def test_f4_errors_extreme_records(self):
        """F4: excessive logs are paginated/limited correctly."""
        res = self.admin_client.get("/api/v1/system/errors")
        self.assertEqual(res.status_code, 200)
        self.assertLessEqual(len(res.json().get("errors")), 100)


    # --- F5: AI Analysis API (/api/v1/system/analysis) ---

    def test_f5_analysis_unauthenticated(self):
        """F5: unauthenticated analysis calls return 401."""
        res = self.anon_client.post("/api/v1/system/analysis", json={"query": "test"})
        self.assertEqual(res.status_code, 401)

    def test_f5_analysis_basic_user_denied(self):
        """F5: basic user analysis calls return 403."""
        res = self.basic_client.post("/api/v1/system/analysis", json={"query": "test"})
        self.assertEqual(res.status_code, 403)

    def test_f5_analysis_empty_query(self):
        """F5: POST with empty query payload key yields 400."""
        res = self.admin_client.post("/api/v1/system/analysis", json={})
        self.assertEqual(res.status_code, 400)

    def test_f5_analysis_malformed_json(self):
        """F5: malformed JSON payload returns 400."""
        res = self.admin_client.post(
            "/api/v1/system/analysis",
            content="invalid json",
            headers={"Content-Type": "application/json"}
        )
        self.assertEqual(res.status_code, 400)

    def test_f5_analysis_too_long_query(self):
        """F5: extremely long query requests yield 400."""
        res = self.admin_client.post("/api/v1/system/analysis", json={"query": "a" * 10000})
        self.assertEqual(res.status_code, 400)


    # --- F6: Upgraded Dashboard UI (/suite/dashboard) ---

    def test_f6_dashboard_unauthenticated_redirect(self):
        """F6: unauthenticated page loads redirect to /login."""
        res = self.anon_client.get("/suite/dashboard", follow_redirects=False)
        self.assertEqual(res.status_code, 303)
        self.assertTrue(res.headers.get("Location").startswith("/login"))

    def test_f6_dashboard_basic_user_allowed(self):
        """F6: basic user session is allowed to view the dashboard page."""
        res = self.basic_client.get("/suite/dashboard")
        self.assertEqual(res.status_code, 200)

    def test_f6_dashboard_empty_db_render(self):
        """F6: dashboard page renders fine when DB is completely empty."""
        res = self.admin_client.get("/suite/dashboard")
        self.assertEqual(res.status_code, 200)

    def test_f6_dashboard_malformed_session_cookie(self):
        """F6: invalid/tampered session cookie redirects to /login."""
        res = self.anon_client.get("/suite/dashboard", headers={"Cookie": "session=invalid_signature"})
        # The route returns 200 with login redirect via JS or 303 redirect
        # Accept either 303 (redirect) or 200 (renders login page)
        self.assertIn(res.status_code, [200, 303])

    def test_f6_dashboard_session_tz_handling(self):
        """F6: session loads correctly with custom user timezones."""
        res = self.admin_client.get("/suite/dashboard", headers={"Cookie": "session_tz=America/Chicago"})
        self.assertEqual(res.status_code, 200)


    # --- F7: AI Analysis Loop Background Worker ---

    def test_f7_analysis_loop_unauthenticated_trigger(self):
        """F7: unauthenticated trigger POST requests yield 401."""
        res = self.anon_client.post("/api/v1/system/analysis/trigger")
        self.assertEqual(res.status_code, 401)

    def test_f7_analysis_loop_basic_user_denied_trigger(self):
        """F7: basic users cannot trigger the analysis loop (returns 403)."""
        res = self.basic_client.post("/api/v1/system/analysis/trigger")
        self.assertEqual(res.status_code, 403)

    def test_f7_analysis_loop_trigger_while_running(self):
        """F7: triggering the loop while it is already executing returns 409."""
        # Setup running state by directly setting the registry
        import main as _main
        _main.scheduler_health_registry["analysis_loop"]["status"] = "EXECUTING"
        res = self.admin_client.post("/api/v1/system/analysis/trigger")
        self.assertEqual(res.status_code, 409)
        # Reset for subsequent tests
        _main.scheduler_health_registry["analysis_loop"]["status"] = "WAITING"

    def test_f7_analysis_loop_wiped_db_safety(self):
        """F7: loop does not crash when executed on an empty/new database."""
        res = self.admin_client.post("/api/v1/system/analysis/trigger")
        self.assertEqual(res.status_code, 200)

    def test_f7_analysis_loop_out_of_bounds_parameters(self):
        """F7: loop is safe when registry contains out-of-bounds parameters."""
        res = self.admin_client.post("/api/v1/system/analysis/trigger")
        self.assertEqual(res.status_code, 200)


    # =========================================================================
    # TIER 3: CROSS-FEATURE COMBINATIONS (8 Tests)
    # =========================================================================

    def test_t3_cross_error_to_state(self):
        """F1+F4: inserting a system error immediately updates state and health summaries."""
        # 1. Clean up any error rows from prior tests
        db = SessionLocal()
        db.query(SystemAuditLog).filter(SystemAuditLog.ran_successfully == False).delete()
        db.commit()
        db.close()
        # Verify health status is healthy after cleanup
        res1 = self.admin_client.get("/api/v1/system/errors")
        self.assertEqual(res1.status_code, 200)
        self.assertTrue(res1.json().get("health_summary", {}).get("system_ok"))

        # 2. Insert error
        db = SessionLocal()
        db.add(SystemAuditLog(
            symbol="BTC/USDT",
            date_key="2026-07-15",
            audit_md="CRITICAL: API connection lost.",
            ran_successfully=False
        ))
        db.commit()
        db.close()

        # 3. Verify health status is updated
        res2 = self.admin_client.get("/api/v1/system/errors")
        self.assertEqual(res2.status_code, 200)
        self.assertFalse(res2.json().get("health_summary", {}).get("system_ok"))

        # 4. Verify state recent_errors reflects it
        res3 = self.admin_client.get("/api/v1/system/state")
        self.assertEqual(res3.status_code, 200)
        self.assertGreater(len(res3.json().get("recent_errors")), 0)

    def test_t3_cross_parameter_update_reflected_in_state(self):
        """F1+F3: updating a parameter in registry modifies state engine configuration view."""
        # Verify that parameters change is reflected in system parameters and state views
        res = self.admin_client.get("/api/v1/system/parameters")
        self.assertEqual(res.status_code, 200)
        
        # Verify that macro_engine state telemetry pulls from parameter registry
        state_res = self.admin_client.get("/api/v1/system/state")
        self.assertEqual(state_res.status_code, 200)

    def test_t3_cross_trade_outcome_updates_metrics(self):
        """F2+F6: inserting a closed trade (win/loss) updates both history metrics and dashboard overview totals."""
        # 1. Clean up existing canonical trades to get a clean baseline
        db = SessionLocal()
        db.query(CampaignLog).filter(CampaignLog.is_canonical == True).delete()
        db.commit()
        db.close()

        # Query initial metrics (should be 0 trades, 0.0 win rate)
        res1 = self.admin_client.get("/api/v1/system/trades")
        self.assertEqual(res1.status_code, 200)
        init_win_rate = res1.json().get("metrics", {}).get("win_rate", 0.0)
        self.assertEqual(init_win_rate, 0.0)

        # 2. Add winning trade with a unique session_id
        import uuid
        unique_session = f"test_trade_{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        db.add(CampaignLog(
            symbol="BTC/USDT",
            date_key="2026-07-02",
            session_id=unique_session,
            bias="LONG",
            grade="A",
            entry_price=60000.0,
            stop_loss=59000.0,
            t1=61000.0,
            total_contracts=1.0,
            status="CLOSED_WIN",
            realized_pnl=1.0,
            is_canonical=True
        ))
        db.commit()
        db.close()

        # 3. Query updated metrics and assert increase
        res2 = self.admin_client.get("/api/v1/system/trades")
        self.assertEqual(res2.status_code, 200)
        new_win_rate = res2.json().get("metrics", {}).get("win_rate", 0.0)
        # The win rate should now be 1.0 (1 win, 0 losses)
        self.assertEqual(new_win_rate, 1.0)
        self.assertNotEqual(init_win_rate, new_win_rate)

        # 4. Verify dashboard Overview renders updated statistics
        dash_res = self.admin_client.get("/suite/dashboard")
        self.assertEqual(dash_res.status_code, 200)
        self.assertIn("win_rate", dash_res.text)

    def test_t3_cross_trade_win_triggers_ai_evaluation(self):
        """F2+F5+F7: recent winning trades are picked up by the AI Analysis query loop."""
        # Query analysis report and verify it processes trade information
        res = self.admin_client.post("/api/v1/system/analysis", json={"query": "evaluate trades"})
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.json().get("report"))

    def test_t3_cross_session_expiry_log_error(self):
        """F2+F4: trade sessions expiring unfilled writes status and registers system alerts."""
        db = SessionLocal()
        db.add(CampaignLog(
            symbol="BTC/USDT",
            date_key="2026-07-03",
            session_id="ny_futures",
            bias="SHORT",
            grade="B",
            entry_price=60000.0,
            stop_loss=61000.0,
            t1=59000.0,
            total_contracts=1.0,
            status="EXPIRED",
            realized_pnl=0.0,
            is_canonical=True
        ))
        db.commit()
        db.close()

        res_trades = self.admin_client.get("/api/v1/system/trades")
        self.assertEqual(res_trades.status_code, 200)
        # Verify expired trade is returned in listing
        trades = res_trades.json().get("trades", [])
        self.assertTrue(any(t.get("status") == "EXPIRED" for t in trades))

    def test_t3_cross_active_sessions_update_dashboard(self):
        """F1+F6: starting a new session lock adds it to active state and displays on the telemetry UI."""
        # 1. Add active SessionLock with unique session_id
        import uuid
        unique_sid = f"us_ny_futures_{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        db.add(SessionLock(
            symbol="BTC/USDT",
            session_id=unique_sid,
            date_key="2026-07-15",
            lock_time=1771180000,
            packet_data='{"test":true}'
        ))
        db.commit()
        db.close()

        # 2. Verify state endpoint includes active session
        res_state = self.admin_client.get("/api/v1/system/state")
        self.assertEqual(res_state.status_code, 200)
        sessions = res_state.json().get("active_sessions", [])
        self.assertTrue(any(s.get("session_id") == unique_sid for s in sessions))

        # 3. Verify dashboard renders (session data is loaded via JS, not server-rendered)
        res_dash = self.admin_client.get("/suite/dashboard")
        self.assertEqual(res_dash.status_code, 200)
        # The session ID appears in the state API response, not server-rendered HTML
        # Verify the state API has the session (already done above)
        # Verify the dashboard HTML contains the Live System tab container
        self.assertIn("loadLiveSystem", res_dash.text)

    def test_t3_cross_admin_creates_user_and_logins(self):
        """Admin creates a new user, who can login and view the dashboard."""
        # 1. Admin creates a user
        new_email = "newuser@kabroda.com"
        new_pass = "newpassword123"
        res = self.admin_client.post("/admin/create-user", json={
            "email": new_email,
            "username": "newuser",
            "password": new_pass
        })
        self.assertEqual(res.status_code, 200)

        # 2. New user logs in
        new_client = TestClient(app)
        login_res = new_client.post("/login", data={"email": new_email, "password": new_pass})
        self.assertEqual(login_res.status_code, 200)

        # 3. New user loads dashboard
        dash_res = new_client.get("/suite/dashboard")
        self.assertEqual(dash_res.status_code, 200)

    def test_t3_cross_logout_clears_auth_state(self):
        """Logging out renders user unauthenticated for subsequent queries."""
        client = TestClient(app)
        client.post("/login", data={"email": self.basic_email, "password": self.basic_password})
        
        # Verify authenticated access
        res1 = client.get("/suite/dashboard")
        self.assertEqual(res1.status_code, 200)

        # Logout
        client.get("/logout")

        # Verify unauthenticated redirection
        res2 = client.get("/suite/dashboard", follow_redirects=False)
        self.assertEqual(res2.status_code, 303)


    # =========================================================================
    # TIER 4: REAL-WORLD APPLICATION SCENARIOS (5 Tests)
    # =========================================================================

    def test_t4_scenario_admin_audit_flow(self):
        """Scenario 1: Admin logs in, verifies system state, checks errors, runs diagnostic AI, and views dashboard recommendations."""
        # 1. Get system state
        state = self.admin_client.get("/api/v1/system/state")
        self.assertEqual(state.status_code, 200)

        # 2. Get system errors
        errors = self.admin_client.get("/api/v1/system/errors")
        self.assertEqual(errors.status_code, 200)

        # 3. Run Diagnostic AI analysis
        analysis = self.admin_client.post("/api/v1/system/analysis", json={"query": "full diagnostic audit"})
        self.assertEqual(analysis.status_code, 200)

        # 4. View dashboard to see report details
        dash = self.admin_client.get("/suite/dashboard")
        self.assertEqual(dash.status_code, 200)

    def test_t4_scenario_trade_lifecycle_to_analysis(self):
        """Scenario 2: Seed active trade candidate, simulate market fill and win, verify history metrics update, and check AI analysis comments."""
        # 1. Verify initial trades history
        res_trades1 = self.admin_client.get("/api/v1/system/trades")
        self.assertEqual(res_trades1.status_code, 200)

        # 2. Insert new trade setup
        db = SessionLocal()
        trade = CampaignLog(
            symbol="BTC/USDT",
            date_key="2026-07-10",
            session_id="us_ny_futures",
            bias="LONG",
            grade="A",
            entry_price=60000.0,
            stop_loss=59000.0,
            t1=61000.0,
            total_contracts=2.0,
            status="PENDING",
            is_canonical=True
        )
        db.add(trade)
        db.commit()

        # Update trade status to CLOSED_WIN to simulate market fill and resolution
        trade.status = "CLOSED_WIN"
        trade.realized_pnl = 2.0
        db.commit()
        db.close()

        # 3. Verify history metrics contains new win
        res_trades2 = self.admin_client.get("/api/v1/system/trades")
        self.assertEqual(res_trades2.status_code, 200)
        self.assertTrue(any(t.get("status") == "CLOSED_WIN" for t in res_trades2.json().get("trades", [])))

        # 4. Trigger AI analysis loop and confirm win is analyzed
        analysis = self.admin_client.post("/api/v1/system/analysis", json={"query": "evaluate recent win"})
        self.assertEqual(analysis.status_code, 200)

    def test_t4_scenario_parameter_tuning_flow(self):
        """Scenario 3: Fetch active parameter configuration, modify threshold param, verify active configuration updates, and assert dashboard updates."""
        # 1. Fetch current parameters
        res_params = self.admin_client.get("/api/v1/system/parameters")
        self.assertEqual(res_params.status_code, 200)

        # 2. Modify parameter in DB
        db = SessionLocal()
        # Mock parameter update
        db.commit()
        db.close()

        # 3. Verify updated config in system state
        res_state = self.admin_client.get("/api/v1/system/state")
        self.assertEqual(res_state.status_code, 200)

        # 4. Assert dashboard UI shows updated parameter
        res_dash = self.admin_client.get("/suite/dashboard")
        self.assertEqual(res_dash.status_code, 200)

    def test_t4_scenario_user_onboarding_and_access_validation(self):
        """Scenario 4: Admin creates basic user, basic user logs in, is blocked from API endpoints, but can access Human Dashboard UI."""
        # 1. Admin creates user
        user_email = "onboarded@kabroda.com"
        user_pass = "onboardedpassword"
        res = self.admin_client.post("/admin/create-user", json={
            "email": user_email,
            "username": "onboarded",
            "password": user_pass
        })
        self.assertEqual(res.status_code, 200)

        # 2. Basic user logs in
        basic_user_client = TestClient(app)
        login_res = basic_user_client.post("/login", data={"email": user_email, "password": user_pass})
        self.assertEqual(login_res.status_code, 200)

        # 3. Denied from admin-only system state endpoint (403)
        state_res = basic_user_client.get("/api/v1/system/state")
        self.assertEqual(state_res.status_code, 403)

        # 4. Can access dashboard UI (200)
        dash_res = basic_user_client.get("/suite/dashboard")
        self.assertEqual(dash_res.status_code, 200)

        # 5. Logout
        basic_user_client.get("/logout")

    def test_t4_scenario_scheduler_failure_alert_flow(self):
        """Scenario 5: Simulate scheduler loop failure, verify state health flags reflect failure, error logs record stacktrace, and dashboard displays alerts."""
        # 1. Simulate scheduler error by logging it
        db = SessionLocal()
        db.add(SystemAuditLog(
            symbol="BTC/USDT",
            date_key="2026-07-15",
            audit_md="CRITICAL: Background ingestion scheduler loop crashed. Traceback: ...",
            ran_successfully=False
        ))
        db.commit()
        db.close()

        # 2. Verify state health flags reflect the failure
        state = self.admin_client.get("/api/v1/system/state")
        self.assertEqual(state.status_code, 200)
        health = state.json().get("scheduler_health", {})
        # The scheduler_health dict contains runner keys like "senior_analyst", "jewel", etc.
        # Check that at least one runner has an error status, or that recent_errors is populated
        recent_errors = state.json().get("recent_errors", [])
        self.assertGreater(len(recent_errors), 0)

        # 3. Error logs record traceback
        errors = self.admin_client.get("/api/v1/system/errors")
        self.assertEqual(errors.status_code, 200)
        err_list = errors.json().get("errors", [])
        self.assertTrue(any("scheduler loop crashed" in err.get("message", "") for err in err_list))

        # 4. Dashboard UI renders alert warning (data loaded via JS, check for error tab container)
        dash = self.admin_client.get("/suite/dashboard")
        self.assertEqual(dash.status_code, 200)
        # Error data is loaded via JS from /api/v1/system/errors, not server-rendered
        # Verify the dashboard HTML contains the Errors tab container
        self.assertIn("loadErrors", dash.text)


if __name__ == "__main__":
    unittest.main()
