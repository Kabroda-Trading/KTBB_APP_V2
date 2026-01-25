# research_lab.py
# ==============================================================================
# RESEARCH LAB: HYBRID ENGINE v1.6 (PORTABLE PHASE 2 DNA)
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

# --- KINETIC SENSORS (OPTIONAL) ---
def _calculate_kinetic_score(row, sensors):
    if row is None or pd.isna(row['ma']): return 0, {}
    score = 0
    comps = {} 

    if sensors.get("energy"):
        val = max(0, min(25, 25 - (row['bb_w'] * 1000)))
        score += val
        comps['energy'] = int(val)

    if sensors.get("space"):
        atr_pct = row['atr'] / row['close']
        val = max(0, min(25, atr_pct * 5000))
        score += val
        comps['space'] = int(val)

    if sensors.get("wind"):
        val = max(0, min(25, (row['slope'] / row['close']) * 5000))
        score += val
        comps['wind'] = int(val)

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
        
        # 1. PREPARE DATAFRAME
        df = pd.DataFrame(raw_5m)
        first_time = df['time'].iloc[0]
        time_unit = 'ms' if first_time > 1000000000000 else 's'
        df['time'] = pd.to_datetime(df['time'], unit=time_unit, utc=True)
        df.set_index('time', inplace=True)
        df.sort_index(inplace=True)
        
        # Indicators
        df['ma'] = df['close'].rolling(20).mean()
        df['std'] = df['close'].rolling(20).std()
        df['bb_w'] = (4 * df['std']) / df['close'] 
        df['tr'] = np.maximum(df['high'] - df['low'], abs(df['high'] - df['close'].shift(1)))
        df['atr'] = df['tr'].rolling(14).mean() 
        df['slope'] = df['close'].diff(5).abs() 
        df['z'] = (df['close'] - df['ma']) / df['std']

        # 2. PHASE 1 SETUP
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        active_cfgs = [s for s in session_manager.SESSION_CONFIGS if s["id"] in session_ids]
        
        results = []
        curr_day = start_dt
        
        # --- PHASE 2 DNA (THE STRATEGY PROFILE) ---
        # This acts as the "Default Settings".
        # The HTML tuning will OVERRIDE these if the user changes the dials.
        phase2_dna = {
            "fusion_mode": True,             # Default: ON
            "ignore_15m_alignment": True,    # Default: ON (Dead feature)
            "ignore_5m_stoch": True,         # Default: ON (Dead feature)
            "zone_tolerance_bps": 10,        # Default: 10bps
            "min_trigger_dist_bps": 20,      # Default: 20bps
            "confirmation_mode": "1_CANDLE_CLOSE", # Default: 1-Close
            "require_volume": False,
            "require_divergence": False
        }
        
        # Merge User Overrides from HTML
        if tuning:
            phase2_dna.update(tuning)

        # Pre-calc derived values
        zone_tol = phase2_dna["zone_tolerance_bps"] / 10000.0

        processed_anchors = set()

        while curr_day <= end_dt:
            query_time = curr_day + timedelta(hours=12)
            
            for cfg in active_cfgs:
                anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, query_time)
                if anchor_ts in processed_anchors: continue
                processed_anchors.add(anchor_ts)
                
                actual_session_date = datetime.fromtimestamp(anchor_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                lock_end_ts = anchor_ts + 1800 
                exec_end_ts = lock_end_ts + (12 * 3600) 

                calibration = _slice_by_ts(raw_5m, anchor_ts, lock_end_ts)
                if len(calibration) < 6: continue
                
                context_24h = _slice_by_ts(raw_5m, lock_end_ts - 86400, lock_end_ts)
                post_lock = _slice_by_ts(raw_5m, lock_end_ts, exec_end_ts)

                # --- WEEKLY BIAS ---
                weekly_bias = "NEUTRAL"
                try:
                    week_ago_ts = anchor_ts - (7 * 86400)
                    week_ago_idx = df.index.get_indexer([pd.to_datetime(week_ago_ts, unit='s', utc=True)], method='nearest')[0]
                    price_week_ago = df.iloc[week_ago_idx]['close']
                    price_now = calibration[0]['open']
                    if price_now > price_week_ago * 1.01: weekly_bias = "BULLISH"
                    elif price_now < price_week_ago * 0.99: weekly_bias = "BEARISH"
                except: pass

                # --- PHASE 1 EXECUTION (SSE ENGINE) ---
                sse_input = {
                    "locked_history_5m": context_24h,
                    "slice_24h_5m": context_24h,
                    "session_open_price": calibration[0]["open"],
                    "r30_high": max(c["high"] for c in calibration),
                    "r30_low": min(c["low"] for c in calibration),
                    "last_price": context_24h[-1]["close"] if context_24h else 0.0,
                    "tuning": phase2_dna # Triggers need to know min_dist
                }
                computed = sse_engine.compute_sse_levels(sse_input)
                if "error" in computed: continue
                levels = computed["levels"]

                # --- PHASE 2 EXECUTION (RULES ENGINE) ---
                state = structure_state_engine.compute_structure_state(levels, post_lock, tuning=phase2_dna)
                had_acceptance = (state["permission"]["status"] == "EARNED")
                side = state["permission"]["side"]

                go = {"ok": False, "go_type": "NONE", "simulation": {}}
                
                if had_acceptance and side in ("LONG", "SHORT"):
                    candles_15m_proxy = sse_engine._resample(context_24h, 15) if hasattr(sse_engine, "_resample") else []
                    st15 = battlebox_rules.compute_stoch(candles_15m_proxy)
                    
                    # RUN RULES using Phase 2 DNA
                    go = battlebox_rules.detect_pullback_go(
                        side=side, levels=levels, post_accept_5m=post_lock, stoch_15m_at_accept=st15,
                        use_zone="TRIGGER", 
                        require_volume=phase2_dna["require_volume"], 
                        require_divergence=phase2_dna["require_divergence"],
                        fusion_mode=phase2_dna["fusion_mode"], 
                        zone_tol=zone_tol, 
                        ignore_15m=phase2_dna["ignore_15m_alignment"],
                        ignore_5m_stoch=phase2_dna["ignore_5m_stoch"], 
                        confirmation_mode=phase2_dna["confirmation_mode"]
                    )

                    if go["ok"]:
                        stop_price = 0.0
                        if side == "LONG": stop_price = min(c["low"] for c in calibration) 
                        else: stop_price = max(c["high"] for c in calibration)

                        trade_candles = [c for c in raw_5m if c["time"] > go["go_ts"] and c["time"] < exec_end_ts]
                        
                        sim = battlebox_rules.simulate_trade(
                            entry_price=levels.get("breakout_trigger") if side == "LONG" else levels.get("breakdown_trigger"),
                            entry_ts=go["go_ts"],
                            stop_price=stop_price,
                            direction=side,
                            levels=levels,
                            future_candles=trade_candles
                        )
                        go["simulation"] = sim

                # KINETIC MATH
                k_score, k_comps = 0, {}
                if sensors and any(sensors.values()):
                    try:
                        score_time = pd.to_datetime(anchor_ts, unit='s', utc=True)
                        row = df.asof(score_time) 
                        k_score, k_comps = _calculate_kinetic_score(row, sensors)
                    except: pass

                # --- EXPORT RESULTS ---
                results.append({
                    "date": f"{actual_session_date} [{cfg['id']}]",
                    "weekly_bias": weekly_bias,
                    "protocol": state["action"],
                    "kinetic_score": k_score if any(sensors.values()) else None,
                    "kinetic_comps": k_comps if any(sensors.values()) else None,
                    "trade_signal": go["ok"],
                    "trade_type": go.get("go_type", "NONE"),
                    "simulation": go.get("simulation", {}), 
                    
                    # PHASE 1 TRUTH
                    "levels": {
                        "anchor_price": levels.get("anchor_price"),
                        "BO": levels.get("breakout_trigger"),
                        "BD": levels.get("breakdown_trigger"),
                        "DR": levels.get("daily_resistance"),
                        "DS": levels.get("daily_support"),
                        "r30_high": levels.get("range30m_high"),
                        "r30_low": levels.get("range30m_low"),
                        "structure_score": levels.get("structure_score", 0)
                    }
                })

            curr_day += timedelta(days=1)

        valid_trades = [r for r in results if r['trade_signal']]
        total_r = sum(r['simulation'].get('r_realized', 0) for r in valid_trades)

        return {
            "ok": True,
            "total_sessions": len(results),
            "trade_signals": len(valid_trades),
            "total_r_realized": round(total_r, 2),
            "results": results
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}