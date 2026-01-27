# backtest_lab.py
# ==============================================================================
# KABRODA BACKTEST LAB v3.0 (PORTFOLIO EDITION)
# CAPABILITY: Runs Single, Dual, or Triple Threat Simulations
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
        
        # Use 'nearest' to find closest candles in history
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
    """Fetches raw data for a single asset and prepares the DataFrame"""
    raw = await battlebox_pipeline.fetch_historical_pagination(symbol, s_ts, e_ts)
    if not raw or len(raw) < 100: return None
    
    df = pd.DataFrame(raw)
    df['time_dt'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df.set_index('time_dt', inplace=True)
    df.sort_index(inplace=True)
    return {"raw": raw, "df": df}

# --- THE PORTFOLIO ENGINE ---
async def run_system_test(symbol, start_date, end_date, starting_balance=1000, strategy="MARKET_RADAR"):
    # NOTE: 'symbol' arg can be a string "BTCUSDT" or a list ["BTCUSDT", "ETHUSDT"]
    # The API payload maps 'symbol' to this argument.
    
    try:
        # Handle Input: Support both string and list
        target_symbols = symbol if isinstance(symbol, list) else [symbol]
        
        print(f"--- INITIALIZING PORTFOLIO SIM: {target_symbols} ---")

        # 1. SETUP DATES
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        
        # Add buffer for weekly lookback
        s_ts = int(start_dt.timestamp()) - (86400 * 10)
        e_ts = int(end_dt.timestamp()) + 86400

        # 2. PRE-FETCH DATA FOR ALL TARGETS (Parallel)
        fetch_tasks = [_fetch_asset_data(sym, s_ts, e_ts) for sym in target_symbols]
        results = await asyncio.gather(*fetch_tasks)
        
        # Map Data: {"BTCUSDT": {df:..., raw:...}, "ETHUSDT": ...}
        market_data = {}
        for sym, res in zip(target_symbols, results):
            if res: market_data[sym] = res
            else: print(f"WARN: No data for {sym}, skipping.")
            
        if not market_data:
            return {"ok": False, "error": "No valid data found for any target."}

        # 3. SIMULATION LOOP
        balance = float(starting_balance)
        equity_curve = [{"date": start_date, "balance": balance}]
        trade_log = []
        curr_day = start_dt
        
        cfg = session_manager.get_session_config("us_ny_futures")

        while curr_day <= end_dt:
            daily_total_pnl = 0
            
            # --- LOOP THROUGH EACH ASSET FOR THIS DAY ---
            for sym in market_data.keys():
                asset_pack = market_data[sym]
                raw_5m = asset_pack["raw"]
                df = asset_pack["df"]

                # A. Identify Session Anchor
                query_time = curr_day + timedelta(hours=14) 
                anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, query_time)
                
                lock_end_ts = anchor_ts + 1800
                session_close_ts = anchor_ts + 86400
                
                # B. Slices
                # Manual slice for speed since we have the full array
                calibration = [c for c in raw_5m if anchor_ts <= c["time"] < lock_end_ts]
                context_24h = [c for c in raw_5m if (lock_end_ts - 86400) <= c["time"] < lock_end_ts]
                
                if len(calibration) < 6: continue 

                # C. Phase 1 Calc
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
                bias = _reconstruct_weekly_bias(df, anchor_ts)
                context = {"weekly_force": bias}
                anchor_price = float(levels.get("anchor_price", 0))

                # D. Cartridge Logic
                plan = None
                metrics = {}
                
                if strategy == "MARKET_RADAR" and anchor_price > 0:
                    metrics = market_radar._calc_kinetics(anchor_price, levels, context)
                    mode, advice, color = market_radar._get_status(sym, metrics)
                    # SCORE FILTER: >= 50
                    if metrics['score'] >= 50:
                        plan = market_radar._get_plan(mode, levels, metrics['bias'])

                # E. Execution
                outcome = "NO TRADE"
                trade_pnl = 0
                
                if plan and plan['valid']:
                    session_candles = [c for c in raw_5m if lock_end_ts <= c["time"] < session_close_ts]
                    entry = plan['entry']
                    stop = plan['stop']
                    target = plan['targets'][0] if len(plan['targets']) > 0 else 0
                    
                    in_trade = False
                    for c in session_candles:
                        h, l = c['high'], c['low']
                        # Entry
                        if not in_trade:
                            if (plan['bias'] == "LONG" and h >= entry) or (plan['bias'] == "SHORT" and l <= entry):
                                in_trade = True
                        # Exit
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
                
                # F. Record Asset Result
                if outcome != "NO TRADE":
                    daily_total_pnl += trade_pnl
                    trade_log.append({
                        "date": curr_day.strftime("%Y-%m-%d"),
                        "symbol": sym, # Track which asset fired
                        "bias": plan['bias'],
                        "result": outcome,
                        "pnl": round(trade_pnl, 2),
                        "score": metrics.get('score', 0),
                        "balance": round(balance + daily_total_pnl, 2)
                    })

            # End of Day: Update Global Balance
            balance += daily_total_pnl
            equity_curve.append({"date": curr_day.strftime("%Y-%m-%d"), "balance": round(balance, 2)})
            curr_day += timedelta(days=1)

        # --- METRICS (PRO UPGRADE) ---
        peak = starting_balance
        max_dd = 0
        for pt in equity_curve:
            if pt["balance"] > peak: peak = pt["balance"]
            dd = (peak - pt["balance"]) / peak
            if dd > max_dd: max_dd = dd
            
        gross_win = sum(t['pnl'] for t in trade_log if t['pnl'] > 0)
        gross_loss = abs(sum(t['pnl'] for t in trade_log if t['pnl'] < 0))
        profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.99

        # Streaks
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

        return {
            "ok": True,
            "symbol": "PORTFOLIO" if len(target_symbols) > 1 else target_symbols[0],
            "strategy": strategy,
            "start_balance": starting_balance,
            "end_balance": round(balance, 2),
            "net_profit": round(balance - starting_balance, 2),
            "total_trades": len(trade_log),
            # PRO METRICS
            "max_drawdown_pct": round(max_dd * 100, 2),
            "profit_factor": profit_factor,
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            # DATA
            "trade_log": trade_log,
            "equity_curve": equity_curve
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}