# Handoff Report — explorer_m2

This handoff report summarizes the codebase investigation findings and detailed implementation plan for the Milestone M2: AI Diagnostic API endpoints.

---

## 1. Observation

Direct observations made within the Kabroda trading system codebase:

1. **System Parameters Definition**:
   * **JSON Config**: `/bold-hubble/krown_settings_and_rules.json` contains static indicator configurations, but is **not** imported or read at runtime in any active strategy file.
   * **Hardcoded Defaults**:
     * `battlebox_pipeline.py:226`: `def _calc_bbwp(closes: List[float], bb_period: int = 20, bb_std: float = 2.0, lookback: int = 252) -> float`
     * `battlebox_pipeline.py:249`: `def _calc_pmarp(closes: List[float], ma_period: int = 50, lookback: int = 252) -> float`
     * `battlebox_pipeline.py:268-281`: `_bbwp_state_label` and `_pmarp_state_label` define thresholds statically (e.g. `<= 5.0` and `>= 95.0`).
     * `gravity_engine.py:602-680` (4H) and `829-909` (1H) hardcode ATR multipliers (1.5x for 4H, 1.0x for 1H) and Fibonacci targets (1.0x T1, 1.618x T2, 2.618x T3).
     * `position_sizing/position_sizing.py:20-30`: RISK_PERCENT (default `0.02`) and POSITION_SIZING_METHOD (default `"volatility"`) are loaded from environment variables with defaults fallback.

2. **Active Sessions & Shadow Runners Storage**:
   * `database.py:421`: `class SessionLock(Base): __tablename__ = "session_locks"` holds locked sessions with `session_id`, `date_key`, `lock_time`, and `packet_data`.
   * `database.py:435`: `class CampaignLog(Base): __tablename__ = "campaign_logs"` holds trading and shadow runner fields:
     * Line 568: `shadow_runner_active = Column(Boolean, default=False, server_default="0")`
     * Line 574: `shadow_runner_closed_at = Column(DateTime, nullable=True)`
     * Line 506: `session_timeframe = Column(String, nullable=True, default="15M")`

3. **System Errors and Alerts**:
   * `main.py:1964-1977`: `global_exception_handler` intercepts exceptions, formats them, and prints to console: `print(f"CRITICAL CRASH:\n{error_trace}")`.
   * `database.py:660`: `class AgentRunLog(Base): __tablename__ = "agent_run_log"` stores agent execution parameters. When a run fails, `status = "ERROR"` and the `error_message` is logged to the DB.
   * `notify.py:28`: `def send_admin_email(subject: str, body: str) -> bool` handles standard SMTP email alerts to the `SMTP_DEST` email address.

4. **Background Schedulers**:
   * `main.py:618-644`: FastAPI `lifespan` context registers 6 scheduler loops (5 active, 1 commented out/disabled):
     ```python
     app.state.senior_analyst_task   = asyncio.create_task(run_senior_analyst_scheduler())
     app.state.jewel_task            = asyncio.create_task(run_jewel_scheduler())
     app.state.weekly_task           = asyncio.create_task(run_weekly_scheduler())
     # app.state.lti_task            = asyncio.create_task(run_monthly_lti_scheduler())
     app.state.daily_audit_task      = asyncio.create_task(run_daily_4h1h_audit_scheduler())
     app.state.outcome_tracker_task  = asyncio.create_task(run_outcome_tracker())
     ```
   * Schedulers sleep using `asyncio.sleep` calculated dynamically, with no memory-based or DB-based status logging built in.

5. **Diagnostic Tests Execution**:
   * Execution of `python harness/test_audit_safety.py` completed successfully:
     ```
     RESULT: 7 passed, 0 failed
     CHECK 3 PASS — audit failures are fully contained. Trade path is safe.
     ```

---

## 2. Logic Chain

1. **Parameters Registry**: Because parameters (BBWP, PMARP, ATR, Risk, Fibs) are hardcoded into Python function signatures or retrieved from environment variables rather than a dynamic configuration database table, the `GET /api/v1/system/parameters` endpoint must be implemented as a read-only registry reading static defaults, environment overrides, and single-row protocol tables (`LtiProtocol`, `MonitorConfig`).
2. **State & Trade Queries**: Since `SessionLock` contains locked daily sessions, and `CampaignLog` holds canonical trade setups (`is_canonical == True`) with fields indicating status, timestamps, and shadow runner states (`shadow_runner_active`, `shadow_runner_closed_at`), we can fully construct the `GET /api/v1/system/state` and `GET /api/v1/system/trades` endpoints using standard SQLAlchemy query filters against these tables.
3. **Error Reporting**: Since runtime HTTP errors are captured by `global_exception_handler` and agent execution failures are persisted to `AgentRunLog` with `status == "ERROR"`, the `GET /api/v1/system/errors` endpoint should query the `AgentRunLog` table to return the structured history of recent system anomalies.
4. **Scheduler Health Tracking**: Since background scheduler tasks run in infinite loops without recording execution timestamps or statuses, we must introduce an in-memory dictionary `scheduler_health_registry` on FastAPI's `app.state` to track health metrics (`last_run`, `next_run`, `status`, `error_count`, `last_error`) without polluting the database with high-frequency writes.
5. **AI ad-hoc Analysis**: To support `POST /api/v1/system/analysis`, we need to define a new database model `SystemAnalysisReport` to persist reports and a new agent spec `agents/system_analysis.md` to format a structured JSON block output from Claude based on gathered database context and the user query.

---

## 3. Caveats

* The monthly LTI scheduler (`run_monthly_lti_scheduler`) is currently commented out in `main.py:630` (lifespan registration) because it is undergoing a rebuild. The proposed health registry registers its status as `DISABLED` by default, but it will support active tracking once re-enabled.
* Account balance and risk calculations in `position_sizing.py` assume fallback constants if environment variables are unset.

---

## 4. Conclusion

The Kabroda system is highly automated but currently lacks structured API observability. Milestone M2 endpoints can be fully implemented using read-only database queries against `SessionLock`, `CampaignLog`, `AgentRunLog`, and the new proposed `SystemAnalysisReport` table, coupled with a lightweight in-memory task health registry on FastAPI's application state. This design satisfies all milestone criteria and maintains absolute isolation from active trade-gating pipelines.

---

## 5. Verification Method

1. **Unit & Safety Tests**:
   * Run the audit safety harness to ensure no regressions are introduced in existing files:
     `python harness/test_audit_safety.py`
2. **Server Execution**:
   * Run the API server: `python main.py` or `uvicorn main:app --host 127.0.0.1 --port 8000`
3. **Endpoint Validation**:
   * Send local HTTP requests (via cURL or Postman) to verify response schemas for the 5 routes:
     * `GET http://localhost:8000/api/v1/system/state`
     * `GET http://localhost:8000/api/v1/system/trades?window=30d`
     * `GET http://localhost:8000/api/v1/system/parameters`
     * `GET http://localhost:8000/api/v1/system/errors`
     * `POST http://localhost:8000/api/v1/system/analysis` with JSON payload `{ "query": "Analyze win-rate on 1H candidates." }`
4. **Invalidation Conditions**:
   * Modification of `CampaignLog` column names (e.g. `shadow_runner_active`) would invalidate the active state queries.
   * Modifying lifespan tasks in `main.py` without updating the `scheduler_health_registry` mapping would result in missed scheduler logs.
