# notify.py
# ==============================================================================
# KABRODA ADMIN EMAIL NOTIFICATIONS
# Plain SMTP sender (stdlib smtplib — no new dependency). Used for 4H/1H
# candidate open/close alerts so admins don't have to babysit the radar.
#
# Recipient: SMTP_DEST env var. Already provisioned in Render for exactly this
# purpose (a designated system-alert destination) — no separate per-user
# notification-preference system is built on top of it.
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

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_DEST = os.getenv("SMTP_DEST", "")


def send_admin_email(subject: str, body: str) -> bool:
    """
    Sends a plain-text email to SMTP_DEST via STARTTLS. Returns True on
    success, False on any failure (missing config, connection error, auth
    error). Never raises — callers should not need their own try/except,
    but the pattern is safe to double-wrap if a caller already does.
    """
    if not (SMTP_USER and SMTP_PASS and SMTP_DEST):
        print(f"[NOTIFY] Skipped — SMTP_USER/SMTP_PASS/SMTP_DEST not fully configured.")
        return False
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = SMTP_DEST

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [SMTP_DEST], msg.as_string())
        print(f"[NOTIFY] Sent: {subject}")
        return True
    except Exception as e:
        print(f"[NOTIFY ERROR] Failed to send '{subject}': {e}")
        return False
