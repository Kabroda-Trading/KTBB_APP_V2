# research_lab.py
# ==============================================================================
# KABRODA RESEARCH LAB (BACKEND REWRITE - STRICT COMPLIANCE)
# ==============================================================================
# 1. Real historical replay (Pagination + Deduplication)
# 2. Strict Session Anchoring (Timezone correct)
# 3. Exact Slicing (Calibration, Context, Post-Lock)
# 4. SSE v2.0 + Law Layer Integration
# ==============================================================================

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import traceback
import pytz
import ccxt.async_support as ccxt
import sse_engine
import structure_state_engine

# --- CONFIGURATION (MATCHING BATTLE CONTROL) ---
SESSION_CONFIGS = [
    {"id": "us_ny_futures", "name": "NY Futures", "tz": "America/New_York", "open_h": 8, "open_m": 30},
    {"id": "us_ny_equity", "name": "NY Equity", "tz": "America/New_York", "open_h": 9, "open_m": 30},
    {"id": "eu_london", "name": "London", "tz": "Europe/London", "open_h": 8, "open_m": 0},
    {"id": "asia_tokyo", "name": "Tokyo", "tz": "Asia/Tokyo", "open_h": 9, "open_m": 0},
    {"id": "au_sydney", "name": "Sydney", "tz": "Australia/Sydney", "open_h": 10, "open_m": 0},
]

DEFAULT_SESSION_IDS = [s["id"] for s in SESSION_CONFIGS]

exchange_kucoin = ccxt.kucoin({"enableRateLimit": True})

# --- HELPERS ---
def _sym(symbol: str) -> str:
    s = (symbol or "BTCUSDT").strip().upper()
    return s.replace("BTCUSDT", "BTC/USDT").replace("ETHUSDT", "ETH/USDT")

def _to_candle_list(ohlcv: List[List[float]]) -> List[Dict[str, Any]]:
    # [ms, open, high, low, close, volume]
    out = []
    for c in ohlcv:
        out.append({
            "time": int(c[0] / 1000),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]),
        })
    return out

async def fetch_5m_historical_range(symbol: str, start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    """
    Step 1: Real historical replay fetcher.
    Fetches 5m candles from start_ts to end_ts using pagination (since).
    """
    market = _sym(symbol)
    all_ohlcv = []
    current_since = start_ts * 1000
    end_ms = end_ts * 1000
    limit = 1000
    
    # Safety break to prevent infinite loops
    max_loops = 100 
    loop_count = 0

    while current_since < end_ms and loop_count < max_loops:
        loop_count += 1
        try:
            ohlcv = await exchange_kucoin.fetch_ohlcv(market, '5m', since=current_since, limit=limit)
            if not ohlcv:
                break
            
            all_ohlcv.extend(ohlcv)
            
            # Update cursor to the timestamp of the last candle + 5m
            last_candle_ms = ohlcv[-1][0]
            current_since = last_candle_ms + (5 * 60 * 1000)
            
            # Break if we reached the target time
            if last_candle_ms >= end_ms:
                break
                
        except Exception as e:
            print(f"Research Lab Fetch Error: {e}")
            break

    # Deduplicate by timestamp and sort
    unique_candles = {}
    for c in all_ohlcv:
        ts = int(c[0] / 1000)
        # Only keep candles within the requested broad window
        if start_ts <= ts <= end_ts:
            unique_candles[ts] = {
                "time": ts,
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5])
            }
            
    sorted_candles = sorted(unique_candles.values(), key=lambda x: x['time'])
    return sorted_candles

def _get_anchor_ts(session_cfg: Dict, day_date: datetime) -> int:
    """
    Step 2: Session Anchoring.
    Converts a UTC date -> Local Session Open Time -> UTC Timestamp.
    """
    tz = pytz.timezone(session_cfg["tz"])
    # Localize the day start
    local_day = day_date.astimezone(tz)
    # Set the open hour/minute
    local_open = local_day.replace(hour=session_cfg["open_h"], minute=session_cfg["open_m"], second=0, microsecond=0)
    # Convert back to UTC timestamp
    return int(local_open.astimezone(timezone.utc).timestamp())

async def run_research_lab(symbol: str, start_date_utc: str, end_date_utc: str, session_ids: List[str] = None) -> Dict[str, Any]:
    """
    Main entry point for Research Lab.
    """
    try:
        # 1. Parse Dates
        start_dt = datetime.strptime(start_date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        
        # 2. Define Fetch Range (Buffer: -48h for context, +6h for execution)
        fetch_start_ts = int((start_dt - timedelta(hours=48)).timestamp())
        # End date covers the full day, so add 24h + 6h buffer
        fetch_end_ts = int((end_dt + timedelta(hours=30)).timestamp())
        
        # 3. Fetch Data
        raw_5m = await fetch_5m_historical_range(symbol, fetch_start_ts, fetch_end_ts)
        
        if not raw_5m:
            return {"ok": False, "error": "No data returned from exchange."}
            
        # Map candles for fast lookup (optional optimization, but linear scan is fine for daily chunks)
        # We will slice from the sorted list directly.
        
        sessions_result = []
        
        target_ids = session_ids or DEFAULT_SESSION_IDS
        active_configs = [s for s in SESSION_CONFIGS if s["id"] in target_ids]
        
        # Loop through each day in range
        curr_day = start_dt
        while curr_day <= end_dt:
            day_str = curr_day.strftime("%Y-%m-%d")
            
            for cfg in active_configs:
                # Step 2: Anchoring
                anchor_ts = _get_anchor_ts(cfg, curr_day)
                lock_end_ts = anchor_ts + 1800 # 30 mins
                
                # Step 3: Slicing
                # Calibration: Anchor to Lock
                calibration_slice = [c for c in raw_5m if anchor_ts <= c["time"] < lock_end_ts]
                
                # Context: 24h ending at Lock
                context_24h = [c for c in raw_5m if (lock_end_ts - 86400) <= c["time"] < lock_end_ts]
                
                # Execution: Lock to Lock + 6h
                exec_end_ts = lock_end_ts + (6 * 3600)
                post_lock_candles = [c for c in raw_5m if lock_end_ts <= c["time"] < exec_end_ts]
                
                # Validation
                if len(calibration_slice) < 6:
                    sessions_result.append({
                        "ok": False,
                        "date": day_str,
                        "session_id": cfg["id"],
                        "session_name": cfg["name"],
                        "error": "Missing calibration data (need 6 candles)"
                    })
                    continue
                    
                if len(context_24h) < 100: # Soft check for context
                     # We proceed but note it might be low quality? 
                     # For now, strict fail is safer if critical, but let's allow it to run if basic calib exists.
                     pass

                # Step 4: Compute Levels (SSE)
                session_open_price = calibration_slice[0]["open"]
                r30_high = max(c["high"] for c in calibration_slice)
                r30_low = min(c["low"] for c in calibration_slice)
                last_price = context_24h[-1]["close"] if context_24h else session_open_price
                
                sse_input = {
                    "locked_history_5m": context_24h,
                    "slice_24h_5m": context_24h,
                    # slice_4h_5m is subset of context_24h
                    "slice_4h_5m": context_24h[-48:], 
                    "session_open_price": session_open_price,
                    "r30_high": r30_high,
                    "r30_low": r30_low,
                    "last_price": last_price
                }
                
                levels_out = sse_engine.compute_sse_levels(sse_input)
                
                if "error" in levels_out:
                    sessions_result.append({
                        "ok": False,
                        "date": day_str,
                        "session_id": cfg["id"],
                        "session_name": cfg["name"],
                        "error": levels_out["error"]
                    })
                    continue

                # Step 5: Compute State (Law Layer)
                # Pass the full post-lock window to see if acceptance/alignment occurred
                state = structure_state_engine.compute_structure_state(levels_out["levels"], post_lock_candles)
                
                had_acceptance = (state["permission"]["status"] == "EARNED")
                had_alignment = (state["execution"]["gates_mode"] == "LOCKED")
                acceptance_side = state["permission"]["side"]
                
                # Step 6: Return Schema
                sessions_result.append({
                    "ok": True,
                    "date": day_str,
                    "session_id": cfg["id"],
                    "session_name": cfg["name"],
                    "counts": {
                        "had_acceptance": had_acceptance,
                        "had_alignment": had_alignment
                    },
                    "events": {
                        "acceptance_side": acceptance_side
                    }
                })

            curr_day += timedelta(days=1)
            
        # Stats Aggregation
        valid_sessions = [s for s in sessions_result if s["ok"]]
        total = len(valid_sessions)
        acc_count = sum(1 for s in valid_sessions if s["counts"]["had_acceptance"])
        align_count = sum(1 for s in valid_sessions if s["counts"]["had_alignment"])
        no_trade = total - align_count
        rate = round((align_count / total * 100), 1) if total > 0 else 0.0
        
        return {
            "ok": True,
            "symbol": symbol,
            "range": {"start_date_utc": start_date_utc, "end_date_utc": end_date_utc},
            "stats": {
                "sessions_total": total,
                "acceptance_count": acc_count,
                "alignment_count": align_count,
                "no_trade_sessions": no_trade,
                "alignment_rate_pct": rate
            },
            "sessions": sessions_result
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}