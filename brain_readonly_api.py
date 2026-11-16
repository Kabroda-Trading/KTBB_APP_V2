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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import (CMEConfig, CMEOrder, CMEPlan, ExecutorAccount, ExecutorAuditLog, ExecutorGlobalConfig,
                      get_db)

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
