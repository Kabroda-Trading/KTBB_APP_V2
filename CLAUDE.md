# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Production (deployed on Render at kabroda.com)
python -m uvicorn main:app --host 0.0.0.0 --port 10000
```

There is no test suite. Validation is done by running the server and hitting routes directly.

## Required Environment Variables

```
OPENAI_API_KEY        # No longer used — replaced by Anthropic
ANTHROPIC_API_KEY     # Powers all CrewAI agents and the Operator Commlink
SESSION_SECRET        # Cookie signing key (default: kabroda_prod_key_999)
DATABASE_URL          # Default: sqlite:///./kabroda.db (prod uses PostgreSQL)
PUBLIC_BASE_URL       # Used to auto-detect HTTPS for secure cookies
ADMIN_EMAIL           # Bootstrap admin account on first boot
ADMIN_PASSWORD        # Bootstrap admin password on first boot
COINALYZE_API_KEY     # Optional — open interest data; system degrades gracefully without it
```

## Architecture Overview

### Request → Response Flow

`main.py` is the only FastAPI app. All routes live there. The two primary API endpoints are:

- `POST /api/dmr/live` — The main engine. Fetches 5 candle timeframes + macro oracle concurrently, resolves the current session, and either returns a cached `SessionLock` or computes a new one via `battlebox_pipeline.py`. On a new lock, it fires `run_mas_analysis` in a background thread.
- `GET /suite/macro-war-room` — Server-rendered page. Reads the latest `CampaignLog` for the symbol from DB and injects it into Jinja2 context. If the log exists but has no brief, it retries MAS.

### The Session Lock (SSOT)

Everything is anchored to a **30-minute calibration window** after each session open. During this window, the system returns `CALIBRATING` state. After 30 minutes, `battlebox_pipeline.py` computes the SSE packet once and freezes it into a `SessionLock` DB record. All subsequent calls within that session read from this frozen packet — nothing is recomputed mid-session.

The `session_key` cache in memory (`_LOCKED_PACKETS` dict) mirrors the DB. On restart, the DB is the authoritative source.

### Symbol Format Rule

**All DB writes must use `BTC/USDT` format (with slash), not `BTCUSDT`.** Use `_normalize_symbol()` in `battlebox_pipeline.py` before any DB operation or MAS trigger. The War Room and Commlink routes normalize via `symbol.replace("USDT", "/USDT")`. Inconsistency here causes CampaignLog lookups to silently miss.

### MAS Pipeline (`kabroda_mas_flow.py`)

Six sequential CrewAI agents powered by `claude-sonnet-4-6`:
1. **Macro Architect** → daily bias
2. **Liquidity Scavenger** → gravity wall clearance
3. **Momentum Quant** → fuel gauge / EMA velocity
4. **Chief Risk Officer (Ghost Lead)** → synthesizes all three, issues final execution plan in plain text
5. **Chief Content Officer** → converts CRO output to Markdown newsletter AND emits the `ExecutiveBrief` Pydantic JSON
6. **Intel Auditor** — only used by the `/api/research/audit-intel` route, not the main MAS crew

The CCO task carries `output_pydantic=ExecutiveBrief`. If the LLM doesn't return valid JSON, `task_cco.output.pydantic` is `None`, which raises and is caught — `_mark_mas_error()` then sets `mas_approval_status = "MAS_ERROR"` on the log.

`_inject_brief_to_database` is an **upsert** — it creates a new `CampaignLog` if none exists for `(symbol, session_id, date_key)`. Do not add manual CampaignLog creation elsewhere.

### Background Tasks (started in `lifespan`)

- **Gravity Ingestion Loop** (`gravity_engine.py`) — scans daily/1h candles, logs pivot levels to `gravity_memory` table. Runs continuously, sleeps between cycles.
- **Ledger Closing Loop** (`ledger_closing_engine.py`) — polls MEXC live prices every 60s against APPROVED campaigns. Closes at T1 (win) or SL (loss), writes `realized_pnl` back to `CampaignLog`. This feeds the RAG memory bank in `_fetch_cro_memory`.

### Database Migrations

No migration framework. Schema changes are raw `ALTER TABLE` statements wrapped in `try/except` inside `init_db()` in `database.py`. Add new columns there using the same pattern.

### AUTO Session Mode

`session_manager.resolve_current_session()` with `mode="AUTO"` is hardcoded to `us_ny_futures` (NY 8:30 AM ET). There is no dynamic session detection. Manual session override is passed via `manual_session_id` in the `/api/dmr/live` payload.

### Authentication

PBKDF2-SHA256 (200k iterations) stored as `pbkdf2_sha256$salt$digest`. `argon2-cffi` is in requirements but not used. Sessions are Starlette `SessionMiddleware` cookies with 30-day max_age. The `is_admin` flag gates all `/admin/*` routes.

### Unauthenticated Endpoint

`GET /api/gravity/scan` requires no login — it is polled publicly by the War Room JS every 60 seconds. Do not put sensitive data in its response.
