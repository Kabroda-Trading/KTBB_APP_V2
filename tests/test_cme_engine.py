"""Coverage for cme_engine.py -- the orchestration layer tying D1
(cme_signals.calculate_session_lock), D2/D3 (cme_signals.check_cross/
check_retest_fill/check_breakeven_ratchet/check_exit), risk admission
(cme_risk.can_open_new_position), and real order placement (cme_executor)
together. The signal/risk math itself is already covered by
tests/test_cme_signals.py and tests/test_cme_risk.py -- these tests
monkeypatch that math directly where convenient, so engine-level tests
exercise THIS module's own branching (which account gets skipped and why,
idempotent restart-safety, the PENDING_ENTRY->WAITING_RETEST->FILLED state
machine, exit priority ordering) without re-deriving real TPO/cross math.
Same style as tests/test_alt_matrix_engine.py."""
import asyncio
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./kabroda_test_cme_engine.db")

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount, CMEPlan, CMEOrder, CMEConfig, CMETransition
import executor_accounts as ea
import executor_control as ec
import cme_engine as ce
import cme_executor as cx
import cme_market as cm
import cme_risk as cr
import cme_signals as cs
import cme_clock as cc

UTC = datetime.timezone.utc


def _clean_db_files():
    for path in ["kabroda_test_cme_engine.db", "kabroda_test_cme_engine.db-journal",
                 "kabroda_test_cme_engine.db-shm", "kabroda_test_cme_engine.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    for model in (CMETransition, CMEOrder, CMEPlan, CMEConfig, ExecutorAccount):
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


def _account(db, label="cme_engine_test", mode="DRY_RUN"):
    account = ea.create_account(db, user_id=1, label=label)
    db.flush()
    account.mode = mode
    if mode == "LIVE":
        ea.set_credentials(db, account, api_key="u", api_secret="p", set_by="test@kabroda.com")
        ec.enable_live_orders(db, reason="testing", by="andy@kabroda.com")
    db.commit()
    return account


def _config(db, account, mnq=True, mes=True, **overrides):
    defaults = dict(account_id=account.id, mnq_enabled=mnq, mes_enabled=mes)
    defaults.update(overrides)
    cfg = CMEConfig(**defaults)
    db.add(cfg)
    db.commit()
    return cfg


def _plan(db, symbol="MNQ", date_key="2026-11-04", status="ARMED", **overrides):
    defaults = dict(
        symbol=symbol, date_key=date_key, lock_time=datetime.datetime(2026, 11, 4, 15, 0, 0),
        status=status, box=20.0, bo=4500.0, bd=4480.0,
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
        cme_plan_id=plan.id, account_id=account.id, mode=account.mode,
        symbol=plan.symbol, direction="LONG", contracts=2, box_at_entry=plan.box,
        decision="WOULD_PLACE", management_state="PENDING_ENTRY",
    )
    defaults.update(overrides)
    order = CMEOrder(**defaults)
    db.add(order)
    db.flush()
    return order


def _run(coro):
    return asyncio.run(coro)


def _capture_emails(monkeypatch):
    import notify
    calls = []
    monkeypatch.setattr(notify, "send_admin_email", lambda subject, body: calls.append({"subject": subject, "body": body}) or True)
    return calls


# ------------------------------------------------------------------ cme_executor._client_for (is_demo wiring)

def test_client_for_reads_is_demo_from_the_account_not_hardcoded_false(db):
    """2026-11-04 -- found while setting up the first real Tradovate
    account (MFFU, an evaluation account that trades on Tradovate's demo
    gateway, not live): this was hardcoded to is_demo=False with no
    account-level override at all, on the one real code path that
    actually places live orders. main.py's diagnostic routes have their
    own equivalent coverage (test_cme_routes.py)."""
    account = _account(db, "demo_acct", mode="LIVE")
    account.tradovate_account_id = 12345
    account.tradovate_account_name = "MFFUEVREOD745510001"
    account.tradovate_is_demo = True
    db.commit()

    client = cx._client_for(account)
    assert client.is_demo is True
    assert client.base_url.startswith("https://demo.")


def test_client_for_defaults_to_live_when_is_demo_unset(db):
    account = _account(db, "live_acct", mode="LIVE")
    account.tradovate_account_id = 12345
    db.commit()

    client = cx._client_for(account)
    assert client.is_demo is False


def test_client_for_routes_to_crosstrade_when_execution_provider_set(db):
    """2026-11-04 -- added once a real evaluation account (MFFU) confirmed
    direct Tradovate API access is gated off for unfunded accounts.
    execution_provider='crosstrade' must produce a CrossTradeClient, not
    a TradovateClient, with the bearer token (reusing the same encrypted
    api_key column) and the CrossTrade-specific account identifier."""
    import cme_crosstrade_client as ctc

    account = _account(db, "crosstrade_acct", mode="LIVE")
    account.execution_provider = "crosstrade"
    account.crosstrade_account_id = "DEMO12345678"
    db.commit()

    client = cx._client_for(account)
    assert isinstance(client, ctc.CrossTradeClient)
    assert client.bearer_token == "u"   # from _account()'s set_credentials(api_key="u", ...)
    assert client.crosstrade_account_id == "DEMO12345678"


def test_client_for_crosstrade_requires_crosstrade_account_id(db):
    account = _account(db, "crosstrade_no_id", mode="LIVE")
    account.execution_provider = "crosstrade"
    db.commit()

    with pytest.raises(ValueError):
        cx._client_for(account)


def test_client_for_unset_provider_still_uses_tradovate(db):
    """A None/blank execution_provider is NOT a new default -- every
    account created before this field existed must keep working exactly
    as before."""
    import cme_tradovate_client as tvc

    account = _account(db, "legacy_acct", mode="LIVE")
    account.tradovate_account_id = 1
    db.commit()
    assert account.execution_provider is None   # the real pre-existing-row shape

    client = cx._client_for(account)
    assert isinstance(client, tvc.TradovateClient)


# ------------------------------------------------------------------ _enabled_accounts_for_symbol

def test_enabled_accounts_filters_by_symbol_flag_and_sorts_by_id(db):
    a1 = _account(db, "a1")
    a2 = _account(db, "a2")
    a3 = _account(db, "a3")
    _config(db, a2, mnq=True, mes=False)
    _config(db, a3, mnq=False, mes=True)
    _config(db, a1, mnq=True, mes=True)

    mnq_pairs = ce._enabled_accounts_for_symbol(db, "MNQ")
    mes_pairs = ce._enabled_accounts_for_symbol(db, "MES")
    assert [a.id for a, _cfg in mnq_pairs] == sorted([a1.id, a2.id])
    assert [a.id for a, _cfg in mes_pairs] == sorted([a1.id, a3.id])


# ------------------------------------------------------------------ _process_symbol_lock (D1)

def test_process_symbol_lock_is_idempotent_on_restart_replay(db, monkeypatch):
    _plan(db, symbol="MNQ", date_key="2026-11-04")   # a lock already exists for today
    calls = {"n": 0}

    async def _fake_calib(symbol, date_key):
        calls["n"] += 1
        return []
    monkeypatch.setattr(cm, "fetch_calibration_1m", _fake_calib)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-04"))
    assert calls["n"] == 0   # never even tried to fetch -- existing row short-circuits first
    assert db.query(CMEPlan).filter_by(symbol="MNQ", date_key="2026-11-04").count() == 1


def test_process_symbol_lock_creates_no_row_at_all_on_a_calibration_fetch_failure(db, monkeypatch):
    account = _account(db)
    _config(db, account)

    async def _fail(symbol, date_key):
        raise NotImplementedError("Tradovate Market Data API not yet confirmed")
    monkeypatch.setattr(cm, "fetch_calibration_1m", _fail)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-04"))
    assert db.query(CMEPlan).count() == 0   # no partial/fabricated row on a data-layer failure


def test_process_symbol_lock_stand_down_creates_plan_but_no_orders(db, monkeypatch):
    account = _account(db)
    _config(db, account)
    monkeypatch.setattr(cx, "_client_for", lambda account_: object())

    async def _fake_calib(symbol, date_key):
        return [{"open": 100, "high": 101, "low": 99, "close": 100}] * 20
    monkeypatch.setattr(cm, "fetch_calibration_1m", _fake_calib)
    monkeypatch.setattr(cs, "calculate_session_lock", lambda symbol, bars, atr: {
        "symbol": symbol, "status": "STAND_DOWN_UNREACHABLE", "status_reason": "box too wide",
        "box": 50.0, "poc": 100.0, "vah": 101.0, "val": 99.0, "r30h": 101.0, "r30l": 99.0,
        "atr14": None, "reachability": 0.9,
    })
    admin_calls = _capture_emails(monkeypatch)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-04"))

    plan = db.query(CMEPlan).filter_by(symbol="MNQ", date_key="2026-11-04").first()
    assert plan is not None and plan.status == "STAND_DOWN_UNREACHABLE"
    assert db.query(CMEOrder).count() == 0   # non-ARMED plan never fans out to orders


_ARMED_LOCK = {
    "symbol": "MNQ", "status": "ARMED", "status_reason": None,
    "box": 20.0, "poc": 4490.0, "vah": 4500.0, "val": 4480.0, "r30h": 4500.0, "r30l": 4480.0,
    "atr14": 40.0, "reachability": 0.5,
    "bo": 4500.0, "bd": 4480.0, "stop_long": 4480.0, "stop_short": 4500.0,
    "target_long": 4540.0, "target_short": 4460.0,
    "be_trigger_long": 4516.0, "be_trigger_short": 4464.0,
    "be_stop_long": 4502.0, "be_stop_short": 4498.0,
}


def test_process_symbol_lock_armed_fans_out_long_and_short_orders_per_enabled_account(db, monkeypatch):
    account = _account(db, mode="DRY_RUN")
    account.trailing_drawdown_limit_usd = 2000.0
    account.high_water_mark_usd = 50000.0
    db.commit()
    _config(db, account, daily_risk_budget_usd=350.0, contracts_cap=3)
    monkeypatch.setattr(cx, "_client_for", lambda account_: object())

    async def _fake_calib(symbol, date_key):
        return [{"open": 100, "high": 101, "low": 99, "close": 100}] * 20
    monkeypatch.setattr(cm, "fetch_calibration_1m", _fake_calib)
    monkeypatch.setattr(cs, "calculate_session_lock", lambda symbol, bars, atr: dict(_ARMED_LOCK))

    async def _fake_equity(account_):
        return 50000.0
    monkeypatch.setattr(ce, "_query_equity", _fake_equity)
    _capture_emails(monkeypatch)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-04"))

    plan = db.query(CMEPlan).filter_by(symbol="MNQ", date_key="2026-11-04").first()
    assert plan is not None and plan.status == "ARMED"
    orders = db.query(CMEOrder).filter_by(cme_plan_id=plan.id, account_id=account.id).all()
    assert {o.direction for o in orders} == {"LONG", "SHORT"}
    assert all(o.decision == "WOULD_PLACE" for o in orders)
    assert all(o.management_state == "PENDING_ENTRY" for o in orders)
    assert all(o.contracts > 0 for o in orders)


def test_process_symbol_lock_admits_both_directions_independently_same_tick(db, monkeypatch):
    # Proves both directions of the OCO-style fan-out get admitted on
    # their own merits in the same tick -- not a reproduced live bug (see
    # cme_risk.py's own comment on _OPEN_CME_STATES: the theoretical
    # same-tick self-rejection via a sibling PENDING_ENTRY row turned out
    # not to be reachable given database.py's autoflush=False session),
    # but worth locking in directly since it's exactly the kind of thing
    # a future refactor (e.g. adding an early flush inside the loop)
    # could silently reintroduce.
    account = _account(db, mode="DRY_RUN")
    account.trailing_drawdown_limit_usd = 2000.0
    account.high_water_mark_usd = 50000.0
    db.commit()
    _config(db, account, daily_risk_budget_usd=350.0, contracts_cap=3)
    monkeypatch.setattr(cx, "_client_for", lambda account_: object())

    async def _fake_calib(symbol, date_key):
        return [{"open": 100, "high": 101, "low": 99, "close": 100}] * 20
    monkeypatch.setattr(cm, "fetch_calibration_1m", _fake_calib)
    monkeypatch.setattr(cs, "calculate_session_lock", lambda symbol, bars, atr: dict(_ARMED_LOCK))

    async def _fake_equity(account_):
        return 50000.0
    monkeypatch.setattr(ce, "_query_equity", _fake_equity)
    _capture_emails(monkeypatch)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-04"))

    plan = db.query(CMEPlan).filter_by(symbol="MNQ", date_key="2026-11-04").first()
    orders = {o.direction: o for o in db.query(CMEOrder).filter_by(cme_plan_id=plan.id, account_id=account.id).all()}
    assert orders["LONG"].decision == "WOULD_PLACE" and orders["LONG"].contracts > 0
    assert orders["SHORT"].decision == "WOULD_PLACE" and orders["SHORT"].contracts > 0
    assert orders["SHORT"].decision_reason != "ALREADY_OPEN_ON_SYMBOL_TODAY"


def test_process_symbol_lock_respects_a_real_risk_rejection_not_just_a_mock(db, monkeypatch):
    # Uses the REAL cme_risk.can_open_new_position (not mocked) with an
    # account whose trailing drawdown is already blown -- proves the
    # engine actually wires risk admission through, not just a fake.
    account = _account(db, mode="DRY_RUN")
    account.high_water_mark_usd = 50000.0
    account.trailing_drawdown_limit_usd = 2000.0
    db.commit()
    _config(db, account, daily_risk_budget_usd=350.0, contracts_cap=3)
    monkeypatch.setattr(cx, "_client_for", lambda account_: object())

    async def _fake_calib(symbol, date_key):
        return [{"open": 100, "high": 101, "low": 99, "close": 100}] * 20
    monkeypatch.setattr(cm, "fetch_calibration_1m", _fake_calib)
    monkeypatch.setattr(cs, "calculate_session_lock", lambda symbol, bars, atr: dict(_ARMED_LOCK))

    async def _fake_equity(account_):
        return 47000.0   # $3000 under the high-water mark -- over the $2000 trailing DD limit
    monkeypatch.setattr(ce, "_query_equity", _fake_equity)
    _capture_emails(monkeypatch)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-04"))

    plan = db.query(CMEPlan).filter_by(symbol="MNQ", date_key="2026-11-04").first()
    orders = db.query(CMEOrder).filter_by(cme_plan_id=plan.id, account_id=account.id).all()
    assert len(orders) == 2   # a row is still written (audit trail) for both sides
    assert all(o.decision == "REJECTED" for o in orders)
    assert all(o.contracts == 0 for o in orders)


# ------------------------------------------------------------------ _advance_pending_order (D2)

def test_advance_pending_order_no_cross_yet_leaves_state_untouched(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="PENDING_ENTRY")

    async def _fake_bars(symbol, since):
        return [{"close": 4490.0}]   # inside the box -- no cross
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fake_bars)

    _run(ce._advance_pending_order(db, order))
    assert order.management_state == "PENDING_ENTRY"
    assert order.cross_confirmed_at is None


def test_advance_pending_order_cross_confirmed_places_bracket(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="PENDING_ENTRY")

    async def _fake_bars(symbol, since):
        return [{"close": 4510.0}]   # beyond bo=4500 -- confirmed LONG cross
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fake_bars)

    called = {}
    async def _fake_place(db_, account_, plan_, order_):
        called["order_id"] = order_.id
    monkeypatch.setattr(cx, "place_resting_bracket", _fake_place)

    _run(ce._advance_pending_order(db, order))
    assert order.cross_confirmed_at is not None
    assert called.get("order_id") == order.id


def test_advance_pending_order_opposite_side_stands_down_instead_of_also_entering(db, monkeypatch):
    # Regression for a real bug found 2026-11-04: check_cross() returns
    # whichever side actually crossed; place_resting_bracket() always
    # reads order_row.direction. Without filtering the two against each
    # other, a single confirmed LONG cross placed a REAL bracket on BOTH
    # the LONG row (correct) and the SHORT row (an unbacktested straddle,
    # never part of any measured strategy).
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    long_order = _order(db, account, plan, direction="LONG", management_state="PENDING_ENTRY")
    short_order = _order(db, account, plan, direction="SHORT", management_state="PENDING_ENTRY")

    async def _fake_bars(symbol, since):
        return [{"close": 4510.0}]   # beyond bo=4500 -- confirmed LONG cross only
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fake_bars)

    place_calls = []
    async def _fake_place(db_, account_, plan_, order_):
        place_calls.append(order_.direction)
    monkeypatch.setattr(cx, "place_resting_bracket", _fake_place)

    _run(ce._advance_pending_order(db, long_order))
    _run(ce._advance_pending_order(db, short_order))
    db.flush()   # this session is autoflush=False (database.py's own SessionLocal) -- a plain query after db.add() won't see it otherwise

    assert place_calls == ["LONG"]   # SHORT never got a real bracket placed
    assert short_order.management_state == "OPPOSITE_CROSSED"
    assert short_order.cross_confirmed_at is None   # never treated as its own entry
    transitions = db.query(CMETransition).filter_by(cme_order_id=short_order.id).all()
    assert len(transitions) == 1 and transitions[0].to_state == "OPPOSITE_CROSSED"


def test_advance_pending_order_skips_a_rejected_decision(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="PENDING_ENTRY", decision="REJECTED", decision_reason="trailing dd")

    called = {"n": 0}
    async def _should_not_run(symbol, since):
        called["n"] += 1
        return [{"close": 4510.0}]
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _should_not_run)

    _run(ce._advance_pending_order(db, order))
    assert called["n"] == 0   # never even checked for a cross -- this row was never going live
    assert order.management_state == "PENDING_ENTRY"


def test_advance_pending_order_retest_window_elapsed_cancels(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    stale_cross = datetime.datetime.utcnow() - datetime.timedelta(minutes=120)
    order = _order(db, account, plan, management_state="WAITING_RETEST", cross_confirmed_at=stale_cross)

    called = {}
    async def _fake_cancel(db_, account_, order_):
        called["order_id"] = order_.id
    monkeypatch.setattr(cx, "cancel_no_retest", _fake_cancel)

    _run(ce._advance_pending_order(db, order))
    assert called.get("order_id") == order.id


def test_advance_pending_order_retest_window_still_open_checks_fill(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    recent_cross = datetime.datetime.utcnow() - datetime.timedelta(minutes=5)
    order = _order(db, account, plan, management_state="WAITING_RETEST", cross_confirmed_at=recent_cross)

    called = {}
    async def _fake_check(db_, account_, order_):
        called["order_id"] = order_.id
    monkeypatch.setattr(cx, "check_retest_fill_and_confirm", _fake_check)

    _run(ce._advance_pending_order(db, order))
    assert called.get("order_id") == order.id


# ------------------------------------------------------------------ _advance_open_position (D3)

def test_advance_open_position_time_flush_closes_regardless_of_price(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", entry_fill_price=4505.0,
                    sl_price_current=4480.0, be_amended=False)

    monkeypatch.setattr(cc, "today_flush_utc", lambda now: now - datetime.timedelta(seconds=1))

    called = {}
    async def _fake_close(db_, account_, order_, exit_reason):
        called["exit_reason"] = exit_reason
    monkeypatch.setattr(cx, "market_close", _fake_close)

    _run(ce._advance_open_position(db, order))
    assert called.get("exit_reason") == "TIME_FLUSH"


def test_advance_open_position_breakeven_ratchet_fires_and_returns_before_exit_check(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, direction="LONG", management_state="FILLED",
                    entry_fill_price=4505.0, sl_price_current=4480.0, be_amended=False,
                    sl_exchange_order_id="1")

    monkeypatch.setattr(cc, "today_flush_utc", lambda now: now + datetime.timedelta(hours=1))
    async def _fake_bars(symbol, since):
        return [{"close": 4520.0, "high": 4522.0, "low": 4518.0}]   # past be_trigger_long=4516
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fake_bars)

    be_called = {}
    async def _fake_amend(db_, account_, order_, stop_order_id, be_price):
        be_called["stop_order_id"] = stop_order_id
        be_called["be_price"] = be_price
        return True
    monkeypatch.setattr(cx, "amend_to_breakeven", _fake_amend)

    exit_called = {"n": 0}
    async def _should_not_run(db_, account_, order_, exit_reason):
        exit_called["n"] += 1
    monkeypatch.setattr(cx, "market_close", _should_not_run)

    _run(ce._advance_open_position(db, order))
    assert be_called.get("stop_order_id") == 1
    assert be_called.get("be_price") == pytest.approx(4502.0)
    assert exit_called["n"] == 0   # ratchet and exit are mutually exclusive THIS tick


def test_advance_open_position_stop_hit_closes_with_correct_reason(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, direction="LONG", management_state="FILLED",
                    entry_fill_price=4505.0, sl_price_current=4480.0, be_amended=True)

    monkeypatch.setattr(cc, "today_flush_utc", lambda now: now + datetime.timedelta(hours=1))
    async def _fake_bars(symbol, since):
        return [{"close": 4475.0, "high": 4481.0, "low": 4478.0}]   # low <= stop 4480
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fake_bars)

    called = {}
    async def _fake_close(db_, account_, order_, exit_reason):
        called["exit_reason"] = exit_reason
    monkeypatch.setattr(cx, "market_close", _fake_close)

    _run(ce._advance_open_position(db, order))
    assert called.get("exit_reason") == "STOP"


def test_advance_open_position_no_action_leaves_order_untouched(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, direction="LONG", management_state="FILLED",
                    entry_fill_price=4505.0, sl_price_current=4480.0, be_amended=True)

    monkeypatch.setattr(cc, "today_flush_utc", lambda now: now + datetime.timedelta(hours=1))
    async def _fake_bars(symbol, since):
        return [{"close": 4508.0, "high": 4510.0, "low": 4506.0}]   # flat -- no stop/target touch
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fake_bars)

    called = {"n": 0}
    async def _should_not_run(*a, **kw):
        called["n"] += 1
    monkeypatch.setattr(cx, "market_close", _should_not_run)

    _run(ce._advance_open_position(db, order))
    assert called["n"] == 0
    assert order.management_state == "FILLED"


# ------------------------------------------------------------------ _advance_one_row -- routing + commit/rollback

def test_advance_one_row_routes_pending_entry_states_correctly(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="PENDING_ENTRY")

    called = {"pending": 0, "open": 0}
    async def _fake_pending(db_, order_):
        called["pending"] += 1
    async def _fake_open(db_, order_):
        called["open"] += 1
    monkeypatch.setattr(ce, "_advance_pending_order", _fake_pending)
    monkeypatch.setattr(ce, "_advance_open_position", _fake_open)

    _run(ce._advance_one_row(db, order))
    assert called == {"pending": 1, "open": 0}


def test_advance_one_row_routes_open_position_states_correctly(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="RATCHETED")

    called = {"pending": 0, "open": 0}
    async def _fake_pending(db_, order_):
        called["pending"] += 1
    async def _fake_open(db_, order_):
        called["open"] += 1
    monkeypatch.setattr(ce, "_advance_pending_order", _fake_pending)
    monkeypatch.setattr(ce, "_advance_open_position", _fake_open)

    _run(ce._advance_one_row(db, order))
    assert called == {"pending": 0, "open": 1}


def test_advance_one_row_does_not_crash_the_loop_on_a_single_row_exception(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="PENDING_ENTRY")

    async def _boom(db_, order_):
        raise RuntimeError("simulated exchange timeout")
    monkeypatch.setattr(ce, "_advance_pending_order", _boom)

    _run(ce._advance_one_row(db, order))   # must not raise -- caller (the 30s loop) keeps going
