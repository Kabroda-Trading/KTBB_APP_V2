# data_feed.py
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple, List

import requests
import feedparser  # <-- ADDED
from zoneinfo import ZoneInfo
from volume_profile import compute_volume_profile_from_candles

# ... (Keep all your existing symbol/Candle/BinanceUS/Coinbase classes EXACTLY as they were) ...
# ... (I am truncating the middle part for brevity, assume the classes from your upload are here) ...

# ------------------------------------------------------------------
# NEW: News & Calendar Fetcher
# ------------------------------------------------------------------
def _fetch_crypto_news(limit: int = 5) -> List[str]:
    """
    Fetches latest headlines from CoinTelegraph RSS (public/free).
    """
    url = "https://cointelegraph.com/rss"
    out = []
    try:
        feed = feedparser.parse(url)
        # Check if feed parsed correctly
        if feed.bozo and not feed.entries:
            return ["- (News feed unavailable)"]
            
        for entry in feed.entries[:limit]:
            title = entry.title.strip()
            # Basic cleanup
            title = re.sub(r'<[^>]+>', '', title)
            out.append(f"- {title}")
    except Exception:
        out.append("- (News fetch failed)")
    
    return out

def _fetch_calendar_stub() -> List[str]:
    """
    Placeholder for high-impact events. 
    Real calendar APIs are expensive/complex.
    """
    return [
        "- Check ForexFactory for High Impact USD Events (CPI/FOMC/NFP).",
        "- Check CryptoCraft for major protocol unlocks."
    ]

# ------------------------------------------------------------------
# Public builder (Modified to include News)
# ------------------------------------------------------------------
def build_auto_inputs(symbol: str = "BTCUSDT", session_tz: str = "UTC") -> Dict[str, Any]:
    # ... (Keep your existing provider setup, timing, and price fetching logic) ...
    
    # [Rest of your existing function code for candles, shelves, FRVP...]
    # ...
    
    # --- INSERT THIS AT THE END BEFORE RETURN ---
    news_headlines = _fetch_crypto_news()
    calendar_events = _fetch_calendar_stub()

    return {
        # ... (Your existing return keys) ...
        "date": datetime.now(ZoneInfo(session_tz)).strftime("%Y-%m-%d"),
        "symbol": symbol,
        "session_tz": session_tz,
        # ...
        "levels": {}, # Filled by SSE later
        
        # NEW KEYS
        "news": news_headlines,
        "events": calendar_events,
        
        # ... (Rest of existing keys) ...
    }

def get_inputs(*, symbol: str, date: Optional[str] = None, session_tz: str = "UTC") -> Dict[str, Any]:
    # 1) Pull raw inputs
    inputs = build_auto_inputs(symbol=symbol, session_tz=session_tz)

    # 2) Compute SSE levels
    import sse_engine
    sse = sse_engine.compute_sse_levels(inputs)
    inputs.update(sse)

    return inputs