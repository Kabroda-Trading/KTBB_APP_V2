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
import json
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


# ------------------------------------------------------------------ EmailSendLog (2026-09-27)
# Andy ruling 14:55 CT, TRAVELER_D1_D2_D3_SPEC.md ONE-TRUTH RULE: "every
# send lands in newsletter_log" -- the real audit finding was that
# send_admin_email() (every email in this codebase routes through it,
# confirmed by a full-repo grep) had ZERO database writes of any kind.
# Real DB, not mocked -- the whole point is proving the write actually
# lands, not just that the right function gets called.

@pytest.fixture(autouse=True)
def _clean_email_send_log():
    import database
    database.init_db()
    db = database.SessionLocal()
    db.query(database.EmailSendLog).delete()
    db.commit()
    db.close()
    yield
    db = database.SessionLocal()
    db.query(database.EmailSendLog).delete()
    db.commit()
    db.close()


def _log_rows():
    import database
    db = database.SessionLocal()
    try:
        rows = db.query(database.EmailSendLog).order_by(database.EmailSendLog.id).all()
        db.expunge_all()
        return rows
    finally:
        db.close()


def test_send_admin_email_logs_a_sent_row_on_success(smtp_env, monkeypatch):
    monkeypatch.setattr(notify, "SMTP_DEST", "andy@x.com")
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)
    with patch("smtplib.SMTP", return_value=mock_server):
        notify.send_admin_email("KABRODA test subject", "test body")
    rows = _log_rows()
    assert len(rows) == 1
    assert rows[0].subject == "KABRODA test subject"
    assert rows[0].body == "test body"
    assert rows[0].outcome == "SENT"
    assert rows[0].recipients == "andy@x.com"


def test_send_admin_email_logs_a_skipped_row_with_reason(smtp_env, monkeypatch):
    monkeypatch.setattr(notify, "SMTP_DEST", "")
    with patch("smtplib.SMTP") as mock_smtp_cls:
        notify.send_admin_email("subject", "body")
    mock_smtp_cls.assert_not_called()
    rows = _log_rows()
    assert len(rows) == 1
    assert rows[0].outcome == "SKIPPED"
    assert rows[0].detail   # a real reason string, not blank


def test_send_admin_email_logs_a_failed_row_with_the_exception_text(smtp_env, monkeypatch):
    monkeypatch.setattr(notify, "SMTP_DEST", "andy@x.com")
    with patch("smtplib.SMTP", side_effect=RuntimeError("connection refused")):
        notify.send_admin_email("subject", "body")
    rows = _log_rows()
    assert len(rows) == 1
    assert rows[0].outcome == "FAILED"
    assert "connection refused" in rows[0].detail


def test_send_admin_email_logging_failure_never_blocks_the_real_send(smtp_env, monkeypatch):
    # A logging bug must never take down the actual email send -- send_
    # admin_email() already returns before _log_send() can affect the
    # outcome, but confirm this explicitly: a broken DB session must not
    # raise back into send_admin_email() or flip its return value.
    monkeypatch.setattr(notify, "SMTP_DEST", "andy@x.com")
    monkeypatch.setattr("database.SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("DB down")))
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)
    with patch("smtplib.SMTP", return_value=mock_server):
        ok = notify.send_admin_email("subject", "body")
    assert ok is True   # the real send still succeeded and reported success


# ------------------------------------------------------------------ send_account_email (2026-09-28, Andy's L4 email-routing ruling)
# CC_INTERFACE.md L4: trade-execution emails (fills/opens/closes/cancels/
# errors/risk$) must route ONLY to the specific ExecutorAccount's real
# owner (executor_accounts.user_id -> users.email), never the merged
# SMTP_DEST + EmailSubscriber radar list send_admin_email() uses. Real
# DB rows (ExecutorAccount + UserModel), not mocked -- the whole point is
# proving the actual owner lookup works, not just that some address gets
# used.

@pytest.fixture
def account_and_owner():
    import database
    database.init_db()
    db = database.SessionLocal()
    db.query(database.ExecutorAccount).filter(database.ExecutorAccount.label == "test_l4_account").delete()
    db.query(database.UserModel).filter(database.UserModel.email == "owner@test.com").delete()
    db.commit()
    user = database.UserModel(email="owner@test.com", password_hash="x")
    db.add(user)
    db.flush()
    account = database.ExecutorAccount(user_id=user.id, label="test_l4_account")
    db.add(account)
    db.commit()
    account_id = account.id
    db.close()
    yield account_id
    db = database.SessionLocal()
    db.query(database.ExecutorAccount).filter(database.ExecutorAccount.id == account_id).delete()
    db.query(database.UserModel).filter(database.UserModel.email == "owner@test.com").delete()
    db.commit()
    db.close()


def test_send_account_email_delivers_only_to_the_account_owner_not_the_merged_list(smtp_env, monkeypatch, account_and_owner):
    # The core of the L4 fix: SMTP_DEST/EmailSubscriber recipients must
    # NOT receive this email, even though they would for send_admin_email().
    monkeypatch.setattr(notify, "SMTP_DEST", "andy@x.com")
    monkeypatch.setattr(notify, "_db_subscriber_recipients", lambda: ["dawson@y.com"])
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)
    with patch("smtplib.SMTP", return_value=mock_server):
        ok = notify.send_account_email("subject", "body", account_and_owner)
    assert ok is True
    to_addrs = mock_server.sendmail.call_args[0][1]
    assert to_addrs == ["owner@test.com"]
    assert "andy@x.com" not in to_addrs
    assert "dawson@y.com" not in to_addrs


def test_send_account_email_skips_and_does_not_fall_back_to_merged_list_for_unknown_account(smtp_env, monkeypatch):
    # An unresolvable account must NOT silently broadcast to the merged
    # list -- that would reintroduce the exact cross-account leak L4 exists
    # to close. It should simply not send.
    monkeypatch.setattr(notify, "SMTP_DEST", "andy@x.com")
    with patch("smtplib.SMTP") as mock_smtp_cls:
        ok = notify.send_account_email("subject", "body", 999999)
    assert ok is False
    mock_smtp_cls.assert_not_called()


def test_send_account_email_skips_when_account_exists_but_user_does_not(smtp_env, monkeypatch):
    import database
    database.init_db()
    db = database.SessionLocal()
    db.query(database.ExecutorAccount).filter(database.ExecutorAccount.label == "test_l4_orphan").delete()
    db.commit()
    account = database.ExecutorAccount(user_id=999999, label="test_l4_orphan")   # no matching UserModel row
    db.add(account)
    db.commit()
    account_id = account.id
    db.close()
    try:
        with patch("smtplib.SMTP") as mock_smtp_cls:
            ok = notify.send_account_email("subject", "body", account_id)
        assert ok is False
        mock_smtp_cls.assert_not_called()
    finally:
        db = database.SessionLocal()
        db.query(database.ExecutorAccount).filter(database.ExecutorAccount.id == account_id).delete()
        db.commit()
        db.close()


def test_send_account_email_returns_false_on_smtp_exception_never_raises(smtp_env, monkeypatch, account_and_owner):
    with patch("smtplib.SMTP", side_effect=RuntimeError("connection refused")):
        ok = notify.send_account_email("subject", "body", account_and_owner)
    assert ok is False


def test_send_account_email_logs_a_sent_row_to_only_the_owner(smtp_env, monkeypatch, account_and_owner):
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)
    with patch("smtplib.SMTP", return_value=mock_server):
        notify.send_account_email("KABRODA test subject", "test body", account_and_owner)
    rows = _log_rows()
    assert len(rows) == 1
    assert rows[0].outcome == "SENT"
    assert rows[0].recipients == "owner@test.com"


def test_send_account_email_logs_a_skipped_row_for_an_unresolvable_account(smtp_env, monkeypatch):
    with patch("smtplib.SMTP") as mock_smtp_cls:
        notify.send_account_email("subject", "body", 999999)
    mock_smtp_cls.assert_not_called()
    rows = _log_rows()
    assert len(rows) == 1
    assert rows[0].outcome == "SKIPPED"
    assert "999999" in rows[0].detail


# ------------------------------------------------------------------ Telegram push (2026-11-xx)
# Second delivery channel for the exact same per-account events send_
# account_email() above already covers. Mocks urllib.request.urlopen
# entirely, same "no real network call" discipline as the SMTP tests
# above -- no existing precedent for mocking urllib in this repo, written
# from scratch the same way test_send_admin_email's smtplib mocking was.

@pytest.fixture
def telegram_account_and_owner(monkeypatch):
    """Real ExecutorAccount + UserModel rows, same shape as
    account_and_owner above, but also cleans up any NotificationPreference
    row so each test starts from a known-empty state."""
    import database
    database.init_db()
    db = database.SessionLocal()
    db.query(database.ExecutorAccount).filter(database.ExecutorAccount.label == "test_telegram_account").delete()
    db.query(database.UserModel).filter(database.UserModel.email == "tg_owner@test.com").delete()
    db.commit()
    user = database.UserModel(email="tg_owner@test.com", password_hash="x", is_admin=False)
    db.add(user)
    db.flush()
    user_id = user.id
    account = database.ExecutorAccount(user_id=user_id, label="test_telegram_account")
    db.add(account)
    db.commit()
    account_id = account.id
    db.query(database.NotificationPreference).filter_by(user_id=user_id).delete()
    db.commit()
    db.close()
    yield {"account_id": account_id, "user_id": user_id}
    db = database.SessionLocal()
    db.query(database.ExecutorAccount).filter(database.ExecutorAccount.id == account_id).delete()
    db.query(database.UserModel).filter(database.UserModel.id == user_id).delete()
    db.query(database.NotificationPreference).filter_by(user_id=user_id).delete()
    db.commit()
    db.close()


def _set_pref(user_id, **fields):
    import database
    db = database.SessionLocal()
    pref = db.query(database.NotificationPreference).filter_by(user_id=user_id).first()
    if pref is None:
        pref = database.NotificationPreference(user_id=user_id)
        db.add(pref)
        db.flush()
    for k, v in fields.items():
        setattr(pref, k, v)
    db.commit()
    db.close()


class _FakeHttpResponse:
    def __init__(self, status=200):
        self.status = status
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_send_telegram_raw_skips_when_token_unset(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with patch("urllib.request.urlopen") as mock_urlopen:
        ok = notify._send_telegram_raw("12345", "hello")
    assert ok is False
    mock_urlopen.assert_not_called()


def test_send_telegram_raw_skips_when_no_chat_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    with patch("urllib.request.urlopen") as mock_urlopen:
        ok = notify._send_telegram_raw("", "hello")
    assert ok is False
    mock_urlopen.assert_not_called()


def test_send_telegram_raw_posts_to_the_real_bot_api_url(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    with patch("urllib.request.urlopen", return_value=_FakeHttpResponse(200)) as mock_urlopen:
        ok = notify._send_telegram_raw("12345", "hello world")
    assert ok is True
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "https://api.telegram.org/botfake-token/sendMessage"
    payload = json.loads(req.data.decode("utf-8"))
    assert payload == {"chat_id": "12345", "text": "hello world"}


def test_send_telegram_raw_returns_false_on_non_200(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    with patch("urllib.request.urlopen", return_value=_FakeHttpResponse(500)):
        ok = notify._send_telegram_raw("12345", "hello")
    assert ok is False


def test_send_telegram_raw_returns_false_on_exception_never_raises(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    with patch("urllib.request.urlopen", side_effect=RuntimeError("network down")):
        ok = notify._send_telegram_raw("12345", "hello")
    assert ok is False


def test_send_account_telegram_sends_when_chat_id_set_and_category_on(telegram_account_and_owner, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    _set_pref(telegram_account_and_owner["user_id"], telegram_chat_id="999", notify_fill=True)
    with patch("urllib.request.urlopen", return_value=_FakeHttpResponse(200)) as mock_urlopen:
        ok = notify.send_account_telegram("fill", "test message", telegram_account_and_owner["account_id"])
    assert ok is True
    mock_urlopen.assert_called_once()


def test_send_account_telegram_respects_the_category_toggle_off(telegram_account_and_owner, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    _set_pref(telegram_account_and_owner["user_id"], telegram_chat_id="999", notify_fill=False)
    with patch("urllib.request.urlopen") as mock_urlopen:
        ok = notify.send_account_telegram("fill", "test message", telegram_account_and_owner["account_id"])
    assert ok is False
    mock_urlopen.assert_not_called()


def test_send_account_telegram_problem_category_always_sends_even_if_other_toggles_off(telegram_account_and_owner, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    _set_pref(telegram_account_and_owner["user_id"], telegram_chat_id="999",
              notify_fill=False, notify_breakeven=False, notify_exit=False)
    with patch("urllib.request.urlopen", return_value=_FakeHttpResponse(200)) as mock_urlopen:
        ok = notify.send_account_telegram("problem", "unprotected position", telegram_account_and_owner["account_id"])
    assert ok is True
    mock_urlopen.assert_called_once()


def test_send_account_telegram_no_chat_id_and_not_admin_does_not_send(telegram_account_and_owner, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    # No NotificationPreference row at all for this non-admin user.
    with patch("urllib.request.urlopen") as mock_urlopen:
        ok = notify.send_account_telegram("fill", "test message", telegram_account_and_owner["account_id"])
    assert ok is False
    mock_urlopen.assert_not_called()


def test_send_account_telegram_admin_falls_back_to_env_var_chat_id(monkeypatch):
    """Andy's own already-configured Render setup: no NotificationPreference
    row yet, but he's the admin user -- TELEGRAM_CHAT_ID env var covers him
    with zero further action."""
    import database
    database.init_db()
    db = database.SessionLocal()
    db.query(database.ExecutorAccount).filter(database.ExecutorAccount.label == "test_telegram_admin_acct").delete()
    db.query(database.UserModel).filter(database.UserModel.email == "tg_admin@test.com").delete()
    db.commit()
    user = database.UserModel(email="tg_admin@test.com", password_hash="x", is_admin=True)
    db.add(user)
    db.flush()
    account = database.ExecutorAccount(user_id=user.id, label="test_telegram_admin_acct")
    db.add(account)
    db.commit()
    account_id = account.id
    user_id = user.id
    db.close()
    try:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "admin-chat-id")
        with patch("urllib.request.urlopen", return_value=_FakeHttpResponse(200)) as mock_urlopen:
            ok = notify.send_account_telegram("fill", "test message", account_id)
        assert ok is True
        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        assert payload["chat_id"] == "admin-chat-id"
    finally:
        db = database.SessionLocal()
        db.query(database.ExecutorAccount).filter(database.ExecutorAccount.id == account_id).delete()
        db.query(database.UserModel).filter(database.UserModel.id == user_id).delete()
        db.commit()
        db.close()


def test_send_account_telegram_unresolvable_account_returns_false(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    with patch("urllib.request.urlopen") as mock_urlopen:
        ok = notify.send_account_telegram("fill", "test message", 999999)
    assert ok is False
    mock_urlopen.assert_not_called()


# ------------------------------------------------------------------ Telegram message builders

def test_telegram_fill_text_includes_stop_when_given():
    text = notify._telegram_fill_text("CME", "MNQ", "LONG", 20450.5, stop=20405.0)
    assert text == "✅ CME: MNQ LONG filled @ 20450.5 (stop 20405)"


def test_telegram_fill_text_omits_stop_when_not_given():
    text = notify._telegram_fill_text("Traveler", "BTC/USDT", "LONG", 90000.0)
    assert "stop" not in text
    assert "90000" in text


def test_telegram_breakeven_text():
    text = notify._telegram_breakeven_text("CME", "MNQ", 20451.0)
    assert "MNQ" in text and "20451" in text and "breakeven" in text.lower()


def test_telegram_exit_text_with_both_r_and_usd():
    text = notify._telegram_exit_text("CME", "MNQ", "T1", r=1.0, usd=142.50)
    assert "+1.00R" in text
    assert "+$142.50" in text
    assert "T1" in text


def test_telegram_exit_text_negative_values_show_sign():
    text = notify._telegram_exit_text("Alt Matrix", "SOL/USDT", "STOP", r=-1.0, usd=-100.0)
    assert "-1.00R" in text
    assert "-$100.00" in text


def test_telegram_exit_text_with_neither_r_nor_usd():
    text = notify._telegram_exit_text("Traveler", "BTC/USDT", "TIME")
    assert text == "🏁 Traveler: BTC/USDT closed via TIME"


def test_telegram_problem_text():
    text = notify._telegram_problem_text("CME", "MNQ", "entry filled but stop placement failed")
    assert "MNQ" in text and "entry filled but stop placement failed" in text
