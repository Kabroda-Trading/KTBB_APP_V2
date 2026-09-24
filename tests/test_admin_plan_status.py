"""
Regression coverage for /api/admin/traveler-plan-status --
CC_WORK_ORDER_RADAR_PLAN_PANEL.md work item 2. TestClient-based, same
style as tests/test_executor_admin_routes.py.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_admin_plan_status.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

import sys
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import datetime as dt

import pytest
from fastapi.testclient import TestClient

import database
from database import SessionLocal, UserModel, TravelerPlan, ExecutorAccount, ExecutorOrder
import auth
import executor_accounts as ea
from main import app


def _clean_db_files():
    for path in ["kabroda_test_admin_plan_status.db", "kabroda_test_admin_plan_status.db-journal",
                 "kabroda_test_admin_plan_status.db-shm", "kabroda_test_admin_plan_status.db-wal"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@pytest.fixture
def env():
    _clean_db_files()
    database.init_db()
    db = SessionLocal()
    db.query(TravelerPlan).delete()
    db.query(UserModel).filter(UserModel.email.in_([
        "radar_admin@kabroda.com", "radar_nonadmin@kabroda.com",
    ])).delete(synchronize_session=False)
    db.commit()

    db.add(UserModel(email="radar_admin@kabroda.com", password_hash=auth.hash_password("adminpass123"),
                      username="radaradmin", tier="admin", is_admin=True, subscription_status="active"))
    db.add(UserModel(email="radar_nonadmin@kabroda.com", password_hash=auth.hash_password("plainpass123"),
                      username="radarplain", tier="basic", is_admin=False, subscription_status="active"))
    db.commit()

    yield {"db": db}

    db.close()
    database.engine.dispose()
    _clean_db_files()


def _login(email, password):
    client = TestClient(app)
    client.post("/login", data={"email": email, "password": password})
    return client


def _make_plan(db, **kwargs):
    defaults = dict(
        symbol="BTC/USDT", date_key="2026-09-18", session_id="us_ny_futures",
        status="WAITING_TOUCH", direction="LONG",
        breakout_trigger=81414.10, breakdown_trigger=80000.0,
        stop_price=81100.0, t1_price=82000.0,
        cross_time=dt.datetime(2026, 9, 18, 14, 5, 0), cross_price=81414.10,
        journey_cap_at=dt.datetime(2026, 9, 25, 14, 5, 0),
        last_transition_reason="LONG cross confirmed at 81,414.10 -- resting limit at 81,414.10, watching for a trigger touch",
    )
    defaults.update(kwargs)
    row = TravelerPlan(**defaults)
    db.add(row)
    db.commit()
    return row


def _make_account(db, user_id, mode="DRY_RUN", gate_profile=None, is_active=True):
    account = ea.create_account(db, user_id=user_id, label="radar_test_acct")
    account.mode = mode
    account.gate_profile = gate_profile
    account.is_active = is_active
    db.commit()
    return account


def _make_order(db, traveler_plan_id, account_id, mode="DRY_RUN", **kwargs):
    defaults = dict(
        # trade_plan_id is NOT NULL on this table even for a traveler-only
        # order -- reusing traveler_plan_id as the value, same convention
        # tests/test_executor_live_e1_engine.py's own _order_row() helper
        # already uses for this exact reason.
        trade_plan_id=traveler_plan_id,
        traveler_plan_id=traveler_plan_id, account_id=account_id, mode=mode,
        symbol="BTC/USDT", direction="LONG", entry_price=81414.10, stop_price=81100.0,
        t1_price=82000.0, qty=0.01, risk_dollars_used=100.0,
        decision="WOULD_PLACE", management_state="ENTRY_FILLED_ORDERS_PLACED",
        gate_profile_used="GATE_TRAVELER", mgmt_profile_used="MGMT_E1_STACK",
    )
    defaults.update(kwargs)
    row = ExecutorOrder(**defaults)
    db.add(row)
    db.commit()
    return row


def test_traveler_plan_status_requires_admin(env):
    client = _login("radar_nonadmin@kabroda.com", "plainpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    assert resp.status_code == 403


def test_traveler_plan_status_empty_when_no_rows(env):
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["rows"] == []


def test_traveler_plan_status_returns_real_fields(env):
    _make_plan(env["db"])
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert row["status"] == "WAITING_TOUCH"
    assert row["direction"] == "LONG"
    assert row["breakout_trigger"] == 81414.10
    assert row["stop_price"] == 81100.0
    assert row["t1_price"] == 82000.0
    assert row["cross_price"] == 81414.10
    assert row["fill_time"] is None
    assert row["fill_price"] is None
    assert row["journey_cap_at"] is not None
    assert "seconds_since_update" in row
    assert row["any_account_live"] is False  # no accounts exist at all yet


def test_traveler_plan_status_surfaces_a_multi_day_active_journey(env):
    # The real case this endpoint exists to handle correctly: a journey
    # that crossed on an EARLIER date_key and is still active today --
    # NOT scoped to today's date_key (unlike TradePlan's own endpoint),
    # since traveler_plan_engine.py's own design allows a WAITING_TOUCH
    # journey to span up to 7 days.
    _make_plan(env["db"], date_key="2026-09-15", status="WAITING_TOUCH")
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    assert row["date_key"] == "2026-09-15"
    assert row["status"] == "WAITING_TOUCH"


def test_traveler_plan_status_returns_the_most_recent_row_only(env):
    _make_plan(env["db"], date_key="2026-09-15", status="DONE", last_transition_reason="older, concluded journey")
    newer = _make_plan(env["db"], date_key="2026-09-18", status="WAITING_TOUCH", last_transition_reason="newer, active journey")
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    body = resp.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["id"] == newer.id
    assert body["rows"][0]["last_transition_reason"] == "newer, active journey"


def test_traveler_plan_status_shows_fill_fields_when_filled(env):
    _make_plan(
        env["db"], status="FILLED",
        fill_time=dt.datetime(2026, 9, 18, 15, 0, 0), fill_price=81400.0,
        last_transition_reason="trigger touch fill at 81,400.00",
    )
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    assert row["status"] == "FILLED"
    assert row["fill_price"] == 81400.0
    assert row["fill_time"] is not None
    # cross_price is audit-only and DIFFERENT from fill_price on a real
    # journey -- confirms the endpoint never conflates the two fields the
    # way v2's trigger_price/fill_price collapse to the same value.
    assert row["cross_price"] == 81414.10


# 2026-09-22 audit Finding 2 follow-up -- Andy's own question ("will the
# radar mislead once we go live?") surfaced that the panel's header
# hardcoded "DRY_RUN" with no real check behind it. `any_account_live` is
# a genuine, freshly-queried read of ExecutorAccount state, not a fixed
# label -- these tests prove it actually discriminates on mode, profile,
# AND is_active, not just "does any account exist."

def test_traveler_plan_status_any_account_live_false_with_no_accounts(env):
    _make_plan(env["db"])
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    assert row["any_account_live"] is False


def test_traveler_plan_status_any_account_live_true_for_live_traveler_account(env):
    _make_plan(env["db"])
    admin_user = env["db"].query(UserModel).filter_by(email="radar_admin@kabroda.com").first()
    _make_account(env["db"], admin_user.id, mode="LIVE", gate_profile="GATE_TRAVELER")
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    assert row["any_account_live"] is True


def test_traveler_plan_status_any_account_live_false_for_live_v2_account(env):
    # A LIVE account running the OTHER, now-retired profile must NOT
    # false-positive the traveler panel's LIVE badge -- proves the
    # profile filter is doing real work, not just the mode filter.
    # 2026-09-24 (V2 Crown retirement, Step 3f-iv): gate_profile=None no
    # longer means "the other profile" -- executor_accounts.
    # gate_profile_of()'s own default flipped from GATE_V2 to
    # GATE_TRAVELER (the only profile left), and the route's own filter
    # was fixed in the same pass to treat NULL as GATE_TRAVELER too (a
    # real gap that fix uncovered). The remaining "other profile" this
    # test can still exercise is an account whose raw column literally
    # holds the old GATE_V2 string from before that retirement (no
    # migration framework in this repo -- old rows are never rewritten).
    _make_plan(env["db"])
    admin_user = env["db"].query(UserModel).filter_by(email="radar_admin@kabroda.com").first()
    _make_account(env["db"], admin_user.id, mode="LIVE", gate_profile="GATE_V2")
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    assert row["any_account_live"] is False


def test_traveler_plan_status_any_account_live_true_for_a_never_configured_live_account(env):
    # The real gap 2026-09-24's fix (see the test above) closed: a
    # brand-new account that's never had its profile explicitly set
    # (gate_profile still NULL) IS a real GATE_TRAVELER account by
    # gate_profile_of()'s own resolution -- it must count as live too,
    # not just an account with the string explicitly set.
    _make_plan(env["db"])
    admin_user = env["db"].query(UserModel).filter_by(email="radar_admin@kabroda.com").first()
    _make_account(env["db"], admin_user.id, mode="LIVE", gate_profile=None)
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    assert row["any_account_live"] is True


def test_traveler_plan_status_any_account_live_false_for_dry_run_traveler_account(env):
    # A GATE_TRAVELER account that exists but is still DRY_RUN must not
    # light up the LIVE badge -- proves the mode filter, not just the
    # profile filter, is doing real work.
    _make_plan(env["db"])
    admin_user = env["db"].query(UserModel).filter_by(email="radar_admin@kabroda.com").first()
    _make_account(env["db"], admin_user.id, mode="DRY_RUN", gate_profile="GATE_TRAVELER")
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    assert row["any_account_live"] is False


def test_traveler_plan_status_any_account_live_false_for_inactive_live_traveler_account(env):
    # A LIVE GATE_TRAVELER account that's been deactivated must not still
    # light up the badge.
    _make_plan(env["db"])
    admin_user = env["db"].query(UserModel).filter_by(email="radar_admin@kabroda.com").first()
    _make_account(env["db"], admin_user.id, mode="LIVE", gate_profile="GATE_TRAVELER", is_active=False)
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    assert row["any_account_live"] is False


# 2026-09-23 -- the D3 management detail this route now also surfaces
# (Andy's own ask, radar-rebuild-around-Traveler-communication work).
# `mgmt_*` fields are sourced from ONE ExecutorOrder linked to the current
# journey, preferring a LIVE-mode order over a DRY_RUN one -- these tests
# specifically prove that preference, not just that the fields populate at
# all, since a naive "last inserted" pick was the real risk a validation
# pass flagged before this shipped.

def test_traveler_plan_status_mgmt_fields_none_when_no_order_exists(env):
    _make_plan(env["db"])
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    for field in ("mgmt_mode", "mgmt_state", "mgmt_entry_fill_price", "mgmt_entry_fill_time",
                  "mgmt_exit_reason", "mgmt_exit_price", "mgmt_exit_time", "mgmt_realized_pnl_r",
                  "mgmt_c5_fired", "mgmt_bbwp_fired"):
        assert row[field] is None
    assert row["mgmt_exit_approximated"] is False


def test_traveler_plan_status_mgmt_fields_populate_for_a_dry_run_only_journey(env):
    plan = _make_plan(env["db"])
    admin_user = env["db"].query(UserModel).filter_by(email="radar_admin@kabroda.com").first()
    account = _make_account(env["db"], admin_user.id, mode="DRY_RUN", gate_profile="GATE_TRAVELER")
    _make_order(
        env["db"], plan.id, account.id, mode="DRY_RUN",
        management_state="CLOSED_STOP", exit_reason="STOP", exit_price=81100.0,
        exit_time=dt.datetime(2026, 9, 18, 15, 0, 0), realized_pnl_r=-1.0,
        c5_fired=False, bbwp_fired=False,
    )
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    assert row["mgmt_mode"] == "DRY_RUN"
    assert row["mgmt_state"] == "CLOSED_STOP"
    assert row["mgmt_exit_reason"] == "STOP"
    assert row["mgmt_exit_price"] == 81100.0
    assert row["mgmt_realized_pnl_r"] == -1.0
    # DRY_RUN is never approximated, even for a reason that WOULD be
    # approximated on LIVE -- confirmed with a C5_EXIT case below too.
    assert row["mgmt_exit_approximated"] is False


def test_traveler_plan_status_mgmt_exit_approximated_true_only_for_live_contingency_exits(env):
    plan = _make_plan(env["db"])
    admin_user = env["db"].query(UserModel).filter_by(email="radar_admin@kabroda.com").first()
    account = _make_account(env["db"], admin_user.id, mode="LIVE", gate_profile="GATE_TRAVELER")
    _make_order(
        env["db"], plan.id, account.id, mode="LIVE",
        management_state="CLOSED_C5_EXIT", exit_reason="C5_EXIT", exit_price=81900.0,
        realized_pnl_r=0.85, c5_fired=True, bbwp_fired=False,
    )
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    assert row["mgmt_mode"] == "LIVE"
    assert row["mgmt_exit_approximated"] is True


def test_traveler_plan_status_mgmt_exit_approximated_false_for_live_stop_and_t1(env):
    plan = _make_plan(env["db"])
    admin_user = env["db"].query(UserModel).filter_by(email="radar_admin@kabroda.com").first()
    account = _make_account(env["db"], admin_user.id, mode="LIVE", gate_profile="GATE_TRAVELER")
    for reason in ("STOP", "T1"):
        env["db"].query(ExecutorOrder).delete()
        env["db"].commit()
        _make_order(
            env["db"], plan.id, account.id, mode="LIVE",
            management_state=f"CLOSED_{reason}", exit_reason=reason, exit_price=82000.0,
            realized_pnl_r=1.0,
        )
        client = _login("radar_admin@kabroda.com", "adminpass123")
        resp = client.get("/api/admin/traveler-plan-status")
        row = resp.json()["rows"][0]
        assert row["mgmt_exit_approximated"] is False, f"reason={reason} should not be approximated"


def test_traveler_plan_status_mgmt_prefers_live_order_over_dry_run_order(env):
    """The real risk a validation pass flagged before this shipped:
    ExecutorOrder's unique constraint is (traveler_plan_id, account_id),
    not just traveler_plan_id -- multiple accounts can each have their own
    order against the same journey by design. A blind order_by(id.desc())
    could silently flip between accounts; this proves the route always
    prefers the LIVE row regardless of insertion order."""
    plan = _make_plan(env["db"])
    admin_user = env["db"].query(UserModel).filter_by(email="radar_admin@kabroda.com").first()
    dry_account = _make_account(env["db"], admin_user.id, mode="DRY_RUN", gate_profile="GATE_TRAVELER")
    live_account = _make_account(env["db"], admin_user.id, mode="LIVE", gate_profile="GATE_TRAVELER")

    # DRY_RUN order inserted SECOND (higher id) -- if the route ever
    # regresses to a blind id-desc pick, this is exactly the case that
    # would silently flip to the wrong (DRY_RUN) account.
    _make_order(
        env["db"], plan.id, live_account.id, mode="LIVE",
        management_state="CLOSED_T1", exit_reason="T1", exit_price=82000.0, realized_pnl_r=1.0,
    )
    _make_order(
        env["db"], plan.id, dry_account.id, mode="DRY_RUN",
        management_state="CLOSED_STOP", exit_reason="STOP", exit_price=81100.0, realized_pnl_r=-1.0,
    )

    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    assert row["mgmt_mode"] == "LIVE"
    assert row["mgmt_exit_reason"] == "T1"
    assert row["mgmt_realized_pnl_r"] == 1.0


def test_traveler_plan_status_mgmt_falls_back_to_most_recent_dry_run_when_no_live_order(env):
    plan = _make_plan(env["db"])
    admin_user = env["db"].query(UserModel).filter_by(email="radar_admin@kabroda.com").first()
    account = _make_account(env["db"], admin_user.id, mode="DRY_RUN", gate_profile="GATE_TRAVELER")
    _make_order(
        env["db"], plan.id, account.id, mode="DRY_RUN",
        management_state="ENTRY_FILLED_ORDERS_PLACED",
    )
    client = _login("radar_admin@kabroda.com", "adminpass123")
    resp = client.get("/api/admin/traveler-plan-status")
    row = resp.json()["rows"][0]
    assert row["mgmt_mode"] == "DRY_RUN"
    assert row["mgmt_state"] == "ENTRY_FILLED_ORDERS_PLACED"
    assert row["mgmt_exit_reason"] is None  # still open, no exit yet
