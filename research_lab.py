# research_lab.py
# ==============================================================================
# KABRODA RESEARCH LAB v8.2 (AI ANALYST + SIMULATOR)
# ==============================================================================
# Updates:
# - ADDED: Strategy Simulator (Equity Curve Math).
# - FIXED: Full Session Lock Logic preserved.
# - INTEGRATED: Prepared output for AI Analyst consumption.
# ==============================================================================

from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone
import pandas as pd
import sse_engine
import ccxt.async_support as ccxt
import pytz
import strategy_auditor
import traceback

# --- GLOBAL PERSISTENCE (In-Memory Cache) ---
# Stores locked levels to prevent re-computation/drift during a session.
LOCKED_SESSIONS = {}

# --- MATH HELPERS ---
def calculate_ema(prices: List[float], period: int = 21) -> List[float]:
    if len(prices) < period: return []
    return pd.Series(prices).ewm(span=period, adjust=False).mean().tolist()

def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1: return 0.0
    tr_list = []
    for i in range(1, len(closes)):
        h = highs[i]; l = lows[i]; pc = closes[i-1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
    return sum(tr_list[-period:]) / period if tr_list else 0.0

def _slice_by_ts(candles: List[Dict[str, Any]], start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    return [c for c in candles if start_ts <= c["time"] < end_ts]

# --- NEW: EQUITY SIMULATOR ENGINE ---
def _simulate_equity(sessions: List[Dict], start_bal: float, risk_pct: float, risk_cap: float) -> Dict[str, Any]:
    balance = start_bal
    equity_curve = []
    max_bal = start_bal
    max_drawdown = 0.0
    wins = 0
    losses = 0
    
    biggest_win_amt = 0.0
    biggest_win_date = ""

    # Sort sessions by date to ensure curve is chronological
    sessions.sort(key=lambda x: x["date"])

    for s in sessions:
        # Get result from the strategy auditor
        res = s.get("strategy", {})
        r_realized = res.get("r_realized", 0.0)
        outcome = res.get("outcome", "NO_TRADE")
        
        if outcome == "NO_TRADE" or outcome == "SKIPPED":
            equity_curve.append({"date": s["date"], "bal": int(balance)})
            continue

        # RISK CALCULATION
        risk_amt = balance * (risk_pct / 100.0)
        
        # RISK CAP 
        if risk_cap > 0 and risk_amt > risk_cap:
            risk_amt = risk_cap

        pnl = 0.0
        if r_realized > 0:
            pnl = risk_amt * r_realized
            wins += 1
            if pnl > biggest_win_amt:
                biggest_win_amt = pnl
                biggest_win_date = s["date"]
        elif r_realized < 0:
            # For losses, we assume full 1R loss if R is negative
            pnl = -risk_amt
            losses += 1
        
        balance += pnl
        
        # DRAWDOWN TRACKING
        if balance > max_bal: max_bal = balance
        dd = (max_bal - balance) / max_bal if max_bal > 0 else 0
        if dd > max_drawdown: max_drawdown = dd

        equity_curve.append({"date": s["date"], "bal": int(balance)})

    is_bust = balance <= 0
    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    return {
        "start_bal": start_bal,
        "end_bal": int(balance),
        "return_pct": round(((balance - start_bal) / start_bal) * 100, 2) if start_bal > 0 else 0,
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "win_rate": round(win_rate, 1),
        "total_trades": total_trades,
        "biggest_win": {"date": biggest_win_date, "amt": int(biggest_win_amt)},
        "is_bust": is_bust,
        "equity_curve": equity_curve
    }

# --- MAIN CONTROLLER ---
async def run_research_lab_from_candles(
    symbol: str,
    raw_5m: List[Dict[str, Any]],
    start_date_utc: str,
    end_date_utc: str,
    session_ids: Optional[List[str]] = None,
    exec_hours: int = 12,
    tuning: Dict[str, Any] = None,
    sim_settings: Dict[str, Any] = None 
) -> Dict[str, Any]:
    try:
        print(f"[LAB] Starting analysis for {symbol} ({len(raw_5m)} candles provided)")
        if not raw_5m: return {"ok": False, "error": "No candles provided to Research Lab."}
        raw_5m.sort(key=lambda x: x["time"])

        start_dt = datetime.strptime(start_date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        
        sessions_result = []
        
        # Determine Session Configs
        import session_manager
        configs = []
        if not session_ids: session_ids = ["us_ny_futures"]
        for sid in session_ids:
            cfg = session_manager.get_session_config(sid)
            if cfg: configs.append(cfg)

        curr_day = start_dt
        while curr_day <= end_dt:
            for cfg in configs:
                # 1. Resolve Anchor
                anchor_ts = session_manager.anchor_ts_for_utc_date(cfg, curr_day)
                open_dt_str = datetime.fromtimestamp(anchor_ts, timezone.utc).strftime("%Y-%m-%d")

                # 2. Slice Data (Anchor -> Anchor + 12h)
                session_end_ts = anchor_ts + (exec_hours * 3600)
                session_candles = _slice_by_ts(raw_5m, anchor_ts, session_end_ts)

                if not session_candles:
                    continue

                # 3. CHECK FOR LOCKED LEVELS (Prevent Drift)
                lock_key = f"{symbol}_{cfg['id']}_{open_dt_str}"
                
                # A. GET LEVELS
                if lock_key in LOCKED_SESSIONS:
                    # Use persisted levels
                    levels = LOCKED_SESSIONS[lock_key]["levels"]
                    r30 = LOCKED_SESSIONS[lock_key]["range_30m"]
                else:
                    # Compute & Lock
                    lock_end_ts = anchor_ts + 1800 # 30 mins
                    lock_candles = _slice_by_ts(raw_5m, anchor_ts, lock_end_ts)
                    
                    if not lock_candles: continue

                    # Compute SSE
                    pkt = sse_engine.compute_sse_levels(raw_5m, anchor_ts, tuning=tuning)
                    if "error" in pkt: continue
                    
                    levels = pkt["levels"]
                    r30 = pkt["range_30m"]
                    
                    # Persist
                    LOCKED_SESSIONS[lock_key] = {
                        "levels": levels, "range_30m": r30, "ts": anchor_ts
                    }

                # B. RUN STRUCTURE ENGINE
                # We only pass candles that happened AFTER the 30m lock
                post_lock_ts = anchor_ts + 1800
                post_lock_candles = [c for c in session_candles if c["time"] >= post_lock_ts]

                import structure_state_engine
                state = structure_state_engine.compute_structure_state(
                    levels, post_lock_candles, tuning=tuning
                )
                
                # C. KINETIC SCORING (For AI)
                # Calculate basic kinetic metrics for the log
                dr = levels.get("daily_resistance", 0)
                ds = levels.get("daily_support", 0)
                anchor_price = levels.get("session_open_price", 0)
                rg = abs(dr - ds)
                bps = (rg / anchor_price * 10000) if anchor_price else 0
                
                # D. RUN STRATEGY AUDIT
                import project_omega # Use Omega logic for audit
                # Mock Omega status to get plan
                omega_res = await project_omega.get_omega_status(
                    symbol=symbol, session_id=cfg["id"], force_time_utc=None, force_price=None
                )
                
                # Actual Audit
                strat_res = strategy_auditor.audit_session(
                    session_candles, levels, r30, 
                    strategy_name="OMEGA_V8",
                    tuning=tuning
                )
                
                sessions_result.append({
                    "date": open_dt_str,
                    "session": cfg["name"],
                    "kinetic": {
                        "total_score": omega_res.get("kinetic", {}).get("total_score", 0),
                        "protocol": omega_res.get("kinetic", {}).get("protocol", "UNKNOWN"),
                        "bps": int(bps)
                    },
                    "levels": levels,
                    "strategy": strat_res
                })
            
            curr_day += timedelta(days=1)

        # --- RUN SIMULATION ---
        sim_settings = sim_settings or {"start_bal": 10000, "risk_pct": 1.0, "risk_cap": 0}
        simulation = _simulate_equity(
            sessions_result, 
            float(sim_settings.get("start_bal", 10000)),
            float(sim_settings.get("risk_pct", 1.0)),
            float(sim_settings.get("risk_cap", 0))
        )

        total = len(sessions_result)
        print(f"[LAB] Analysis complete. Sessions: {total}. End Bal: {simulation['end_bal']}")

        return {
            "ok": True,
            "symbol": symbol,
            "range": {"start": start_date_utc, "end": end_date_utc},
            "stats": {
                "sessions_total": total,
                "win_rate": simulation["win_rate"],
                "pnl_total": simulation["return_pct"]
            },
            "simulation": simulation,
            "sessions": sessions_result
        }

    except Exception as e:
        print(f"[LAB ERROR] {str(e)}")
        traceback.print_exc()
        return {"ok": False, "error": str(e)}