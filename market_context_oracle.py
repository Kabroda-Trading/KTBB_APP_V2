# market_context_oracle.py
# ==============================================================================
# KABRODA MACRO CONTEXT ORACLE v1.0
# Purpose: Fetches traditional finance metrics (SPX, DXY, VIX) to provide 
# external world context to the Multi-Agent System.
# ==============================================================================
import yfinance as yf
import asyncio
from typing import Dict, Any

def _fetch_macro_sync() -> Dict[str, Any]:
    try:
        # We fetch explicitly. If a ticker is invalid, it won't crash the whole batch.
        tickers_list = ["^GSPC", "DX-Y.NYB", "^VIX", "BTC-DOM"]
        data = yf.download(tickers_list, period="5d", group_by='ticker', progress=False)
        
        results = {}
        for t in tickers_list:
            try:
                # Robust index access
                hist = data[t]
                now = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2]
                trend = "BULLISH" if now > prev else "BEARISH"
                results[t] = {"price": round(float(now), 2), "trend": trend}
            except Exception as e:
                print(f"[ORACLE] Failed to parse {t}: {e}")
                results[t] = {"price": 0.0, "trend": "UNKNOWN"}

        # Logic: Risk Posture
        spx = results.get("^GSPC", {})
        dxy = results.get("DX-Y.NYB", {})
        vix = results.get("^VIX", {})
        
        risk_posture = "NEUTRAL"
        if spx.get("trend") == "BULLISH" and dxy.get("trend") == "BEARISH" and vix.get("price", 20) < 20:
            risk_posture = "RISK-ON (CRYPTO FAVORABLE)"
        elif spx.get("trend") == "BEARISH" and dxy.get("trend") == "BULLISH":
            risk_posture = "RISK-OFF (HOSTILE)"
        elif vix.get("price", 0) > 25:
            risk_posture = "HIGH VOLATILITY (PRESERVE CAPITAL)"

        return {
            "status": "SUCCESS",
            "risk_posture": risk_posture,
            "metrics": results
        }
    except Exception as e:
        print(f"[MACRO ORACLE ERROR] {e}")
        return {"status": "ERROR", "message": str(e)}

async def get_global_macro_context() -> Dict[str, Any]:
    return await asyncio.to_thread(_fetch_macro_sync)