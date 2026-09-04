# executor_control.py
# ==============================================================================
# EXECUTOR GLOBAL CONTROL -- the global kill switch, ANDed with each
# account's own kill_switch_engaged flag in executor_accounts.
# is_account_tradeable(). Stage 1 of the Bitunix executor bot.
#
# Fails closed TOWARD BLOCKING TRADING on any DB error -- the opposite
# polarity from session_monitor.py's _is_notification_enabled() (which
# fails closed toward not sending a notification), but the same
# defensive spirit: uncertain state never permits the higher-consequence
# action. A notification that doesn't send is a missed email; an
# executor that doesn't fail closed here could place an order it
# shouldn't have.
# ==============================================================================

from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy.orm import Session

from database import ExecutorGlobalConfig

_CONFIG_KEY = "executor_global"


def _get_or_init(db: Session) -> ExecutorGlobalConfig:
    cfg = db.query(ExecutorGlobalConfig).filter_by(config_key=_CONFIG_KEY).first()
    if cfg is None:
        cfg = ExecutorGlobalConfig(config_key=_CONFIG_KEY)
        db.add(cfg)
        db.flush()
    return cfg


def is_global_kill_switch_engaged(db: Session) -> bool:
    try:
        cfg = db.query(ExecutorGlobalConfig).filter_by(config_key=_CONFIG_KEY).first()
        if cfg is None:
            # No config row yet -- treat as NOT engaged (the default,
            # pre-any-human-action state), matching stage_default_mode's
            # own DRY_RUN default: safe by construction (Stage 1 never
            # calls the exchange regardless), not by this flag alone.
            return False
        return bool(cfg.global_kill_switch_engaged)
    except Exception:
        return True  # fail CLOSED toward blocking trading


def engage_global_kill_switch(db: Session, reason: str, by: str) -> None:
    cfg = _get_or_init(db)
    cfg.global_kill_switch_engaged = True
    cfg.global_kill_switch_engaged_at = datetime.datetime.utcnow()
    cfg.global_kill_switch_engaged_by = by
    cfg.global_kill_switch_reason = reason


def release_global_kill_switch(db: Session, by: str) -> None:
    cfg = _get_or_init(db)
    cfg.global_kill_switch_engaged = False
    cfg.global_kill_switch_engaged_at = None
    cfg.global_kill_switch_engaged_by = None
    cfg.global_kill_switch_reason = None
