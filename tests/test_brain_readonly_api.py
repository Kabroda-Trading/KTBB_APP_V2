"""
GET /api/brain/v1/* -- the read-only API the Kabroda AI Brain uses to audit the live site
(Brain repo kabroda_org/80_engineering/SITE_BRIDGE.md, flows up item 5; Andy 2026-11-15).
Guarantees under test: key required and fail-closed; GET only; never writes; no credentials,
secrets or people's emails in any response.
"""
import os
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_brain_readonly_api.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import datetime
import json

import pytest
from fastapi.testclient import TestClient

import brain_readonly_api as bra
import database
from database import (CMEConfig, CMEOrder, CMEPlan, ExecutorAccount, ExecutorAuditLog, ExecutorGlobalConfig,
                      SessionLocal)
from main import app

KEY = "test-brain-key"
H = {"X-API-Key": KEY}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv(bra.ENV_KEY, KEY)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc1234")
    database.init_db()
    db = SessionLocal()
    for m in (ExecutorAuditLog, CMEOrder, CMEPlan, CMEConfig, ExecutorGlobalConfig, ExecutorAccount):
        db.query(m).delete()
    db.commit()
    acct = ExecutorAccount(user_id=7, label="andy_cme", exchange="tradovate", mode="LIVE",
                           api_key_encrypted="ENC-KEY-SHOULD-NOT-LEAK", api_secret_encrypted="ENC-SECRET-SHOULD-NOT-LEAK",
                           credential_set_by="owner@example.com", kill_switch_engaged=True,
                           kill_switch_engaged_by="owner@example.com", kill_switch_reason="test")
    db.add(acct)
    db.add(ExecutorGlobalConfig(config_key="executor_global", global_kill_switch_engaged=True,
                                global_kill_switch_engaged_by="owner@example.com", live_orders_enabled=True))
    db.commit()
    plan = CMEPlan(symbol="MNQ", date_key="2026-11-16", lock_time=datetime.datetime(2026, 11, 16, 15, 0), status="ARMED")
    db.add(plan)
    db.commit()
    db.add(CMEOrder(cme_plan_id=plan.id, account_id=acct.id, mode="LIVE", symbol="MNQ", direction="LONG",
                    decision="WOULD_PLACE", management_state="PENDING_ENTRY"))
    db.add(CMEConfig(account_id=acct.id))
    db.add(ExecutorAuditLog(account_id=acct.id, event_type="DATA_FIX", actor="system", message="fixed one row"))
    db.add(ExecutorAuditLog(account_id=acct.id, event_type="ERROR", actor="system", message="an error"))
    db.commit()
    yield {"db": db, "client": TestClient(app), "account_id": acct.id}
    db.close()


ENDPOINTS = ["/status", "/accounts", "/cme/configs", "/cme/plans", "/cme/orders", "/audit"]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_requires_the_key(env, path):
    c = env["client"]
    assert c.get(f"/api/brain/v1{path}").status_code == 401
    assert c.get(f"/api/brain/v1{path}", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get(f"/api/brain/v1{path}", headers=H).status_code == 200


@pytest.mark.parametrize("path", ENDPOINTS)
def test_fails_closed_when_env_key_unset(env, monkeypatch, path):
    monkeypatch.delenv(bra.ENV_KEY, raising=False)
    assert env["client"].get(f"/api/brain/v1{path}", headers=H).status_code == 401
    monkeypatch.setenv(bra.ENV_KEY, "")
    assert env["client"].get(f"/api/brain/v1{path}", headers={"X-API-Key": ""}).status_code == 401


def test_router_is_get_only():
    for route in bra.router.routes:
        assert route.methods == {"GET"}, f"{route.path} allows {route.methods}"


@pytest.mark.parametrize("path", ENDPOINTS)
def test_write_methods_are_rejected(env, path):
    c = env["client"]
    for method in ("post", "put", "patch", "delete"):
        assert getattr(c, method)(f"/api/brain/v1{path}", headers=H).status_code in (404, 405)


@pytest.mark.parametrize("path", ENDPOINTS)
def test_no_secrets_or_emails_in_any_response(env, path):
    body = env["client"].get(f"/api/brain/v1{path}", headers=H).text
    for leak in ("ENC-KEY-SHOULD-NOT-LEAK", "ENC-SECRET-SHOULD-NOT-LEAK", "owner@example.com",
                 "api_key_encrypted", "api_secret_encrypted", "credential_set_by", "_engaged_by"):
        assert leak not in body, f"{path} leaked {leak}"


def test_status_reports_switches_and_commit(env):
    d = env["client"].get("/api/brain/v1/status", headers=H).json()
    assert d["deployed_commit"] == "abc1234"
    assert d["global_config_row_present"] is True
    assert d["global_config"]["global_kill_switch_engaged"] is True
    assert d["global_config"]["live_orders_enabled"] is True
    assert d["accounts_live"] == 1


def test_status_never_creates_the_config_row(env):
    db = env["db"]
    db.query(ExecutorGlobalConfig).delete()
    db.commit()
    d = env["client"].get("/api/brain/v1/status", headers=H).json()
    assert d["global_config_row_present"] is False and d["global_config"] is None
    db.expire_all()
    assert db.query(ExecutorGlobalConfig).count() == 0


def test_reads_do_not_change_row_counts(env):
    db, c = env["db"], env["client"]
    models = (ExecutorAccount, ExecutorGlobalConfig, CMEPlan, CMEOrder, CMEConfig, ExecutorAuditLog)
    before = [db.query(m).count() for m in models]
    for p in ENDPOINTS:
        c.get(f"/api/brain/v1{p}", headers=H)
    db.expire_all()
    assert [db.query(m).count() for m in models] == before


def test_audit_filters_and_limit(env):
    c = env["client"]
    rows = c.get("/api/brain/v1/audit?event_type=DATA_FIX", headers=H).json()["audit"]
    assert [r["event_type"] for r in rows] == ["DATA_FIX"]
    assert len(c.get("/api/brain/v1/audit?limit=1", headers=H).json()["audit"]) == 1
    assert c.get("/api/brain/v1/audit?since=2000-01-01", headers=H).json()["audit"]
    assert c.get("/api/brain/v1/audit?since=2999-01-01", headers=H).json()["audit"] == []


def test_cme_orders_and_plans(env):
    c = env["client"]
    orders = c.get(f"/api/brain/v1/cme/orders?account_id={env['account_id']}", headers=H).json()["cme_orders"]
    assert len(orders) == 1 and orders[0]["management_state"] == "PENDING_ENTRY"
    plans = c.get("/api/brain/v1/cme/plans?since=2026-11-16", headers=H).json()["cme_plans"]
    assert len(plans) == 1 and plans[0]["status"] == "ARMED"
    assert c.get("/api/brain/v1/cme/plans?since=2026-11-17", headers=H).json()["cme_plans"] == []


def test_limit_is_capped():
    assert bra._limit(10**9) == bra.MAX_LIMIT and bra._limit(0) == 1 and bra._limit(None) == bra.DEFAULT_LIMIT


# ---------------------------------------------------------------- market history (2026-11-18)
class _FakeClient:
    def __init__(self, bars=None, err=None):
        self.bars, self.err, self.calls = bars or [], err, []

    async def get_market_bars(self, instrument, **kw):
        self.calls.append((instrument, kw))
        if self.err:
            raise self.err
        return self.bars


def _bar(minute):
    t = datetime.datetime(2026, 11, 16, 15, minute, tzinfo=datetime.timezone.utc)
    return {"time": t, "timestamp": int(t.timestamp()), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10,
            "token": "SHOULD-NOT-LEAK"}


def test_market_bars_needs_the_key(env):
    c = TestClient(app)
    assert c.get("/api/brain/v1/market/bars", params={"instrument": "MGC 12-26"}).status_code == 401


def test_market_bars_validates_inputs(env, monkeypatch):
    import cme_market
    monkeypatch.setattr(cme_market, "_get_crosstrade_client", lambda: _FakeClient())
    c = TestClient(app)
    assert c.get("/api/brain/v1/market/bars", params={"instrument": "MGC"}, headers=H).status_code == 400
    assert c.get("/api/brain/v1/market/bars", params={"instrument": "MGC 12-26; drop"}, headers=H).status_code == 400
    assert c.get("/api/brain/v1/market/bars", params={"instrument": "MGC 12-26", "period_type": "tick"},
                 headers=H).status_code == 400


def test_market_bars_without_a_client_is_503(env, monkeypatch):
    import cme_market
    monkeypatch.setattr(cme_market, "_get_crosstrade_client", lambda: None)
    r = TestClient(app).get("/api/brain/v1/market/bars", params={"instrument": "MGC 12-26"}, headers=H)
    assert r.status_code == 503


def test_market_bars_happy_path_caps_and_never_logs_or_leaks(env, monkeypatch):
    import cme_market
    fake = _FakeClient(bars=[_bar(0), _bar(1)])
    monkeypatch.setattr(cme_market, "_get_crosstrade_client", lambda: fake)
    r = TestClient(app).get("/api/brain/v1/market/bars", headers=H,
                            params={"instrument": "MGC 12-26", "period": 1, "days_back": 99999, "limit": 10 ** 7})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2 and body["first"].startswith("2026-11-16T15:00") and body["last"].startswith("2026-11-16T15:01")
    inst, kw = fake.calls[0]
    assert inst == "MGC 12-26" and kw["log_raw"] is False and kw["period_type"] == "Minute"
    assert kw["days_back"] == bra.MAX_DAYS_BACK and kw["limit"] == bra.MAX_BARS
    assert "SHOULD-NOT-LEAK" not in r.text and set(body["bars"][0]) == {"time", "open", "high", "low", "close", "volume"}


def test_market_bars_provider_error_is_502(env, monkeypatch):
    import cme_market
    monkeypatch.setattr(cme_market, "_get_crosstrade_client", lambda: _FakeClient(err=RuntimeError("CrossTrade market bars query failed (429): slow down")))
    r = TestClient(app).get("/api/brain/v1/market/bars", params={"instrument": "MGC 12-26"}, headers=H)
    assert r.status_code == 502 and "429" in r.json()["error"]


def test_market_bars_route_is_get_only():
    routes = [rt for rt in bra.router.routes if rt.path.endswith("/market/bars")]
    assert len(routes) == 1 and routes[0].methods == {"GET"}
