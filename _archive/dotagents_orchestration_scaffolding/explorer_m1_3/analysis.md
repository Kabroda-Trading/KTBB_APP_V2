# Executive Dashboard Edge-Case & Exception Audit Report

**Date**: 2026-07-15
**Auditor**: Teamwork Explorer (explorer_m1_3)
**Scope**: 
- Frontend Human Dashboard Layer (`templates/suite_dashboard.html`)
- Backend Executive Dashboard API Routes (`main.py` - `/api/dashboard/*` and `/suite/dashboard`)
- Database Schema and Initialization (`database.py`)
- Background schedulers & asynchronous workers (`main.py`)

---

## Executive Summary
This audit evaluated the resilience of the Kabroda Executive Dashboard against edge cases such as empty database states, null/missing values, missing columns/keys, and malformed database entries or API responses. The system demonstrates strong overall crash resilience due to comprehensive backend exception handling (global exception handlers and task-level `try-except` blocks). However, several key vulnerabilities and optimization opportunities were identified:
1. **Critical N+1 Query Performance Bug** in `/api/dashboard/jewel`, executing database lookups inside a loop for each session snapshot.
2. **Missing Frontend Exception Catch Blocks** in 6 of 7 JavaScript loader functions, exposing the UI to partial load failures if any API endpoint fails or returns bad data.
3. **Falsy Zero Value Formatting Bug** in the dashboard trade table formatter, displaying `—` instead of `$0.00` for zero prices.

---

## 1. Empty Database State Audit
An audit was performed to evaluate the behavior of both frontend and backend when all database tables are completely empty or have zero matching rows.

### Backend Performance on Empty DB
- **ZeroDivisionError Protection**: The backend routes are fully protected against division-by-zero errors.
  - In `/api/dashboard/overview`, calculations like `approved_rate` and `win_rate` check the denominator before division:
    ```python
    approved_rate = round(approved / total * 100, 1) if total > 0 else 0.0
    win_rate = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0.0
    ```
- **Null Aggregations Handling**: Database aggregation operations like `func.sum` returning `None` on empty tables are safely converted using default fallbacks:
    ```python
    net_r = round(float(net_r_raw or 0.0), 4)
    spend_7d = round(spend_raw or 0.0, 4)
    total_tok = (tok[0] or 0) + (tok[1] or 0)
    cache_hit_rate = round((tok[1] or 0) / total_tok * 100, 1) if total_tok > 0 else 0.0
    ```
- **Empty Datasets**: Query results such as `pnl_series`, `trades`, `audits`, and `newsletters` return empty lists (`[]`) or dicts (`{}`) without throwing errors.

### Frontend Performance on Empty DB
- **Graceful Table fallbacks**: The tables render helpful placeholder empty states:
  - Trade History: *"No trade history yet. Sessions will appear here after the first session lock."*
  - Newsletter Archive: *"No newsletters generated yet. The first will appear here after the next session lock."*
  - System Audits: *"No system audits yet. The first will appear after the next Sunday 23:00 UTC scheduler run."*
- **Empty Chart Rendering**: Chart.js receives empty datasets (`[]` or `{}`) and labels. Chart.js safely handles these empty datasets by displaying empty axes without crashing.

---

## 2. Null Values & Data Types Audit
A review was conducted of the fields marked nullable in the database models and how they are parsed.

### Backend Null Handling
- In `/api/dashboard/overview`, the queries filter out `CampaignLog.realized_pnl.isnot(None)`. If a campaign has a null PnL, it is simply skipped in the calculations.
- In `/api/dashboard/accuracy`, the queries filter out rows where `CampaignLog.kinematic_grade`, `CampaignLog.realized_pnl`, `DecisionJournal.outcome_direction_correct`, or `DecisionJournal.confluence_score` are null, ensuring only valid data is aggregated.
- In `/api/dashboard/mas-history`, rows with `realized_pnl` as null or invalid status are skipped in the cumulative logic.

### Frontend Null & Type Handling
- Null fields from JSON payloads are handled using defaults:
  - `n.date_key || '—'`, `t.bias || '—'`, `t.mas_approval_status || '—'`, `n.newsletter_md || ""`
- **Zero Value Formatting Bug**:
  In `templates/suite_dashboard.html`, the currency formatter function is defined as:
  ```javascript
  const fmt = v => v ? '$' + Number(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}) : '—';
  ```
  In JavaScript, `0` or `0.0` is falsy. If a price value (such as `entry_price` or `stop_loss`) is legitimately `0` or `0.0` (or fails to load and defaults to `0.0`), the formatter will display `—` instead of `$0.00`.
- **NaN Rendering**:
  If the backend returns a non-numeric string (e.g. `"N/A"`) instead of a float/int/null, `Number("N/A")` will evaluate to `NaN`, and the formatter will render `"$NaN"`. A robust numeric check is needed.

---

## 3. Missing Columns/Keys & Schema Evolution
When the database schema evolves, columns might be missing temporarily in local/legacy testing environments.

### Database Migrations
- `database.py` contains automated migration steps in `init_db()` that execute `ALTER TABLE` statements:
  ```python
  try:
      with engine.begin() as conn:
          conn.execute(text("ALTER TABLE campaign_logs ADD COLUMN mas_executive_brief TEXT"))
  except Exception:
      pass
  ```
  Wrapping these migrations in `try-except` blocks guarantees that database startup succeeds even if columns already exist or if the DB does not support the migration.

### Endpoint Crash Resistance
- If a column is missing from the database, SQLAlchemy will throw a `ProgrammingError` or `OperationalError` when querying that column.
- Every dashboard API route in `main.py` is fully protected by a catch-all `try-except Exception as e` wrapper:
  ```python
  except Exception as e:
      return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
  ```
  This guarantees that schema mismatch errors will return a JSON error response instead of inducing a FastAPI web server process crash.

---

## 4. Exception Handling & Server Crash Hardening
We verified that malformed requests, empty queries, or DB exceptions cannot cause server crashes.

### Background Task Schedulers
The async tasks (`run_senior_analyst_scheduler`, `run_jewel_scheduler`, `run_weekly_scheduler`, `run_daily_4h1h_audit_scheduler`, `run_outcome_tracker`, `session_monitor.run_session_monitor_loop`) run as background tasks on the event loop.
- Every scheduler has a loop wrapped in a `try-except Exception` block:
  ```python
  except asyncio.CancelledError:
      raise
  except Exception as e:
      print(f"[SCHEDULER] Error: {e}")
      await asyncio.sleep(300)
  ```
- This ensures that if any background agent fails due to DB locks, timeouts, or API errors, the exception is caught, logged, and the loop sleeps before retrying, preventing the scheduler thread/task from dying.

### Web Server Lifespan
- The FastAPI boot script starts all tasks within the `lifespan` manager:
  ```python
  app.state.gravity_task = asyncio.create_task(...)
  ```
  Since tasks run in the background, a crash in one does not terminate the FastAPI server.
- The global exception handler:
  ```python
  @app.exception_handler(Exception)
  async def global_exception_handler(request: Request, exc: Exception):
      ...
  ```
  ensures that any uncaught web request exception is rendered as a clean 500 error page rather than crashing the Uvicorn worker.

---

## 5. Key Vulnerabilities & Optimization Gaps

### GAP 1: Critical N+1 Query in `/api/dashboard/jewel`
In `main.py` (lines 1857-1877), the route `api_dashboard_jewel` queries all `JewelSnapshotLog` rows where `session_label == "NY_OPEN"`. For every snapshot found, it executes a separate query to `CampaignLog` to find the corresponding trade on that `date_key`:
```python
snapshots = db.query(JewelSnapshotLog).filter(
    JewelSnapshotLog.session_label == "NY_OPEN").all()
for snap in snapshots:
    ...
    trade = db.query(CampaignLog).filter(
        CampaignLog.symbol == "BTC/USDT",
        CampaignLog.date_key == date_key,
        CampaignLog.status.in_(["CLOSED_WIN", "CLOSED_LOSS"]),
        CampaignLog.is_canonical == True).first()
```
If there are 500 snapshot rows, the backend will perform **500 sequential DB queries**. This creates a massive bottleneck on the SQLite file lock, causing the request to take seconds or timeout, which will block other async controllers.

### GAP 2: Missing JS Loader try-catch blocks
In `templates/suite_dashboard.html` (lines 620-630), the frontend initializes 7 loaders in parallel:
```javascript
async function initDashboard() {
    await Promise.all([
        loadOverview(),
        loadMasHistory(),
        loadAccuracy(),
        loadCosts(),
        loadJewel(),
        loadAudits(),
        loadNewsletters(),
    ]);
}
```
Only `loadAudits()` has a `try-catch` wrapper. If any of the other 6 endpoints fails (e.g. throws a 500 or experiences a network failure during fetch), the promise returned by that loader will reject. Because `Promise.all` immediately rejects if any of its input promises reject, the remaining loaders will continue running in the background but their errors will go unhandled, and the page loading flow will break, leaving the dashboard in a half-loaded state.

---

## 6. Recommended Remediations

### Remediation 1: Resolve the N+1 Query in `api_dashboard_jewel`
Optimize the database lookup by querying all matching campaigns in a single query and grouping them in a dictionary.

**Proposed Code Modification in `main.py` (lines 1857-1879)**:
```python
        # Fetch snapshots
        snapshots = db.query(JewelSnapshotLog).filter(
            JewelSnapshotLog.session_label == "NY_OPEN").all()
            
        open_win = open_loss = closed_win = closed_loss = 0
        
        if snapshots:
            # Gather unique date keys to filter trades in one batch
            date_keys = {snap.timestamp.strftime("%Y-%m-%d") for snap in snapshots if snap.timestamp}
            
            # Batch query CampaignLog in a single SQL execution
            trades = db.query(CampaignLog.date_key, CampaignLog.status).filter(
                CampaignLog.symbol == "BTC/USDT",
                CampaignLog.date_key.in_(list(date_keys)),
                CampaignLog.status.in_(["CLOSED_WIN", "CLOSED_LOSS"]),
                CampaignLog.is_canonical == True
            ).all()
            
            # Map date_key -> status for O(1) lookup
            trade_lookup = {t.date_key: t.status for t in trades}
            
            for snap in snapshots:
                if not snap.timestamp:
                    continue
                date_key = snap.timestamp.strftime("%Y-%m-%d")
                status = trade_lookup.get(date_key)
                if not status:
                    continue
                is_win = (status == "CLOSED_WIN")
                if snap.jewel_gate_open:
                    open_win  += (1 if is_win else 0)
                    open_loss += (0 if is_win else 1)
                else:
                    closed_win  += (1 if is_win else 0)
                    closed_loss += (0 if is_win else 1)
```

### Remediation 2: Secure Frontend JS Initialization with Individual catch Blocks
Prevent single-endpoint errors from breaking the page initialization.

**Proposed Code Modification in `templates/suite_dashboard.html` (lines 620-630)**:
```javascript
    async function initDashboard() {
        const loaders = [
            loadOverview,
            loadMasHistory,
            loadAccuracy,
            loadCosts,
            loadJewel,
            loadAudits,
            loadNewsletters
        ];
        
        // Run loaders in parallel, catching errors individually so all others can complete
        await Promise.all(loaders.map(async (fn) => {
            try {
                await fn();
            } catch (err) {
                console.error(`Dashboard component loading failed for ${fn.name}:`, err);
            }
        }));
    }
```

### Remediation 3: Fix Zero Value Formatting Bug in Frontend
Update the formatter function to verify that the value is not null, undefined, or empty, instead of using standard truthiness.

**Proposed Code Modification in `templates/suite_dashboard.html` (line 360)**:
```javascript
            const fmt = v => {
                if (v === null || v === undefined || v === '') return '—';
                const num = Number(v);
                if (isNaN(num)) return '—';
                return '$' + num.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
            };
```
