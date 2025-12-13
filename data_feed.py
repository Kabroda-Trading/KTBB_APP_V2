# data_feed.py
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta, time as dtime
from typing import Dict, List, Tuple, Optional

import requests
from zoneinfo import ZoneInfo

BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL", "https://api.binance.us")

KTBB_SYMBOLS: Dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
}

DEFAULT_SESSION_OPEN_LOCAL = "06:00"
DEFAULT_OR_START_LOCAL = "06:30"
DEFAULT_OR_DURATION_MIN = 30


def resolve_symbol(symbol: str) -> str:
    sym = (symbol or "").upper().strip()
    return KTBB_SYMBOLS.get(sym, sym)


def ms_to_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def utc_to_ms(dt_utc: datetime) -> int:
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return int(dt_utc.astimezone(timezone.utc).timestamp() * 1000)


def _parse_hhmm(s: str) -> dtime:
    hh, mm = s.strip().split(":")
    return dtime(hour=int(hh), minute=int(mm))


def compute_session_window_utc(
    now_utc: datetime,
    session_tz: str,
    session_open_local_hhmm: str = DEFAULT_SESSION_OPEN_LOCAL,
) -> Tuple[datetime, datetime]:
    tz = ZoneInfo(session_tz)
    now_local = now_utc.astimezone(tz)
    open_t = _parse_hhmm(session_open_local_hhmm)

    open_local = datetime.combine(now_local.date(), open_t, tzinfo=tz)
    if now_local < open_local:
        open_local -= timedelta(days=1)

    start_utc = open_local.astimezone(timezone.utc)
    end_utc = (open_local + timedelta(days=1)).astimezone(timezone.utc)
    return start_utc, end_utc


def compute_or_window_utc(
    session_start_utc: datetime,
    session_tz: str,
    or_start_local_hhmm: str = DEFAULT_OR_START_LOCAL,
    duration_min: int = DEFAULT_OR_DURATION_MIN,
) -> Tuple[datetime, datetime]:
    tz = ZoneInfo(session_tz)
    session_start_local = session_start_utc.astimezone(tz)
    or_t = _parse_hhmm(or_start_local_hhmm)

    or_start_local = datetime.combine(session_start_local.date(), or_t, tzinfo=tz)
    or_end_local = or_start_local + timedelta(minutes=duration_min)

    return or_start_local.astimezone(timezone.utc), or_end_local.astimezone(timezone.utc)


def fetch_klines(
    symbol: str,
    interval: str,
    limit: int,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
) -> List[Dict[str, float]]:
    url = f"{BINANCE_BASE_URL}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time_ms is not None:
        params["startTime"] = int(start_time_ms)
    if end_time_ms is not None:
        params["endTime"] = int(end_time_ms)

    r = requests.get(url, params=params, timeout=12)
    r.raise_for_status()
    raw = r.json()

    out: List[Dict[str, float]] = []
    for k in raw:
        out.append(
            {
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
        )
    return out


def compute_volume_profile(klines: List[Dict[str, float]], num_bins: int = 40) -> Tuple[float, float, float]:
    if not klines:
        raise ValueError("No candles")

    prices = [((k["high"] + k["low"] + k["close"]) / 3.0) for k in klines]
    vols = [max(float(k["volume"]), 0.0) for k in klines]

    pmin, pmax = min(prices), max(prices)
    if pmax <= pmin:
        return float(pmin), float(pmin), float(pmin)

    step = (pmax - pmin) / float(num_bins)
    bins = [0.0 for _ in range(num_bins)]
    for p, v in zip(prices, vols):
        idx = int((p - pmin) / step)
        idx = max(0, min(num_bins - 1, idx))
        bins[idx] += v

    total = sum(bins)
    poc_idx = max(range(num_bins), key=lambda i: bins[i])
    poc = pmin + (poc_idx + 0.5) * step

    if total <= 0:
        return float(pmin), float(poc), float(pmax)

    target = total * 0.70
    left = right = poc_idx
    area = bins[poc_idx]
    while area < target and (left > 0 or right < num_bins - 1):
        lv = bins[left - 1] if left > 0 else -1
        rv = bins[right + 1] if right < num_bins - 1 else -1
        if rv >= lv and right < num_bins - 1:
            right += 1
            area += bins[right]
        elif left > 0:
            left -= 1
            area += bins[left]
        else:
            break

    val = pmin + left * step
    vah = pmin + (right + 1) * step
    return float(val), float(poc), float(vah)


def _last_pivot_high(highs: List[float], left: int = 3, right: int = 3) -> Optional[float]:
    if len(highs) < left + right + 1:
        return None
    for i in range(len(highs) - right - 1, left - 1, -1):
        p = highs[i]
        if all(p > highs[j] for j in range(i - left, i)) and all(p > highs[j] for j in range(i + 1, i + right + 1)):
            return float(p)
    return None


def _last_pivot_low(lows: List[float], left: int = 3, right: int = 3) -> Optional[float]:
    if len(lows) < left + right + 1:
        return None
    for i in range(len(lows) - right - 1, left - 1, -1):
        p = lows[i]
        if all(p < lows[j] for j in range(i - left, i)) and all(p < lows[j] for j in range(i + 1, i + right + 1)):
            return float(p)
    return None


def build_auto_inputs(
    symbol: str,
    session_tz: str = "America/New_York",
    session_open_local_hhmm: str = DEFAULT_SESSION_OPEN_LOCAL,
    or_start_local_hhmm: str = DEFAULT_OR_START_LOCAL,
) -> Dict[str, float]:
    symbol = resolve_symbol(symbol)
    now_utc = datetime.now(timezone.utc)

    weekly_kl = fetch_klines(symbol, "1h", 7 * 24 + 10)
    weekly_val, weekly_poc, weekly_vah = compute_volume_profile(weekly_kl)

    session_start_utc, session_end_utc = compute_session_window_utc(
        now_utc, session_tz=session_tz, session_open_local_hhmm=session_open_local_hhmm
    )

    all_15m = fetch_klines(symbol, "15m", 24 * 4 * 3)
    f24 = [k for k in all_15m if session_start_utc <= ms_to_utc(k["open_time"]) < session_end_utc]
    if len(f24) < 10:
        f24 = all_15m[-96:] if len(all_15m) >= 96 else all_15m
    f24_val, f24_poc, f24_vah = compute_volume_profile(f24)

    morn = f24[-16:] if len(f24) >= 16 else f24
    try:
        morn_val, morn_poc, morn_vah = compute_volume_profile(morn)
    except Exception:
        morn_val, morn_poc, morn_vah = f24_val, f24_poc, f24_vah

    h4_kl = fetch_klines(symbol, "4h", 200)
    h4_supply = _last_pivot_high([k["high"] for k in h4_kl]) or max(k["high"] for k in h4_kl)
    h4_demand = _last_pivot_low([k["low"] for k in h4_kl]) or min(k["low"] for k in h4_kl)

    h1_kl = fetch_klines(symbol, "1h", 400)
    h1_supply = _last_pivot_high([k["high"] for k in h1_kl]) or max(k["high"] for k in h1_kl)
    h1_demand = _last_pivot_low([k["low"] for k in h1_kl]) or min(k["low"] for k in h1_kl)

    or_start_utc, or_end_utc = compute_or_window_utc(
        session_start_utc, session_tz=session_tz, or_start_local_hhmm=or_start_local_hhmm
    )

    # exact 30m candle at OR start if available
    or_30m = fetch_klines(
        symbol,
        "30m",
        limit=5,
        start_time_ms=utc_to_ms(or_start_utc - timedelta(minutes=60)),
        end_time_ms=utc_to_ms(or_end_utc + timedelta(minutes=60)),
    )
    target_ms = utc_to_ms(or_start_utc)
    exact = next((k for k in or_30m if int(k["open_time"]) == target_ms), None)

    if exact:
        r30_high = float(exact["high"])
        r30_low = float(exact["low"])
    else:
        # fallback: build OR from the two 15m candles inside the OR window
        or_15m = [k for k in all_15m if or_start_utc <= ms_to_utc(k["open_time"]) < or_end_utc]
        if not or_15m:
            raise RuntimeError("Could not resolve OR candle.")
        r30_high = float(max(k["high"] for k in or_15m))
        r30_low = float(min(k["low"] for k in or_15m))

    def _r(x: float) -> float:
        return float(f"{float(x):.1f}")

    return {
        "h4_supply": _r(h4_supply),
        "h4_demand": _r(h4_demand),
        "h1_supply": _r(h1_supply),
        "h1_demand": _r(h1_demand),
        "weekly_val": _r(weekly_val),
        "weekly_poc": _r(weekly_poc),
        "weekly_vah": _r(weekly_vah),
        "f24_val": _r(f24_val),
        "f24_poc": _r(f24_poc),
        "f24_vah": _r(f24_vah),
        "morn_val": _r(morn_val),
        "morn_poc": _r(morn_poc),
        "morn_vah": _r(morn_vah),
        "r30_high": _r(r30_high),
        "r30_low": _r(r30_low),
    }
