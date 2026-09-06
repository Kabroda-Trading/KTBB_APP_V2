# notify.py
# ==============================================================================
# KABRODA ADMIN EMAIL NOTIFICATIONS
# Plain SMTP sender (stdlib smtplib — no new dependency). Used for 4H/1H
# candidate open/close alerts AND every trade-plan lock/armed/vetoed/done
# email (trade_plan_notify.py) so admins/traders don't have to babysit
# the radar.
#
# Recipient(s): SMTP_DEST env var, comma-separated for multiple real
# people (2026-09-06 fix -- see _parse_recipients() below for the real
# incident this closes: Andy set SMTP_DEST to "andy@x.com,grossmonkey@
# y.com" expecting both to receive it, but the old code passed the
# whole raw string as ONE list entry to smtplib.sendmail(), which most
# SMTP servers reject outright as a single malformed address -- meaning
# NEITHER address reliably received mail, not just Gross Monkey's).
# Still one shared destination list, not a per-user notification-
# preference system — anyone who needs these emails goes in this one
# env var.
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


def send_admin_email(subject: str, body: str) -> bool:
    """
    Sends a plain-text email to every address in SMTP_DEST (comma-
    separated) via STARTTLS. Returns True on success, False on any
    failure (missing config, connection error, auth error). Never
    raises — callers should not need their own try/except, but the
    pattern is safe to double-wrap if a caller already does.
    """
    recipients = _parse_recipients(SMTP_DEST)
    if not (SMTP_USER and SMTP_PASS and recipients):
        print(f"[NOTIFY] Skipped — SMTP_USER/SMTP_PASS/SMTP_DEST not fully configured.")
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
