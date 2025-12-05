import requests
from typing import List, Dict, Tuple
from datetime import datetime, timezone, timedelta

BINANCE_BASE_URL = "https://api.binance.us"


def fetch_klines(symbol: str, interval: str, limit: int) -> List[Dict[str, float]]:
    """
    Fetch OHLCV candles from Binance US.

    interval examples:
      - "1m", "5m", "15m", "30m"
      - "1h", "4h", "1d"
    """
    url = f"{BINANCE_BASE_URL}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    raw = resp.json()

    klines: List[Dict[str, float]] = []
    for k in raw:
        # Binance kline format:
        # 0 open time, 1 open, 2 high, 3 low, 4 close, 5 volume, ...
        klines.append(
            {
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
        )
    return klines


def ms_to_utc(ms: int) -> datetime:
    """Convert Binance millisecond timestamp to a UTC datetime."""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def compute_volume_profile(
    klines: List[Dict[str, float]],
    num_bins: int = 40,
) -> Tuple[float, float, float]:
    """
    Build a simple volume profile (VAL / POC / VAH) from a list of candles.

    Pure Python implementation (no numpy):
      - Uses typical price (H+L+C)/3 as the price for each candle.
      - Weights each price by its volume.
      - Finds a 70% value area (approx 15%–85% cumulative volume)
        to define VAL/VAH.
    """
    if not klines:
        raise ValueError("No candles provided for volume profile.")

    prices: List[float] = [
        (k["high"] + k["low"] + k["close"]) / 3.0 for k in klines
    ]
    volumes: List[float] = [k["volume"] for k in klines]

    if not prices or sum(volumes) <= 0:
        raise ValueError("Insufficient data for volume profile.")

    p_min = min(prices)
    p_max = max(prices)

    if p_min == p_max:
        # Flat price, just return the single level
        return float(p_min), float(p_min), float(p_min)

    # Build bin edges
    num_bins = max(1, num_bins)
    price_range = p_max - p_min
    step = price_range / num_bins

    # To avoid zero step if range is tiny
    if step == 0:
        return float(p_min), float(p_min), float(p_min)

    edges: List[float] = [p_min + i * step for i in range(num_bins + 1)]
    hist: List[float] = [0.0 for _ in range(num_bins)]

    # Fill histogram
    for price, vol in zip(prices, volumes):
        idx = int((price - p_min) / step)
        if idx < 0:
            idx = 0
        if idx >= num_bins:
            idx = num_bins - 1
        hist[idx] += vol

    # Point of control = bin with max volume
    poc_idx = 0
    max_vol = hist[0]
    for i in range(1, num_bins):
        if hist[i] > max_vol:
            max_vol = hist[i]
            poc_idx = i

    poc_price = (edges[poc_idx] + edges[poc_idx + 1]) / 2.0

    total_vol = sum(hist)
    if total_vol <= 0:
        avg_price = sum(prices) / len(prices)
        return float(avg_price), float(avg_price), float(avg_price)

    # Normalized and cumulative volume
    vol_norm = [v / total_vol for v in hist]
    cum: List[float] = []
    running = 0.0
    for v in vol_norm:
        running += v
        cum.append(running)

    lower_target = 0.15
    upper_target = 0.85

    # Find VAL index (first index where cum >= lower_target)
    val_idx = 0
    for i, c in enumerate(cum):
        if c >= lower_target:
            val_idx = i
            break

    # Find VAH index (first index where cum >= upper_target)
    vah_idx = num_bins - 1
    for i, c in enumerate(cum):
        if c >= upper_target:
            vah_idx = i
            break

    val_price = edges[val_idx]
    vah_price = edges[min(vah_idx + 1, len(edges) - 1)]

    return float(val_price), float(poc_price), float(vah_price)


def find_last_pivot_high(highs: List[float], left: int, right: int) -> float | None:
    """
    Find the last confirmed pivot high in a list of highs.

    Pivot definition (similar to Pine ta.pivothigh):
      - The pivot index i must have `left` bars before and `right` bars after it.
      - high[i] must be strictly greater than the highs of the `left` bars before it.
      - high[i] must be greater than or equal to the highs of the `right` bars after it.

    We scan from the most recent *confirmed* bar backwards, so we return
    the latest pivot, just like your Pine script does with lastSupply4h etc.
    """
    n = len(highs)
    if n == 0:
        return None

    # We can't confirm a pivot on the last `right` bars.
    # Start from n - 1 - right and move backwards.
    for i in range(n - 1 - right, left - 1, -1):
        candidate = highs[i]

        # Check left side: strictly greater than `left` previous highs
        ok_left = True
        for j in range(i - left, i):
            if candidate <= highs[j]:
                ok_left = False
                break

        if not ok_left:
            continue

        # Check right side: greater or equal than `right` next highs
        ok_right = True
        for j in range(i + 1, i + 1 + right):
            if j >= n or candidate < highs[j]:
                ok_right = False
                break

        if ok_right:
            return candidate

    return None


def find_last_pivot_low(lows: List[float], left: int, right: int) -> float | None:
    """
    Mirror of find_last_pivot_high for lows.

    Pivot low:
      - low[i] must be strictly lower than the `left` lows before it.
      - low[i] must be lower than or equal to the `right` lows after it.
    """
    n = len(lows)
    if n == 0:
        return None

    for i in range(n - 1 - right, left - 1, -1):
        candidate = lows[i]

        ok_left = True
        for j in range(i - left, i):
            if candidate >= lows[j]:
                ok_left = False
                break

        if not ok_left:
            continue

        ok_right = True
        for j in range(i + 1, i + 1 + right):
            if j >= n or candidate > lows[j]:
                ok_right = False
                break

        if ok_right:
            return candidate

    return None


def build_auto_inputs_for_btc() -> Dict[str, float]:
    """
    Build the exact inputs your compute_dm_levels() expects, using live BTCUSDT data.

    Time-window rules (in UTC, so DST & user timezone don't matter):

      - 24h FRVP:
            previous 12:00 UTC  --> current 12:00 UTC
        (this corresponds to 6:00–6:00 CST in winter)

      - 30m Opening Range:
            current day 12:30–13:00 UTC
        (this corresponds to 6:30–7:00 CST in winter)

      - Morning FRVP:
            last 4h inside that 24h window (i.e. 16x 15m candles).

      - HTF shelves:
            4H / 1H supply & demand from last confirmed pivot highs/lows
            with left=3, right=3 bars (matching the KTBB HTF Shelf Helper).
    """
    symbol = "BTCUSDT"

    now_utc = datetime.now(timezone.utc)

    # ---- Anchor for the 24h window at 12:00 UTC ----
    # If it's before today's 12:00 UTC, we use yesterday 12:00 as the END.
    anchor_today = now_utc.replace(hour=12, minute=0, second=0, microsecond=0)
    if now_utc < anchor_today:
        fr24_end = anchor_today - timedelta(days=1)
    else:
        fr24_end = anchor_today
    fr24_start = fr24_end - timedelta(days=1)  # 24 hours earlier

    # ---- Weekly VRVP: simple rolling 7 days of 1h ----
    weekly_kl = fetch_klines(symbol, "1h", 24 * 7)
    weekly_val, weekly_poc, weekly_vah = compute_volume_profile(weekly_kl)

    # ---- 24h FRVP anchored to 12:00 UTC ----
    # Pull ~3 days of 15m candles, then filter to our 24h window.
    all_15m = fetch_klines(symbol, "15m", 24 * 4 * 3)  # 3 days * 24h * 4 candles/h
    f24_kl = [
        k
        for k in all_15m
        if fr24_start <= ms_to_utc(k["open_time"]) < fr24_end
    ]

    if len(f24_kl) < 10:
        # If something weird happens, fall back to last 24h worth of 15m candles
        f24_kl = all_15m[-96:]

    f24_val, f24_poc, f24_vah = compute_volume_profile(f24_kl)

    # ---- Morning FRVP: last 4 hours (16x 15m candles) inside that same 24h window ----
    if len(f24_kl) >= 16:
        morn_kl = f24_kl[-16:]
    else:
        morn_kl = f24_kl

    try:
        morn_val, morn_poc, morn_vah = compute_volume_profile(morn_kl)
    except ValueError:
        # Fallback: reuse 24h FRVP if morning subset is degenerate
        morn_val, morn_poc, morn_vah = f24_val, f24_poc, f24_vah

    # ---- 4H & 1H shelves using pivot logic (KTBB Shelf Helper v2) ----
    LEFT_BARS = 3
    RIGHT_BARS = 3

    # 4H shelves: use enough history to find pivots
    h4_kl = fetch_klines(symbol, "4h", 200)
    h4_highs = [k["high"] for k in h4_kl]
    h4_lows = [k["low"] for k in h4_kl]

    h4_supply_pivot = find_last_pivot_high(h4_highs, LEFT_BARS, RIGHT_BARS)
    h4_demand_pivot = find_last_pivot_low(h4_lows, LEFT_BARS, RIGHT_BARS)

    if h4_supply_pivot is None and h4_highs:
        h4_supply_pivot = max(h4_highs)
    if h4_demand_pivot is None and h4_lows:
        h4_demand_pivot = min(h4_lows)

    h4_supply = h4_supply_pivot or 0.0
    h4_demand = h4_demand_pivot or 0.0

    # 1H shelves
    h1_kl = fetch_klines(symbol, "1h", 400)
    h1_highs = [k["high"] for k in h1_kl]
    h1_lows = [k["low"] for k in h1_kl]

    h1_supply_pivot = find_last_pivot_high(h1_highs, LEFT_BARS, RIGHT_BARS)
    h1_demand_pivot = find_last_pivot_low(h1_lows, LEFT_BARS, RIGHT_BARS)

    if h1_supply_pivot is None and h1_highs:
        h1_supply_pivot = max(h1_highs)
    if h1_demand_pivot is None and h1_lows:
        h1_demand_pivot = min(h1_lows)

    h1_supply = h1_supply_pivot or 0.0
    h1_demand = h1_demand_pivot or 0.0

    # ---- 30m opening range: 12:30–13:00 UTC on same "fr24_end" day ----
    or_start = fr24_end.replace(hour=12, minute=30, second=0, microsecond=0)
    or_end = fr24_end.replace(hour=13, minute=0, second=0, microsecond=0)

    all_30m = fetch_klines(symbol, "30m", 96)  # ~2 days
    r30_candle = None
    for k in all_30m:
        t = ms_to_utc(k["open_time"])
        if or_start <= t < or_end:
            r30_candle = k
            break

    if r30_candle is None:
        # Fallback: just use the latest 30m candle
        if all_30m:
            r30_candle = all_30m[-1]
        else:
            r30_candle = {"high": 0.0, "low": 0.0}

    r30_high = r30_candle["high"]
    r30_low = r30_candle["low"]

    # ---- Small helper to round levels to 1 decimal for display ----
    def r(x: float) -> float:
        return round(float(x), 1)

    return {
        "h4_supply": r(h4_supply),
        "h4_demand": r(h4_demand),
        "h1_supply": r(h1_supply),
        "h1_demand": r(h1_demand),
        "weekly_val": r(weekly_val),
        "weekly_poc": r(weekly_poc),
        "weekly_vah": r(weekly_vah),
        "f24_val": r(f24_val),
        "f24_poc": r(f24_poc),
        "f24_vah": r(f24_vah),
        "morn_val": r(morn_val),
        "morn_poc": r(morn_poc),
        "morn_vah": r(morn_vah),
        "r30_high": r(r30_high),
        "r30_low": r(r30_low),
    }
