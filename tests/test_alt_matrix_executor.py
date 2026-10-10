"""Coverage for alt_matrix_executor.py -- real Bitunix order-placement
mechanics for Alt Matrix LIVE accounts. Same fake-client pattern as
tests/test_alt_matrix_portfolio.py and tests/test_executor_live_e1_engine.py:
a real file-backed SQLite DB, the Bitunix client monkeypatched (no real
network calls)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./kabroda_test_alt_matrix_executor.db")

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount, AltMatrixOrder, AltMatrixTransition, ExecutorSizingPolicy, ExecutorAuditLog
import executor_accounts as ea
import executor_control as ec
import executor_bitunix_client as ebc
import alt_matrix_executor as ax


def _clean_db_files():
    for path in ["kabroda_test_alt_matrix_executor.db", "kabroda_test_alt_matrix_executor.db-journal",
                 "kabroda_test_alt_matrix_executor.db-shm", "kabroda_test_alt_matrix_executor.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    for model in (AltMatrixTransition, AltMatrixOrder, ExecutorSizingPolicy, ExecutorAuditLog, ExecutorAccount):
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


def _ready_account(db, label="alt_matrix_exec_test"):
    account = ea.create_account(db, user_id=1, label=label)
    db.flush()
    account.mode = "LIVE"
    ea.set_credentials(db, account, api_key="fake-key", api_secret="fake-secret", set_by="test@kabroda.com")
    ec.enable_live_orders(db, reason="testing", by="andy@kabroda.com")
    db.commit()
    return account


_plan_id_counter = {"n": 0}


def _order(db, account, **overrides):
    _plan_id_counter["n"] += 1
    defaults = dict(
        alt_matrix_plan_id=_plan_id_counter["n"], account_id=account.id, mode="LIVE",
        symbol="SOL/USDT", direction="LONG", management_state="PENDING_ENTRY",
        qty=10.0, r_distance=2.0, sl_price_initial=98.0,
    )
    defaults.update(overrides)
    order = AltMatrixOrder(**defaults)
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


def _pair_resp():
    return {"code": 0, "data": [{"symbol": "SOLUSDT", "basePrecision": 1, "quotePrecision": 2, "minTradeVolume": "0.1"}], "msg": "Success"}


def _order_detail_resp(status="FILLED"):
    return {"code": 0, "data": {"status": status}, "msg": "Success"}


def _position_resp(position_id="pos1", avg_open_price=100.0, symbol="SOLUSDT", side="BUY"):
    return {"code": 0, "data": [{"positionId": position_id, "symbol": symbol, "side": side, "avgOpenPrice": str(avg_open_price), "qty": "10.0"}], "msg": "Success"}


def _empty_position_resp():
    return {"code": 0, "data": [], "msg": "Success"}


def _tpsl_set_resp(order_id="sl1"):
    return {"code": 0, "data": {"orderId": order_id}, "msg": "Success"}


def _tpsl_pending_resp(sl_price=None):
    data = [{"slPrice": str(sl_price)}] if sl_price is not None else []
    return {"code": 0, "data": data, "msg": "Success"}


def _patch(monkeypatch, **methods):
    for name, fn in methods.items():
        monkeypatch.setattr(ebc.BitunixClient, name, fn)


# ------------------------------------------------------------------ size_entry()

def test_size_entry_rejects_when_no_banded_policy_configured(db):
    account = _ready_account(db)
    result = _run(ax.size_entry(db, account, "SOLUSDT", equity=10000.0, entry_price=100.0, stop_price=98.0))
    assert result["decision"] == "REJECTED"
    assert "no banded sizing policy" in result["decision_reason"]


def test_size_entry_uses_band_below_pct_not_base_risk_pct(db, monkeypatch):
    # Regression test for a real bug found during review: size_entry() must
    # read policy.band_below_pct (the banded schedule's own below-first-step
    # percentage), never policy.base_risk_pct (an unrelated field -- the
    # fixed-$-vs-%-of-balance MODE SELECTOR for a totally different, non-
    # banded sizing path). Set base_risk_pct to a wildly different value so
    # the test would catch either field being silently swapped.
    account = _ready_account(db)
    policy = ea.get_or_init_sizing_policy(db, account)
    policy.band_step_usd = 10_000.0
    policy.band_risk_per_step_usd = 1_000.0
    policy.band_below_pct = 0.25   # deliberately far from base_risk_pct below
    policy.base_risk_pct = 0.99    # unrelated field -- must NOT be read as below_pct
    db.commit()

    _patch(monkeypatch,
           get_leverage_and_margin_mode=_async({"code": 0, "data": {"leverage": 10}}),
           get_position_tiers=_async({"code": 0, "data": [{"startValue": "0", "endValue": "999999999", "maintenanceMarginRate": "0.005"}]}))

    # balance below step_usd -> below_pct branch: risk = balance * below_pct
    result = _run(ax.size_entry(db, account, "SOLUSDT", equity=5000.0, entry_price=100.0, stop_price=98.0))
    assert result["risk_dollars_used"] == pytest.approx(5000.0 * 0.25)


def test_size_entry_computes_qty_leverage_margin_and_admits_when_safe(db, monkeypatch):
    account = _ready_account(db)
    policy = ea.get_or_init_sizing_policy(db, account)
    policy.band_step_usd = 10_000.0
    policy.band_risk_per_step_usd = 1_000.0
    policy.band_max_risk_usd = 5_000.0
    db.commit()

    _patch(monkeypatch,
           get_leverage_and_margin_mode=_async({"code": 0, "data": {"leverage": 5}}),
           get_position_tiers=_async({"code": 0, "data": [{"startValue": "0", "endValue": "999999999", "maintenanceMarginRate": "0.005"}]}))

    result = _run(ax.size_entry(db, account, "SOLUSDT", equity=25000.0, entry_price=100.0, stop_price=98.0))
    assert result["decision"] == "WOULD_PLACE"
    assert result["risk_dollars_used"] == pytest.approx(2000.0)   # floor(25000/10000)*1000
    assert result["qty"] == pytest.approx(2000.0 / 2.0)           # risk / stop_distance
    assert result["leverage"] == 5
    assert result["liquidation_check_passed"] is True


def test_size_entry_rejects_when_leverage_too_high_for_the_stop(db, monkeypatch):
    account = _ready_account(db)
    policy = ea.get_or_init_sizing_policy(db, account)
    policy.band_step_usd = 10_000.0
    policy.band_risk_per_step_usd = 1_000.0
    db.commit()

    # leverage 100x on a 2%-away stop -> liquidation sits inside the stop
    _patch(monkeypatch,
           get_leverage_and_margin_mode=_async({"code": 0, "data": {"leverage": 100}}),
           get_position_tiers=_async({"code": 0, "data": [{"startValue": "0", "endValue": "999999999", "maintenanceMarginRate": "0.005"}]}))

    result = _run(ax.size_entry(db, account, "SOLUSDT", equity=25000.0, entry_price=100.0, stop_price=98.0))
    assert result["decision"] == "REJECTED"
    assert "leverage too high" in result["decision_reason"]


# ------------------------------------------------------------------ place_entry_and_protect() -- happy path + each failure branch

def test_place_entry_and_protect_happy_path_fills_and_protects(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account)

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           place_order=_async({"code": 0, "data": {"orderId": "entry1"}}),
           get_order_detail=_async(_order_detail_resp("FILLED")),
           get_position=_async(_position_resp(position_id="pos1", avg_open_price=101.5)),
           set_position_tpsl=_async(_tpsl_set_resp("sl1")))

    _run(ax.place_entry_and_protect(db, account, order))
    db.flush()   # SessionLocal is autoflush=False -- a plain query after db.add() won't see it otherwise

    assert order.management_state == "FILLED"
    assert order.entry_fill_price == 101.5
    assert order.position_id == "pos1"
    assert order.sl_exchange_order_id == "sl1"
    assert order.sl_price_current == 98.0

    transitions = db.query(AltMatrixTransition).filter_by(alt_matrix_order_id=order.id).all()
    assert len(transitions) == 1
    assert transitions[0].to_state == "FILLED"
    assert transitions[0].price == 101.5


def test_place_entry_and_protect_entry_call_failure_closes_as_error(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account)

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           place_order=_async({"code": 10001, "msg": "insufficient balance", "data": None}))

    _run(ax.place_entry_and_protect(db, account, order))
    db.flush()

    assert order.management_state == "CLOSED_ERROR"
    transitions = db.query(AltMatrixTransition).filter_by(alt_matrix_order_id=order.id).all()
    assert len(transitions) == 1
    assert transitions[0].to_state == "CLOSED_ERROR"


def test_place_entry_and_protect_resumes_without_replacing_an_already_placed_entry(db, monkeypatch):
    # The watch loop calls this repeatedly for a PENDING_ENTRY row. On a
    # resumed call (entry_exchange_order_id already set from a prior
    # tick), it must NOT call place_order() again -- only confirm/protect.
    account = _ready_account(db)
    order = _order(db, account, entry_exchange_order_id="entry1")

    def _fail_if_called(self, *a, **kw):
        raise AssertionError("place_order must not be called on a resumed entry")

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           place_order=_fail_if_called,
           get_order_detail=_async(_order_detail_resp("FILLED")),
           get_position=_async(_position_resp(position_id="pos1", avg_open_price=102.0)),
           set_position_tpsl=_async(_tpsl_set_resp("sl1")))

    _run(ax.place_entry_and_protect(db, account, order))

    assert order.management_state == "FILLED"
    assert order.entry_fill_price == 102.0
    assert order.entry_exchange_order_id == "entry1"   # unchanged


def test_place_entry_and_protect_not_yet_filled_stays_pending_no_transition(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account)

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           place_order=_async({"code": 0, "data": {"orderId": "entry1"}}),
           get_order_detail=_async(_order_detail_resp("NEW")))

    _run(ax.place_entry_and_protect(db, account, order))

    assert order.management_state == "PENDING_ENTRY"
    assert order.entry_status == "NEW"
    assert db.query(AltMatrixTransition).filter_by(alt_matrix_order_id=order.id).count() == 0


def test_place_entry_and_protect_ambiguous_position_count_closes_as_error(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account)

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           place_order=_async({"code": 0, "data": {"orderId": "entry1"}}),
           get_order_detail=_async(_order_detail_resp("FILLED")),
           get_position=_async(_empty_position_resp()))

    _run(ax.place_entry_and_protect(db, account, order))
    assert order.management_state == "CLOSED_ERROR"


def test_place_entry_and_protect_stop_placement_api_error_leaves_unprotected(db, monkeypatch):
    # The single most safety-critical branch in this file: a REAL open
    # position with NO stop must be flagged loudly (ENTRY_FILLED_
    # UNPROTECTED), never silently treated as FILLED/protected.
    account = _ready_account(db)
    order = _order(db, account)

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           place_order=_async({"code": 0, "data": {"orderId": "entry1"}}),
           get_order_detail=_async(_order_detail_resp("FILLED")),
           get_position=_async(_position_resp()),
           set_position_tpsl=_async({"code": 10002, "msg": "rate limited", "data": None}))

    _run(ax.place_entry_and_protect(db, account, order))
    db.flush()

    assert order.management_state == "ENTRY_FILLED_UNPROTECTED"
    assert order.entry_fill_price == 100.0   # still recorded despite no stop
    error_rows = db.query(ExecutorAuditLog).filter_by(event_type="ERROR").all()
    assert any("NO STOP PLACED" in r.message for r in error_rows)


def test_place_entry_and_protect_stop_placement_exception_leaves_unprotected(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account)

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           place_order=_async({"code": 0, "data": {"orderId": "entry1"}}),
           get_order_detail=_async(_order_detail_resp("FILLED")),
           get_position=_async(_position_resp()),
           set_position_tpsl=_async(exc=ConnectionError("network blip")))

    _run(ax.place_entry_and_protect(db, account, order))
    assert order.management_state == "ENTRY_FILLED_UNPROTECTED"


# ------------------------------------------------------------------ amend_to_breakeven() -- the "verify, don't trust the REST response" check

def test_amend_to_breakeven_happy_path_confirmed(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account, management_state="FILLED", entry_fill_price=100.0, position_id="pos1")

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           modify_position_tp_sl_order=_async({"code": 0, "data": {}}),
           get_pending_tp_sl_order=_async(_tpsl_pending_resp(sl_price=100.2)))

    ok = _run(ax.amend_to_breakeven(db, account, order, be_price=100.2))
    db.flush()

    assert ok is True
    assert order.be_amended is True
    assert order.be_price == 100.2
    assert order.sl_price_current == 100.2
    assert order.management_state == "TRAILING"
    transitions = db.query(AltMatrixTransition).filter_by(alt_matrix_order_id=order.id).all()
    assert len(transitions) == 1
    assert transitions[0].from_state == "FILLED" and transitions[0].to_state == "TRAILING"


def test_amend_to_breakeven_api_error_does_not_amend(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account, management_state="FILLED", entry_fill_price=100.0, position_id="pos1")

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           modify_position_tp_sl_order=_async({"code": 10003, "msg": "fail", "data": None}))

    ok = _run(ax.amend_to_breakeven(db, account, order, be_price=100.2))
    assert ok is False
    assert order.be_amended is False
    assert order.management_state == "FILLED"


def test_amend_to_breakeven_call_exception_does_not_amend(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account, management_state="FILLED", entry_fill_price=100.0, position_id="pos1")

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           modify_position_tp_sl_order=_async(exc=ConnectionError("blip")))

    ok = _run(ax.amend_to_breakeven(db, account, order, be_price=100.2))
    assert ok is False
    assert order.be_amended is False


def test_amend_to_breakeven_confirmed_mismatch_refuses_to_trust_the_rest_response(db, monkeypatch):
    # The exact lesson from the real 2026-09-05 incident: a 200 OK from
    # modify_position_tp_sl_order() does NOT guarantee the exchange actually
    # applied it. get_pending_tp_sl_order() shows a DIFFERENT sl than what
    # was requested -- must refuse to mark be_amended, not trust the call.
    account = _ready_account(db)
    order = _order(db, account, management_state="FILLED", entry_fill_price=100.0, position_id="pos1")

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           modify_position_tp_sl_order=_async({"code": 0, "data": {}}),
           get_pending_tp_sl_order=_async(_tpsl_pending_resp(sl_price=99.0)))   # wrong -- requested 100.2

    ok = _run(ax.amend_to_breakeven(db, account, order, be_price=100.2))
    assert ok is False
    assert order.be_amended is False
    assert order.management_state == "FILLED"


def test_amend_to_breakeven_confirmed_missing_sl_refuses(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account, management_state="FILLED", entry_fill_price=100.0, position_id="pos1")

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           modify_position_tp_sl_order=_async({"code": 0, "data": {}}),
           get_pending_tp_sl_order=_async(_tpsl_pending_resp(sl_price=None)))   # no sl row at all

    ok = _run(ax.amend_to_breakeven(db, account, order, be_price=100.2))
    assert ok is False
    assert order.be_amended is False


# ------------------------------------------------------------------ market_close()

def test_market_close_happy_path_confirmed_and_records_r(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account, management_state="TRAILING", entry_fill_price=100.0, position_id="pos1", r_distance=2.0)

    _patch(monkeypatch,
           close_position=_async({"code": 0, "data": {}}),
           get_position=_async(_empty_position_resp()))
    import market_data
    monkeypatch.setattr(market_data, "fetch_bitunix_5m", _async_fn_returning([{"close": "104.0"}]))

    _run(ax.market_close(db, account, order, exit_reason="EMA21_TRAIL"))
    db.flush()

    assert order.management_state == "CLOSED_EMA21_TRAIL"
    assert order.exit_price == 104.0
    assert order.realized_pnl_r == pytest.approx((104.0 - 100.0) / 2.0)
    assert order.closed_at is not None
    transitions = db.query(AltMatrixTransition).filter_by(alt_matrix_order_id=order.id).all()
    assert len(transitions) == 1
    assert transitions[0].to_state == "CLOSED_EMA21_TRAIL"
    assert transitions[0].price == 104.0


def test_market_close_call_failure_never_assumes_closed(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account, management_state="FILLED", entry_fill_price=100.0, position_id="pos1")

    _patch(monkeypatch, close_position=_async(exc=ConnectionError("blip")))

    _run(ax.market_close(db, account, order, exit_reason="EMA55_CLOSE"))

    assert order.management_state == "FILLED"   # unchanged -- never assume closed on a call failure
    assert order.exit_price is None
    assert db.query(AltMatrixTransition).filter_by(alt_matrix_order_id=order.id).count() == 0


def test_market_close_still_open_after_confirm_attempts_does_not_finalize(db, monkeypatch):
    # Mutation check on the confirm loop itself: if the position is STILL
    # visible in get_position() on every retry, market_close() must NOT
    # finalize the row as closed.
    account = _ready_account(db)
    order = _order(db, account, management_state="FILLED", entry_fill_price=100.0, position_id="pos1")

    _patch(monkeypatch,
           close_position=_async({"code": 0, "data": {}}),
           get_position=_async(_position_resp(position_id="pos1", symbol="SOLUSDT", side="BUY")))

    orig_sleep = ax.asyncio.sleep
    monkeypatch.setattr(ax.asyncio, "sleep", lambda *_a, **_kw: orig_sleep(0))   # don't actually wait in the test

    _run(ax.market_close(db, account, order, exit_reason="EMA55_CLOSE"))

    assert order.management_state == "FILLED"
    assert order.exit_price is None


def test_market_close_falls_back_to_entry_price_when_live_price_unavailable(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account, management_state="FILLED", entry_fill_price=100.0, position_id="pos1", r_distance=2.0)

    _patch(monkeypatch,
           close_position=_async({"code": 0, "data": {}}),
           get_position=_async(_empty_position_resp()))
    import market_data
    monkeypatch.setattr(market_data, "fetch_bitunix_5m", _async_fn_returning([]))   # empty -- feed unavailable

    _run(ax.market_close(db, account, order, exit_reason="STOP"))

    assert order.exit_price == 100.0   # last resort -- never leaves exit_price None
    assert order.management_state == "CLOSED_STOP"


def _async_fn_returning(value):
    async def _fake(*a, **kw):
        return value
    return _fake


# ------------------------------------------------------------------ notify wiring

def _capture_account_email(monkeypatch):
    import notify
    calls = []
    def _fake(subject, body, account_id):
        calls.append({"subject": subject, "body": body, "account_id": account_id})
        return True
    monkeypatch.setattr(notify, "send_account_email", _fake)
    return calls


def test_place_entry_and_protect_happy_path_sends_entry_email(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account)
    calls = _capture_account_email(monkeypatch)

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           place_order=_async({"code": 0, "data": {"orderId": "entry1"}}),
           get_order_detail=_async(_order_detail_resp("FILLED")),
           get_position=_async(_position_resp(position_id="pos1", avg_open_price=101.5)),
           set_position_tpsl=_async(_tpsl_set_resp("sl1")))

    _run(ax.place_entry_and_protect(db, account, order))

    assert len(calls) == 1
    assert calls[0]["account_id"] == account.id
    assert "Position Opened" in calls[0]["subject"]
    assert "101.5" in calls[0]["body"]


def test_place_entry_and_protect_stop_failure_sends_unprotected_alert(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account)
    calls = _capture_account_email(monkeypatch)

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           place_order=_async({"code": 0, "data": {"orderId": "entry1"}}),
           get_order_detail=_async(_order_detail_resp("FILLED")),
           get_position=_async(_position_resp()),
           set_position_tpsl=_async({"code": 10002, "msg": "rate limited", "data": None}))

    _run(ax.place_entry_and_protect(db, account, order))

    assert len(calls) == 1
    assert "unprotected" in calls[0]["subject"].lower()
    assert "CHECK THE EXCHANGE DIRECTLY" in calls[0]["body"]


def test_amend_to_breakeven_happy_path_sends_breakeven_email(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account, management_state="FILLED", entry_fill_price=100.0, position_id="pos1")
    calls = _capture_account_email(monkeypatch)

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           modify_position_tp_sl_order=_async({"code": 0, "data": {}}),
           get_pending_tp_sl_order=_async(_tpsl_pending_resp(sl_price=100.2)))

    _run(ax.amend_to_breakeven(db, account, order, be_price=100.2))

    assert len(calls) == 1
    assert "Breakeven" in calls[0]["subject"]


def test_market_close_happy_path_sends_exit_email_with_approximation_note(db, monkeypatch):
    account = _ready_account(db)
    order = _order(db, account, management_state="TRAILING", entry_fill_price=100.0, position_id="pos1", r_distance=2.0)
    calls = _capture_account_email(monkeypatch)

    _patch(monkeypatch,
           close_position=_async({"code": 0, "data": {}}),
           get_position=_async(_empty_position_resp()))
    import market_data
    monkeypatch.setattr(market_data, "fetch_bitunix_5m", _async_fn_returning([{"close": "104.0"}]))

    _run(ax.market_close(db, account, order, exit_reason="EMA21_TRAIL"))

    assert len(calls) == 1
    assert "Alt Matrix Closed" in calls[0]["subject"]
    assert "Real order -- live money." in calls[0]["body"]
    assert "Note:" in calls[0]["body"]   # LIVE market_close() always approximates


def test_notify_failure_never_breaks_real_order_placement(db, monkeypatch):
    # The notify wrapper must swallow ANY failure -- a broken email
    # integration must never prevent a real fill/protect from completing.
    account = _ready_account(db)
    order = _order(db, account)
    import notify
    def _broken(*a, **kw):
        raise RuntimeError("SMTP is down")
    monkeypatch.setattr(notify, "send_account_email", _broken)

    _patch(monkeypatch,
           get_trading_pairs=_async(_pair_resp()),
           place_order=_async({"code": 0, "data": {"orderId": "entry1"}}),
           get_order_detail=_async(_order_detail_resp("FILLED")),
           get_position=_async(_position_resp(position_id="pos1", avg_open_price=101.5)),
           set_position_tpsl=_async(_tpsl_set_resp("sl1")))

    _run(ax.place_entry_and_protect(db, account, order))

    assert order.management_state == "FILLED"   # real execution unaffected by the notify crash
