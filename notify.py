# notify.py
# ==============================================================================
# KABRODA ADMIN EMAIL NOTIFICATIONS
# Plain SMTP sender (stdlib smtplib — no new dependency). Used for 4H/1H
# candidate open/close alerts AND every trade-plan lock/armed/vetoed/done
# email (trade_plan_notify.py) so admins/traders don't have to babysit
# the radar.
#
# Recipient(s): the UNION of two sources, deduped, as of 2026-09-23:
# 1) SMTP_DEST env var, comma-separated for multiple real people
#    (2026-09-06 fix -- see _parse_recipients() below for the real
#    incident this closes: Andy set SMTP_DEST to "andy@x.com,grossmonkey@
#    y.com" expecting both to receive it, but the old code passed the
#    whole raw string as ONE list entry to smtplib.sendmail(), which most
#    SMTP servers reject outright as a single malformed address -- meaning
#    NEITHER address reliably received mail, not just Gross Monkey's).
# 2) EmailSubscriber rows (database.py), added via the admin page's Email
#    Distribution List section -- lets Andy add/remove real people without
#    touching Render's env config. Purely additive: SMTP_DEST keeps
#    working with zero change; this is a second source merged in, not a
#    replacement. Queried fresh on every send (a short-lived self-opened
#    DB session, same pattern gravity_engine.py/battlebox_pipeline.py
#    already use for their own DB access) so an admin's add/remove takes
#    effect on the very next email, no restart needed. A DB read failure
#    here (e.g. table genuinely unreachable) never blocks the send --
#    falls back to SMTP_DEST alone, logged, not raised.
#
# Non-blocking by design: every caller wraps send_admin_email() in its own
# try/except already (gravity_engine, ledger_closing_engine), matching the
# pattern used everywhere else in this system (audit/monitor writes, macro
# engine subprocess launch). A failed send is logged and never raised further.
# ==============================================================================
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from typing import List

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_DEST = os.getenv("SMTP_DEST", "")


def _parse_recipients(raw: str) -> List[str]:
    """Splits a comma-separated SMTP_DEST into a real list of trimmed,
    non-empty addresses -- e.g. "andy@x.com, grossmonkey@y.com" ->
    ["andy@x.com", "grossmonkey@y.com"]. A single address with no comma
    still returns a clean 1-item list, so this is safe to always call,
    not just when multiple recipients are expected."""
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


def _db_subscriber_recipients() -> List[str]:
    """Active EmailSubscriber addresses from the DB, lowercased and
    trimmed. Self-contained -- opens and closes its own short-lived
    session, matching the codebase's own established pattern for a
    non-DB module needing occasional DB access. Never raises: any failure
    (table not yet migrated on a brand-new boot, DB briefly unreachable)
    returns an empty list so the env-var recipients above still get the
    email regardless."""
    try:
        from database import SessionLocal, EmailSubscriber
        db = SessionLocal()
        try:
            rows = db.query(EmailSubscriber).filter(EmailSubscriber.is_active == True).all()
            return [r.email.strip().lower() for r in rows if r.email and r.email.strip()]
        finally:
            db.close()
    except Exception as e:
        print(f"[NOTIFY] EmailSubscriber lookup failed, continuing with SMTP_DEST only: {e}")
        return []


def send_admin_email(subject: str, body: str) -> bool:
    """
    Sends a plain-text email to the union of SMTP_DEST's addresses and
    every active EmailSubscriber row, deduped, via STARTTLS. Returns
    True on success, False on any failure (missing config, connection
    error, auth error). Never raises — callers should not need their own
    try/except, but the pattern is safe to double-wrap if a caller
    already does.
    """
    env_recipients = _parse_recipients(SMTP_DEST)
    db_recipients = _db_subscriber_recipients()
    # Case-insensitive dedupe, first-seen order preserved -- order has no
    # real effect on delivery, this just avoids the same address getting
    # two envelope entries if it's in both SMTP_DEST and EmailSubscriber.
    seen = set()
    recipients = []
    for addr in env_recipients + db_recipients:
        key = addr.lower()
        if key not in seen:
            seen.add(key)
            recipients.append(addr)
    if not (SMTP_USER and SMTP_PASS and recipients):
        print(f"[NOTIFY] Skipped — SMTP_USER/SMTP_PASS not configured, or no recipients (SMTP_DEST + EmailSubscriber both empty).")
        return False
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = ", ".join(recipients)  # display header only -- the real envelope recipients are the list below

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, recipients, msg.as_string())
        print(f"[NOTIFY] Sent to {len(recipients)} recipient(s): {subject}")
        return True
    except Exception as e:
        print(f"[NOTIFY ERROR] Failed to send '{subject}': {e}")
        return False
