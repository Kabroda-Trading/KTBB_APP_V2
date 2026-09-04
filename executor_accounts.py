# executor_accounts.py
# ==============================================================================
# EXECUTOR ACCOUNTS -- CRUD + safety-gate layer over executor_accounts /
# executor_risk_state (database.py). Stage 1 of the Bitunix executor bot.
#
# get_decrypted_credentials() is the ONLY function in this codebase
# permitted to decrypt a stored exchange secret -- never call it from an
# admin route or any HTML-rendering code path. Even if the admin UI were
# compromised, there should be no keys reachable from it (Andy's own
# explicit requirement, Kabroda AI Brain repo AGENT_LOG.md, 2026-09-04).
# ==============================================================================

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

import executor_control
from database import ExecutorAccount, ExecutorAuditLog, ExecutorRiskState
import executor_crypto


def _write_audit(
    db: Session, event_type: str, message: str,
    account_id: Optional[int] = None, trade_plan_id: Optional[int] = None,
    executor_order_id: Optional[int] = None, actor: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    db.add(ExecutorAuditLog(
        account_id=account_id, trade_plan_id=trade_plan_id, executor_order_id=executor_order_id,
        event_type=event_type, actor=actor or "system", message=message,
        detail_json=json.dumps(detail, default=str) if detail else None,
    ))


def create_account(db: Session, user_id: int, label: str, exchange: str = "bitunix", created_by: Optional[str] = None) -> ExecutorAccount:
    account = ExecutorAccount(user_id=user_id, label=label, exchange=exchange, mode="DRY_RUN")
    db.add(account)
    db.flush()  # populate account.id for the audit row below
    _write_audit(db, "ACCOUNT_CREATED", f"account '{label}' created for user {user_id}", account_id=account.id, actor=created_by)
    return account


def set_credentials(db: Session, account: ExecutorAccount, api_key: str, api_secret: str, set_by: str) -> None:
    """Encrypts and stores the exchange credentials. Never stores or logs
    plaintext anywhere -- the audit row records THAT a credential was set/
    rotated, never the value."""
    is_rotation = account.api_key_encrypted is not None
    account.api_key_encrypted = executor_crypto.encrypt_secret(api_key)
    account.api_secret_encrypted = executor_crypto.encrypt_secret(api_secret)
    account.credential_set_at = datetime.datetime.utcnow()
    account.credential_set_by = set_by
    _write_audit(
        db, "CREDENTIAL_ROTATED" if is_rotation else "CREDENTIAL_SET",
        f"credentials {'rotated' if is_rotation else 'set'} for account {account.id}",
        account_id=account.id, actor=set_by,
    )


def get_decrypted_credentials(account: ExecutorAccount) -> Tuple[Optional[str], Optional[str]]:
    """THE ONLY decrypt entry point in this codebase. Caller is
    executor_engine.py's PAPER/LIVE branch (Stage 2/3) only -- never an
    admin route, never anything that renders HTML/JSON back to a browser."""
    if not account.api_key_encrypted or not account.api_secret_encrypted:
        return None, None
    return (
        executor_crypto.decrypt_secret(account.api_key_encrypted),
        executor_crypto.decrypt_secret(account.api_secret_encrypted),
    )


def engage_kill_switch(db: Session, account: ExecutorAccount, reason: str, by: str) -> None:
    account.kill_switch_engaged = True
    account.kill_switch_engaged_at = datetime.datetime.utcnow()
    account.kill_switch_engaged_by = by
    account.kill_switch_reason = reason
    _write_audit(db, "KILL_SWITCH_ENGAGED", f"account {account.id} kill switch engaged -- {reason}", account_id=account.id, actor=by)


def release_kill_switch(db: Session, account: ExecutorAccount, by: str) -> None:
    account.kill_switch_engaged = False
    account.kill_switch_engaged_at = None
    account.kill_switch_engaged_by = None
    account.kill_switch_reason = None
    _write_audit(db, "KILL_SWITCH_RELEASED", f"account {account.id} kill switch released", account_id=account.id, actor=by)


def get_or_init_risk_state(db: Session, account: ExecutorAccount) -> ExecutorRiskState:
    state = db.query(ExecutorRiskState).filter_by(account_id=account.id).first()
    if state is None:
        state = ExecutorRiskState(account_id=account.id)
        db.add(state)
        db.flush()
    return state


def update_risk_state(db: Session, account: ExecutorAccount, changes: Dict[str, float], updated_by: str) -> ExecutorRiskState:
    """Applies an admin/owner edit to risk_last_usd/risk_floor_usd/
    risk_cap_usd/compounding_factor and writes the audit row -- the one
    place this happens, so callers (main.py's admin route) never touch
    ExecutorAuditLog directly."""
    state = get_or_init_risk_state(db, account)
    for field, value in changes.items():
        setattr(state, field, value)
    if changes:
        _write_audit(
            db, "RISK_STATE_UPDATED", f"risk state updated for account {account.id}: {changes}",
            account_id=account.id, actor=updated_by, detail=changes,
        )
    return state


def is_account_tradeable(db: Session, account: ExecutorAccount) -> Tuple[bool, str]:
    """ANDs account-level active/kill-switch state with the GLOBAL kill
    switch. Fails CLOSED (not tradeable) on any error -- modeled directly
    on session_monitor.py's _is_notification_enabled(): uncertain state
    never permits an action with real consequences."""
    try:
        if executor_control.is_global_kill_switch_engaged(db):
            return False, "global kill switch is engaged"
        if not account.is_active:
            return False, f"account {account.id} is inactive"
        if account.kill_switch_engaged:
            return False, f"account {account.id} kill switch is engaged -- {account.kill_switch_reason or 'no reason given'}"
        return True, "account is tradeable"
    except Exception as e:
        return False, f"error checking tradeability, failing closed: {e}"
