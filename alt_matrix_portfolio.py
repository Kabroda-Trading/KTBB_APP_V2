# alt_matrix_portfolio.py
# ==============================================================================
# ALT MATRIX PORTFOLIO / CONCURRENCY CHECK -- 2026-10-09, binding spec
# ALT_MATRIX_D1_D2_D3_SPEC.md SS5.2/5.3, Andy's ruling on Part 0 findings
# (CC_INTERFACE.md SS5 / AGENT_LOG.md, both repos, 2026-10-09 23:25 CT).
#
# THE ONLY ALT MATRIX MODULE ALLOWED TO REFERENCE ExecutorOrder. Every
# function in this file that touches the DB is read-only -- no db.add(),
# db.flush(), or db.commit() anywhere below (enforced by tests/test_
# alt_matrix_portfolio.py's own before_flush guard, not just this
# comment). This is the one place the spec's "BTC Iron Wall" (acceptance
# criterion 5: no import of/call into gate_traveler.py or traveler_plan_
# engine.py, no alteration of BTC session state) meets the portfolio-wide
# risk caps the spec ALSO requires (SS5.2/5.3) -- resolved by reading
# ExecutorOrder's own DATA via the ORM (never importing or calling
# Traveler's CODE, never writing anything), keeping the dependency
# strictly one-way: Alt Matrix reads Traveler's state to decide whether
# ALT MATRIX ITSELF should stand down. Traveler never reads, calls, or
# waits on anything in this file -- nothing in gate_traveler.py or
# traveler_plan_engine.py references alt_matrix_portfolio.py at all, so
# no bug here can ever delay or block a BTC entry through code.
#
# EXCHANGE-FIRST DESIGN: for anything that already has a real open
# position, this module queries the exchange's own ground truth (real
# equity, every open position account-wide, each position's own
# registered stop), never Traveler's database -- this also catches
# hand-placed positions, unprotected ones, and any DB-vs-exchange
# disagreement Traveler's own tables could never show. The ONE gap a
# pure exchange-side check can't see is a RESTING entry limit order that
# has no position yet -- that's the one narrow, explicitly-scoped
# ExecutorOrder read below.
#
# Andy's ruling added a THIRD cap beyond the spec's own two (max 3 open
# positions, max 20% total risk): a 30% FREE-MARGIN RESERVE FLOOR. Both
# systems share one physical Bitunix account's margin pool -- code-level
# isolation alone cannot stop Alt Matrix opening a position first and
# causing BTC's LATER order to be exchange-rejected for insufficient
# margin (a real way "BTC never blocked" can break with zero code
# coupling at all, found during the Step 1 audit). This floor is the
# ruled fix: Alt Matrix may not open an order if doing so would leave
# less than 30% of account equity as free (unused) margin.
#
# ANY ERROR ANYWHERE IN check_admission() MEANS STAND DOWN. Never guess,
# never admit on a partial/unexpected read.
# ==============================================================================

from typing import Any, Dict

from sqlalchemy.orm import Session

from database import ExecutorAccount, ExecutorOrder, AltMatrixOrder

MAX_OPEN_POSITIONS = 3            # spec SS5.2 -- 1 BTC + 1 SOL + 1 ETH
MAX_TOTAL_RISK_PCT = 0.20         # spec SS5.2 -- 20% total equity at risk, account-wide
MIN_FREE_MARGIN_PCT_AFTER = 0.30  # Andy ruling 2026-10-09 (Option A) -- free margin AFTER this order, as a % of equity


def resting_btc_entry_exposure(db: Session) -> Dict[str, Any]:
    """The ONE narrow DB read this module makes, and the only reason this
    module needs ExecutorOrder at all: a resting BTC Traveler limit
    entry (confirmed cross, not yet touched) has no exchange position
    yet, so the exchange's own get_position() can't show its risk. Reads
    ExecutorOrder directly via the ORM -- no import of, or call into,
    gate_traveler.py or traveler_plan_engine.py anywhere in this module.
    Read-only: no write of any kind."""
    rows = (
        db.query(ExecutorOrder)
        .filter(
            ExecutorOrder.traveler_plan_id.isnot(None),
            ExecutorOrder.decision == "WOULD_PLACE",
            ExecutorOrder.management_state == "PENDING_ENTRY",
            ExecutorOrder.mode == "LIVE",
        )
        .all()
    )
    risk = sum(float(r.risk_dollars_used or 0.0) for r in rows)
    return {"count": len(rows), "risk_dollars": risk}


def alt_matrix_pending_exposure(db: Session) -> Dict[str, Any]:
    """Alt Matrix's OWN just-placed rows that may not show in
    get_position() yet (the fill confirmation and the position actually
    appearing on the exchange are not the same instant). Read-only."""
    rows = (
        db.query(AltMatrixOrder)
        .filter(
            AltMatrixOrder.mode == "LIVE",
            AltMatrixOrder.management_state.in_(["PENDING_ENTRY", "ENTRY_FILLED_UNPROTECTED"]),
        )
        .all()
    )
    risk = sum(float(r.risk_dollars_used or 0.0) for r in rows)
    return {"count": len(rows), "risk_dollars": risk}


async def exchange_account_state(account: ExecutorAccount) -> Dict[str, Any]:
    """Real exchange-side equity + every open position's own risk, using
    the Alt account's own credentials (the SAME physical Bitunix account
    BTC Traveler trades on, per Andy's Option A ruling). Equity formula
    copied from executor_plan_builder.py::_query_real_balance() (NOT
    imported -- see this module's own header on why nothing here imports
    a Traveler file): available + margin + unrealized PnL, confirmed
    against a real Verify Auth response for the no-open-position case
    (2026-09-05). Each position's own REGISTERED stop is read via
    get_pending_tp_sl_order() -- per that client method's own docstring,
    its slPrice/tpPrice field names are per Bitunix's documented shape
    but have NOT yet been verified against a real live response (unlike
    get_position()'s own side="BUY"/"SELL" correction, which WAS caught
    against a real response). Any missing/malformed field here is
    treated as "no stop" (unbounded risk), never guessed at -- this
    function raises on any unexpected API error rather than returning a
    partial result, so the caller's own stand-down-on-any-error rule
    applies uniformly."""
    import executor_accounts
    import executor_bitunix_client

    api_key, api_secret = executor_accounts.get_decrypted_credentials(account)
    if not api_key or not api_secret:
        raise RuntimeError("no credentials configured for this account")
    client = executor_bitunix_client.BitunixClient(api_key, api_secret)

    balance_resp = await client.get_balance()
    if balance_resp.get("code") not in (0, None):
        raise RuntimeError(f"get_balance returned a real API error: code={balance_resp.get('code')} msg={balance_resp.get('msg')!r}")
    bal = balance_resp.get("data") or {}
    available = float(bal.get("available", 0) or 0)
    margin = float(bal.get("margin", 0) or 0)
    unrealized = float(bal.get("isolationUnrealizedPNL", bal.get("crossUnrealizedPNL", 0)) or 0)
    equity = available + margin + unrealized

    pos_resp = await client.get_position()
    if pos_resp.get("code") not in (0, None):
        raise RuntimeError(f"get_position returned a real API error: code={pos_resp.get('code')} msg={pos_resp.get('msg')!r}")
    positions = pos_resp.get("data") or []

    committed_risk = 0.0
    unprotected = False
    for pos in positions:
        position_id = pos.get("positionId")
        try:
            qty = float(pos.get("qty", 0) or 0)
            entry = float(pos.get("avgOpenPrice", 0) or 0)
        except (TypeError, ValueError):
            unprotected = True
            continue
        if qty <= 0 or entry <= 0:
            unprotected = True
            continue

        tpsl_resp = await client.get_pending_tp_sl_order(position_id=position_id)
        stop_price = None
        if tpsl_resp.get("code") in (0, None):
            for row in (tpsl_resp.get("data") or []):
                sl = row.get("slPrice")
                if sl not in (None, "", "0"):
                    try:
                        stop_price = float(sl)
                    except (TypeError, ValueError):
                        stop_price = None
                    break
        if stop_price is None or stop_price <= 0:
            unprotected = True
            continue
        committed_risk += qty * abs(entry - stop_price)

    return {
        "equity": equity, "available": available, "margin": margin,
        "open_count": len(positions), "committed_risk": committed_risk,
        "unprotected": unprotected,
    }


async def check_admission(
    db: Session,
    account: ExecutorAccount,
    symbol: str,
    candidate_risk_dollars: float,
    candidate_margin_required_usd: float,
) -> Dict[str, Any]:
    """The full admission decision for ONE candidate Alt Matrix entry.
    Returns {"admitted": bool, "reason": Optional[str], "snapshot": dict}
    -- `snapshot` is JSON-serializable, meant to be stored verbatim on
    the new AltMatrixOrder row's own admission_snapshot_json column for
    audit (spec acceptance criterion 6's "every transition logged").

    Callers placing BOTH a SOL and an ETH candidate from the same 4H
    signal tick must serialize these calls (e.g. one asyncio.Lock in the
    engine's own signal loop, a fixed symbol-priority order) -- this
    function does not do that itself, so the second call sees the
    first's own freshly-placed AltMatrixOrder row via alt_matrix_
    pending_exposure() above.
    """
    try:
        exch = await exchange_account_state(account)
    except Exception as e:
        return {"admitted": False, "reason": f"exchange_state_query_failed: {e}", "snapshot": {"symbol": symbol}}

    if exch["unprotected"]:
        return {"admitted": False, "reason": "an_open_position_has_no_registered_stop", "snapshot": {"symbol": symbol, **exch}}

    equity = exch["equity"]
    if equity <= 0:
        return {"admitted": False, "reason": "non_positive_equity", "snapshot": {"symbol": symbol, **exch}}

    btc_resting = resting_btc_entry_exposure(db)
    alt_pending = alt_matrix_pending_exposure(db)

    total_open_count = exch["open_count"] + btc_resting["count"] + alt_pending["count"]
    total_committed_risk = exch["committed_risk"] + btc_resting["risk_dollars"] + alt_pending["risk_dollars"]
    free_margin_after = exch["available"] - candidate_margin_required_usd
    free_margin_pct_after = free_margin_after / equity

    snapshot: Dict[str, Any] = {
        "symbol": symbol,
        "equity": equity,
        "exchange_open_count": exch["open_count"], "exchange_committed_risk": exch["committed_risk"],
        "btc_resting_count": btc_resting["count"], "btc_resting_risk": btc_resting["risk_dollars"],
        "alt_pending_count": alt_pending["count"], "alt_pending_risk": alt_pending["risk_dollars"],
        "total_open_count_before": total_open_count, "total_committed_risk_before": total_committed_risk,
        "candidate_risk_dollars": candidate_risk_dollars,
        "candidate_margin_required_usd": candidate_margin_required_usd,
        "free_margin_pct_after": free_margin_pct_after,
    }

    if total_open_count + 1 > MAX_OPEN_POSITIONS:
        return {"admitted": False, "reason": "max_open_positions_reached", "snapshot": snapshot}
    if total_committed_risk + candidate_risk_dollars > MAX_TOTAL_RISK_PCT * equity:
        return {"admitted": False, "reason": "max_total_risk_pct_reached", "snapshot": snapshot}
    if free_margin_pct_after < MIN_FREE_MARGIN_PCT_AFTER:
        return {"admitted": False, "reason": "margin_reserve_floor_breached", "snapshot": snapshot}

    return {"admitted": True, "reason": None, "snapshot": snapshot}
