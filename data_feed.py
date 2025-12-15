# data_feed.py
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple, List

import requests
from zoneinfo import ZoneInfo


# ----------------------------
# Symbol normalization
# ----------------------------
def resolve_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s:
        return "BTCUSDT"
    if s.endswith("USDT"):
        return s
    if s in ("BTC", "XBT"):
        return "BTCUSDT"
    if re.fullmatch(r"[A-Z0-9]{2,12}", s):
        return f"{s}USDT"
    return "BTCUSDT"


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today_in_tz(tz: str) -> datetime:
    z = ZoneInfo(tz)
    now_local = datetime.now(z)
    return now_local.replace(hour=0, minute=0, second=0, microsecond=0)


# ----------------------------
# Exchange clients (public APIs)
# ----------------------------
class MarketDataError(RuntimeError):
    pass


@dataclass
class Candle:
    open_time_ms: int
    o: float
    h: float
    l: float
    c: float


class BinanceUS:
    """
    Public endpoints (no auth). Works where Binance.US is available.
    """
    BASE = "https://api.binance.us"

    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        url = f"{self.BASE}{path}"
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            raise MarketDataError(f"BinanceUS {path} HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    def price(self, symbol: str) -> float:
        j = self._get("/api/v3/ticker/price", {"symbol": symbol})
        return float(j["price"])

    def klines(self, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 500) -> List[Candle]:
        j = self._get(
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "startTime": start_ms, "endTime": end_ms, "limit": limit},
        )
        out: List[Candle] = []
        for row in j:
            # [ openTime, open, high, low, close, volume, closeTime, ...]
            out.append(
                Candle(
                    open_time_ms=int(row[0]),
                    o=float(row[1]),
                    h=float(row[2]),
                    l=float(row[3]),
                    c=float(row[4]),
                )
            )
        return out


class CoinbaseExchange:
    """
    Public candles endpoint (no auth).
    Uses BTC-USD (not USDT) as a fallback.
    """
    BASE = "https://api.exchange.coinbase.com"

    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        url = f"{self.BASE}{path}"
        r = requests.get(url, params=params, timeout=10, headers={"User-Agent": "ktbb-app"})
        if r.status_code != 200:
            raise MarketDataError(f"Coinbase {path} HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    def _product(self, symbol: str) -> str:
        # BTCUSDT -> BTC-USD fallback mapping
        base = symbol.replace("USDT", "")
        return f"{base}-USD"

    def price(self, symbol: str) -> float:
        prod = self._product(symbol)
        j = self._get(f"/products/{prod}/ticker", {})
        return float(j["price"])

    def candles(self, symbol: str, granularity_sec: int, start: datetime, end: datetime) -> List[Candle]:
        prod = self._product(symbol)
        j = self._get(
            f"/products/{prod}/candles",
            {"granularity": granularity_sec, "start": start.isoformat(), "end": end.isoformat()},
        )
        # returns list of [time, low, high, open, close, volume] newest-first
        out: List[Candle] = []
        for row in reversed(j):
            t = datetime.fromtimestamp(int(row[0]), tz=timezone.utc)
            out.append(Candle(open_time_ms=_ms(t), o=float(row[3]), h=float(row[2]), l=float(row[1]), c=float(row[4])))
        return out


def _provider():
    # You can set DATA_PROVIDER=binanceus or coinbase
    p = (os.getenv("DATA_PROVIDER") or "binanceus").strip().lower()
    if p == "coinbase":
        return CoinbaseExchange()
    return BinanceUS()


# ----------------------------
# Session candle logic
# ----------------------------
def _session_30m_window(session_tz: str, session_open_hhmm: str = "06:00") -> Tuple[datetime, datetime]:
    """
    We want the 2nd half-hour of the first hour after session open:
      session open = 06:00
      target candle = 06:30–07:00 (in session_tz)
    """
    z = ZoneInfo(session_tz)
    today0 = _today_in_tz(session_tz)

    hh, mm = session_open_hhmm.split(":")
    open_dt = today0.replace(hour=int(hh), minute=int(mm))
    start = open_dt + timedelta(minutes=30)
    end = open_dt + timedelta(minutes=60)

    # convert to UTC for exchange queries
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _pick_candle_by_open(candles: List[Candle], target_open_ms: int) -> Optional[Candle]:
    for c in candles:
        if c.open_time_ms == target_open_ms:
            return c
    return None


# ----------------------------
# Public builder used by main.py
# ----------------------------
def build_auto_inputs(symbol: str = "BTCUSDT", session_tz: str = "UTC") -> Dict[str, Any]:
    symbol = resolve_symbol(symbol)
    session_tz = (session_tz or "UTC").strip() or "UTC"

    prov = _provider()

    # last price
    last_price = None
    try:
        last_price = float(prov.price(symbol))
    except Exception:
        # fallback provider
        prov2 = CoinbaseExchange()
        last_price = float(prov2.price(symbol))

    # 30m session candle (06:30–07:00 in session_tz)
    start_utc, end_utc = _session_30m_window(session_tz=session_tz, session_open_hhmm=os.getenv("SESSION_OPEN", "06:00"))

    range_high = None
    range_low = None
    try:
        if isinstance(prov, BinanceUS):
            # pull 30m klines spanning the window
            candles = prov.klines(symbol, "30m", start_ms=_ms(start_utc), end_ms=_ms(end_utc), limit=5)
            # the candle open time equals start_utc
            c = _pick_candle_by_open(candles, _ms(start_utc))
            if c:
                range_high, range_low = c.h, c.l
        else:
            candles = prov.candles(symbol, 1800, start=start_utc, end=end_utc)
            # coinbase returns candle open at start_utc
            if candles:
                c = candles[0]
                range_high, range_low = c.h, c.l
    except Exception:
        # fallback to coinbase if binanceus failed
        prov2 = CoinbaseExchange()
        candles = prov2.candles(symbol, 1800, start=start_utc, end=end_utc)
        if candles:
            c = candles[0]
            range_high, range_low = c.h, c.l

    # H1/H4 anchors: compute simple “supply/demand” from recent high/low
    # (This is not your final “perfect shelves”, but it gives SSE real anchors.)
    now = _utc_now()
    h1_supply = h1_demand = None
    h4_supply = h4_demand = None

    try:
        if isinstance(prov, BinanceUS):
            # 1H lookback 24 hours, 4H lookback 7 days
            h1 = prov.klines(symbol, "1h", start_ms=_ms(now - timedelta(hours=24)), end_ms=_ms(now), limit=200)
            h4 = prov.klines(symbol, "4h", start_ms=_ms(now - timedelta(days=7)), end_ms=_ms(now), limit=200)
        else:
            # coinbase: 1h granularity 3600, 4h granularity 14400
            h1 = prov.candles(symbol, 3600, start=now - timedelta(hours=24), end=now)
            h4 = prov.candles(symbol, 14400, start=now - timedelta(days=7), end=now)

        if h1:
            h1_supply = max(c.h for c in h1)
            h1_demand = min(c.l for c in h1)
        if h4:
            h4_supply = max(c.h for c in h4)
            h4_demand = min(c.l for c in h4)

    except Exception:
        # leave as None; SSE will still work off opening range + price
        pass

    return {
        "date": datetime.now(ZoneInfo(session_tz)).strftime("%Y-%m-%d"),
        "symbol": symbol,
        "session_tz": session_tz,
        "last_price": last_price,

        "range30m_high": range_high,
        "range30m_low": range_low,
        "range_30m": {"high": range_high, "low": range_low},

        "h1_supply": h1_supply,
        "h1_demand": h1_demand,
        "h4_supply": h4_supply,
        "h4_demand": h4_demand,

        # You can wire these later from your volume profile module:
        "f24_vah": None,
        "f24_val": None,
        "f24_poc": None,
        "morn_vah": None,
        "morn_val": None,
        "morn_poc": None,

        "news": [],
        "sentiment": None,
    }
