"""Read-only API for the Kabroda AI Brain (the auditor). Andy, 2026-11-15: "Wire it up into code".

Contract: Kabroda AI Brain `kabroda_org/80_engineering/SITE_BRIDGE.md` ("flows up", item 5).

Guarantees, enforced here and by tests/test_brain_readonly_api.py:
- GET only. No handler writes, flushes or commits; no call to any `_get_or_init` helper (those create rows).
- Auth: `X-API-Key` must match the BRAIN_READONLY_API_KEY env var (constant-time compare). Fails closed:
  if the env var is unset or empty, every endpoint returns 401. Separate from GATE_LOG_EXPORT_API_KEY so
  each key can be revoked on its own.
- No secrets or personal data: credential columns, anything named like a secret/token/password, and the
  `*_by` columns (which hold people's emails) are never serialized.
"""
from __future__ import annotations

import datetime
import hmac
import os
import re
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import (CMEConfig, CMEOrder, CMEPlan, ExecutorAccount, ExecutorAuditLog, ExecutorGlobalConfig,
                      get_db)

# Market history (Andy 2026-11-18: "Kabroda.com would be a better route"): read-only historical bars from the same
# CrossTrade -> NinjaTrader 8 feed the live CME desk uses, for the Brain's backtests. GetBars is a read RPC; this
# endpoint never places, changes or cancels anything and never returns the token.
_INSTRUMENT = re.compile(r"^[A-Z0-9]{1,4} \d{2}-\d{2}$")          # a dated NT8 contract, e.g. "MGC 12-26"
_PERIOD_TYPES = {"minute": "Minute", "day": "Day"}
MAX_DAYS_BACK = 3650
MAX_BARS = 50000
# Optional date window and merge policy (2026-11-18): GetBars lists from/to/mergePolicy/timeoutMs as optional fields
# (CrossTrade endpoint catalog). Formats are undocumented, so values are strictly shape-checked and passed through as is.
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})?)?$")
_MERGE = re.compile(r"^[A-Za-z]{1,32}$")
MAX_TIMEOUT_MS = 120000

router = APIRouter(prefix="/api/brain/v1", tags=["brain-readonly"])

ENV_KEY = "BRAIN_READONLY_API_KEY"
DEFAULT_LIMIT = 500
MAX_LIMIT = 5000

# Never serialized, whatever the table (credentials, secrets, people's emails in *_by columns).
_DENY = re.compile(r"(encrypted|secret|password|passwd|token|api_key|private|_by$|^credential_)", re.I)


def _authorized(request: Request) -> bool:
    expected = os.getenv(ENV_KEY, "")
    given = request.headers.get("X-API-Key", "")
    return bool(expected) and hmac.compare_digest(given, expected)


def _unauthorized() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)


def _value(v: Any) -> Any:
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    return v


def _row(obj: Any) -> Dict[str, Any]:
    return {c.name: _value(getattr(obj, c.name)) for c in obj.__table__.columns if not _DENY.search(c.name)}


def _rows(objs: Iterable[Any]) -> List[Dict[str, Any]]:
    return [_row(o) for o in objs]


def _limit(limit: Optional[int]) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))


def _parse_since(since: Optional[str]) -> Optional[datetime.datetime]:
    if not since:
        return None
    return datetime.datetime.fromisoformat(since.replace("Z", "+00:00")).replace(tzinfo=None)


def _ok(payload: Dict[str, Any]) -> JSONResponse:
    payload = {"ok": True, "server_utc": datetime.datetime.utcnow().isoformat(timespec="seconds"), **payload}
    return JSONResponse(payload)


@router.get("/status")
async def brain_status(request: Request, db: Session = Depends(get_db)):
    """Deployed commit, both global switches, and account counts. The switches are read, never initialised:
    a missing config row is reported as missing (the site treats it as kill switch off / live orders OFF)."""
    if not _authorized(request):
        return _unauthorized()
    cfg = db.query(ExecutorGlobalConfig).filter_by(config_key="executor_global").first()
    accounts = db.query(ExecutorAccount).all()
    return _ok({
        "deployed_commit": os.getenv("RENDER_GIT_COMMIT", ""),
        "global_config_row_present": cfg is not None,
        "global_config": _row(cfg) if cfg is not None else None,
        "accounts_total": len(accounts),
        "accounts_live": sum(1 for a in accounts if (a.mode or "").upper() == "LIVE"),
    })


@router.get("/accounts")
async def brain_accounts(request: Request, db: Session = Depends(get_db)):
    if not _authorized(request):
        return _unauthorized()
    rows = db.query(ExecutorAccount).order_by(ExecutorAccount.id.asc()).all()
    return _ok({"accounts": _rows(rows)})


@router.get("/cme/configs")
async def brain_cme_configs(request: Request, db: Session = Depends(get_db)):
    if not _authorized(request):
        return _unauthorized()
    return _ok({"cme_configs": _rows(db.query(CMEConfig).order_by(CMEConfig.id.asc()).all())})


@router.get("/cme/plans")
async def brain_cme_plans(request: Request, since: Optional[str] = None, limit: Optional[int] = None,
                          db: Session = Depends(get_db)):
    """since = YYYY-MM-DD (date_key >= since)."""
    if not _authorized(request):
        return _unauthorized()
    q = db.query(CMEPlan)
    if since:
        q = q.filter(CMEPlan.date_key >= since)
    rows = q.order_by(CMEPlan.id.desc()).limit(_limit(limit)).all()
    return _ok({"cme_plans": _rows(rows)})


@router.get("/cme/orders")
async def brain_cme_orders(request: Request, since: Optional[str] = None, account_id: Optional[int] = None,
                           limit: Optional[int] = None, db: Session = Depends(get_db)):
    """since = ISO timestamp or date (created_at >= since, UTC)."""
    if not _authorized(request):
        return _unauthorized()
    q = db.query(CMEOrder)
    s = _parse_since(since)
    if s is not None:
        q = q.filter(CMEOrder.created_at >= s)
    if account_id is not None:
        q = q.filter(CMEOrder.account_id == account_id)
    rows = q.order_by(CMEOrder.id.desc()).limit(_limit(limit)).all()
    return _ok({"cme_orders": _rows(rows)})


@router.get("/audit")
async def brain_audit(request: Request, since: Optional[str] = None, event_type: Optional[str] = None,
                      account_id: Optional[int] = None, limit: Optional[int] = None, db: Session = Depends(get_db)):
    """Executor audit rows, newest first. since = ISO timestamp or date (occurred_at >= since, UTC).
    event_type filters exactly (e.g. ERROR, DATA_FIX)."""
    if not _authorized(request):
        return _unauthorized()
    q = db.query(ExecutorAuditLog)
    s = _parse_since(since)
    if s is not None:
        q = q.filter(ExecutorAuditLog.occurred_at >= s)
    if event_type:
        q = q.filter(ExecutorAuditLog.event_type == event_type)
    if account_id is not None:
        q = q.filter(ExecutorAuditLog.account_id == account_id)
    rows = q.order_by(ExecutorAuditLog.id.desc()).limit(_limit(limit)).all()
    return _ok({"audit": _rows(rows)})


@router.get("/market/bars")
async def brain_market_bars(request: Request, instrument: str, period_type: str = "minute", period: int = 1,
                            days_back: int = 1, limit: Optional[int] = None,
                            from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                            merge_policy: Optional[str] = None, timeout_ms: Optional[int] = None):
    """Historical bars for one dated contract from the CrossTrade -> NinjaTrader 8 feed (read-only).
    instrument = a dated NT8 contract ("MGC 12-26"); period_type minute|day; period 1-1440; days_back 1-3650;
    limit 1-50000 (default 5000); optional from/to (YYYY-MM-DD or ISO datetime), merge_policy (letters only) and
    timeout_ms (1000-120000), passed to GetBars only when given. Returns {count, first, last, bars:[{time, open, high,
    low, close, volume}]}. 503 if no CrossTrade client is configured; an empty list when the NT8 add-on is disconnected."""
    if not _authorized(request):
        return _unauthorized()
    if not _INSTRUMENT.match(instrument or ""):
        return JSONResponse({"ok": False, "error": "instrument must be a dated NT8 contract like 'MGC 12-26'"},
                            status_code=400)
    pt = _PERIOD_TYPES.get((period_type or "").lower())
    if pt is None or not (1 <= int(period) <= 1440):
        return JSONResponse({"ok": False, "error": "period_type must be minute|day and period 1-1440"}, status_code=400)
    for name, value in (("from", from_), ("to", to)):
        if value is not None and not _DATE.match(value):
            return JSONResponse({"ok": False, "error": f"{name} must be YYYY-MM-DD or an ISO datetime"}, status_code=400)
    if merge_policy is not None and not _MERGE.match(merge_policy):
        return JSONResponse({"ok": False, "error": "merge_policy must be letters only"}, status_code=400)
    tmo = None if timeout_ms is None else max(1000, min(int(timeout_ms), MAX_TIMEOUT_MS))
    days = max(1, min(int(days_back), MAX_DAYS_BACK))
    lim = max(1, min(int(limit) if limit is not None else 5000, MAX_BARS))
    import cme_market                                              # lazy: the site's own CrossTrade client resolver
    client = cme_market._get_crosstrade_client()
    if client is None:
        return JSONResponse({"ok": False, "error": "CrossTrade market-data client not configured"}, status_code=503)
    try:
        extra = {k: v for k, v in (("from_ts", from_), ("to_ts", to), ("merge_policy", merge_policy),
                                   ("timeout_ms", tmo)) if v is not None}
        bars = await client.get_market_bars(instrument, period_type=pt, period=int(period), days_back=days,
                                            limit=lim, log_raw=False, **extra)
    except Exception as e:                                         # surface the provider's error, never the token
        msg = _DENY.sub("***", str(e))[:300]
        return JSONResponse({"ok": False, "error": f"market bars query failed: {msg}"}, status_code=502)
    out = [{k: _value(b.get(k)) for k in ("time", "open", "high", "low", "close", "volume")} for b in bars]
    return _ok({"instrument": instrument, "period_type": pt, "period": int(period), "days_back": days, "limit": lim,
                "from": from_, "to": to, "merge_policy": merge_policy, "timeout_ms": tmo,
                "count": len(out), "first": out[0]["time"] if out else None, "last": out[-1]["time"] if out else None,
                "bars": out})

