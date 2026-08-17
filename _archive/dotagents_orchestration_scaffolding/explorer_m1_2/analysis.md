# Kabroda Executive Dashboard Audit Analysis Report

This report presents a comprehensive frontend/backend audit of the Kabroda Executive Dashboard, specifically targeting the HTML/JS template `templates/suite_dashboard.html` and its associated API endpoints in `main.py`.

---

## Executive Summary

The audit revealed **10 key findings** across rendering bugs, styling omissions, cosmetic defects, N+1 query performance bottlenecks, and database compatibility edge cases.

1. **Performance Bottleneck (N+1 Query)**: The `/api/dashboard/jewel` endpoint queries the database inside a loop for each snapshot, leading to severe latency as historical data scales.
2. **Doughnut Chart Color Mismatch**: The MAS Approval Distribution chart maps `Rejected` to `C.green` due to an array index off-by-one/palette alignment mismatch.
3. **Raw Markdown Display**: Both the newsletter archive and system audits display raw markdown syntax (such as hashes and asterisks) inside the reader modal because no markdown parser (e.g., `marked.js`) is imported or invoked.
4. **Faulty String Interpolation**: String concatenation in both the audit and newsletter modal meta fields produces malformed labels with trailing/double delimiters (e.g., `·  ·`) when certain optional fields are null.
5. **Missing CSS for Key Statuses**: Critical trade and approval statuses (`CLOSED_AT_EXPIRY`, `EXPIRED`, and `MAS_ERROR`) lack corresponding classes in the CSS, rendering them in default body text color and breaking the dashboard's visual scannability.
6. **Fragile Client-Side Error Handling**: If any dashboard API call fails (network error, unauthorized status, or 500 error), the UI gets stuck in a permanent "Loading..." state or displays misleading placeholder messages (such as claiming there are zero audits when in fact the query failed).
7. **Jinja/JS Admin Check Redundancy**: When a non-admin accesses the dashboard, the cost card is faded out, but the REST API is still queried (resulting in a silent 403) and the empty canvas is not cleaned up.
8. **Junk JS Code (Typo)**: The string replacement `.replace(/_/g, '_')` in `loadNewsletters` is a typo that does nothing.
9. **Timezone-Naive vs Timezone-Aware Query Collision**: The backend queries naive datetime columns using aware datetime objects, which can trigger errors depending on the SQL compiler/engine.
10. **Line Chart X-Axis Label Duplication**: Chronological ordering of trades by `closed_at` can result in duplicate `date_key` labels on the PnL line chart X-axis if multiple trades close on the same day.

---

## Detailed Findings & Recommendations

### 1. N+1 Database Query in `/api/dashboard/jewel`
* **Observation**: `main.py` lines 1857-1877 retrieves all `JewelSnapshotLog` rows and then loops over them, querying `CampaignLog` individually for each:
  ```python
  snapshots = db.query(JewelSnapshotLog).filter(JewelSnapshotLog.session_label == "NY_OPEN").all()
  for snap in snapshots:
      ...
      trade = db.query(CampaignLog).filter(...).first()
  ```
* **Impact**: If there are $N$ snapshots, the database performs $N+1$ queries. This causes significant performance degradation as the trading history grows.
* **Recommendation**: Perform a bulk query or in-memory map to reduce this to exactly 2 queries:
  ```python
  # Optimized implementation
  snapshots = db.query(JewelSnapshotLog).filter(JewelSnapshotLog.session_label == "NY_OPEN").all()
  dates = {snap.timestamp.strftime("%Y-%m-%d") for snap in snapshots if snap.timestamp}
  
  trades = db.query(CampaignLog).filter(
      CampaignLog.symbol == "BTC/USDT",
      CampaignLog.date_key.in_(dates),
      CampaignLog.status.in_(["CLOSED_WIN", "CLOSED_LOSS"]),
      CampaignLog.is_canonical == True
  ).all()
  
  trades_by_date = {t.date_key: t for t in trades}
  
  open_win = open_loss = closed_win = closed_loss = 0
  for snap in snapshots:
      if not snap.timestamp: continue
      date_key = snap.timestamp.strftime("%Y-%m-%d")
      trade = trades_by_date.get(date_key)
      if not trade: continue
      is_win = trade.status == "CLOSED_WIN"
      if snap.jewel_gate_open:
          open_win += (1 if is_win else 0)
          open_loss += (0 if is_win else 1)
      else:
          closed_win += (1 if is_win else 0)
          closed_loss += (0 if is_win else 1)
  ```

---

### 2. Doughnut Chart Color Palette Alignment Error (Chart Approval)
* **Observation**: `templates/suite_dashboard.html` lines 329-338 defines the doughnut chart:
  ```javascript
  labels: ['Approved', 'Rejected', 'Waiting', 'Error/Other'],
  datasets: [{
      data: [ap.APPROVED||0, ap.REJECTED||0, ap.WAITING_FOR_15M||0, (ap.MAS_ERROR||0)+(ap.PENDING||0)],
      backgroundColor: [C.cognac, C.green, C.gold, C.amber, C.muted],
      borderColor:     C.panel,
      borderWidth:     3,
  }]
  ```
* **Impact**:
  * `Approved` receives `C.cognac` (OK).
  * `Rejected` receives `C.green` (Severe bug: green represents win rates/closed wins, which visually misleads executives to think rejected trades are positive).
  * `Waiting` receives `C.gold`.
  * `Error/Other` receives `C.amber`.
  * `C.muted` is unused because there are only 4 data points.
* **Recommendation**: Re-align the colors to match their semantic meaning:
  ```javascript
  labels: ['Approved', 'Rejected', 'Waiting', 'Error/Other'],
  datasets: [{
      data: [ap.APPROVED||0, ap.REJECTED||0, ap.WAITING_FOR_15M||0, (ap.MAS_ERROR||0)+(ap.PENDING||0)],
      backgroundColor: [C.cognac, C.red, C.amber, C.muted],
      borderColor:     C.panel,
      borderWidth:     3,
  }]
  ```

---

### 3. Raw Markdown Display in Modal (No Markdown Parser)
* **Observation**: `templates/suite_dashboard.html` lines 568 and 605 write raw markdown text to the modal body:
  ```javascript
  document.getElementById('nlModalBody').textContent = a.audit_md || '(No content)';
  ```
  No markdown parser library (such as `marked.js`) is imported in the template's head.
* **Impact**: Executives are presented with raw markdown characters (`#`, `**`, `-`) rather than formatted headers, bold text, lists, and tables. This looks unpolished and is inconsistent with `templates/macro_war_room.html` which renders markdown correctly.
* **Recommendation**: Import `marked.js` in `templates/suite_dashboard.html` and parse the markdown:
  ```html
  <!-- In <head> -->
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  ```
  ```javascript
  // In JS (safely using innerHTML after marked.parse)
  document.getElementById('nlModalBody').innerHTML = marked.parse(a.audit_md || '(No content)');
  ```

---

### 4. Faulty String Formatting for Null Values in Modals
* **Observation**: String concatenation is used to generate the metadata strings:
  * Audit Modal (lines 565-566):
    ```javascript
    'SYSTEM AUDIT  ·  ' + (a.date_key || '—') + '  ·  ' + (a.created_at ? a.created_at.slice(0, 16).replace('T', ' ') + ' UTC' : '');
    ```
  * Newsletter Modal (lines 602-603):
    ```javascript
    (n.date_key || '—') + '  ·  ' + (n.approval_status || '') + '  ·  ' + (n.publish_status || '');
    ```
* **Impact**:
  * If `a.created_at` is null, the metadata string has a dangling separator: `SYSTEM AUDIT  ·  2026-07-16  ·  `.
  * If `n.approval_status` is null, it produces a double separator: `2026-07-16  ·    ·  PUBLISHED`.
* **Recommendation**: Filter out falsy values before joining:
  ```javascript
  // For Audit Modal
  const timeStr = a.created_at ? a.created_at.slice(0, 16).replace('T', ' ') + ' UTC' : '';
  document.getElementById('nlModalMeta').textContent = 
      ['SYSTEM AUDIT', a.date_key || '—', timeStr].filter(Boolean).join('  ·  ');
      
  // For Newsletter Modal
  document.getElementById('nlModalMeta').textContent = 
      [n.date_key || '—', n.approval_status, n.publish_status].filter(Boolean).join('  ·  ');
  ```

---

### 5. Missing CSS Classes for Particular Trade and Approval Statuses
* **Observation**: The Trade History and Newsletter tables style cells using `s-${st}` classes. However, the stylesheet (lines 53-66) does not define rules for:
  * `CLOSED_AT_EXPIRY` (which is a valid Trade PnL status) -> class `s-closed_at_expiry`.
  * `EXPIRED` (setup expired unfilled) -> class `s-expired`.
  * `MAS_ERROR` (approval status) -> class `s-mas_error`.
* **Impact**: Text is rendered in the default table color (`#c9a880`), losing the critical warning (error) or neutral (expired) color coding that enables quick scanning.
* **Recommendation**: Add appropriate styles to the stylesheet:
  ```css
  .s-closed_at_expiry      { color: #a67c3a; font-weight: 800; } /* Amber / Neutral Close */
  .s-expired              { color: #6b5345; }                   /* Muted / Ignored */
  .s-mas_error            { color: #8c4a3a; font-weight: 800; } /* Red / Alert */
  ```

---

### 6. Client-Side API Error Handling Omissions
* **Observation**: The page relies on fetch calls, but if a request returns `!d.ok` or fails:
  * `loadOverview`, `loadMasHistory`, `loadNewsletters`, and `loadAccuracy` return early and do not update the DOM.
  * The tables display "Loading trade history..." and "Loading newsletter log..." indefinitely.
  * In `loadAudits`, if `!d.ok` is true (such as a 500 error), the UI displays the empty state message: *"No system audits yet. The first will appear..."* instead of indicating an error.
* **Impact**: A server/database failure is masked as "no data" or causes a permanent loading spinner, providing a poor user experience.
* **Recommendation**: Capture failed responses and display clean error messages:
  ```javascript
  // Example for Trade History Table
  if (!d.ok) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty-state" style="color: #8c4a3a;">Failed to load trade history.</td></tr>';
      return;
  }
  ```

---

### 7. Jinja vs. REST API Authorization Discrepancy
* **Observation**: The `costCard` opacity is checked via Jinja rendering `const IS_ADMIN = {{ 'true' if is_admin else 'false' }};`.
  However, if `IS_ADMIN` is false, `loadCosts` returns early. The REST endpoint `/api/dashboard/costs` returns a 403, but the client does not check it, and the canvas remains inside the DOM without being hidden or cleaned.
* **Impact**: Non-admins see a faded-out card with an empty canvas, which looks like a rendering bug rather than a disabled feature.
* **Recommendation**: Hide the cost card entirely if `IS_ADMIN` is false:
  ```javascript
  if (!IS_ADMIN) {
      document.getElementById('costCard').style.display = 'none';
      return;
  }
  ```

---

### 8. Dead Code/Typo in `loadNewsletters()`
* **Observation**: `templates/suite_dashboard.html` line 588:
  ```javascript
  const apSt = (n.approval_status || '').toLowerCase().replace(/_/g, '_');
  ```
* **Impact**: The `.replace(/_/g, '_')` statement is a typo (replaces underscore with underscore) and has no effect. The developer likely meant `.replace(/_/g, '-')`, but because they also defined `.s-waiting_for_15m` in the CSS, it worked by coincidence.
* **Recommendation**: Remove the redundant replacement or correct it to `-`.

---

### 9. Timezone-Naive vs. Timezone-Aware Comparison in Python Queries
* **Observation**: In `main.py` lines 1717-1720 and 1789-1791:
  ```python
  since_7d = datetime.now(timezone.utc) - timedelta(days=7)
  rows = db.query(AgentRunLog).filter(AgentRunLog.created_at >= since_7d).all()
  ```
  `AgentRunLog.created_at` is a naive `DateTime` column, whereas `since_7d` is timezone-aware.
* **Impact**: Depending on the SQL dialect (especially PostgreSQL), comparing naive and aware columns can trigger a database-level compilation or runtime exception (`ProgrammingError`).
* **Recommendation**: Make `since_7d` timezone-naive:
  ```python
  since_7d = datetime.utcnow() - timedelta(days=7)
  ```

---

### 10. Chronological Duplicate Dates on line chart X-Axis
* **Observation**: In `main.py` line 1820:
  ```python
  pnl_rows = db.query(CampaignLog.closed_at, CampaignLog.date_key, ...).order_by(CampaignLog.closed_at).all()
  ```
  If multiple trades close on the same day, `pnl_series` will contain multiple items with the same `date_key`.
* **Impact**: The line chart renders multiple points on the X-axis with identical date labels, causing labels to cluster and overlap, which looks visually messy.
* **Recommendation**: Group/aggregate PnL by date key before returning it, or append a unique index/timestamp to make each X-axis entry distinct.
