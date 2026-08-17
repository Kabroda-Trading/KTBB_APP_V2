# harness/health_check.py
# =============================================================================
# KABRODA HEALTH CHECK — 2026-06-27
#
# Run from Render Shell where DATABASE_URL is set:
#     python harness/health_check.py
#
# Three-part report:
#   PART 1 — HEALTH: is each data layer actually writing? (per-layer verdict)
#   PART 2 — STAND-DOWN REVIEW: what did price do after each stand-down?
#   PART 3 — SUMMARY: doc-ready text for WORK_LOG update
#
# READ-ONLY. No writes to any table.
# =============================================================================

import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import (
    SessionLocal, CampaignLog, SessionLock, GravityMemory,
    MonitorEventLog, SessionAuditLog,
)
from sqlalchemy import text

# ── Config ──────────────────────────────────────────────────────────────────
REVIEW_SINCE   = "2026-06-24"   # sessions on or after this date_key
SYMBOL         = "BTC/USDT"
NY_OPEN_UTC_HR = 13             # 9:00 AM ET = 13:00 UTC (EDT)
NY_CLOSE_UTC_HR = 19            # 3:00 PM ET


# ── Helpers ──────────────────────────────────────────────────────────────────

def sep(title="", width=72):
    if title:
        pad = "─" * ((width - len(title) - 2) // 2)
        print(f"\n{pad} {title} {pad}")
    else:
        print("─" * width)


def yn(val):
    if val is None: return "NULL"
    return "YES" if val else "no"


def fmt_f(val, digits=2):
    return f"{val:.{digits}f}" if val is not None else "NULL"


def fmt_pct(val):
    if val is None: return "NULL"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"


# ── PART 1 helpers ───────────────────────────────────────────────────────────

def check_db_url():
    url = os.getenv("DATABASE_URL", "sqlite:///./kabroda.db")
    if "sqlite" in url:
        print("FATAL: DATABASE_URL points at local SQLite. Run from Render Shell.")
        sys.exit(1)
    print(f"DB: {url.split('://')[0]}://<redacted>")


def audit_log_health(db):
    sep("PART 1A — session_audit_log")
    rows = (
        db.query(SessionAuditLog)
        .filter(
            SessionAuditLog.symbol == SYMBOL,
            SessionAuditLog.date_key >= REVIEW_SINCE,
        )
        .order_by(SessionAuditLog.date_key)
        .all()
    )

    if not rows:
        print(f"NOT WRITING — zero rows found since {REVIEW_SINCE}")
        return []

    print(f"Row count since {REVIEW_SINCE}: {len(rows)}")
    print()

    header = (
        f"{'Date':<12} {'Status':<14} {'Kg':<14} {'Energy':<14} "
        f"{'µState':<18} {'bo':<10} {'bd':<10} "
        f"{'d21dir':<13} {'w200pos':<9} {'w200dist':>9} {'test_cnt':>8} "
        f"{'outcome':<14}"
    )
    print(header)
    print("─" * len(header))

    for r in rows:
        d21dir  = r.daily_21ema_direction or "NULL"
        w200pos = r.weekly_200sma_position or "NULL"
        w200dst = fmt_pct(r.weekly_200sma_distance_pct)
        w200cnt = str(r.weekly_200sma_test_count) if r.weekly_200sma_test_count is not None else "NULL"
        outcome = r.outcome_type or "pending"

        print(
            f"{r.date_key:<12} {(r.approval_status or 'NULL'):<14} "
            f"{(r.kinematic_grade or 'NULL'):<14} {(r.energy_status or 'NULL'):<14} "
            f"{(r.micro_state_lock or 'NULL'):<18} "
            f"{fmt_f(r.bo_trigger):<10} {fmt_f(r.bd_trigger):<10} "
            f"{d21dir:<13} {w200pos:<9} {w200dst:>9} {w200cnt:>8} "
            f"{outcome:<14}"
        )

    print()
    # Diagnostic counts
    null_kg    = sum(1 for r in rows if r.kinematic_grade is None)
    null_micro = sum(1 for r in rows if r.micro_state_lock is None)
    null_d21   = sum(1 for r in rows if r.daily_21ema_direction is None)
    null_w200  = sum(1 for r in rows if r.weekly_200sma_position is None)
    null_out   = sum(1 for r in rows if r.outcome_type is None)

    print(f"NULL kinematic_grade:         {null_kg}/{len(rows)}")
    print(f"NULL micro_state_lock:        {null_micro}/{len(rows)}")
    print(f"NULL daily_21ema_direction:   {null_d21}/{len(rows)}  ← Phase 1 MTF")
    print(f"NULL weekly_200sma_position:  {null_w200}/{len(rows)}  ← Phase 1 MTF")
    print(f"outcome_type still NULL:      {null_out}/{len(rows)}")

    # Column-exists check for new MTF columns (old schema would fail)
    try:
        db.execute(text("SELECT daily_21ema_direction FROM session_audit_log LIMIT 1"))
        print("Schema: MTF columns PRESENT")
    except Exception as e:
        print(f"Schema: MTF columns MISSING — {e}")

    return rows


def monitor_health(db):
    sep("PART 1B — monitor_event_log")

    # Check if table exists
    try:
        rows = (
            db.query(MonitorEventLog)
            .filter(
                MonitorEventLog.symbol == SYMBOL,
                MonitorEventLog.session_date >= REVIEW_SINCE,
            )
            .order_by(MonitorEventLog.session_date, MonitorEventLog.poll_sequence)
            .all()
        )
    except Exception as e:
        print(f"NOT ACCESSIBLE — {e}")
        return

    if not rows:
        print(f"NOT WRITING — zero rows found since {REVIEW_SINCE}")
        print("Possible causes: monitor loop not started / session window not active / silent crash")
        return

    # Group by session_date
    by_date: dict = {}
    for r in rows:
        by_date.setdefault(r.session_date, []).append(r)

    print(f"Total rows: {len(rows)} across {len(by_date)} session dates")
    print()
    print(f"{'Date':<12} {'Polls':>6} {'Max_seq':>8} {'Transitions':>12} {'Price_range':>20} {'Verdict_seen'}")
    print("─" * 80)

    for date_key in sorted(by_date.keys()):
        session_rows = by_date[date_key]
        poll_count   = len(session_rows)
        max_seq      = max(r.poll_sequence for r in session_rows)
        trans_count  = sum(r.transition_count or 0 for r in session_rows)
        prices       = [r.btc_price for r in session_rows if r.btc_price]
        price_rng    = (
            f"{min(prices):.0f}–{max(prices):.0f}" if prices else "NULL"
        )
        verdicts = set(r.mas_verdict for r in session_rows if r.mas_verdict)
        verdict_seen = "/".join(sorted(verdicts)) or "NULL"

        print(f"{date_key:<12} {poll_count:>6} {max_seq:>8} {trans_count:>12} {price_rng:>20} {verdict_seen}")

    # Sample state snapshot from most recent poll
    most_recent = sorted(rows, key=lambda r: r.poll_timestamp)[-1]
    print(f"\nMost recent poll: {most_recent.poll_timestamp} UTC — seq {most_recent.poll_sequence}")
    if most_recent.state_snapshot_json:
        try:
            snap = json.loads(most_recent.state_snapshot_json)
            print("State snapshot (sample):")
            for k, v in snap.items():
                print(f"  {k}: {v}")
        except Exception:
            print(f"  state_snapshot_json: {most_recent.state_snapshot_json[:200]}")
    else:
        print("  state_snapshot_json: NULL — states not being captured")

    # Sample a transition event if any
    has_trans = [r for r in rows if r.any_transition]
    if has_trans:
        ex = has_trans[0]
        print(f"\nSample transition (row {ex.id}, {ex.session_date} seq {ex.poll_sequence}):")
        print(f"  {ex.transitions_json}")
    else:
        print("\nNo transitions logged yet across any session")


def gravity_memory_health(db):
    sep("PART 1C — gravity_memory (WEEKLY_200_SMA + macro engine)")

    # Check for WEEKLY_200_SMA entry
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        row = (
            db.query(GravityMemory)
            .filter(
                GravityMemory.symbol == sym,
                GravityMemory.source == "WEEKLY_200_SMA",
            )
            .first()
        )
        if row:
            print(f"{sym}: WEEKLY_200_SMA = {row.price:.2f}  (written {row.timestamp}  active={row.active})")
        else:
            print(f"{sym}: WEEKLY_200_SMA — NOT PRESENT  ← macro engine hasn't written this yet")

    # Check MACRO_ENGINE_CLASS_0 recency
    print()
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        newest = (
            db.query(GravityMemory)
            .filter(
                GravityMemory.symbol == sym,
                GravityMemory.source == "MACRO_ENGINE_CLASS_0",
            )
            .order_by(GravityMemory.timestamp.desc())
            .first()
        )
        if newest:
            print(f"{sym}: latest MACRO_ENGINE_CLASS_0 written {newest.timestamp}")
        else:
            print(f"{sym}: MACRO_ENGINE_CLASS_0 — no rows")


# ── PART 2 helpers ───────────────────────────────────────────────────────────

async def _fetch_1m_kraken(symbol: str, since_ms: int, limit: int = 500) -> list:
    """Fetch 1m Kraken OHLCV from since_ms. Returns list of {ts_ms, o, h, l, c}."""
    import ccxt.async_support as ccxt
    exc = ccxt.kraken({"enableRateLimit": True})
    try:
        fmt = symbol if "/" in symbol else symbol.replace("USDT", "/USDT")
        rows = await exc.fetch_ohlcv(fmt, "1m", since=since_ms, limit=limit)
        return [{"ts_ms": int(r[0]), "o": r[1], "h": r[2], "l": r[3], "c": r[4]} for r in rows]
    except Exception as e:
        print(f"  Kraken fetch error: {e}")
        return []
    finally:
        await exc.close()


def _session_lock_ts_ms(date_key: str) -> int:
    """Approximate lock time (9:00 AM ET = 13:00 UTC) as epoch ms."""
    dt = datetime.strptime(date_key, "%Y-%m-%d").replace(
        hour=NY_OPEN_UTC_HR, tzinfo=timezone.utc
    )
    return int(dt.timestamp() * 1000)


def _session_close_ts_ms(date_key: str) -> int:
    """Approximate session close (3:00 PM ET = 19:00 UTC) as epoch ms."""
    dt = datetime.strptime(date_key, "%Y-%m-%d").replace(
        hour=NY_CLOSE_UTC_HR, tzinfo=timezone.utc
    )
    return int(dt.timestamp() * 1000)


async def counterfactual_price_action(campaign, audit_row, lock_ts_ms: int, close_ts_ms: int):
    """
    Given a stand-down session, compute the counterfactual:
    1. Did price reach the hypothetical entry trigger?
    2. If yes: did it hit T1 or stop first?

    Levels come from session_audit_log (bo_trigger / bd_trigger) — these are frozen
    at lock time and are the authoritative SSOT regardless of what the brief said.
    Measured-move T1 is recomputed from bo/bd distance.
    """
    bias = campaign.bias or "NEUTRAL"

    # Use audit_row bo/bd as the authoritative triggers; fall back to campaign if no audit row
    bo = (audit_row.bo_trigger if audit_row else None) or campaign.entry_price
    bd = (audit_row.bd_trigger if audit_row else None) or campaign.stop_loss

    entry, stop, t1 = None, None, None
    if bo and bd:
        dist = bo - bd
        if bias == "LONG":
            entry, stop, t1 = bo, bd, bo + dist
        elif bias == "SHORT":
            entry, stop, t1 = bd, bo, bd - dist

    result = {
        "bias": bias,
        "entry": entry,
        "stop": stop,
        "t1": t1,
        "bo": bo,
        "bd": bd,
        "entry_triggered": False,
        "entry_triggered_at": None,
        "outcome": "NOT_TRIGGERED",
        "outcome_detail": "",
        "session_high": None,
        "session_low": None,
        "post_lock_move_pct": None,
    }

    if not entry:
        result["outcome"] = "NO_LEVELS"
        return result

    candles = await _fetch_1m_kraken(SYMBOL, since_ms=lock_ts_ms, limit=500)
    session_candles = [c for c in candles if c["ts_ms"] <= close_ts_ms]

    if not session_candles:
        result["outcome"] = "NO_CANDLES"
        return result

    # Price at lock time (first candle open)
    lock_price = session_candles[0]["o"]
    highs = [c["h"] for c in session_candles]
    lows  = [c["l"] for c in session_candles]
    session_high = max(highs)
    session_low  = min(lows)
    result["session_high"] = session_high
    result["session_low"]  = session_low

    # Post-lock directional move
    if bias == "LONG":
        result["post_lock_move_pct"] = (session_high - lock_price) / lock_price * 100
    elif bias == "SHORT":
        result["post_lock_move_pct"] = (lock_price - session_low) / lock_price * 100

    # Was entry triggered?
    for i, c in enumerate(session_candles):
        if bias == "LONG" and c["h"] >= entry:
            result["entry_triggered"] = True
            result["entry_triggered_at"] = i  # candle index
            break
        elif bias == "SHORT" and c["l"] <= entry:
            result["entry_triggered"] = True
            result["entry_triggered_at"] = i
            break

    if not result["entry_triggered"]:
        result["outcome"] = "NOT_TRIGGERED"
        result["outcome_detail"] = (
            f"Price never reached entry {'≥' if bias=='LONG' else '≤'} {entry:.2f}"
            f" (high={session_high:.2f}, low={session_low:.2f})"
        )
        return result

    # Entry triggered — scan from that candle for stop or T1
    post_entry = session_candles[result["entry_triggered_at"]:]
    for c in post_entry:
        hit_stop = (bias == "LONG" and c["l"] <= stop) if stop else False
        hit_t1   = (bias == "LONG" and c["h"] >= t1)  if t1  else False
        if bias == "SHORT":
            hit_stop = (c["h"] >= stop) if stop else False
            hit_t1   = (c["l"] <= t1)  if t1  else False

        if hit_stop and hit_t1:
            result["outcome"]        = "STOP_FIRST (same candle)"
            result["outcome_detail"] = f"Stop {stop:.2f} and T1 {t1:.2f} both in same candle — conservative: STOP"
            return result
        if hit_stop:
            result["outcome"]        = "STOP"
            result["outcome_detail"] = f"Stop {stop:.2f} hit"
            return result
        if hit_t1:
            result["outcome"]        = "T1_HIT"
            result["outcome_detail"] = f"T1 {t1:.2f} reached"
            return result

    result["outcome"]        = "UNRESOLVED_AT_SESSION_CLOSE"
    result["outcome_detail"] = "Neither stop nor T1 hit by 3 PM ET"
    return result


async def stand_down_review(db):
    sep("PART 2 — STAND-DOWN REVIEW (directional observations, N is tiny)")
    print("All results labeled DIRECTIONAL_OBSERVATION — N<10, no conclusions drawn.\n")

    campaigns = (
        db.query(CampaignLog)
        .filter(
            CampaignLog.symbol == SYMBOL,
            CampaignLog.date_key >= REVIEW_SINCE,
            CampaignLog.is_canonical == True,
        )
        .order_by(CampaignLog.date_key)
        .all()
    )

    if not campaigns:
        print(f"No canonical campaign_log rows since {REVIEW_SINCE}")
        return

    print(f"Sessions since {REVIEW_SINCE}: {len(campaigns)}")
    stand_downs = [c for c in campaigns if c.mas_approval_status in ("STAND_DOWN", "MAS_STAND_DOWN", "REJECTED", "MAS_REJECTED")]
    approved    = [c for c in campaigns if c.mas_approval_status in ("APPROVED", "MAS_APPROVED")]
    print(f"  STAND_DOWN/REJECTED: {len(stand_downs)}")
    print(f"  APPROVED:            {len(approved)}")
    print()

    if not stand_downs:
        print("No stand-down sessions found in this date range.")
        return

    sep("Stand-down table (one row per session)")
    results = []
    for c in stand_downs:
        lock_ts_ms  = _session_lock_ts_ms(c.date_key)
        close_ts_ms = _session_close_ts_ms(c.date_key)
        audit_row = (
            db.query(SessionAuditLog)
            .filter(
                SessionAuditLog.symbol == SYMBOL,
                SessionAuditLog.date_key == c.date_key,
            )
            .first()
        )
        cf = await counterfactual_price_action(c, audit_row, lock_ts_ms, close_ts_ms)
        results.append((c, cf, audit_row))
        print(f"  Fetched {c.date_key}…", flush=True)

    print()
    print(
        f"{'Date':<12} {'Bias':<6} {'Status':<14} "
        f"{'Entry':<10} {'Stop':<10} {'T1':<10} "
        f"{'Triggered':<10} {'Outcome':<28} {'Session_H':>10} {'Session_L':>10} {'Move%':>7}"
    )
    print("─" * 120)

    for c, cf, _ar in results:
        print(
            f"{c.date_key:<12} {cf['bias']:<6} {(c.mas_approval_status or ''):<14} "
            f"{fmt_f(cf.get('entry')):<10} {fmt_f(cf.get('stop')):<10} {fmt_f(cf.get('t1')):<10} "
            f"{yn(cf['entry_triggered']):<10} {cf['outcome']:<28} "
            f"{fmt_f(cf.get('session_high')):>10} {fmt_f(cf.get('session_low')):>10} "
            f"{fmt_pct(cf.get('post_lock_move_pct')):>7}"
        )
        if cf.get("outcome_detail"):
            print(f"  ↳ {cf['outcome_detail']}")

    # Summary counts
    print()
    n_total       = len(results)
    n_triggered   = sum(1 for _, cf, _ar in results if cf["entry_triggered"])
    n_t1          = sum(1 for _, cf, _ar in results if cf["outcome"] == "T1_HIT")
    n_stop        = sum(1 for _, cf, _ar in results if "STOP" in cf["outcome"])
    n_no_trigger  = sum(1 for _, cf, _ar in results if not cf["entry_triggered"])

    print(f"DIRECTIONAL_OBSERVATION summary (N={n_total} — no conclusions):")
    print(f"  Entry trigger NOT reached:  {n_no_trigger}/{n_total}  (stand-down was correct — no trade would have occurred)")
    print(f"  Entry trigger reached:      {n_triggered}/{n_total}")
    if n_triggered:
        print(f"    → T1 hit:               {n_t1}/{n_triggered}")
        print(f"    → Stop hit:             {n_stop}/{n_triggered}")

    # Audit row context — use the rows already fetched in the results loop
    sep("Audit log context for stand-down sessions")
    for c, cf, audit in results:
        if audit:
            print(f"{c.date_key}: kg={audit.kinematic_grade or 'NULL'} "
                  f"µ={audit.micro_state_lock or 'NULL'} "
                  f"energy={audit.energy_status or 'NULL'} "
                  f"d21={audit.daily_21ema_direction or 'NULL'} "
                  f"1h200={audit.tf1h_200sma_position or 'NULL'} "
                  f"4h200={audit.tf4h_200sma_position or 'NULL'} "
                  f"w200={audit.weekly_200sma_position or 'NULL'}({fmt_pct(audit.weekly_200sma_distance_pct)})")
        else:
            print(f"{c.date_key}: NO AUDIT ROW — session_audit_log not writing for this session")

    return results


# ── PART 3 helpers ───────────────────────────────────────────────────────────

def doc_summary(audit_rows, sd_results, monitor_rows_by_date):
    sep("PART 3 — DOC-READY SUMMARY (paste into WORK_LOG next session)")
    print()
    print(f"Health check run: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Period reviewed: {REVIEW_SINCE} → today")
    print()
    print("Layer status:")
    print(f"  session_audit_log:      {'WRITING' if audit_rows else 'NOT WRITING'} "
          f"({len(audit_rows)} rows since {REVIEW_SINCE})")
    mtf_pop = sum(1 for r in audit_rows if r.daily_21ema_direction is not None)
    print(f"  Phase 1 MTF columns:    {'POPULATING' if mtf_pop else 'ALL NULL'} "
          f"({mtf_pop}/{len(audit_rows)} rows with daily_21ema_direction set)")
    print(f"  monitor_event_log:      {'WRITING' if monitor_rows_by_date else 'NOT WRITING'} "
          f"({sum(len(v) for v in monitor_rows_by_date.values())} rows across "
          f"{len(monitor_rows_by_date)} sessions)")


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    check_db_url()
    db = SessionLocal()
    try:
        audit_rows = audit_log_health(db)
        monitor_health(db)
        gravity_memory_health(db)

        sd_results = await stand_down_review(db)

        # Build monitor_rows_by_date for summary
        try:
            mon_rows = (
                db.query(MonitorEventLog)
                .filter(
                    MonitorEventLog.symbol == SYMBOL,
                    MonitorEventLog.session_date >= REVIEW_SINCE,
                )
                .all()
            )
            monitor_rows_by_date = {}
            for r in mon_rows:
                monitor_rows_by_date.setdefault(r.session_date, []).append(r)
        except Exception:
            monitor_rows_by_date = {}

        doc_summary(audit_rows, sd_results, monitor_rows_by_date)

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
