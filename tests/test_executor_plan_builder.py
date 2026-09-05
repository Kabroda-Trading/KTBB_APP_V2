"""
Unit coverage for executor_plan_builder.py -- DB-backed with hand-built
TradePlan/ExecutorAccount/ExecutorRiskState rows, same fixture style as
tests/test_executor_accounts.py.

build_hypothetical_order() is async (2026-09-05 -- it queries the real
exchange leverage/margin mode when credentials are set). Most tests in
this file have no credentials set, so they exercise the "no credentials
yet -- use configured baseline" fallback path automatically -- exactly
the same values these tests already asserted. The credentialed / real-
exchange-query path (leverage/margin-mode mismatch REJECTED paths, and
the exchange-query-failure fallback) is covered by the dedicated tests
at the bottom of this file, which monkeypatch
executor_bitunix_client.BitunixClient.get_leverage_and_margin_mode
directly -- no real network call.
"""
import asyncio
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_executor_plan_builder.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount, ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorGlobalConfig, TradePlan
import executor_accounts as ea
import executor_plan_builder as epb


def _clean_db_files():
    for path in ["kabroda_test_executor_plan_builder.db", "kabroda_test_executor_plan_builder.db-journal",
                 "kabroda_test_executor_plan_builder.db-shm", "kabroda_test_executor_plan_builder.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    # Row-level, not just _clean_db_files() -- database.engine is cached
    # module-globally across the whole pytest session (see
    # test_trade_plan_engine.py's own fixture comment), so every test
    # file must clean up EVERY table it could plausibly collide on, not
    # just its own file's expected db path.
    for model in (ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorAccount, ExecutorGlobalConfig, TradePlan):
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


def _make_filled_plan(db, symbol="BTC/USDT", direction="LONG", entry=100.0, stop=95.0, t1=112.0, t2=120.0, t3=132.0, date_key="2026-09-04"):
    plan = TradePlan(
        symbol=symbol, date_key=date_key, session_id="us_ny_futures", status="FILLED",
        direction=direction, tier="STANDARD", trigger_price=entry, fill_price=entry,
        stop_price=stop, t1=t1, t2=t2, t3=t3,
    )
    db.add(plan)
    db.flush()
    return plan


def _make_account(db, label="andy_bitunix_main", leverage_baseline=10, assumed_balance_usd=10000.0):
    account = ea.create_account(db, user_id=1, label=label)
    account.leverage_baseline = leverage_baseline
    account.assumed_balance_usd = assumed_balance_usd
    db.flush()
    state = ea.get_or_init_risk_state(db, account)
    db.commit()
    return account, state


# ------------------------------------------------------------------ WOULD_PLACE (normal case, hand-verified math)

def test_would_place_normal_case(db):
    plan = _make_filled_plan(db, entry=100.0, stop=95.0)   # stop_distance=5
    account, state = _make_account(db, assumed_balance_usd=100000.0)  # huge balance -- no margin pressure
    # risk_last_usd default 100.0 -> qty = 100/5 = 20.0

    order = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["qty"] == pytest.approx(20.0)
    assert order["stop_distance"] == pytest.approx(5.0)
    assert order["leverage_used"] == 10  # baseline, no margin pressure with $100k balance
    assert order["liquidation_check_passed"] is True
    # liq at 10x LONG: 100*(1-0.1)=90, distance 10 > stop_distance 5 -- safe
    assert order["liquidation_price_estimate"] == pytest.approx(90.0)


def test_would_place_short_side(db):
    plan = _make_filled_plan(db, direction="SHORT", entry=100.0, stop=105.0)
    account, state = _make_account(db, assumed_balance_usd=100000.0)
    order = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["direction"] == "SHORT"
    assert order["liquidation_price_estimate"] == pytest.approx(110.0)


# ------------------------------------------------------------------ REJECTED (forced liquidation-check failure)

def test_rejected_when_liquidation_inside_stop(db):
    # A very tight stop (0.5 away) at 10x baseline leverage: liq_distance =
    # 100*(1/10)=10, far beyond the 0.5 stop -- need a MUCH higher leverage
    # to force liq inside the stop. Force it via a huge leverage_baseline
    # directly (bypassing suggest_leverage's own safety refusal by putting
    # the baseline itself already past the unsafe point).
    plan = _make_filled_plan(db, entry=100.0, stop=99.5)   # stop_distance=0.5
    account, state = _make_account(db, leverage_baseline=250, assumed_balance_usd=100000.0)
    # liq at 250x LONG: 100*(1-1/250)=99.6, distance 0.4 < stop_distance 0.5 -- UNSAFE
    order = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    assert order["decision"] == "REJECTED"
    assert order["liquidation_check_passed"] is False
    assert "refuse this trade" in order["decision_reason"]


# ------------------------------------------------------------------ SKIPPED_KILL_SWITCH / SKIPPED_ACCOUNT_INACTIVE

def test_skipped_when_account_kill_switch_engaged(db):
    plan = _make_filled_plan(db)
    account, state = _make_account(db)
    ea.engage_kill_switch(db, account, reason="testing", by="andy@kabroda.com")
    db.commit()
    order = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    assert order["decision"] == "SKIPPED_KILL_SWITCH"


def test_skipped_when_account_inactive(db):
    plan = _make_filled_plan(db)
    account, state = _make_account(db)
    account.is_active = False
    db.commit()
    order = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    assert order["decision"] == "SKIPPED_ACCOUNT_INACTIVE"


# ------------------------------------------------------------------ SKIPPED_ALREADY_IN_TRADE

def test_skipped_already_in_trade_same_plan_twice(db):
    plan = _make_filled_plan(db)
    account, state = _make_account(db, assumed_balance_usd=100000.0)
    first = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    assert first["decision"] == "WOULD_PLACE"
    db.add(ExecutorOrder(**{k: v for k, v in first.items() if k in ExecutorOrder.__table__.columns.keys()}))
    db.commit()

    second = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    assert second["decision"] == "SKIPPED_ALREADY_IN_TRADE"


def test_skipped_already_in_trade_different_open_plan(db):
    account, state = _make_account(db, assumed_balance_usd=100000.0)

    plan1 = _make_filled_plan(db, symbol="BTC/USDT", date_key="2026-09-03")
    order1 = asyncio.run(epb.build_hypothetical_order(db, plan1, account, state))
    assert order1["decision"] == "WOULD_PLACE"
    db.add(ExecutorOrder(**{k: v for k, v in order1.items() if k in ExecutorOrder.__table__.columns.keys()}))
    db.commit()
    # plan1 stays "FILLED" (not DONE) -- still open

    plan2 = _make_filled_plan(db, symbol="ETH/USDT", date_key="2026-09-04")
    order2 = asyncio.run(epb.build_hypothetical_order(db, plan2, account, state))
    assert order2["decision"] == "SKIPPED_ALREADY_IN_TRADE"


def test_not_skipped_when_prior_plan_is_done(db):
    account, state = _make_account(db, assumed_balance_usd=100000.0)

    plan1 = _make_filled_plan(db, symbol="BTC/USDT", date_key="2026-09-03")
    order1 = asyncio.run(epb.build_hypothetical_order(db, plan1, account, state))
    db.add(ExecutorOrder(**{k: v for k, v in order1.items() if k in ExecutorOrder.__table__.columns.keys()}))
    plan1.status = "DONE"   # resolved
    db.commit()

    plan2 = _make_filled_plan(db, symbol="ETH/USDT", date_key="2026-09-04")
    order2 = asyncio.run(epb.build_hypothetical_order(db, plan2, account, state))
    assert order2["decision"] == "WOULD_PLACE"


# ------------------------------------------------------------------ credentialed / real-exchange-query path
# (2026-09-05 -- the direct fix for the real leverage-mismatch incident:
# Andy's real Bitunix account was 40x while the whole design assumed
# 10x. These monkeypatch executor_bitunix_client.BitunixClient at the
# method level -- no real network call -- to prove the REJECTED/
# WOULD_PLACE paths driven by a real queried value actually fire.)

def _set_fake_credentials(db, account):
    ea.set_credentials(db, account, api_key="fake-key", api_secret="fake-secret", set_by="test@kabroda.com")
    db.commit()


def _patch_leverage_query(monkeypatch, leverage, margin_mode):
    import executor_bitunix_client

    async def fake_get_leverage_and_margin_mode(self, symbol, margin_coin="USDT"):
        return {"code": 0, "data": {"leverage": leverage, "marginMode": margin_mode}, "msg": "Success"}

    monkeypatch.setattr(executor_bitunix_client.BitunixClient, "get_leverage_and_margin_mode", fake_get_leverage_and_margin_mode)


def _patch_mmr_query(monkeypatch, mmr=0.0, start=0, end=10_000_000):
    # Default mmr=0.0 with a huge bracket -- reproduces the pre-MMR naive
    # liquidation formula exactly, so tests that only care about leverage
    # behavior don't need to also hand-recompute a real mmr's effect.
    # ALWAYS applied alongside _patch_leverage_query for any credentialed
    # test -- otherwise build_hypothetical_order() makes a REAL,
    # unmocked get_position_tiers network call (this project's own
    # testing discipline: no real network call in any test, ever).
    import executor_bitunix_client

    async def fake_get_position_tiers(self, symbol):
        return {"code": 0, "data": [
            {"symbol": symbol, "level": 1, "startValue": str(start), "endValue": str(end),
             "leverage": 125, "maintenanceMarginRate": str(mmr)},
        ], "msg": "Success"}

    monkeypatch.setattr(executor_bitunix_client.BitunixClient, "get_position_tiers", fake_get_position_tiers)


def test_would_place_uses_real_queried_leverage_when_credentials_set(db, monkeypatch):
    # Real account leverage (40x, exactly Andy's real incident value)
    # differs from the account's configured baseline (10x) -- the
    # verified exchange value must win, not the stored baseline. Stop is
    # tight enough (distance 1) to stay safely inside the 40x liquidation
    # distance (2.5) -- this test isolates "real leverage used" from
    # "leverage safety," which is covered separately below.
    plan = _make_filled_plan(db, entry=100.0, stop=99.0)   # stop_distance=1
    account, state = _make_account(db, leverage_baseline=10, assumed_balance_usd=100000.0)
    _set_fake_credentials(db, account)
    _patch_leverage_query(monkeypatch, leverage=40, margin_mode=account.margin_mode)
    _patch_mmr_query(monkeypatch)

    order = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["leverage_used"] == 40
    assert "verified against the real exchange account" in order["decision_reason"]
    # liq at 40x LONG: 100*(1-1/40)=97.5, distance 2.5 > stop_distance 1 -- safe
    assert order["liquidation_price_estimate"] == pytest.approx(97.5)


def test_rejected_when_real_margin_mode_mismatches_configured(db, monkeypatch):
    plan = _make_filled_plan(db, entry=100.0, stop=95.0)
    account, state = _make_account(db, assumed_balance_usd=100000.0)
    _set_fake_credentials(db, account)
    # Account is configured "ISOLATION" (database.py's real default) --
    # simulate the exchange actually reporting "CROSS" instead.
    _patch_leverage_query(monkeypatch, leverage=account.leverage_baseline, margin_mode="CROSS")
    _patch_mmr_query(monkeypatch)

    order = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    assert order["decision"] == "REJECTED"
    assert "margin mode" in order["decision_reason"]
    assert "CROSS" in order["decision_reason"]


def test_rejected_when_real_leverage_is_unsafe_for_the_stop(db, monkeypatch):
    # entry 100, stop 99.5 (distance 0.5) at a real queried 250x:
    # liq = 100*(1-1/250) = 99.6, distance 0.4 < stop_distance 0.5 -- unsafe.
    # The account's own configured baseline (10x) would have looked safe --
    # proves the REAL queried value, not the baseline, drives the refusal.
    plan = _make_filled_plan(db, entry=100.0, stop=99.5)
    account, state = _make_account(db, leverage_baseline=10, assumed_balance_usd=100000.0)
    _set_fake_credentials(db, account)
    _patch_leverage_query(monkeypatch, leverage=250, margin_mode=account.margin_mode)
    _patch_mmr_query(monkeypatch)

    order = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    assert order["decision"] == "REJECTED"
    assert order["liquidation_check_passed"] is False
    assert order["leverage_used"] == 250
    assert "refuse this trade" in order["decision_reason"]


def test_falls_back_to_baseline_when_exchange_query_fails(db, monkeypatch):
    import executor_bitunix_client

    async def fake_raises(self, symbol, margin_coin="USDT"):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(executor_bitunix_client.BitunixClient, "get_leverage_and_margin_mode", fake_raises)
    _patch_mmr_query(monkeypatch)

    plan = _make_filled_plan(db, entry=100.0, stop=95.0)
    account, state = _make_account(db, leverage_baseline=10, assumed_balance_usd=100000.0)
    _set_fake_credentials(db, account)

    order = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["leverage_used"] == 10  # falls back to the configured baseline
    assert "exchange query failed" in order["decision_reason"]
    assert "NOT verified against the exchange" in order["decision_reason"]


# ------------------------------------------------------------------ maintenance margin rate (Stage 2, 2026-09-05)
# The live get_position_tiers query folded into the safety check --
# never a hardcoded MMR table, same philosophy as the leverage query.

def test_would_place_uses_real_queried_mmr_and_selects_the_right_notional_tier(db, monkeypatch):
    # liq at 125x, mmr=0.004: 100*(1-1/125+0.004)=99.6, distance 0.4 --
    # stop must be tighter than that to stay safe (isolates "correct
    # tier selection" from "leverage safety," covered in the next test).
    plan = _make_filled_plan(db, entry=100.0, stop=99.7)   # stop_distance=0.3
    account, state = _make_account(db, leverage_baseline=125, assumed_balance_usd=100000.0)
    _set_fake_credentials(db, account)
    _patch_leverage_query(monkeypatch, leverage=125, margin_mode=account.margin_mode)
    # Two tiers -- the trade's notional must land in tier 1, not tier 2.
    import executor_bitunix_client

    async def fake_tiers(self, symbol):
        return {"code": 0, "data": [
            {"symbol": symbol, "level": 1, "startValue": "0", "endValue": "50000",
             "leverage": 125, "maintenanceMarginRate": "0.004"},
            {"symbol": symbol, "level": 2, "startValue": "50000", "endValue": "200000",
             "leverage": 100, "maintenanceMarginRate": "0.005"},
        ], "msg": "Success"}

    monkeypatch.setattr(executor_bitunix_client.BitunixClient, "get_position_tiers", fake_tiers)

    order = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["maintenance_margin_rate_used"] == pytest.approx(0.004)
    assert order["liquidation_price_estimate"] == pytest.approx(99.6)
    assert "verified against the real exchange position tiers" in order["decision_reason"]


def test_real_mmr_can_flip_a_would_place_to_rejected(db, monkeypatch):
    # entry 100, stop 97.6 (distance 2.4) at 40x: naive (mmr=0) liq=97.5,
    # distance 2.5 > 2.4 -- would look SAFE without the real mmr. With a
    # real mmr of 0.004: liq = 100*(1-1/40+0.004) = 97.9, distance 2.1 <
    # 2.4 -- REJECTED. This is the real safety improvement this fix buys.
    plan = _make_filled_plan(db, entry=100.0, stop=97.6)
    account, state = _make_account(db, leverage_baseline=40, assumed_balance_usd=100000.0)
    _set_fake_credentials(db, account)
    _patch_leverage_query(monkeypatch, leverage=40, margin_mode=account.margin_mode)
    _patch_mmr_query(monkeypatch, mmr=0.004)

    order = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    assert order["decision"] == "REJECTED"
    assert order["liquidation_check_passed"] is False
    assert order["maintenance_margin_rate_used"] == pytest.approx(0.004)
    assert order["liquidation_price_estimate"] == pytest.approx(97.9)


def test_mmr_query_failure_falls_back_to_conservative_constant_not_zero(db, monkeypatch):
    import executor_bitunix_client

    async def fake_tiers_raises(self, symbol):
        raise RuntimeError("simulated tiers query failure")

    plan = _make_filled_plan(db, entry=100.0, stop=95.0)
    account, state = _make_account(db, leverage_baseline=10, assumed_balance_usd=100000.0)
    _set_fake_credentials(db, account)
    _patch_leverage_query(monkeypatch, leverage=10, margin_mode=account.margin_mode)
    monkeypatch.setattr(executor_bitunix_client.BitunixClient, "get_position_tiers", fake_tiers_raises)

    order = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    # A query FAILURE (credentials exist, call was attempted) uses the
    # elevated conservative fallback (0.01), unlike the benign "no
    # credentials yet" case which uses 0.0 -- never hard-blocks purely
    # because the tiers query failed, but never silently assumes 0 either.
    assert order["maintenance_margin_rate_used"] == pytest.approx(0.01)
    assert "NOT verified against the exchange" in order["decision_reason"]


def test_no_credentials_uses_zero_mmr_not_the_conservative_fallback(db):
    # The benign "never connected yet" case -- matches
    # _query_real_leverage_and_margin_mode()'s own no-credentials
    # behavior (reuse the baseline, don't get artificially more cautious).
    plan = _make_filled_plan(db, entry=100.0, stop=95.0)
    account, state = _make_account(db, leverage_baseline=10, assumed_balance_usd=100000.0)
    # no _set_fake_credentials() call -- account has no credentials set

    order = asyncio.run(epb.build_hypothetical_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["maintenance_margin_rate_used"] == pytest.approx(0.0)
    assert order["liquidation_price_estimate"] == pytest.approx(90.0)  # naive formula, unchanged
