# market_simulator.py
# ==============================================================================
# KABRODA MARKET SIMULATOR v1.0
# JOB: Standalone backtesting engine. Duplicates Market Radar math and Research 
#      Lab data fetching to test historical probabilities safely.
# ==============================================================================
import pandas as pd
from datetime import datetime, timedelta, timezone
import traceback

# CORE ENGINES (Directly asking the site, not other pages)
import session_manager
import sse_engine 
import battlebox_pipeline

def _slice_by_ts(candles, start_ts, end_ts):
    """Helper to cut candle arrays by timestamp"""
    return [c for c in candles if start_ts <= c["time"] < end_ts]

def _precalculate_daily_emas(df_5m):
    try:
        df_daily = df_5m.resample('1D').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        df_daily['ema30'] = df_daily['close'].ewm(span=30, adjust=False).mean()
        df_daily['ema50'] = df_daily['close'].ewm(span=50, adjust=False).mean()
        return df_daily
    except:
        return pd.DataFrame()

async def run_simulation(payload: dict):
    """
    Main entry point for the Simulator.
    """
    symbol = payload.get("symbol", "BTCUSDT").strip().upper()
    start_date = payload.get("start_date_utc")
    end_date = payload.get("end_date_utc")
    session_ids = payload.get("session_ids", ["us_ny_futures"])
    
    # Simulation Parameters
    target_type = payload.get("target_type", "pole") # 'pole', 't1', 't2', 't3'
    entry_type = payload.get("entry_type", "15m_close") # 'instant', '15m_close'
    stop_type = payload.get("stop_type", "30m_range") # '30m_range', '15m_candle'

    if not start_date or not end_date:
        return {"ok": False, "error": "Start and End dates are required."}

    try:
        # 1. FETCH HISTORICAL DATA (Duplicated from Research Lab)
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        fetch_start_dt = start_dt - timedelta(days=40)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        
        raw_5m = await battlebox_pipeline.fetch_historical_pagination(
            symbol, int(fetch_start_dt.timestamp()), int(end_dt.timestamp())
        )

        if not raw_5m or len(raw_5m) < 100:
            return {"ok": False, "error": "Insufficient historical data."}

        df = pd.DataFrame(raw_5m)
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df.set_index('time', inplace=True)
        df.sort_index(inplace=True)
        df_daily_emas = _precalculate_daily_emas(df)

        active_cfgs = [s for s in session_manager.SESSION_CONFIGS if s["id"] in session_ids]
        
        # SIMULATION TRACKING
        wins = 0
        losses = 0
        trade_log = []
        curr_day = start_dt
        
        # 2. ITERATE DAYS & RUN SIMULATION
        while curr_day <= end_dt:
            query_time = curr_day + timedelta(hours=12)
            
            for cfg in active_cfgs:
                anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, query_time)
                actual_session_date = datetime.fromtimestamp(anchor_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                
                lock_end_ts = anchor_ts + 1800  
                exec_end_ts = lock_end_ts + (12 * 3600) 

                calibration = _slice_by_ts(raw_5m, anchor_ts, lock_end_ts)
                if len(calibration) < 6: continue 
                
                context_24h = _slice_by_ts(raw_5m, lock_end_ts - 86400, lock_end_ts)
                session_candles = _slice_by_ts(raw_5m, lock_end_ts, exec_end_ts)

                sse_input = {
                    "locked_history_5m": context_24h,
                    "slice_24h_5m": context_24h,
                    "session_open_price": calibration[0]["open"],
                    "r30_high": max(c["high"] for c in calibration),
                    "r30_low": min(c["low"] for c in calibration),
                    "last_price": context_24h[-1]["close"] if context_24h else 0.0,
                    "tuning": {} 
                }
                
                computed = sse_engine.compute_sse_levels(sse_input)
                if "error" in computed: continue
                levels = computed["levels"]

                # 3. DUPLICATE MARKET RADAR MATH
                breakout = levels.get("breakout_trigger")
                breakdown = levels.get("breakdown_trigger")
                if not breakout or not breakdown: continue

                pole = breakout - breakdown
                r30_high = levels.get("range30m_high")
                r30_low = levels.get("range30m_low")

                # Trackers for the day
                triggered_dir = None
                trigger_idx = -1
                
                # A. Find the Trigger
                for i, c in enumerate(session_candles):
                    if c['high'] >= breakout:
                        triggered_dir = 'LONG'
                        trigger_idx = i
                        break
                    elif c['low'] <= breakdown:
                        triggered_dir = 'SHORT'
                        trigger_idx = i
                        break

                if not triggered_dir:
                    trade_log.append({"date": actual_session_date, "status": "NO TRIGGER", "msg": "Price stayed inside triggers."})
                    continue

                # B. Determine Entry and Stop
                entry_price = 0
                stop_loss = 0
                entry_idx = -1

                if entry_type == "instant":
                    entry_price = breakout if triggered_dir == 'LONG' else breakdown
                    entry_idx = trigger_idx
                    stop_loss = r30_low if triggered_dir == 'LONG' else r30_high

                elif entry_type == "15m_close":
                    # Wait for 15m candle close (minutes 10, 25, 40, 55 in Kabroda framework)
                    found = False
                    for i in range(trigger_idx, len(session_candles)):
                        c = session_candles[i]
                        minute = datetime.fromtimestamp(c["time"], tz=timezone.utc).minute
                        if minute in [10, 25, 40, 55]:
                            entry_price = c['close']
                            entry_idx = i
                            found = True
                            
                            # Calculate Stop based on selection
                            if stop_type == "15m_candle":
                                stop_loss = c['low'] if triggered_dir == 'LONG' else c['high']
                            else:
                                stop_loss = r30_low if triggered_dir == 'LONG' else r30_high
                            break
                    
                    if not found:
                        trade_log.append({"date": actual_session_date, "status": "NO ENTRY", "msg": "Triggered too late for 15m close."})
                        continue

                # C. Determine Target
                if target_type == "pole":
                    target = entry_price + pole if triggered_dir == 'LONG' else entry_price - pole
                else:
                    # Fallback if testing specific ATR targets later
                    target = entry_price + pole if triggered_dir == 'LONG' else entry_price - pole

                # D. Run the Trade Simulation
                result_status = "PENDING"
                for i in range(entry_idx + 1, len(session_candles)):
                    c = session_candles[i]
                    
                    # Check Stop Loss First (Pessimistic execution)
                    if triggered_dir == 'LONG' and c['low'] <= stop_loss:
                        result_status = "LOSS"
                        break
                    elif triggered_dir == 'SHORT' and c['high'] >= stop_loss:
                        result_status = "LOSS"
                        break
                    
                    # Check Target
                    if triggered_dir == 'LONG' and c['high'] >= target:
                        result_status = "WIN"
                        break
                    elif triggered_dir == 'SHORT' and c['low'] <= target:
                        result_status = "WIN"
                        break

                if result_status == "PENDING":
                    result_status = "LOSS" # Didn't hit target before session end

                if result_status == "WIN": wins += 1
                if result_status == "LOSS": losses += 1

                trade_log.append({
                    "date": actual_session_date,
                    "status": result_status,
                    "direction": triggered_dir,
                    "entry": round(entry_price, 1),
                    "target": round(target, 1),
                    "stop": round(stop_loss, 1),
                    "msg": f"Entered {triggered_dir} @ {round(entry_price,1)}. Target: {round(target,1)}, Stop: {round(stop_loss,1)}"
                })

            curr_day += timedelta(days=1)

        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

        return {
            "ok": True,
            "total_wins": wins,
            "total_losses": losses,
            "win_rate": round(win_rate, 2),
            "log": trade_log
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": f"Simulation Error: {str(e)}"}