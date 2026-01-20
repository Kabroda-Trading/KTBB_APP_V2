# research_lab.py
# ==============================================================================
# RESEARCH LAB: HYBRID ENGINE (Structure + Kinetics)
# ==============================================================================
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import traceback

# CORE ENGINES
import session_manager
import sse_engine
import structure_state_engine
import battlebox_rules

def _slice_by_ts(candles, start_ts, end_ts):
    return [c for c in candles if start_ts <= c["time"] < end_ts]

def _calculate_kinetic_score(row, sensors):
    """Calculates the 0-100 score based on enabled sensors."""
    if row is None or pd.isna(row['ma']): return 0, {}
    
    score = 0
    comps = {"energy":0, "space":0, "wind":0, "hull":0}

    # 1. Energy (Inverse: Low BB Width = Coiled)
    if sensors.get("energy"):
        val = max(0, min(25, 25 - (row['bb_w'] * 1000)))
        score += val
        comps['energy'] = int(val)

    # 2. Space (Direct: High ATR = Room)
    if sensors.get("space"):
        atr_pct = row['atr'] / row['close']
        val = max(0, min(25, atr_pct * 5000))
        score += val
        comps['space'] = int(val)

    # 3. Wind (Direct: High Momentum)
    if sensors.get("wind"):
        val = max(0, min(25, (row['slope'] / row['close']) * 5000))
        score += val
        comps['wind'] = int(val)

    # 4. Hull (Inverse: Z-Score < 1.5 is ideal)
    if sensors.get("hull"):
        z = abs(row['z'])
        val = 25 if z < 1.5 else max(0, 25 - ((z-1.5)*25))
        score += val
        comps['hull'] = int(val)

    active_count = sum(1 for v in sensors.values() if v)
    final_score = int((score / (active_count * 25)) * 100) if active_count > 0 else 0
    return final_score, comps

async def run_hybrid_analysis(symbol, raw_5m, start_date, end_date, session_ids, tuning, sensors, min_score):
    try:
        if not raw_5m or len(raw_5m) < 100: return {"ok": False, "error": "Insufficient Data"}
        
        # 1. PREPARE DATAFRAME (AUTO-DETECT TIME UNITS)
        df = pd.DataFrame(raw_5m)
        
        # Check if timestamp is likely seconds or milliseconds
        # 1000000000000 roughly corresponds to year 2001 in milliseconds
        first_time = df['time'].iloc[0]
        time_unit = 'ms' if first_time > 1000000000000 else 's'
        
        df['time'] = pd.to_datetime(df['time'], unit=time_unit, utc=True)
        df.set_index('time', inplace=True)
        df.sort_index(inplace=True)
        
        # Calculate Indicators for Kinetics
        df['ma'] = df['close'].rolling(20).mean()
        df['std'] = df['close'].rolling(20).std()
        df['bb_w'] = (4 * df['std']) / df['close'] 
        df['tr'] = np.maximum(df['high'] - df['low'], abs(df['high'] - df['close'].shift(1)))
        df['atr'] = df['tr'].rolling(14).mean() 
        df['slope'] = df['close'].diff(5).abs() 
        df['z'] = (df['close'] - df['ma']) / df['std']

        # 2. STRUCTURE ENGINE SETUP
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        active_cfgs = [s for s in session_manager.SESSION_CONFIGS if s["id"] in session_ids]
        
        results = []
        curr_day = start_dt

        # Unpack Tuning
        req_vol = tuning.get("require_volume", False)
        req_div = tuning.get("require_divergence", False)
        fusion = tuning.get("fusion_mode", False)
        ignore_15 = tuning.get("ignore_15m_alignment", False)
        ignore_5 = tuning.get("ignore_5m_stoch", False)
        confirm_mode = tuning.get("confirmation_mode", "TOUCH")
        tol_bps = int(tuning.get("zone_tolerance_bps", 10))
        zone_tol = tol_bps / 10000.0

        while curr_day <= end_dt:
            day_str = curr_day.strftime("%Y-%m-%d")
            
            for cfg in active_cfgs:
                # A. Time Slicing
                anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, curr_day)
                lock_end_ts = anchor_ts + 1800 
                exec_end_ts = lock_end_ts + (12 * 3600) 

                calibration = _slice_by_ts(raw_5m, anchor_ts, lock_end_ts)
                if len(calibration) < 6: continue
                
                context_24h = _slice_by_ts(raw_5m, lock_end_ts - 86400, lock_end_ts)
                post_lock = _slice_by_ts(raw_5m, lock_end_ts, exec_end_ts)

                # B. SSE Levels
                sse_input = {
                    "locked_history_5m": context_24h,
                    "slice_24h_5m": context_24h,
                    "session_open_price": calibration[0]["open"],
                    "r30_high": max(c["high"] for c in calibration),
                    "r30_low": min(c["low"] for c in calibration),
                    "last_price": context_24h[-1]["close"] if context_24h else 0.0,
                    "tuning": tuning
                }
                computed = sse_engine.compute_sse_levels(sse_input)
                if "error" in computed: continue
                levels = computed["levels"]

                # C. Structure State (The Protocol)
                state = structure_state_engine.compute_structure_state(levels, post_lock, tuning=tuning)
                had_acceptance = (state["permission"]["status"] == "EARNED")
                side = state["permission"]["side"]

                # D. Go Logic (The Signal)
                go = {"ok": False, "go_type": "NONE", "simulation": {}}
                if had_acceptance and side in ("LONG", "SHORT"):
                    candles_15m_proxy = sse_engine._resample(context_24h, 15) if hasattr(sse_engine, "_resample") else []
                    st15 = battlebox_rules.compute_stoch(candles_15m_proxy)
                    
                    go = battlebox_rules.detect_pullback_go(
                        side=side, levels=levels, post_accept_5m=post_lock, stoch_15m_at_accept=st15,
                        use_zone="TRIGGER", require_volume=req_vol, require_divergence=req_div,
                        fusion_mode=fusion, zone_tol=zone_tol, ignore_15m=ignore_15,
                        ignore_5m_stoch=ignore_5, confirmation_mode=confirm_mode
                    )

                    # E. TRADE SIMULATION (Restored)
                    if go["ok"]:
                        stop_price = 0.0
                        if side == "LONG": stop_price = min(c["low"] for c in calibration) 
                        else: stop_price = max(c["high"] for c in calibration)

                        trade_candles = [c for c in raw_5m if c["time"] > go["go_ts"] and c["time"] < exec_end_ts]
                        
                        # Full Simulation Logic
                        sim = battlebox_rules.simulate_trade(
                            entry_price=levels.get("breakout_trigger") if side == "LONG" else levels.get("breakdown_trigger"),
                            entry_ts=go["go_ts"],
                            stop_price=stop_price,
                            direction=side,
                            levels=levels,
                            future_candles=trade_candles
                        )
                        go["simulation"] = sim

                # F. Kinetic Math (The Layer)
                try:
                    score_time = pd.to_datetime(anchor_ts, unit='s', utc=True)
                    row = df.asof(score_time) 
                    k_score, k_comps = _calculate_kinetic_score(row, sensors)
                except:
                    k_score, k_comps = 0, {}

                # G. Full Data Payload
                results.append({
                    "date": f"{day_str} [{cfg['id']}]",
                    "protocol": state["action"],
                    "kinetic_score": k_score,
                    "kinetic_comps": k_comps,
                    "trade_signal": go["ok"],
                    "trade_type": go.get("go_type", "NONE"),
                    "simulation": go.get("simulation", {}), 
                    "levels": {
                        "BO": levels.get("breakout_trigger"),
                        "BD": levels.get("breakdown_trigger"),
                        "DR": levels.get("daily_resistance"),
                        "DS": levels.get("daily_support")
                    }
                })

            curr_day += timedelta(days=1)

        # Summary Calculation
        valid_trades = [r for r in results if r['trade_signal']]
        prime_setups = [r for r in results if r['trade_signal'] and r['kinetic_score'] >= min_score]
        
        # Calculate Realized R for Summary
        total_r = sum(r['simulation'].get('r_realized', 0) for r in valid_trades)

        return {
            "ok": True,
            "total_sessions": len(results),
            "trade_signals": len(valid_trades),
            "kinetic_passes": len([r for r in results if r['kinetic_score'] >= min_score]),
            "prime_setups": len(prime_setups), 
            "total_r_realized": round(total_r, 2),
            "avg_score": int(np.mean([r['kinetic_score'] for r in results])) if results else 0,
            "results": results
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}