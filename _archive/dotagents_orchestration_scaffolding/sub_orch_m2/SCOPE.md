# Scope: AI Diagnostic API (M2)

## Architecture
The diagnostic API consists of five JSON endpoints mounted under `/api/v1/system/*` in `main.py`.
- They interact with the SQLite database via SQLAlchemy sessions (`get_db` dependency).
- They query `SessionLock`, `CampaignLog`, `AgentRunLog`, and a new table `SystemAnalysisReport` for system status, errors, trades, and ad-hoc analysis reports.
- Schedulers update an in-memory health registry on the FastAPI application state.
- Ad-hoc natural language queries invoke a new agent spec `agents/system_analysis.md` to format response JSON using Claude.

```
FastAPI Server (main.py)
   ├── scheduler_health_registry (App state dict)
   └── /api/v1/system/*
         ├── /state       (Active sessions, shadow runners, scheduler health, recent errors)
         ├── /trades      (Windowed trades and win/loss metrics)
         ├── /parameters  (Tunable indicator settings, risk variables, Lti/Monitor configs)
         ├── /errors      (Agent error log and audit suggestions)
         └── /analysis    (Ad-hoc natural language query handler -> agent_core LLM run)
```

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | M2.1: DB Schema & Agent Spec | Add `SystemAnalysisReport` model to `database.py` and create `agents/system_analysis.md` | None | PLANNED |
| 2 | M2.2: Scheduler Telemetry | Define `scheduler_health_registry` and inject update logic into the 6 scheduler loops in `main.py` | None | PLANNED |
| 3 | M2.3: API Router & Endpoints | Implement `/state`, `/trades`, `/parameters`, `/errors`, and `/analysis` JSON endpoints in `main.py` | M2.1, M2.2 | PLANNED |
| 4 | M2.4: Verification & Auditing | Run automated checks, verify schemas, and verify through Forensic Auditor | M2.3 | PLANNED |

## Interface Contracts
### GET /api/v1/system/state
- Response: `{"ok": bool, "active_sessions": [...], "active_runners": [...], "scheduler_health": {...}, "macro_engine": {...}, "recent_errors": [...]}`

### GET /api/v1/system/trades
- Query Parameters: `window: str` (values: `7d`, `30d`, `all`, defaults to `30d`)
- Response: `{"ok": bool, "window": str, "metrics": {"win_rate": float, "net_r": float, "approval_rate": float}, "trades": [...]}`

### GET /api/v1/system/parameters
- Response: `{"ok": bool, "parameters": [...], "dependencies": [...]}`

### GET /api/v1/system/errors
- Response: `{"ok": bool, "errors": [...], "alert_history": [...], "health_summary": {...}}`

### POST /api/v1/system/analysis
- Request: `{"query": str}`
- Response: `{"ok": bool, "query": str, "analysis_id": str, "report": {"summary": str, "verdict": str, "data_metrics": {...}, "recommendations": [...], "confidence_score": float}}`
