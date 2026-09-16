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
from database import ExecutorAccount, ExecutorAuditLog, ExecutorRiskState, ExecutorSizingPolicy
import executor_crypto
import executor_sizing


def write_audit(
    db: Session, event_type: str, message: str,
    account_id: Optional[int] = None, trade_plan_id: Optional[int] = None,
    traveler_plan_id: Optional[int] = None,
    executor_order_id: Optional[int] = None, executor_mechanism_test_id: Optional[int] = None,
    actor: Optional[str] = None, detail: Optional[Dict[str, Any]] = None,
) -> None:
    """Public -- the one place any caller (this module, executor_engine.py,
    executor_mechanism_test.py, or main.py's admin routes) writes an
    ExecutorAuditLog row, so no caller ever needs to construct one by
    hand. traveler_plan_id (Phase 2, 2026-09-15): a SEPARATE field from
    trade_plan_id, not an overload of it -- TravelerPlan/TradePlan have
    independent id sequences and could collide on the same integer."""
    db.add(ExecutorAuditLog(
        account_id=account_id, trade_plan_id=trade_plan_id, traveler_plan_id=traveler_plan_id,
        executor_order_id=executor_order_id,
        executor_mechanism_test_id=executor_mechanism_test_id,
        event_type=event_type, actor=actor or "system", message=message,
        detail_json=json.dumps(detail, default=str) if detail else None,
    ))


def create_account(db: Session, user_id: int, label: str, exchange: str = "bitunix", created_by: Optional[str] = None) -> ExecutorAccount:
    account = ExecutorAccount(user_id=user_id, label=label, exchange=exchange, mode="DRY_RUN")
    db.add(account)
    db.flush()  # populate account.id for the audit row below
    write_audit(db, "ACCOUNT_CREATED", f"account '{label}' created for user {user_id}", account_id=account.id, actor=created_by)
    return account


_VALID_MODES = ("DRY_RUN", "LIVE")   # PAPER excluded -- unimplemented in executor_engine.py, no PAPER accounts exist
# Shared with set_account_profile() below (Phase 2, 2026-09-15 Ruling A) --
# a LIVE account's profile change needs the same friction as going LIVE in
# the first place, one phrase, one source of truth.
LIVE_TRADING_CONFIRM_PHRASE = "CONFIRM ENABLE LIVE TRADING"


def set_account_mode(db: Session, account: ExecutorAccount, new_mode: str, by: str, confirm: Optional[str] = None) -> ExecutorAccount:
    """2026-09-07 -- until this function existed, NOTHING in this codebase
    could ever set an account's mode to LIVE (grepped: create_account()
    hardcodes DRY_RUN, nothing else ever assigns .mode), which made
    executor_engine.py's entire LIVE branch unreachable in production
    regardless of how well-tested it was. The real gate this function
    enforces: DRY_RUN -> LIVE requires credentials already set (refuses
    otherwise -- there's no point going live with nothing to authenticate
    with) AND the exact confirm phrase (checked here, not just at the
    route layer, so no caller can accidentally skip it). LIVE -> DRY_RUN
    is unconditional -- the safety-DECREASING direction never needs a
    confirm phrase, matching this file's own kill-switch asymmetry
    (engage vs release). Every transition is audited via the MODE_CHANGED
    event type -- reserved in ExecutorAuditLog's own docstring since
    2026-09-05, never used until now."""
    if new_mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {_VALID_MODES}, got {new_mode!r}")
    old_mode = account.mode
    if new_mode == old_mode:
        return account

    if new_mode == "LIVE":
        if not account.api_key_encrypted or not account.api_secret_encrypted:
            raise ValueError("cannot go LIVE -- no credentials set on this account yet")
        # 2026-09-08 real incident: Dawson selected 10%-of-balance in the
        # Sizing Wizard, saw the correct $250 in the live preview, then went
        # LIVE -- but "preview"/"save sizing" and "go live" are three
        # separate clicks, and nothing ever checked whether the middle one
        # actually happened. His real trade filled at $100 -- the exact,
        # untouched ExecutorRiskState.risk_last_usd default every brand-new
        # account starts at, confirming the sizing choice was never
        # persisted. get_or_init_sizing_policy()'s own first-touch seed
        # stamps preset_name as "steady_grow"/"conservative" (the OLD,
        # pre-wizard vocabulary) specifically so this state is
        # distinguishable from an explicit save -- the new wizard always
        # writes one of fixed_dollar/percent_capped/roll_profits/
        # percent_uncapped/risk_based/custom (see saveSizingPolicy() in
        # executor_admin.html). Refuse to go live until a real choice has
        # actually been saved, the same way credentials are required first.
        policy = get_or_init_sizing_policy(db, account)
        if policy.preset_name in (None, "steady_grow", "conservative"):
            raise ValueError(
                "cannot go LIVE -- no sizing choice has ever been explicitly saved on this account "
                "(it's still running the untouched default). Go to step 3, pick an option, and click "
                "SAVE SIZING POLICY first -- selecting an option and previewing it is not the same as saving it."
            )
        if confirm != LIVE_TRADING_CONFIRM_PHRASE:
            raise ValueError(f"confirm phrase must be exactly {LIVE_TRADING_CONFIRM_PHRASE!r}")

    # 2026-09-16 -- DeepSeek's ruling (Kabroda AI Brain repo AGENT_LOG.md)
    # on the "simulation-inflated risk_last_usd" risk CC flagged when
    # shipping Ruling D (DRY_RUN ledger compounding): a DRY_RUN->LIVE flip
    # must reset the account's own compounding state, so simulated
    # evaluation P&L can never carry into real-money derisk/sizing math.
    # The ruling's own wording said "reset risk_last_usd to NULL" -- verified
    # against ExecutorRiskState's own column (risk_last_usd is nullable=
    # False, defaulted 100.0), a literal NULL write would violate the schema
    # and fail outright on Postgres. Implemented instead as a reset to the
    # account's own configured risk_floor_usd -- the schema's existing
    # "clean baseline" value, same one compute_next_risk() already floors
    # every real compounding step at. No new column, no nullability change.
    # Flagged back to DeepSeek/Andy rather than silently reconciled.
    if old_mode == "DRY_RUN" and new_mode == "LIVE":
        state = get_or_init_risk_state(db, account)
        old_risk_last, old_losses = state.risk_last_usd, state.consecutive_losses
        state.risk_last_usd = state.risk_floor_usd
        state.consecutive_losses = 0
        write_audit(
            db, "RISK_STATE_RESET_ON_LIVE_FLIP",
            f"account {account.id} risk state reset on DRY_RUN->LIVE flip: "
            f"risk_last_usd {old_risk_last} -> {state.risk_last_usd}, consecutive_losses {old_losses} -> 0",
            account_id=account.id, actor=by,
            detail={
                "old_risk_last_usd": old_risk_last, "new_risk_last_usd": state.risk_last_usd,
                "old_consecutive_losses": old_losses,
            },
        )

    account.mode = new_mode
    write_audit(
        db, "MODE_CHANGED", f"account {account.id} mode changed {old_mode} -> {new_mode}",
        account_id=account.id, actor=by, detail={"old_mode": old_mode, "new_mode": new_mode},
    )
    return account


def set_assumed_balance(db: Session, account: ExecutorAccount, assumed_balance_usd: Optional[float], by: str = "system") -> ExecutorAccount:
    """CC_WORK_ORDER_ASSUMED_BALANCE.md (2026-09-16) -- ExecutorAccount.
    assumed_balance_usd existed in the schema with no way to ever write it:
    display-only in the admin UI (executor_admin.html, rendered as "not
    set"), read-only in main.py's account serializer, and the ONLY balance
    fallback executor_plan_builder.py's _query_real_balance() uses when an
    account has no credentials (or a live balance query fails) --
    executor_sizing.py's banded_risk() raises ValueError("banded_risk
    requires a balance") the instant it needs one and gets None. A DRY_RUN
    evaluation account has no exchange to query at all, so without this
    setter, banded sizing could never compute for it -- exactly the error
    Andy hit on the eval_traveler account. Same validation/audit shape as
    set_account_mode()/set_account_profile() above -- never touches
    credentials or mode, valid for every mode (a LIVE account can also use
    this as its own plan-builder fallback if a live balance query fails)."""
    old_value = account.assumed_balance_usd
    if assumed_balance_usd is not None and assumed_balance_usd <= 0:
        raise ValueError("assumed_balance_usd must be a positive number, or null to clear")
    if assumed_balance_usd == old_value:
        return account
    account.assumed_balance_usd = assumed_balance_usd
    write_audit(
        db, "ASSUMED_BALANCE_SET",
        f"account {account.id} assumed_balance_usd changed {old_value} -> {assumed_balance_usd}",
        account_id=account.id, actor=by,
        detail={"old_assumed_balance_usd": old_value, "new_assumed_balance_usd": assumed_balance_usd},
    )
    return account


_VALID_GATE_PROFILES = ("GATE_V2", "GATE_TRAVELER")
_VALID_MGMT_PROFILES = ("MGMT_SPLIT", "MGMT_E1_STACK")
DEFAULT_GATE_PROFILE = "GATE_V2"
DEFAULT_MGMT_PROFILE = "MGMT_SPLIT"


def gate_profile_of(account: ExecutorAccount) -> str:
    """account.gate_profile is nullable (Phase 2, 2026-09-15) -- every real
    call site should go through this rather than reading the column
    directly, so a brand-new or never-touched account reads as today's
    exact v2 behavior (GATE_V2), not None."""
    return account.gate_profile or DEFAULT_GATE_PROFILE


def mgmt_profile_of(account: ExecutorAccount) -> str:
    """Same reasoning as gate_profile_of() -- default MGMT_SPLIT."""
    return account.mgmt_profile or DEFAULT_MGMT_PROFILE


def set_account_profile(
    db: Session, account: ExecutorAccount,
    gate_profile: Optional[str] = None, mgmt_profile: Optional[str] = None,
    by: str = "system", confirm: Optional[str] = None,
) -> ExecutorAccount:
    """Phase 2 (CC_WORK_ORDER_PHASE2.md step 1; LIVE-mode gate added per
    Ruling A, AGENT_LOG.md Kabroda AI Brain repo 2026-09-15 22:10 CT) --
    the per-account strategy profile setter, same validation/audit shape
    as set_account_mode() above. Either field may be omitted (None) to
    leave it unchanged -- callers can set gate and management profiles
    independently. Read AT ORDER TIME by executor_plan_builder.py via
    gate_profile_of()/mgmt_profile_of() above, not just at go-live --
    mirrors the sizing-policy read pattern (get_or_init_sizing_policy() is
    also read fresh per order, not cached at go-live).

    Ruling A: an account already in LIVE mode requires the SAME
    credentials+confirm gate set_account_mode() uses for DRY_RUN->LIVE --
    changing what a live account trades is as consequential as flipping it
    live in the first place. DRY_RUN accounts switch profiles freely, no
    confirm phrase needed (same safety-direction reasoning as LIVE->
    DRY_RUN mode changes needing no confirm)."""
    if gate_profile is not None and gate_profile not in _VALID_GATE_PROFILES:
        raise ValueError(f"gate_profile must be one of {_VALID_GATE_PROFILES}, got {gate_profile!r}")
    if mgmt_profile is not None and mgmt_profile not in _VALID_MGMT_PROFILES:
        raise ValueError(f"mgmt_profile must be one of {_VALID_MGMT_PROFILES}, got {mgmt_profile!r}")

    old_gate, old_mgmt = gate_profile_of(account), mgmt_profile_of(account)
    new_gate = gate_profile if gate_profile is not None else old_gate
    new_mgmt = mgmt_profile if mgmt_profile is not None else old_mgmt
    is_real_change = new_gate != old_gate or new_mgmt != old_mgmt

    if account.mode == "LIVE" and is_real_change:
        if not account.api_key_encrypted or not account.api_secret_encrypted:
            raise ValueError("cannot change profile on a LIVE account -- no credentials set")
        if confirm != LIVE_TRADING_CONFIRM_PHRASE:
            raise ValueError(f"confirm phrase must be exactly {LIVE_TRADING_CONFIRM_PHRASE!r}")

    if gate_profile is not None:
        account.gate_profile = gate_profile
    if mgmt_profile is not None:
        account.mgmt_profile = mgmt_profile

    if is_real_change:
        write_audit(
            db, "PROFILE_CHANGED",
            f"account {account.id} profile changed gate {old_gate}->{new_gate}, mgmt {old_mgmt}->{new_mgmt}",
            account_id=account.id, actor=by,
            detail={"old_gate": old_gate, "new_gate": new_gate, "old_mgmt": old_mgmt, "new_mgmt": new_mgmt},
        )
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
    write_audit(
        db, "CREDENTIAL_ROTATED" if is_rotation else "CREDENTIAL_SET",
        f"credentials {'rotated' if is_rotation else 'set'} for account {account.id}",
        account_id=account.id, actor=set_by,
    )


def get_decrypted_credentials(account: ExecutorAccount) -> Tuple[Optional[str], Optional[str]]:
    """THE ONLY decrypt entry point in this codebase. Permitted callers:
    executor_engine.py's PAPER/LIVE branch (Stage 2/3), executor_plan_
    builder.py's real-leverage/real-mmr query helpers, executor_
    mechanism_test.py's real order-placing orchestration, and main.py's
    read-only test-connection route -- each a deliberate, reviewed
    exception, never anything that renders the decrypted value itself
    back to a browser."""
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
    write_audit(db, "KILL_SWITCH_ENGAGED", f"account {account.id} kill switch engaged -- {reason}", account_id=account.id, actor=by)


def release_kill_switch(db: Session, account: ExecutorAccount, by: str) -> None:
    account.kill_switch_engaged = False
    account.kill_switch_engaged_at = None
    account.kill_switch_engaged_by = None
    account.kill_switch_reason = None
    write_audit(db, "KILL_SWITCH_RELEASED", f"account {account.id} kill switch released", account_id=account.id, actor=by)


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
    old_compounding_factor = state.compounding_factor
    for field, value in changes.items():
        setattr(state, field, value)
    # 2026-09-07 stagnant-sweep fix (the "Base $ class" of bug, found by
    # auditing every ExecutorRiskState/ExecutorSizingPolicy field for a
    # real consumer, per STAGNANT_SWEEP.md item 6): record_trade_result()
    # -- the ONLY thing that actually compounds a stake -- reads
    # policy.roll_in_pct, never state.compounding_factor. This "Legacy
    # Risk State" field only ever feeds compounding_factor -> roll_in_pct
    # as a ONE-TIME seed inside get_or_init_sizing_policy()'s first-touch
    # branch (same shape as the original Base $ bug). Once a sizing
    # policy row exists for the account -- which is every account that
    # has ever built a live plan even once, since executor_plan_builder.py
    # calls get_or_init_sizing_policy() unconditionally -- editing this
    # legacy field here was a real, silent no-op: the UI input looked and
    # behaved like any other saveable field with zero indication it no
    # longer did anything. Same fix shape as the Base $ bug: only
    # propagate when the value actually changes to a NEW one (never
    # clobber a value someone set intentionally via the real Sizing
    # Policy Wizard through an unrelated legacy-panel save).
    if "compounding_factor" in changes and changes["compounding_factor"] != old_compounding_factor:
        policy = get_or_init_sizing_policy(db, account)
        policy.roll_in_pct = changes["compounding_factor"]
    if changes:
        write_audit(
            db, "RISK_STATE_UPDATED", f"risk state updated for account {account.id}: {changes}",
            account_id=account.id, actor=updated_by, detail=changes,
        )
    return state


def get_or_init_sizing_policy(db: Session, account: ExecutorAccount) -> ExecutorSizingPolicy:
    """Lazy-init, same idiom as get_or_init_risk_state() -- no bulk data
    migration needed for the new table. On first touch, seeds the policy
    FROM the account's existing (real, production) ExecutorRiskState so an
    account already running the old fixed/rolling risk-state fields keeps
    behaving identically the moment the wizard is introduced -- nothing
    silently changes size on an account that hasn't opted into a preset."""
    policy = db.query(ExecutorSizingPolicy).filter_by(account_id=account.id).first()
    if policy is None:
        risk_state = get_or_init_risk_state(db, account)
        policy = ExecutorSizingPolicy(
            account_id=account.id,
            preset_name="steady_grow" if risk_state.compounding_factor > 0 else "conservative",
            base_risk_usd=risk_state.risk_last_usd,
            roll_in_pct=risk_state.compounding_factor,
            cap_abs_usd=risk_state.risk_cap_usd,
        )
        db.add(policy)
        db.flush()
    return policy


def _validate_sizing_policy(changes: Dict[str, Any]) -> None:
    """Raises ValueError on an internally-inconsistent policy. Called with
    the FULL merged view (existing fields + the incoming changes), never
    just the incoming partial dict, so a partial update can't leave the
    row in a state that would have been rejected outright."""
    band_step = changes.get("band_step_usd")
    band_per_step = changes.get("band_risk_per_step_usd")
    if (band_step is None) != (band_per_step is None):
        raise ValueError("band_step_usd and band_risk_per_step_usd must be set together or not at all")
    banded = band_step is not None
    if banded and (band_step <= 0 or band_per_step <= 0):
        raise ValueError("band_step_usd and band_risk_per_step_usd must be positive")

    base_usd = changes.get("base_risk_usd")
    base_pct = changes.get("base_risk_pct")
    if base_usd is not None and base_pct is not None:
        raise ValueError("set exactly one of base_risk_usd / base_risk_pct, not both")
    # Banded mode (Andy's stair-step schedule) computes the stake base
    # entirely from the live balance -- it needs neither base_risk_usd nor
    # base_risk_pct. Only require a base when NOT banded.
    if not banded and base_usd is None and base_pct is None:
        raise ValueError("set exactly one of base_risk_usd / base_risk_pct")

    tier_threshold = changes.get("tier_threshold_usd")
    tier_flat = changes.get("tier_flat_usd")
    if (tier_threshold is None) != (tier_flat is None):
        raise ValueError("tier_threshold_usd and tier_flat_usd must be set together or not at all")
    if banded and (tier_threshold is not None or tier_flat is not None):
        raise ValueError("banded sizing is mutually exclusive with the tier switch (a schedule vs a single step)")

    below_pct = changes.get("band_below_pct")
    if below_pct is not None and not (0.0 < below_pct <= 1.0):
        raise ValueError("band_below_pct must be between 0 and 1")
    band_max = changes.get("band_max_risk_usd")
    if band_max is not None and band_max <= 0:
        raise ValueError("band_max_risk_usd must be positive when set")

    derisk_n = changes.get("derisk_n")
    derisk_factor = changes.get("derisk_factor")
    if (derisk_n is None) != (derisk_factor is None):
        raise ValueError("derisk_n and derisk_factor must be set together or not at all")


def update_sizing_policy(db: Session, account: ExecutorAccount, changes: Dict[str, Any], updated_by: str) -> ExecutorSizingPolicy:
    """Applies an owner/admin edit to the sizing policy. Validates the
    FULL resulting row (existing fields overlaid with `changes`), not just
    the incoming partial dict -- raises ValueError on 400 up in main.py.
    Same audit-then-return pattern as update_risk_state()."""
    policy = get_or_init_sizing_policy(db, account)
    old_base_risk_usd = policy.base_risk_usd
    merged = {
        "preset_name": policy.preset_name, "base_risk_usd": policy.base_risk_usd, "base_risk_pct": policy.base_risk_pct,
        "roll_in_pct": policy.roll_in_pct, "cap_abs_usd": policy.cap_abs_usd, "cap_pct": policy.cap_pct,
        "tier_threshold_usd": policy.tier_threshold_usd, "tier_flat_usd": policy.tier_flat_usd,
        "band_step_usd": policy.band_step_usd, "band_risk_per_step_usd": policy.band_risk_per_step_usd,
        "band_below_pct": policy.band_below_pct, "band_max_risk_usd": policy.band_max_risk_usd,
        "derisk_n": policy.derisk_n, "derisk_factor": policy.derisk_factor,
    }
    merged.update(changes)

    # Setting ONLY one of base_risk_usd/base_risk_pct in this call
    # implicitly clears the other -- applied to the validation view
    # BEFORE _validate_sizing_policy() runs, so a caller switching from
    # fixed-mode to percent-mode only needs to pass the new field, not
    # also explicitly null out the stale one. If the caller explicitly
    # passed BOTH in this same call, that's a genuine conflict -- leave
    # it alone so _validate_sizing_policy() still rejects it.
    switching_base = ("base_risk_usd" in changes) != ("base_risk_pct" in changes)
    if switching_base:
        if changes.get("base_risk_usd") is not None:
            merged["base_risk_pct"] = None
        elif changes.get("base_risk_pct") is not None:
            merged["base_risk_usd"] = None

    # Switching TO banded mode (Andy's stair-step schedule) makes the base,
    # the tier switch, AND the standalone dual caps meaningless -- banded_
    # risk() computes the stake entirely from the live balance and carries
    # its own band_max_risk_usd ceiling. Clear them in the same call so a
    # caller only needs to send the band fields, mirroring switching_base
    # above (and matching the wizard's own F-card behavior). A caller that
    # explicitly re-sends one of these in the same call keeps it -- e.g. a
    # deliberate "bands, but never more than 5% of balance" (cap_pct).
    _BANDED_CLEARS = ("base_risk_usd", "base_risk_pct", "tier_threshold_usd",
                       "tier_flat_usd", "cap_abs_usd", "cap_pct")
    switching_to_banded = changes.get("band_step_usd") is not None
    if switching_to_banded:
        for _f in _BANDED_CLEARS:
            if _f not in changes:
                merged[_f] = None

    _validate_sizing_policy(merged)

    for field, value in changes.items():
        setattr(policy, field, value)
    if switching_base:
        if changes.get("base_risk_usd") is not None:
            policy.base_risk_pct = None
        elif changes.get("base_risk_pct") is not None:
            policy.base_risk_usd = None
    if switching_to_banded:
        for _f in _BANDED_CLEARS:
            if _f not in changes:
                setattr(policy, _f, None)

    # 2026-09-07 real bug found while confirming Andy's compounding
    # question: compute_stake() (and this preview route's own math) never
    # read policy.base_risk_usd at all -- the FIXED/ROLLING stake always
    # came from ExecutorRiskState.risk_last_usd directly, which nothing
    # ever synced FROM the wizard's "Base $" field except once, at
    # get_or_init_sizing_policy()'s own first-touch seed (the OTHER
    # direction). Setting "Base $100" and saving silently did nothing to
    # the real stake used at trade time. Only reset when the value
    # actually CHANGES (not on every save -- the JS always resends every
    # field, so "in changes" alone would wipe real compounding progress
    # on every unrelated edit, e.g. adjusting the cap).
    new_base_risk_usd = changes.get("base_risk_usd")
    if new_base_risk_usd is not None and new_base_risk_usd != old_base_risk_usd:
        risk_state = get_or_init_risk_state(db, account)
        risk_state.risk_last_usd = new_base_risk_usd

    write_audit(
        db, "SIZING_POLICY_UPDATED", f"sizing policy updated for account {account.id}: {changes}",
        account_id=account.id, actor=updated_by, detail=changes,
    )
    return policy


def record_trade_result(
    db: Session, account: ExecutorAccount, pnl_usd: float,
    trade_plan_id: Optional[int] = None, recorded_by: Optional[str] = None,
    is_simulation: bool = False,
) -> ExecutorRiskState:
    """MANUAL STOPGAP -- confirmed by direct grep that TradePlan (what the
    executor hooks into) and CampaignLog (the only table with a real R-
    multiple outcome) have NO link to each other today. There is no
    automatic way yet for the executor to know a real trade won or lost,
    so this is, today, the ONLY write path to
    ExecutorRiskState.last_trade_pnl_usd (otherwise permanently NULL) and
    to consecutive_losses. A future closed-position monitor becomes a new
    CALLER of this exact function -- same schema, zero migration -- it
    does not replace it.

    is_simulation (Ruling D, DeepSeek, relayed by Andy 2026-09-15): the
    two candle-only DRY_RUN walks (dry_run_split_engine.py's MGMT_SPLIT,
    traveler_plan_engine.py's MGMT_E1_STACK) call this SAME function so a
    DRY_RUN account's own ledger actually compounds instead of sitting
    permanently flat -- the stagnant-surface bug class this codebase has
    hit before (see update_risk_state()'s own "Base $ class" comment
    above). Tagged here, not via a new column: an account's own `mode` is
    already the ground truth for "is this real money" everywhere else in
    the UI, and a DRY_RUN account's ledger numbers are ALWAYS simulation
    by construction (no other path can move them) -- a redundant boolean
    on ExecutorRiskState would duplicate that fact, not add one. The audit
    trail is where the explicit label lives, for anyone reviewing WHY a
    number moved. No new DB columns."""
    state = get_or_init_risk_state(db, account)
    policy = get_or_init_sizing_policy(db, account)

    state.last_trade_pnl_usd = pnl_usd
    state.last_updated_from_trade_plan_id = trade_plan_id

    if policy.roll_in_pct is not None:
        state.risk_last_usd = executor_sizing.compute_next_risk(
            risk_last=state.risk_last_usd, last_trade_pnl=pnl_usd,
            floor=state.risk_floor_usd, cap=policy.cap_abs_usd or state.risk_cap_usd,
            factor=policy.roll_in_pct,
        )

    state.consecutive_losses = 0 if pnl_usd > 0 else state.consecutive_losses + 1

    label = "[SIMULATION] " if is_simulation else ""
    write_audit(
        db, "TRADE_RESULT_RECORDED", f"{label}trade result recorded for account {account.id}: pnl_usd={pnl_usd}",
        account_id=account.id, trade_plan_id=trade_plan_id, actor=recorded_by,
        detail={
            "pnl_usd": pnl_usd, "trade_plan_id": trade_plan_id,
            "risk_last_usd_after": state.risk_last_usd, "consecutive_losses_after": state.consecutive_losses,
            "is_simulation": is_simulation,
        },
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
