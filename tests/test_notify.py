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

# Captured before any fixture/test can monkeypatch notify._db_subscriber_
# recipients (the `no_db_subscribers_by_default` autouse fixture below
# reassigns that module attribute for every test) -- this reference
# always points at the real, unpatched implementation, since monkeypatch
# reassigns the module's attribute rather than mutating this function
# object. The direct-unit tests for _db_subscriber_recipients() itself
# call this, not notify._db_subscriber_recipients, so they actually
# exercise the real code instead of the autouse fixture's stub.
_real_db_subscriber_recipients = notify._db_subscriber_recipients


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


@pytest.fixture(autouse=True)
def no_db_subscribers_by_default(monkeypatch):
    """2026-09-23: send_admin_email() now also merges in active
    EmailSubscriber rows from the DB (see notify.py's own header). Every
    pre-existing test above/below this fixture is testing SMTP_DEST's own
    behavior specifically -- forcing the DB side to an empty list by
    default keeps their original intent exactly unchanged and makes them
    independent of whatever happens to be in the ambient test DB, rather
    than relying on it silently being empty. Tests that actually want to
    exercise the DB-merge path override this explicitly, after this
    fixture runs, via their own monkeypatch.setattr call -- see the
    dedicated section below."""
    monkeypatch.setattr(notify, "_db_subscriber_recipients", lambda: [])


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


# ------------------------------------------------------------------ _db_subscriber_recipients / DB-merge (2026-09-23)
# The `no_db_subscribers_by_default` autouse fixture above forces the DB
# side to [] for every test in this file unless a test explicitly
# overrides it (as the tests below do) -- so these are the only tests in
# this file actually exercising the merge with the DB side non-empty.

def test_send_admin_email_merges_smtp_dest_and_db_subscribers(smtp_env, monkeypatch):
    monkeypatch.setattr(notify, "SMTP_DEST", "andy@x.com")
    monkeypatch.setattr(notify, "_db_subscriber_recipients", lambda: ["dawson@y.com"])
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)
    with patch("smtplib.SMTP", return_value=mock_server):
        ok = notify.send_admin_email("subject", "body")
    assert ok is True
    to_addrs = mock_server.sendmail.call_args[0][1]
    assert to_addrs == ["andy@x.com", "dawson@y.com"]


def test_send_admin_email_dedupes_case_insensitively_across_both_sources(smtp_env, monkeypatch):
    # Same address, different case, once in SMTP_DEST and once as a DB
    # subscriber -- must appear exactly once in the real envelope list,
    # not twice (which some SMTP servers would reject or double-send).
    monkeypatch.setattr(notify, "SMTP_DEST", "Andy@X.com")
    monkeypatch.setattr(notify, "_db_subscriber_recipients", lambda: ["andy@x.com", "dawson@y.com"])
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)
    with patch("smtplib.SMTP", return_value=mock_server):
        notify.send_admin_email("subject", "body")
    to_addrs = mock_server.sendmail.call_args[0][1]
    assert to_addrs == ["Andy@X.com", "dawson@y.com"]
    assert len(to_addrs) == 2


def test_send_admin_email_works_with_only_db_subscribers_no_smtp_dest(smtp_env, monkeypatch):
    # A real new capability, not just additive: before 2026-09-23, an
    # empty SMTP_DEST always meant "skip, no recipients." Now a DB-only
    # subscriber list is sufficient on its own.
    monkeypatch.setattr(notify, "SMTP_DEST", "")
    monkeypatch.setattr(notify, "_db_subscriber_recipients", lambda: ["dawson@y.com"])
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)
    with patch("smtplib.SMTP", return_value=mock_server):
        ok = notify.send_admin_email("subject", "body")
    assert ok is True
    to_addrs = mock_server.sendmail.call_args[0][1]
    assert to_addrs == ["dawson@y.com"]


def test_send_admin_email_skips_when_both_sources_empty(smtp_env, monkeypatch):
    monkeypatch.setattr(notify, "SMTP_DEST", "")
    monkeypatch.setattr(notify, "_db_subscriber_recipients", lambda: [])
    with patch("smtplib.SMTP") as mock_smtp_cls:
        ok = notify.send_admin_email("subject", "body")
    assert ok is False
    mock_smtp_cls.assert_not_called()


class _FakeSubscriberRow:
    def __init__(self, email):
        self.email = email


def test_db_subscriber_recipients_lowercases_and_trims(monkeypatch):
    fake_rows = [_FakeSubscriberRow(" Dawson@Y.com "), _FakeSubscriberRow("nick@z.com")]

    class _FakeQuery:
        def filter(self, *a, **kw):
            return self
        def all(self):
            return fake_rows

    class _FakeSession:
        def query(self, model):
            return _FakeQuery()
        def close(self):
            pass

    monkeypatch.setattr("database.SessionLocal", lambda: _FakeSession())
    assert _real_db_subscriber_recipients() == ["dawson@y.com", "nick@z.com"]


def test_db_subscriber_recipients_returns_empty_list_on_any_failure_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("DB unreachable")
    monkeypatch.setattr("database.SessionLocal", lambda: _boom())
    # Must not raise -- callers (send_admin_email) depend on this always
    # degrading gracefully to "no DB recipients," never blocking a send.
    assert _real_db_subscriber_recipients() == []


def test_db_subscriber_recipients_real_db_returns_only_active_rows():
    """Integration-style check against a REAL sqlite DB and REAL
    EmailSubscriber rows (not mocked) -- proves the actual is_active
    filter in the real query, not just the mocked-session plumbing above."""
    import database
    database.init_db()
    db = database.SessionLocal()
    try:
        db.query(database.EmailSubscriber).filter(
            database.EmailSubscriber.email.in_(["active@test.com", "inactive@test.com"])
        ).delete(synchronize_session=False)
        db.commit()
        db.add(database.EmailSubscriber(email="active@test.com", is_active=True))
        db.add(database.EmailSubscriber(email="inactive@test.com", is_active=False))
        db.commit()

        result = _real_db_subscriber_recipients()

        assert "active@test.com" in result
        assert "inactive@test.com" not in result
    finally:
        db.query(database.EmailSubscriber).filter(
            database.EmailSubscriber.email.in_(["active@test.com", "inactive@test.com"])
        ).delete(synchronize_session=False)
        db.commit()
        db.close()
