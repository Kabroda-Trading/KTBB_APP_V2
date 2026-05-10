# bounce_engine.py
# ==============================================================================
# KABRODA BOUNCE ENGINE (MEAN-REVERSION CONTINUATION DAEMON)
# Purpose: Identifies Titanium Floors/Ceilings & fires dual-payload email alerts.
# UPGRADE: Integrated Pipeline SSOT (Harmonic Matrix) & Sentinel Zone Lock.
# ==============================================================================
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
import asyncio

import battlebox_pipeline
import gravity_math

TARGETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

# --- SENTINEL ZONE LOCK (Anti-Machine Gun Protocol) ---
# Format: { "SYMBOL": {"price": 0.0, "bias": "BULLISH", "timestamp": unix_seconds} }
_ZONE_LOCK = {}  

# --- EMAIL NOTIFICATION SYSTEM (DUAL PAYLOAD) ---
def send_bounce_alert(symbol: str, grade: str, bias: str, entry: float, stop: float, t1: float, t2: float, t3: float):
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    smtp_dest = os.getenv("SMTP_DEST") 
    
    if not all([smtp_user, smtp_pass, smtp_dest]):
        print(f"[BOUNCE ENGINE] Alert triggered for {symbol}, but SMTP credentials missing.")
        return

    grade_color = "#22c55e" if grade == "GRADE A" else "#eab308"
    vector_color = "#22c55e" if bias == "BULLISH" else "#ef4444"

    text_content = f"""
KABRODA SYSTEM ALERT: {grade} BOUNCE DETECTED

Asset: {symbol}
Vector: {bias}

RESTING LIMIT ENTRY: ${entry:.2f}
STOP LOSS (ARMOR): ${stop:.2f}
TARGET 1 (SCALE): ${t1:.2f}
TARGET 2 (STRUCTURE): ${t2:.2f}
TARGET 3 (MACRO PUSH): ${t3:.2f}

Log into the Admin Suite to view structural alignment.
"""

    html_content = f"""
    <html>
    <body style="background-color: #020617; color: #f8fafc; font-family: 'Courier New', monospace; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; border: 1px solid #334155; border-radius: 6px; padding: 20px; background-color: #0f172a;">
            <h2 style="color: #a855f7; border-bottom: 1px solid #334155; padding-bottom: 10px; margin-top: 0;">KABRODA SYSTEM ALERT</h2>
            <h3 style="color: {grade_color}; font-size: 20px;">{grade} BOUNCE DETECTED</h3>
            
            <div style="margin-bottom: 20px;">
                <div style="color: #94a3b8; font-size: 14px;">ASSET: <span style="color: #fff; font-weight: bold; font-size: 16px;">{symbol}</span></div>
                <div style="color: #94a3b8; font-size: 14px;">VECTOR: <span style="color: {vector_color}; font-weight: bold; font-size: 16px;">{bias}</span></div>
            </div>
            
            <div style="background-color: #020617; padding: 15px; border-radius: 4px; border-left: 4px solid {grade_color}; margin-bottom: 20px;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 5px;">
                    <span style="color: #94a3b8;">LIMIT ENTRY:</span>
                    <span style="color: #fff; font-weight: bold;">${entry:.2f}</span>
                </div>
                <div style="display: flex; justify-content: space-between; margin-bottom: 5px;">
                    <span style="color: #94a3b8;">STOP LOSS:</span>
                    <span style="color: #ef4444; font-weight: bold;">${stop:.2f}</span>
                </div>
                <div style="display: flex; justify-content: space-between; margin-bottom: 5px;">
                    <span style="color: #94a3b8;">TARGET 1 (SCALE):</span>
                    <span style="color: #eab308; font-weight: bold;">${t1:.2f}</span>
                </div>
                <div style="display: flex; justify-content: space-between; margin-bottom: 5px;">
                    <span style="color: #94a3b8;">TARGET 2 (STRUCTURE):</span>
                    <span style="color: #0ea5e9; font-weight: bold;">${t2:.2f}</span>
                </div>
                <div style="display: flex; justify-content: space-between;">
                    <span style="color: #94a3b8;">TARGET 3 (MACRO PUSH):</span>
                    <span style="color: #a855f7; font-weight: bold;">${t3:.2f}</span>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg['Subject'] = f"[{grade}] {symbol} Kabroda Limit Order"
    msg['From'] = smtp_user
    msg['To'] = smtp_dest
    msg.attach(MIMEText(text_content, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        print(f"[BOUNCE ENGINE] >>> DUAL-PAYLOAD EMAIL ALERT SENT FOR {symbol} <<<")
    except Exception as e:
        print(f"[BOUNCE ENGINE] Failed to send email: {e}")

# --- QUANTITATIVE ENGINE ---
async def scan_sector():
    results = []
    
    # Let the SSOT Pipeline do the heavy lifting
    tasks = [battlebox_pipeline.get_live_battlebox(sym, "MANUAL", manual_id="us_ny_futures") for sym in TARGETS]
    pipeline_results = await asyncio.gather(*tasks, return_exceptions=True)

    for symbol, data in zip(TARGETS, pipeline_results):
        try:
            if isinstance(data, Exception) or data.get("status") == "ERROR" or data.get("status") == "CALIBRATING": 
                continue
            
            price = float(data.get("price", 0))
            context = data.get("battlebox", {}).get("context", {})
            fuel_gauge = context.get("fuel_gauge", {})
            kde_peaks = context.get("kde_peaks", [])
            
            macro_bias = context.get("macro_bias", "NEUTRAL")
            micro_state = context.get("micro_state", "CHOP")
            
            j_15m = fuel_gauge.get("15M_JEWEL", {})
            sma_200 = j_15m.get("sma200", 0)
            if not sma_200: continue

            base_ledger = {
                "15m_200_sma": sma_200, 
                "pipeline_state": micro_state,
                "macro_bias": macro_bias,
                "gravity_wall": "N/A"
            }

            # 1. Pipeline Gate (SSOT Enforcement)
            allowed_bias = "NONE"
            if macro_bias == "BULLISH":
                if micro_state == "SWEET_ZONE": allowed_bias = "BULLISH"        # Primary Continuation
                elif micro_state == "PULLBACK": allowed_bias = "BEARISH"        # Counter-trend Scalp
            elif macro_bias == "BEARISH":
                if micro_state == "SWEET_ZONE_BEAR": allowed_bias = "BEARISH"   # Primary Continuation
                elif micro_state == "PULLBACK": allowed_bias = "BULLISH"        # Counter-trend Scalp

            if allowed_bias == "NONE":
                base_ledger["rejection_reason"] = f"Pipeline lockdown ({micro_state})"
                results.append({"symbol": symbol, "grade": "STAND DOWN", "briefing": f"PIPELINE LOCKDOWN: {micro_state}.", "price": price, "diagnostic_ledger": base_ledger})
                continue
            
            # 2. Touch the Mean
            dist_to_sma = abs(price - sma_200) / sma_200
            if dist_to_sma > 0.005: 
                base_ledger["rejection_reason"] = "Price > 0.5% away from 200 SMA"
                results.append({"symbol": symbol, "grade": "STAND DOWN", "briefing": "Waiting for pullback to 200 SMA.", "price": price, "diagnostic_ledger": base_ledger})
                continue

            # 3. Find Gravity Wall Overlap
            closest_wall = None
            wall_dist = 999.0
            for p in kde_peaks:
                if p["intensity"] in ["HEAVY", "MAXIMUM"]:
                    d = abs(sma_200 - p["price"]) / sma_200
                    if d < 0.005 and d < wall_dist:
                        closest_wall = p
                        wall_dist = d
            
            if not closest_wall:
                base_ledger["rejection_reason"] = "No structural wall at 200 SMA"
                results.append({"symbol": symbol, "grade": "STAND DOWN", "briefing": "No Gravity Wall support at 200 SMA.", "price": price, "diagnostic_ledger": base_ledger})
                continue

            wall_price = closest_wall["price"]
            base_ledger["gravity_wall"] = wall_price

            # 4. SENTINEL ZONE LOCK (Stop the Machine Gun)
            now_ts = datetime.now(timezone.utc).timestamp()
            if symbol in _ZONE_LOCK:
                lock = _ZONE_LOCK[symbol]
                locked_dist = abs(wall_price - lock["price"]) / lock["price"]
                # If same zone (within 1%) and same direction, and less than 12 hours ago
                if locked_dist < 0.01 and lock["bias"] == allowed_bias and (now_ts - lock["timestamp"]) < 43200:
                    base_ledger["rejection_reason"] = "Zone locked (Cooldown Active)"
                    results.append({"symbol": symbol, "grade": "STAND DOWN", "briefing": "ZONE LOCKED: Engine already fired here.", "price": price, "diagnostic_ledger": base_ledger})
                    continue

            # 5. Define Trade Parameters & Scale-Out Targets
            if allowed_bias == "BULLISH":
                entry = min(sma_200, wall_price)
                stop = wall_price * 0.995 # 0.5% structural armor
                risk = entry - stop
                valid_ceilings = sorted([p["price"] for p in kde_peaks if p["price"] > entry + (risk * 1.5)])
                t1 = valid_ceilings[0] if len(valid_ceilings) > 0 else entry + (risk * 1.5)
                t2 = valid_ceilings[1] if len(valid_ceilings) > 1 else entry + (risk * 3.0)
                t3 = valid_ceilings[2] if len(valid_ceilings) > 2 else entry * 1.03
            else:
                entry = max(sma_200, wall_price)
                stop = wall_price * 1.005 # 0.5% structural armor
                risk = stop - entry
                valid_floors = sorted([p["price"] for p in kde_peaks if p["price"] < entry - (risk * 1.5)], reverse=True)
                t1 = valid_floors[0] if len(valid_floors) > 0 else entry - (risk * 1.5)
                t2 = valid_floors[1] if len(valid_floors) > 1 else entry - (risk * 3.0)
                t3 = valid_floors[2] if len(valid_floors) > 2 else entry * 0.97
            
            grade = "GRADE A"
            briefing = f"🟢 SSOT CLEARED ({micro_state}): 200 SMA + Gravity Wall Overlap."
            
            # 6. Fire Alert & Lock the Zone
            send_bounce_alert(symbol, grade, allowed_bias, entry, stop, t1, t2, t3)
            _ZONE_LOCK[symbol] = {"price": wall_price, "bias": allowed_bias, "timestamp": now_ts}
            
            plan = {"valid": True, "bias": allowed_bias, "entry": entry, "stop": stop, "targets": [t1, t2, t3]}
            
            results.append({
                "symbol": symbol, "grade": grade, "briefing": briefing, "color_code": "GREEN",
                "price": price, "plan": plan, "macro_bias": macro_bias,
                "diagnostic_ledger": base_ledger
            })

        except Exception as e:
            print(f"[BOUNCE ENGINE] Error processing {symbol}: {e}")

    return results