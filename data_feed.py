import requests
from typing import List, Dict, Tuple

BINANCE_BASE_URL = "https://api.binance.us"


def fetch_klines(symbol: str, interval: str, limit: int) -> List[Dict[str, float]]:
    """
    Fetch OHLCV candles from Binance.

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
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
        )
    return klines


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


def build_auto_inputs_for_btc() -> Dict[str, float]:
    """
    Build the exact inputs your compute_dm_levels() expects, using live BTCUSDT data.

    Design choices (simple v1, we can refine later):
      - Weekly VRVP: last 168 x 1h candles (~7 days)
      - 24h FRVP:    last 96 x 15m candles (24h)
      - Morning FRVP:last 16 x 15m candles (~4h window before 'now')
      - 4H shelves:  high/low of last 12 x 4h candles
      - 1H shelves:  high/low of last 24 x 1h candles
      - 30m range:   high/low of latest 30m candle
    """
    symbol = "BTCUSDT"

    # Weekly VRVP from last ~7 days of 1h candles
    weekly_kl = fetch_klines(symbol, "1h", 168)
    weekly_val, weekly_poc, weekly_vah = compute_volume_profile(weekly_kl)

    # 24h FRVP from last 24h of 15m candles
    f24_kl = fetch_klines(symbol, "15m", 96)
    f24_val, f24_poc, f24_vah = compute_volume_profile(f24_kl)

    # Morning FRVP: last 4h worth of 15m candles
    if len(f24_kl) >= 16:
        morn_kl = f24_kl[-16:]
    else:
        morn_kl = f24_kl

    try:
        morn_val, morn_poc, morn_vah = compute_volume_profile(morn_kl)
    except ValueError:
        # Fallback: reuse 24h FRVP if morning subset is degenerate
        morn_val, morn_poc, morn_vah = f24_val, f24_poc, f24_vah

    # 4H shelves: extremes of recent 4h candles
    h4_kl = fetch_klines(symbol, "4h", 12)
    h4_highs = [k["high"] for k in h4_kl]
    h4_lows = [k["low"] for k in h4_kl]
    h4_supply = max(h4_highs) if h4_highs else 0.0
    h4_demand = min(h4_lows) if h4_lows else 0.0

    # 1H shelves: extremes of recent 1h candles
    h1_kl = fetch_klines(symbol, "1h", 24)
    h1_highs = [k["high"] for k in h1_kl]
    h1_lows = [k["low"] for k in h1_kl]
    h1_supply = max(h1_highs) if h1_highs else 0.0
    h1_demand = min(h1_lows) if h1_lows else 0.0

    # 30m opening range: latest 30m candle
    r30_kl = fetch_klines(symbol, "30m", 1)
    if r30_kl:
        r30_high = r30_kl[-1]["high"]
        r30_low = r30_kl[-1]["low"]
    else:
        # Fallback to last 15m candle if 30m missing
        if f24_kl:
            r30_high = f24_kl[-1]["high"]
            r30_low = f24_kl[-1]["low"]
        else:
            r30_high = 0.0
            r30_low = 0.0

        # helper for rounding to 1 decimal
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

