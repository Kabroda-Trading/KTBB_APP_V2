# Handoff Report: Kabroda Executive Dashboard Audit

## 1. Observation
We examined `main.py`, `database.py`, and `kabroda.db` in `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2`.

* **Timezone Offset Mismatch in Cost and Outcome Queries**:
  - In `database.py`, `AgentRunLog.created_at` (lines 660-680) and `DecisionJournal.timestamp` (line 627) are defined without timezone settings, compiling to timezone-naive columns:
    ```python
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    ```
  - In `main.py` lines 1715, 1789, and 553, timezone-aware datetimes are constructed and compared:
    ```python
    since_7d = datetime.now(timezone.utc) - timedelta(days=7)
    ... filter(AgentRunLog.created_at >= since_7d)
    
    cutoff = now - timedelta(hours=4)  # where now = datetime.now(timezone.utc)
    ... filter(DecisionJournal.timestamp < cutoff)
    ```

* **Excluding Expiry Status in Win Rate**:
  - In `main.py` lines 1698-1700:
    ```python
    wins   = db.query(func.count(CampaignLog.id)).filter(CampaignLog.symbol == "BTC/USDT", CampaignLog.status == "CLOSED_WIN", CampaignLog.is_canonical == True).scalar() or 0
    losses = db.query(func.count(CampaignLog.id)).filter(CampaignLog.symbol == "BTC/USDT", CampaignLog.status == "CLOSED_LOSS", CampaignLog.is_canonical == True).scalar() or 0
    win_rate = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0.0
    ```
  - However, in line 1711, net R includes expired trades:
    ```python
    CampaignLog.status.in_(["CLOSED_WIN", "CLOSED_LOSS", "CLOSED_AT_EXPIRY"])
    ```

* **Pollution by 4H/1H Candidate Rows in database**:
  - Running a raw query on `kabroda.db` showed that rows 1 and 2 (timeframes `4H` and `1H`) have `is_canonical = True` (or `1`):
    ```
    1 BTC/USDT 4H 4H_CANDIDATE CLOSED_LOSS True 2026-07-03 18:42:25.815521 None
    2 BTC/USDT 1H 1H_CANDIDATE CLOSED_WIN True 2026-07-04 18:42:25.815521 None
    ```
  - In `main.py`, the dashboard endpoints filter on `is_canonical == True` to query 15M metrics but do not filter on `session_timeframe == "15M"`.

* **Missing is_canonical filter in Accuracy Panel**:
  - In `main.py` line 1762:
    ```python
    grade_rows_4h1h = db.query(CampaignLog.kinematic_grade, CampaignLog.realized_pnl).filter(
        CampaignLog.symbol == "BTC/USDT",
        CampaignLog.session_timeframe.in_(["4H", "1H"]),
        CampaignLog.kinematic_grade.isnot(None),
        CampaignLog.status.in_(["CLOSED_WIN", "CLOSED_LOSS", "CLOSED_AT_EXPIRY"]),
        CampaignLog.realized_pnl.isnot(None),
    ).all()
    ```

* **Null-Safety Cost Accumulation**:
  - In `main.py` lines 1795-1796:
    ```python
    day = row.created_at.strftime("%m/%d")
    daily[day][row.agent_name] += row.estimated_cost_usd
    ```
  - `estimated_cost_usd` is nullable (`NotNull: 0`) and `created_at` is nullable.

* **Null closed_at fields**:
  - SQLite query showed `closed_at` is `None` for all rows, but `main.py` line 1820 queries `CampaignLog.closed_at.isnot(None)` to build the cumulative series.

## 2. Logic Chain
1. Comparing timezone-aware values (`since_7d`, `cutoff`) with timezone-naive columns (`AgentRunLog.created_at`, `DecisionJournal.timestamp`) is syntactically fine in SQLite but throws type/operator mismatch exceptions in PostgreSQL. Thus, `/api/dashboard/overview`, `/api/dashboard/costs`, and `run_outcome_tracker()` will fail in production.
2. An expired trade (`CLOSED_AT_EXPIRY`) has realized fractional PnL. Excluding it from the win rate while including it in Net R creates a mathematical discrepancy where the win rate does not reflect actual trading profitability ratio.
3. 4H and 1H candidate rows should have `is_canonical = False` to prevent them from showing up in 15M KPIs. Because rows 1 and 2 in `kabroda.db` have `is_canonical = True`, they pollute the overview session count and drop the apparent approval rate from 100% to 33.3% since they have `mas_approval_status = "4H_CANDIDATE"` / `"1H_CANDIDATE"` (which is not `"APPROVED"`).
4. Missing timeframe filters on `/api/dashboard/overview`, `/api/dashboard/mas-history`, and `/api/dashboard/jewel` makes them vulnerable to 4H/1H canonical pollution.
5. In `/api/dashboard/costs`, a null value in `estimated_cost_usd` will throw a `TypeError` on `+=`, and a null in `created_at` will throw an `AttributeError` on `strftime`. Since these are nullable columns, this is a crash hazard.
6. Cumulative performance line chart queries `closed_at.isnot(None)`. Since all closed trades in `kabroda.db` have `closed_at = NULL`, the PnL line chart will render completely empty, while the Net R KPI card shows the correct non-zero Net R.

## 3. Caveats
We did not test these queries directly on PostgreSQL since we are in a read-only local environment. However, offset-naive vs offset-aware datetime comparison errors are standard behaviors for Python/SQLAlchemy/PostgreSQL drivers. We assumed `is_canonical` should always be `False` for 4H/1H candidate rows as documented in `audit_ai.py`.

## 4. Conclusion
The dashboard endpoints and outcome tracker have major bugs that will cause production crashes under PostgreSQL and mathematical discrepancies in KPI reporting. These can be solved by (1) converting query datetime offsets to naive, (2) adding timeframe filters and aligning win rate calculations, and (3) adding null-safety checks in the cost query.

## 5. Verification Method
1. Run `verify_math.py` inside `.agents/explorer_m1_1/` to verify the mathematical calculations.
2. Check `main.py` lines 1716, 1719, 1790, and 553 to verify timezone aware-naive comparison operations.
3. Inspect `main.py` line 1762 to verify the lack of `is_canonical` filtering in the accuracy query.
