# market_simulator.py
# ==============================================================================
# KABRODA MARKET SIMULATOR v5.0 (TRUE RADAR CLONE)
# JOB: Exact 1:1 duplication of Market Radar math wrapped in a historical 
#      execution engine with "What-If" parameter overrides.
# ==============================================================================
import pandas as pd
from datetime import datetime, timedelta, timezone
import traceback

import session_manager
import sse_engine 
import battlebox_pipeline

# ==============================================================================
# 1. EXACT MARKET RADAR MATH (DUPLICATED)
# ==============================================================================

def _get_thresholds(symbol, overrides=None):
    """Returns (Min Gap %, Primal Max %, Exhaustion Max %, Allow Jailbreaks)"""
    if overrides and overrides.get("use_custom"):
        return (
            float(overrides.get("min_gap", 0.5)),
            float(overrides.get("primal_max", 1.5)),
            float(overrides.get("exhaust_max", 2.25)),
            bool(overrides.get("allow_jb", True))
        )

    if "BTC" in symbol: return 0.5, 1.5, 2.25, True
    if "ETH" in symbol: return 0.8, 2.5, 3.50, True
    if "SOL" in symbol: return 1.5, 4.0, 6.00, True
    return 0.8, 2.5, 3.5, False

def _find_predator_stop(symbol, entry, direction, levels, verdict):
    pred_h = float(levels.get("range30m_high", 0))
    pred_l = float(levels.get("range30m_low", 0))

    if "SOL" in symbol:
        if direction == "LONG": return pred_l if pred_l > 0 else entry * 0.98
        elif direction == "SHORT": return pred_h if pred_h > 0 else entry * 1.02
        return 0
        
    if "ETH" in symbol:
        if "JAILBREAK" in verdict: 
            return pred_l if direction == "LONG" and pred_l > 0 else (pred_h if direction == "SHORT" and pred_h > 0 else entry)
        else:
            eth_buffer = entry * 0.002
            return entry - eth_buffer if direction == "LONG" else entry + eth_buffer

    if "SNIPER" in verdict:
        if direction == "SHORT": return entry * 1.017
        if direction == "LONG": return entry * 0.983

    btc_buffer = entry * 0.001 
    if direction == "LONG":
        if pred_l > 0 and pred_l < entry: return pred_l - btc_buffer
        return entry * 0.99 
    elif direction == "SHORT":
        if pred_h > 0 and pred_h > entry: return pred_h + btc_buffer
        return entry * 1.01 
        
    return 0

def _eval_side(symbol, anchor, trigger, wall, is_inverted, overrides=None):
    if anchor == 0 or trigger == 0: return 0.0, "WAITING"
    min_gap, primal_max, exhaust_max, allow_jb = _get_thresholds(symbol, overrides)
    gap_pct = (abs(wall - trigger) / anchor) * 100
    
    if is_inverted:
        if gap_pct < min_gap: return gap_pct, "DEATH ZONE (TOO TIGHT)"
        if allow_jb: return gap_pct, "JAILBREAK"
        return gap_pct, "DEATH ZONE (UNCONFIRMED)"
    else:
        if gap_pct < min_gap: return gap_pct, "DEATH ZONE (CHOP)"
        if gap_pct > exhaust_max: return gap_pct, "DEATH ZONE (EXHAUSTION)"
        if gap_pct > primal_max: return gap_pct, "EXTENDED MAGNET"
        return gap_pct, "MAGNET"

def _get_plan(symbol, anchor, vector, tier, levels):
    plan = {"valid": False, "bias": vector, "entry": 0, "stop": 0, "targets": [0,0,0]}
    if "WAITING" in tier or "DEATH ZONE" in tier: return plan
    
    bo = float(levels.get("breakout_trigger", 0))
    bd = float(levels.get("breakdown_trigger", 0))
    dr = float(levels.get("daily_resistance", 0))
    ds = float(levels.get("daily_support", 0))
    
    entry_price = anchor if "SNIPER" in tier else (bo if vector == "LONG" else bd)
    stop_price = _find_predator_stop(symbol, entry_price, vector, levels, tier)
    
    if "SNIPER" in tier:
        if vector == "LONG":
            t1, t2, t3 = entry_price * 1.02, dr, entry_price * 1.05
        else:
            t1, t2, t3 = entry_price * 0.98, ds, entry_price * 0.95
    else:
        gap = abs(bo - bd) or (entry_price * 0.02)
        if vector == "LONG":
            t1, t2, t3 = entry_price + (gap * 0.618), entry_price + gap, entry_price + (gap * 1.618)
        else:
            t1, t2, t3 = entry_price - (gap * 0.618), entry_price - gap, entry_price - (gap * 1.618)
    
    plan.update({"valid": True, "entry": entry_price, "stop": stop_price, "targets": [t1, t2, t3]})
    return plan

# ==============================================================================
# 2. HISTORICAL DATA RECONSTRUCTION HELPER
# ==============================================================================
def _slice_by_ts(candles, start_ts, end_ts):
    return [c for c in candles if start_ts <= c["time"] < end_ts]

def _calc_momentum(df, current_ts, hours_back):
    try:
        past_ts = current_ts - (hours_back * 3600)
        idx_past = df.index.get_indexer([pd.to_datetime(past_ts, unit='s', utc=True)], method='nearest')[0]
        idx_curr = df.index.get_indexer([pd.to_datetime(current_ts, unit='s', utc=True)], method='nearest')[0]
        return "BULLISH" if df.iloc[idx_curr]['open'] > df.iloc[idx_past]['close'] else "BEARISH"
    except:
        return "NEUTRAL"

# ==============================================================================
# 3. THE SIMULATION LOOP
# ==============================================================================
async def run_simulation(payload: dict):
    symbol = payload.get("symbol", "BTCUSDT").strip().upper()
    start_date = payload.get("start_date_utc")
    end_date = payload.get("end_date_utc")
    session_ids = payload.get("session_ids", ["us_ny_futures"])
    
    # User Scenarios
    entry_style = payload.get("entry_style", "15m_close") # instant, 15m_close, pullback
    stop_style = payload.get("stop_style", "radar_default") # radar_default, 15m_candle
    
    # What-If Overrides
    overrides = {
        "use_custom": payload.get("use_custom_gaps", False),
        "min_gap": float(payload.get("min_gap", 0.5)),
        "primal_max": float(payload.get("primal_max", 1.5)),
        "exhaust_max": float(payload.get("exhaust_max", 2.25)),
        "allow_jb": payload.get("allow_jb", True)
    }

    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        fetch_start_dt = start_dt - timedelta(days=10) # Enough for 168h momentum
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        
        raw_5m = await battlebox_pipeline.fetch_historical_pagination(
            symbol, int(fetch_start_dt.timestamp()), int(end_dt.timestamp())
        )
        if not raw_5m: return {"ok": False, "error": "No data found"}

        df = pd.DataFrame(raw_5m)
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df.set_index('time', inplace=True)
        df.sort_index(inplace=True)

        active_cfgs = [s for s in session_manager.SESSION_CONFIGS if s["id"] in session_ids]
        
        stats = {"total_trades": 0, "skipped": 0, "wins_t1": 0, "wins_t2": 0, "wins_t3": 0, "stops": 0}
        trade_log = []
        curr_day = start_dt
        
        while curr_day <= end_dt:
            query_time = curr_day + timedelta(hours=12)
            for cfg in active_cfgs:
                anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, query_time)
                actual_date = datetime.fromtimestamp(anchor_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                lock_end_ts = anchor_ts + 1800  
                exec_end_ts = lock_end_ts + (12 * 3600) 

                calibration = _slice_by_ts(raw_5m, anchor_ts, lock_end_ts)
                if len(calibration) < 6: continue 
                
                context_24h = _slice_by_ts(raw_5m, lock_end_ts - 86400, lock_end_ts)
                session_candles = _slice_by_ts(raw_5m, lock_end_ts, exec_end_ts)
                anchor_price = calibration[0]["open"]

                # A. Reconstruct Levels
                sse_input = {
                    "locked_history_5m": context_24h,
                    "session_open_price": anchor_price,
                    "r30_high": max(c["high"] for c in calibration),
                    "r30_low": min(c["low"] for c in calibration),
                    "last_price": context_24h[-1]["close"],
                    "tuning": {}
                }
                computed = sse_engine.compute_sse_levels(sse_input)
                lvls = computed.get("levels", {})
                
                bo = float(lvls.get("breakout_trigger", 0))
                bd = float(lvls.get("breakdown_trigger", 0))
                dr = float(lvls.get("daily_resistance", 0))
                ds = float(lvls.get("daily_support", 0))
                if not bo or not bd: continue

                # B. Query Market Radar Brain
                l_gap, l_tier = _eval_side(symbol, anchor_price, bo, dr, (bo > dr and dr > 0), overrides)
                s_gap, s_tier = _eval_side(symbol, anchor_price, bd, ds, (bd < ds and ds > 0), overrides)
                
                l_plan = _get_plan(symbol, anchor_price, "LONG", l_tier, lvls)
                s_plan = _get_plan(symbol, anchor_price, "SHORT", s_tier, lvls)

                # C. Execution Engine (ONE AND DONE RULE)
                trade_taken = False
                triggered_dir = None
                plan = None
                tier = ""

                for i, c in enumerate(session_candles):
                    # Find the absolute first trigger breach
                    if not trade_taken:
                        if c['high'] >= bo:
                            triggered_dir = "LONG"
                            plan = l_plan
                            tier = l_tier
                            trigger_idx = i
                            trade_taken = True
                        elif c['low'] <= bd:
                            triggered_dir = "SHORT"
                            plan = s_plan
                            tier = s_tier
                            trigger_idx = i
                            trade_taken = True
                    
                    if trade_taken:
                        break # Break loop to process the locked trade

                if not trade_taken:
                    continue # Day ended with no triggers hit

                if not plan['valid']:
                    stats["skipped"] += 1
                    trade_log.append({"date": actual_date, "status": "SKIPPED", "msg": f"Triggered {triggered_dir}, but Radar ruled: {tier}"})
                    continue

                # Execute Entry Style
                actual_entry = 0
                actual_stop = plan['stop']
                exec_idx = -1

                if entry_style == "instant":
                    actual_entry = plan['entry']
                    exec_idx = trigger_idx

                elif entry_style == "15m_close" or entry_style == "pullback":
                    # Wait for the current 15m block to close
                    for j in range(trigger_idx, len(session_candles)):
                        cc = session_candles[j]
                        minute = datetime.fromtimestamp(cc["time"], tz=timezone.utc).minute
                        if minute in [10, 25, 40, 55]:
                            # This is the 15m close
                            if entry_style == "15m_close":
                                actual_entry = cc['close']
                                exec_idx = j
                                if stop_style == "15m_candle":
                                    actual_stop = cc['low'] if triggered_dir == "LONG" else cc['high']
                            elif entry_style == "pullback":
                                # We have the close. Now we set a limit order at the exact trigger line.
                                limit_price = plan['entry']
                                for k in range(j + 1, len(session_candles)):
                                    pc = session_candles[k]
                                    if (triggered_dir == "LONG" and pc['low'] <= limit_price) or \
                                       (triggered_dir == "SHORT" and pc['high'] >= limit_price):
                                        actual_entry = limit_price
                                        exec_idx = k
                                        if stop_style == "15m_candle":
                                            actual_stop = cc['low'] if triggered_dir == "LONG" else cc['high']
                                        break
                            break
                
                if exec_idx == -1 or actual_entry == 0:
                    trade_log.append({"date": actual_date, "status": "MISSED ENTRY", "msg": f"{triggered_dir} triggered but entry criteria ({entry_style}) never met."})
                    continue

                # Run the Trade to Conclusion
                hit_t1, hit_t2, hit_t3, stopped = False, False, False, False
                for j in range(exec_idx + 1, len(session_candles)):
                    tc = session_candles[j]
                    
                    if (triggered_dir == 'LONG' and tc['low'] <= actual_stop) or \
                       (triggered_dir == 'SHORT' and tc['high'] >= actual_stop):
                        stopped = True
                        break
                    
                    if triggered_dir == 'LONG':
                        if tc['high'] >= plan['targets'][0]: hit_t1 = True
                        if tc['high'] >= plan['targets'][1]: hit_t2 = True
                        if tc['high'] >= plan['targets'][2]: hit_t3 = True
                    else:
                        if tc['low'] <= plan['targets'][0]: hit_t1 = True
                        if tc['low'] <= plan['targets'][1]: hit_t2 = True
                        if tc['low'] <= plan['targets'][2]: hit_t3 = True
                    
                    if hit_t3: break

                # Tally
                stats["total_trades"] += 1
                if stopped: stats["stops"] += 1
                elif hit_t3: stats["wins_t3"] += 1
                elif hit_t2: stats["wins_t2"] += 1
                elif hit_t1: stats["wins_t1"] += 1

                res_str = "STOPPED OUT" if stopped else ("HIT T3" if hit_t3 else ("HIT T2" if hit_t2 else ("HIT T1" if hit_t1 else "TIME EXPIRED")))

                trade_log.append({
                    "date": actual_date,
                    "status": res_str,
                    "msg": f"{triggered_dir} | {tier} | In @ {actual_entry:.1f} | Stop @ {actual_stop:.1f} | T1:{hit_t1} T2:{hit_t2} T3:{hit_t3}"
                })

            curr_day += timedelta(days=1)

        return {"ok": True, "stats": stats, "log": trade_log}

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}