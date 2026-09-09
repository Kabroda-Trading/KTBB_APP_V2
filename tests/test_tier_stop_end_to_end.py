"""
End-to-end proof, not a unit test of one function in isolation: walks a
COMPLETE real scenario through the exact chain a live trade tomorrow would
use -- trade_plan.build_trade_plan() -> a real TradePlan row persisted to a
real database (same valid_cols filtering kabroda_mas_flow.py's real
_inject_trade_plan_to_database() uses) -> executor_plan_builder.
build_hypothetical_order() reading that row back, exactly as
executor_engine.process_fill() does for a real account.

Built specifically to answer Andy's ask (2026-09-08): "if there is a trade
tomorrow it will put on my trade just like we are talking about" -- this
confirms, with real code wired together (no mocks of the logic itself,
only the Bitunix HTTP client, which build_hypothetical_order() never calls),
that a STANDARD trade's real order gets sized against the r30 stop and a
PREMIUM trade's against the zone stop, all the way through to the qty math
the exchange would actually receive.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./kabroda_test_tier_stop_e2e.db")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

import asyncio

import pytest
from cryptography.fernet import Fernet

import database
import executor_accounts as ea
import executor_plan_builder as epb
import trade_plan as tp
from database import ExecutorAccount, ExecutorAuditLog, ExecutorOrder, ExecutorRiskState, ExecutorSizingPolicy, SessionLocal, TradePlan, UserModel

ANCHOR = datetime.datetime(2026, 9, 9, 13, 0, 0, tzinfo=datetime.timezone.utc)


def _clean_db_files():
    for path in ["kabroda_test_tier_stop_e2e.db", "kabroda_test_tier_stop_e2e.db-journal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", Fernet.generate_key().decode("utf-8"))
    _clean_db_files()
    database.init_db()
    session = SessionLocal()
    for model in (ExecutorOrder, ExecutorAuditLog, ExecutorRiskState, ExecutorSizingPolicy, ExecutorAccount, TradePlan):
        session.query(model).delete()
    session.query(UserModel).filter(UserModel.email == "e2e_test@kabroda.com").delete(synchronize_session=False)
    session.commit()
    yield session
    session.close()
    _clean_db_files()


def _persist_trade_plan(db, plan_fields, symbol, date_key, session_id):
    """Same real filter kabroda_mas_flow.py's _inject_trade_plan_to_database()
    uses -- not a hand-picked subset, so this test can't silently drift from
    what a real trade plan write actually persists."""
    valid_cols = set(TradePlan.__table__.columns.keys())
    row = TradePlan(symbol=symbol, date_key=date_key, session_id=session_id,
                     **{k: v for k, v in plan_fields.items() if k in valid_cols and k not in ("symbol", "date_key", "session_id")})
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _flat_candles(price=100.0, n=10):
    return [{"open": price, "high": price + 1, "low": price - 1, "close": price} for _ in range(n)]


def _real_account(db):
    user = UserModel(email="e2e_test@kabroda.com", username="e2e", password_hash="x",
                      subscription_status="active", tier="basic")
    db.add(user)
    db.commit()
    account = ea.create_account(db, user_id=user.id, label="e2e_bitunix", exchange="bitunix")
    ea.update_sizing_policy(db, account, {"base_risk_usd": 100.0}, updated_by="test")
    db.commit()
    return account


def _take_decision(side, tier, entry, t1, t2, t3):
    return {
        "verdict_state": "TAKE_PREMIUM" if tier == "PREMIUM" else "TAKE_STANDARD",
        "side": side, "tier": tier,
        "entry_price": entry, "stop_loss": entry - 20.0 if side == "LONG" else entry + 20.0,
        "t1": t1, "t2": t2, "t3": t3,
        "tactical_brief": "gate approved",
    }


def test_standard_trade_end_to_end_uses_the_r30_stop_all_the_way_to_the_real_order(db):
    decision = _take_decision("LONG", "STANDARD", entry=100.0, t1=112.0, t2=120.0, t3=132.0)
    plan_fields = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-09-09", session_id="us_ny_futures",
        decision_dict=decision, anchor_time=ANCHOR, candles_24h=_flat_candles(price=100.0),
        r30_high=105.0, r30_low=95.0, f24_vah=110.0, f24_val=90.0, daily_atr14=2.0,
    )
    assert plan_fields["status"] == "WAITING"
    assert plan_fields["tier"] == "STANDARD"
    # box=|120-100|=20; r30_stop = 95 - 0.12*20 = 92.6 -- this is what a
    # real trade tomorrow, gated STANDARD, would actually use as its stop.
    assert plan_fields["stop_price"] == pytest.approx(92.6)

    # Simulate the real fill (the FILLED transition sets fill_price=trigger).
    plan_fields["status"] = "FILLED"
    plan_fields["fill_price"] = 100.0
    plan_fields["direction"] = "LONG"
    row = _persist_trade_plan(db, plan_fields, "BTC/USDT", "2026-09-09", "us_ny_futures")
    assert row.stop_price == pytest.approx(92.6)  # survived the real DB round-trip

    account = _real_account(db)
    risk_state = ea.get_or_init_risk_state(db, account)
    order = asyncio.run(epb.build_hypothetical_order(db, row, account, risk_state))

    assert order["decision"] == "WOULD_PLACE"
    assert order["stop_price"] == pytest.approx(92.6)
    # $100 risk / (100-92.6) stop distance = 13.5135 qty -- the exact real
    # position size a live account would compute against THIS stop.
    assert order["qty"] == pytest.approx(100.0 / 7.4, rel=1e-4)
    assert order["stop_distance"] == pytest.approx(7.4)


def test_premium_trade_end_to_end_still_uses_the_zone_stop_all_the_way_to_the_real_order(db):
    decision = _take_decision("LONG", "PREMIUM", entry=100.0, t1=112.0, t2=120.0, t3=132.0)
    plan_fields = tp.build_trade_plan(
        symbol="BTC/USDT", date_key="2026-09-09", session_id="us_ny_futures",
        decision_dict=decision, anchor_time=ANCHOR, candles_24h=_flat_candles(price=100.0),
        r30_high=105.0, r30_low=95.0, f24_vah=110.0, f24_val=90.0, daily_atr14=2.0,
    )
    assert plan_fields["status"] == "WAITING"
    assert plan_fields["tier"] == "PREMIUM"
    # The flat candles' own sweep-wick candidate at 99 wins (nearer than
    # r30_low=95) -- zone stop = 99 - 0.125*2.0 = 98.75, NOT the STANDARD
    # test's 92.6 above, on the IDENTICAL r30/candle inputs -- this is the
    # actual, direct proof the two tiers diverge correctly end to end.
    assert plan_fields["stop_price"] == pytest.approx(98.75)
    assert plan_fields["stop_price"] != pytest.approx(92.6)

    plan_fields["status"] = "FILLED"
    plan_fields["fill_price"] = 100.0
    plan_fields["direction"] = "LONG"
    row = _persist_trade_plan(db, plan_fields, "BTC/USDT", "2026-09-09", "us_ny_futures")
    assert row.stop_price == pytest.approx(98.75)

    account = _real_account(db)
    risk_state = ea.get_or_init_risk_state(db, account)
    order = asyncio.run(epb.build_hypothetical_order(db, row, account, risk_state))

    assert order["decision"] == "WOULD_PLACE"
    assert order["stop_price"] == pytest.approx(98.75)
    # $100 risk / (100-98.75) stop distance = 80.0 qty -- a MUCH smaller
    # stop distance than STANDARD's, so a correspondingly larger qty for
    # the same dollar risk -- confirms the sizing math genuinely reacts
    # differently per tier, not just the stop_price field in isolation.
    assert order["qty"] == pytest.approx(100.0 / 1.25, rel=1e-4)
    assert order["stop_distance"] == pytest.approx(1.25)
