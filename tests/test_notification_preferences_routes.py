"""Regression coverage for the Telegram push build's new per-user routes:
GET/POST /api/account/notification-preferences and POST /api/account/
telegram-test. Same TestClient/login style as tests/test_cme_routes.py.
No existing test file covered the pre-existing /account/* routes either
(profile/password/settings) -- this is new coverage, not a gap left by
this build specifically."""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_notification_prefs.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

import sys
from unittest.mock import MagicMock, patch

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient

import database
from database import SessionLocal, UserModel, NotificationPreference
import auth
from main import app


def _clean_db_files():
    for path in ["kabroda_test_notification_prefs.db", "kabroda_test_notification_prefs.db-journal",
                 "kabroda_test_notification_prefs.db-shm", "kabroda_test_notification_prefs.db-wal"]:
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
    db.query(NotificationPreference).delete()
    db.query(UserModel).filter(UserModel.email.in_([
        "np_admin@kabroda.com", "np_owner@kabroda.com",
    ])).delete(synchronize_session=False)
    db.commit()

    admin = UserModel(email="np_admin@kabroda.com", password_hash=auth.hash_password("adminpass123"),
                       username="npadmin", tier="admin", is_admin=True, subscription_status="active")
    owner = UserModel(email="np_owner@kabroda.com", password_hash=auth.hash_password("ownerpass123"),
                       username="npowner", tier="basic", is_admin=False, subscription_status="active")
    db.add(admin)
    db.add(owner)
    db.commit()
    admin_id, owner_id = admin.id, owner.id

    yield {"db": db, "admin_id": admin_id, "owner_id": owner_id}

    db.close()
    database.engine.dispose()
    _clean_db_files()


def _login(email, password):
    client = TestClient(app)
    client.post("/login", data={"email": email, "password": password})
    return client


# ------------------------------------------------------------------ GET/POST /api/account/notification-preferences

def test_get_preferences_requires_login(env):
    client = TestClient(app)
    resp = client.get("/api/account/notification-preferences")
    assert resp.status_code == 401


def test_get_preferences_returns_schema_defaults_with_no_row_yet(env):
    client = _login("np_owner@kabroda.com", "ownerpass123")
    resp = client.get("/api/account/notification-preferences")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["preferences"]["telegram_chat_id"] is None
    assert body["preferences"]["notify_fill"] is True
    assert body["preferences"]["notify_breakeven"] is True
    assert body["preferences"]["notify_exit"] is True
    assert body["preferences"]["notify_email_enabled"] is True


def test_post_preferences_can_turn_off_routine_email_independent_of_telegram(env):
    # 2026-11-12, Andy's own ask: one master email switch, independent of
    # the Telegram category toggles -- turning email off must not touch
    # notify_fill/breakeven/exit (those gate Telegram only).
    client = _login("np_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/account/notification-preferences", json={"notify_email_enabled": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["preferences"]["notify_email_enabled"] is False
    assert body["preferences"]["notify_fill"] is True
    assert body["preferences"]["notify_breakeven"] is True
    assert body["preferences"]["notify_exit"] is True

    db = env["db"]
    row = db.query(NotificationPreference).filter_by(user_id=env["owner_id"]).first()
    assert row.notify_email_enabled is False


def test_post_preferences_lazy_creates_the_row(env):
    client = _login("np_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/account/notification-preferences", json={"telegram_chat_id": "12345"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["preferences"]["telegram_chat_id"] == "12345"

    db = env["db"]
    row = db.query(NotificationPreference).filter_by(user_id=env["owner_id"]).first()
    assert row is not None
    assert row.telegram_chat_id == "12345"


def test_post_preferences_omitted_fields_leave_existing_values_unchanged(env):
    client = _login("np_owner@kabroda.com", "ownerpass123")
    client.post("/api/account/notification-preferences", json={"telegram_chat_id": "12345", "notify_fill": False})
    resp = client.post("/api/account/notification-preferences", json={"notify_exit": False})
    body = resp.json()
    # notify_exit changed, but telegram_chat_id and notify_fill (set earlier,
    # not in THIS request's body) must be untouched.
    assert body["preferences"]["telegram_chat_id"] == "12345"
    assert body["preferences"]["notify_fill"] is False
    assert body["preferences"]["notify_exit"] is False


def test_post_preferences_empty_chat_id_string_clears_it_to_none(env):
    client = _login("np_owner@kabroda.com", "ownerpass123")
    client.post("/api/account/notification-preferences", json={"telegram_chat_id": "12345"})
    resp = client.post("/api/account/notification-preferences", json={"telegram_chat_id": "  "})
    assert resp.json()["preferences"]["telegram_chat_id"] is None


def test_preferences_are_isolated_per_user(env):
    owner_client = _login("np_owner@kabroda.com", "ownerpass123")
    owner_client.post("/api/account/notification-preferences", json={"telegram_chat_id": "owner-chat"})

    admin_client = _login("np_admin@kabroda.com", "adminpass123")
    resp = admin_client.get("/api/account/notification-preferences")
    # The admin has no row of their own yet -- must see schema defaults,
    # never the owner's chat ID.
    assert resp.json()["preferences"]["telegram_chat_id"] is None


# ------------------------------------------------------------------ POST /api/account/telegram-test

def test_telegram_test_requires_login(env):
    client = TestClient(app)
    resp = client.post("/api/account/telegram-test")
    assert resp.status_code == 401


def test_telegram_test_400s_with_no_chat_id_configured(env):
    client = _login("np_owner@kabroda.com", "ownerpass123")
    resp = client.post("/api/account/telegram-test")
    assert resp.status_code == 400


def test_telegram_test_sends_a_real_message_to_the_saved_chat_id(env, monkeypatch):
    client = _login("np_owner@kabroda.com", "ownerpass123")
    client.post("/api/account/notification-preferences", json={"telegram_chat_id": "12345"})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")

    import notify

    class _FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    with patch("urllib.request.urlopen", return_value=_FakeResp()) as mock_urlopen:
        resp = client.post("/api/account/telegram-test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_urlopen.assert_called_once()


def test_telegram_test_502s_when_send_fails(env, monkeypatch):
    client = _login("np_owner@kabroda.com", "ownerpass123")
    client.post("/api/account/notification-preferences", json={"telegram_chat_id": "12345"})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    with patch("urllib.request.urlopen", side_effect=RuntimeError("network down")):
        resp = client.post("/api/account/telegram-test")
    assert resp.status_code == 502


def test_telegram_test_admin_falls_back_to_env_var_chat_id(env, monkeypatch):
    client = _login("np_admin@kabroda.com", "adminpass123")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "admin-env-chat")

    class _FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    with patch("urllib.request.urlopen", return_value=_FakeResp()) as mock_urlopen:
        resp = client.post("/api/account/telegram-test")
    assert resp.status_code == 200
    req = mock_urlopen.call_args[0][0]
    import json as _json
    assert _json.loads(req.data.decode("utf-8"))["chat_id"] == "admin-env-chat"
