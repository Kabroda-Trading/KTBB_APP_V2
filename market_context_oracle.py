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
    """
    Synchronous fetch operation isolated for threading.
    Pulls the S&P 500 (^GSPC), US Dollar Index (DX-Y.NYB), and Volatility Index (^VIX).
    """
    try:
        # Request the last 5 days to calculate a simple micro-trend
        tickers = yf.Tickers("^GSPC DX-Y.NYB ^VIX")
        
        spx = tickers.tickers["^GSPC"].history(period="5d")
        dxy = tickers.tickers["DX-Y.NYB"].history(period="5d")
        vix = tickers.tickers["^VIX"].history(period="5d")

        if spx.empty or dxy.empty or vix.empty:
            return {"status": "ERROR", "message": "Incomplete data from Yahoo Finance."}

        # Calculate simple Day-over-Day (DoD) trends
        spx_now = spx["Close"].iloc[-1]
        spx_prev = spx["Close"].iloc[-2]
        spx_trend = "BULLISH" if spx_now > spx_prev else "BEARISH"

        dxy_now = dxy["Close"].iloc[-1]
        dxy_prev = dxy["Close"].iloc[-2]
        dxy_trend = "BULLISH" if dxy_now > dxy_prev else "BEARISH"

        vix_now = vix["Close"].iloc[-1]
        vix_prev = vix["Close"].iloc[-2]
        vix_state = "ELEVATED (FEAR)" if vix_now > 20.0 else "CALM"

        # Determine Global Risk Posture
        # Risk-On: Equities up, Dollar down
        # Risk-Off: Equities down, Dollar up, or VIX elevated
        risk_posture = "NEUTRAL"
        if spx_trend == "BULLISH" and dxy_trend == "BEARISH" and vix_state == "CALM":
            risk_posture = "RISK-ON (FAVORABLE FOR CRYPTO)"
        elif spx_trend == "BEARISH" and dxy_trend == "BULLISH":
            risk_posture = "RISK-OFF (HOSTILE FOR CRYPTO)"
        elif vix_state == "ELEVATED (FEAR)":
            risk_posture = "HIGH VOLATILITY (PRESERVE CAPITAL)"

        return {
            "status": "SUCCESS",
            "risk_posture": risk_posture,
            "sp500": {
                "price": round(spx_now, 2),
                "trend_24h": spx_trend
            },
            "us_dollar_dxy": {
                "price": round(dxy_now, 2),
                "trend_24h": dxy_trend
            },
            "vix": {
                "price": round(vix_now, 2),
                "state": vix_state
            }
        }

    except Exception as e:
        print(f"[MACRO ORACLE ERROR] Failed to fetch traditional finance data: {e}")
        return {"status": "ERROR", "message": str(e)}

async def get_global_macro_context() -> Dict[str, Any]:
    """
    Async wrapper to prevent blocking the main FastAPI event loop.
    Returns the global risk posture for MAS injection.
    """
    return await asyncio.to_thread(_fetch_macro_sync)

# For quick local testing
if __name__ == "__main__":
    result = asyncio.run(get_global_macro_context())
    print(result)