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
    _patch(monkeypatch, cancel_order=_async(True))

    _run(cx.cancel_no_retest(db, account, order))
    db.commit()

    assert order.management_state == "NO_RETEST"
    assert order.closed_at is not None


def test_cancel_no_retest_leaves_state_unchanged_on_a_failed_cancel_call(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="WAITING_RETEST", entry_exchange_order_id="777")
    _patch(monkeypatch, cancel_order=_async(exc=RuntimeError("network error")))

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
    _patch(monkeypatch,
           emergency_market_close=_async(True),
           get_positions=_async([]))   # confirmed flat on first check

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
    _patch(monkeypatch,
           cancel_order=_async(True),
           emergency_market_close=_async(True),
           get_positions=_async([]))

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
    _patch(monkeypatch, cancel_order=_async(exc=RuntimeError("boom")))

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
    _patch(monkeypatch, cancel_order=_async(True))

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
    _patch(monkeypatch, emergency_market_close=_async(True), get_positions=_async([]))

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
    _patch(monkeypatch, cancel_order=_async(True))

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

    _patch_crosstrade(monkeypatch,
                       emergency_market_close=_async(True),
                       get_positions=_async([]),
                       get_fills_history=_fills_history)

    _run(cx.market_close(db, account, order, exit_reason="TIME_FLUSH"))
    db.commit()

    assert order.management_state == "CLOSED_TIME_FLUSH"
    assert order.exit_fee_usd == pytest.approx(0.45)
    assert len(calls) == 1
    assert calls[0]["is_demo"] is False


def test_exit_fee_not_attempted_for_a_tradovate_direct_account(db, monkeypatch):
    account = _ready_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", entry_fill_price=4500.0)
    _patch(monkeypatch, emergency_market_close=_async(True), get_positions=_async([]))

    _run(cx.market_close(db, account, order, exit_reason="TIME_FLUSH"))
    db.commit()

    assert order.management_state == "CLOSED_TIME_FLUSH"
    assert order.exit_fee_usd is None


def test_exit_fee_fetch_failure_does_not_break_closure_for_a_crosstrade_account(db, monkeypatch):
    account = _ready_crosstrade_account(db)
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", entry_fill_price=4500.0)
    _patch_crosstrade(monkeypatch,
                       emergency_market_close=_async(True),
                       get_positions=_async([]),
                       get_fills_history=_async(exc=RuntimeError("CrossTrade unavailable")))

    _run(cx.market_close(db, account, order, exit_reason="TIME_FLUSH"))
    db.commit()

    assert order.management_state == "CLOSED_TIME_FLUSH"   # real closure unaffected
    assert order.exit_fee_usd is None
