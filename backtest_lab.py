# backtest_lab.py
# ==============================================================================
# KABRODA BACKTEST LAB v4.0 (AUDIT EDITION)
# CAPABILITY: "Glass Box" Logging - Records EVERY decision, even "Hold Fire"
# ==============================================================================
import pandas as pd
from datetime import datetime, timedelta, timezone
import traceback
import asyncio

# INFRASTRUCTURE
import session_manager
import sse_engine 
import battlebox_pipeline

# CARTRIDGE
import market_radar

# --- HELPER: WEEKLY BIAS ---
def _reconstruct_weekly_bias(df, current_time_ts):
    try:
        week_ago_ts = current_time_ts - (7 * 86400)
        if df.empty: return "NEUTRAL"
        
        t_now = pd.to_datetime(current_time_ts, unit='s', utc=True)
        t_week = pd.to_datetime(week_ago_ts, unit='s', utc=True)
        
        try:
            idx_now = df.index.get_indexer([t_now], method='nearest')[0]
            price_now = df.iloc[idx_now]['open']
            
            idx_week = df.index.get_indexer([t_week], method='nearest')[0]
            price_week = df.iloc[idx_week]['close']
        except:
            return "NEUTRAL"

        if price_now > price_week: return "BULLISH"
        if price_now < price_week: return "BEARISH"
        return "NEUTRAL"
    except:
        return "NEUTRAL"

# --- HELPER: DATA FETCHER ---
async def _fetch_asset_data(symbol, s_ts, e_ts):
    raw = await battlebox_pipeline.fetch_historical_pagination(symbol, s_ts, e_ts)
    if not raw or len(raw) < 100: return None
    df = pd.DataFrame(raw)
    df['time_dt'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df.set_index('time_dt', inplace=True)
    df.sort_index(inplace=True)
    return {"raw": raw, "df": df}

# --- THE AUDIT ENGINE ---
async def run_system_test(symbol, start_date, end_date, starting_balance=1000, strategy="MARKET_RADAR"):
    try:
        target_symbols = symbol if isinstance(symbol, list) else [symbol]
        
        # 1. SETUP DATES
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        s_ts = int(start_dt.timestamp()) - (86400 * 10)
        e_ts = int(end_dt.timestamp()) + 86400

        # 2. DATA FETCH
        fetch_tasks = [_fetch_asset_data(sym, s_ts, e_ts) for sym in target_symbols]
        results = await asyncio.gather(*fetch_tasks)
        
        market_data = {}
        for sym, res in zip(target_symbols, results):
            if res: market_data[sym] = res
            
        if not market_data: return {"ok": False, "error": "No valid data found."}

        # 3. SIMULATION LOOP
        balance = float(starting_balance)
        equity_curve = [{"date": start_date, "balance": balance}]
        
        # CHANGED: 'mission_log' now records EVERY day, not just trades
        mission_log = [] 
        curr_day = start_dt
        
        cfg = session_manager.get_session_config("us_ny_futures")

        while curr_day <= end_dt:
            daily_pnl = 0
            
            for sym in market_data.keys():
                asset_pack = market_data[sym]
                raw_5m = asset_pack["raw"]
                df = asset_pack["df"]

                # A. Session Setup
                query_time = curr_day + timedelta(hours=14) 
                anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, query_time)
                lock_end_ts = anchor_ts + 1800
                session_close_ts = anchor_ts + 86400
                
                # B. Data Slices
                calibration = [c for c in raw_5m if anchor_ts <= c["time"] < lock_end_ts]
                context_24h = [c for c in raw_5m if (lock_end_ts - 86400) <= c["time"] < lock_end_ts]
                if len(calibration) < 6: continue 

                # C. Battlebox Calc
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
                
                # D. Context & Radar
                bias = _reconstruct_weekly_bias(df, anchor_ts)
                anchor_price = float(levels.get("anchor_price", 0))
                
                plan = None
                metrics = {"score": 0, "energy": 0, "wind": 0, "hull": 0, "space": 0}
                mode = "OFFLINE"
                advice = "NO DATA"
                
                if strategy == "MARKET_RADAR" and anchor_price > 0:
                    # 1. KINETICS (The Math)
                    metrics = market_radar._calc_kinetics(anchor_price, levels, {"weekly_force": bias})
                    # 2. DECISION
                    mode, advice, color = market_radar._get_status(sym, metrics)
                    # 3. FILTER
                    if metrics['score'] >= 50:
                        plan = market_radar._get_plan(mode, levels, metrics['bias'])

                # E. Execution (Only if Valid)
                outcome = "HOLD FIRE"
                trade_pnl = 0
                
                if plan and plan['valid']:
                    session_candles = [c for c in raw_5m if lock_end_ts <= c["time"] < session_close_ts]
                    entry = plan['entry']
                    stop = plan['stop']
                    target = plan['targets'][0] if len(plan['targets']) > 0 else 0
                    
                    in_trade = False
                    for c in session_candles:
                        h, l = c['high'], c['low']
                        if not in_trade:
                            if (plan['bias'] == "LONG" and h >= entry) or (plan['bias'] == "SHORT" and l <= entry):
                                in_trade = True
                        if in_trade:
                            if plan['bias'] == "LONG":
                                if l <= stop: 
                                    trade_pnl = balance * ((stop - entry) / entry)
                                    outcome = "LOSS"
                                    break
                                elif h >= target: 
                                    trade_pnl = balance * ((target - entry) / entry)
                                    outcome = "WIN"
                                    break
                            elif plan['bias'] == "SHORT":
                                if h >= stop: 
                                    trade_pnl = balance * ((entry - stop) / entry)
                                    outcome = "LOSS"
                                    break
                                elif l <= target: 
                                    trade_pnl = balance * ((entry - target) / entry)
                                    outcome = "WIN"
                                    break
                    
                    if outcome == "HOLD FIRE": outcome = "NO FILL" # Setup valid, but entry never hit

                # F. LOG EVERYTHING (The "Glass Box")
                daily_pnl += trade_pnl
                mission_log.append({
                    "date": curr_day.strftime("%Y-%m-%d"),
                    "symbol": sym,
                    "score": metrics.get('score', 0),
                    "status": mode,     # "ASSAULT" or "HOLD FIRE"
                    "advice": advice,   # "PATH OBSTRUCTED", etc.
                    "metrics": metrics, # {energy, wind, hull...}
                    "result": outcome,  # WIN, LOSS, HOLD FIRE
                    "pnl": round(trade_pnl, 2),
                    "balance": round(balance + daily_pnl, 2)
                })

            balance += daily_pnl
            equity_curve.append({"date": curr_day.strftime("%Y-%m-%d"), "balance": round(balance, 2)})
            curr_day += timedelta(days=1)

        # METRICS CALCULATION
        peak = starting_balance
        max_dd = 0
        for pt in equity_curve:
            if pt["balance"] > peak: peak = pt["balance"]
            dd = (peak - pt["balance"]) / peak
            if dd > max_dd: max_dd = dd
            
        executed_trades = [t for t in mission_log if t['result'] in ['WIN', 'LOSS']]
        gross_win = sum(t['pnl'] for t in executed_trades if t['pnl'] > 0)
        gross_loss = abs(sum(t['pnl'] for t in executed_trades if t['pnl'] < 0))
        profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.99

        return {
            "ok": True,
            "net_profit": round(balance - starting_balance, 2),
            "end_balance": round(balance, 2),
            "start_balance": starting_balance,
            "total_trades": len(executed_trades),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "profit_factor": profit_factor,
            "equity_curve": equity_curve,
            "mission_log": mission_log # NOW CONTAINS EVERYTHING
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}