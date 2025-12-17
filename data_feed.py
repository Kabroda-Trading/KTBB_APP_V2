from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple, List

import requests
from zoneinfo import ZoneInfo
import time

from volume_profile import compute_volume_profile_from_candles


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


def _today0_in_tz(tz: str) -> datetime:
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
    v: float  # volume


class BinanceUS:
    BASE = "https://api.binance.us"

    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        url = f"{self.BASE}{path}"
        r = requests.get(url, params=params, timeout=12)
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
            # [ openTime, open, high, low, close, volume, ...]
            out.append(
                Candle(
                    open_time_ms=int(row[0]),
                    o=float(row[1]),
                    h=float(row[2]),
                    l=float(row[3]),
                    c=float(row[4]),
                    v=float(row[5]),
                )
            )
        return out


class CoinbaseExchange:
    BASE = "https://api.exchange.coinbase.com"

    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        url = f"{self.BASE}{path}"
        r = requests.get(url, params=params, timeout=12, headers={"User-Agent": "ktbb-app"})
        if r.status_code != 200:
            raise MarketDataError(f"Coinbase {path} HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    def _product(self, symbol: str) -> str:
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
        # returns [time, low, high, open, close, volume] newest-first
        out: List[Candle] = []
        for row in reversed(j):
            t = datetime.fromtimestamp(int(row[0]), tz=timezone.utc)
            out.append(
                Candle(
                    open_time_ms=_ms(t),
                    o=float(row[3]),
                    h=float(row[2]),
                    l=float(row[1]),
                    c=float(row[4]),
                    v=float(row[5]),
                )
            )
        return out


def _provider():
    p = (os.getenv("DATA_PROVIDER") or "binanceus").strip().lower()
    if p == "coinbase":
        return CoinbaseExchange()
    return BinanceUS()


# ----------------------------
# Session candle logic
# ----------------------------
def _session_open_dt(session_tz: str, session_open_hhmm: str) -> datetime:
    z = ZoneInfo(session_tz)
    today0 = _today0_in_tz(session_tz)
    hh, mm = session_open_hhmm.split(":")
    return today0.replace(hour=int(hh), minute=int(mm), tzinfo=z)


def _session_30m_window(session_tz: str, session_open_hhmm: str = "06:00") -> Tuple[datetime, datetime]:
    """
    Target candle = 06:30–07:00 (session_tz), converted to UTC for queries.
    """
    open_dt = _session_open_dt(session_tz, session_open_hhmm)
    start_local = open_dt + timedelta(minutes=30)
    end_local = open_dt + timedelta(minutes=60)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _morning_window(session_tz: str, session_open_hhmm: str = "06:00") -> Tuple[datetime, datetime]:
    """
    Morning FRVP = 4 hours before session open → session open, in session_tz.
    """
    open_dt = _session_open_dt(session_tz, session_open_hhmm)
    start_local = open_dt - timedelta(hours=4)
    end_local = open_dt
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _pick_candle_by_open(candles: List[Candle], target_open_ms: int) -> Optional[Candle]:
    for c in candles:
        if c.open_time_ms == target_open_ms:
            return c
    return None


def _fetch_window_candles(prov: Any, symbol: str, start_utc: datetime, end_utc: datetime, granularity: str) -> List[Candle]:
    if isinstance(prov, BinanceUS):
        return prov.klines(symbol, granularity, start_ms=_ms(start_utc), end_ms=_ms(end_utc), limit=1000)

    # Coinbase granularities: 300, 900, 3600, 21600, 86400
    g_map = {"5m": 300, "15m": 900, "1h": 3600, "6h": 21600}
    sec = g_map.get(granularity, 900)
    return prov.candles(symbol, sec, start=start_utc, end=end_utc)


# ----------------------------
# Public builder used by DMR pipeline
# ----------------------------
def build_auto_inputs(symbol: str = "BTCUSDT", session_tz: str = "UTC") -> Dict[str, Any]:
    symbol = resolve_symbol(symbol)
    session_tz = (session_tz or "UTC").strip() or "UTC"
    session_open = os.getenv("SESSION_OPEN", "06:00")

    prov = _provider()

    t0 = time.perf_counter()
    def mark(name):
        dt = time.perf_counter() - t0
        print(f"[data_feed] {name} @ {dt:.2f}s")
    mark("start")
    mark("price_ok")
    mark("or_ok")
    mark("morning_ok")
    mark("f24_ok")
    mark("weekly_ok")
    mark("h1h4_ok")
    mark("done")
    # last price
    try:
        last_price = float(prov.price(symbol))
    except Exception:
        last_price = float(CoinbaseExchange().price(symbol))

    # 30m session candle (06:30–07:00 session_tz)
    start_utc, end_utc = _session_30m_window(session_tz=session_tz, session_open_hhmm=session_open)
    range_high = range_low = None
    try:
        candles = _fetch_window_candles(prov, symbol, start_utc, end_utc, "30m") if isinstance(prov, BinanceUS) else _fetch_window_candles(prov, symbol, start_utc, end_utc, "15m")
        # Binance: candle open time equals start_utc
        c = _pick_candle_by_open(candles, _ms(start_utc))
        if c:
            range_high, range_low = c.h, c.l
        elif candles:
            range_high, range_low = candles[0].h, candles[0].l
    except Exception:
        pass

    # Morning FRVP (4h pre-open)
    morn_start, morn_end = _morning_window(session_tz=session_tz, session_open_hhmm=session_open)
    morn_candles: List[Candle] = []
    try:
        morn_candles = _fetch_window_candles(prov, symbol, morn_start, morn_end, "5m")
    except Exception:
        try:
            morn_candles = _fetch_window_candles(CoinbaseExchange(), symbol, morn_start, morn_end, "5m")
        except Exception:
            morn_candles = []

    morn_vp = compute_volume_profile_from_candles(morn_candles) if morn_candles else None

    # Fixed 24h FRVP
    now = _utc_now()
    f24_start = now - timedelta(hours=24)
    f24_candles: List[Candle] = []
    try:
        f24_candles = _fetch_window_candles(prov, symbol, f24_start, now, "15m")
    except Exception:
        try:
            f24_candles = _fetch_window_candles(CoinbaseExchange(), symbol, f24_start, now, "15m")
        except Exception:
            f24_candles = []

    f24_vp = compute_volume_profile_from_candles(f24_candles) if f24_candles else None

    # Weekly “VRVP-ish” (last 7d)
    wk_start = now - timedelta(days=7)
    wk_candles: List[Candle] = []
    try:
        wk_candles = _fetch_window_candles(prov, symbol, wk_start, now, "1h")
    except Exception:
        try:
            wk_candles = _fetch_window_candles(CoinbaseExchange(), symbol, wk_start, now, "1h")
        except Exception:
            wk_candles = []

    wk_vp = compute_volume_profile_from_candles(wk_candles) if wk_candles else None

    # H1/H4 anchors: quick swing hi/lo (kept as HTF shelves input to SSE)
    h1_supply = h1_demand = None
    h4_supply = h4_demand = None
    try:
        if isinstance(prov, BinanceUS):
            h1 = prov.klines(symbol, "1h", start_ms=_ms(now - timedelta(hours=24)), end_ms=_ms(now), limit=200)
            h4 = prov.klines(symbol, "4h", start_ms=_ms(now - timedelta(days=7)), end_ms=_ms(now), limit=200)
        else:
            h1 = prov.candles(symbol, 3600, start=now - timedelta(hours=24), end=now)
            h4 = prov.candles(symbol, 14400, start=now - timedelta(days=7), end=now)

        if h1:
            h1_supply = max(c.h for c in h1)
            h1_demand = min(c.l for c in h1)
        if h4:
            h4_supply = max(c.h for c in h4)
            h4_demand = min(c.l for c in h4)
    except Exception:
        pass

    return {
        "date": datetime.now(ZoneInfo(session_tz)).strftime("%Y-%m-%d"),
        "symbol": symbol,
        "session_tz": session_tz,
        "last_price": last_price,

        # Opening range keys (keep both new + legacy)
        "r30_high": range_high,
        "r30_low": range_low,
        "range30m_high": range_high,
        "range30m_low": range_low,
        "range_30m": {"high": range_high, "low": range_low},

        # HTF shelves inputs for SSE
        "h1_supply": h1_supply,
        "h1_demand": h1_demand,
        "h4_supply": h4_supply,
        "h4_demand": h4_demand,

        # Profiles
        "weekly_vah": wk_vp.vah if wk_vp else None,
        "weekly_val": wk_vp.val if wk_vp else None,
        "weekly_poc": wk_vp.poc if wk_vp else None,

        "f24_vah": f24_vp.vah if f24_vp else None,
        "f24_val": f24_vp.val if f24_vp else None,
        "f24_poc": f24_vp.poc if f24_vp else None,

        "morn_vah": morn_vp.vah if morn_vp else None,
        "morn_val": morn_vp.val if morn_vp else None,
        "morn_poc": morn_vp.poc if morn_vp else None,

        "news": [],
        "sentiment": None,
    }
