# Audit Report: Kabroda Executive Dashboard KPI Calculations & Null-Safety

**Date**: July 16, 2026
**Audited Files**: `main.py`, `database.py`
**Audited Database**: `kabroda.db`

---

## Executive Summary
This audit evaluated the `/api/dashboard/*` endpoints in `main.py`, database models in `database.py`, and database schema/records in `kabroda.db`. We identified **five critical/major issues** (including a fatal timezone comparison bug on PostgreSQL, logical discrepancies in KPI calculations, and database data anomalies) and **two minor null-safety edge cases** that could cause application crashes or display corrupt metrics.

---

## Consensus Findings & Audit Matrix

| Finding / Bug | Severity | Impact | Location |
|---|---|---|---|
| **1. Timezone-Naive vs Aware Comparison Mismatch** | **Critical** | Fatal crash (500 Error) in production on PostgreSQL. | `main.py` lines 1716, 1719, 1790 (cost queries), and line 557 (outcome tracker). |
| **2. Inconsistent Win Rate Calculation (Ignoring Expiry)** | **Major** | Win rate metric is mathematically inconsistent with total trades and Net R. | `main.py` line 1700 (overview KPI). |
| **3. Canonical Candidate Pollution (Data Anomaly)** | **Major** | 4H and 1H candidates distort 15M MAS metrics (e.g. approval rate becomes 33.3% instead of 100%). | `kabroda.db` (`campaign_logs` rows 1 & 2). |
| **4. Lack of Canonical Filter in Accuracy Panel** | **Major** | Non-canonical or legacy test trades can pollute directional accuracy metrics. | `main.py` line 1762 (`grade_rows_4h1h`). |
| **5. Null-Safety Bug in Costs Stacked Bar** | **Medium** | Null values in `estimated_cost_usd` will cause a crash (TypeError) in the cost dashboard. | `main.py` line 1796 (`api_dashboard_costs`). |
| **6. Null-Safety Bug in Cost Dates** | **Medium** | Null values in `created_at` will cause an AttributeError crash. | `main.py` line 1795 (`api_dashboard_costs`). |
| **7. Empty Cumulative Chart Mismatch** | **Low** | Closed trades with `closed_at = NULL` are omitted from the cumulative chart, causing a visual mismatch. | `main.py` line 1823 (`pnl_rows`), `kabroda.db` data. |

---

## Detailed Analysis & Evidence Chain

### 1. Timezone-Naive vs. Aware Comparison Mismatch (Critical)
* **Observation**: 
  - `AgentRunLog.created_at` is defined as a timezone-naive `DateTime` column in `database.py` (lines 660-680).
  - In `main.py` (lines 1715, 1789), the cost query creates `since_7d = datetime.now(timezone.utc) - timedelta(days=7)` which is timezone-aware.
  - The query then filters using `AgentRunLog.created_at >= since_7d`.
  - In the outcome tracker (lines 552-558), `cutoff = now - timedelta(hours=4)` (timezone-aware) is compared against `DecisionJournal.timestamp` (timezone-naive).
* **Logic**: While SQLite silently ignores timezone offsets during string conversion, PostgreSQL (used in production on Render) strictly prohibits comparing a naive timestamp (`TIMESTAMP WITHOUT TIME ZONE`) with an offset-aware timestamp (`TIMESTAMP WITH TIME ZONE`). This raises an exception: `operator does not exist: timestamp without time zone >= timestamp with time zone` or similar.
* **Risk**: Complete crash of the dashboard overview `/api/dashboard/overview`, the cost endpoint `/api/dashboard/costs`, and silent failures in the outcome tracker background task `run_outcome_tracker()`.

### 2. Inconsistent Win Rate Calculation (Major)
* **Observation**:
  - The win rate is calculated in `/api/dashboard/overview` as:
    ```python
    wins   = db.query(func.count(CampaignLog.id)).filter(CampaignLog.symbol == "BTC/USDT", CampaignLog.status == "CLOSED_WIN", CampaignLog.is_canonical == True).scalar() or 0
    losses = db.query(func.count(CampaignLog.id)).filter(CampaignLog.symbol == "BTC/USDT", CampaignLog.status == "CLOSED_LOSS", CampaignLog.is_canonical == True).scalar() or 0
    win_rate = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0.0
    ```
  - The net R calculation includes `CLOSED_AT_EXPIRY` trades:
    ```python
    CampaignLog.status.in_(["CLOSED_WIN", "CLOSED_LOSS", "CLOSED_AT_EXPIRY"])
    ```
* **Logic**: An expired trade resolved at session end represents a real completed trade with a real fractional realized PnL. If a trade closes at expiry with a profit of `+0.5R`, it is counted in `Net R Lifetime` but excluded from the `win_rate` denominator and numerator. If it closes with a loss of `-0.3R`, it decreases `Net R Lifetime` but is not counted as a loss.
* **Risk**: The reported win rate will be mathematically inconsistent with the total set of trades and the reported cumulative Net R.

### 3. Canonical Candidate Pollution & Missing Timeframe Filter (Major)
* **Observation**:
  - Examining `kabroda.db`, the two candidate rows (4H and 1H) are written with `is_canonical = True` (or 1 in SQLite).
  - The `/api/dashboard/overview` approved rate queries count canonical logs:
    ```python
    total      = db.query(func.count(CampaignLog.id)).filter(CampaignLog.symbol == "BTC/USDT", CampaignLog.is_canonical == True).scalar() or 0
    approved   = db.query(func.count(CampaignLog.id)).filter(CampaignLog.symbol == "BTC/USDT", CampaignLog.mas_approval_status == "APPROVED", CampaignLog.is_canonical == True).scalar() or 0
    approved_rate = round(approved / total * 100, 1) if total > 0 else 0.0
    ```
* **Logic**: 4H and 1H candidates are created by the gravity engine and saved with `mas_approval_status = "4H_CANDIDATE"` or `"1H_CANDIDATE"`. Since they are canonical in `kabroda.db`, they inflate `total` (denominator) to 3, but cannot be counted in `approved` (numerator). This drops the apparent approved rate from 100% (1/1 for 15M) down to 33.3% (1/3). Furthermore, they pollute the PnL cumulative line and jewel gate analysis which also only filter on `is_canonical == True`.
* **Risk**: Incorrect and distorted MAS statistics, PnL tracking, and Jewel gate correlation metrics on the dashboard.

### 4. Lack of Canonical Filter in Accuracy Panel (Major)
* **Observation**:
  - The accuracy query `grade_rows_4h1h` filters by symbol, timeframe, status, realized PnL, and grade, but fails to check `CampaignLog.is_canonical == True`.
* **Logic**: By design, production-quality 4H/1H rows should be marked canonical if they are meant to be audited. If non-canonical test or legacy data exists in the database, it will contaminate the accuracy metrics. If candidate rows are kept non-canonical by design (as stated in `audit_ai.py` lines 61-70), then the panel will show correct candidate statistics but may capture draft/replayed records if they are not cleaned up.
* **Risk**: Pollution of directional accuracy metrics with non-canonical/test data.

### 5. Null-Safety Bug in Costs Stacked Bar (Medium)
* **Observation**:
  - In `main.py` line 1796, `daily[day][row.agent_name] += row.estimated_cost_usd` is evaluated for every success log.
  - According to `PRAGMA table_info(agent_run_log)`, `estimated_cost_usd` is nullable (`NotNull: 0`, default is `None`).
* **Logic**: If an `estimated_cost_usd` field is stored as `NULL` in the database, SQLAlchemy passes it as `None`. In Python, adding `None` to a float raises a `TypeError` and crashes the endpoint.
* **Risk**: A single run log with a null cost will crash `/api/dashboard/costs` for all admin requests.

### 6. Potential Crash in Cost Dates (Medium)
* **Observation**:
  - In `main.py` line 1795, `day = row.created_at.strftime("%m/%d")` is evaluated.
  - The column `created_at` in `agent_run_log` is nullable.
* **Logic**: If any run log has a null `created_at`, calling `strftime` on `None` raises `AttributeError: 'NoneType' object has no attribute 'strftime'`.
* **Risk**: Admin cost dashboard endpoint crashes.

### 7. Empty Cumulative Performance Chart (Low)
* **Observation**:
  - The cumulative performance chart queries `CampaignLog.closed_at.isnot(None)` to order and build the PnL series.
  - In `kabroda.db`, all closed campaign logs have `closed_at = NULL`.
* **Logic**: The query returns 0 rows. The cumulative PnL line is rendered completely empty on the UI, despite the KPI card showing a non-zero `Net R Lifetime` (which only sums PnL and does not check `closed_at`).
* **Risk**: Visual discrepancy and missing data on the dashboard chart.

---

## Recommendations & Proposed Fixes

### 1. Fix Timezone Comparisons (Critical)
Always strip timezone offsets or compare timezone-naive values in database queries where columns are naive.
```python
# Convert to naive UTC datetime
since_7d = (datetime.now(timezone.utc) - timedelta(days=7)).replace(tzinfo=None)
cutoff = (datetime.now(timezone.utc) - timedelta(hours=4)).replace(tzinfo=None)
```

### 2. Standardize Win Rate Calculation (Major)
Align the win rate calculation with `audit_ai.py` and include `CLOSED_AT_EXPIRY` trades by checking positive realized PnL:
```python
resolved_q = db.query(CampaignLog).filter(
    CampaignLog.symbol == "BTC/USDT",
    CampaignLog.session_timeframe == "15M",
    CampaignLog.status.in_(["CLOSED_WIN", "CLOSED_LOSS", "CLOSED_AT_EXPIRY"]),
    CampaignLog.is_canonical == True,
    CampaignLog.realized_pnl.isnot(None)
)
total_resolved = resolved_q.count()
wins = resolved_q.filter(CampaignLog.realized_pnl > 0).count()
win_rate = round(wins / total_resolved * 100, 1) if total_resolved > 0 else 0.0
```

### 3. Exclude 4H/1H Timeframes from 15M Dashboard Metrics (Major)
Add `session_timeframe == "15M"` to the overview total/approved counts, PnL series, and Jewel gate queries.
```python
total = db.query(func.count(CampaignLog.id)).filter(
    CampaignLog.symbol == "BTC/USDT", 
    CampaignLog.is_canonical == True,
    CampaignLog.session_timeframe == "15M"
).scalar() or 0
```

### 4. Apply Null Guards in Cost Dashboard (Medium)
```python
day = (row.created_at or datetime.utcnow()).strftime("%m/%d")
daily[day][row.agent_name] += (row.estimated_cost_usd or 0.0)
```

### 5. Correct Database is_canonical values (Major)
Run a script to correct the canonical flags on the 4H/1H rows:
```sql
UPDATE campaign_logs SET is_canonical = 0 WHERE session_timeframe IN ('4H', '1H');
```
