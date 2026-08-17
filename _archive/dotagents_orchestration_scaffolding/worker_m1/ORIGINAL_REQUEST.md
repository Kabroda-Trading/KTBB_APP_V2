## 2026-07-16T01:21:16Z
You are a versatile worker (teamwork_preview_worker) with loadable domain expertise.
Your working directory is: C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\.agents\worker_m1
Your task is to implement fixes for the Kabroda Executive Dashboard (M1).
Refer to PROJECT.md: C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\PROJECT.md
Refer to SCOPE.md: C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\.agents\sub_orch_m1\SCOPE.md

Please make the following changes in the codebase:
1. **Timezone Offset Mismatch in Queries**: Convert aware datetimes to naive datetimes in `main.py` (lines 1716, 1719, 1790, 557, etc.) by using `.replace(tzinfo=None)` or `utcnow()`.
2. **Inconsistent Win Rate Logic**: Overview win rate calculation should include `CLOSED_AT_EXPIRY` in total resolved count, and wins should check `realized_pnl > 0`.
3. **Missing Timeframe Filter**: Add `session_timeframe == "15M"` filter to overview stats, PnL series, and Jewel gate queries.
4. **Lack of Canonical Filter in Accuracy Panel**: Add `is_canonical == True` to accuracy data queries in `main.py` (line 1762).
5. **Null-Safety Bug in Costs bar chart**: Use default `0.0` if `estimated_cost_usd` is null in `main.py` line 1796.
6. **Null-Safety Bug in Cost dates**: Fall back to current date if `created_at` is null in `main.py` line 1795.
7. **Empty PnL Line Chart**: Allow falling back to another field or handling null `closed_at` values in database and query (e.g. fallback to `updated_at` or `created_at`). Also, update SQLite `kabroda.db` `closed_at` field for closed trades if needed using SQL execution.
8. **N+1 Query in `/api/dashboard/jewel`**: Batch query campaign logs by dates and map them, optimizing performance.
9. **Doughnut Chart Color Mismatch**: Map 'Rejected' to red and align palettes in Chart.js initialization in `templates/suite_dashboard.html`.
10. **Raw Markdown Display**: Import `marked.js` library in HTML and use it to parse the markdown contents of newsletter/audit reader modals.
11. **Faulty Metadata String Interpolation**: Avoid dangling or double ` · ` delimiters by filtering falsy values before joining with `  ·  `.
12. **Missing CSS Classes for Statuses**: Add `.s-closed_at_expiry`, `.s-expired`, and `.s-mas_error` class styles in `templates/suite_dashboard.html`.
13. **Fragile JS Load catch Blocks**: Wrap JS loader functions inside `Promise.all` with individual catch blocks to prevent a single failure from breaking the dashboard. Also show clean user-facing error messages in the DOM if a request fails.
14. **Jinja/JS Admin Check Redundancy**: Hide the cost card container completely if `IS_ADMIN` is false.
15. **Junk JS Code Typo**: Remove/fix `.replace(/_/g, '_')`.

MANDATORY INTEGRITY WARNING:
> DO NOT CHEAT. All implementations must be genuine. DO NOT
> hardcode test results, create dummy/facade implementations, or
> circumvent the intended task. A Forensic Auditor will independently
> verify your work. Integrity violations WILL be detected and your
> work WILL be rejected.

After making changes:
- Run builds / tests or check if server starts correctly.
- Verify everything works as intended.
- Write a detailed handoff report `handoff.md` and notify the sub-orchestrator (ID: 51cfc87e-9770-47dc-b09a-f76e59729362).
