# backtest_lab.py
# ==============================================================================
# KABRODA BACKTEST LAB (THE SIMULATOR)
# BASED ON: research_lab.py (Data Fetching Core)
# UPGRADE: Adds "Cartridge" Execution Logic for PnL Simulation
# ==============================================================================
import pandas as pd
from datetime import datetime, timedelta, timezone
import traceback

# CORE INFRASTRUCTURE (ReadOnly Access)
import session_manager
import sse_engine 
import battlebox_pipeline

# STRATEGY CARTRIDGES (Plug them in here)
import market_radar  # <--- CARTRIDGE #1

# --- HELPER: DATA SLICING ---
def _slice_by_ts(candles, start_ts, end_ts):
    return [c for c in candles if start_ts <= c["time"] < end_ts]

# --- HELPER: WEEKLY BIAS RECONSTRUCTION ---
def _reconstruct_weekly_bias(df, current_time_ts):
    """
    Looks back 7 days in the dataframe to determine the 'Weekly Force' 
    at that specific moment in history.
    """
    try:
        # Simple Logic: Current Open vs Open 7 days ago
        week_ago_ts = current_time_ts - (7 * 86400)
        
        if df.empty: return "NEUTRAL"
        
        # Convert TS to datetime for pandas lookup
        t_now = pd.to_datetime(current_time_ts, unit='s', utc=True)
        t_week = pd.to_datetime(week_ago_ts, unit='s', utc=True)
        
        # Get Price Now (Session Open)
        idx_now = df.index.get_indexer([t_now], method='nearest')[0]
        price_now = df.iloc[idx_now]['open']
        
        # Get Price Week Ago
        idx_week = df.index.get_indexer([t_week], method='nearest')[0]
        price_week = df.iloc[idx_week]['close']

        if price_now > price_week: return "BULLISH"
        if price_now < price_week: return "BEARISH"
        return "NEUTRAL"
    except:
        return "NEUTRAL"

# --- THE SIMULATION ENGINE ---
async def run_system_test(symbol, start_date, end_date, starting_balance=1000, strategy="MARKET_RADAR"):
    try:
        # 1. SETUP DATE RANGE & DATA FETCH
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        
        # Fetch timestamps (add 10 days buffer for context)
        s_ts = int(start_dt.timestamp()) - (86400 * 10)
        e_ts = int(end_dt.timestamp()) + 86400
        
        # FETCH RAW DATA (Using Existing Pipeline)
        raw_5m = await battlebox_pipeline.fetch_historical_pagination(symbol, s_ts, e_ts)
        
        if not raw_5m or len(raw_5m) < 100:
            return {"ok": False, "error": "Insufficient Data from Pipeline"}

        # CREATE DATAFRAME (Speed optimization for lookups)
        df = pd.DataFrame(raw_5m)
        df['time_dt'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df.set_index('time_dt', inplace=True)
        df.sort_index(inplace=True)

        # 2. SIMULATION STATE
        balance = float(starting_balance)
        equity_curve = []
        trade_log = []
        curr_day = start_dt
        
        # USE DEFAULT SESSION (NY Futures)
        cfg = session_manager.get_session_config("us_ny_futures")

        # 3. RUN THE TIMELINE (Day by Day)
        while curr_day <= end_dt:
            # A. Identify Session Anchor
            query_time = curr_day + timedelta(hours=14) 
            anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, query_time)
            
            # Time Windows
            lock_end_ts = anchor_ts + 1800  # 30 mins Calibration
            session_close_ts = anchor_ts + 86400 # Hold until next day
            
            # B. Get Data Slices
            calibration = _slice_by_ts(raw_5m, anchor_ts, lock_end_ts)
            context_24h = _slice_by_ts(raw_5m, lock_end_ts - 86400, lock_end_ts)
            
            if len(calibration) < 6: 
                curr_day += timedelta(days=1)
                continue

            # C. CALCULATE PHASE 1 (THE BATTLEBOX)
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
            if "error" in computed: 
                curr_day += timedelta(days=1)
                continue
            
            levels = computed["levels"]
            
            # D. DETERMINE WEEKLY FORCE
            bias = _reconstruct_weekly_bias(df, anchor_ts)
            context = {"weekly_force": bias}
            anchor_price = float(levels.get("anchor_price", 0))

            # --- E. INSERT CARTRIDGE (STRATEGY SWITCH) ---
            plan = None
            metrics = {}
            
            if strategy == "MARKET_RADAR":
                if anchor_price > 0:
                    metrics = market_radar._calc_kinetics(anchor_price, levels, context)
                    mode, advice, color = market_radar._get_status(symbol, metrics)
                    if metrics['score'] >= 50:
                        plan = market_radar._get_plan(mode, levels, metrics['bias'])
            
            # --- F. EXECUTE THE TRADE ---
            day_pnl = 0
            outcome = "NO TRADE"
            
            if plan and plan['valid']:
                # Get candles for the rest of the session
                session_candles = _slice_by_ts(raw_5m, lock_end_ts, session_close_ts)
                
                entry = plan['entry']
                stop = plan['stop']
                target = plan['targets'][0] if len(plan['targets']) > 0 else 0
                
                in_trade = False
                
                for c in session_candles:
                    h = c['high']
                    l = c['low']
                    
                    # CHECK ENTRY
                    if not in_trade:
                        if plan['bias'] == "LONG" and h >= entry:
                            in_trade = True
                        elif plan['bias'] == "SHORT" and l <= entry:
                            in_trade = True
                    
                    # CHECK EXIT
                    if in_trade:
                        if plan['bias'] == "LONG":
                            if l <= stop: 
                                pct_loss = (stop - entry) / entry
                                day_pnl = balance * pct_loss
                                outcome = "LOSS"
                                break
                            elif h >= target: 
                                pct_win = (target - entry) / entry
                                day_pnl = balance * pct_win
                                outcome = "WIN"
                                break
                        elif plan['bias'] == "SHORT":
                            if h >= stop: 
                                pct_loss = (entry - stop) / entry
                                day_pnl = balance * pct_loss
                                outcome = "LOSS"
                                break
                            elif l <= target: 
                                pct_win = (entry - target) / entry
                                day_pnl = balance * pct_win
                                outcome = "WIN"
                                break
            
            # G. UPDATE STATS
            if outcome != "NO TRADE":
                balance += day_pnl
                trade_log.append({
                    "date": curr_day.strftime("%Y-%m-%d"),
                    "bias": plan['bias'],
                    "result": outcome,
                    "pnl": round(day_pnl, 2),
                    "score": metrics.get('score', 0),
                    "balance": round(balance, 2)
                })
            
            equity_curve.append({"date": curr_day.strftime("%Y-%m-%d"), "balance": round(balance, 2)})
            curr_day += timedelta(days=1)

        # 4. FINAL REPORT
        return {
            "ok": True,
            "symbol": symbol,
            "strategy": strategy,
            "start_balance": starting_balance,
            "end_balance": round(balance, 2),
            "net_profit": round(balance - starting_balance, 2),
            "total_trades": len(trade_log),
            "trade_log": trade_log,
            "equity_curve": equity_curve
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}