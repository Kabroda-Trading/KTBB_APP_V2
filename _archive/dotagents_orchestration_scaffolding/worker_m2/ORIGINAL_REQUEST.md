## 2026-07-16T01:20:13Z
You are the worker for Milestone M2: AI Diagnostic API.
Your working directory: C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\.agents\worker_m2
Identity: teamwork_preview_worker

Tasks:
1. Update C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\database.py:
   - Add the `SystemAnalysisReport` SQLAlchemy model as defined in the Explorer report:
     ```python
     class SystemAnalysisReport(Base):
         __tablename__ = "system_analysis_reports"
         id = Column(Integer, primary_key=True, index=True)
         analysis_id = Column(String, unique=True, index=True, nullable=False)
         query = Column(String, nullable=False)
         status = Column(String, default="PENDING", nullable=False)
         report_json = Column(String, nullable=True)
         error_message = Column(String, nullable=True)
         created_at = Column(DateTime, default=datetime.datetime.utcnow)
     ```
     Ensure `SystemAnalysisReport` is imported inside `main.py` along with other database models.

2. Create a new agent spec file: C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\agents\system_analysis.md
   - Set up standard YAML frontmatter for Claude model `claude-sonnet-4-6` and maximum tokens `4096`.
   - Set up the prompt instructions to output valid JSON matching the schema:
     ```json
     {
       "summary": "...",
       "verdict": "STABLE" | "OPTIMIZE" | "RISK_ALERT",
       "data_metrics": { ... },
       "recommendations": [
         {
           "parameter": "...",
           "observation": "...",
           "suggestion": "..."
         }
       ],
       "confidence_score": 0.0 - 1.0
     }
     ```

3. Update C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\main.py:
   - Declare a global in-memory dict `scheduler_health_registry` at the top level:
     ```python
     scheduler_health_registry = {
         "senior_analyst": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
         "jewel": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
         "weekly": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
         "daily_4h1h_audit": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
         "outcome_tracker": {"last_run": None, "next_run": None, "status": "PENDING", "error_count": 0, "last_error": None},
         "monthly_lti": {"last_run": None, "next_run": None, "status": "DISABLED", "error_count": 0, "last_error": None},
     }
     ```
   - Update the 6 background scheduler tasks (`run_senior_analyst_scheduler`, `run_jewel_scheduler`, `run_weekly_scheduler`, `run_daily_4h1h_audit_scheduler`, `run_outcome_tracker`, `run_monthly_lti_scheduler`) inside `main.py`. Ensure that:
     * When a loop begins or recalculates its next execution time: calculate `next_run` datetime using the sleep duration, format it to ISO format, and write it to `next_run` in the registry. Set `status` to `"WAITING"`.
     * Immediately after waking up from sleep (e.g. `await asyncio.sleep(seconds)`): set `status` to `"EXECUTING"`.
     * Upon successful completion of a scheduled run: set `last_run` to current UTC ISO format string, and status back to `"WAITING"`.
     * In the outer `except Exception as e` handler of each scheduler: increment `error_count`, store `str(e)` in `last_error`, and set `status` to `"ERROR"`.
   - Implement the five API JSON endpoints under the path prefix `/api/v1/system` in `main.py`:
     * `GET /api/v1/system/state`: Expose the live structural snapshot of the system.
     * `GET /api/v1/system/trades`: Expose windowed canonical trade records and key win/loss/approval metrics.
     * `GET /api/v1/system/parameters`: Expose a comprehensive registry of all indicator settings, risk configurations, dynamic configs, and dependency maps.
     * `GET /api/v1/system/errors`: Expose agent failures, system alerts, and system health grades.
     * `POST /api/v1/system/analysis`: Accept natural language query, build structured system context block, run analysis using Claude from the `agents/system_analysis.md` spec, log the request/response in `SystemAnalysisReport`, and return the JSON analysis.
     * Make sure all endpoints check user authentication:
       ```python
       ctx = get_user_context(request, db)
       if not ctx.get("is_logged_in"):
           return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
       ```
     * For `POST /api/v1/system/analysis`, dynamically construct the DB context (past 30 days trade statistics, current params, recent errors, etc.) and call `agent_core._call_from_spec`.

4. Run tests:
   - Run `pytest harness/test_audit_safety.py` to make sure that the safety of the audit write-once policy and campaign flow has not been violated.
   - Run the server or any other verification tests.
   - Document the exact commands run and the results of verification.

Handoff instructions:
Provide a handoff report at C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\.agents\worker_m2\handoff.md and notify me via send_message when complete. Include build and test run command outputs.
