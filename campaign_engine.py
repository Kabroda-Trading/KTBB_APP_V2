# campaign_engine.py
# ==============================================================================
# KABRODA CAMPAIGN STATE MACHINE (THE MISSION LEDGER DAEMON)
# ==============================================================================

import asyncio
import json
import traceback
from datetime import datetime, timezone

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

    # --- PENDING -> ACTIVE ---
    if log.status == "PENDING":
        if is_long and low <= log.entry_price <= high:
            log.status = "ACTIVE"
            log.activated_at = now_utc # NEW: Stamp Entry Time
        elif not is_long and low <= log.entry_price <= high:
            log.status = "ACTIVE"
            log.activated_at = now_utc # NEW: Stamp Entry Time

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
            log.closed_at = now_utc # NEW: Stamp Termination Time
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
                log.closed_at = now_utc # NEW: Stamp Termination Time

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
            if plan.get("valid"):
                exists = db.query(CampaignLog).filter(
                    CampaignLog.symbol == lock.symbol,
                    CampaignLog.session_id == lock.session_id,
                    CampaignLog.date_key == date_key
                ).first()
                
                if not exists:
                    risk_amt = 1000.00 
                    dist = abs(plan["entry"] - plan["stop"])
                    total_contracts = (risk_amt / dist) if dist > 0 else 0
                    
                    new_log = CampaignLog(
                        symbol=lock.symbol, date_key=date_key, session_id=lock.session_id,
                        bias=plan["bias"], grade=dossier["grade"], entry_price=plan["entry"],
                        stop_loss=plan["stop"], t1=plan["targets"][0], t2=plan["targets"][1], t3=plan["targets"][2],
                        total_contracts=total_contracts
                    )
                    db.add(new_log)
        db.commit()
    except Exception as e:
        traceback.print_exc()
    finally:
        db.close()

async def run_campaign_tracker_loop():
    print(">>> CAMPAIGN ENGINE: Mission Ledger Daemon Online...")
    while True:
        await sync_daily_campaigns()
        
        db = SessionLocal()
        try:
            active_logs = db.query(CampaignLog).filter(
                CampaignLog.status.in_(["PENDING", "ACTIVE", "T1_HIT", "T2_HIT"])
            ).all()
            
            targets_to_fetch = set([log.symbol for log in active_logs])
            
            for symbol in targets_to_fetch:
                candles_5m = await battlebox_pipeline.fetch_live_5m(symbol, limit=3)
                if not candles_5m: continue
                
                high_px = max([float(c["high"]) for c in candles_5m])
                low_px  = min([float(c["low"]) for c in candles_5m])
                
                symbol_logs = [log for log in active_logs if log.symbol == symbol]
                for log in symbol_logs:
                    old_status = log.status
                    _evaluate_state(log, high_px, low_px)
                    if old_status != log.status:
                        log.updated_at = datetime.now(timezone.utc)
                        print(f"[MISSION LEDGER] {symbol} | Status Update: {old_status} -> {log.status} | PnL: ${log.realized_pnl:.2f}")
            db.commit()
        except Exception as e:
            traceback.print_exc()
        finally:
            db.close()
            
        await asyncio.sleep(60)