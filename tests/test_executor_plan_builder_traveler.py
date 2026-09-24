"""
Unit coverage for executor_plan_builder.py's build_hypothetical_traveler_
order() -- the GATE_TRAVELER counterpart to build_hypothetical_order(),
which tests/test_executor_plan_builder.py already covers exhaustively.

Written 2026-09-24 (V2 Crown retirement, Step 3f-ii) as a real gap
backfill, not an afterthought: before test_executor_plan_builder.py (and
build_hypothetical_order() itself) get deleted, this file confirms the
SAME sizing/leverage/liquidation/kill-switch/idempotency behavior --
which lives in the SHARED _size_and_check_order() core, confirmed via
source read to have zero V2-specific branches -- actually works when
exercised through the wrapper that's live in production TODAY.
Previously, build_hypothetical_traveler_order() had ZERO real test
coverage anywhere: every other test file that references it
(tests/test_executor_live_e1_engine.py) monkeypatches it out entirely
rather than calling the real function.

Each test here mirrors one from test_executor_plan_builder.py by name
(swap "order" for "traveler_order" in the docstring/assertions) using a
TravelerPlan row instead of TradePlan, and build_hypothetical_traveler_
order() instead of build_hypothetical_order(). Real differences from the
V2 vehicle, not oversights:
  - TravelerPlan has no t2/t3 (E1 is single full-exit) -- base["t2_price"]/
    ["t3_price"] are always None, asserted where relevant.
  - The "already in trade" check reads the OTHER order's management_state
    against _E1's open-states tuple, not the plan's own status (TravelerPlan
    has no TradePlan-style status the executor writes to) -- "resolved" is
    represented by setting management_state to a real CLOSED_* value
    (executor_live_e1_engine.py's own f"CLOSED_{exit_reason}" convention),
    not a plan-level flag.
  - build_hypothetical_traveler_order() always computes and passes a real
    F_A sizing_multiplier (executor_sizing.f_a_multiplier(), never None) --
    every test below sets rsi_4h_at_cross to the side's own EXTREME
    threshold (>=80 LONG / <=20 SHORT) so F_A resolves to 1.0, isolating
    "does the wrapper wire sizing/leverage/liquidation correctly" from
    "does F_A scaling itself work" (a separate, already-covered concern).
"""
import asyncio
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_executor_plan_builder_traveler.db"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from cryptography.fernet import Fernet

import database
from database import SessionLocal, ExecutorAccount, ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorGlobalConfig, ExecutorSizingPolicy, TravelerPlan
import executor_accounts as ea
import executor_plan_builder as epb


def _clean_db_files():
    for path in ["kabroda_test_executor_plan_builder_traveler.db", "kabroda_test_executor_plan_builder_traveler.db-journal",
                 "kabroda_test_executor_plan_builder_traveler.db-shm", "kabroda_test_executor_plan_builder_traveler.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _clean_rows(session):
    # ExecutorSizingPolicy included -- omitting it let a leftover row from
    # an earlier test (any file; the SQLite engine is cached module-globally
    # across the whole pytest session) survive keyed on a since-reused
    # account_id and silently override a later test's expected default
    # sizing (confirmed the hard way: passed in isolation, failed in the
    # full suite with risk_dollars_used=1000.0 instead of the expected 50.0).
    for model in (ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorSizingPolicy, ExecutorAccount, ExecutorGlobalConfig, TravelerPlan):
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


def _make_filled_plan(db, symbol="BTC/USDT", direction="LONG", entry=100.0, stop=95.0, t1=112.0, date_key="2026-09-04", rsi_4h_at_cross=80.0):
    # rsi_4h_at_cross defaults to the LONG extreme threshold (80.0) so
    # f_a_multiplier() resolves to 1.0 (neutral) -- callers passing
    # direction="SHORT" must override this to 20.0 (that side's own
    # extreme), same as the direction-flip tests below do.
    plan = TravelerPlan(
        symbol=symbol, date_key=date_key, session_id="us_ny_futures", status="FILLED",
        direction=direction, fill_price=entry, stop_price=stop, t1_price=t1,
        rsi_4h_at_cross=rsi_4h_at_cross,
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

    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["qty"] == pytest.approx(20.0)
    assert order["stop_distance"] == pytest.approx(5.0)
    assert order["leverage_used"] == 10  # baseline, no margin pressure with $100k balance
    assert order["liquidation_check_passed"] is True
    assert order["liquidation_price_estimate"] == pytest.approx(90.0)
    assert order["sizing_multiplier_used"] == pytest.approx(1.0)   # F_A extreme (rsi 80 for LONG)
    assert order["t2_price"] is None and order["t3_price"] is None  # E1 has no T2/T3
    assert order["traveler_plan_id"] == plan.id


def test_would_place_short_side(db):
    plan = _make_filled_plan(db, direction="SHORT", entry=100.0, stop=105.0, rsi_4h_at_cross=20.0)
    account, state = _make_account(db, assumed_balance_usd=100000.0)
    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["direction"] == "SHORT"
    assert order["liquidation_price_estimate"] == pytest.approx(110.0)
    assert order["sizing_multiplier_used"] == pytest.approx(1.0)   # F_A extreme (rsi 20 for SHORT)


# ------------------------------------------------------------------ REJECTED (forced liquidation-check failure)

def test_rejected_when_liquidation_inside_stop(db):
    plan = _make_filled_plan(db, entry=100.0, stop=99.5)   # stop_distance=0.5
    account, state = _make_account(db, leverage_baseline=250, assumed_balance_usd=100000.0)
    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "REJECTED"
    assert order["liquidation_check_passed"] is False
    assert "refuse this trade" in order["decision_reason"]


# ------------------------------------------------------------------ SKIPPED_KILL_SWITCH / SKIPPED_ACCOUNT_INACTIVE

def test_skipped_when_account_kill_switch_engaged(db):
    plan = _make_filled_plan(db)
    account, state = _make_account(db)
    ea.engage_kill_switch(db, account, reason="testing", by="andy@kabroda.com")
    db.commit()
    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "SKIPPED_KILL_SWITCH"


def test_skipped_when_account_inactive(db):
    plan = _make_filled_plan(db)
    account, state = _make_account(db)
    account.is_active = False
    db.commit()
    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "SKIPPED_ACCOUNT_INACTIVE"


# ------------------------------------------------------------------ SKIPPED_ALREADY_IN_TRADE

def test_skipped_already_in_trade_same_plan_twice(db):
    plan = _make_filled_plan(db)
    account, state = _make_account(db, assumed_balance_usd=100000.0)
    first = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert first["decision"] == "WOULD_PLACE"
    db.add(ExecutorOrder(**{k: v for k, v in first.items() if k in ExecutorOrder.__table__.columns.keys()}))
    db.commit()

    second = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert second["decision"] == "SKIPPED_ALREADY_IN_TRADE"


def test_skipped_already_in_trade_different_open_plan(db):
    account, state = _make_account(db, assumed_balance_usd=100000.0)

    plan1 = _make_filled_plan(db, symbol="BTC/USDT", date_key="2026-09-03")
    order1 = asyncio.run(epb.build_hypothetical_traveler_order(db, plan1, account, state))
    assert order1["decision"] == "WOULD_PLACE"
    row1 = ExecutorOrder(**{k: v for k, v in order1.items() if k in ExecutorOrder.__table__.columns.keys()})
    row1.management_state = "PENDING_ENTRY"   # still open -- an E1 open-state, not TravelerPlan.status
    db.add(row1)
    db.commit()

    plan2 = _make_filled_plan(db, symbol="ETH/USDT", date_key="2026-09-04")
    order2 = asyncio.run(epb.build_hypothetical_traveler_order(db, plan2, account, state))
    assert order2["decision"] == "SKIPPED_ALREADY_IN_TRADE"


def test_not_skipped_when_prior_order_is_closed(db):
    # E1's own "still open" check reads the OTHER order's management_state
    # against its open-states tuple, not the plan's own status (TravelerPlan
    # has no executor-facing status field) -- "resolved" here means a real
    # CLOSED_* value, executor_live_e1_engine.py's own f"CLOSED_{exit_reason}"
    # convention.
    account, state = _make_account(db, assumed_balance_usd=100000.0)

    plan1 = _make_filled_plan(db, symbol="BTC/USDT", date_key="2026-09-03")
    order1 = asyncio.run(epb.build_hypothetical_traveler_order(db, plan1, account, state))
    row1 = ExecutorOrder(**{k: v for k, v in order1.items() if k in ExecutorOrder.__table__.columns.keys()})
    row1.management_state = "CLOSED_T1"   # resolved
    db.add(row1)
    db.commit()

    plan2 = _make_filled_plan(db, symbol="ETH/USDT", date_key="2026-09-04")
    order2 = asyncio.run(epb.build_hypothetical_traveler_order(db, plan2, account, state))
    assert order2["decision"] == "WOULD_PLACE"


# ------------------------------------------------------------------ credentialed / real-exchange-query path

def _set_fake_credentials(db, account):
    ea.set_credentials(db, account, api_key="fake-key", api_secret="fake-secret", set_by="test@kabroda.com")
    db.commit()


def _patch_leverage_query(monkeypatch, leverage, margin_mode):
    import executor_bitunix_client

    async def fake_get_leverage_and_margin_mode(self, symbol, margin_coin="USDT"):
        return {"code": 0, "data": {"leverage": leverage, "marginMode": margin_mode}, "msg": "Success"}

    monkeypatch.setattr(executor_bitunix_client.BitunixClient, "get_leverage_and_margin_mode", fake_get_leverage_and_margin_mode)


def _patch_mmr_query(monkeypatch, mmr=0.0, start=0, end=10_000_000):
    import executor_bitunix_client

    async def fake_get_position_tiers(self, symbol):
        return {"code": 0, "data": [
            {"symbol": symbol, "level": 1, "startValue": str(start), "endValue": str(end),
             "leverage": 125, "maintenanceMarginRate": str(mmr)},
        ], "msg": "Success"}

    monkeypatch.setattr(executor_bitunix_client.BitunixClient, "get_position_tiers", fake_get_position_tiers)


def test_would_place_uses_real_queried_leverage_when_credentials_set(db, monkeypatch):
    plan = _make_filled_plan(db, entry=100.0, stop=99.0)   # stop_distance=1
    account, state = _make_account(db, leverage_baseline=10, assumed_balance_usd=100000.0)
    _set_fake_credentials(db, account)
    _patch_leverage_query(monkeypatch, leverage=40, margin_mode=account.margin_mode)
    _patch_mmr_query(monkeypatch)

    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["leverage_used"] == 40
    assert "verified against the real exchange account" in order["decision_reason"]
    assert order["liquidation_price_estimate"] == pytest.approx(97.5)


def test_rejected_when_real_margin_mode_mismatches_configured(db, monkeypatch):
    plan = _make_filled_plan(db, entry=100.0, stop=95.0)
    account, state = _make_account(db, assumed_balance_usd=100000.0)
    _set_fake_credentials(db, account)
    _patch_leverage_query(monkeypatch, leverage=account.leverage_baseline, margin_mode="CROSS")
    _patch_mmr_query(monkeypatch)

    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "REJECTED"
    assert "margin mode" in order["decision_reason"]
    assert "CROSS" in order["decision_reason"]


def test_rejected_when_real_leverage_is_unsafe_for_the_stop(db, monkeypatch):
    plan = _make_filled_plan(db, entry=100.0, stop=99.5)
    account, state = _make_account(db, leverage_baseline=10, assumed_balance_usd=100000.0)
    _set_fake_credentials(db, account)
    _patch_leverage_query(monkeypatch, leverage=250, margin_mode=account.margin_mode)
    _patch_mmr_query(monkeypatch)

    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
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

    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["leverage_used"] == 10
    assert "exchange query failed" in order["decision_reason"]
    assert "NOT verified against the exchange" in order["decision_reason"]


# ------------------------------------------------------------------ maintenance margin rate

def test_would_place_uses_real_queried_mmr_and_selects_the_right_notional_tier(db, monkeypatch):
    plan = _make_filled_plan(db, entry=100.0, stop=99.7)   # stop_distance=0.3
    account, state = _make_account(db, leverage_baseline=125, assumed_balance_usd=100000.0)
    _set_fake_credentials(db, account)
    _patch_leverage_query(monkeypatch, leverage=125, margin_mode=account.margin_mode)
    import executor_bitunix_client

    async def fake_tiers(self, symbol):
        return {"code": 0, "data": [
            {"symbol": symbol, "level": 1, "startValue": "0", "endValue": "50000",
             "leverage": 125, "maintenanceMarginRate": "0.004"},
            {"symbol": symbol, "level": 2, "startValue": "50000", "endValue": "200000",
             "leverage": 100, "maintenanceMarginRate": "0.005"},
        ], "msg": "Success"}

    monkeypatch.setattr(executor_bitunix_client.BitunixClient, "get_position_tiers", fake_tiers)

    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["maintenance_margin_rate_used"] == pytest.approx(0.004)
    assert order["liquidation_price_estimate"] == pytest.approx(99.6)
    assert "verified against the real exchange position tiers" in order["decision_reason"]


def test_real_mmr_can_flip_a_would_place_to_rejected(db, monkeypatch):
    plan = _make_filled_plan(db, entry=100.0, stop=97.6)
    account, state = _make_account(db, leverage_baseline=40, assumed_balance_usd=100000.0)
    _set_fake_credentials(db, account)
    _patch_leverage_query(monkeypatch, leverage=40, margin_mode=account.margin_mode)
    _patch_mmr_query(monkeypatch, mmr=0.004)

    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
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

    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["maintenance_margin_rate_used"] == pytest.approx(0.01)
    assert "NOT verified against the exchange" in order["decision_reason"]


def _patch_balance_query(monkeypatch, available, margin=0, isolation_unrealized_pnl=0):
    import executor_bitunix_client

    async def fake_get_balance(self, margin_coin="USDT"):
        return {"code": 0, "data": {
            "available": str(available), "margin": str(margin),
            "isolationUnrealizedPNL": str(isolation_unrealized_pnl),
        }, "msg": "Success"}

    monkeypatch.setattr(executor_bitunix_client.BitunixClient, "get_balance", fake_get_balance)


# ------------------------------------------------------------------ Sizing Policy Wizard wiring

def test_would_place_uses_percent_of_balance_stake_with_live_balance(db, monkeypatch):
    plan = _make_filled_plan(db, entry=100.0, stop=95.0)   # stop_distance=5, safe at 10x baseline (liq=90)
    account, state = _make_account(db, assumed_balance_usd=999999.0)  # must NOT be used -- real balance wins
    _set_fake_credentials(db, account)
    _patch_leverage_query(monkeypatch, leverage=account.leverage_baseline, margin_mode=account.margin_mode)
    _patch_mmr_query(monkeypatch)
    _patch_balance_query(monkeypatch, available=2000.0)

    ea.update_sizing_policy(db, account, {"base_risk_usd": None, "base_risk_pct": 0.10, "roll_in_pct": None}, updated_by="test@kabroda.com")
    db.commit()

    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["risk_dollars_used"] == pytest.approx(200.0)
    assert order["qty"] == pytest.approx(40.0)  # 200/5
    assert order["stake_calculation_detail"]["base"] == pytest.approx(200.0)
    assert "verified against the real exchange account" in order["stake_calculation_detail"]["balance_source"]


def test_would_place_falls_back_to_assumed_balance_when_no_credentials(db):
    plan = _make_filled_plan(db, entry=100.0, stop=95.0)   # stop_distance=5, safe at 10x baseline (liq=90)
    account, state = _make_account(db, assumed_balance_usd=5000.0)
    ea.update_sizing_policy(db, account, {"base_risk_usd": None, "base_risk_pct": 0.10, "roll_in_pct": None}, updated_by="test@kabroda.com")
    db.commit()

    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["risk_dollars_used"] == pytest.approx(500.0)  # 10% of assumed 5000
    assert order["qty"] == pytest.approx(100.0)  # 500/5
    assert "assumed_balance_usd" in order["stake_calculation_detail"]["balance_source"]


def test_balance_not_queried_when_policy_does_not_need_it(db, monkeypatch):
    import executor_bitunix_client

    async def fail_if_called(self, margin_coin="USDT"):
        raise AssertionError("get_balance() must not be called for a FIXED-mode policy")

    monkeypatch.setattr(executor_bitunix_client.BitunixClient, "get_balance", fail_if_called)

    plan = _make_filled_plan(db, entry=100.0, stop=95.0)
    account, state = _make_account(db, assumed_balance_usd=100000.0)
    _set_fake_credentials(db, account)
    _patch_leverage_query(monkeypatch, leverage=account.leverage_baseline, margin_mode=account.margin_mode)
    _patch_mmr_query(monkeypatch)
    ea.update_sizing_policy(db, account, {"base_risk_usd": 100.0, "roll_in_pct": None}, updated_by="test@kabroda.com")
    db.commit()

    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["stake_calculation_detail"]["balance_source"] == "not queried -- policy does not use account balance"


def test_stake_calculation_detail_present_and_cap_binds(db):
    plan = _make_filled_plan(db, entry=100.0, stop=95.0)   # stop_distance=5, safe at 10x baseline (liq=90)
    account, state = _make_account(db, assumed_balance_usd=12000.0)
    ea.update_sizing_policy(
        db, account,
        {"base_risk_usd": None, "base_risk_pct": 0.10, "roll_in_pct": None,
         "tier_threshold_usd": 10000.0, "tier_flat_usd": 1000.0},
        updated_by="test@kabroda.com",
    )
    db.commit()

    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["risk_dollars_used"] == pytest.approx(1000.0)
    assert order["qty"] == pytest.approx(200.0)  # 1000/5
    detail = order["stake_calculation_detail"]
    assert detail["tier_applied"] is True
    assert detail["final_stake"] == pytest.approx(1000.0)


def test_no_credentials_uses_zero_mmr_not_the_conservative_fallback(db):
    plan = _make_filled_plan(db, entry=100.0, stop=95.0)
    account, state = _make_account(db, leverage_baseline=10, assumed_balance_usd=100000.0)
    # no _set_fake_credentials() call -- account has no credentials set

    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["maintenance_margin_rate_used"] == pytest.approx(0.0)
    assert order["liquidation_price_estimate"] == pytest.approx(90.0)  # naive formula, unchanged


# ------------------------------------------------------------------ F_A sizing multiplier (Traveler-only -- no V2 equivalent)

def test_f_a_non_extreme_rsi_applies_the_half_multiplier(db):
    # Direct proof the wrapper actually PASSES a real (not None/1.0)
    # sizing_multiplier through when RSI-at-cross isn't extreme -- the
    # one piece of build_hypothetical_traveler_order()'s own logic with
    # no V2 analog at all, so it gets its own dedicated test rather than
    # just being ported.
    plan = _make_filled_plan(db, entry=100.0, stop=95.0, rsi_4h_at_cross=55.0)  # not >=80 -- non-extreme
    account, state = _make_account(db, assumed_balance_usd=100000.0)
    order = asyncio.run(epb.build_hypothetical_traveler_order(db, plan, account, state))
    assert order["decision"] == "WOULD_PLACE"
    assert order["sizing_multiplier_used"] == pytest.approx(0.5)
    assert order["risk_dollars_used"] == pytest.approx(50.0)  # 100 base * 0.5 F_A
    assert order["qty"] == pytest.approx(10.0)  # 50/5
