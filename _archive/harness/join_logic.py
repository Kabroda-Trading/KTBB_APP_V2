# harness/join_logic.py
# =============================================================================
# KABRODA BATTLE-TEST HARNESS — Join Logic
#
# Assembles two evaluable-event streams from raw query results:
#
#   APPROVED STREAM — canonical APPROVED campaigns (CLOSED_WIN / CLOSED_LOSS)
#     joined to their DecisionJournal row on (symbol, date_key == session_date)
#     and optionally to a JEWEL snapshot on the same calendar date.
#
#   STAND-DOWN STREAM — MAS_STAND_DOWN DecisionJournal rows with
#     outcome_direction_correct populated (scoreable stand-downs only).
#     Same optional JEWEL join.
#
# Both streams return a list of plain dicts. Every dict includes the join
# provenance fields so callers can see exactly what matched and what didn't.
#
# READ-ONLY. No write path to any table.
# =============================================================================

from typing import Any, Dict, List, Optional


# ─── helpers ──────────────────────────────────────────────────────────────────

def _date_str(obj, field: str) -> Optional[str]:
    """Extract a YYYY-MM-DD string from a datetime or string attribute."""
    val = getattr(obj, field, None)
    if val is None:
        return None
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    return str(val)[:10]


def _box_size_pct(bo_price, bd_price) -> Optional[float]:
    """Compute session box size as a percentage of the breakout trigger."""
    if not bo_price or not bd_price or bo_price <= 0:
        return None
    return round((bo_price - bd_price) / bo_price * 100.0, 4)


def _box_bucket(pct: float) -> str:
    if pct < 0.5:
        return "NARROW (<0.5%)"
    if pct < 1.0:
        return "MEDIUM (0.5–1.0%)"
    return "WIDE (>1.0%)"


# ─── index builders ────────────────────────────────────────────────────────────

def _index_decisions_by_date(decisions: list) -> Dict[str, Any]:
    """
    Build a {date_key: decision_journal_row} index from mas_flow DJ rows.
    If multiple rows share a date (double-fire), the LATER row wins and
    the anomaly is flagged in the returned index under '_double_fire_dates'.
    """
    index: Dict[str, Any] = {}
    double_fire: List[str] = []
    for d in decisions:
        date = d.session_date
        if date is None:
            continue
        if date in index:
            double_fire.append(date)
        index[date] = d
    index["_double_fire_dates"] = double_fire
    return index


def _index_jewel_by_date(snapshots: list) -> Dict[str, Any]:
    """
    Build a {date_str: jewel_snapshot_row} index from NY_OPEN JEWEL snapshots.
    One snapshot per date expected; later row wins on collision.
    """
    index: Dict[str, Any] = {}
    for snap in snapshots:
        date = _date_str(snap, "timestamp")
        if date:
            index[date] = snap
    return index


# ─── stream builders ───────────────────────────────────────────────────────────

def build_approved_stream(
    campaigns: list,
    decisions: list,
    jewel_snapshots: list,
) -> Dict[str, Any]:
    """
    Approved stream: canonical APPROVED campaigns resolved as CLOSED_WIN or CLOSED_LOSS,
    joined to their mas_flow DecisionJournal row and optionally to a JEWEL snapshot.

    Returns:
      {
        "events": [...],           # list of event dicts
        "n_total": int,            # APPROVED CLOSED_WIN/LOSS campaigns found
        "n_dj_joined": int,        # events that matched a DJ row
        "n_jewel_joined": int,     # events that also matched a JEWEL snapshot
        "double_fire_dates": [...], # dates with multiple DJ rows (anomaly flag)
      }
    """
    dj_index    = _index_decisions_by_date(decisions)
    jewel_index = _index_jewel_by_date(jewel_snapshots)
    double_fire = dj_index.pop("_double_fire_dates", [])

    resolved_statuses = {"CLOSED_WIN", "CLOSED_LOSS"}
    candidates = [
        c for c in campaigns
        if c.mas_approval_status == "APPROVED" and c.status in resolved_statuses
    ]

    events: List[Dict[str, Any]] = []
    n_dj_joined    = 0
    n_jewel_joined = 0

    for c in candidates:
        date = c.date_key
        dj   = dj_index.get(date)
        jewel = jewel_index.get(date)

        if dj:
            n_dj_joined += 1
        if jewel:
            n_jewel_joined += 1

        box_pct = _box_size_pct(
            getattr(dj, "bo_price", None),
            getattr(dj, "bd_price", None),
        ) if dj else None

        events.append({
            # identity
            "date_key":       date,
            "campaign_id":    c.id,
            # outcome
            "outcome":        c.status,           # CLOSED_WIN | CLOSED_LOSS
            "realized_pnl":   c.realized_pnl,
            "target_hit":     c.target_hit,
            # campaign fields
            "bias":           c.bias,
            "entry_price":    c.entry_price,
            "stop_loss":      c.stop_loss,
            "t1":             c.t1,
            # decision_journal fields (None if no DJ match)
            "dj_joined":      dj is not None,
            "energy_status":  getattr(dj, "energy_status",  None),
            "kinematic_grade":getattr(dj, "kinematic_grade", None),
            "bo_price":       getattr(dj, "bo_price",        None),
            "bd_price":       getattr(dj, "bd_price",        None),
            "box_size_pct":   box_pct,
            "box_bucket":     _box_bucket(box_pct) if box_pct is not None else None,
            # jewel fields (None if no snapshot match)
            "jewel_joined":       jewel is not None,
            "jewel_gate_open":    getattr(jewel, "jewel_gate_open",    None),
            "jewel_conviction":   getattr(jewel, "jewel_conviction",   None),
            "jewel_exit_warning": getattr(jewel, "jewel_exit_warning", None),
        })

    return {
        "events":            events,
        "n_total":           len(candidates),
        "n_dj_joined":       n_dj_joined,
        "n_jewel_joined":    n_jewel_joined,
        "double_fire_dates": double_fire,
    }


def build_standdown_stream(
    campaigns: list,
    decisions: list,
    jewel_snapshots: list,
) -> Dict[str, Any]:
    """
    Stand-down stream: canonical STAND_DOWN campaign_log rows joined to their
    mas_flow DecisionJournal row on (symbol, date_key == session_date).

    Accepts BOTH MAS_REJECTED (pre-W-11) and MAS_STAND_DOWN (post-W-11) DJ
    decision types. The campaign_log mas_approval_status='STAND_DOWN' is the
    authority — the DJ label changed semantics at W-11 (2026-06-13) but the
    campaign record correctly identifies all stand-downs regardless of era.

    Pre-W-11 rows labeled MAS_REJECTED in DJ but STAND_DOWN in campaign_logs are
    now included; this surfaces ~14 scoreable veto events vs. the prior 5.
    Note: the Week-0 direct-SQL count (17) included 3 pre-canonical MAS_REJECTED
    rows that predate campaign_log tracking — those are correctly excluded here.

    Returns:
      {
        "events":        [...],  # scoreable stand-down event dicts
        "n_total":       int,    # all canonical STAND_DOWN campaign rows
        "n_dj_joined":   int,    # campaign rows that matched a DJ row
        "n_jewel_joined":int,    # events that also matched a JEWEL snapshot
        "n_scoreable":   int,    # events with outcome_direction_correct populated
        "n_unscoreable": int,    # events with outcome_direction_correct = NULL
      }
    """
    # Build DJ index keyed by session_date, accepting both veto labels.
    # Later row wins on date collision (rare but possible in early double-fire era).
    _VETO_TYPES = {"MAS_REJECTED", "MAS_STAND_DOWN"}
    dj_index: Dict[str, Any] = {}
    for d in decisions:
        if d.decision_type in _VETO_TYPES and d.session_date:
            dj_index[d.session_date] = d

    jewel_index = _index_jewel_by_date(jewel_snapshots)

    # Anchor on campaign STAND_DOWN rows — these are the canonical veto records.
    standdown_campaigns = [
        c for c in campaigns if c.mas_approval_status == "STAND_DOWN"
    ]

    events: List[Dict[str, Any]] = []
    n_dj_joined    = 0
    n_jewel_joined = 0

    for c in standdown_campaigns:
        date  = c.date_key
        dj    = dj_index.get(date)
        jewel = jewel_index.get(date)

        if dj:
            n_dj_joined += 1
        if jewel:
            n_jewel_joined += 1

        odc     = getattr(dj, "outcome_direction_correct", None)
        box_pct = _box_size_pct(
            getattr(dj, "bo_price", None),
            getattr(dj, "bd_price", None),
        ) if dj else None

        events.append({
            # identity
            "date_key":           date,
            "campaign_id":        c.id,
            "dj_id":              getattr(dj, "id", None),
            # join provenance
            "dj_joined":          dj is not None,
            "dj_decision_type":   getattr(dj, "decision_type", None),
            # outcome
            "outcome_direction_correct": odc,
            # indicator readings (None if no DJ match)
            "energy_status":      getattr(dj, "energy_status",   None),
            "kinematic_grade":    getattr(dj, "kinematic_grade",  None),
            "bo_price":           getattr(dj, "bo_price",         None),
            "bd_price":           getattr(dj, "bd_price",         None),
            "box_size_pct":       box_pct,
            "box_bucket":         _box_bucket(box_pct) if box_pct is not None else None,
            # jewel fields
            "jewel_joined":       jewel is not None,
            "jewel_gate_open":    getattr(jewel, "jewel_gate_open",    None),
            "jewel_conviction":   getattr(jewel, "jewel_conviction",   None),
            "jewel_exit_warning": getattr(jewel, "jewel_exit_warning", None),
        })

    scoreable   = [e for e in events if e["outcome_direction_correct"] is not None]
    unscoreable = [e for e in events if e["outcome_direction_correct"] is None]

    return {
        "events":         scoreable,           # only scoreable events for analysis
        "n_total":        len(standdown_campaigns),
        "n_dj_joined":    n_dj_joined,
        "n_jewel_joined": n_jewel_joined,
        "n_scoreable":    len(scoreable),
        "n_unscoreable":  len(unscoreable),
    }
