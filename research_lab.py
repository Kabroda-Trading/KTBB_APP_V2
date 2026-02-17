# research_lab.py
# ==============================================================================
# KABRODA RESEARCH LAB v8.5 (DATA COLLECTOR & TIME MACHINE)
# JOB: Reconstruct the exact "Phase 1" data packet from history.
# CHAIN OF COMMAND: main.py -> run_research_lab -> battlebox_pipeline -> sse_engine
# ==============================================================================
import pandas as pd
from datetime import datetime, timedelta, timezone
import traceback

# CORE ENGINES
import session_manager
import sse_engine 
import battlebox_pipeline  # Respects the chain of command for fetching data

def _slice_by_ts(candles, start_ts, end_ts):
    """Helper to cut candle arrays by timestamp"""
    return [c for c in candles if start_ts <= c["time"] < end_ts]

def _calculate_weekly_bias(df, current_time_ts):
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

def _precalculate_daily_emas(df_5m):
    try:
        df_daily = df_5m.resample('1D').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()

        df_daily['ema30'] = df_daily['close'].ewm(span=30, adjust=False).mean()
        df_daily['ema50'] = df_daily['close'].ewm(span=50, adjust=False).mean()
        return df_daily
    except Exception as e:
        print(f"[LAB] Error calculating Daily EMAs: {e}")
        return pd.DataFrame()

async def run_research_lab(payload: dict):
    """
    Main entry point from main.py.
    Coordinates data fetching and analysis.
    """
    symbol = payload.get("symbol", "BTCUSDT").strip().upper()
    start_date = payload.get("start_date_utc")
    end_date = payload.get("end_date_utc")
    session_ids = payload.get("session_ids", ["us_ny_futures"])
    include_candles = payload.get("include_candles", False)
    tuning = payload.get("tuning", {})

    if not start_date or not end_date:
        return {"ok": False, "error": "Start and End dates are required."}

    try:
        # 1. Setup timestamps for the Fetcher
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        # We fetch 40 days prior so the system has enough data to accurately calculate the 30/50 EMAs
        fetch_start_dt = start_dt - timedelta(days=40)
        
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        
        fetch_start_ts = int(fetch_start_dt.timestamp())
        end_ts = int(end_dt.timestamp())

        # 2. Command the Pipeline to fetch the history
        raw_5m = await battlebox_pipeline.fetch_historical_pagination(symbol, fetch_start_ts, end_ts)

        if not raw_5m or len(raw_5m) < 100:
            return {"ok": False, "error": "Insufficient historical data fetched from exchange."}

        # 3. Pass the fetched data directly into the Analyzer
        return await _run_hybrid_analysis(symbol, raw_5m, start_date, end_date, session_ids, tuning, include_candles)

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": f"Research Lab Error: {str(e)}"}

async def _run_hybrid_analysis(symbol, raw_5m, start_date, end_date, session_ids, tuning, include_candles):
    try:
        # Master Index
        df = pd.DataFrame(raw_5m)
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df.set_index('time', inplace=True)
        df.sort_index(inplace=True)

        # Pre-calculate Sniper Data
        df_daily_emas = _precalculate_daily_emas(df)

        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        active_cfgs = [s for s in session_manager.SESSION_CONFIGS if s["id"] in session_ids]
        
        results = []
        curr_day = start_dt
        
        while curr_day <= end_dt:
            query_time = curr_day + timedelta(hours=12) # Mid-day check
            
            for cfg in active_cfgs:
                anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, query_time)
                actual_session_date = datetime.fromtimestamp(anchor_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                
                lock_end_ts = anchor_ts + 1800  
                exec_end_ts = lock_end_ts + (12 * 3600) 

                calibration = _slice_by_ts(raw_5m, anchor_ts, lock_end_ts)
                if len(calibration) < 6: continue 
                
                context_24h = _slice_by_ts(raw_5m, lock_end_ts - 86400, lock_end_ts)

                d_ema30 = 0.0
                d_ema50 = 0.0
                try:
                    day_key = pd.to_datetime(actual_session_date).date()
                    if str(day_key) in df_daily_emas.index:
                        daily_row = df_daily_emas.loc[str(day_key)]
                        d_ema30 = daily_row['ema30']
                        d_ema50 = daily_row['ema50']
                except:
                    pass

                # RUN THE ENGINE
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
                levels['daily_ema30'] = d_ema30
                levels['daily_ema50'] = d_ema50
                
                bias = _calculate_weekly_bias(df, anchor_ts)

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
                            "slope": levels.get("slope", 0),
                            "daily_ema30": levels.get("daily_ema30", 0),
                            "daily_ema50": levels.get("daily_ema50", 0)
                        },
                        "context": {
                            "weekly_force": bias
                        }
                    }
                }

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