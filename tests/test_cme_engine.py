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
import cme_calendar as ccal
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
    # ExecutorGlobalConfig too (Lane H tests engage the global kill switch /
    # disable Live Orders; on Windows the DB file can survive between tests).
    for model in (CMETransition, CMEOrder, CMEPlan, CMEConfig, ExecutorAccount, database.ExecutorGlobalConfig):
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


def _account(db, label="cme_engine_test", mode="DRY_RUN", active=True, kill_switch=False):
    account = ea.create_account(db, user_id=1, label=label)
    db.flush()
    account.mode = mode
    account.is_active = active
    account.kill_switch_engaged = kill_switch
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


# Lane H C5 (SPEC-H-20261114): _advance_pending_order now enforces the
# 60-min cross window and the session-flush backstop against
# cme_engine._now_utc(). Every fixture plan locks at 2026-11-04 15:00 UTC
# (10:00 ET), so the engine clock is frozen 30 min after that lock by
# default -- inside the cross window, before the 16:00 ET flush. Tests
# that need another instant re-freeze it.
_LOCK_UTC = datetime.datetime(2026, 11, 4, 15, 0, 0, tzinfo=UTC)
_T0 = _LOCK_UTC + datetime.timedelta(minutes=30)
_T0_NAIVE = _T0.replace(tzinfo=None)


def _freeze(monkeypatch, instant):
    monkeypatch.setattr(ce, "_now_utc", lambda: instant)


@pytest.fixture(autouse=True)
def _frozen_engine_clock(monkeypatch):
    _freeze(monkeypatch, _T0)


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


def test_process_symbol_lock_persists_missed_window_on_a_calibration_fetch_failure(db, monkeypatch):
    # 2026-11-06 -- changed from "no row at all" to a real MISSED_WINDOW
    # row: the old behavior left the desk showing an ambiguous "DATA
    # PENDING" for a day that was never actually checked, indistinguishable
    # from "nothing has happened yet today." MISSED_WINDOW is honest,
    # not fabricated -- it carries no trading data at all (box/bo/bd/etc
    # all stay None), just a real record that the fetch failed and why.
    account = _account(db)
    _config(db, account)

    async def _fail(symbol, date_key):
        raise NotImplementedError("Tradovate Market Data API not yet confirmed")
    monkeypatch.setattr(cm, "fetch_calibration_1m", _fail)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-04"))
    plan = db.query(CMEPlan).filter_by(symbol="MNQ", date_key="2026-11-04").first()
    assert plan is not None
    assert plan.status == "MISSED_WINDOW"
    assert "Tradovate Market Data API not yet confirmed" in plan.status_reason
    assert plan.box is None and plan.bo is None   # no fabricated trading data, just the status


def test_process_one_symbol_lock_tick_timeout_persists_a_missed_window_row(db, monkeypatch):
    # 2026-11-10 (audit Bug 1) -- the blanket except used to roll back and
    # return, leaving ZERO rows for the day (not even the fallback record),
    # so the desk leaked yesterday's error as today's. A timeout must now
    # commit a MISSED_WINDOW row, never a silent void.
    monkeypatch.setattr(ce, "_ROW_TIMEOUT_SECONDS", 0.05)
    account = _account(db)
    _config(db, account)

    async def _hang(symbol, date_key):
        await asyncio.sleep(999)   # never returns -> asyncio.wait_for raises TimeoutError
    monkeypatch.setattr(cm, "fetch_calibration_1m", _hang)

    _run(ce._process_one_symbol_lock_tick(db, "MNQ", "2026-11-10"))
    plan = db.query(CMEPlan).filter_by(symbol="MNQ", date_key="2026-11-10").first()
    assert plan is not None
    assert plan.status == "MISSED_WINDOW"
    assert "timed out" in plan.status_reason


def test_process_one_symbol_lock_tick_timeout_does_not_clobber_an_existing_row(db, monkeypatch):
    # Same idempotency convention as the fetch-failure path: if a real lock
    # somehow already exists for the day, a timeout must not overwrite it.
    monkeypatch.setattr(ce, "_ROW_TIMEOUT_SECONDS", 0.05)
    _plan(db, symbol="MNQ", date_key="2026-11-10", status="ARMED")
    db.commit()

    async def _hang(symbol, date_key):
        await asyncio.sleep(999)
    monkeypatch.setattr(cm, "fetch_calibration_1m", _hang)

    _run(ce._process_one_symbol_lock_tick(db, "MNQ", "2026-11-10"))
    mnq = db.query(CMEPlan).filter_by(symbol="MNQ", date_key="2026-11-10").one()
    assert mnq.status == "ARMED"   # untouched


def test_process_one_symbol_lock_tick_generic_error_rolls_back_without_row(db, monkeypatch):
    # A non-timeout exception escaping _process_symbol_lock (the calibration
    # fetch failure is caught INTERNALLY and already becomes a MISSED_WINDOW
    # row, so the only way to reach the generic-except is a real unexpected
    # raise) keeps the original rollback behavior -- surfaced loudly via
    # traceback, not silently swallowed, and NOT manufactured into a
    # MISSED_WINDOW row it cannot distinguish from a real one.
    account = _account(db)
    _config(db, account)

    async def _boom(db_, symbol, date_key):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(ce, "_process_symbol_lock", _boom)

    _run(ce._process_one_symbol_lock_tick(db, "MNQ", "2026-11-10"))
    assert db.query(CMEPlan).filter_by(symbol="MNQ", date_key="2026-11-10").count() == 0


# ------------------------------------------------------------------ _persist_missed_window_rows (2026-11-06)
# Real structural fragility found live: run_cme_session_loop() used to
# just sleep until tomorrow when the catchup window was missed -- today's
# date_key never got a CMEPlan row for ANY symbol at all. Pulled out as
# its own function specifically so this is testable without driving the
# whole async loop.

def test_persist_missed_window_rows_writes_a_row_per_symbol(db):
    lock_instant = datetime.datetime(2026, 11, 6, 15, 0, 0, tzinfo=datetime.timezone.utc)
    ce._persist_missed_window_rows(lock_instant)

    rows = db.query(CMEPlan).filter_by(date_key="2026-11-06").all()
    assert {r.symbol for r in rows} == {"MNQ", "MES"}
    for r in rows:
        assert r.status == "MISSED_WINDOW"
        assert "catchup window elapsed" in r.status_reason
        assert r.box is None   # no fabricated trading data


def test_persist_missed_window_rows_is_idempotent(db):
    # A real CMEPlan already exists for one symbol (e.g. a prior tick did
    # manage to lock MNQ for real before the process died) -- must not be
    # clobbered by a later missed-window sweep for the same date_key.
    _plan(db, symbol="MNQ", date_key="2026-11-06", status="ARMED")
    db.commit()   # _persist_missed_window_rows() opens its OWN connection -- must not race an uncommitted write
    lock_instant = datetime.datetime(2026, 11, 6, 15, 0, 0, tzinfo=datetime.timezone.utc)

    ce._persist_missed_window_rows(lock_instant)

    mnq = db.query(CMEPlan).filter_by(symbol="MNQ", date_key="2026-11-06").one()
    assert mnq.status == "ARMED"   # untouched -- the real lock wins, never overwritten
    mes = db.query(CMEPlan).filter_by(symbol="MES", date_key="2026-11-06").one()
    assert mes.status == "MISSED_WINDOW"   # MES still gets its own row


# ------------------------------------------------------------------ run_cme_session_loop() iteration-level robustness (2026-11-11)
# Real production incident: the clock-arithmetic/catchup-window span at the
# top of each iteration used to sit OUTSIDE any try/except, unlike
# run_cme_watch_loop()'s own fully-wrapped body. A single uncaught exception
# there would silently end this bare asyncio.create_task() forever -- no
# supervisor restarts it (see main.py's lifespan()). Proves one bad tick no
# longer kills the loop.

def test_run_cme_session_loop_survives_an_exception_in_the_clock_span(monkeypatch):
    calls = {"clock": 0, "sleep": 0}

    def _boom_once(now):
        calls["clock"] += 1
        if calls["clock"] == 1:
            raise RuntimeError("simulated cme_clock failure")
        raise asyncio.CancelledError()   # stop the infinite loop on the 2nd iteration
    monkeypatch.setattr(cc, "most_recent_lock_utc", _boom_once)

    async def _fake_sleep(seconds):
        calls["sleep"] += 1
    monkeypatch.setattr(ce.asyncio, "sleep", _fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        _run(ce.run_cme_session_loop())

    assert calls["clock"] == 2   # survived the first failure and tried again
    assert calls["sleep"] >= 1   # the outer except's backoff sleep actually ran


# ------------------------------------------------------------------ cme_calendar.py full-closure short-circuit (2026-11-11)
# Real gap found live: "was today a holiday" used to be inferred only from
# an empty calibration window (indistinguishable from a data-fetch failure
# or a dead background loop -- the exact ambiguity behind the 2026-11-11
# "was it closed for Veterans Day?" confusion). Proves a known full-closure
# day short-circuits BEFORE any fetch attempt and persists an explicit,
# unambiguous HOLIDAY_CLOSED row.

def test_process_symbol_lock_short_circuits_on_a_known_holiday_before_any_fetch(db, monkeypatch):
    account = _account(db)
    _config(db, account)

    async def _boom_if_called(symbol, date_key):
        raise AssertionError("fetch_calibration_1m should never be called on a known full-closure day")
    monkeypatch.setattr(cm, "fetch_calibration_1m", _boom_if_called)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-26"))   # Thanksgiving -- known full closure

    plan = db.query(CMEPlan).filter_by(symbol="MNQ", date_key="2026-11-26").first()
    assert plan is not None and plan.status == "HOLIDAY_CLOSED"
    assert db.query(CMEOrder).count() == 0


def test_process_symbol_lock_proceeds_normally_on_a_known_early_close_day(db, monkeypatch):
    # An early-close day is NOT a full closure -- the holiday short-circuit
    # must not fire for it (the flush-time override is cme_clock.py's job,
    # not this one's).
    account = _account(db)
    _config(db, account)
    monkeypatch.setattr(cx, "_client_for", lambda account_: object())

    async def _fake_calib(symbol, date_key):
        return [{"open": 100, "high": 101, "low": 99, "close": 100}] * 20
    monkeypatch.setattr(cm, "fetch_calibration_1m", _fake_calib)
    monkeypatch.setattr(cs, "calculate_session_lock", lambda symbol, bars, atr: dict(_ARMED_LOCK))
    async def _fake_equity(account_):
        return 50000.0
    monkeypatch.setattr(ce, "_query_equity", _fake_equity)
    _capture_emails(monkeypatch)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-27"))   # day after Thanksgiving -- early close, not closed

    plan = db.query(CMEPlan).filter_by(symbol="MNQ", date_key="2026-11-27").first()
    assert plan is not None and plan.status == "ARMED"


# ------------------------------------------------------------------ CME admission-rejection notification (2026-11-12)
# Real gap found live: unlike the Traveler's own placement-failure email,
# a CME admission rejection notified NOBODY -- Account 17 saw a cheerful
# "Session Locked, ARMED" email, then silence, with the real reason
# (NO_TRAILING_DD_LIMIT_CONFIGURED) sitting only in a CMEOrder row.

def test_process_symbol_lock_notifies_live_account_on_admission_rejection(db, monkeypatch):
    # Real trailing-DD breach (equity $3000 under HWM, over the $2000
    # limit) -- CMEConfig.trailing_dd_limit_usd is nullable=False with a
    # schema default of 2000.0, so "unconfigured" can no longer actually
    # happen for a real CMEConfig row; a genuine breach is the realistic
    # way a LIVE account gets rejected post-fix.
    account = _account(db, mode="LIVE")
    account.high_water_mark_usd = 50000.0
    db.commit()
    _config(db, account, daily_risk_budget_usd=350.0, contracts_cap=3, trailing_dd_limit_usd=2000.0)
    monkeypatch.setattr(cx, "_client_for", lambda account_: object())

    async def _fake_calib(symbol, date_key):
        return [{"open": 100, "high": 101, "low": 99, "close": 100}] * 20
    monkeypatch.setattr(cm, "fetch_calibration_1m", _fake_calib)
    monkeypatch.setattr(cs, "calculate_session_lock", lambda symbol, bars, atr: dict(_ARMED_LOCK))

    async def _fake_equity(account_):
        return 47000.0   # $3000 under HWM -- over the $2000 limit
    monkeypatch.setattr(ce, "_query_equity", _fake_equity)
    _capture_emails(monkeypatch)

    import notify
    account_emails = []
    email_categories = []
    telegram_calls = []
    monkeypatch.setattr(notify, "send_account_email", lambda subject, body, account_id, category="routine": (
        account_emails.append(subject), email_categories.append(category), True)[-1])
    monkeypatch.setattr(notify, "send_account_telegram", lambda category, text, account_id: telegram_calls.append(text) or True)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-04"))

    orders = db.query(CMEOrder).filter_by(account_id=account.id).all()
    assert all(o.decision == "REJECTED" for o in orders)
    assert len(account_emails) == 2   # one per direction (LONG + SHORT fan-out)
    assert all("REJECTED" in s and "MNQ" in s for s in account_emails)
    # 2026-11-12: an admission rejection is a "problem" category -- must
    # never be muted by notify_email_enabled, same as Telegram's own
    # always-on "problem" category.
    assert email_categories == ["problem", "problem"]
    assert len(telegram_calls) == 2
    assert all("TRAILING_DD_BREACHED" in t for t in telegram_calls)


def test_process_symbol_lock_does_not_notify_a_dry_run_account_on_rejection(db, monkeypatch):
    account = _account(db, mode="DRY_RUN")
    account.high_water_mark_usd = 50000.0
    db.commit()
    _config(db, account, daily_risk_budget_usd=350.0, contracts_cap=3, trailing_dd_limit_usd=2000.0)
    monkeypatch.setattr(cx, "_client_for", lambda account_: object())

    async def _fake_calib(symbol, date_key):
        return [{"open": 100, "high": 101, "low": 99, "close": 100}] * 20
    monkeypatch.setattr(cm, "fetch_calibration_1m", _fake_calib)
    monkeypatch.setattr(cs, "calculate_session_lock", lambda symbol, bars, atr: dict(_ARMED_LOCK))

    async def _fake_equity(account_):
        return 47000.0   # $3000 under HWM -- over the $2000 limit
    monkeypatch.setattr(ce, "_query_equity", _fake_equity)
    _capture_emails(monkeypatch)

    import notify
    account_emails = []
    monkeypatch.setattr(notify, "send_account_email", lambda subject, body, account_id, category="routine": account_emails.append(subject) or True)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-04"))

    orders = db.query(CMEOrder).filter_by(account_id=account.id).all()
    assert all(o.decision == "REJECTED" for o in orders)
    assert account_emails == []   # DRY_RUN -- no real consequence, no human needs alerting


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


def test_process_symbol_lock_rejects_closed_on_equity_query_failure(db, monkeypatch):
    # Real risk finding, 2026-11-11 (external audit): _query_equity() used
    # to fall back to account.high_water_mark_usd on a broker-query failure,
    # which made trailing_drawdown_usd() read as zero drawdown and silently
    # ADMIT a new position on a day the real equity (unknown) could already
    # be in breach of the MFFU trailing-DD rule. Proves a failed equity
    # query now fails this account's orders CLOSED (REJECTED,
    # EQUITY_QUERY_FAILED) instead of being admitted on a fabricated value.
    account = _account(db, mode="DRY_RUN")
    account.high_water_mark_usd = 50000.0
    db.commit()
    _config(db, account, daily_risk_budget_usd=350.0, contracts_cap=3)
    monkeypatch.setattr(cx, "_client_for", lambda account_: object())

    async def _fake_calib(symbol, date_key):
        return [{"open": 100, "high": 101, "low": 99, "close": 100}] * 20
    monkeypatch.setattr(cm, "fetch_calibration_1m", _fake_calib)
    monkeypatch.setattr(cs, "calculate_session_lock", lambda symbol, bars, atr: dict(_ARMED_LOCK))

    async def _failed_equity(account_):
        return None   # simulates _query_equity()'s own real contract on a broker-query failure
    monkeypatch.setattr(ce, "_query_equity", _failed_equity)
    _capture_emails(monkeypatch)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-04"))

    plan = db.query(CMEPlan).filter_by(symbol="MNQ", date_key="2026-11-04").first()
    assert plan is not None and plan.status == "ARMED"   # the plan itself is unaffected
    orders = db.query(CMEOrder).filter_by(cme_plan_id=plan.id, account_id=account.id).all()
    assert {o.direction for o in orders} == {"LONG", "SHORT"}
    assert all(o.decision == "REJECTED" for o in orders)
    assert all(o.decision_reason == "EQUITY_QUERY_FAILED -- broker balance query failed, cannot verify trailing drawdown" for o in orders)
    assert all(o.contracts == 0 for o in orders)


def test_query_equity_returns_none_never_fabricates_a_value(monkeypatch):
    account = ExecutorAccount(high_water_mark_usd=50000.0)
    monkeypatch.setattr(cx, "_client_for", lambda account_: (_ for _ in ()).throw(RuntimeError("no client")))
    result = _run(ce._query_equity(account))
    assert result is None


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
    db.commit()
    _config(db, account, daily_risk_budget_usd=350.0, contracts_cap=3, trailing_dd_limit_usd=2000.0)
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

    async def _fake_bars(symbol, since, until_utc=None):
        return [{"close": 4490.0}]   # inside the box -- no cross
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fake_bars)

    _run(ce._advance_pending_order(db, order))
    assert order.management_state == "PENDING_ENTRY"
    assert order.cross_confirmed_at is None


def test_advance_pending_order_cross_confirmed_places_bracket(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="PENDING_ENTRY")

    async def _fake_bars(symbol, since, until_utc=None):
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

    async def _fake_bars(symbol, since, until_utc=None):
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
    async def _should_not_run(symbol, since, until_utc=None):
        called["n"] += 1
        return [{"close": 4510.0}]
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _should_not_run)

    _run(ce._advance_pending_order(db, order))
    assert called["n"] == 0   # never even checked for a cross -- this row was never going live
    assert order.management_state == "PENDING_ENTRY"


def test_advance_pending_order_retest_window_elapsed_cancels(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    stale_cross = _T0_NAIVE - datetime.timedelta(minutes=120)
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
    recent_cross = _T0_NAIVE - datetime.timedelta(minutes=5)
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

    monkeypatch.setattr(cc, "flush_utc_for_date_key", lambda date_key: datetime.datetime.now(UTC) - datetime.timedelta(seconds=1))

    called = {}
    async def _fake_close(db_, account_, order_, exit_reason):
        called["exit_reason"] = exit_reason
    monkeypatch.setattr(cx, "market_close", _fake_close)

    _run(ce._advance_open_position(db, order))
    assert called.get("exit_reason") == "TIME_FLUSH"


def test_advance_open_position_flush_deadline_is_anchored_to_the_plans_own_date_key_not_today(db, monkeypatch):
    # 2026-11-13 real finding (Account 17): _advance_open_position() used
    # to call cme_clock.today_flush_utc(now_utc), which recomputes a FRESH
    # future deadline every time "today" (relative to the real wall clock)
    # advances -- so a position that survived past its own entry day's
    # 4:00 PM ET flush (loop downtime, an exchange outage, a failed
    # market_close() retry) got a silent extra day's grace once the
    # calendar rolled over, instead of staying overdue. This test uses a
    # plan dated far in the past with NO monkeypatch on the clock at all --
    # proving the real flush_utc_for_date_key(plan.date_key) anchor, not a
    # stubbed one, is what closes it on the very next poll regardless of
    # what day it actually is right now.
    account = _account(db, mode="LIVE")
    plan = _plan(db, date_key="2020-01-02")   # a Thursday, long since past
    order = _order(db, account, plan, management_state="FILLED", entry_fill_price=4505.0,
                    sl_price_current=4480.0, be_amended=False)

    called = {}
    async def _fake_close(db_, account_, order_, exit_reason):
        called["exit_reason"] = exit_reason
    monkeypatch.setattr(cx, "market_close", _fake_close)

    _run(ce._advance_open_position(db, order))
    assert called.get("exit_reason") == "TIME_FLUSH"


def test_advance_open_position_news_blackout_closes_regardless_of_price(db, monkeypatch):
    # 2026-11-11: MFFU's Tier-1 news rule requires zero open positions
    # within the blackout window -- checked ahead of TIME_FLUSH/stop/
    # target, same "mandatory override" treatment TIME_FLUSH already gets.
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", entry_fill_price=4505.0,
                    sl_price_current=4480.0, be_amended=False)

    monkeypatch.setattr(ccal, "news_blackout_reason", lambda now: "FOMC Decision (Jan 2026)")

    called = {}
    async def _fake_close(db_, account_, order_, exit_reason):
        called["exit_reason"] = exit_reason
    monkeypatch.setattr(cx, "market_close", _fake_close)

    _run(ce._advance_open_position(db, order))
    assert called.get("exit_reason") == "NEWS_BLACKOUT"


def test_advance_open_position_no_news_blackout_falls_through_to_normal_checks(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", entry_fill_price=4505.0,
                    sl_price_current=4480.0, be_amended=False)

    monkeypatch.setattr(ccal, "news_blackout_reason", lambda now: None)
    monkeypatch.setattr(cc, "flush_utc_for_date_key", lambda date_key: datetime.datetime.now(UTC) - datetime.timedelta(seconds=1))

    called = {}
    async def _fake_close(db_, account_, order_, exit_reason):
        called["exit_reason"] = exit_reason
    monkeypatch.setattr(cx, "market_close", _fake_close)

    _run(ce._advance_open_position(db, order))
    assert called.get("exit_reason") == "TIME_FLUSH"   # falls through past the (inactive) news check


def test_advance_open_position_breakeven_ratchet_fires_and_returns_before_exit_check(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, direction="LONG", management_state="FILLED",
                    entry_fill_price=4505.0, sl_price_current=4480.0, be_amended=False,
                    sl_exchange_order_id="1")

    monkeypatch.setattr(cc, "flush_utc_for_date_key", lambda date_key: datetime.datetime.now(UTC) + datetime.timedelta(hours=1))
    async def _fake_bars(symbol, since, until_utc=None):
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

    monkeypatch.setattr(cc, "flush_utc_for_date_key", lambda date_key: datetime.datetime.now(UTC) + datetime.timedelta(hours=1))
    async def _fake_bars(symbol, since, until_utc=None):
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

    monkeypatch.setattr(cc, "flush_utc_for_date_key", lambda date_key: datetime.datetime.now(UTC) + datetime.timedelta(hours=1))
    async def _fake_bars(symbol, since, until_utc=None):
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


# ================================================================== Lane H batch 1 (SPEC-H-20261114, AA-20261114-01/03)

from database import ExecutorAuditLog  # noqa: E402

_FLUSH_UTC = datetime.datetime(2026, 11, 4, 21, 0, 0, tzinfo=UTC)   # 16:00 ET on the fixture date_key


def _audit_messages(db, account, event_type=None):
    db.flush()
    q = db.query(ExecutorAuditLog).filter_by(account_id=account.id)
    if event_type:
        q = q.filter_by(event_type=event_type)
    return [r.message or "" for r in q.all()]


def _no_client(monkeypatch):
    def _boom(account_):
        raise AssertionError("no exchange call expected")
    monkeypatch.setattr(cx, "_client_for", _boom)


def _armed_lock_mocks(monkeypatch, equity=None):
    async def _fake_calib(symbol, date_key):
        return [{"open": 100, "high": 101, "low": 99, "close": 100}] * 20
    monkeypatch.setattr(cm, "fetch_calibration_1m", _fake_calib)

    async def _fake_atr(symbol, date_key):
        return None
    monkeypatch.setattr(cm, "fetch_daily_atr14", _fake_atr)
    monkeypatch.setattr(cs, "calculate_session_lock", lambda symbol, bars, atr: dict(_ARMED_LOCK))

    async def _equity(account_):
        if equity is None:
            raise AssertionError("equity must not be queried for a refused account")
        return equity
    monkeypatch.setattr(ce, "_query_equity", _equity)
    _capture_emails(monkeypatch)
    import notify
    sent = []
    monkeypatch.setattr(notify, "send_account_email", lambda subject, body, account_id, category="routine": sent.append(subject) or True)
    monkeypatch.setattr(notify, "send_account_telegram", lambda category, text, account_id: True)
    return sent


# ------------------------------------------------------------------ C7 admission

@pytest.mark.parametrize("setup,expected", [
    ("account_kill_switch", "SKIPPED_KILL_SWITCH"),
    ("inactive", "SKIPPED_ACCOUNT_INACTIVE"),
    ("global_kill_switch", "SKIPPED_KILL_SWITCH"),
])
def test_c7_admission_refuses_untradeable_account_with_terminal_rows(db, monkeypatch, setup, expected):
    account = _account(db, mode="LIVE", kill_switch=(setup == "account_kill_switch"), active=(setup != "inactive"))
    if setup == "global_kill_switch":
        ec.engage_global_kill_switch(db, reason="test", by="t")
        db.commit()
    _config(db, account)
    sent = _armed_lock_mocks(monkeypatch)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-04"))

    orders = db.query(CMEOrder).filter_by(account_id=account.id).all()
    assert sorted(o.direction for o in orders) == ["LONG", "SHORT"]
    assert all(o.decision == expected and o.management_state == expected for o in orders)
    assert all(o.contracts == 0 for o in orders)
    assert len(sent) == 2   # owner told, one per direction (LIVE only)


# ------------------------------------------------------------------ C9 admission

def test_c9_admission_live_account_with_live_orders_off_writes_terminal_skip_rows(db, monkeypatch):
    account = _account(db, mode="LIVE")
    ec.disable_live_orders(db, by="test")
    db.commit()
    _config(db, account)
    _armed_lock_mocks(monkeypatch)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-04"))

    orders = db.query(CMEOrder).filter_by(account_id=account.id).all()
    assert len(orders) == 2
    assert all(o.decision == "SKIPPED_LIVE_ORDERS_OFF" and o.management_state == "SKIPPED_LIVE_ORDERS_OFF" for o in orders)
    assert all(o.contracts == 0 for o in orders)
    assert any("Live Orders switch is OFF" in m for m in _audit_messages(db, account, "ERROR"))


def test_c9_admission_dry_run_account_ignores_live_orders_switch(db, monkeypatch):
    account = _account(db, mode="DRY_RUN")
    account.high_water_mark_usd = 50000.0
    ec.disable_live_orders(db, by="test")
    db.commit()
    _config(db, account, daily_risk_budget_usd=350.0, contracts_cap=3, trailing_dd_limit_usd=2000.0)
    _armed_lock_mocks(monkeypatch, equity=50000.0)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-04"))

    orders = db.query(CMEOrder).filter_by(account_id=account.id).all()
    assert len(orders) == 2
    assert all(o.decision in ("WOULD_PLACE", "REJECTED") for o in orders)   # normal admission, as before


def test_c7_c9_order_tradeable_is_reported_before_live_orders(db, monkeypatch):
    account = _account(db, mode="LIVE", kill_switch=True)
    ec.disable_live_orders(db, by="test")
    db.commit()
    _config(db, account)
    _armed_lock_mocks(monkeypatch)

    _run(ce._process_symbol_lock(db, "MNQ", "2026-11-04"))
    orders = db.query(CMEOrder).filter_by(account_id=account.id).all()
    assert all(o.decision == "SKIPPED_KILL_SWITCH" for o in orders)


# ------------------------------------------------------------------ C7 re-check at placement

def test_c7_cross_confirmed_but_kill_switch_engaged_since_admission_places_nothing(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="PENDING_ENTRY")
    account.kill_switch_engaged = True
    db.commit()

    async def _fake_bars(symbol, since, until_utc=None):
        return [{"close": 4510.0}]   # confirmed LONG cross
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fake_bars)

    async def _fail_place(*a, **kw):
        raise AssertionError("place_resting_bracket must not be called")
    monkeypatch.setattr(cx, "place_resting_bracket", _fail_place)

    _run(ce._advance_pending_order(db, order))
    assert order.management_state == "SKIPPED_KILL_SWITCH"
    assert order.closed_at is not None
    assert order.cross_confirmed_at is None


def test_c7_tradeable_check_error_fails_closed(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="PENDING_ENTRY")
    monkeypatch.setattr(ea, "is_account_tradeable", lambda db_, a: (False, "error checking tradeability, failing closed: x"))

    async def _fake_bars(symbol, since, until_utc=None):
        return [{"close": 4510.0}]
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fake_bars)

    async def _fail_place(*a, **kw):
        raise AssertionError("place_resting_bracket must not be called")
    monkeypatch.setattr(cx, "place_resting_bracket", _fail_place)

    _run(ce._advance_pending_order(db, order))
    assert order.management_state == "SKIPPED_ACCOUNT_INACTIVE"


# ------------------------------------------------------------------ AA-20261114-03: kill switch cancels a RESTING entry

@pytest.mark.parametrize("setup,terminal", [("global", "CLOSED_KILL_SWITCH"), ("inactive", "CLOSED_ACCOUNT_INACTIVE")])
def test_aa03_kill_switch_cancels_a_resting_entry(db, monkeypatch, setup, terminal):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="WAITING_RETEST",
                   cross_confirmed_at=_T0_NAIVE - datetime.timedelta(minutes=5), entry_exchange_order_id="777")
    if setup == "global":
        ec.engage_global_kill_switch(db, reason="test", by="t")
    else:
        account.is_active = False
    db.commit()

    seen = {}
    async def _fake_cancel(db_, account_, order_, terminal_state, reason):
        seen["terminal"] = terminal_state
        seen["reason"] = reason
        return "failed"
    monkeypatch.setattr(cx, "cancel_resting_entry", _fake_cancel)

    async def _fail_check(*a, **kw):
        raise AssertionError("must cancel, not keep waiting for the retest")
    monkeypatch.setattr(cx, "check_retest_fill_and_confirm", _fail_check)

    _run(ce._advance_pending_order(db, order))
    assert seen["terminal"] == terminal


def test_aa03_kill_switch_does_not_close_an_open_position(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="FILLED", entry_fill_price=4505.0,
                   sl_price_current=4480.0, be_amended=False)
    ec.engage_global_kill_switch(db, reason="test", by="t")
    db.commit()

    monkeypatch.setattr(ccal, "news_blackout_reason", lambda now: None)
    monkeypatch.setattr(cc, "flush_utc_for_date_key", lambda date_key: datetime.datetime.now(UTC) + datetime.timedelta(hours=1))

    async def _fake_bars(symbol, since, until_utc=None):
        return [{"open": 4505.0, "high": 4506.0, "low": 4504.0, "close": 4505.0}]
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fake_bars)

    async def _fail_close(*a, **kw):
        raise AssertionError("the kill switch must not close an open position")
    monkeypatch.setattr(cx, "market_close", _fail_close)

    _run(ce._advance_open_position(db, order))
    assert order.management_state == "FILLED"


# ------------------------------------------------------------------ C5 cross-window expiry

def test_c5_pending_entry_expires_to_no_cross_after_window_and_plan_done(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    long_o = _order(db, account, plan, direction="LONG")
    short_o = _order(db, account, plan, direction="SHORT")
    _freeze(monkeypatch, _LOCK_UTC + datetime.timedelta(minutes=61))
    _no_client(monkeypatch)

    async def _fake_bars(symbol, since, until_utc=None):
        return [{"close": 4490.0}]   # inside the box
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fake_bars)

    _run(ce._advance_pending_order(db, long_o))
    assert long_o.management_state == "NO_CROSS"
    assert long_o.closed_at is not None
    assert plan.status == "ARMED"   # the SHORT side hasn't expired yet

    _run(ce._advance_pending_order(db, short_o))
    assert short_o.management_state == "NO_CROSS"
    assert plan.status == "DONE"
    assert plan.status_reason == "no cross within window"


def test_c5_pending_entry_inside_window_passes_the_window_end_to_the_fetch(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan)
    seen = {}

    async def _fake_bars(symbol, since, until_utc=None):
        seen["until"] = until_utc
        return []
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fake_bars)

    _run(ce._advance_pending_order(db, order))
    assert seen["until"] == _LOCK_UTC + datetime.timedelta(minutes=60)
    assert order.management_state == "PENDING_ENTRY"


def test_c5_cross_after_window_is_ignored_and_row_expires(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan)
    _freeze(monkeypatch, _LOCK_UTC + datetime.timedelta(minutes=75))
    bars = [{"time": _LOCK_UTC + datetime.timedelta(minutes=70), "close": 4510.0}]   # crosses, but after the window

    async def _fake_bars(symbol, since, until_utc=None):
        return [{"close": b["close"]} for b in bars if until_utc is None or b["time"] < until_utc]
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fake_bars)

    async def _fail_place(*a, **kw):
        raise AssertionError("a post-window cross must never place a bracket")
    monkeypatch.setattr(cx, "place_resting_bracket", _fail_place)

    _run(ce._advance_pending_order(db, order))
    assert order.management_state == "NO_CROSS"


def test_c5_cross_inside_window_detected_late_still_places(db, monkeypatch):
    # Documents the as-built late placement the spec leaves alone (N-2).
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan)
    _freeze(monkeypatch, _LOCK_UTC + datetime.timedelta(minutes=90))

    async def _fake_bars(symbol, since, until_utc=None):
        return [{"close": 4510.0}]
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fake_bars)
    called = {}

    async def _fake_place(db_, account_, plan_, order_):
        called["id"] = order_.id
    monkeypatch.setattr(cx, "place_resting_bracket", _fake_place)

    _run(ce._advance_pending_order(db, order))
    assert called.get("id") == order.id


def test_c5_session_backstop_terminates_pending_entry_at_flush(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan)
    _freeze(monkeypatch, _FLUSH_UTC + datetime.timedelta(seconds=1))

    async def _fail_fetch(*a, **kw):
        raise AssertionError("no fetch after the session flush")
    monkeypatch.setattr(cm, "fetch_confirmed_5m", _fail_fetch)

    _run(ce._advance_pending_order(db, order))
    assert order.management_state == "NO_CROSS"
    assert any("session backstop" in m for m in _audit_messages(db, account, "ERROR"))


def test_c5_session_backstop_cancels_waiting_retest_at_flush(db, monkeypatch):
    account = _account(db, mode="LIVE")
    plan = _plan(db)
    order = _order(db, account, plan, management_state="WAITING_RETEST",
                   cross_confirmed_at=_FLUSH_UTC.replace(tzinfo=None) - datetime.timedelta(minutes=5),
                   entry_exchange_order_id="777")
    _freeze(monkeypatch, _FLUSH_UTC + datetime.timedelta(seconds=1))
    seen = {}

    async def _fake_cancel(db_, account_, order_, terminal_state, reason):
        seen["terminal"] = terminal_state
        return "cancelled"
    monkeypatch.setattr(cx, "cancel_resting_entry", _fake_cancel)

    _run(ce._advance_pending_order(db, order))
    assert seen["terminal"] == "NO_RETEST"


def test_c5_fetch_confirmed_5m_until_filter_drops_bars_after_the_window(monkeypatch):
    bars = [{"time": _LOCK_UTC + datetime.timedelta(minutes=5 * i), "open": 1.0, "high": 2.0, "low": 0.0, "close": float(i)}
            for i in range(20)]

    class _FakeCT:
        async def get_market_bars(self, *a, **kw):
            return bars
    monkeypatch.setattr(cm, "_require_crosstrade_client", lambda: _FakeCT())
    monkeypatch.setattr(cm.cme_contracts, "resolve_contract", lambda symbol: symbol)

    windowed = _run(cm.fetch_confirmed_5m("MNQ", _LOCK_UTC.replace(tzinfo=None),
                                          until_utc=_LOCK_UTC + datetime.timedelta(minutes=60)))
    assert [b["close"] for b in windowed] == [float(i) for i in range(12)]
    assert len(_run(cm.fetch_confirmed_5m("MNQ", _LOCK_UTC.replace(tzinfo=None)))) == 20   # unchanged without the bound
