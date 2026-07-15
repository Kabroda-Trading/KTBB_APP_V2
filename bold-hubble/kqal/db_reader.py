"""
db_reader.py — KQAL Read-Only PostgreSQL Database Access Layer

Read-only access to Kabroda's PostgreSQL database on Render.com.
Every connection enforces default_transaction_read_only=on as a safety belt.
All functions return lists of dicts or None on failure.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Optional

import psycopg
import psycopg.rows

logger = logging.getLogger(__name__)

# ── Connection Helpers ──────────────────────────────────────────────────────

READ_ONLY_OPTIONS = "-c default_transaction_read_only=on"


def _get_connection() -> Optional[psycopg.Connection]:
    """Create a new read-only PostgreSQL connection from DATABASE_URL.

    Returns
    -------
    psycopg (v3) connection or None if connection fails. Kabroda's own
    database.py/requirements.txt use psycopg v3, not psycopg2 -- this
    matches that, rather than adding a second, redundant Postgres driver.
    """
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL environment variable is not set.")
        return None

    try:
        conn = psycopg.connect(db_url, options=READ_ONLY_OPTIONS)
        conn.autocommit = True
        conn.read_only = True
        return conn
    except psycopg.Error as exc:
        logger.error("Failed to connect to database: %s", exc)
        return None


def _fetch_all(
    query: str,
    params: Optional[tuple] = None,
) -> Optional[list[dict[str, Any]]]:
    """Execute a SELECT query and return results as a list of dicts.

    Parameters
    ----------
    query : str
        SQL SELECT statement.
    params : tuple, optional
        Parameters for the query.

    Returns
    -------
    list[dict] or None on failure.
    """
    conn = _get_connection()
    if conn is None:
        return None
    try:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchall()
    except psycopg.Error as exc:
        logger.error("Query failed: %s\nSQL: %s", exc, query[:200])
        return None
    finally:
        conn.close()


def _truncate(text: Optional[str], max_chars: int) -> Optional[str]:
    """Truncate a string to max_chars, appending '…' if truncated."""
    if text is None:
        return None
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


# ── Safety Verification ─────────────────────────────────────────────────────


def verify_read_only() -> bool:
    """Verify the connection is read-only by running a test SELECT.

    Returns
    -------
    bool
        True if the connection works and is read-only, False otherwise.
    """
    conn = _get_connection()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW default_transaction_read_only")
            row = cur.fetchone()
            is_ro = row[0] == "on" if row else False
            if not is_ro:
                logger.warning("Connection is NOT in read-only mode!")
                return False
            # Verify we can actually read
            cur.execute("SELECT 1 AS ok")
            result = cur.fetchone()
            if result and result[0] == 1:
                logger.info("Read-only connection verified successfully.")
                return True
            logger.warning("Test SELECT returned unexpected result.")
            return False
    except psycopg.Error as exc:
        logger.error("verify_read_only failed: %s", exc)
        return False
    finally:
        conn.close()


# ── Query Functions ─────────────────────────────────────────────────────────


def get_recent_trades(days: int = 30) -> Optional[list[dict[str, Any]]]:
    """Fetch recent canonical trades from campaign_logs.

    Parameters
    ----------
    days : int
        How many days back to look (default 30).

    Returns
    -------
    list[dict] or None on failure.
    """
    query = """
        SELECT
            id,
            symbol,
            date_key AS session_date,
            session_id,
            session_timeframe,
            bias,
            entry_price AS entry,
            stop_loss,
            t1,
            t2,
            t3,
            status,
            realized_pnl,
            mas_approval_status,
            target_logic_version,
            closed_at,
            entry_filled_at,
            kinematic_grade,
            energy_grade,
            macro_bias,
            dominant_direction AS confluence_dominant_direction,
            confluence_score,
            structure_reasoning,
            LEFT(formatted_newsletter, 500) AS formatted_newsletter
        FROM campaign_logs
        WHERE is_canonical = TRUE
          AND date_key >= %s
        ORDER BY id DESC
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    return _fetch_all(query, (cutoff,))


def get_session_locks(days: int = 30) -> Optional[list[dict[str, Any]]]:
    """Fetch recent session locks.

    Parameters
    ----------
    days : int
        How many days back to look (default 30).

    Returns
    -------
    list[dict] or None on failure.
    """
    query = """
        SELECT
            id,
            symbol,
            date_key AS session_date,
            session_id,
            lock_time,
            NULL::timestamp AS lock_end_ts,
            NULL::varchar AS session_type,
            LEFT(packet_data, 2000) AS packet_data
        FROM session_locks
        WHERE date_key >= %s
        ORDER BY id DESC
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    return _fetch_all(query, (cutoff,))


def get_agent_run_logs(days: int = 7) -> Optional[list[dict[str, Any]]]:
    """Fetch recent agent run logs.

    Parameters
    ----------
    days : int
        How many days back to look (default 7).

    Returns
    -------
    list[dict] or None on failure.
    """
    query = """
        SELECT
            id,
            agent_name,
            NULL::varchar AS symbol,
            NULL::varchar AS session_date,
            NULL::varchar AS session_id,
            input_tokens AS prompt_tokens,
            output_tokens AS completion_tokens,
            estimated_cost_usd AS estimated_cost,
            (status = 'SUCCESS') AS ran_successfully,
            error_message
        FROM agent_run_log
        WHERE created_at >= %s
        ORDER BY id DESC
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    return _fetch_all(query, (cutoff,))


def get_decision_journal(days: int = 30) -> Optional[list[dict[str, Any]]]:
    """Fetch recent MAS-flow decision journal entries.

    Parameters
    ----------
    days : int
        How many days back to look (default 30).

    Returns
    -------
    list[dict] or None on failure.
    """
    query = """
        SELECT
            id,
            symbol,
            session_date,
            session_id,
            decision_type,
            confluence_score,
            energy_status,
            kinematic_grade,
            source,
            LEFT(full_context_json, 2000) AS full_context_json
        FROM decision_journal
        WHERE source = 'mas_flow'
          AND session_date >= %s
        ORDER BY id DESC
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    return _fetch_all(query, (cutoff,))


def get_gravity_memory_active() -> Optional[list[dict[str, Any]]]:
    """Fetch active gravity memory zones.

    Returns
    -------
    list[dict] or None on failure.
    """
    query = """
        SELECT
            id,
            symbol,
            source,
            level_type,
            price AS level_price,
            NULL::varchar AS intensity_label,
            timestamp AS created_at,
            active
        FROM gravity_memory
        WHERE active = TRUE
        ORDER BY id DESC
    """
    return _fetch_all(query)


def get_jewel_snapshots(days: int = 7) -> Optional[list[dict[str, Any]]]:
    """Fetch recent jewel snapshot logs.

    Parameters
    ----------
    days : int
        How many days back to look (default 7).

    Returns
    -------
    list[dict] or None on failure.
    """
    query = """
        SELECT
            id,
            symbol,
            session_label,
            timestamp AS snapshot_time,
            jsonb_build_object(
                'asset_price', asset_price,
                'tf_15m_state', tf_15m_state,
                'tf_1h_state', tf_1h_state,
                'tf_4h_state', tf_4h_state,
                'tf_daily_state', tf_daily_state,
                'tf_weekly_state', tf_weekly_state,
                'confluence_score', confluence_score,
                'dominant_direction', dominant_direction,
                'conviction', conviction,
                'any_tf_compressed', any_tf_compressed,
                'any_tf_overextended', any_tf_overextended,
                'any_tf_divergence', any_tf_divergence,
                'jewel_gate_open', jewel_gate_open,
                'jewel_conviction', jewel_conviction,
                'jewel_exit_warning', jewel_exit_warning,
                'jewel_divergence_warning', jewel_divergence_warning,
                'jewel_signal_summary', jewel_signal_summary
            ) AS jewel_json
        FROM jewel_snapshot_log
        WHERE timestamp >= %s
        ORDER BY id DESC
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    return _fetch_all(query, (cutoff,))


def get_performance_audits(days: int = 30) -> Optional[list[dict[str, Any]]]:
    """Fetch recent performance audit logs.

    Parameters
    ----------
    days : int
        How many days back to look (default 30).

    Returns
    -------
    list[dict] or None on failure.
    """
    query = """
        SELECT
            id,
            symbol,
            date_key AS audit_date,
            LEFT(audit_md, 1000) AS audit_md
        FROM system_audit_log
        WHERE date_key >= %s
        ORDER BY id DESC
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    return _fetch_all(query, (cutoff,))


def get_system_stats() -> Optional[dict[str, Any]]:
    """Aggregate KPIs from canonical campaign_logs.

    Returns
    -------
    dict with keys:
        total_trades, win_count, loss_count, avg_realized_pnl, net_r,
        win_rate, by_timeframe (list of per-timeframe stats)
    or None on failure.
    """
    # Overall stats
    overall_query = """
        SELECT
            COUNT(*)::int AS total_trades,
            COUNT(*) FILTER (WHERE realized_pnl > 0)::int AS win_count,
            COUNT(*) FILTER (WHERE realized_pnl <= 0)::int AS loss_count,
            ROUND(AVG(realized_pnl)::numeric, 4)::float AS avg_realized_pnl,
            ROUND(COALESCE(SUM(realized_pnl), 0)::numeric, 4)::float AS net_r,
            ROUND(
                (COUNT(*) FILTER (WHERE realized_pnl > 0))::numeric
                / NULLIF(COUNT(*), 0) * 100, 2
            )::float AS win_rate
        FROM campaign_logs
        WHERE is_canonical = TRUE
    """
    # Per-timeframe breakdown
    tf_query = """
        SELECT
            COALESCE(session_timeframe, 'UNKNOWN') AS timeframe,
            COUNT(*)::int AS total_trades,
            COUNT(*) FILTER (WHERE realized_pnl > 0)::int AS win_count,
            COUNT(*) FILTER (WHERE realized_pnl <= 0)::int AS loss_count,
            ROUND(AVG(realized_pnl)::numeric, 4)::float AS avg_realized_pnl,
            ROUND(COALESCE(SUM(realized_pnl), 0)::numeric, 4)::float AS net_r,
            ROUND(
                (COUNT(*) FILTER (WHERE realized_pnl > 0))::numeric
                / NULLIF(COUNT(*), 0) * 100, 2
            )::float AS win_rate
        FROM campaign_logs
        WHERE is_canonical = TRUE
        GROUP BY session_timeframe
        ORDER BY timeframe
    """

    overall = _fetch_all(overall_query)
    by_tf = _fetch_all(tf_query)

    if overall is None or by_tf is None:
        return None

    result = overall[0] if overall else {}
    result["by_timeframe"] = by_tf if by_tf else []
    return result


def get_interpreter_logs(days: int = 7) -> Optional[list[dict[str, Any]]]:
    """Fetch recent interpreter logs.

    Parameters
    ----------
    days : int
        How many days back to look (default 7).

    Returns
    -------
    list[dict] or None on failure.
    """
    query = """
        SELECT
            id,
            symbol,
            session_date,
            session_id,
            interpreter_name,
            LEFT(output_text, 500) AS output_text,
            ran_successfully
        FROM interpreter_log
        WHERE created_at >= %s
        ORDER BY id DESC
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    return _fetch_all(query, (cutoff,))


# ── Main / Self-Test ────────────────────────────────────────────────────────


def _run_self_test() -> None:
    """Run all queries and print results for verification."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 72)
    print("  KQAL db_reader.py — Self-Test")
    print("=" * 72)

    # 1. verify_read_only
    print("\n[1] verify_read_only()")
    ro_ok = verify_read_only()
    print(f"    Read-only verified: {ro_ok}")
    if not ro_ok:
        print("    ⚠  Cannot proceed — connection failed or not read-only.")
        return

    # 2. get_recent_trades
    print("\n[2] get_recent_trades(days=30)")
    trades = get_recent_trades(days=30)
    if trades is None:
        print("    ❌ Query returned None (error)")
    else:
        print(f"    ✅ {len(trades)} rows")
        if trades:
            t = trades[0]
            print(f"    Sample: id={t.get('id')}, symbol={t.get('symbol')}, "
                  f"bias={t.get('bias')}, status={t.get('status')}, "
                  f"pnl={t.get('realized_pnl')}")
            # Show keys present
            print(f"    Keys: {list(t.keys())}")

    # 3. get_session_locks
    print("\n[3] get_session_locks(days=30)")
    locks = get_session_locks(days=30)
    if locks is None:
        print("    ❌ Query returned None (error)")
    else:
        print(f"    ✅ {len(locks)} rows")
        if locks:
            lk = locks[0]
            print(f"    Sample: id={lk.get('id')}, symbol={lk.get('symbol')}, "
                  f"session_id={lk.get('session_id')}")
            print(f"    Keys: {list(lk.keys())}")

    # 4. get_agent_run_logs
    print("\n[4] get_agent_run_logs(days=7)")
    runs = get_agent_run_logs(days=7)
    if runs is None:
        print("    ❌ Query returned None (error)")
    else:
        print(f"    ✅ {len(runs)} rows")
        if runs:
            r = runs[0]
            print(f"    Sample: id={r.get('id')}, agent={r.get('agent_name')}, "
                  f"success={r.get('ran_successfully')}, "
                  f"cost={r.get('estimated_cost')}")
            print(f"    Keys: {list(r.keys())}")

    # 5. get_decision_journal
    print("\n[5] get_decision_journal(days=30)")
    decisions = get_decision_journal(days=30)
    if decisions is None:
        print("    ❌ Query returned None (error)")
    else:
        print(f"    ✅ {len(decisions)} rows")
        if decisions:
            d = decisions[0]
            print(f"    Sample: id={d.get('id')}, symbol={d.get('symbol')}, "
                  f"type={d.get('decision_type')}, "
                  f"source={d.get('source')}")
            print(f"    Keys: {list(d.keys())}")

    # 6. get_gravity_memory_active
    print("\n[6] get_gravity_memory_active()")
    gravity = get_gravity_memory_active()
    if gravity is None:
        print("    ❌ Query returned None (error)")
    else:
        print(f"    ✅ {len(gravity)} rows")
        if gravity:
            g = gravity[0]
            print(f"    Sample: id={g.get('id')}, symbol={g.get('symbol')}, "
                  f"type={g.get('level_type')}, "
                  f"price={g.get('level_price')}")
            print(f"    Keys: {list(g.keys())}")

    # 7. get_jewel_snapshots
    print("\n[7] get_jewel_snapshots(days=7)")
    jewels = get_jewel_snapshots(days=7)
    if jewels is None:
        print("    ❌ Query returned None (error)")
    else:
        print(f"    ✅ {len(jewels)} rows")
        if jewels:
            j = jewels[0]
            print(f"    Sample: id={j.get('id')}, symbol={j.get('symbol')}, "
                  f"label={j.get('session_label')}")
            print(f"    Keys: {list(j.keys())}")

    # 8. get_performance_audits
    print("\n[8] get_performance_audits(days=30)")
    audits = get_performance_audits(days=30)
    if audits is None:
        print("    ❌ Query returned None (error)")
    else:
        print(f"    ✅ {len(audits)} rows")
        if audits:
            a = audits[0]
            print(f"    Sample: id={a.get('id')}, symbol={a.get('symbol')}, "
                  f"date={a.get('audit_date')}")
            print(f"    Keys: {list(a.keys())}")

    # 9. get_system_stats
    print("\n[9] get_system_stats()")
    stats = get_system_stats()
    if stats is None:
        print("    ❌ Query returned None (error)")
    else:
        print(f"    ✅ Stats retrieved")
        print(f"    total_trades={stats.get('total_trades')}, "
              f"win_count={stats.get('win_count')}, "
              f"loss_count={stats.get('loss_count')}, "
              f"win_rate={stats.get('win_rate')}%, "
              f"net_r={stats.get('net_r')}")
        tf_breakdown = stats.get("by_timeframe", [])
        print(f"    timeframe_breakdown ({len(tf_breakdown)} entries):")
        for tf in tf_breakdown:
            print(f"      {tf.get('timeframe'):>6s}: "
                  f"{tf.get('total_trades')} trades, "
                  f"{tf.get('win_rate')}% WR, "
                  f"{tf.get('net_r')} net R")

    # 10. get_interpreter_logs
    print("\n[10] get_interpreter_logs(days=7)")
    interpreters = get_interpreter_logs(days=7)
    if interpreters is None:
        print("    ❌ Query returned None (error)")
    else:
        print(f"    ✅ {len(interpreters)} rows")
        if interpreters:
            ip = interpreters[0]
            print(f"    Sample: id={ip.get('id')}, symbol={ip.get('symbol')}, "
                  f"name={ip.get('interpreter_name')}, "
                  f"success={ip.get('ran_successfully')}")
            print(f"    Keys: {list(ip.keys())}")

    print("\n" + "=" * 72)
    print("  Self-test complete.")
    print("=" * 72)


if __name__ == "__main__":
    _run_self_test()
