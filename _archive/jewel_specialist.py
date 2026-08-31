# jewel_specialist.py
# ==============================================================================
# KABRODA JEWEL SPECIALIST — Phase 3C
# Captures one structured multi-timeframe energy snapshot per session transition.
# No LLM call — pure extraction from mtf_confluence_scanner.
# Called 6× daily at: NY_OPEN, NY_MIDDAY, NY_CLOSE, ASIA_OPEN, ASIA_MIDDAY, LONDON_OPEN
# Phase 4 scheduler triggers this at each session boundary.
# ==============================================================================

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from database import SessionLocal, JewelSnapshotLog, AgentRunLog
from mtf_confluence_scanner import run_mtf_confluence_scan

logger = logging.getLogger(__name__)

VALID_SESSION_LABELS = {
    "NY_OPEN", "NY_MIDDAY", "NY_CLOSE",
    "ASIA_OPEN", "ASIA_MIDDAY", "LONDON_OPEN",
}

# Maps scanner timeframe keys to DB column names
_TF_MAP = [
    ("15M", "tf_15m_state"),
    ("1H",  "tf_1h_state"),
    ("4H",  "tf_4h_state"),
    ("1D",  "tf_daily_state"),
    ("1W",  "tf_weekly_state"),
]


def _extract_tf_state(tf: Dict[str, Any]) -> str:
    """
    Extract all JEWEL fields from one timeframe dict and return as JSON string.
    Captures: direction, zone, momentum, adx_strength, bbwp, pmarp, divergence.
    """
    return json.dumps({
        "direction":          tf.get("direction_vote", "NEUTRAL"),
        "zone":               tf.get("stoch_rsi", {}).get("zone", "NEUTRAL"),
        "momentum":           tf.get("stoch_rsi", {}).get("curl", "FLAT"),
        "adx_strength":       tf.get("adx_strength", "WEAK"),
        "bbwp_value":         tf.get("bbwp_value", 50.0),
        "bbwp_compressed":    tf.get("bbwp_compressed", False),
        "pmarp_value":        tf.get("pmarp_value", 50.0),
        "pmarp_overextended": tf.get("pmarp_overextended", False),
        "pmarp_direction":    tf.get("pmarp_direction", "NEUTRAL"),
        "divergence":         tf.get("divergence", "NONE"),
        "divergence_strength":tf.get("divergence_strength", "NONE"),
    })


def _log_run(
    session_label: str,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    db = SessionLocal()
    try:
        db.add(AgentRunLog(
            agent_name="jewel_specialist",
            model="n/a",
            triggered_by=session_label,
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            estimated_cost_usd=0.0,
            status=status,
            error_message=error_message,
        ))
        db.commit()
    except Exception as e:
        logger.error(f"[JEWEL] agent_run_log write failed: {e}")
    finally:
        db.close()


async def run_jewel_snapshot(
    symbol: str,
    session_label: str,
    current_price: float,
    date_key: str,
) -> Dict[str, Any]:
    """
    Capture one JEWEL snapshot at a session transition.

    Steps:
    1. Call mtf_confluence_scanner for live 5-TF data
    2. Extract 11-field state per timeframe as JSON
    3. Write one row to jewel_snapshot_log
    4. Write one row to agent_run_log (cost = $0.00, no LLM call)
    5. Return {"status": "SUCCESS", "snapshot": {...}}
    """
    if session_label not in VALID_SESSION_LABELS:
        return {"status": "ERROR", "error": f"Invalid session_label: {session_label}"}

    try:
        scan = await run_mtf_confluence_scan(symbol)
    except Exception as e:
        logger.error(f"[JEWEL] scan failed for {symbol}: {e}")
        _log_run(session_label, "ERROR", str(e))
        return {"status": "ERROR", "error": str(e)}

    timeframes = scan.get("timeframes", {})
    jewel = scan.get("jewel_signal", {})

    tf_states = {col: _extract_tf_state(timeframes.get(key, {})) for key, col in _TF_MAP}

    now_utc = datetime.now(tz=timezone.utc)

    db = SessionLocal()
    try:
        row = JewelSnapshotLog(
            symbol=symbol,
            timestamp=now_utc,
            session_label=session_label,
            asset_price=current_price,
            tf_15m_state=tf_states["tf_15m_state"],
            tf_1h_state=tf_states["tf_1h_state"],
            tf_4h_state=tf_states["tf_4h_state"],
            tf_daily_state=tf_states["tf_daily_state"],
            tf_weekly_state=tf_states["tf_weekly_state"],
            confluence_score=scan.get("confluence_score"),
            dominant_direction=scan.get("dominant_direction"),
            conviction=scan.get("conviction"),
            any_tf_compressed=scan.get("any_tf_compressed"),
            any_tf_overextended=scan.get("any_tf_overextended"),
            any_tf_divergence=scan.get("any_tf_divergence"),
            jewel_gate_open=jewel.get("gate_open"),
            jewel_conviction=jewel.get("conviction"),
            jewel_exit_warning=jewel.get("exit_warning"),
            jewel_divergence_warning=jewel.get("divergence_warning"),
            jewel_signal_summary=jewel.get("signal_summary"),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        snapshot_id = row.id
    except Exception as e:
        db.rollback()
        logger.error(f"[JEWEL] DB write failed: {e}")
        _log_run(session_label, "ERROR", str(e))
        return {"status": "ERROR", "error": str(e)}
    finally:
        db.close()

    _log_run(session_label, "SUCCESS")

    snapshot = {
        "id": snapshot_id,
        "symbol": symbol,
        "timestamp": now_utc.isoformat(),
        "session_label": session_label,
        "asset_price": current_price,
        "confluence_score": scan.get("confluence_score"),
        "dominant_direction": scan.get("dominant_direction"),
        "conviction": scan.get("conviction"),
        "any_tf_compressed": scan.get("any_tf_compressed"),
        "any_tf_overextended": scan.get("any_tf_overextended"),
        "any_tf_divergence": scan.get("any_tf_divergence"),
        "jewel_signal": jewel,
        **{col: json.loads(state) for col, state in tf_states.items()},
    }

    logger.info(
        f"[JEWEL] Snapshot written — {session_label} | {symbol} | ${current_price:,.2f} | "
        f"gate={'OPEN' if jewel.get('gate_open') else 'CLOSED'} | "
        f"dir={jewel.get('direction')} | conviction={jewel.get('conviction')}"
    )
    return {"status": "SUCCESS", "snapshot": snapshot}
