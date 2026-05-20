# ledger_closing_engine.py
# ==============================================================================
# KABRODA LEDGER CLOSING ENGINE
# Purpose: Autonomously audits live prices against open CampaignLog entries.
# Enforces PnL realities to feed the MAS RAG Memory Bank.
# ==============================================================================

import asyncio
from datetime import datetime, timezone
import traceback
import ccxt.async_support as ccxt
from database import SessionLocal, CampaignLog

# Use a dedicated lightweight exchange instance strictly for price checking
_ticker_exchange = ccxt.mexc({"enableRateLimit": True})

async def _get_live_price(symbol: str) -> float:
    try:
        # ccxt expects 'BTC/USDT', ensure format is correct
        fmt_sym = symbol.replace("USDT", "/USDT") if "/" not in symbol else symbol
        ticker = await _ticker_exchange.fetch_ticker(fmt_sym)
        return float(ticker['last'])
    except Exception as e:
        print(f"Ledger Ticker Error for {symbol}: {e}")
        return 0.0

async def run_ledger_audit_loop():
    print(">>> LEDGER ENGINE: Initializing Background PnL Auditor...")
    
    while True:
        db = SessionLocal()
        try:
            # Fetch all trades that the MAS approved but haven't been closed yet
            # AUDIT FIX: Used .is_(None) to comply with SQLAlchemy syntax
            open_campaigns = db.query(CampaignLog).filter(
                CampaignLog.mas_approval_status == 'APPROVED',
                CampaignLog.closed_at.is_(None)
            ).all()

            if not open_campaigns:
                await asyncio.sleep(60) # Sleep for 60 seconds if no open trades
                continue

            for campaign in open_campaigns:
                live_price = await _get_live_price(campaign.symbol)
                if live_price == 0.0:
                    continue

                now_utc = datetime.now(timezone.utc)
                closed = False
                pnl = 0.0

                # Risk Box Size (1R)
                box_size = abs(campaign.entry_price - campaign.stop_loss)
                if box_size == 0: box_size = campaign.entry_price * 0.01

                # --- LONG LOGIC ---
                if campaign.bias == 'LONG':
                    if live_price <= campaign.stop_loss:
                        # Hard Stop Hit (-1R)
                        campaign.status = 'CLOSED_LOSS'
                        pnl = -box_size
                        closed = True
                        print(f"|| LEDGER UPDATE || {campaign.symbol} LONG Stopped Out at {live_price}.")
                    
                    elif live_price >= campaign.t1:
                        # Target 1 Hit (+1R) 
                        # (For Phase 1, we close the ledger at T1 to build the win rate memory)
                        campaign.status = 'CLOSED_WIN'
                        pnl = box_size
                        closed = True
                        print(f"|| LEDGER UPDATE || {campaign.symbol} LONG Target 1 Hit at {live_price}.")

                # --- SHORT LOGIC ---
                elif campaign.bias == 'SHORT':
                    if live_price >= campaign.stop_loss:
                        # Hard Stop Hit (-1R)
                        campaign.status = 'CLOSED_LOSS'
                        pnl = -box_size
                        closed = True
                        print(f"|| LEDGER UPDATE || {campaign.symbol} SHORT Stopped Out at {live_price}.")
                    
                    elif live_price <= campaign.t1:
                        # Target 1 Hit (+1R)
                        campaign.status = 'CLOSED_WIN'
                        pnl = box_size
                        closed = True
                        print(f"|| LEDGER UPDATE || {campaign.symbol} SHORT Target 1 Hit at {live_price}.")

                # Update the database if the trade concluded
                if closed:
                    campaign.realized_pnl = pnl
                    campaign.closed_at = now_utc
                    db.commit()

        except Exception as e:
            print(f"Ledger Audit Loop Error: {e}")
            traceback.print_exc()
        finally:
            db.close()

        # Audit every 60 seconds
        await asyncio.sleep(60)