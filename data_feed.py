# data_feed.py
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol

from zoneinfo import ZoneInfo

import requests


# ----------------------------
# Config
# ----------------------------
DEFAULT_PROVIDER = os.getenv("KTBB_DATA_PROVIDER", "binanceus").strip().lower()
SESSION_OPEN_LOCAL_HHMM = os.getenv("KTBB_SESSION_OPEN_LOCAL", "06:00").strip()  # user-local session open
OR_OFFSET_MIN = int(os.getenv("KTBB_OR_OFFSET_MIN", "30"))      # “2nd half-hour”
OR_DURATION_MIN = int(os.getenv("KTBB_OR_DURATION_MIN", "30"))  # 30m candle


def resolve_symbol(symbol_short: str) -> str:
    s = (symbol_short or "BTC").upper()
    if s in ("BTC", "BTCUSDT"):
        return "BTCUSDT"
    if s.endswith("USDT"):
        return s
    return f"{s}USDT"


def _parse_hhmm(hhmm: str) -> tuple[int, int]:
    hh, mm = hhmm.split(":")
    return int(hh), int(mm)


def _utc_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _ms_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def compute_or_window_utc(
    *,
    session_date_utc: datetime,
    session_tz: str,
) -> tuple[datetime, datetime]:
    """
    Compute the OR window in UTC, defined as:
      OR start = session open (local) + OR_OFFSET_MIN
      OR end   = OR start + OR_DURATION_MIN

    `session_date_utc` is "today" anchor; we convert to local date first.
    """
    tz = ZoneInfo(session_tz)

    # Determine the local trading day based on UTC "now"
    local_now = session_date_utc.astimezone(tz)
    local_date = local_now.date()

    open_h, open_m = _parse_hhmm(SESSION_OPEN_LOCAL_HHMM)
    session_open_local = datetime(local_date.year, local_date.month, local_date.day, open_h, open_m, tzinfo=tz)

    or_start_local = session_open_local + timedelta(minutes=OR_OFFSET_MIN)
    or_end_local = or_start_local + timedelta(minutes=OR_DURATION_MIN)

    return or_start_local.astimezone(timezone.utc), or_end_local.astimezone(timezone.utc)


# ----------------------------
# Provider interface
# ----------------------------
class MarketDataProvider(Protocol):
    def ticker_price(self, symbol: str) -> float:
        ...

    def klines(self, symbol: str, interval: str, limit: int, start_ms: Optional[int] = None, end_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        ...


# ----------------------------
# BinanceUS implementation (existing pattern)
# ----------------------------
@dataclass
class BinanceUSProvider:
    base: str = "https://api.binance.us"

    def ticker_price(self, symbol: str) -> float:
        url = f"{self.base}/api/v3/ticker/price"
        r = requests.get(url, params={"symbol": symbol}, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])

    def klines(self, symbol: str, interval: str, limit: int, start_ms: Optional[int] = None, end_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        url = f"{self.base}/api/v3/klines"
        params: Dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        out = []
        for k in r.json():
            out.append({
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": int(k[6]),
            })
        return out


def _provider() -> MarketDataProvider:
    # Easy switch without touching code
    if DEFAULT_PROVIDER == "binanceus":
        return BinanceUSProvider()
    # If you add other providers later, route them here.
    # Keeping it minimal right now:
    return BinanceUSProvider()


# ----------------------------
# Volume profile stub (keep your existing implementation if you have it)
# ----------------------------
def compute_volume_profile(candles: List[Dict[str, Any]]) -> tuple[float, float, float]:
    """
    Placeholder. Keep your existing VP logic if already implemented elsewhere.
    Returning (VAL, POC, VAH).
    """
    closes = [c["close"] for c in candles if c.get("close") is not None]
    if not closes:
        return 0.0, 0.0, 0.0
    lo = min(closes)
    hi = max(closes)
    poc = closes[len(closes) // 2]
    return float(lo), float(poc), float(hi)


def _last_pivot_high(highs: List[float]) -> Optional[float]:
    if len(highs) < 5:
        return None
    # simple last swing-high heuristic
    for i in range(len(highs) - 3, 1, -1):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            return highs[i]
    return None


def _last_pivot_low(lows: List[float]) -> Optional[float]:
    if len(lows) < 5:
        return None
    for i in range(len(lows) - 3, 1, -1):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            return lows[i]
    return None


def build_auto_inputs(symbol: str, session_tz: str) -> Dict[str, Any]:
    """
    Produces the unified input dictionary consumed by SSE + DMR.
    """
    p = _provider()

    # True live price
    last_price = p.ticker_price(symbol)

    # Grab 15m for 24h + morning profiles (basic)
    now_utc = datetime.now(tz=timezone.utc)
    candles_15m = p.klines(symbol, "15m", limit=96)  # ~24h

    f24_val, f24_poc, f24_vah = compute_volume_profile(candles_15m)

    # Morning = last 4 hours of 15m as a placeholder (keep your original if needed)
    morn = candles_15m[-16:] if len(candles_15m) >= 16 else candles_15m
    morn_val, morn_poc, morn_vah = compute_volume_profile(morn)

    # HTF shelves from candles
    h4 = p.klines(symbol, "4h", limit=200)
    h1 = p.klines(symbol, "1h", limit=400)

    h4_supply = _last_pivot_high([c["high"] for c in h4]) or max(c["high"] for c in h4)
    h4_demand = _last_pivot_low([c["low"] for c in h4]) or min(c["low"] for c in h4)

    h1_supply = _last_pivot_high([c["high"] for c in h1]) or max(c["high"] for c in h1)
    h1_demand = _last_pivot_low([c["low"] for c in h1]) or min(c["low"] for c in h1)

    # OR candle
    or_start_utc, or_end_utc = compute_or_window_utc(session_date_utc=now_utc, session_tz=session_tz)

    # Pull a small window around OR and find the exact 30m candle open
    window = p.klines(
        symbol,
        "30m",
        limit=10,
        start_ms=_utc_ms(or_start_utc - timedelta(minutes=60)),
        end_ms=_utc_ms(or_end_utc + timedelta(minutes=60)),
    )

    target_open_ms = _utc_ms(or_start_utc)
    exact = next((c for c in window if int(c["open_time"]) == target_open_ms), None)

    if exact:
        r30_high = float(exact["high"])
        r30_low = float(exact["low"])
    else:
        # fallback: stitch using 15m candles inside OR window
        inside = [c for c in candles_15m if or_start_utc <= _ms_utc(int(c["open_time"])) < or_end_utc]
        if not inside:
            raise RuntimeError(f"Could not resolve OR candle for tz={session_tz} start={or_start_utc.isoformat()}")
        r30_high = max(c["high"] for c in inside)
        r30_low = min(c["low"] for c in inside)

    # Weekly references (YOU said you already have these; keep them as inputs)
    # If you later fetch weekly VP from exchange, wire it here.
    weekly_val = inputs_float_env("KTBB_WEEKLY_VAL")
    weekly_poc = inputs_float_env("KTBB_WEEKLY_POC")
    weekly_vah = inputs_float_env("KTBB_WEEKLY_VAH")

    return round_inputs({
        "h4_supply": h4_supply,
        "h4_demand": h4_demand,
        "h1_supply": h1_supply,
        "h1_demand": h1_demand,

        "weekly_val": weekly_val,
        "weekly_poc": weekly_poc,
        "weekly_vah": weekly_vah,

        "f24_val": f24_val,
        "f24_poc": f24_poc,
        "f24_vah": f24_vah,

        "morn_val": morn_val,
        "morn_poc": morn_poc,
        "morn_vah": morn_vah,

        "r30_high": r30_high,
        "r30_low": r30_low,

        "last_price": last_price,
        "session_tz": session_tz,
        "or_start_utc": or_start_utc.isoformat(),
        "or_end_utc": or_end_utc.isoformat(),
    })


def inputs_float_env(key: str) -> float:
    v = os.getenv(key, "").strip()
    if not v:
        return 0.0
    try:
        return float(v)
    except Exception:
        return 0.0


def round_inputs(d: Dict[str, Any]) -> Dict[str, Any]:
    def r(x: Any) -> Any:
        try:
            if x is None:
                return None
            if isinstance(x, (int, float)):
                return float(f"{float(x):.1f}")
            return x
        except Exception:
            return x

    return {k: r(v) for k, v in d.items()}
