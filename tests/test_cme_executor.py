"""Coverage for cme_executor.py -- real Tradovate order-placement mechanics
for CME LIVE accounts. Same fake-client pattern as
tests/test_alt_matrix_executor.py: a real file-backed SQLite DB, the
Tradovate client monkeypatched at the class level (no real network calls)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./kabroda_test_cme_executor.db")

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount, CMEPlan, CMEOrder, CMETransition, ExecutorAuditLog
import executor_accounts as ea
import executor_control as ec
import cme_tradovate_client as tvc
import cme_crosstrade_client as ctc
import cme_executor as cx


def _clean_db_files():
    for path in ["kabroda_test_cme_executor.db", "kabroda_test_cme_executor.db-journal",
                 "kabroda_test_cme_executor.db-shm", "kabroda_test_cme_executor.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    for model in (CMETransition, CMEOrder, CMEPlan, ExecutorAuditLog, ExecutorAccount):
        session.query(model).delete()
    session.commit()


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", Fernet.generate_key().decode("utf-8"))
    _clean_db_files()
    database.init_db()
    session = SessionLocal()
    _clean_rows(session)
    yield session
    _clean_rows(session)
    session.close()
    database.engine.dispose()
    _clean_db_files()


def _ready_account(db, label="cme_exec_test"):
    account = ea.create_account(db, user_id=1, label=label)
    db.flush()
    account.mode = "LIVE"
    account.tradovate_account_id = 12345
    account.tradovate_account_name = "APEXTEST001"
    ea.set_credentials(db, account, api_key="fake-user", api_secret="fake-pass", set_by="test@kabroda.com")
    ec.enable_live_orders(db, reason="testing", by="andy@kabroda.com")
    db.commit()
    return account


def _ready_crosstrade_account(db, label="cme_exec_crosstrade_test"):
    # Fee capture (2026-11-xx) -- a second account flavor, execution_
    # provider="crosstrade", for exercising cme_executor.py's CrossTrade-
    # only fee-fetch gate. api_secret is an unused placeholder for this
    # provider (CrossTrade needs only the bearer token) -- same convention
    # cme_crosstrade_client.py's own header documents.
    account = ea.create_account(db, user_id=1, label=label)
    db.flush()
    account.mode = "LIVE"
    account.execution_provider = "crosstrade"
    account.crosstrade_account_id = "acct-ct-1"
    ea.set_credentials(db, account, api_key="fake-bearer-token", api_secret="unused", set_by="test@kabroda.com")
    ec.enable_live_orders(db, reason="testing", by="andy@kabroda.com")
    db.commit()
    return account


def _patch_crosstrade(monkeypatch, **methods):
    for name, fn in methods.items():
        monkeypatch.setattr(ctc.CrossTradeClient, name, fn)


def _plan(db, symbol="MNQ", **overrides):
    defaults = dict(
        symbol=symbol, date_key="2026-11-04", lock_time=database.datetime.datetime.utcnow(),
        status="ARMED", box=20.0, bo=4500.0, bd=4480.0,
        stop_long=4480.0, stop_short=4500.0, target_long=4540.0, target_short=4460.0,
        be_trigger_long=4516.0, be_trigger_short=4464.0, be_stop_long=4502.0, be_stop_short=4498.0,
    )
    defaults.update(overrides)
    plan = CMEPlan(**defaults)
    db.add(plan)
    db.flush()
    return plan


def _order(db, account, plan, **overrides):
    defaults = dict(
        cme_plan_id=plan.id, account_id=account.id, mode="LIVE",
        symbol=plan.symbol, direction="LONG", contracts=2, box_at_entry=plan.box,
        decision="WOULD_PLACE", management_state="PENDING_ENTRY",
    )
    defaults.update(overrides)
    order = CMEOrder(**defaults)
    db.add(order)
    db.flush()
    return order


def _async(value=None, exc=None):
    async def _fake(self, *a, **kw):
        if exc is not None:
            raise exc
        return value
    return _fake


def _run(coro):
    return asyncio.run(coro)


def _patch(monkeypatch, **methods):
    for name, fn in methods.items():
        monkeypatch.setattr(tvc.TradovateClient, name, fn)


# Lane H batch 2 (C1/C2): fill confirmation now looks for the bracket legs
# (get_orders), the BE amend reads the stop back (get_order_detail) and an
# exit price may come from the last 5m close (cme_market). Safe defaults so
# no test ever reaches a real broker or feed; tests override as needed.
@pytest.fixture(autouse=True)
def _broker_defaults(monkeypatch):
    import cme_market
    async def _no_orders(self, *a, **kw):
        raise RuntimeError("get_orders not mocked in this test")
    async def _no_detail(self, *a, **kw):
        return None
    async def _no_bars(*a, **kw):
        return []
    for cls in (tvc.TradovateClient, ctc.CrossTradeClient):
        monkeypatch.setattr(cls, "get_orders", _no_orders)
        monkeypatch.setattr(cls, "get_order_detail", _no_detail)
    monkeypatch.setattr(cme_market, "fetch_confirmed_5m", _no_bars)


def _broker(monkeypatch, open_symbols, patcher=None, close_ok=True, **extra):
    """Stateful fake broker: positions stay open until emergency_market_close
    is called for that symbol (Lane H C2.4 checks the position BEFORE
    sending a close, then polls for flat)."""
    state = {"open": list(open_symbols), "closes": 0}

    async def _positions(self, *a, **kw):
        return [{"symbol": s, "netPrice": 4500.0} for s in state["open"]]

    async def _close(self, symbol, qty, direction):
        state["closes"] += 1
        if close_ok:
            state["open"] = [s for s in state["open"] if s != symbol]
        return close_ok
    (patcher or _patch)(monkeypatch, get_positions=_positions, emergency_market_close=_close, **extra)
    return state


# ------------------------------------------------------------------ place_resting_bracket()

def test_place_resting_bracket_success_transitions_to_waiting_retest(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan)
    _patch(monkeypatch, place_bracket_limit_order=_async({"orderId": 777}))

    _run(cx.place_resting_bracket(db, account, plan, order))
    db.commit()

    assert order.management_state == "WAITING_RETEST"
    assert order.entry_exchange_order_id == "777"
    assert order.sl_price_initial == plan.stop_long


def test_place_resting_bracket_failure_closes_as_error(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan)
    _patch(monkeypatch, place_bracket_limit_order=_async({"error": "rejected"}))

    _run(cx.place_resting_bracket(db, account, plan, order))
    db.commit()

    assert order.management_state == "CLOSED_ERROR"
    assert order.entry_exchange_order_id is None


def test_place_resting_bracket_is_safely_resumable(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, entry_exchange_order_id="already-placed", management_state="WAITING_RETEST")
    call_count = {"n": 0}

    async def _should_not_be_called(self, *a, **kw):
        call_count["n"] += 1
        return {"orderId": 999}

    monkeypatch.setattr(tvc.TradovateClient, "place_bracket_limit_order", _should_not_be_called)
    _run(cx.place_resting_bracket(db, account, plan, order))
    assert call_count["n"] == 0


# ------------------------------------------------------------------ check_retest_fill_and_confirm()

def test_check_retest_fill_confirms_a_real_position(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="WAITING_RETEST", entry_exchange_order_id="777")
    _patch(monkeypatch, get_positions=_async([{"symbol": "MNQ", "id": "pos1", "netPrice": "4500.0"}]))

    _run(cx.check_retest_fill_and_confirm(db, account, order))
    db.commit()

    assert order.management_state == "FILLED"
    assert order.entry_fill_price == 4500.0
    assert order.position_id == "pos1"


def test_check_retest_fill_leaves_row_untouched_while_still_resting(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="WAITING_RETEST", entry_exchange_order_id="777")
    _patch(monkeypatch, get_positions=_async([]))

    _run(cx.check_retest_fill_and_confirm(db, account, order))
    assert order.management_state == "WAITING_RETEST"


# ------------------------------------------------------------------ cancel_no_retest()

def test_cancel_no_retest_closes_the_order(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="WAITING_RETEST", entry_exchange_order_id="777")
    _patch(monkeypatch, cancel_order=_async(True), get_positions=_async([]), get_order_detail=_async(None))

    _run(cx.cancel_no_retest(db, account, order))
    db.commit()

    assert order.management_state == "NO_RETEST"
    assert order.closed_at is not None


def test_cancel_no_retest_leaves_state_unchanged_on_a_failed_cancel_call(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="WAITING_RETEST", entry_exchange_order_id="777")
    _patch(monkeypatch, cancel_order=_async(exc=RuntimeError("network error")), get_positions=_async([]), get_order_detail=_async({"id": 777, "ordStatus": "Working"}))

    _run(cx.cancel_no_retest(db, account, order))
    assert order.management_state == "WAITING_RETEST"   # never assume cancelled on a failed call


# ------------------------------------------------------------------ amend_to_breakeven()

def test_amend_to_breakeven_success(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", sl_exchange_order_id="sl1")
    _patch(monkeypatch, amend_stop_to_breakeven=_async(True))

    ok = _run(cx.amend_to_breakeven(db, account, order, stop_order_id=1, be_price=4502.0))
    db.commit()

    assert ok is True
    assert order.be_amended is True
    assert order.management_state == "RATCHETED"
    assert order.be_price == 4502.0


def test_amend_to_breakeven_failure_never_marks_amended(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", sl_exchange_order_id="sl1")
    _patch(monkeypatch, amend_stop_to_breakeven=_async(False))

    ok = _run(cx.amend_to_breakeven(db, account, order, stop_order_id=1, be_price=4502.0))
    assert ok is False
    assert order.be_amended is False
    assert order.management_state == "FILLED"


# ------------------------------------------------------------------ market_close()

def test_market_close_confirms_flat_and_finalizes(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", entry_fill_price=4500.0)
    _broker(monkeypatch, ["MNQ"])   # open before the close, flat after (Lane H C2.4)

    _run(cx.market_close(db, account, order, exit_reason="TIME_FLUSH"))
    db.commit()

    assert order.management_state == "CLOSED_TIME_FLUSH"
    assert order.exit_reason == "TIME_FLUSH"
    assert order.closed_at is not None


def test_market_close_does_not_finalize_if_still_open_after_confirm_attempts(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED")
    _patch(monkeypatch,
           emergency_market_close=_async(True),
           get_positions=_async([{"symbol": "MNQ"}]))   # still shows open every check

    _run(cx.market_close(db, account, order, exit_reason="STOP"))
    assert order.management_state == "FILLED"   # untouched -- never assumed closed


# ------------------------------------------------------------------ emergency_flatten()

def test_emergency_flatten_cancels_resting_and_closes_open_positions(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    resting = _order(db, account, plan, symbol="MNQ", management_state="WAITING_RETEST", entry_exchange_order_id="777")
    plan2 = _plan(db, symbol="MES", date_key="2026-11-04")
    open_pos = _order(db, account, plan2, symbol="MES", management_state="FILLED", entry_fill_price=4500.0)
    _broker(monkeypatch, ["MES"], cancel_order=_async(True), get_order_detail=_async(None))

    result = _run(cx.emergency_flatten(db, account))
    db.commit()

    assert resting.id in result["cancelled"]
    assert open_pos.id in result["closed"]
    assert result["errors"] == []
    assert resting.management_state == "CLOSED_MANUAL"
    assert open_pos.management_state == "CLOSED_MANUAL"


def test_emergency_flatten_collects_errors_without_stopping(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    resting = _order(db, account, plan, management_state="WAITING_RETEST", entry_exchange_order_id="777")
    _patch(monkeypatch, cancel_order=_async(exc=RuntimeError("boom")), get_positions=_async([]), get_order_detail=_async({"id": 777, "ordStatus": "Working"}))

    result = _run(cx.emergency_flatten(db, account))
    assert result["cancelled"] == []
    assert len(result["errors"]) == 1
    assert result["errors"][0]["order_id"] == resting.id


# ------------------------------------------------------------------ cancel_or_close_order() (desk-page build, 2026-11-xx)
# The shared single-order helper behind both emergency_flatten()'s own
# per-row loop (covered above) and the new POST /api/cme/orders/{id}/cancel
# route (tests/test_cme_routes.py) -- unit-tested directly here so the
# branch logic itself (not just the route wiring) is proven.

def test_cancel_or_close_order_cancels_a_resting_entry(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="WAITING_RETEST", entry_exchange_order_id="777")
    _patch(monkeypatch, cancel_order=_async(True), get_positions=_async([]), get_order_detail=_async(None))

    result = _run(cx.cancel_or_close_order(db, account, order, exit_reason="MANUAL"))
    db.commit()

    assert result == {"action": "cancelled", "order_id": order.id}
    assert order.management_state == "CLOSED_MANUAL"
    assert order.exit_reason == "MANUAL"
    assert order.closed_at is not None


def test_cancel_or_close_order_closes_an_open_position_instead(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", entry_fill_price=4500.0)
    _broker(monkeypatch, ["MNQ"])

    result = _run(cx.cancel_or_close_order(db, account, order, exit_reason="MANUAL"))
    db.commit()

    assert result == {"action": "closed", "order_id": order.id}
    assert order.management_state == "CLOSED_MANUAL"


def test_cancel_or_close_order_rejects_an_already_terminal_order(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="CLOSED_STOP")

    with pytest.raises(ValueError):
        _run(cx.cancel_or_close_order(db, account, order, exit_reason="MANUAL"))
    # untouched -- never silently no-ops into a wrong state
    assert order.management_state == "CLOSED_STOP"


def test_cancel_or_close_order_double_cancel_second_call_raises_not_double_cancels(db, monkeypatch):
    """The real financial-risk case this route must get right: two near-
    simultaneous cancel requests against the same resting order must never
    both succeed and both call cancel_order() against the real exchange."""
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="WAITING_RETEST", entry_exchange_order_id="777")
    _patch(monkeypatch, cancel_order=_async(True), get_positions=_async([]), get_order_detail=_async(None))

    first = _run(cx.cancel_or_close_order(db, account, order, exit_reason="MANUAL"))
    db.commit()
    assert first["action"] == "cancelled"
    assert order.management_state == "CLOSED_MANUAL"

    # Second call sees the now-terminal state and must raise, not re-cancel.
    with pytest.raises(ValueError):
        _run(cx.cancel_or_close_order(db, account, order, exit_reason="MANUAL"))


# ------------------------------------------------------------------ Telegram push wiring (2026-11-xx)
# _notify_account()/_notify_unprotected() already send email unconditionally
# in these tests' environment (no real SMTP/Telegram creds set -- both fail
# closed silently, caught by the enclosing try/except). These tests mock
# notify.send_account_telegram() directly to prove the NEW dispatch wiring
# itself: the right category fires for each real event, with a real
# non-empty message built from the order's own fields.

def test_notify_account_fill_dispatches_the_right_telegram_category(db, monkeypatch):
    import notify
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, entry_fill_price=4500.0, sl_price_initial=4480.0)
    calls = []
    monkeypatch.setattr(notify, "send_account_telegram", lambda cat, text, acct_id: calls.append((cat, text, acct_id)))

    cx._notify_account(db, account, "build_cme_entry_email", order)

    assert len(calls) == 1
    cat, text, acct_id = calls[0]
    assert cat == "fill"
    assert "MNQ" in text and "4500" in text
    assert acct_id == account.id


def test_notify_account_breakeven_dispatches_the_right_telegram_category(db, monkeypatch):
    import notify
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, be_price=4502.0)
    calls = []
    monkeypatch.setattr(notify, "send_account_telegram", lambda cat, text, acct_id: calls.append((cat, text, acct_id)))

    cx._notify_account(db, account, "build_cme_breakeven_email", order)

    assert len(calls) == 1
    cat, text, _ = calls[0]
    assert cat == "breakeven"
    assert "4502" in text


def test_notify_account_exit_dispatches_the_right_telegram_category(db, monkeypatch):
    import notify
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, exit_reason="T1", exit_price=4540.0, realized_pnl_r=1.0, dollar_pnl=142.50)
    calls = []
    monkeypatch.setattr(notify, "send_account_telegram", lambda cat, text, acct_id: calls.append((cat, text, acct_id)))

    cx._notify_account(db, account, "build_cme_exit_email", order)

    assert len(calls) == 1
    cat, text, _ = calls[0]
    assert cat == "exit"
    assert "T1" in text and "+1.00R" in text


def test_notify_unprotected_dispatches_the_problem_category(db, monkeypatch):
    import notify
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, sl_price_initial=4480.0, position_id="pos1")
    calls = []
    monkeypatch.setattr(notify, "send_account_telegram", lambda cat, text, acct_id: calls.append((cat, text, acct_id)))

    cx._notify_unprotected(db, account, order, "stop placement failed")

    assert len(calls) == 1
    cat, text, _ = calls[0]
    assert cat == "problem"
    assert "stop placement failed" in text


def test_notify_account_telegram_failure_never_blocks_the_email_send(db, monkeypatch):
    """A broken Telegram call must never prevent the existing email send
    -- both are inside the same try/except, but Telegram is dispatched
    AFTER email, so email must have already gone out by the time Telegram
    breaks."""
    import notify
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, entry_fill_price=4500.0)
    email_calls = []
    monkeypatch.setattr(notify, "send_account_email", lambda subject, body, acct_id: email_calls.append(subject))
    monkeypatch.setattr(notify, "send_account_telegram", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("telegram down")))

    cx._notify_account(db, account, "build_cme_entry_email", order)   # must not raise

    assert len(email_calls) == 1


# ------------------------------------------------------------------ fee capture (2026-11-xx):
# CrossTrade accounts only -- direct-Tradovate fee capture is explicitly
# deferred (see cme_crosstrade_client.py's own header).

def test_entry_fill_sets_entry_fee_usd_for_a_crosstrade_account(db, monkeypatch):
    account = _ready_crosstrade_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="WAITING_RETEST", entry_exchange_order_id="777")
    _patch_crosstrade(monkeypatch,
                       get_positions=_async([{"symbol": "MNQ", "id": "pos1", "netPrice": "4500.0"}]),
                       get_fills_for_order=_async([{"commission": "0.35"}]))

    _run(cx.check_retest_fill_and_confirm(db, account, order))
    db.commit()

    assert order.management_state == "FILLED"
    assert order.entry_fee_usd == pytest.approx(0.35)


def test_entry_fee_not_attempted_for_a_tradovate_direct_account(db, monkeypatch):
    # Direct-Tradovate fee capture is explicitly deferred -- no call, no
    # error, entry_fee_usd simply stays None.
    account = _ready_account(db)   # plain Tradovate-direct account
    plan = _plan(db)
    order = _order(db, account, plan, management_state="WAITING_RETEST", entry_exchange_order_id="777")
    _patch(monkeypatch, get_positions=_async([{"symbol": "MNQ", "id": "pos1", "netPrice": "4500.0"}]))

    _run(cx.check_retest_fill_and_confirm(db, account, order))
    db.commit()

    assert order.management_state == "FILLED"
    assert order.entry_fee_usd is None


def test_entry_fee_fetch_failure_does_not_break_fill_confirmation(db, monkeypatch):
    account = _ready_crosstrade_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="WAITING_RETEST", entry_exchange_order_id="777")
    _patch_crosstrade(monkeypatch,
                       get_positions=_async([{"symbol": "MNQ", "id": "pos1", "netPrice": "4500.0"}]),
                       get_fills_for_order=_async(exc=RuntimeError("CrossTrade unavailable")))

    _run(cx.check_retest_fill_and_confirm(db, account, order))
    db.commit()

    assert order.management_state == "FILLED"   # fill confirmation unaffected
    assert order.entry_fee_usd is None


def test_market_close_sets_exit_fee_usd_for_a_crosstrade_account(db, monkeypatch):
    account = _ready_crosstrade_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", entry_fill_price=4500.0)
    calls = []

    async def _fills_history(self, from_ts, to_ts, is_demo=False):
        calls.append({"from_ts": from_ts, "to_ts": to_ts, "is_demo": is_demo})
        return [{"commission": "0.35", "fees": "0.10"}]

    _broker(monkeypatch, ["MNQ"], patcher=_patch_crosstrade, get_fills_history=_fills_history)

    _run(cx.market_close(db, account, order, exit_reason="TIME_FLUSH"))
    db.commit()

    assert order.management_state == "CLOSED_TIME_FLUSH"
    assert order.exit_fee_usd == pytest.approx(0.45)
    # Lane H C2.5: one lookup for the exit fill price, one for the exit fee.
    assert len(calls) == 2
    assert all(c["is_demo"] is False for c in calls)


def test_exit_fee_not_attempted_for_a_tradovate_direct_account(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", entry_fill_price=4500.0)
    _broker(monkeypatch, ["MNQ"])

    _run(cx.market_close(db, account, order, exit_reason="TIME_FLUSH"))
    db.commit()

    assert order.management_state == "CLOSED_TIME_FLUSH"
    assert order.exit_fee_usd is None


def test_exit_fee_fetch_failure_does_not_break_closure_for_a_crosstrade_account(db, monkeypatch):
    account = _ready_crosstrade_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", entry_fill_price=4500.0)
    _broker(monkeypatch, ["MNQ"], patcher=_patch_crosstrade,
            get_fills_history=_async(exc=RuntimeError("CrossTrade unavailable")))

    _run(cx.market_close(db, account, order, exit_reason="TIME_FLUSH"))
    db.commit()

    assert order.management_state == "CLOSED_TIME_FLUSH"   # real closure unaffected
    assert order.exit_fee_usd is None


# ================================================================== Lane H batch 1 (SPEC-H-20261114): C9 + C5 verified cancel

def _audit_messages(db, account, event_type=None):
    db.flush()
    q = db.query(ExecutorAuditLog).filter_by(account_id=account.id)
    if event_type:
        q = q.filter_by(event_type=event_type)
    return [r.message or "" for r in q.all()]


def _resting(db, account, plan):
    return _order(db, account, plan, management_state="WAITING_RETEST", entry_exchange_order_id="777")


def test_c9_place_resting_bracket_refuses_when_live_orders_off(db, monkeypatch):
    account = _ready_account(db)
    ec.disable_live_orders(db, by="test")
    db.commit()
    plan = _plan(db)
    order = _order(db, account, plan)
    calls = {"n": 0}

    async def _count(self, *a, **kw):
        calls["n"] += 1
        return {"orderId": 1}
    _patch(monkeypatch, place_bracket_limit_order=_count)

    _run(cx.place_resting_bracket(db, account, plan, order))
    db.flush()
    assert calls["n"] == 0
    assert order.management_state == "SKIPPED_LIVE_ORDERS_OFF"
    assert order.entry_exchange_order_id is None
    assert any("Live Orders switch is OFF" in m for m in _audit_messages(db, account, "ERROR"))


def test_c9_place_resting_bracket_switch_read_failure_fails_closed(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan)
    monkeypatch.setattr(ec, "is_live_orders_enabled", lambda db_: False)

    async def _fail(self, *a, **kw):
        raise AssertionError("must not place")
    _patch(monkeypatch, place_bracket_limit_order=_fail)

    _run(cx.place_resting_bracket(db, account, plan, order))
    assert order.management_state == "SKIPPED_LIVE_ORDERS_OFF"


def test_c9_emergency_flatten_still_works_with_live_orders_off(db, monkeypatch):
    account = _ready_account(db)
    ec.disable_live_orders(db, by="test")
    db.commit()
    plan = _plan(db)
    resting = _resting(db, account, plan)
    plan2 = _plan(db, symbol="MES")
    open_pos = _order(db, account, plan2, symbol="MES", management_state="FILLED", entry_fill_price=4500.0)
    _broker(monkeypatch, ["MES"], cancel_order=_async(True), get_order_detail=_async(None))

    result = _run(cx.emergency_flatten(db, account))
    assert resting.id in result["cancelled"]
    assert open_pos.id in result["closed"]


def test_c5_cancel_no_retest_false_return_is_a_failed_cancel(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _resting(db, account, plan)
    _patch(monkeypatch, cancel_order=_async(False), get_positions=_async([]), get_order_detail=_async({"id": 777, "ordStatus": "Working"}))

    result = _run(cx.cancel_no_retest(db, account, order))
    db.flush()
    assert result == "failed"
    assert order.management_state == "WAITING_RETEST"
    assert order.closed_at is None
    assert any("cancel failed" in m for m in _audit_messages(db, account, "ERROR"))


def test_c5_cancel_no_retest_detects_fill_race_and_confirms_fill(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _resting(db, account, plan)
    _patch(monkeypatch, cancel_order=_async(False),
           get_positions=_async([{"symbol": "MNQ", "netPrice": 4500.0, "id": 9}]))

    result = _run(cx.cancel_no_retest(db, account, order))
    assert result == "filled"
    assert order.management_state == "FILLED"
    assert order.entry_fill_price == 4500.0


def test_c5_cancel_reported_ok_but_order_still_working_is_not_booked(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _resting(db, account, plan)
    _patch(monkeypatch, cancel_order=_async(True), get_positions=_async([]),
           get_order_detail=_async({"id": 777, "ordStatus": "Working"}))

    result = _run(cx.cancel_no_retest(db, account, order))
    assert result == "failed"
    assert order.management_state == "WAITING_RETEST"


def test_c5_cancel_verified_gone_books_no_retest(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _resting(db, account, plan)
    _patch(monkeypatch, cancel_order=_async(True), get_positions=_async([]), get_order_detail=_async(None))

    result = _run(cx.cancel_no_retest(db, account, order))
    db.flush()
    assert result == "cancelled"
    assert order.management_state == "NO_RETEST"
    assert order.closed_at is not None
    t = db.query(CMETransition).filter_by(cme_order_id=order.id).all()
    assert t[-1].to_state == "NO_RETEST" and '"cancel_verified": true' in t[-1].detail_json


def test_c5_cancel_with_unreadable_order_status_is_accepted_but_marked_unverified(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _resting(db, account, plan)
    _patch(monkeypatch, cancel_order=_async(True), get_positions=_async([]), get_order_detail=_async({"id": 777}))

    result = _run(cx.cancel_no_retest(db, account, order))
    db.flush()
    assert result == "cancelled"
    t = db.query(CMETransition).filter_by(cme_order_id=order.id).all()
    assert '"cancel_verified": false' in t[-1].detail_json


def test_c5_positions_unreadable_leaves_state_unchanged(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _resting(db, account, plan)
    _patch(monkeypatch, cancel_order=_async(True), get_positions=_async(exc=RuntimeError("down")))

    assert _run(cx.cancel_no_retest(db, account, order)) == "failed"
    assert order.management_state == "WAITING_RETEST"


def test_c5_emergency_flatten_reports_an_unconfirmed_cancel_as_an_error(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _resting(db, account, plan)
    _patch(monkeypatch, cancel_order=_async(False), get_positions=_async([]), get_order_detail=_async({"id": 777, "ordStatus": "Working"}))

    result = _run(cx.emergency_flatten(db, account))
    assert result["cancelled"] == []
    assert len(result["errors"]) == 1 and result["errors"][0]["order_id"] == order.id
    assert order.management_state == "WAITING_RETEST"


def test_c5_crosstrade_cancel_failure_reason_is_audited(db, monkeypatch):
    account = _ready_crosstrade_account(db)
    plan = _plan(db)
    order = _resting(db, account, plan)

    async def _reject(self, order_id):
        self.last_cancel_error = "HTTP 400: order not cancellable"
        return False
    _patch_crosstrade(monkeypatch, cancel_order=_reject, get_positions=_async([]), get_order_detail=_async({"id": 777, "ordStatus": "Working"}))

    _run(cx.cancel_no_retest(db, account, order))
    db.flush()
    assert order.management_state == "WAITING_RETEST"
    assert any("HTTP 400: order not cancellable" in m for m in _audit_messages(db, account, "ERROR"))


# ================================================================== Lane H batch 2 (SPEC-H-20261114): C1 bracket legs + BE

_STOP_LEG = {"id": 12, "orderType": "Stop", "action": "Sell", "stopPrice": 4480.0, "orderQty": 2, "ordStatus": "Working"}
_TARGET_LEG = {"id": 11, "orderType": "Limit", "action": "Sell", "price": 4540.0, "orderQty": 2, "ordStatus": "Working"}


def _filled_entry(db, account, plan, **kw):
    defaults = dict(management_state="WAITING_RETEST", entry_exchange_order_id="777", sl_price_initial=4480.0, sl_price_current=4480.0)
    defaults.update(kw)
    return _order(db, account, plan, **defaults)


def _count_unprotected(monkeypatch):
    calls = []
    monkeypatch.setattr(cx, "_notify_unprotected", lambda db_, a, o, detail: calls.append(detail))
    return calls


def test_c1_place_resting_bracket_stores_leg_ids_when_response_carries_them(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan)
    _patch(monkeypatch, place_bracket_limit_order=_async({"orderId": 1, "oso1Id": 11, "oso2Id": 12}))
    _run(cx.place_resting_bracket(db, account, plan, order))
    db.flush()
    assert order.sl_exchange_order_id == "12" and order.tp_exchange_order_id == "11"
    t = db.query(CMETransition).filter_by(cme_order_id=order.id, to_state="WAITING_RETEST").one()
    assert "raw_placement_response" in t.detail_json


def test_c1_place_resting_bracket_without_leg_ids_records_raw_response(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan)
    _patch(monkeypatch, place_bracket_limit_order=_async({"orderId": 1}))
    _run(cx.place_resting_bracket(db, account, plan, order))
    db.flush()
    assert order.management_state == "WAITING_RETEST" and order.sl_exchange_order_id is None
    t = db.query(CMETransition).filter_by(cme_order_id=order.id, to_state="WAITING_RETEST").one()
    assert '"orderId": 1' in t.detail_json


def test_c1_fill_confirm_discovers_stop_and_target_legs(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _filled_entry(db, account, plan)
    _patch(monkeypatch, get_positions=_async([{"symbol": "MNQ", "netPrice": 4500.0}]),
           get_orders=_async([_STOP_LEG, _TARGET_LEG]))
    _run(cx.check_retest_fill_and_confirm(db, account, order))
    assert order.management_state == "FILLED"
    assert order.sl_exchange_order_id == "12" and order.tp_exchange_order_id == "11"
    assert order.entry_price_source == "BROKER_POSITION_AVG"


def test_c1_fill_confirm_with_no_stop_leg_goes_unprotected_and_alerts(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _filled_entry(db, account, plan)
    alerts = _count_unprotected(monkeypatch)
    _patch(monkeypatch, get_positions=_async([{"symbol": "MNQ", "netPrice": 4500.0}]), get_orders=_async([]))
    _run(cx.check_retest_fill_and_confirm(db, account, order))
    assert order.management_state == "ENTRY_FILLED_UNPROTECTED"
    assert len(alerts) == 1
    assert any("UNPROTECTED" in m for m in _audit_messages(db, account, "ERROR"))


def test_c1_fill_confirm_with_two_candidate_stops_does_not_guess(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _filled_entry(db, account, plan)
    alerts = _count_unprotected(monkeypatch)
    _patch(monkeypatch, get_positions=_async([{"symbol": "MNQ", "netPrice": 4500.0}]),
           get_orders=_async([_STOP_LEG, dict(_STOP_LEG, id=13)]))
    _run(cx.check_retest_fill_and_confirm(db, account, order))
    assert order.management_state == "FILLED" and order.sl_exchange_order_id is None
    assert alerts == []
    assert any("ambiguous" in m for m in _audit_messages(db, account, "ERROR"))


def test_c1_fill_confirm_get_orders_failure_is_unknown_not_unprotected(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _filled_entry(db, account, plan)
    alerts = _count_unprotected(monkeypatch)
    _patch(monkeypatch, get_positions=_async([{"symbol": "MNQ", "netPrice": 4500.0}]),
           get_orders=_async(exc=RuntimeError("down")))
    _run(cx.check_retest_fill_and_confirm(db, account, order))
    assert order.management_state == "FILLED" and order.sl_exchange_order_id is None
    assert alerts == []


def test_c1_unprotected_row_promoted_to_filled_when_stop_leg_appears(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="ENTRY_FILLED_UNPROTECTED", sl_price_initial=4480.0, entry_fill_price=4500.0)
    _patch(monkeypatch, get_orders=_async([_STOP_LEG]))
    assert _run(cx.ensure_bracket_legs(db, account, order)) == "found"
    db.flush()
    assert order.management_state == "FILLED" and order.sl_exchange_order_id == "12"
    assert db.query(CMETransition).filter_by(cme_order_id=order.id, from_state="ENTRY_FILLED_UNPROTECTED", to_state="FILLED").count() == 1


def test_c1_amend_confirmed_by_readback(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", sl_exchange_order_id="12")
    _patch(monkeypatch, amend_stop_to_breakeven=_async(True), get_order_detail=_async({"id": 12, "stopPrice": 4502.0}))
    assert _run(cx.amend_to_breakeven(db, account, order, stop_order_id=12, be_price=4502.0)) is True
    db.flush()
    t = db.query(CMETransition).filter_by(cme_order_id=order.id, to_state="RATCHETED").one()
    assert '"confirmed": true' in t.detail_json


def test_c1_amend_readback_price_mismatch_is_not_marked_amended(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", sl_exchange_order_id="12")
    _patch(monkeypatch, amend_stop_to_breakeven=_async(True), get_order_detail=_async({"id": 12, "stopPrice": 4480.0}))
    assert _run(cx.amend_to_breakeven(db, account, order, stop_order_id=12, be_price=4502.0)) is False
    assert order.be_amended is False and order.management_state == "FILLED"
    assert any("read back stop 4480" in m for m in _audit_messages(db, account, "ERROR"))


def test_c1_amend_readback_unavailable_marks_amended_unconfirmed(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", sl_exchange_order_id="12")
    _patch(monkeypatch, amend_stop_to_breakeven=_async(True), get_order_detail=_async(None))
    assert _run(cx.amend_to_breakeven(db, account, order, stop_order_id=12, be_price=4502.0)) is True
    db.flush()
    t = db.query(CMETransition).filter_by(cme_order_id=order.id, to_state="RATCHETED").one()
    assert '"confirmed": false' in t.detail_json
    assert _audit_messages(db, account, "WARNING")


def test_c1_amend_failure_alerts_owner_once_not_every_tick(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", sl_exchange_order_id="12")
    _patch(monkeypatch, amend_stop_to_breakeven=_async(False))
    alerts = []
    monkeypatch.setattr(cx, "_notify_owner_problem", lambda a, o, tail, body: alerts.append(tail))
    _run(cx.amend_to_breakeven(db, account, order, stop_order_id=12, be_price=4502.0))
    _run(cx.amend_to_breakeven(db, account, order, stop_order_id=12, be_price=4502.0))
    assert len(alerts) == 1


# ================================================================== Lane H batch 2: C2 exit price, reconcile, guarded close

def _open_row(db, account, plan, **kw):
    defaults = dict(management_state="FILLED", entry_fill_price=4500.0, point_multiplier=2.0,
                    sl_price_initial=4480.0, sl_price_current=4480.0)
    defaults.update(kw)
    return _order(db, account, plan, **defaults)


def test_c2_market_close_crosstrade_books_broker_fill_price(db, monkeypatch):
    account = _ready_crosstrade_account(db)
    plan = _plan(db)
    order = _open_row(db, account, plan)
    _broker(monkeypatch, ["MNQ"], patcher=_patch_crosstrade,
            get_fills_history=_async([{"price": 4520.0, "qty": 2, "action": "Sell", "instrument": "MNQZ6"}]))
    _run(cx.market_close(db, account, order, exit_reason="TIME_FLUSH"))
    assert order.management_state == "CLOSED_TIME_FLUSH"
    assert order.exit_price == 4520.0 and order.exit_price_source == "BROKER_FILL"
    assert order.realized_pnl_r == pytest.approx(1.0) and order.dollar_pnl == pytest.approx(80.0)


def test_c2_market_close_tradovate_direct_uses_last_5m_close_and_labels_it(db, monkeypatch):
    import cme_market
    account = _ready_account(db)
    plan = _plan(db)
    order = _open_row(db, account, plan)
    _broker(monkeypatch, ["MNQ"])
    async def _bars(*a, **kw):
        return [{"close": 4505.0}, {"close": 4510.0}]
    monkeypatch.setattr(cme_market, "fetch_confirmed_5m", _bars)
    _run(cx.market_close(db, account, order, exit_reason="TIME_FLUSH"))
    db.flush()
    assert order.exit_price == 4510.0 and order.exit_price_source == "APPROX_LAST_5M_CLOSE"
    assert order.realized_pnl_r == pytest.approx(0.5) and order.dollar_pnl == pytest.approx(40.0)
    t = db.query(CMETransition).filter_by(cme_order_id=order.id, to_state="CLOSED_TIME_FLUSH").one()
    assert '"approximated": true' in t.detail_json


def test_c2_market_close_no_price_available_leaves_r_and_dollars_null(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _open_row(db, account, plan)
    _broker(monkeypatch, ["MNQ"])   # autouse fixture: no bars
    _run(cx.market_close(db, account, order, exit_reason="TIME_FLUSH"))
    assert order.management_state == "CLOSED_TIME_FLUSH"
    assert order.exit_price_source == "UNKNOWN"
    assert order.exit_price is None and order.realized_pnl_r is None and order.dollar_pnl is None


def test_c2_market_close_does_not_send_close_when_already_flat(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _open_row(db, account, plan)
    state = _broker(monkeypatch, [])
    _run(cx.market_close(db, account, order, exit_reason="STOP"))
    assert state["closes"] == 0
    assert order.management_state == "FILLED"   # flat, no leg fill confirmed -> nothing booked


def test_c2_market_close_does_not_send_when_position_query_fails(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _open_row(db, account, plan)
    sent = {"n": 0}
    async def _close(self, *a, **kw):
        sent["n"] += 1
        return True
    _patch(monkeypatch, get_positions=_async(exc=RuntimeError("down")), emergency_market_close=_close)
    _run(cx.market_close(db, account, order, exit_reason="TIME_FLUSH"))
    assert sent["n"] == 0 and order.management_state == "FILLED"
    assert any("NOT sent" in m for m in _audit_messages(db, account, "ERROR"))


def test_c2_market_close_false_return_is_treated_as_not_sent(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _open_row(db, account, plan)
    _broker(monkeypatch, ["MNQ"], close_ok=False)
    _run(cx.market_close(db, account, order, exit_reason="TIME_FLUSH"))
    assert order.management_state == "FILLED"
    assert any("rejected by the broker" in m for m in _audit_messages(db, account, "ERROR"))


def test_c2_reconcile_books_exchange_stop_fill_without_sending_an_order(db, monkeypatch):
    account = _ready_crosstrade_account(db)
    plan = _plan(db)
    order = _open_row(db, account, plan, sl_exchange_order_id="12", tp_exchange_order_id="11")
    async def _detail(self, order_id):
        return {"id": order_id, "status": "Filled" if order_id == 12 else "Canceled"}
    state = _broker(monkeypatch, [], patcher=_patch_crosstrade, get_order_detail=_detail,
                    get_fills_for_order=_async([{"price": 4479.5, "qty": 2}]))
    assert _run(cx.reconcile_open_position(db, account, order)) is True
    assert order.management_state == "CLOSED_STOP"
    assert order.exit_price == 4479.5 and order.exit_price_source == "BROKER_FILL"
    assert state["closes"] == 0


def test_c2_reconcile_books_target_with_order_price_when_no_fill_price(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _open_row(db, account, plan, sl_exchange_order_id="12", tp_exchange_order_id="11")
    async def _detail(self, order_id):
        return {"id": order_id, "ordStatus": "Filled" if order_id == 11 else "Canceled"}
    _broker(monkeypatch, [], get_order_detail=_detail)
    assert _run(cx.reconcile_open_position(db, account, order)) is True
    assert order.management_state == "CLOSED_TARGET"
    assert order.exit_price == 4540.0 and order.exit_price_source == "APPROX_BRACKET_ORDER_PRICE"


def test_c2_reconcile_flat_but_no_leg_fill_confirmed_books_nothing_and_alerts_once(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _open_row(db, account, plan)
    state = _broker(monkeypatch, [])
    assert _run(cx.reconcile_open_position(db, account, order)) is True
    assert _run(cx.reconcile_open_position(db, account, order)) is True
    assert order.management_state == "FILLED" and state["closes"] == 0
    assert len([m for m in _audit_messages(db, account, "ERROR") if "CHECK BROKER" in m]) == 1


def test_c2_reconcile_both_legs_cancelled_and_flat_books_external(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _open_row(db, account, plan, sl_exchange_order_id="12", tp_exchange_order_id="11")
    _broker(monkeypatch, [], get_order_detail=_async({"ordStatus": "Canceled"}))
    assert _run(cx.reconcile_open_position(db, account, order)) is True
    assert order.management_state == "CLOSED_EXTERNAL"


def test_c2_reconcile_still_open_returns_false(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _open_row(db, account, plan)
    _broker(monkeypatch, ["MNQ"])
    assert _run(cx.reconcile_open_position(db, account, order)) is False
    assert order.management_state == "FILLED"


def test_c2_fill_qty_mismatch_falls_back_to_approximation(db, monkeypatch):
    import cme_market
    account = _ready_crosstrade_account(db)
    plan = _plan(db)
    order = _open_row(db, account, plan)
    _broker(monkeypatch, ["MNQ"], patcher=_patch_crosstrade,
            get_fills_history=_async([{"price": 4520.0, "qty": 3, "action": "Sell"}]))
    async def _bars(*a, **kw):
        return [{"close": 4510.0}]
    monkeypatch.setattr(cme_market, "fetch_confirmed_5m", _bars)
    _run(cx.market_close(db, account, order, exit_reason="TIME_FLUSH"))
    db.flush()
    assert order.exit_price_source == "APPROX_LAST_5M_CLOSE"
    t = db.query(CMETransition).filter_by(cme_order_id=order.id, to_state="CLOSED_TIME_FLUSH").one()
    assert "fill_qty_mismatch" in t.detail_json


def test_c2_fee_sweep_upgrades_approx_exit_to_broker_fill(db, monkeypatch):
    account = _ready_crosstrade_account(db)
    plan = _plan(db)
    order = _open_row(db, account, plan, management_state="CLOSED_TIME_FLUSH", exit_reason="TIME_FLUSH",
                      exit_price=4510.0, exit_price_source="APPROX_LAST_5M_CLOSE",
                      exit_time=database.datetime.datetime.utcnow(), closed_at=database.datetime.datetime.utcnow())
    _patch_crosstrade(monkeypatch, get_fills_history=_async([{"price": 4515.0, "qty": 2, "action": "Sell"}]))
    assert _run(cx.upgrade_approx_exit_price(db, account, order)) is True
    db.flush()
    assert order.exit_price == 4515.0 and order.exit_price_source == "BROKER_FILL"
    assert order.realized_pnl_r == pytest.approx(0.75)
    t = db.query(CMETransition).filter_by(cme_order_id=order.id).all()
    assert any("exit_price_upgraded_from" in (x.detail_json or "") for x in t)


def test_c2_init_db_marks_historical_zero_r_artifacts_unknown(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _open_row(db, account, plan, management_state="CLOSED_TIME_FLUSH", exit_reason="TIME_FLUSH",
                      exit_price=4500.0, realized_pnl_r=0.0)
    db.add(CMETransition(cme_plan_id=plan.id, cme_order_id=order.id, from_state="FILLED", to_state="CLOSED_TIME_FLUSH",
                         price=4500.0, realized_pnl_r=0.0, detail_json='{"approximated": true}'))
    db.commit()
    assert database._fix_cme_zero_r_artifacts() == 1
    db.expire_all()
    fixed = db.query(CMEOrder).filter_by(id=order.id).one()
    assert fixed.exit_price is None and fixed.realized_pnl_r is None and fixed.exit_price_source == "UNKNOWN"
    assert db.query(ExecutorAuditLog).filter_by(event_type="DATA_FIX").count() == 1
    assert database._fix_cme_zero_r_artifacts() == 0   # idempotent


@pytest.mark.parametrize("source,expect_approx,expect_unavailable", [
    ("BROKER_FILL", False, False), ("APPROX_LAST_5M_CLOSE", True, False), ("UNKNOWN", False, True),
])
def test_c2_exit_email_says_approximated_only_when_it_is(source, expect_approx, expect_unavailable):
    import cme_notify
    order = {"symbol": "MNQ", "direction": "LONG", "exit_reason": "TIME_FLUSH", "id": 1,
             "exit_price": None if source == "UNKNOWN" else 4510.0, "exit_price_source": source, "dollar_pnl": 40.0}
    _subject, body = cme_notify.build_cme_exit_email(order)
    assert ("approximated" in body) is expect_approx
    assert ("unavailable" in body) is expect_unavailable


# ------------------------------------------------------------------ H5 follow-ups to C5 (built in batch 2)

def test_c5_rejected_cancel_of_an_already_expired_order_closes_the_row(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _resting(db, account, plan)
    _patch(monkeypatch, cancel_order=_async(False), get_positions=_async([]), get_order_detail=_async({"id": 777, "ordStatus": "Expired"}))
    assert _run(cx.cancel_no_retest(db, account, order)) == "cancelled"
    db.flush()
    t = db.query(CMETransition).filter_by(cme_order_id=order.id, to_state="NO_RETEST").one()
    assert "cancel_rejected_but_order_gone" in t.detail_json


def test_c5_entry_filled_without_position_is_not_booked_and_alerts_once(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _resting(db, account, plan)
    alerts = []
    monkeypatch.setattr(cx, "_notify_owner_problem", lambda a, o, tail, body: alerts.append(tail))
    _patch(monkeypatch, cancel_order=_async(False), get_positions=_async([]), get_order_detail=_async({"id": 777, "ordStatus": "Filled"}))
    assert _run(cx.cancel_no_retest(db, account, order)) == "failed"
    assert _run(cx.cancel_no_retest(db, account, order)) == "failed"
    assert order.management_state == "WAITING_RETEST"
    assert len(alerts) == 1


def test_c5_working_bracket_leg_is_cancelled_before_the_row_closes(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="WAITING_RETEST", entry_exchange_order_id="777",
                   sl_exchange_order_id="12", tp_exchange_order_id="11")
    status = {777: None, 12: "Working", 11: None}
    cancelled = []
    async def _cancel(self, order_id):
        cancelled.append(order_id)
        status[order_id] = None if order_id == 777 else "Canceled"
        return True
    async def _detail(self, order_id):
        s = status.get(order_id)
        return None if s is None else {"id": order_id, "ordStatus": s}
    _patch(monkeypatch, cancel_order=_cancel, get_positions=_async([]), get_order_detail=_detail)
    assert _run(cx.cancel_no_retest(db, account, order)) == "cancelled"
    assert cancelled == [777, 12]


def test_c5_leg_that_will_not_cancel_keeps_the_row_open(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="WAITING_RETEST", entry_exchange_order_id="777", sl_exchange_order_id="12")
    async def _cancel(self, order_id):
        return order_id == 777
    async def _detail(self, order_id):
        return None if order_id == 777 else {"id": order_id, "ordStatus": "Working"}
    _patch(monkeypatch, cancel_order=_cancel, get_positions=_async([]), get_order_detail=_detail)
    assert _run(cx.cancel_no_retest(db, account, order)) == "failed"
    assert order.management_state == "WAITING_RETEST"

