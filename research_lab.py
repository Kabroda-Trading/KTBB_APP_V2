# research_lab.py
# ==============================================================================
# RESEARCH LAB: HYBRID ENGINE (Structure + Kinetics)
# ==============================================================================
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import traceback

# CORE ENGINES (Restored)
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
        if not raw_5m: return {"ok": False, "error": "No Data"}
        
        # 1. PREPARE DATAFRAME FOR KINETICS (The Math Layer)
        df = pd.DataFrame(raw_5m)
        df['time'] = pd.to_datetime(df['time'], unit='ms', utc=True)
        df.set_index('time', inplace=True)
        df.sort_index(inplace=True)
        
        # Calculate Indicators for Kinetics
        df['ma'] = df['close'].rolling(20).mean()
        df['std'] = df['close'].rolling(20).std()
        df['bb_w'] = (4 * df['std']) / df['close'] # Energy
        df['tr'] = np.maximum(df['high'] - df['low'], abs(df['high'] - df['close'].shift(1)))
        df['atr'] = df['tr'].rolling(14).mean() # Space
        df['slope'] = df['close'].diff(5).abs() # Wind
        df['z'] = (df['close'] - df['ma']) / df['std'] # Hull

        # 2. RUN STRUCTURE ENGINE (The Logic Layer)
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        
        # Map session IDs to configs
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
                # A. Get Anchor Time
                anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, curr_day)
                lock_end_ts = anchor_ts + 1800 # 30m Calibration
                exec_end_ts = lock_end_ts + (12 * 3600) # 12h Session

                # B. Slice Data
                calibration = _slice_by_ts(raw_5m, anchor_ts, lock_end_ts)
                if len(calibration) < 6: continue
                
                context_24h = _slice_by_ts(raw_5m, lock_end_ts - 86400, lock_end_ts)
                post_lock = _slice_by_ts(raw_5m, lock_end_ts, exec_end_ts)

                # C. Run SSE (Levels)
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

                # D. Run Structure State (Action)
                state = structure_state_engine.compute_structure_state(levels, post_lock, tuning=tuning)
                had_acceptance = (state["permission"]["status"] == "EARNED")
                side = state["permission"]["side"]

                # E. Run Go Logic (Trade)
                go = {"ok": False, "go_type": "NONE"}
                if had_acceptance and side in ("LONG", "SHORT"):
                    candles_15m_proxy = sse_engine._resample(context_24h, 15) if hasattr(sse_engine, "_resample") else []
                    st15 = battlebox_rules.compute_stoch(candles_15m_proxy)
                    
                    go = battlebox_rules.detect_pullback_go(
                        side=side, levels=levels, post_accept_5m=post_lock, stoch_15m_at_accept=st15,
                        use_zone="TRIGGER", require_volume=req_vol, require_divergence=req_div,
                        fusion_mode=fusion, zone_tol=zone_tol, ignore_15m=ignore_15,
                        ignore_5m_stoch=ignore_5, confirmation_mode=confirm_mode
                    )

                # F. Run Kinetic Math (Score)
                # Find DataFrame row at anchor time to score the "Start of Session"
                try:
                    score_time = pd.to_datetime(anchor_ts, unit='s', utc=True)
                    row = df.asof(score_time) # Get nearest data point
                    k_score, k_comps = _calculate_kinetic_score(row, sensors)
                except:
                    k_score, k_comps = 0, {}

                # G. Package Result
                results.append({
                    "date": f"{day_str} [{cfg['id']}]",
                    "protocol": state["action"],
                    "kinetic_score": k_score,
                    "kinetic_comps": k_comps,
                    "trade_signal": go["ok"],
                    "trade_type": go.get("go_type", "NONE"),
                    "levels": {
                        "BO": levels.get("breakout_trigger"),
                        "BD": levels.get("breakdown_trigger")
                    }
                })

            curr_day += timedelta(days=1)

        # Summary
        valid_trades = [r for r in results if r['trade_signal']]
        valid_kinetic = [r for r in results if r['kinetic_score'] >= min_score]
        
        # Intersection: Trade Signal AND Kinetic Score >= Min
        prime_setups = [r for r in results if r['trade_signal'] and r['kinetic_score'] >= min_score]

        return {
            "ok": True,
            "total_sessions": len(results),
            "trade_signals": len(valid_trades),
            "kinetic_passes": len(valid_kinetic),
            "prime_setups": len(prime_setups), # The Holy Grail count
            "avg_score": int(np.mean([r['kinetic_score'] for r in results])) if results else 0,
            "results": results
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}