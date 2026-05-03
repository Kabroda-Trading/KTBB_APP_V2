# campaign_engine.py
# ==============================================================================
# KABRODA CAMPAIGN STATE MACHINE (THE MISSION LEDGER DAEMON)
# AUDIT FIX: Inverted Breakout/Breakdown trigger logic corrected.
# ADDED: Stand Down / Chop tracking to prove capital preservation.
# ==============================================================================

import asyncio
import json
import traceback
from datetime import datetime, timezone, timedelta

from database import SessionLocal, SessionLock, CampaignLog
import battlebox_pipeline

TARGETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

def _calculate_scale_split(grade: str, total_contracts: float) -> tuple:
    if grade == "GRADE B": return (total_contracts * 0.70, total_contracts * 0.30)
    return (total_contracts * 0.30, total_contracts * 0.70) 

def _evaluate_state(log: CampaignLog, high: float, low: float):
    is_long = log.bias == "LONG"
    t1_vol, runner_vol = _calculate_scale_split(log.grade, log.total_contracts)
    now_utc = datetime.now(timezone.utc)

    # --- PENDING -> ACTIVE (FIXED BREAKOUT LOGIC) ---
    if log.status == "PENDING":
        if is_long and high >= log.entry_price:  # Breakout UP
            log.status = "ACTIVE"
            log.activated_at = now_utc 
        elif not is_long and low <= log.entry_price: # Breakdown DOWN
            log.status = "ACTIVE"
            log.activated_at = now_utc 

    # --- ACTIVE MANAGEMENT ---
    if log.status in ["ACTIVE", "T1_HIT", "T2_HIT"]:
        current_stop = log.entry_price if log.status in ["T1_HIT", "T2_HIT"] else log.stop_loss
        
        # 1. Stop Out
        if (is_long and low <= current_stop) or (not is_long and high >= current_stop):
            if log.status == "ACTIVE":
                loss = (current_stop - log.entry_price) * log.total_contracts if is_long else (log.entry_price - current_stop) * log.total_contracts
                log.realized_pnl += loss
                log.status = "CLOSED_LOSS"
            else:
                log.status = "CLOSED_SCRATCH" 
            log.closed_at = now_utc 
            return

        # 2. Target Progression
        if log.status == "ACTIVE":
            if (is_long and high >= log.t1) or (not is_long and low <= log.t1):
                profit = (log.t1 - log.entry_price) * t1_vol if is_long else (log.entry_price - log.t1) * t1_vol
                log.realized_pnl += profit
                log.status = "T1_HIT"

        if log.status == "T1_HIT":
            if (is_long and high >= log.t2) or (not is_long and low <= log.t2):
                log.status = "T2_HIT" 

        if log.status == "T2_HIT":
            if (is_long and high >= log.t3) or (not is_long and low <= log.t3):
                profit = (log.t3 - log.entry_price) * runner_vol if is_long else (log.entry_price - log.t3) * runner_vol
                log.realized_pnl += profit
                log.status = "CLOSED_WIN"
                log.closed_at = now_utc 

async def sync_daily_campaigns():
    db = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)
        date_key = now_utc.strftime("%Y-%m-%d")
        
        locks = db.query(SessionLock).filter(SessionLock.date_key == date_key).all()
        
        for lock in locks:
            pkt = json.loads(lock.packet_data)
            import market_radar 
            dossier = market_radar._build_dossier(
                lock.symbol, 0, pkt.get("levels",{}), pkt.get("context",{}).get("macro_bias","NEUTRAL"),
                pkt.get("context",{}).get("micro_bias","NEUTRAL"), pkt.get("context",{}).get("fuel_gauge",{}),
                pkt.get("context",{}).get("kde_peaks",[]), pkt.get("context",{}).get("macro_fibs",{})
            )
            
            plan = dossier.get("plan", {})
            
            exists = db.query(CampaignLog).filter(
                CampaignLog.symbol == lock.symbol,
                CampaignLog.session_id == lock.session_id,
                CampaignLog.date_key == date_key
            ).first()
            
            if not exists:
                risk_amt = 1000.00 
                
                last_price = 0.0
                if "meta" in pkt and "last_price" in pkt["meta"]:
                    last_price = float(pkt["meta"]["last_price"])
                elif "levels" in pkt and "range30m_high" in pkt["levels"]:
                    last_price = float(pkt["levels"]["range30m_high"])
                
                entry_ref = plan.get("entry", 0.0) if plan.get("valid") else last_price
                stop_ref = plan.get("stop", 0.0) if plan.get("valid") else (last_price * 0.99)
                
                dist = abs(entry_ref - stop_ref)
                total_contracts = (risk_amt / dist) if dist > 0 else 0.0
                
                diagnostics_payload = dossier.get("diagnostic_ledger", {})
                diagnostics_payload["rejection_reason"] = dossier.get("checks", ["No valid setup"])[0] if dossier.get("grade") == "STAND DOWN" else ""
                
                initial_status = "PENDING" if plan.get("valid") else "STAND_DOWN"
                bias_ref = plan.get("bias", "NEUTRAL") if plan.get("valid") else dossier.get("favored", "NEUTRAL")
                
                targets = plan.get("targets", [0.0, 0.0, 0.0])
                
                new_log = CampaignLog(
                    symbol=lock.symbol, date_key=date_key, session_id=lock.session_id,
                    bias=bias_ref, grade=dossier["grade"], 
                    entry_price=plan.get("entry", 0.0), stop_loss=plan.get("stop", 0.0), 
                    t1=targets[0] if len(targets) > 0 else 0.0, 
                    t2=targets[1] if len(targets) > 1 else 0.0, 
                    t3=targets[2] if len(targets) > 2 else 0.0,
                    total_contracts=total_contracts,
                    status=initial_status,
                    diagnostic_data=json.dumps(diagnostics_payload)
                )
                db.add(new_log)
        db.commit()
    except Exception as e:
        traceback.print_exc()
    finally:
        db.close()

async def run_campaign_tracker_loop():
    print(">>> CAMPAIGN ENGINE: Mission Ledger Daemon Online (100% Data Capture Active)...")
    while True:
        await sync_daily_campaigns()
        
        db = SessionLocal()
        try:
            active_logs = db.query(CampaignLog).filter(
                CampaignLog.status.in_(["PENDING", "ACTIVE", "T1_HIT", "T2_HIT"])
            ).all()
            
            targets_to_fetch = set([log.symbol for log in active_logs])
            
            for symbol in targets_to_fetch:
                candles_5m = await battlebox_pipeline.fetch_live_5m(symbol, limit=288)
                if not candles_5m: continue
                
                symbol_logs = [log for log in active_logs if log.symbol == symbol]
                for log in symbol_logs:
                    old_status = log.status
                    now_utc = datetime.now(timezone.utc)
                    
                    log_created_utc = log.created_at.replace(tzinfo=timezone.utc) if log.created_at.tzinfo is None else log.created_at
                    
                    # 4-Hour Session TTL Expiration
                    if log.status == "PENDING" and (now_utc - log_created_utc).total_seconds() > 14400:
                        log.status = "EXPIRED"
                        log.closed_at = now_utc
                        log.updated_at = now_utc
                        print(f"[MISSION LEDGER] {symbol} | Status Update: PENDING -> EXPIRED (4-Hour Session TTL Reached)")
                        continue

                    last_check_ts = int(log.updated_at.timestamp()) if log.updated_at else int(log_created_utc.timestamp())
                    unseen_candles = [c for c in candles_5m if int(c["time"]) >= last_check_ts - 300]
                    
                    for candle in unseen_candles:
                        if log.status in ["CLOSED_WIN", "CLOSED_LOSS", "CLOSED_SCRATCH", "EXPIRED", "STAND_DOWN"]:
                            break 
                        _evaluate_state(log, float(candle["high"]), float(candle["low"]))
                        
                    if old_status != log.status:
                        log.updated_at = now_utc
                        print(f"[MISSION LEDGER] {symbol} | Status Update: {old_status} -> {log.status} | PnL: ${log.realized_pnl:.2f}")
            db.commit()
        except Exception as e:
            traceback.print_exc()
        finally:
            db.close()
            
        await asyncio.sleep(60)