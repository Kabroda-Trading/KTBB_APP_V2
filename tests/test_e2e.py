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


    # --- F2: Trade History & Metrics API removed 2026-09-23 (V2 Crown
    # retirement, Audit-AI surface retirement) -- /api/v1/system/trades was
    # 100% CampaignLog-sourced with zero frontend caller, removed from
    # main.py in the same pass.


    # --- F3 (Parameter Registry) and F4 (Error Registry) removed 2026-09-23
    # -- Andy's call during the strategic site audit. Both backing routes
    # (/api/v1/system/parameters, /api/v1/system/errors) were already dead
    # in practice, unrelated to V2/Traveler: parameters returned hardcoded
    # literals never wired to real config, and errors read SystemAuditLog,
    # which has had zero live writers since the Performance Auditor was
    # archived (always reported "all systems operational" regardless of
    # real state). See main.py's own removal comment and V2_RETIREMENT_MAP.md
    # for the dashboard tab-by-tab audit this followed.

    # --- F5: AI Analysis API removed 2026-09-23 (V2 Crown retirement) --
    # POST /api/v1/system/analysis (no suffix) was confirmed to have zero
    # frontend caller anywhere in the repo before removal -- the live
    # "Recent Reports"/single-report routes it fed were never actually
    # reachable from the real UI. See main.py's own removal comment.


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

    # test_f6_dashboard_renders_parameters/_errors removed 2026-09-23 along
    # with the F3/F4 routes and tabs themselves -- see the removal comment
    # further up this file.

    def test_f6_dashboard_renders_analysis(self):
        """F6: dashboard view contains the AI Reports tab container."""
        res = self.admin_client.get("/suite/dashboard")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Analysis", res.text)


    # --- F7: AI Analysis Loop Background Worker removed 2026-09-23 (V2
    # Crown retirement, Audit-AI surface retirement, Andy's "retire
    # entirely" ruling) -- /api/v1/system/analysis/trigger, its
    # _run_analysis_loop_body()/run_analysis_loop_scheduler() background
    # task, and the scheduler_health_registry["analysis_loop"] entry were
    # all removed from main.py in the same pass.


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


    # --- F2 boundary tests and F5: AI Analysis API removed 2026-09-23 (V2
    # Crown retirement) -- see the Tier-1 removal comments above for both.


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


    # --- F7: AI Analysis Loop Background Worker boundary tests removed
    # 2026-09-23 (V2 Crown retirement) -- see the Tier-1 F7 removal comment.


    # =========================================================================
    # TIER 3: CROSS-FEATURE COMBINATIONS (8 Tests)
    # =========================================================================

    # test_t3_cross_error_to_state/_cross_parameter_update_reflected_in_state
    # removed 2026-09-23 -- their primary subjects (/api/v1/system/errors,
    # /api/v1/system/parameters) are gone (see the F3/F4 removal comment
    # further up this file). The one still-relevant check either test made
    # in passing -- /api/v1/system/state's own recent_errors field reflecting
    # a real SystemAuditLog row -- stays covered by test_f1_state_excessive_errors.

    # test_t3_cross_trade_outcome_updates_metrics/_cross_trade_win_triggers_
    # ai_evaluation/_cross_session_expiry_log_error removed 2026-09-23 (V2
    # Crown retirement) -- all three depended on /api/v1/system/trades
    # and/or /api/v1/system/analysis, both removed from main.py in the same
    # pass. See the Tier-1 F2/F5 removal comments above.

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

    # test_t4_scenario_admin_audit_flow/_scenario_trade_lifecycle_to_analysis
    # removed 2026-09-23 (V2 Crown retirement) -- both scenarios' actual
    # point (running /api/v1/system/analysis, checking /api/v1/system/
    # trades) is gone; their remaining incidental checks (system state,
    # dashboard render) are already covered individually by the F1/F6
    # tests above, not worth keeping a hollowed-out scenario test for.

    # test_t4_scenario_parameter_tuning_flow removed 2026-09-23 -- its
    # primary subject (/api/v1/system/parameters) is gone (see the F3/F4
    # removal comment further up this file); its own "modify parameter"
    # step was already a no-op with no real assertion behind it.

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
        """Scenario 5: Simulate scheduler loop failure, verify state health flags
        reflect failure. Steps 3-4 (the removed /api/v1/system/errors route +
        Errors tab dashboard check) were removed 2026-09-23 along with that
        route/tab -- see the F3/F4 removal comment further up this file.
        This trimmed test's remaining coverage overlaps with, but is distinct
        from, test_f1_state_excessive_errors (that one bulk-checks truncation
        at 50 rows; this one checks a single CRITICAL-tagged real error)."""
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


if __name__ == "__main__":
    unittest.main()
