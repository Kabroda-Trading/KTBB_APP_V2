# Scope: Milestone M1 - Dashboard Audit & Fix

## Architecture
Milestone M1 focuses on the existing Executive Dashboard system:
- **API Endpoints**: `/api/dashboard/*` defined in `main.py`
- **UI Template**: `templates/suite_dashboard.html` rendered at `/suite/dashboard`
- **Database Access**: Queries database `kabroda.db` using schemas/utilities in `database.py`

## Milestones / Tasks
| # | Name | Scope | Dependencies | Status | Conv ID / Agent |
|---|------|-------|-------------|--------|-----------------|
| M1.1 | Detailed Exploration/Audit | Perform code and runtime exploration to find bugs, incorrect calculations, null-safety issues, formatting, and edge cases in the dashboard UI/API. | None | DONE | ec915a9c-ddf6-4154-801d-7ecd60d4adb3, fafdb3b8-a951-48d4-8e37-c541df1c8925, 571b9a0b-4132-450a-869e-9d745d3df84d |
| M1.2 | Fix Implementation | Implement fixes for the identified 15 dashboard bugs across `main.py` and `templates/suite_dashboard.html`. | M1.1 | IN_PROGRESS | 41c7fd59-9928-4e9a-b06f-e84974b27208 |
| M1.3 | Verification & Review | Run validation tests and verify code logic via independent reviewers, critics, challengers, and forensic auditor. | M1.2 | PLANNED | TBD |

## Identified Bugs & Fix Plan
1. **Timezone Offset Mismatch in Queries**: Convert aware datetimes to naive datetimes in `main.py` (lines 1716, 1719, 1790, 557, etc.) by using `.replace(tzinfo=None)` or `utcnow()`.
2. **Inconsistent Win Rate Logic**: Overview win rate calculation should include `CLOSED_AT_EXPIRY` in total resolved count, and wins should check `realized_pnl > 0`.
3. **Missing Timeframe Filter**: Add `session_timeframe == "15M"` filter to overview stats, PnL series, and Jewel gate queries.
4. **Lack of Canonical Filter in Accuracy Panel**: Add `is_canonical == True` to accuracy data queries in `main.py` (line 1762).
5. **Null-Safety Bug in Costs bar chart**: Use default `0.0` if `estimated_cost_usd` is null in `main.py` line 1796.
6. **Null-Safety Bug in Cost dates**: Fall back to current date if `created_at` is null in `main.py` line 1795.
7. **Empty PnL Line Chart**: Allow falling back to another field or handling null `closed_at` values in database and query (e.g. fallback to `updated_at`). Also update SQLite `kabroda.db` `closed_at` field for closed trades.
8. **N+1 Query in `/api/dashboard/jewel`**: Batch query campaign logs by dates and map them, optimizing performance.
9. **Doughnut Chart Color Mismatch**: Map 'Rejected' to red and align palettes in Chart.js initialization in `templates/suite_dashboard.html`.
10. **Raw Markdown Display**: Import `marked.js` library in HTML and use it to parse the markdown contents of newsletter/audit reader modals.
11. **Faulty Metadata String Interpolation**: Avoid dangling or double ` · ` delimiters by filtering falsy values before joining with `  ·  `.
12. **Missing CSS Classes for Statuses**: Add `.s-closed_at_expiry`, `.s-expired`, and `.s-mas_error` class styles in `templates/suite_dashboard.html`.
13. **Fragile JS Load catch Blocks**: Wrap JS loader functions inside `Promise.all` with individual catch blocks to prevent a single failure from breaking the dashboard. Also show clean user-facing error messages in the DOM if a request fails.
14. **Jinja/JS Admin Check Redundancy**: Hide the cost card container completely if `IS_ADMIN` is false.
15. **Junk JS Code Typo**: Remove/fix `.replace(/_/g, '_')`.
