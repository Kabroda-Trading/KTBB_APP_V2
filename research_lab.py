# research_lab.py
# ==============================================================================
# RESEARCH LAB CONTROLLER v3.2 (RESTORED PIPELINE)
# ==============================================================================

import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
import traceback

import battlebox_pipeline  # <--- USES THE FULL PIPELINE NOW

async def run_research_lab(
    symbol: str, 
    start_date_utc: str, 
    end_date_utc: str, 
    session_ids: List[str] = None,
    tuning: Dict[str, Any] = None
) -> Dict[str, Any]:
    
    try:
        start_dt = datetime.strptime(start_date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        
        fetch_start_ts = int((start_dt - timedelta(hours=48)).timestamp())
        fetch_end_ts = int((end_dt + timedelta(hours=30)).timestamp())
        
        # Call the new integrated fetcher
        raw_5m = await battlebox_pipeline.fetch_historical_pagination(symbol, fetch_start_ts, fetch_end_ts)
        
        if not raw_5m: return {"ok": False, "error": "No data returned from Exchange."}
        
        if not session_ids:
            target_ids = [s["id"] for s in battlebox_pipeline.SESSION_CONFIGS]
        else:
            target_ids = session_ids
            
        active_configs = [s for s in battlebox_pipeline.SESSION_CONFIGS if s["id"] in target_ids]
        
        sessions_result = []
        curr_day = start_dt
        
        while curr_day <= end_dt:
            day_str = curr_day.strftime("%Y-%m-%d")
            for cfg in active_configs:
                result = battlebox_pipeline.compute_session_from_candles(
                    cfg=cfg,
                    utc_date=curr_day,
                    raw_5m=raw_5m,
                    exec_hours=12,
                    tuning=tuning
                )
                sessions_result.append({
                    "date": day_str,
                    "session_name": cfg["name"],
                    "session_id": cfg["id"],
                    **result
                })
            curr_day += timedelta(days=1)
            
        valid_sessions = [s for s in sessions_result if s.get("ok")]
        total = len(valid_sessions)
        acceptance_count = sum(1 for s in valid_sessions if s["counts"]["had_acceptance"])
        alignment_count = sum(1 for s in valid_sessions if s["counts"]["had_alignment"])
        
        fail_reasons = {}
        for s in valid_sessions:
            reason = s["events"].get("fail_reason", "UNKNOWN")
            if reason: fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
        
        rate = round((alignment_count / total * 100), 1) if total > 0 else 0.0
        
        return {
            "ok": True,
            "symbol": symbol,
            "range": {"start": start_date_utc, "end": end_date_utc},
            "tuning_applied": tuning or "DEFAULTS",
            "stats": {
                "sessions_total": total,
                "acceptance_count": acceptance_count,
                "alignment_count": alignment_count,
                "alignment_rate_pct": rate,
                "fail_reasons": fail_reasons
            },
            "sessions": sessions_result
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}