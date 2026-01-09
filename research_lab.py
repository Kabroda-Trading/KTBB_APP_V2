# research_lab.py
# ==============================================================================
# RESEARCH LAB: HISTORICAL REPLAY COORDINATOR
# ==============================================================================
# Role: Time Machine.
# 1. Fetches deep history (Pagination).
# 2. Iterates through dates.
# 3. Uses STRICT COMPLIANCE via Battlebox Pipeline.
# ==============================================================================

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
import traceback
import ccxt.async_support as ccxt

# --- IMPORT THE PIPELINE (SOURCE OF TRUTH) ---
import battlebox_pipeline

exchange_kucoin = ccxt.kucoin({"enableRateLimit": True})

# --- 1. HISTORICAL DATA FETCHER (PAGINATION) ---
async def fetch_5m_historical_range(symbol: str, start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    """
    Fetches a large block of 5m candles using pagination.
    """
    market = symbol.upper().replace("BTCUSDT", "BTC/USDT").replace("ETHUSDT", "ETH/USDT")
    if market.endswith("USDT") and "/" not in market: market = market.replace("USDT", "/USDT")

    all_ohlcv = []
    current_since = start_ts * 1000
    end_ms = end_ts * 1000
    limit = 1000
    max_loops = 150 
    loop_count = 0

    while current_since < end_ms and loop_count < max_loops:
        loop_count += 1
        try:
            ohlcv = await exchange_kucoin.fetch_ohlcv(market, '5m', since=current_since, limit=limit)
            if not ohlcv: break
            all_ohlcv.extend(ohlcv)
            current_since = ohlcv[-1][0] + (5 * 60 * 1000)
            if ohlcv[-1][0] >= end_ms: break
        except Exception: break

    unique_candles = {}
    for c in all_ohlcv:
        ts = int(c[0] / 1000)
        if start_ts <= ts <= end_ts:
            unique_candles[ts] = {
                "time": ts, "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])
            }
            
    return sorted(unique_candles.values(), key=lambda x: x['time'])

# --- 2. MAIN RUNNER ---
async def run_research_lab(symbol: str, start_date_utc: str, end_date_utc: str, session_ids: List[str] = None) -> Dict[str, Any]:
    try:
        start_dt = datetime.strptime(start_date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        
        # Buffer: 48h context, 8h execution
        fetch_start_ts = int((start_dt - timedelta(hours=48)).timestamp())
        fetch_end_ts = int((end_dt + timedelta(hours=30)).timestamp())
        
        raw_5m = await fetch_5m_historical_range(symbol, fetch_start_ts, fetch_end_ts)
        if not raw_5m: return {"ok": False, "error": "No data returned."}
        
        sessions_result = []
        target_ids = session_ids or [s["id"] for s in battlebox_pipeline.SESSION_CONFIGS]
        active_configs = [s for s in battlebox_pipeline.SESSION_CONFIGS if s["id"] in target_ids]
        
        curr_day = start_dt
        while curr_day <= end_dt:
            day_str = curr_day.strftime("%Y-%m-%d")
            
            for cfg in active_configs:
                # --- DELEGATE EVERYTHING TO PIPELINE ---
                # This ensures we use the EXACT same logic as Live Battle Control
                result = battlebox_pipeline.compute_session_from_candles(
                    cfg=cfg,
                    utc_date=curr_day,
                    raw_5m=raw_5m,
                    exec_hours=6
                )
                
                # Add metadata for the UI
                sessions_result.append({
                    "date": day_str,
                    "session_name": cfg["name"],
                    "session_id": cfg["id"],
                    **result # Unpack ok, counts, events, errors
                })

            curr_day += timedelta(days=1)
            
        # Stats
        valid = [s for s in sessions_result if s.get("ok")]
        total = len(valid)
        acc = sum(1 for s in valid if s["counts"]["had_acceptance"])
        align = sum(1 for s in valid if s["counts"]["had_alignment"])
        rate = round((align / total * 100), 1) if total > 0 else 0.0
        
        return {
            "ok": True,
            "symbol": symbol,
            "range": {"start": start_date_utc, "end": end_date_utc},
            "stats": {
                "sessions_total": total,
                "acceptance_count": acc,
                "alignment_count": align,
                "no_trade_sessions": total - align,
                "alignment_rate_pct": rate
            },
            "sessions": sessions_result
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}