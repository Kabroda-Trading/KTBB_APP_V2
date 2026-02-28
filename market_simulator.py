# market_simulator.py
# ==============================================================================
# KABRODA MARKET SIMULATOR v3.0 (TRUE RADAR DUPLICATION)
# JOB: Exact duplication of Market Radar logic to test historical probabilities.
# ==============================================================================
import pandas as pd
from datetime import datetime, timedelta, timezone
import traceback

# CORE ENGINES
import session_manager
import sse_engine 
import battlebox_pipeline

# -------------------------------------------------------------------------
# EXACT LOGIC DUPLICATED FROM MARKET_RADAR.PY
# -------------------------------------------------------------------------
def _get_thresholds(symbol):
    if "BTC" in symbol: return 0.5, 1.5, 2.25
    if "ETH" in symbol: return 0.8, 2.5, 3.50
    if "SOL" in symbol: return 1.5, 4.0, 6.00
    return 0.8, 2.0, 3.0

def _build_dossier(symbol, side, price, trigger, target_level, structure, ema30, ema50, pole, alt_edge):
    if not trigger or not target_level or price == 0:
        return {"status": "INVALID", "reason": "Missing Core Levels"}

    gap_pct = abs(target_level - trigger) / trigger * 100
    min_g, primal_max, exhaust_max = _get_thresholds(symbol)

    if gap_pct < min_g:
        return {"status": "NO TRADE", "reason": f"Gap Too Small ({gap_pct:.2f}%)"}
    elif gap_pct <= primal_max:
        tier = "PRIMAL"
    elif gap_pct <= exhaust_max:
        tier = "EXHAUSTION"
    else:
        return {"status": "NO TRADE", "reason": f"Beyond Exhaustion ({gap_pct:.2f}%)"}

    is_bullish = ema30 > ema50 if ema30 and ema50 else None
    align = "ALIGNED" if (side == "LONG" and is_bullish) or (side == "SHORT" and not is_bullish) else "COUNTER"

    if side == "LONG":
        t1 = target_level
        t2 = trigger + pole
        t3 = trigger + (pole * 1.618)
        stop = alt_edge
    else:
        t1 = target_level
        t2 = trigger - pole
        t3 = trigger - (pole * 1.618)
        stop = alt_edge

    risk = abs(trigger - stop)
    reward_t1 = abs(t1 - trigger)
    reward_t2 = abs(t2 - trigger)
    reward_t3 = abs(t3 - trigger)

    return {
        "status": "ACTIVE",
        "tier": tier,
        "alignment": align,
        "gap_pct": gap_pct,
        "structure": structure,
        "entry": trigger,
        "stop": stop,
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "rr_t1": reward_t1 / risk if risk else 0,
        "rr_t2": reward_t2 / risk if risk else 0,
        "rr_t3": reward_t3 / risk if risk else 0
    }

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

def _slice_by_ts(candles, start_ts, end_ts):
    return [c for c in candles if start_ts <= c["time"] < end_ts]

# -------------------------------------------------------------------------
# SIMULATION ENGINE
# -------------------------------------------------------------------------
async def run_simulation(payload: dict):
    symbol = payload.get("symbol", "BTCUSDT").strip().upper()
    start_date = payload.get("start_date_utc")
    end_date = payload.get("end_date_utc")
    session_ids = payload.get("session_ids", ["us_ny_futures"])
    
    # Simulation Parameters from UI
    target_to_test = payload.get("target_type", "t2") # Which target counts as a "Win" for the main stat
    entry_type = payload.get("entry_type", "15m_close") 
    stop_type = payload.get("stop_type", "radar_default") 
    min_tier = payload.get("min_tier", "ALL")

    if not start_date or not end_date:
        return {"ok": False, "error": "Start and End dates are required."}

    try:
        # Fetch Data
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
        
        # Stats
        stats = {
            "total_trades": 0, "skipped_gap": 0, "stops": 0,
            "t1_hits": 0, "t2_hits": 0, "t3_hits": 0,
            "main_wins": 0, "main_losses": 0
        }
        trade_log = []
        curr_day = start_dt
        
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

                d_ema30 = 0.0
                d_ema50 = 0.0
                try:
                    day_key = pd.to_datetime(actual_session_date).date()
                    if str(day_key) in df_daily_emas.index:
                        daily_row = df_daily_emas.loc[str(day_key)]
                        d_ema30 = daily_row['ema30']
                        d_ema50 = daily_row['ema50']
                except: pass

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

                breakout = levels.get("breakout_trigger")
                breakdown = levels.get("breakdown_trigger")
                r30_high = levels.get("range30m_high")
                r30_low = levels.get("range30m_low")
                daily_res = levels.get("daily_resistance")
                daily_sup = levels.get("daily_support")
                struct = levels.get("structure_score", 0)

                if not breakout or not breakdown: continue
                pole = breakout - breakdown

                # BUILD RADAR DOSSIERS
                long_dossier = _build_dossier(symbol, "LONG", sse_input["last_price"], breakout, daily_res, struct, d_ema30, d_ema50, pole, r30_low)
                short_dossier = _build_dossier(symbol, "SHORT", sse_input["last_price"], breakdown, daily_sup, struct, d_ema30, d_ema50, pole, r30_high)

                # Find the Trigger
                triggered_dir = None
                trigger_idx = -1
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
                    continue

                dossier = long_dossier if triggered_dir == 'LONG' else short_dossier

                # Skip if Radar says NO TRADE
                if dossier["status"] == "NO TRADE":
                    stats["skipped_gap"] += 1
                    trade_log.append({
                        "date": actual_session_date, "status": "SKIPPED", 
                        "msg": f"Skipped {triggered_dir}: {dossier['reason']}"
                    })
                    continue
                
                # Apply User Tier Filter
                if min_tier == "PRIMAL" and dossier["tier"] == "EXHAUSTION":
                    stats["skipped_gap"] += 1
                    continue

                # Determine Actual Entry Price & Dynamic Stop
                entry_price = 0
                stop_loss = dossier["stop"] # Default Radar Stop
                entry_idx = -1

                if entry_type == "instant":
                    entry_price = breakout if triggered_dir == 'LONG' else breakdown
                    entry_idx = trigger_idx
                elif entry_type == "15m_close":
                    found = False
                    for i in range(trigger_idx, len(session_candles)):
                        c = session_candles[i]
                        minute = datetime.fromtimestamp(c["time"], tz=timezone.utc).minute
                        if minute in [10, 25, 40, 55]:
                            entry_price = c['close']
                            entry_idx = i
                            found = True
                            if stop_type == "15m_candle":
                                stop_loss = c['low'] if triggered_dir == 'LONG' else c['high']
                            break
                    if not found:
                        continue

                # RUN THE TRADE (Watch for Stop and Targets)
                hit_t1, hit_t2, hit_t3, stopped = False, False, False, False
                
                for i in range(entry_idx + 1, len(session_candles)):
                    c = session_candles[i]
                    
                    # Check Stop Loss First
                    if triggered_dir == 'LONG' and c['low'] <= stop_loss:
                        stopped = True; break
                    elif triggered_dir == 'SHORT' and c['high'] >= stop_loss:
                        stopped = True; break
                    
                    # Check Targets
                    if triggered_dir == 'LONG':
                        if c['high'] >= dossier['t1']: hit_t1 = True
                        if c['high'] >= dossier['t2']: hit_t2 = True
                        if c['high'] >= dossier['t3']: hit_t3 = True
                    else:
                        if c['low'] <= dossier['t1']: hit_t1 = True
                        if c['low'] <= dossier['t2']: hit_t2 = True
                        if c['low'] <= dossier['t3']: hit_t3 = True
                    
                    if hit_t3: break # Max target hit, done

                # Tally Results
                stats["total_trades"] += 1
                if stopped: stats["stops"] += 1
                if hit_t1: stats["t1_hits"] += 1
                if hit_t2: stats["t2_hits"] += 1
                if hit_t3: stats["t3_hits"] += 1

                # Determine Main Win/Loss based on user selection
                is_win = False
                if target_to_test == "t1" and hit_t1: is_win = True
                elif target_to_test == "t2" and hit_t2: is_win = True
                elif target_to_test == "t3" and hit_t3: is_win = True
                
                if is_win: stats["main_wins"] += 1
                else: stats["main_losses"] += 1

                status_str = "WIN" if is_win else ("LOSS (STOP)" if stopped else "LOSS (TIME)")

                trade_log.append({
                    "date": actual_session_date,
                    "status": status_str,
                    "msg": f"{triggered_dir} {dossier['tier']} | Target Tested: {target_to_test.upper()} | Hit: T1:{hit_t1} T2:{hit_t2} T3:{hit_t3}"
                })

            curr_day += timedelta(days=1)

        win_rate = (stats["main_wins"] / stats["total_trades"] * 100) if stats["total_trades"] > 0 else 0

        return {
            "ok": True,
            "win_rate": round(win_rate, 2),
            "stats": stats,
            "log": trade_log
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": f"Simulation Error: {str(e)}"}