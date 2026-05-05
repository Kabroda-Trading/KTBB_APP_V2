# campaign_engine.py
# ==============================================================================
# KABRODA CAMPAIGN STATE MACHINE (THE MISSION LEDGER DAEMON)
# AUDIT FIX: Dual-tracking enabled for Radar Breakouts & Bounce Engine limits.
# ==============================================================================

import asyncio
import json
import traceback
from datetime import datetime, timezone, timedelta

from database import SessionLocal, CampaignLog
import battlebox_pipeline
import bounce_engine

TARGETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

def _calculate_scale_split(grade: str, total_contracts: float) -> tuple:
    if grade == "GRADE B": return (total_contracts * 0.70, total_contracts * 0.30)
    return (total_contracts * 0.30, total_contracts * 0.70) 

def _evaluate_state(log: CampaignLog, high: float, low: float):
    is_long = log.bias == "LONG"
    t1_vol, runner_vol = _calculate_scale_split(log.grade, log.total_contracts)
    now_utc = datetime.now(timezone.utc)

    if log.status == "PENDING":
        if is_long and low <= log.entry_price and high >= log.entry_price:  
            log.status = "ACTIVE"
            log.activated_at = now_utc 
        elif not is_long and high >= log.entry_price and low <= log.entry_price: 
            log.status = "ACTIVE"
            log.activated_at = now_utc 

    if log.status in ["ACTIVE", "T1_HIT", "T2_HIT"]:
        current_stop = log.entry_price if log.status in ["T1_HIT", "T2_HIT"] else log.stop_loss
        
        if (is_long and low <= current_stop) or (not is_long and high >= current_stop):
            if log.status == "ACTIVE":
                loss = (current_stop - log.entry_price) * log.total_contracts if is_long else (log.entry_price - current_stop) * log.total_contracts
                log.realized_pnl += loss
                log.status = "CLOSED_LOSS"
            else:
                log.status = "CLOSED_SCRATCH" 
            log.closed_at = now_utc 
            return

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

async def sync_radar_campaigns():
    db = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)
        date_key = now_utc.strftime("%Y-%m-%d")
        
        for symbol in TARGETS:
            exists = db.query(CampaignLog).filter(CampaignLog.symbol == symbol, CampaignLog.date_key == date_key, CampaignLog.session_id == "us_ny_futures").first()
            if not exists:
                data = await battlebox_pipeline.get_live_battlebox(symbol, "MANUAL", manual_id="us_ny_futures")
                if data.get("status") in ["ERROR", "CALIBRATING"]: continue

                levels = data.get("battlebox", {}).get("levels", {})
                context = data.get("battlebox", {}).get("context", {})

                import market_radar 
                dossier = market_radar._build_dossier(symbol, 0, levels, context.get("macro_bias","NEUTRAL"), context.get("micro_bias","NEUTRAL"), context.get("fuel_gauge",{}), context.get("kde_peaks",[]), context.get("macro_fibs",{}))
                
                plan = dossier.get("plan", {})
                risk_amt = 1000.00 
                last_price = float(data.get("price", 0.0))
                entry_ref = plan.get("entry", 0.0) if plan.get("valid") else last_price
                stop_ref = plan.get("stop", 0.0) if plan.get("valid") else (last_price * 0.99)
                
                dist = abs(entry_ref - stop_ref)
                total_contracts = (risk_amt / dist) if dist > 0 else 0.0
                
                diagnostics_payload = dossier.get("diagnostic_ledger", {})
                if dossier.get("grade") == "STAND DOWN":
                    diagnostics_payload["rejection_reason"] = dossier.get("checks", ["No valid setup"])[0] if dossier.get("checks") else "Insufficient structural alignment"
                
                initial_status = "PENDING" if plan.get("valid") else "STAND_DOWN"
                
                new_log = CampaignLog(
                    symbol=symbol, date_key=date_key, session_id="us_ny_futures", bias=plan.get("bias", "NEUTRAL") if plan.get("valid") else dossier.get("favored", "NEUTRAL"), grade=dossier["grade"], 
                    entry_price=entry_ref, stop_loss=stop_ref, t1=plan.get("targets", [0]*3)[0], t2=plan.get("targets", [0]*3)[1], t3=plan.get("targets", [0]*3)[2],
                    total_contracts=total_contracts, status=initial_status, diagnostic_data=json.dumps(diagnostics_payload)
                )
                db.add(new_log)
        db.commit()
    except Exception as e: traceback.print_exc()
    finally: db.close()

async def sync_bounce_campaigns():
    db = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)
        date_key = now_utc.strftime("%Y-%m-%d")
        
        results = await bounce_engine.scan_sector()
        for r in results:
            if r["grade"] == "STAND DOWN": continue
            
            exists = db.query(CampaignLog).filter(CampaignLog.symbol == r["symbol"], CampaignLog.date_key == date_key, CampaignLog.session_id == "BOUNCE_ENGINE", CampaignLog.status.in_(["PENDING", "ACTIVE", "T1_HIT", "T2_HIT"])).first()
            if exists: continue # Already tracking a live bounce today
            
            plan = r["plan"]
            risk_amt = 1000.00
            dist = abs(plan["entry"] - plan["stop"])
            total_contracts = (risk_amt / dist) if dist > 0 else 0.0
            
            new_log = CampaignLog(
                symbol=r["symbol"], date_key=date_key, session_id="BOUNCE_ENGINE", bias=plan["bias"], grade=r["grade"], 
                entry_price=plan["entry"], stop_loss=plan["stop"], t1=plan["targets"][0], t2=plan["targets"][1], t3=plan["targets"][2],
                total_contracts=total_contracts, status="PENDING", diagnostic_data=json.dumps(r.get("diagnostic_ledger", {}))
            )
            db.add(new_log)
            print(f"[MISSION LEDGER] Autonomously Logged {r['symbol']} BOUNCE SETUP.")
        db.commit()
    except Exception as e: traceback.print_exc()
    finally: db.close()

async def run_campaign_tracker_loop():
    print(">>> CAMPAIGN ENGINE: Mission Ledger Daemon Online (Radar & Bounce Tracking Active)...")
    while True:
        await sync_radar_campaigns()
        await sync_bounce_campaigns()
        
        db = SessionLocal()
        try:
            active_logs = db.query(CampaignLog).filter(CampaignLog.status.in_(["PENDING", "ACTIVE", "T1_HIT", "T2_HIT"])).all()
            targets_to_fetch = set([log.symbol for log in active_logs])
            
            for symbol in targets_to_fetch:
                candles_5m = await battlebox_pipeline.fetch_live_5m(symbol, limit=288)
                if not candles_5m: continue
                
                symbol_logs = [log for log in active_logs if log.symbol == symbol]
                for log in symbol_logs:
                    old_status = log.status
                    now_utc = datetime.now(timezone.utc)
                    log_created_utc = log.created_at.replace(tzinfo=timezone.utc) if log.created_at.tzinfo is None else log.created_at
                    
                    if log.status == "PENDING" and (now_utc - log_created_utc).total_seconds() > 14400:
                        log.status = "EXPIRED"
                        log.closed_at = now_utc
                        log.updated_at = now_utc
                        continue

                    last_check_ts = int(log.updated_at.timestamp()) if log.updated_at else int(log_created_utc.timestamp())
                    unseen_candles = [c for c in candles_5m if int(c["time"]) >= last_check_ts - 300]
                    
                    for candle in unseen_candles:
                        if log.status in ["CLOSED_WIN", "CLOSED_LOSS", "CLOSED_SCRATCH", "EXPIRED", "STAND_DOWN"]: break 
                        _evaluate_state(log, float(candle["high"]), float(candle["low"]))
                        
                    if old_status != log.status:
                        log.updated_at = now_utc
            db.commit()
        except Exception as e: traceback.print_exc()
        finally: db.close()
        await asyncio.sleep(60)