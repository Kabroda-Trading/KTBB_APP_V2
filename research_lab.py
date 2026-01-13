# research_lab.py
# ==============================================================================
# RESEARCH LAB CONTROLLER v7.0 (CONFIRMATION MODE SUPPORT)
# ==============================================================================
from __future__ import annotations
from datetime import datetime, timedelta, timezone 
from typing import Any, Dict, List, Optional
import traceback

import battlebox_pipeline
import sse_engine
import structure_state_engine
import battlebox_rules 

def _slice_by_ts(candles: List[Dict[str, Any]], start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
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
    try:
        print(f"[LAB] Starting analysis for {symbol} ({len(raw_5m)} candles provided)")
        
        if not raw_5m: return {"ok": False, "error": "No candles provided to Research Lab."}

        raw_5m.sort(key=lambda x: x["time"])

        start_dt = datetime.strptime(start_date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)

        target_ids = session_ids or [s["id"] for s in battlebox_pipeline.SESSION_CONFIGS]
        active_cfgs = [s for s in battlebox_pipeline.SESSION_CONFIGS if s["id"] in target_ids]

        tuning = tuning or {}
        req_vol = bool(tuning.get("require_volume", False))
        req_div = bool(tuning.get("require_divergence", False))
        fusion_mode = bool(tuning.get("fusion_mode", False))
        
        # FLAGS
        ignore_15m = bool(tuning.get("ignore_15m_alignment", False))
        ignore_5m = bool(tuning.get("ignore_5m_stoch", False))
        
        # CONFIRMATION MODE
        confirm_mode = tuning.get("confirmation_mode", "TOUCH")
        
        tol_bps = int(tuning.get("zone_tolerance_bps", 10)) 
        zone_tol = tol_bps / 10000.0

        sessions_result: List[Dict[str, Any]] = []
        curr_day = start_dt

        while curr_day <= end_dt:
            day_str = curr_day.strftime("%Y-%m-%d")
            
            for cfg in active_cfgs:
                anchor_ts = battlebox_pipeline.anchor_ts_for_utc_date(cfg, curr_day)
                lock_end_ts = anchor_ts + 1800
                exec_end_ts = lock_end_ts + (exec_hours * 3600)

                calibration = _slice_by_ts(raw_5m, anchor_ts, lock_end_ts)
                if len(calibration) < 6: continue

                context_24h = _slice_by_ts(raw_5m, lock_end_ts - 86400, lock_end_ts)
                post_lock = _slice_by_ts(raw_5m, lock_end_ts, exec_end_ts)

                sse_input = {
                    "locked_history_5m": context_24h,
                    "slice_24h_5m": context_24h,
                    "session_open_price": calibration[0]["open"],
                    "r30_high": max(c["high"] for c in calibration),
                    "r30_low": min(c["low"] for c in calibration),
                    "last_price": context_24h[-1]["close"] if context_24h else 0.0,
                    "tuning": tuning
                }
                computed = sse_engine.compute_sse_levels(sse_input)
                if "error" in computed: continue

                levels = computed["levels"]
                state = structure_state_engine.compute_structure_state(levels, post_lock, tuning=tuning)
                had_acceptance = (state["permission"]["status"] == "EARNED")
                side = state["permission"]["side"]

                go = {"ok": False, "go_type": "NONE", "go_ts": None, "reason": "NO_ACCEPTANCE", "evidence": {}}
                
                if had_acceptance and side in ("LONG", "SHORT") and post_lock:
                    candles_15m_proxy = sse_engine._resample(context_24h, 15) if hasattr(sse_engine, "_resample") else []
                    st15 = battlebox_rules.compute_stoch(candles_15m_proxy)
                    
                    go = battlebox_rules.detect_pullback_go(
                        side=side, levels=levels, post_accept_5m=post_lock, stoch_15m_at_accept=st15, 
                        use_zone="TRIGGER", require_volume=req_vol, require_divergence=req_div,
                        fusion_mode=fusion_mode, zone_tol=zone_tol,
                        ignore_15m=ignore_15m,
                        ignore_5m_stoch=ignore_5m,
                        confirmation_mode=confirm_mode # <--- PASSING NEW ARG
                    )

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

        total = len(sessions_result)
        fail_reasons = {}
        for s in sessions_result:
            r = s["events"].get("fail_reason")
            if r: fail_reasons[r] = fail_reasons.get(r, 0) + 1
            
        print(f"[LAB] Analysis complete. Sessions: {total}")

        return {
            "ok": True,
            "symbol": symbol,
            "range": {"start": start_date_utc, "end": end_date_utc},
            "stats": {
                "sessions_total": total,
                "acceptance_count": sum(1 for s in sessions_result if s["counts"]["had_acceptance"]),
                "go_count": sum(1 for s in sessions_result if s["counts"]["had_go"]),
                "campaign_go_count": sum(1 for s in sessions_result if s["counts"]["go_type"] == "CAMPAIGN_GO"),
                "scalp_go_count": sum(1 for s in sessions_result if s["counts"]["go_type"] == "SCALP_GO"),
                "fail_reasons": fail_reasons
            },
            "sessions": sessions_result
        }
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}