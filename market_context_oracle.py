# market_context_oracle.py
# ==============================================================================
# KABRODA MACRO CONTEXT ORACLE v1.2
# Purpose: Fetches traditional finance metrics (SPX, DXY, VIX) to provide 
# external world context to the Multi-Agent System.
# FIXED v1.2: yfinance MultiIndex DataFrame crash — handles both MultiIndex
# and single-index DataFrames. Flat fallback no longer reuses same data for
# all tickers (v1.1 bug). Each ticker fails independently to UNKNOWN.
# ==============================================================================
import yfinance as yf
import asyncio
import pandas as pd
from typing import Dict, Any


def _fetch_macro_sync() -> Dict[str, Any]:
    try:
        tickers_list = ["^GSPC", "DX-Y.NYB", "^VIX"]
        data = yf.download(tickers_list, period="5d", group_by='ticker', progress=False)

        results = {}
        is_multi = isinstance(data.columns, pd.MultiIndex)

        for t in tickers_list:
            try:
                if is_multi:
                    # MultiIndex: columns are tuples like ("^GSPC", "Close")
                    close_col = (t, "Close")
                    if close_col in data.columns:
                        close_series = data[close_col]
                    else:
                        # Try Adj Close fallback
                        adj_close_col = (t, "Adj Close")
                        close_series = data[adj_close_col] if adj_close_col in data.columns else data[(t, "Open")]
                else:
                    # Single ticker returned as flat DataFrame — only valid if
                    # yfinance collapsed to one ticker. We can't tell which one
                    # survived, so mark all as UNKNOWN to avoid silent corruption.
                    raise ValueError("Flat DataFrame returned — cannot assign to ticker")

                now = float(close_series.iloc[-1])
                prev = float(close_series.iloc[-2])
                trend = "BULLISH" if now > prev else "BEARISH"
                results[t] = {"price": round(now, 2), "trend": trend}
            except Exception as e:
                print(f"[ORACLE] Failed to parse {t}: {e}")
                results[t] = {"price": 0.0, "trend": "UNKNOWN"}

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