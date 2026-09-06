"""
Unit coverage for notify.py's send_admin_email()/_parse_recipients() --
mocks smtplib.SMTP entirely, no real network call, no existing precedent
for mocking SMTP elsewhere in this repo so this is written from scratch.

2026-09-06 real incident this covers: Andy set SMTP_DEST to a comma-
separated "andy@x.com,grossmonkey@y.com" expecting both to receive every
trade-plan email, but the old code passed the whole raw string as ONE
list entry to smtplib.sendmail() -- which most SMTP servers reject
outright as a single malformed address, meaning NEITHER address
reliably received mail. Fixed to parse SMTP_DEST into a real list.
"""
import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./kabroda_test_notify.db")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import notify


# ------------------------------------------------------------------ _parse_recipients

def test_parse_recipients_splits_comma_separated_list():
    assert notify._parse_recipients("andy@x.com,grossmonkey@y.com") == ["andy@x.com", "grossmonkey@y.com"]


def test_parse_recipients_trims_whitespace_around_each_address():
    assert notify._parse_recipients(" andy@x.com , grossmonkey@y.com ") == ["andy@x.com", "grossmonkey@y.com"]


def test_parse_recipients_single_address_no_comma():
    assert notify._parse_recipients("andy@x.com") == ["andy@x.com"]


def test_parse_recipients_empty_string_returns_empty_list():
    assert notify._parse_recipients("") == []


def test_parse_recipients_drops_empty_entries_from_stray_commas():
    assert notify._parse_recipients("andy@x.com,,grossmonkey@y.com,") == ["andy@x.com", "grossmonkey@y.com"]


# ------------------------------------------------------------------ send_admin_email

@pytest.fixture
def smtp_env(monkeypatch):
    monkeypatch.setattr(notify, "SMTP_USER", "bot@kabroda.com")
    monkeypatch.setattr(notify, "SMTP_PASS", "secret")
    monkeypatch.setattr(notify, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(notify, "SMTP_PORT", 587)


def test_send_admin_email_delivers_to_every_comma_separated_recipient(smtp_env, monkeypatch):
    monkeypatch.setattr(notify, "SMTP_DEST", "andy@x.com,grossmonkey@y.com")
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)
    with patch("smtplib.SMTP", return_value=mock_server) as mock_smtp_cls:
        ok = notify.send_admin_email("subject", "body")
    assert ok is True
    mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=15)
    mock_server.sendmail.assert_called_once()
    from_addr, to_addrs, _msg = mock_server.sendmail.call_args[0]
    assert from_addr == "bot@kabroda.com"
    # The real fix -- a REAL list of two separate addresses, never the
    # whole comma-joined string as a single list entry.
    assert to_addrs == ["andy@x.com", "grossmonkey@y.com"]


def test_send_admin_email_single_recipient_still_works(smtp_env, monkeypatch):
    monkeypatch.setattr(notify, "SMTP_DEST", "andy@x.com")
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)
    with patch("smtplib.SMTP", return_value=mock_server):
        ok = notify.send_admin_email("subject", "body")
    assert ok is True
    to_addrs = mock_server.sendmail.call_args[0][1]
    assert to_addrs == ["andy@x.com"]


def test_send_admin_email_skips_when_smtp_dest_unset(smtp_env, monkeypatch):
    monkeypatch.setattr(notify, "SMTP_DEST", "")
    with patch("smtplib.SMTP") as mock_smtp_cls:
        ok = notify.send_admin_email("subject", "body")
    assert ok is False
    mock_smtp_cls.assert_not_called()


def test_send_admin_email_skips_when_smtp_dest_is_only_whitespace_and_commas(smtp_env, monkeypatch):
    # _parse_recipients() must reduce this to an empty list, not a
    # 1-item list containing junk -- same skip path as fully unset.
    monkeypatch.setattr(notify, "SMTP_DEST", " , , ")
    with patch("smtplib.SMTP") as mock_smtp_cls:
        ok = notify.send_admin_email("subject", "body")
    assert ok is False
    mock_smtp_cls.assert_not_called()


def test_send_admin_email_returns_false_on_smtp_exception_never_raises(smtp_env, monkeypatch):
    monkeypatch.setattr(notify, "SMTP_DEST", "andy@x.com")
    with patch("smtplib.SMTP", side_effect=RuntimeError("connection refused")):
        ok = notify.send_admin_email("subject", "body")
    assert ok is False


def test_send_admin_email_to_header_is_comma_joined_display_string(smtp_env, monkeypatch):
    monkeypatch.setattr(notify, "SMTP_DEST", "andy@x.com,grossmonkey@y.com")
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)
    with patch("smtplib.SMTP", return_value=mock_server):
        notify.send_admin_email("subject", "body")
    sent_msg_str = mock_server.sendmail.call_args[0][2]
    assert "andy@x.com, grossmonkey@y.com" in sent_msg_str
