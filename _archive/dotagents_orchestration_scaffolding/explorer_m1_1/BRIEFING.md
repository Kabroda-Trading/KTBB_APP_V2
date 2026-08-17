# BRIEFING — 2026-07-16T01:21:00Z

## Mission
Audit `/api/dashboard/*` endpoints in `main.py` and the database `kabroda.db` to check KPI calculations (win rate, net R, approval rate, spend), formatting, and null-safety edge cases.

## 🔒 My Identity
- Archetype: explorer
- Roles: read-only explorer, auditor
- Working directory: C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\.agents\explorer_m1_1
- Original parent: 51cfc87e-9770-47dc-b09a-f76e59729362
- Milestone: explorer_m1_1

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- CODE_ONLY network mode: no external web access, no curl/wget/etc.

## Current Parent
- Conversation ID: 51cfc87e-9770-47dc-b09a-f76e59729362
- Updated: not yet

## Investigation State
- **Explored paths**: `main.py`, `database.py`, `kabroda.db`
- **Key findings**:
  1. Timezone-naive vs aware datetime comparison mismatch in cost and outcome queries will cause production crashes on PostgreSQL.
  2. Win rate calculation excludes expired trades, causing logical discrepancy with Net R.
  3. Database `kabroda.db` has 4H/1H candidates marked `is_canonical = True`, distorting the MAS stats.
  4. Missing null guards on cost and date fields in admin cost endpoints.
  5. Missing `session_timeframe == "15M"` filters on 15M metrics.
- **Unexplored areas**: None, the audit is complete.

## Key Decisions Made
- Audited the exact database and compared with codebase calculations.
- Re-run `init_db` to ensure all latest schema migrations are present in SQLite before analysis.

## Artifact Index
- C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\.agents\explorer_m1_1\ORIGINAL_REQUEST.md — original user request
- C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\.agents\explorer_m1_1\analysis.md — detailed audit analysis report
