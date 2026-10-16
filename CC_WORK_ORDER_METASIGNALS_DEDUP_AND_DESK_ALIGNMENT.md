# CC_WORK_ORDER_METASIGNALS_DEDUP_AND_DESK_ALIGNMENT.md
## Turnkey Specification: Alert Deduplication, Single-Position-Per-Symbol Gate & Paper Desk Order Cleanup

**Status:** P0 Execution Specification  
**Authority:** Andy Directive (2026-10-16 15:37 CT)  
**Origin:** `Kabroda Meta Signals AI` (DeepSeek / Antigravity)  
**Target Host:** `KTBB_app_v2` (`kabroda.com` on Render)  
**Target Files:**
1. `metasignals_triage.py` (Single-position-per-symbol gate)
2. `main.py` (5-minute sliding-window ingest deduplication)
3. `templates/metasignals_desk.html` / `metasignals_paper_engine.py` (Duplicate order cancellation & desk balance reconciliation)
4. `tests/test_metasignals_paper.py` (Unit regression test coverage)

---

## 1. Executive Summary & Root Cause Analysis

### The Incident:
At **10/16/2026, 1:03:12 PM**, MetaSignals fired a `LINK | USDT 6H Long (Strategy 2.0 G2)` alert. The Kabroda Paper Desk ingested and approved the signal, but opened **TWO duplicate paper positions simultaneously**:
- Both orders: `LINKUSDT`, `MARKET`, Entry `12.314`, Stop `11.81388`, TP1 `12.662`, TP2 `13.19`, Qty `103.2811`.
- Total risk allocated: **$100.00 (206.56 LINK)** instead of the intended **$50.00 (103.28 LINK)**.

### Root Cause 1: Discord Multi-Channel Broadcast
MetaSignals categorizes signals into multiple Discord channels:
- `#mafioso-majors` (monitors large-caps: BTC, ETH, SOL, LINK, etc.)
- `#mafioso-long-htf` (monitors long setups on 4H/6H/8H/1D)
Because LINK is a major and 6H is a high timeframe, MetaSignals broadcast the exact same alert payload to **both** channels simultaneously. The Raspberry Pi bridge (`bridge.py`) monitors both channels, received both messages, and forwarded both to `POST /api/metasignals/webhook` within milliseconds.

### Root Cause 2: Missing Idempotency & Per-Symbol Concurrency Gate
1. `main.py`'s `_handle_metasignals_ingest` had no sliding-window deduplication on incoming alerts.
2. `metasignals_triage.py` only checked the global portfolio cap (`open_count < 5`), which passed because prior trades had closed. It had **zero checks** verifying whether an active trade was already open on that specific symbol (`symbol == order.symbol`).

---

## 2. Technical Implementation Work Order for Claude Code

### Part A: Single-Position-Per-Symbol Gate (`metasignals_triage.py`)
In `evaluate_alert()`, immediately following the global concurrency cap check (~line 621), enforce that only one active order can exist per symbol at any time:

```python
    # ------------------------------------------------------------------
    # Per-Symbol Concurrency Gate: only one active trade per symbol
    # Prevents multi-channel cross-posts or repeated rapid alerts from
    # doubling up risk on the exact same asset.
    # ------------------------------------------------------------------
    if db is not None:
        try:
            from database import MetaSignalsPaperOrder
            existing_symbol_order = db.query(MetaSignalsPaperOrder).filter(
                MetaSignalsPaperOrder.symbol == symbol,
                MetaSignalsPaperOrder.state.in_(("OPEN", "HALF_CLOSED_BE", "PENDING_LIMIT"))
            ).first()
            if existing_symbol_order is not None:
                out.update(
                    verdict="NO",
                    reason="VETO_ALREADY_OPEN_POSITION",
                    existing_order_id=existing_symbol_order.id
                )
                return out
        except Exception as e:
            print(f"[METASIGNALS] per-symbol concurrency check failed closed: {e}")
            out.update(verdict="NO", reason="VETO_ALREADY_OPEN_POSITION")
            return out
```

---

### Part B: 5-Minute Ingest Deduplication (`main.py`)
In `main.py` inside `_handle_metasignals_ingest()`, before performing expensive MTF candle fetches and triage:
1. Check `message_id`: if `message_id` matches an existing `MetaSignalsRawAlert` row where `created_at >= now - 15 minutes`, discard as duplicate.
2. Check parsed signature: if a `MetaSignalsPlan` with identical `(symbol, side, timeframe, strategy)` was created within the last 5 minutes (300 seconds), return:
```python
    # 5-minute sliding deduplication window
    recent_cutoff = datetime.datetime.utcnow() - datetime.timedelta(seconds=300)
    existing_recent_plan = db.query(_MetaSignalsPlan).filter(
        _MetaSignalsPlan.symbol == alert["symbol"],
        _MetaSignalsPlan.side == alert.get("side", "BUY"),
        _MetaSignalsPlan.timeframe == alert.get("timeframe"),
        _MetaSignalsPlan.strategy == alert.get("strategy"),
        _MetaSignalsPlan.created_at >= recent_cutoff
    ).first()

    if existing_recent_plan is not None:
        db.commit()
        return JSONResponse({
            "status": "duplicate",
            "message": "Duplicate alert ignored (already ingested within 5m window)",
            "existing_plan_id": existing_recent_plan.id,
            "parsed": True,
            "verdict": "NO",
            "reason": "VETO_DUPLICATE_ALERT_WINDOW"
        })
```

---

### Part C: Database Cleanup for the Duplicate LINK Paper Position
On the production Render instance, two LINK orders exist in state `OPEN`.
Create a standalone one-off cleanup script or administrative endpoint to cancel the redundant second order:
```python
# cleanup_duplicate_link.py
from database import SessionLocal, MetaSignalsPaperOrder, MetaSignalsPaperAccount

db = SessionLocal()
orders = db.query(MetaSignalsPaperOrder).filter(
    MetaSignalsPaperOrder.symbol == "LINKUSDT",
    MetaSignalsPaperOrder.state == "OPEN"
).order_by(MetaSignalsPaperOrder.id.asc()).all()

if len(orders) > 1:
    duplicate = orders[1] # Keep order #1, void order #2
    account = db.query(MetaSignalsPaperAccount).filter_by(id=duplicate.account_id).first()
    duplicate.state = "CANCELLED_DUPLICATE"
    duplicate.realized_pnl = 0.0
    duplicate.remaining_qty = 0.0
    # Restore any reserved fees/cash if applicable
    db.commit()
    print(f"Voided duplicate order #{duplicate.id}. Active orders for LINKUSDT: 1.")
```

---

### Part D: Regression Unit Tests (`tests/test_metasignals_paper.py`)
Add 2 unit tests:
1. `test_per_symbol_concurrency_veto`: Verify that when an order is in state `OPEN` for `LINKUSDT`, a second alert for `LINKUSDT` receives verdict `NO` and reason `VETO_ALREADY_OPEN_POSITION`.
2. `test_ingest_sliding_window_deduplication`: Verify that posting two identical payloads within 5 minutes results in the second payload returning status `duplicate`.

---

## 3. Deployment & Render Sync

1. Once Claude Code implements Parts A, B, and C, run `pytest tests/test_metasignals_paper.py` to confirm all suite tests pass.
2. Commit and push to GitHub.
3. Trigger deploy on Render to ensure that:
   - The Study 6 BBWP Volatility Exhaustion exit (`commit ff65fcd`) is active.
   - The Per-Symbol Concurrency Gate & Ingest Dedup are active.
   - The duplicate LINK position is cleaned up.
