# research_lab.py
# ==============================================================================
# RESEARCH LAB: RAW DATA COLLECTOR (TIME MACHINE)
# JOB: Reconstruct the exact "Phase 1" data packet from history.
# STRUCTURE: Matches 'battlebox_pipeline' output perfectly.
# ==============================================================================
import pandas as pd
from datetime import datetime, timedelta, timezone
import traceback

# CORE ENGINES (The actual logic used by your live system)
import session_manager
import sse_engine 

def _slice_by_ts(candles, start_ts, end_ts):
    """Helper to cut candle arrays by timestamp"""
    return [c for c in candles if start_ts <= c["time"] < end_ts]

def _calculate_weekly_bias(df, current_time_ts):
    """
    Reconstructs Weekly Bias for a historical date.
    Logic: Compares current session Open vs. Price 7 days ago.
    """
    try:
        week_ago_ts = current_time_ts - (7 * 86400)
        week_ago_idx = df.index.get_indexer([pd.to_datetime(week_ago_ts, unit='s', utc=True)], method='nearest')[0]
        price_week_ago = df.iloc[week_ago_idx]['close']
        
        curr_idx = df.index.get_indexer([pd.to_datetime(current_time_ts, unit='s', utc=True)], method='nearest')[0]
        price_now = df.iloc[curr_idx]['open']

        if price_now > price_week_ago * 1.01: return "BULLISH"
        if price_now < price_week_ago * 0.99: return "BEARISH"
        return "NEUTRAL"
    except:
        return "NEUTRAL"

async def run_hybrid_analysis(symbol, raw_5m, start_date, end_date, session_ids, tuning=None, sensors=None, min_score=0, include_candles=True):
    try:
        if not raw_5m or len(raw_5m) < 100: 
            return {"ok": False, "error": "Insufficient Data"}
        
        # 1. PREPARE DATA FRAME (For Bias Calculation)
        df = pd.DataFrame(raw_5m)
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df.set_index('time', inplace=True)
        df.sort_index(inplace=True)

        # 2. SETUP DATES
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        active_cfgs = [s for s in session_manager.SESSION_CONFIGS if s["id"] in session_ids]
        
        results = []
        curr_day = start_dt
        
        # 3. RUN HISTORY
        while curr_day <= end_dt:
            query_time = curr_day + timedelta(hours=12)
            
            for cfg in active_cfgs:
                # A. Identify the Session
                anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, query_time)
                actual_session_date = datetime.fromtimestamp(anchor_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                
                lock_end_ts = anchor_ts + 1800  # First 30m
                exec_end_ts = lock_end_ts + (12 * 3600) # Full Session

                # B. Get Data Slices
                calibration = _slice_by_ts(raw_5m, anchor_ts, lock_end_ts)
                if len(calibration) < 6: continue 
                
                context_24h = _slice_by_ts(raw_5m, lock_end_ts - 86400, lock_end_ts)

                # --- C. RUN THE ENGINE (PHASE 1) ---
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
                if "error" in computed: continue
                
                levels = computed["levels"]
                bias = _calculate_weekly_bias(df, anchor_ts)

                # --- E. EXPORT RAW PACKET (STRUCTURED FOR MARKET RADAR) ---
                # This mimics the "battlebox_pipeline" output dictionary
                result_packet = {
                    "date": f"{actual_session_date} [{cfg['id']}]",
                    "price": calibration[0]["open"], 
                    "battlebox": {
                        "levels": {
                            "anchor_price": levels.get("anchor_price"),
                            "breakout_trigger": levels.get("breakout_trigger"),       
                            "breakdown_trigger": levels.get("breakdown_trigger"),      
                            "daily_resistance": levels.get("daily_resistance"),       
                            "daily_support": levels.get("daily_support"),          
                            "range30m_high": levels.get("range30m_high"),    
                            "range30m_low": levels.get("range30m_low"),      
                            "structure_score": levels.get("structure_score", 0),
                            "slope": levels.get("slope", 0) 
                        },
                        "context": {
                            "weekly_force": bias
                        }
                    }
                }

                # OPTIONAL: Candle Data
                if include_candles:
                    full_session = _slice_by_ts(raw_5m, anchor_ts, exec_end_ts)
                    result_packet["session_candles"] = [
                        {
                            "t": datetime.fromtimestamp(c["time"], tz=timezone.utc).strftime("%H:%M"),
                            "ts": c["time"], 
                            "o": c["open"], "h": c["high"], "l": c["low"], "c": c["close"]
                        }
                        for c in full_session
                    ]

                results.append(result_packet)

            curr_day += timedelta(days=1)

        return {
            "ok": True,
            "total_sessions": len(results),
            "results": results
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}