# Implementation Plan: Signal Performance Tracker + System Audit

## Status: CODE WRITTEN — Ready for CC Review

The code changes are complete. Three files modified:

1. **`database.py`** — New `SignalPerformanceLog` model added
2. **`main.py`** — New `POST /api/signal/log` endpoint added
3. **`SIGNAL_PERFORMANCE_TRACKER.md`** — Local reference file with 5 backfilled entries

## What Was Built

### Database Model: `SignalPerformanceLog`

New table `signal_performance_log` with columns for:
- Signal identity (source, symbol, direction, entry/stop/TP prices, timeframe)
- Price action regime (TRENDING / RANGING / COMPRESSING / EXPANDING)
- Full indicator snapshot (JSON blob — all TFs, all indicators)
- Composite signals (confluence_score, jewel_gate_open, etc. — queryable columns)
- Gravity levels (nearest support/resistance)
- Kabroda read text
- Outcome tracking (TPs hit, stop hit, max excursion, price action result)
- Post-mortem text
- Timestamps (signal_timestamp, created_at, updated_at)

**Idempotency:** `(source, symbol, direction, signal_timestamp)` is unique. Duplicate POSTs return the existing row.

**Table creation:** Handled by `Base.metadata.create_all()` on deploy — no Alembic, no migration script needed.

### API Endpoint: `POST /api/signal/log`

**Auth:** `X-API-Key` header must match `SIGNAL_API_KEY` env variable. Uses `hmac.compare_digest` for constant-time comparison. Fail-closed: if env var is unset, all requests return 401.

**Validation:** Pydantic `SignalLogRequest` model validates all fields. `signal_timestamp` must be ISO 8601. `symbol` is normalized via `_normalize_symbol()` from `market_data.py` (handles BTC, BTCUSDT, ETHUSDT, etc. → BTC/USDT format).

**Idempotency check:** Before insert, checks for existing row with same (source, symbol, direction, signal_timestamp). Returns `{"duplicate": true}` if found.

**Response:** `{"ok": true, "id": <pk>, "duplicate": false}` on success.

### Files Changed

| File | Change |
|------|--------|
| [database.py](file:///c:/Users/Shadow/OneDrive/Desktop/KTBB_app_v2/database.py) | Added `SignalPerformanceLog` class (lines 1349–1415) |
| [main.py](file:///c:/Users/Shadow/OneDrive/Desktop/KTBB_app_v2/main.py) | Added `import hmac` (line 8), `SignalPerformanceLog` to import (line 48), `SignalLogRequest` model + endpoint (lines 1514–1616) |
| [SIGNAL_PERFORMANCE_TRACKER.md](file:///c:/Users/Shadow/OneDrive/Desktop/KTBB_app_v2/SIGNAL_PERFORMANCE_TRACKER.md) | Local reference file with 5 backfilled entries |

## Deployment Steps

1. **CC reviews the code** — all changes are in the three files above
2. **Set env variable** — In Render.com dashboard, add `SIGNAL_API_KEY` with a secure random value
3. **Commit and push** to GitHub — Render.com auto-deploys
4. **I POST a test payload** to verify the endpoint works
5. **You check the database** to confirm the row was written

## Post-Deployment

Once live, every signal I analyze gets logged directly to the production database. The local tracker file becomes a historical reference — new entries go straight to the API.

## Crew AI Audit (Separate Phase)

Not in scope for this change. We need to map what Crew AI touches before deciding what to keep or remove.

