"""
Regression coverage for the Email Distribution List admin routes --
GET /admin (email_subscribers context), POST /admin/add-email-subscriber,
POST /admin/delete-email-subscriber. 2026-09-23, part of the strategic
site audit's radar-rebuild-around-Traveler-communication work -- see
notify.py's own header for how these rows actually get read at send time.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_email_subscribers.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

import sys
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient

import database
from database import SessionLocal, UserModel, EmailSubscriber
import auth
from main import app


def _clean_db_files():
    for path in ["kabroda_test_email_subscribers.db", "kabroda_test_email_subscribers.db-journal",
                 "kabroda_test_email_subscribers.db-shm", "kabroda_test_email_subscribers.db-wal"]:
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
    db.query(EmailSubscriber).delete()
    db.query(UserModel).filter(UserModel.email.in_([
        "sub_admin@kabroda.com", "sub_nonadmin@kabroda.com",
    ])).delete(synchronize_session=False)
    db.commit()

    db.add(UserModel(email="sub_admin@kabroda.com", password_hash=auth.hash_password("adminpass123"),
                      username="subadmin", tier="admin", is_admin=True, subscription_status="active"))
    db.add(UserModel(email="sub_nonadmin@kabroda.com", password_hash=auth.hash_password("plainpass123"),
                      username="subplain", tier="basic", is_admin=False, subscription_status="active"))
    db.commit()

    yield {"db": db}

    db.close()
    database.engine.dispose()
    _clean_db_files()


def _login(email, password):
    client = TestClient(app)
    client.post("/login", data={"email": email, "password": password})
    return client


# ------------------------------------------------------------------ GET /admin renders the list

def test_admin_page_includes_email_subscribers_section(env):
    client = _login("sub_admin@kabroda.com", "adminpass123")
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "Email Distribution List" in resp.text


def test_admin_page_renders_existing_subscriber_rows(env):
    env["db"].add(EmailSubscriber(email="dawson@y.com", label="Dawson", is_active=True))
    env["db"].commit()
    client = _login("sub_admin@kabroda.com", "adminpass123")
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "dawson@y.com" in resp.text
    assert "Dawson" in resp.text


def test_admin_page_requires_admin(env):
    client = _login("sub_nonadmin@kabroda.com", "plainpass123")
    resp = client.get("/admin", follow_redirects=False)
    # admin_roster_page() returns RedirectResponse("/suite") with no
    # explicit status_code -- FastAPI's own default is 307.
    assert resp.status_code == 307


# ------------------------------------------------------------------ POST /admin/add-email-subscriber

def test_add_email_subscriber_happy_path(env):
    client = _login("sub_admin@kabroda.com", "adminpass123")
    resp = client.post("/admin/add-email-subscriber", json={"email": "Dawson@Y.com", "label": "Dawson"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    row = env["db"].query(EmailSubscriber).filter(EmailSubscriber.email == "dawson@y.com").first()
    assert row is not None
    assert row.label == "Dawson"
    assert row.is_active is True
    assert row.added_by == "sub_admin@kabroda.com"


def test_add_email_subscriber_requires_admin(env):
    client = _login("sub_nonadmin@kabroda.com", "plainpass123")
    resp = client.post("/admin/add-email-subscriber", json={"email": "dawson@y.com"})
    assert resp.status_code == 200  # this route replies 200 with ok:false, matching admin_create_user's own pattern
    assert resp.json()["ok"] is False


def test_add_email_subscriber_rejects_missing_at_sign(env):
    client = _login("sub_admin@kabroda.com", "adminpass123")
    resp = client.post("/admin/add-email-subscriber", json={"email": "not-an-email"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "email" in body["error"].lower()


def test_add_email_subscriber_rejects_empty_email(env):
    client = _login("sub_admin@kabroda.com", "adminpass123")
    resp = client.post("/admin/add-email-subscriber", json={"email": "  "})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_add_email_subscriber_rejects_duplicate_active_address(env):
    env["db"].add(EmailSubscriber(email="dawson@y.com", is_active=True))
    env["db"].commit()
    client = _login("sub_admin@kabroda.com", "adminpass123")
    resp = client.post("/admin/add-email-subscriber", json={"email": "dawson@y.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "already" in body["error"].lower()
    # Confirms no duplicate row was created either.
    assert env["db"].query(EmailSubscriber).filter(EmailSubscriber.email == "dawson@y.com").count() == 1


def test_add_email_subscriber_reactivates_a_previously_removed_row(env):
    # Re-adding an address that was soft-disabled (is_active=False) must
    # flip the SAME row back on, not raise a unique-constraint error on a
    # fresh insert -- the unique index is on `email`.
    existing = EmailSubscriber(email="dawson@y.com", is_active=False, label="Old Label")
    env["db"].add(existing)
    env["db"].commit()
    old_id = existing.id

    client = _login("sub_admin@kabroda.com", "adminpass123")
    resp = client.post("/admin/add-email-subscriber", json={"email": "dawson@y.com", "label": "New Label"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    env["db"].expire_all()
    rows = env["db"].query(EmailSubscriber).filter(EmailSubscriber.email == "dawson@y.com").all()
    assert len(rows) == 1
    assert rows[0].id == old_id
    assert rows[0].is_active is True
    assert rows[0].label == "New Label"


def test_add_email_subscriber_normalizes_case_and_whitespace(env):
    client = _login("sub_admin@kabroda.com", "adminpass123")
    resp = client.post("/admin/add-email-subscriber", json={"email": "  Dawson@Y.COM  "})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert env["db"].query(EmailSubscriber).filter(EmailSubscriber.email == "dawson@y.com").first() is not None


# ------------------------------------------------------------------ POST /admin/delete-email-subscriber

def test_delete_email_subscriber_happy_path(env):
    sub = EmailSubscriber(email="dawson@y.com", is_active=True)
    env["db"].add(sub)
    env["db"].commit()
    sub_id = sub.id

    client = _login("sub_admin@kabroda.com", "adminpass123")
    resp = client.post("/admin/delete-email-subscriber", json={"subscriber_id": sub_id})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert env["db"].query(EmailSubscriber).filter(EmailSubscriber.id == sub_id).first() is None


def test_delete_email_subscriber_requires_admin(env):
    sub = EmailSubscriber(email="dawson@y.com", is_active=True)
    env["db"].add(sub)
    env["db"].commit()
    sub_id = sub.id

    client = _login("sub_nonadmin@kabroda.com", "plainpass123")
    resp = client.post("/admin/delete-email-subscriber", json={"subscriber_id": sub_id})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    # Confirms nothing was actually deleted despite the attempt.
    assert env["db"].query(EmailSubscriber).filter(EmailSubscriber.id == sub_id).first() is not None


def test_delete_email_subscriber_missing_id_returns_error(env):
    client = _login("sub_admin@kabroda.com", "adminpass123")
    resp = client.post("/admin/delete-email-subscriber", json={"subscriber_id": 999999})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "not found" in body["error"].lower()


def test_delete_email_subscriber_non_numeric_id_returns_clean_json_error(env):
    # A non-numeric subscriber_id must return a clean JSON error, not
    # raise into the generic catch-all HTML 500 handler (which the
    # admin.html JS's `await res.json()` would then throw on).
    client = _login("sub_admin@kabroda.com", "adminpass123")
    resp = client.post("/admin/delete-email-subscriber", json={"subscriber_id": "not-a-number"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "number" in body["error"].lower()
