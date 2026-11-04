import os
import sys
from unittest.mock import MagicMock

# Mock anthropic and yfinance to prevent ModuleNotFoundError when importing main
sys.modules["anthropic"] = MagicMock()
sys.modules["yfinance"] = MagicMock()

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_fixes.db"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient
from database import (
    init_db,
    SessionLocal,
    engine,
    UserModel,
    TravelerPlan,
    ExecutorOrder,
)
import auth
import executor_accounts as ea
from main import app
from datetime import datetime, timezone, timedelta

def clean_db_files():
    for path in ["kabroda_test_fixes.db", "kabroda_test_fixes.db-journal", "kabroda_test_fixes.db-shm", "kabroda_test_fixes.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

@pytest.fixture(scope="module", autouse=True)
def setup_test_db():
    # 2026-09-23 rebuild (V2 Crown retirement): this fixture used to seed
    # CampaignLog rows -- rewritten to seed TravelerPlan/ExecutorOrder,
    # matching what /api/dashboard/overview and /api/dashboard/mas-history
    # actually read now. See main.py's own docstrings on those routes for
    # the full "why" of the rebuild. (/api/dashboard/accuracy itself was
    # removed 2026-09-24 -- see below.)
    clean_db_files()
    init_db()

    db = SessionLocal()

    admin_user = UserModel(
        email="admin_fix@kabroda.com",
        password_hash=auth.hash_password("adminpass123"),
        username="adminfix",
        tier="admin",
        is_admin=True,
        subscription_status="active"
    )
    db.add(admin_user)

    basic_user = UserModel(
        email="basic_fix@kabroda.com",
        password_hash=auth.hash_password("basicpass123"),
        username="basicfix",
        tier="basic",
        is_admin=False,
        subscription_status="active"
    )
    db.add(basic_user)
    db.commit()

    account = ea.create_account(db, user_id=basic_user.id, label="dashboard_fix_test_acct")
    db.commit()

    # p1/p2/p3: FILLED journeys, each with a resolved ExecutorOrder --
    # win_rate/net_r math mirrors the old test's expected 66.7%/1.0 exactly
    # (2 wins of 3 resolved: +1.5, -1.0, +0.5 = net +1.0), just sourced
    # from the Traveler now instead of the retired CampaignLog.
    p1 = TravelerPlan(symbol="BTC/USDT", date_key="2026-09-20", session_id="us_ny_futures",
                       status="FILLED", direction="LONG",
                       breakout_trigger=90000.0, breakdown_trigger=89000.0,
                       stop_price=89000.0, t1_price=91000.0,
                       fill_time=datetime.utcnow() - timedelta(days=2), fill_price=90000.0)
    p2 = TravelerPlan(symbol="BTC/USDT", date_key="2026-09-21", session_id="us_ny_futures",
                       status="FILLED", direction="SHORT",
                       breakout_trigger=92000.0, breakdown_trigger=91000.0,
                       stop_price=92000.0, t1_price=90000.0,
                       fill_time=datetime.utcnow() - timedelta(days=1), fill_price=91000.0)
    p3 = TravelerPlan(symbol="BTC/USDT", date_key="2026-09-22", session_id="us_ny_futures",
                       status="FILLED", direction="LONG",
                       breakout_trigger=90500.0, breakdown_trigger=89500.0,
                       stop_price=89500.0, t1_price=92000.0,
                       fill_time=datetime.utcnow(), fill_price=90500.0)
    # p4: still open, no resolved order yet -- counts toward total_sessions
    # and status_counts, but must NOT affect win_rate/net_r/pnl_series.
    p4 = TravelerPlan(symbol="BTC/USDT", date_key="2026-09-23", session_id="us_ny_futures",
                       status="WAITING_CROSS", breakout_trigger=93000.0, breakdown_trigger=92000.0)
    db.add_all([p1, p2, p3, p4])
    db.commit()

    def _order(plan, exit_reason, exit_price, realized_pnl_r, exit_time):
        return ExecutorOrder(
            trade_plan_id=plan.id, traveler_plan_id=plan.id, account_id=account.id, mode="DRY_RUN",
            symbol="BTC/USDT", direction=plan.direction, entry_price=plan.fill_price, stop_price=plan.stop_price,
            t1_price=plan.t1_price, qty=0.01, risk_dollars_used=100.0,
            decision="WOULD_PLACE", management_state=f"CLOSED_{exit_reason}",
            gate_profile_used="GATE_TRAVELER", mgmt_profile_used="MGMT_E1_STACK",
            exit_reason=exit_reason, exit_price=exit_price, exit_time=exit_time,
            realized_pnl_r=realized_pnl_r,
        )

    db.add_all([
        _order(p1, "T1", 91000.0, 1.5, datetime.utcnow() - timedelta(days=2)),
        _order(p2, "STOP", 92000.0, -1.0, datetime.utcnow() - timedelta(days=1)),
        _order(p3, "T1", 92000.0, 0.5, datetime.utcnow()),
    ])

    # AgentRunLog (r1/r2) and DecisionJournal (dj1/dj2) fixture rows removed
    # 2026-09-24 -- they only backed test_api_dashboard_accuracy/
    # test_api_dashboard_costs_admin/test_api_dashboard_costs_basic_forbidden,
    # all removed in the same pass along with the routes they tested
    # (/api/dashboard/accuracy, /api/dashboard/costs -- both had gone
    # permanently dead, see main.py's own removal notes).

    db.commit()
    db.close()

    yield

    engine.dispose()
    clean_db_files()

@pytest.fixture
def admin_client():
    client = TestClient(app)
    client.post("/login", data={"email": "admin_fix@kabroda.com", "password": "adminpass123"})
    return client

@pytest.fixture
def basic_client():
    client = TestClient(app)
    client.post("/login", data={"email": "basic_fix@kabroda.com", "password": "basicpass123"})
    return client

def test_api_dashboard_overview(basic_client):
    response = basic_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["total_sessions"] == 4
    assert data["fill_rate"] == 75.0   # 3 of 4 TravelerPlan rows reached FILLED
    assert data["win_rate"] == 66.7    # 2 of 3 resolved orders positive
    assert data["net_r"] == 1.0        # +1.5 - 1.0 + 0.5

# test_api_dashboard_accuracy/test_api_dashboard_costs_admin/
# test_api_dashboard_costs_basic_forbidden removed 2026-09-24 -- tested
# /api/dashboard/accuracy and /api/dashboard/costs, both removed from
# main.py in the same pass (confluence_accuracy had gone permanently
# frozen once DecisionJournal's writer was deleted in 3e; costs had been
# reading $0.00 forever, AgentRunLog's only writer had zero live callers
# since 2026-08-17). See main.py's own removal notes on each route.

def test_api_dashboard_mas_history(basic_client):
    response = basic_client.get("/api/dashboard/mas-history")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert len(data["pnl_series"]) == 3   # only the 3 resolved orders, not p4
    assert data["status_counts"]["FILLED"] == 3
    assert data["status_counts"]["WAITING_CROSS"] == 1
    trade_dates = {t["date_key"] for t in data["trades"]}
    assert "2026-09-23" in trade_dates   # p4 (unresolved) is still listed
    p4_row = next(t for t in data["trades"] if t["date_key"] == "2026-09-23")
    assert p4_row["exit_reason"] is None
    assert p4_row["realized_pnl"] is None


def test_api_dashboard_mas_history_prefers_live_order_over_dry_run(basic_client):
    """The one genuinely new piece of logic in this route's rebuild: a
    single batch query resolves each TravelerPlan's mgmt detail from
    potentially several linked ExecutorOrder rows (one per account,
    ExecutorOrder's real unique constraint is (traveler_plan_id,
    account_id)), preferring LIVE over DRY_RUN -- same rule traveler_
    radar.py/the admin traveler-plan-status route already use. The DRY_RUN
    order is inserted SECOND (higher id) specifically to catch a
    regression to a naive 'last seen wins' pick, which would silently
    flip to the wrong (DRY_RUN) account."""
    db = SessionLocal()
    basic = db.query(UserModel).filter_by(email="basic_fix@kabroda.com").first()
    dry_account = ea.create_account(db, user_id=basic.id, label="mas_history_dry")
    live_account = ea.create_account(db, user_id=basic.id, label="mas_history_live")
    db.commit()

    plan = TravelerPlan(symbol="BTC/USDT", date_key="2026-09-24", session_id="us_ny_futures",
                         status="FILLED", direction="LONG",
                         breakout_trigger=94000.0, breakdown_trigger=93000.0,
                         stop_price=93000.0, t1_price=95000.0, fill_price=94000.0)
    db.add(plan)
    db.commit()

    live_order = ExecutorOrder(
        trade_plan_id=plan.id, traveler_plan_id=plan.id, account_id=live_account.id, mode="LIVE",
        symbol="BTC/USDT", direction="LONG", entry_price=94000.0, stop_price=93000.0, t1_price=95000.0,
        qty=0.01, risk_dollars_used=100.0, decision="WOULD_PLACE", management_state="CLOSED_T1",
        gate_profile_used="GATE_TRAVELER", mgmt_profile_used="MGMT_E1_STACK",
        exit_reason="T1", exit_price=95000.0, realized_pnl_r=1.0,
    )
    db.add(live_order)
    db.commit()
    dry_order = ExecutorOrder(
        trade_plan_id=plan.id, traveler_plan_id=plan.id, account_id=dry_account.id, mode="DRY_RUN",
        symbol="BTC/USDT", direction="LONG", entry_price=94000.0, stop_price=93000.0, t1_price=95000.0,
        qty=0.01, risk_dollars_used=100.0, decision="WOULD_PLACE", management_state="CLOSED_STOP",
        gate_profile_used="GATE_TRAVELER", mgmt_profile_used="MGMT_E1_STACK",
        exit_reason="STOP", exit_price=93000.0, realized_pnl_r=-1.0,
    )
    db.add(dry_order)   # inserted after live_order -- higher id
    db.commit()
    db.close()

    response = basic_client.get("/api/dashboard/mas-history")
    data = response.json()
    row = next(t for t in data["trades"] if t["date_key"] == "2026-09-24")
    assert row["exit_reason"] == "T1"
    assert row["realized_pnl"] == "+1.0000R"

# test_api_dashboard_jewel removed 2026-08-30 -- tested /api/dashboard/jewel,
# already removed from main.py (JewelSnapshotLog's only writer is archived).


# ------------------------------------------------------------------ P0 fix (2026-11-04): per-user scoping
#
# Direct regression test for the confirmed bug (AGENT_LOG 2026-11-01 16:30
# CT, independently re-verified against the real route code before fixing,
# not taken on the report's word): neither route filtered ExecutorOrder by
# account_id/user_id, so a logged-in user's win_rate/net_r/pnl_series mixed
# in every other user's real-money P&L. A SEPARATE, unrelated second user
# (own account, own plan, own order) is added here rather than into the
# module-scoped setup_test_db fixture above, specifically so it does NOT
# change total_sessions/fill_rate for the existing tests (those stay
# deliberately UNSCOPED -- shared gate activity, not money, see the
# route's own new comment).

@pytest.fixture
def other_user_with_own_order(basic_client):
    # Captures basic_fix's CURRENT numbers before other_fix's data exists --
    # deliberately not a hardcoded absolute value, since this module's
    # shared DB accumulates permanent rows from earlier tests in this same
    # file (e.g. test_api_dashboard_mas_history_prefers_live_order_over_
    # dry_run adds two more of basic_fix's own orders). Proving "identical
    # before and after other_fix's unrelated data exists" is the real
    # regression guard for the leak this fixes, and it stays correct
    # regardless of what ran earlier in this module.
    before = basic_client.get("/api/dashboard/overview").json()

    db = SessionLocal()
    other_user = UserModel(
        email="other_fix@kabroda.com", password_hash=auth.hash_password("otherpass123"),
        username="otherfix", tier="basic", is_admin=False, subscription_status="active",
    )
    db.add(other_user)
    db.commit()
    other_account = ea.create_account(db, user_id=other_user.id, label="other_fix_acct")
    db.commit()

    other_plan = TravelerPlan(symbol="BTC/USDT", date_key="2026-09-25", session_id="us_ny_futures",
                               status="FILLED", direction="LONG",
                               breakout_trigger=96000.0, breakdown_trigger=95000.0,
                               stop_price=95000.0, t1_price=98000.0, fill_price=96000.0)
    db.add(other_plan)
    db.commit()
    other_order = ExecutorOrder(
        trade_plan_id=other_plan.id, traveler_plan_id=other_plan.id, account_id=other_account.id, mode="LIVE",
        symbol="BTC/USDT", direction="LONG", entry_price=96000.0, stop_price=95000.0, t1_price=98000.0,
        qty=0.01, risk_dollars_used=100.0, decision="WOULD_PLACE", management_state="CLOSED_T1",
        gate_profile_used="GATE_TRAVELER", mgmt_profile_used="MGMT_E1_STACK",
        exit_reason="T1", exit_price=98000.0, realized_pnl_r=5.0,   # deliberately large, so a leak is obvious
    )
    db.add(other_order)
    db.commit()
    other_id = other_user.id
    other_account_id = other_account.id
    db.close()

    client = TestClient(app)
    client.post("/login", data={"email": "other_fix@kabroda.com", "password": "otherpass123"})
    yield {"client": client, "user_id": other_id, "basic_before": before}

    db = SessionLocal()
    db.query(ExecutorOrder).filter_by(account_id=other_account_id).delete()
    db.query(TravelerPlan).filter_by(date_key="2026-09-25").delete()
    db.query(UserModel).filter_by(email="other_fix@kabroda.com").delete()
    db.commit()
    db.close()


def test_overview_does_not_leak_another_users_pnl_into_a_basic_users_own_numbers(basic_client, admin_client, other_user_with_own_order):
    before = other_user_with_own_order["basic_before"]

    # basic_fix's own win_rate/net_r/executor_closed_trades must be
    # COMPLETELY UNCHANGED by other_fix's separate +5.0R order on a
    # different account -- this is the direct regression guard for the
    # confirmed leak (previously: these three summed across EVERY account
    # in the database with zero filtering).
    basic_resp = basic_client.get("/api/dashboard/overview").json()
    assert basic_resp["net_r"] == before["net_r"]
    assert basic_resp["win_rate"] == before["win_rate"]
    assert basic_resp["executor_closed_trades"] == before["executor_closed_trades"]

    # other_fix sees ONLY their own one order -- not basic_fix's any.
    other_resp = other_user_with_own_order["client"].get("/api/dashboard/overview").json()
    assert other_resp["net_r"] == 5.0
    assert other_resp["win_rate"] == 100.0
    assert other_resp["executor_closed_trades"] == 1

    # Admin sees the combined total across both real users -- the one view
    # where seeing everyone's numbers together is the intended behavior.
    admin_resp = admin_client.get("/api/dashboard/overview").json()
    assert admin_resp["executor_closed_trades"] == before["executor_closed_trades"] + 1
    assert admin_resp["net_r"] == pytest.approx(before["net_r"] + 5.0)

    # total_sessions/fill_rate are deliberately NOT user-scoped (shared
    # gate activity) -- adding other_fix's own plan DOES raise the shared
    # total for every viewer, including basic_fix, same as it should (this
    # is correct, not a leak -- the session/fill-rate KPIs describe the
    # gate's own activity, never any one user's money).
    assert basic_resp["total_sessions"] == before["total_sessions"] + 1
    assert other_resp["total_sessions"] == before["total_sessions"] + 1


def test_mas_history_does_not_leak_another_users_trade_detail(basic_client, other_user_with_own_order):
    # pnl_series length should match basic_fix's own resolved-order count,
    # captured by the fixture BEFORE other_fix's order existed.
    before_closed_trades = other_user_with_own_order["basic_before"]["executor_closed_trades"]

    basic_resp = basic_client.get("/api/dashboard/mas-history").json()
    basic_dates = {t["date_key"] for t in basic_resp["trades"]}
    assert "2026-09-25" in basic_dates   # the shared plan row is still listed (unscoped)...
    other_users_row = next(t for t in basic_resp["trades"] if t["date_key"] == "2026-09-25")
    assert other_users_row["exit_reason"] is None   # ...but its real trade DETAIL must not leak through
    assert other_users_row["realized_pnl"] is None
    assert len(basic_resp["pnl_series"]) == before_closed_trades   # only basic_fix's own resolved orders, unaffected by other_fix's

    other_resp = other_user_with_own_order["client"].get("/api/dashboard/mas-history").json()
    assert len(other_resp["pnl_series"]) == 1
    own_row = next(t for t in other_resp["trades"] if t["date_key"] == "2026-09-25")
    assert own_row["exit_reason"] == "T1"
    assert own_row["realized_pnl"] == "+5.0000R"
