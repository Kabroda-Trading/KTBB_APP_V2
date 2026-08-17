# Handoff Report — explorer_m1_3

## 1. Observation
- **N+1 Query Issue**: In `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\main.py` (lines 1857-1877), the `/api/dashboard/jewel` endpoint queries `JewelSnapshotLog` and then executes a loop querying `CampaignLog` for each snapshot:
  ```python
  snapshots = db.query(JewelSnapshotLog).filter(
      JewelSnapshotLog.session_label == "NY_OPEN").all()
  open_win = open_loss = closed_win = closed_loss = 0
  for snap in snapshots:
      if not snap.timestamp:
          continue
      date_key = snap.timestamp.strftime("%Y-%m-%d")
      trade = db.query(CampaignLog).filter(
          CampaignLog.symbol == "BTC/USDT",
          CampaignLog.date_key == date_key,
          CampaignLog.status.in_(["CLOSED_WIN", "CLOSED_LOSS"]),
          CampaignLog.is_canonical == True).first()
  ```
- **Frontend Loader Failures**: In `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\templates\suite_dashboard.html` (lines 620-630), the JavaScript loaders are executed inside a single `Promise.all` block without individual catch blocks or try-catch blocks for 6 out of 7 loaders:
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
- **Falsy Zero Value Formatter**: In `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\templates\suite_dashboard.html` (line 360), currency formatting uses truthiness:
  ```javascript
  const fmt = v => v ? '$' + Number(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}) : '—';
  ```
- **Backend Exception Handling**: In `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\main.py`, all endpoints have try-except blocks:
  ```python
  except Exception as e:
      return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
  ```
- **Global exception handler**: In `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\main.py` (lines 1964-1977), there is a custom `global_exception_handler` for `Exception` returning a fatal system crash HTML page.
- **Asynchronous Task Protection**: In `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\main.py`, tasks such as `run_senior_analyst_scheduler` (lines 272-276), `run_weekly_scheduler` (lines 351-354), `run_daily_4h1h_audit_scheduler`, and `run_outcome_tracker` are wrapped in try-except loops:
  ```python
  except asyncio.CancelledError:
      raise
  except Exception as e:
      print(f"[SCHEDULER] Error: {e}")
      await asyncio.sleep(300)
  ```
- **Database Schema Schema Guard**: In `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\database.py`, `init_db()` runs `ALTER TABLE` statements inside `try-except` blocks.
- **Test execution**: Running `python -m pytest harness/` executed 4 test cases successfully:
  ```
  harness\test_audit_safety.py ....                                        [100%]
  ============================== 4 passed in 0.58s ==============================
  ```

## 2. Logic Chain
1. **Observation 1 (N+1 Query)**: The execution of `db.query(CampaignLog)` inside a loop over `snapshots` means the number of SQL queries scales linearly ($O(N)$) with the size of the `JewelSnapshotLog` table.
2. **Logic**: Under production database volumes, this linear scale will saturate database locks, degrade response times, and eventually cause requests to time out. Since it's executed inside the async controller thread, database bottlenecks will block other concurrent requests, impacting server responsiveness.
3. **Observation 2 (Promise.all)**: The lack of individual try-catch blocks in the loaders, combined with executing them in a single `Promise.all`, means that if any single fetch fails or throws an exception, the entire promise chain will reject.
4. **Logic**: This results in a brittle frontend state where a temporary failure of a non-critical endpoint (such as the admin-only costs endpoint) prevents the remaining components of the dashboard from rendering.
5. **Observation 3 (Currency Formatter)**: The currency formatter checks truthiness (`v`).
6. **Logic**: In JavaScript, the number `0` is falsy. If a price value is `0` or `0.0`, the expression evaluates to the fallback `'—'` rather than the correct formatted currency `"$0.00"`.
7. **Observations 4, 5, 6, 7 (Exception Handling)**: The presence of local try-except blocks on all API routes, task-level exception handling loops in async workers, a global app exception handler, and try-except blocks around migrations guarantees that database, schema, or runtime exceptions are trapped.
8. **Logic**: Because all known exception entry points are wrapped, malformed database entries, missing tables/columns, or bad API responses cannot propagate to the top level and crash the FastAPI process.

## 3. Caveats
- No active penetration testing or database write testing was performed, as this is a read-only investigation.
- It is assumed that the production database uses SQLite (as defined in `database.py` connection URL configuration). If transitioned to PostgreSQL, database connection pool exhaustion could occur during high database lock contention caused by the N+1 query.

## 4. Conclusion
The Kabroda Executive Dashboard is highly resilient against server crashes. The backend endpoints and background workers are well-insulated using try-except guards, preventing malformed inputs or database exceptions from terminating the web server. However, the dashboard contains a critical N+1 query performance bottleneck in the `/api/dashboard/jewel` endpoint and is vulnerable to frontend loading freezes due to the lack of error boundaries in its parallel JavaScript loader block.

## 5. Verification Method
- **Verify Backend Resilience**:
  1. Open a terminal and run `python -m pytest harness/` to verify current test safety checks.
  2. Running the server via `python main.py` or uvicorn, and checking `/api/dashboard/overview`, `/api/dashboard/jewel`, etc., with an empty database to confirm they return status 200 JSON responses containing zeroed fields without throwing exceptions.
- **Verify N+1 Query Fix**:
  Inspect `main.py` lines 1857-1877 to confirm if the query is optimized using a batch filter and dictionary lookup (Remediation 1).
- **Verify Frontend Exception Fix**:
  Inspect `templates/suite_dashboard.html` to confirm that the `initDashboard()` loader block wraps calls in individual catch statements to prevent cascading failures (Remediation 2).
