# bounce_engine.py
# ==============================================================================
# KABRODA BOUNCE ENGINE (MEAN-REVERSION CONTINUATION DAEMON)
# Purpose: Identifies Titanium Floors/Ceilings & fires email alerts.
# ==============================================================================
import os
import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone
import asyncio

import battlebox_pipeline
import gravity_math

TARGETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
_ALERT_CACHE = {}  # Prevents email spam (tracks alerts per symbol per day)

# --- EMAIL NOTIFICATION SYSTEM (HTML UPGRADE) ---
from email.mime.multipart import MIMEMultipart

def send_bounce_alert(symbol: str, grade: str, bias: str, entry: float, stop: float, t1: float, t2: float):
    now_utc = datetime.now(timezone.utc)
    date_key = now_utc.strftime("%Y-%m-%d")
    cache_key = f"{symbol}_{date_key}"
    
    if cache_key in _ALERT_CACHE:
        return  
        
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    smtp_dest = os.getenv("SMTP_DEST") 
    
    if not all([smtp_user, smtp_pass, smtp_dest]):
        print(f"[BOUNCE ENGINE] Alert triggered for {symbol}, but SMTP credentials missing.")
        return

    # Color code the grade
    grade_color = "#22c55e" if grade == "GRADE A" else "#eab308"
    vector_color = "#22c55e" if bias == "BULLISH" else "#ef4444"

    # Build the HTML payload
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
                <div style="display: flex; justify-content: space-between;">
                    <span style="color: #94a3b8;">TARGET 2 (RUNNER):</span>
                    <span style="color: #0ea5e9; font-weight: bold;">${t2:.2f}</span>
                </div>
            </div>
            
            <div style="font-size: 12px; color: #64748b; text-align: center;">
                Log into the Admin Suite to view structural alignment.
            </div>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg['Subject'] = f"[{grade}] {symbol} Kabroda Limit Order"
    msg['From'] = smtp_user
    msg['To'] = smtp_dest
    
    # Attach the HTML rendering
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        _ALERT_CACHE[cache_key] = True
        print(f"[BOUNCE ENGINE] >>> HTML EMAIL ALERT SENT FOR {symbol} <<<")
    except Exception as e:
        print(f"[BOUNCE ENGINE] Failed to send email: {e}")

# --- QUANTITATIVE MATH ---
def _calc_sma(prices: list, period: int) -> float:
    if len(prices) < period: return 0.0
    return sum(prices[-period:]) / period

def _calc_frvp_poc(candles: list) -> float:
    if not candles: return 0.0
    bins = {}
    bin_size = float(candles[0]["close"]) * 0.001 
    for c in candles:
        price = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3
        vol = float(c["volume"])
        b = round(price / bin_size) * bin_size
        bins[b] = bins.get(b, 0) + vol
    return max(bins, key=bins.get) if bins else 0.0

async def scan_sector():
    results = []
    
    for symbol in TARGETS:
        try:
            raw_15m = await battlebox_pipeline.fetch_live_15m(symbol, limit=672) # 7 Days
            raw_1h = await battlebox_pipeline.fetch_live_1h(symbol, limit=200)
            raw_daily = await battlebox_pipeline.fetch_live_daily(symbol, limit=30)
            
            if not raw_15m or not raw_1h or not raw_daily: continue
            
            last_price = float(raw_15m[-1]["close"])
            macro_bias = battlebox_pipeline._calculate_weekly_force(raw_daily, int(raw_15m[-1]["time"]))
            
            closes_15m = [float(c["close"]) for c in raw_15m]
            sma_200 = _calc_sma(closes_15m, 200)
            frvp_poc = _calc_frvp_poc(raw_15m)
            
            kde_data = gravity_math.calculate_gravity_kde(symbol)
            macro_fibs = gravity_math.calculate_macro_fibs(raw_daily, [])
            fuel_gauge = battlebox_pipeline._build_fuel_gauge(raw_1h, [], raw_15m)
            rsi_1h = fuel_gauge["1H"]["rsi"]
            
            # 1. Distance & Overlap Checks
            dist_to_sma = abs(last_price - sma_200) / sma_200
            if dist_to_sma > 0.005: 
                results.append({"symbol": symbol, "grade": "STAND DOWN", "briefing": "Price too extended from 200 SMA.", "price": last_price})
                continue
                
            closest_wall = None
            wall_dist = 999.0
            for p in kde_data.get("peaks", []):
                if p["intensity"] in ["HEAVY", "MAXIMUM"]:
                    d = abs(sma_200 - p["price"]) / sma_200
                    if d < 0.005 and d < wall_dist:
                        closest_wall = p
                        wall_dist = d
                        
            if not closest_wall:
                results.append({"symbol": symbol, "grade": "STAND DOWN", "briefing": "No Gravity Wall support at 200 SMA.", "price": last_price})
                continue
                
            # 2. Fuel & Bias Checks
            if macro_bias == "BULLISH":
                if rsi_1h > 55:
                    results.append({"symbol": symbol, "grade": "STAND DOWN", "briefing": f"Bullish Trend, but 1H RSI ({rsi_1h}) is too high (Needs <=55).", "price": last_price})
                    continue
                entry = min(sma_200, closest_wall["price"])
                stop = closest_wall["price"] * 0.995 # 0.5% below wall
                t1 = kde_data["peaks"][0]["price"] if kde_data["peaks"] and kde_data["peaks"][0]["price"] > entry else macro_fibs.get("ext_up_1272", entry*1.02)
                t2 = macro_fibs.get("ext_up_1618", entry*1.05)
                
            elif macro_bias == "BEARISH":
                if rsi_1h < 45:
                    results.append({"symbol": symbol, "grade": "STAND DOWN", "briefing": f"Bearish Trend, but 1H RSI ({rsi_1h}) is exhausted (Needs >=45).", "price": last_price})
                    continue
                entry = max(sma_200, closest_wall["price"])
                stop = closest_wall["price"] * 1.005 # 0.5% above wall
                t1 = kde_data["peaks"][-1]["price"] if kde_data["peaks"] and kde_data["peaks"][-1]["price"] < entry else macro_fibs.get("ext_dn_1272", entry*0.98)
                t2 = macro_fibs.get("ext_dn_1618", entry*0.95)
            else:
                results.append({"symbol": symbol, "grade": "STAND DOWN", "briefing": "Macro Trend is Neutral. No Bounce Trades allowed.", "price": last_price})
                continue
                
            # 3. Grading the Floor (Titanium vs Standard)
            poc_overlap = abs(frvp_poc - closest_wall["price"]) / closest_wall["price"] < 0.005
            grade = "GRADE A" if poc_overlap else "GRADE B"
            briefing = "🟢 TITANIUM BOUNCE: 200 SMA + Gravity Wall + 7D FRVP POC." if grade == "GRADE A" else "🟡 STANDARD BOUNCE: 200 SMA + Gravity Wall."
            
            # Fire Alert
            send_bounce_alert(symbol, grade, macro_bias, entry, stop, t1, t2)
            
            plan = {"valid": True, "bias": macro_bias, "entry": entry, "stop": stop, "targets": [t1, t2, t2]}
            
            results.append({
                "symbol": symbol, "grade": grade, "briefing": briefing, "color_code": "GREEN" if grade=="GRADE A" else "YELLOW",
                "price": last_price, "plan": plan, "macro_bias": macro_bias,
                "diagnostic_ledger": {"1h_rsi_fuel": rsi_1h, "15m_200_sma": sma_200, "7d_frvp_poc": frvp_poc, "gravity_wall": closest_wall["price"]}
            })
            
        except Exception as e:
            print(f"[BOUNCE ENGINE] Error processing {symbol}: {e}")
            
    return results