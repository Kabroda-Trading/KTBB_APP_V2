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
import json
import os
import smtplib
import ssl
import urllib.request
from email.mime.text import MIMEText
from typing import List, Optional

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


def _log_send(subject: str, body: str, recipients: List[str], outcome: str, detail: str = None) -> None:
    """2026-09-27 (Andy ruling 14:55 CT): every email this codebase sends
    routes through send_admin_email() (confirmed by a full-repo audit),
    so logging here alone covers every category -- traveler lock/armed/
    done/closed, gravity, ledger, executor error alerts, all of it. Same
    self-contained lazy-import/short-lived-session pattern as
    _db_subscriber_recipients() above -- never raises, a logging failure
    must never block or fail the actual send this function already
    completed or skipped by the time this runs."""
    try:
        from database import SessionLocal, EmailSendLog
        db = SessionLocal()
        try:
            db.add(EmailSendLog(
                subject=subject, body=body, recipients=", ".join(recipients),
                outcome=outcome, detail=detail,
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[NOTIFY] EmailSendLog write failed (non-fatal): {e}")


def _send(subject: str, body: str, recipients: List[str]) -> bool:
    """Shared SMTP-send + EmailSendLog-logging core for send_admin_email()
    and send_account_email() (2026-09-28, the L4 email-routing split) --
    both deliver the identical way (STARTTLS, the same SKIPPED/SENT/FAILED
    outcomes); only recipient RESOLUTION differs between the two, which is
    exactly what each of them computes before calling this. Never raises,
    matching both callers' existing contract."""
    if not (SMTP_USER and SMTP_PASS and recipients):
        reason = "SMTP_USER/SMTP_PASS not configured, or no recipients."
        print(f"[NOTIFY] Skipped — {reason}")
        _log_send(subject, body, recipients, "SKIPPED", reason)
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
        _log_send(subject, body, recipients, "SENT")
        return True
    except Exception as e:
        print(f"[NOTIFY ERROR] Failed to send '{subject}': {e}")
        _log_send(subject, body, recipients, "FAILED", str(e))
        return False


def send_admin_email(subject: str, body: str) -> bool:
    """
    Sends a plain-text email to the union of SMTP_DEST's addresses and
    every active EmailSubscriber row, deduped, via STARTTLS. Returns
    True on success, False on any failure (missing config, connection
    error, auth error). Never raises — callers should not need their own
    try/except, but the pattern is safe to double-wrap if a caller
    already does. Every outcome (sent, skipped, failed) is logged to
    EmailSendLog (database.py) -- 2026-09-27 audit finding: this table had
    zero writer, so real sends were invisible in the DB.

    2026-09-28 (Andy's L4 ruling -- see send_account_email() below): this
    is now the RADAR class only -- session-wide/generic notifications
    (lock emails, plan-level ARMED/DONE transitions, pipeline-health
    alerts) with no single real person's trading activity in them. Any
    email describing a SPECIFIC account's fill/open/close/cancel/error
    must go through send_account_email() instead, never this function --
    see that function's own docstring for why.
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
    return _send(subject, body, recipients)


def _resolve_account_owner_email(account_id: int):
    """Looks up the real person who owns this ExecutorAccount, for
    send_account_email() below. Self-contained lazy-import/short-lived-
    session pattern, matching _db_subscriber_recipients() above. Returns
    None (never raises) if the account or its owning user can't be
    resolved -- the caller must treat that as "cannot deliver," never
    fall back to the merged radar list, which would silently reintroduce
    the exact cross-account leak this whole mechanism exists to close."""
    try:
        from database import SessionLocal, ExecutorAccount, UserModel
        db = SessionLocal()
        try:
            account = db.query(ExecutorAccount).filter(ExecutorAccount.id == account_id).first()
            if account is None:
                return None
            user = db.query(UserModel).filter(UserModel.id == account.user_id).first()
            if user is None or not user.email:
                return None
            return user.email.strip()
        finally:
            db.close()
    except Exception as e:
        print(f"[NOTIFY] Account-owner email lookup failed for account_id={account_id}: {e}")
        return None


def send_account_email(subject: str, body: str, account_id: int) -> bool:
    """2026-09-28 (Andy's L4 ruling, Kabroda AI Brain CC_INTERFACE.md,
    "RULED BY ANDY 09-28 ~18:09 CT"): trade-execution emails -- fills,
    opens, closes, cancels, unprotected-position/placement errors,
    risk$ -- must route ONLY to that specific ExecutorAccount's real
    owner (executor_accounts.user_id -> users.email), never the merged
    radar list send_admin_email() uses. Each ExecutorAccount is a
    distinct real person's own exchange account (own API credentials,
    own risk) -- broadcasting one person's fill/close/error detail to
    every subscriber, as every trade email did before this fix, leaks
    another person's real trading activity, not just noise. Andy's own
    words on the finding (AGENT_LOG.md, Kabroda AI Brain, 09-28 18:10 CT):
    "they don't need to know that I'm trading on the exchange or not."

    Deliberately does NOT fall back to the merged list if the owner's
    email can't be resolved (missing account row, missing user, missing
    email) -- that fallback would silently reintroduce the exact leak
    this function exists to prevent. A resolution failure is logged
    (SKIPPED, EmailSendLog) and printed loudly instead, matching this
    codebase's loud-failure-never-silent-degradation discipline; it
    should never actually happen in practice since
    ExecutorAccount.user_id is a required, non-null column.
    """
    owner_email = _resolve_account_owner_email(account_id)
    if owner_email is None:
        reason = (f"Could not resolve an owner email for account_id={account_id} -- "
                  f"trade-execution email NOT sent to the merged list (that would defeat per-account routing).")
        print(f"[NOTIFY] Skipped — {reason}")
        _log_send(subject, body, [], "SKIPPED", reason)
        return False
    return _send(subject, body, [owner_email])


# ==============================================================================
# TELEGRAM PUSH (2026-11-xx) -- a second delivery channel for the exact same
# per-account events send_account_email() above already covers (fill,
# breakeven ratchet, exit/close, and an always-on "problem" category for
# unprotected positions/failed orders/exchange-side cancels). Never a new
# decision point: every call site already computes this data for its email;
# this just gives it a second, shorter, phone-friendly destination. Plain
# stdlib urllib, matching this file's own "no new dependency" principle for
# SMTP above. The radar/admin class (send_admin_email()) is deliberately
# NOT extended here -- Andy's L4 ruling (see send_account_email()'s own
# docstring) is a routing boundary, not just an email-specific one.
# ==============================================================================

def _send_telegram_raw(chat_id: str, text: str) -> bool:
    """POSTs to Telegram's Bot API sendMessage endpoint. Fails closed --
    logs and returns False, never raises -- if TELEGRAM_BOT_TOKEN is
    unset or the call fails, matching send_admin_email()/send_account_
    email()'s own never-raise contract. Same global-env-var, fail-closed
    convention as METASIGNALS_SECRET_KEY/CROSSTRADE_BEARER_TOKEN (CLAUDE.md) --
    one bot token for the whole site, not per-account, since a Telegram
    bot token has no per-account dimension (one bot, many chat_ids)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        print(f"[NOTIFY] Telegram skipped — TELEGRAM_BOT_TOKEN not configured, or no chat_id.")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                print(f"[NOTIFY ERROR] Telegram send failed (HTTP {resp.status}): {text}")
                return False
        print(f"[NOTIFY] Telegram sent to chat_id={chat_id}: {text}")
        return True
    except Exception as e:
        print(f"[NOTIFY ERROR] Telegram send failed: {e}")
        return False


def _resolve_account_owner_user_id(account_id: int) -> Optional[int]:
    """Same self-contained lazy-import/short-lived-session pattern as
    _resolve_account_owner_email() above, but returns the user_id itself
    (needed to look up NotificationPreference) rather than the email."""
    try:
        from database import SessionLocal, ExecutorAccount
        db = SessionLocal()
        try:
            account = db.query(ExecutorAccount).filter(ExecutorAccount.id == account_id).first()
            return account.user_id if account is not None else None
        finally:
            db.close()
    except Exception as e:
        print(f"[NOTIFY] Account-owner user_id lookup failed for account_id={account_id}: {e}")
        return None


def _is_admin_user_id(user_id: int) -> bool:
    """Only consulted for the TELEGRAM_CHAT_ID env-var fallback below --
    not a general admin check, and never used to grant visibility into
    another user's account (that mutual-isolation rule is unrelated and
    untouched by this)."""
    try:
        from database import SessionLocal, UserModel
        db = SessionLocal()
        try:
            user = db.query(UserModel).filter(UserModel.id == user_id).first()
            return bool(user and user.is_admin)
        finally:
            db.close()
    except Exception:
        return False


def send_account_telegram(category: str, text: str, account_id: int) -> bool:
    """category is one of "fill"/"breakeven"/"exit" (gated by that user's
    own NotificationPreference toggle, default-on) or "problem"
    (unprotected position / order failed / canceled on the exchange --
    always sent once a chat_id resolves, no opt-out, since these are
    urgent risk alerts, not routine noise a trader should be able to
    silently mute). Never raises -- wrap at the call site the same way
    the existing send_account_email() call already is.

    If no NotificationPreference row/chat_id exists yet for this user AND
    they're the admin user, falls back to the TELEGRAM_CHAT_ID env var --
    Andy already set this on Render, so his alerts work with zero further
    action from him; Dawson/Terry set their own chat_id through the
    account page's Mobile Alerts card instead."""
    user_id = _resolve_account_owner_user_id(account_id)
    if user_id is None:
        return False

    chat_id = None
    toggle = True
    try:
        from database import SessionLocal, NotificationPreference
        db = SessionLocal()
        try:
            pref = db.query(NotificationPreference).filter_by(user_id=user_id).first()
            if pref is not None:
                chat_id = pref.telegram_chat_id
                toggle = {
                    "fill": pref.notify_fill,
                    "breakeven": pref.notify_breakeven,
                    "exit": pref.notify_exit,
                }.get(category, True)
        finally:
            db.close()
    except Exception as e:
        print(f"[NOTIFY] NotificationPreference lookup failed for user_id={user_id}: {e}")

    if not chat_id and _is_admin_user_id(user_id):
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "") or None

    if not chat_id or not toggle:
        return False
    return _send_telegram_raw(chat_id, text)


def _telegram_fill_text(desk: str, symbol: str, direction: str, price: float, stop: Optional[float] = None) -> str:
    stop_part = f" (stop {stop:g})" if stop is not None else ""
    return f"✅ {desk}: {symbol} {direction} filled @ {price:g}{stop_part}"


def _telegram_breakeven_text(desk: str, symbol: str, be_price: float) -> str:
    return f"🛡️ {desk}: {symbol} stop moved to breakeven ({be_price:g}) — risk-free"


def _telegram_exit_text(desk: str, symbol: str, exit_reason: str, r: Optional[float] = None, usd: Optional[float] = None) -> str:
    parts = []
    if r is not None:
        parts.append(f"{'+' if r >= 0 else ''}{r:.2f}R")
    if usd is not None:
        parts.append(f"{'+' if usd >= 0 else '-'}${abs(usd):.2f}")
    detail = f" ({' / '.join(parts)})" if parts else ""
    return f"🏁 {desk}: {symbol} closed via {exit_reason}{detail}"


def _telegram_problem_text(desk: str, symbol: str, detail: str) -> str:
    return f"⚠️ {desk}: {symbol} — {detail}"
