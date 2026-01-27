# backtest_lab.py
# ==============================================================================
# KABRODA BACKTEST LAB v2.0 (PRO METRICS UPGRADE)
# BASED ON: research_lab.py (Data Fetching Core)
# UPGRADE: Adds "Cartridge" Logic + Wall Street Metrics (Drawdown, PF, Streaks)
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
    try:
        week_ago_ts = current_time_ts - (7 * 86400)
        if df.empty: return "NEUTRAL"
        
        t_now = pd.to_datetime(current_time_ts, unit='s', utc=True)
        t_week = pd.to_datetime(week_ago_ts, unit='s', utc=True)
        
        idx_now = df.index.get_indexer([t_now], method='nearest')[0]
        price_now = df.iloc[idx_now]['open']
        
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
        print(f"--- INITIALIZING BACKTEST LAB: {symbol} [{strategy}] ---")
        
        # 1. SETUP DATE RANGE & DATA FETCH
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        
        s_ts = int(start_dt.timestamp()) - (86400 * 10)
        e_ts = int(end_dt.timestamp()) + 86400
        
        # FETCH RAW DATA
        raw_5m = await battlebox_pipeline.fetch_historical_pagination(symbol, s_ts, e_ts)
        
        if not raw_5m or len(raw_5m) < 100:
            return {"ok": False, "error": "Insufficient Data from Pipeline"}

        # CREATE DATAFRAME
        df = pd.DataFrame(raw_5m)
        df['time_dt'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df.set_index('time_dt', inplace=True)
        df.sort_index(inplace=True)

        # 2. SIMULATION STATE
        balance = float(starting_balance)
        equity_curve = [{"date": start_date, "balance": balance}]
        trade_log = []
        curr_day = start_dt
        
        cfg = session_manager.get_session_config("us_ny_futures")

        # 3. RUN THE TIMELINE
        while curr_day <= end_dt:
            query_time = curr_day + timedelta(hours=14) 
            anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, query_time)
            
            lock_end_ts = anchor_ts + 1800
            session_close_ts = anchor_ts + 86400
            
            calibration = _slice_by_ts(raw_5m, anchor_ts, lock_end_ts)
            context_24h = _slice_by_ts(raw_5m, lock_end_ts - 86400, lock_end_ts)
            
            if len(calibration) < 6: 
                curr_day += timedelta(days=1)
                continue

            # C. CALCULATE PHASE 1
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
            bias = _reconstruct_weekly_bias(df, anchor_ts)
            context = {"weekly_force": bias}
            anchor_price = float(levels.get("anchor_price", 0))

            # --- E. INSERT CARTRIDGE ---
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
                        if plan['bias'] == "LONG" and h >= entry: in_trade = True
                        elif plan['bias'] == "SHORT" and l <= entry: in_trade = True
                    
                    # CHECK EXIT
                    if in_trade:
                        if plan['bias'] == "LONG":
                            if l <= stop: 
                                day_pnl = balance * ((stop - entry) / entry)
                                outcome = "LOSS"
                                break
                            elif h >= target: 
                                day_pnl = balance * ((target - entry) / entry)
                                outcome = "WIN"
                                break
                        elif plan['bias'] == "SHORT":
                            if h >= stop: 
                                day_pnl = balance * ((entry - stop) / entry)
                                outcome = "LOSS"
                                break
                            elif l <= target: 
                                day_pnl = balance * ((entry - target) / entry)
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
            
            # Only update equity curve if day is done
            equity_curve.append({"date": curr_day.strftime("%Y-%m-%d"), "balance": round(balance, 2)})
            curr_day += timedelta(days=1)

        # --- H. PRO METRICS CALCULATION (THE WALL STREET AUDIT) ---
        
        # 1. MAX DRAWDOWN
        peak = starting_balance
        max_dd = 0
        for pt in equity_curve:
            if pt["balance"] > peak: peak = pt["balance"]
            dd = (peak - pt["balance"]) / peak
            if dd > max_dd: max_dd = dd
            
        # 2. PROFIT FACTOR
        gross_win = sum(t['pnl'] for t in trade_log if t['pnl'] > 0)
        gross_loss = abs(sum(t['pnl'] for t in trade_log if t['pnl'] < 0))
        profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.99

        # 3. STREAKS
        curr_win_streak = 0
        max_win_streak = 0
        curr_loss_streak = 0
        max_loss_streak = 0
        
        for t in trade_log:
            if t['result'] == 'WIN':
                curr_win_streak += 1
                curr_loss_streak = 0
                if curr_win_streak > max_win_streak: max_win_streak = curr_win_streak
            elif t['result'] == 'LOSS':
                curr_loss_streak += 1
                curr_win_streak = 0
                if curr_loss_streak > max_loss_streak: max_loss_streak = curr_loss_streak

        # 4. FINAL REPORT PACKET
        return {
            "ok": True,
            "symbol": symbol,
            "strategy": strategy,
            "start_balance": starting_balance,
            "end_balance": round(balance, 2),
            "net_profit": round(balance - starting_balance, 2),
            "total_trades": len(trade_log),
            # NEW PRO METRICS
            "max_drawdown_pct": round(max_dd * 100, 2),
            "profit_factor": profit_factor,
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            # ARRAYS
            "trade_log": trade_log,
            "equity_curve": equity_curve
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}