# research_lab.py
# ==============================================================================
# RESEARCH LAB CONTROLLER v4.0 (PURE FUNCTION)
# ==============================================================================
# 1. Accepts pre-fetched candles (No CCXT usage here).
# 2. Runs SSE + State Engine (Engines are Sacred).
# 3. Runs Battlebox Rules (Stoch/GO) for extra insights.
# ==============================================================================

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import traceback

import battlebox_pipeline  # For session configs and anchor logic
import sse_engine
import structure_state_engine
import battlebox_rules  # <--- The New Rule Layer

def _slice_by_ts(candles: List[Dict[str, Any]], start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    # Assumes candles are sorted by time
    return [c for c in candles if start_ts <= c["time"] < end_ts]

async def run_research_lab_from_candles(
    symbol: str,
    raw_5m: List[Dict[str, Any]],
    start_date_utc: str,
    end_date_utc: str,
    session_ids: Optional[List[str]] = None,
    exec_hours: int = 12,
    tuning: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Pure Replay Coordinator:
    - Uses ONLY raw_5m passed in.
    - Runs Engines (SSE/State) strictly.
    - Applies Rule Layer (GO signals) for reporting.
    """
    try:
        if not raw_5m:
            return {"ok": False, "error": "No candles provided to Research Lab."}

        start_dt = datetime.strptime(start_date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)

        target_ids = session_ids or [s["id"] for s in battlebox_pipeline.SESSION_CONFIGS]
        active_cfgs = [s for s in battlebox_pipeline.SESSION_CONFIGS if s["id"] in target_ids]

        sessions_result: List[Dict[str, Any]] = []
        curr_day = start_dt

        while curr_day <= end_dt:
            day_str = curr_day.strftime("%Y-%m-%d")

            for cfg in active_cfgs:
                # 1. Anchor & Slice (Strict)
                anchor_ts = battlebox_pipeline.anchor_ts_for_utc_date(cfg, curr_day)
                lock_end_ts = anchor_ts + 1800
                exec_end_ts = lock_end_ts + (exec_hours * 3600)

                calibration = _slice_by_ts(raw_5m, anchor_ts, lock_end_ts)
                if len(calibration) < 6:
                    continue

                context_24h = _slice_by_ts(raw_5m, lock_end_ts - 86400, lock_end_ts)
                post_lock = _slice_by_ts(raw_5m, lock_end_ts, exec_end_ts)

                # 2. Run SSE Engine (Levels)
                sse_input = {
                    "locked_history_5m": context_24h,
                    "slice_24h_5m": context_24h,
                    "session_open_price": calibration[0]["open"],
                    "r30_high": max(c["high"] for c in calibration),
                    "r30_low": min(c["low"] for c in calibration),
                    "last_price": context_24h[-1]["close"] if context_24h else 0.0,
                    "tuning": tuning or {}
                }
                computed = sse_engine.compute_sse_levels(sse_input)
                if "error" in computed:
                    continue

                levels = computed["levels"]

                # 3. Run State Engine (The Law) - Respects Tuning Levers
                state = structure_state_engine.compute_structure_state(levels, post_lock, tuning=tuning)
                
                had_acceptance = (state["permission"]["status"] == "EARNED")
                side = state["permission"]["side"]

                # 4. Run Rule Layer (GO Detector) - Only if Accepted
                go = {"ok": False, "go_type": "NONE", "go_ts": None, "reason": "NO_ACCEPTANCE", "evidence": {}}
                
                if had_acceptance and side in ("LONG", "SHORT") and post_lock:
                    # Proxy 15m Stoch at Acceptance time (using context)
                    candles_15m_proxy = sse_engine._resample(context_24h, 15) if hasattr(sse_engine, "_resample") else []
                    st15 = battlebox_rules.compute_stoch(candles_15m_proxy)
                    
                    go = battlebox_rules.detect_pullback_go(
                        side=side,
                        levels=levels,
                        post_accept_5m=post_lock,
                        stoch_15m_at_accept=st15,
                        use_zone="TRIGGER"
                    )

                # 5. Pack Results (Clean Data for GPT)
                sessions_result.append({
                    "ok": True,
                    "date": day_str,
                    "session_id": cfg["id"],
                    "session_name": cfg["name"],
                    "levels_compact": {
                        "BO": levels.get("breakout_trigger"),
                        "BD": levels.get("breakdown_trigger"),
                        "DS": levels.get("daily_support"),
                        "DR": levels.get("daily_resistance")
                    },
                    "counts": {
                        "had_acceptance": had_acceptance,
                        "had_alignment": (state["execution"]["gates_mode"] == "LOCKED"),
                        "had_go": bool(go["ok"]),
                        "go_type": go["go_type"],
                    },
                    "events": {
                        "acceptance_side": side,
                        "final_state": state.get("action"),
                        "go_ts": go.get("go_ts"),
                        "go_reason": go.get("reason"),
                        "fail_reason": state.get("diagnostics", {}).get("fail_reason", "UNKNOWN")
                    }
                })

            curr_day += timedelta(days=1)

        # 6. Aggregate Stats
        total = len(sessions_result)
        acc_count = sum(1 for s in sessions_result if s["counts"]["had_acceptance"])
        align_count = sum(1 for s in sessions_result if s["counts"]["had_alignment"])
        go_count = sum(1 for s in sessions_result if s["counts"]["had_go"])
        
        fail_reasons = {}
        for s in sessions_result:
            r = s["events"].get("fail_reason")
            if r: fail_reasons[r] = fail_reasons.get(r, 0) + 1

        return {
            "ok": True,
            "symbol": symbol,
            "range": {"start": start_date_utc, "end": end_date_utc},
            "stats": {
                "sessions_total": total,
                "acceptance_count": acc_count,
                "alignment_count": align_count,
                "go_count": go_count,
                "fail_reasons": fail_reasons
            },
            "sessions": sessions_result
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}