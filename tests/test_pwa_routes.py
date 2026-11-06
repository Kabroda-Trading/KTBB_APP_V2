"""Regression coverage for the PWA build's static assets: GET /sw.js
(served from root, not /static/sw.js, so its scope covers the whole site),
GET /static/manifest.json, and the two generated icon files."""
import os

os.environ["DATABASE_URL"] = "sqlite:///./kabroda_test_pwa_routes.db"
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "a@b.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")

import sys
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("yfinance", MagicMock())

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from main import app


def test_service_worker_served_from_root():
    client = TestClient(app)
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert b"self.addEventListener" in resp.content


def test_manifest_served_with_the_real_icons_and_name():
    client = TestClient(app)
    resp = client.get("/static/manifest.json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["short_name"] == "Kabroda"
    assert body["display"] == "standalone"
    sizes = {icon["sizes"] for icon in body["icons"]}
    assert sizes == {"192x192", "512x512"}


def test_icon_192_is_a_real_png():
    client = TestClient(app)
    resp = client.get("/static/icons/icon-192.png")
    assert resp.status_code == 200
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"   # real PNG magic bytes, not an empty/placeholder file


def test_icon_512_is_a_real_png():
    client = TestClient(app)
    resp = client.get("/static/icons/icon-512.png")
    assert resp.status_code == 200
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"
