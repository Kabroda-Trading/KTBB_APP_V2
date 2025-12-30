# sse_engine.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# -------------------------------------------------------------------------
# SHARED CORE: Data Structures & Math Helpers
# -------------------------------------------------------------------------

@dataclass(frozen=True)
class Shelf:
    tf: str
    level: float
    kind: str  # "supply" or "demand"
    strength: float
    primary: bool = False
    # New optional fields for the Investing Engine (won't break existing code)
    zone_top: Optional[float] = None
    zone_bottom: Optional[float] = None


def _pct(x: float, p: float) -> float:
    return x * p


def _spacing_ok(a: float, b: float, min_pct: float) -> bool:
    if a <= 0 or b <= 0:
        return True
    return abs(a - b) >= min(a, b) * min_pct


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# -------------------------------------------------------------------------
# PART 1: EXISTING DAY TRADING ENGINE (The SSE Suite)
# -------------------------------------------------------------------------

def _build_htf_shelves(
    h4_supply: float,
    h4_demand: float,
    h1_supply: float,
    h1_demand: float,
) -> Tuple[List[Shelf], List[Shelf]]:
    resistance: List[Shelf] = []
    support: List[Shelf] = []

    if h4_supply and h4_supply > 0:
        resistance.append(Shelf(tf="4H", level=float(h4_supply), kind="supply", strength=8.0, primary=True))
    if h1_supply and h1_supply > 0:
        resistance.append(Shelf(tf="1H", level=float(h1_supply), kind="supply", strength=6.0, primary=False))

    if h4_demand and h4_demand > 0:
        support.append(Shelf(tf="4H", level=float(h4_demand), kind="demand", strength=8.0, primary=True))
    if h1_demand and h1_demand > 0:
        support.append(Shelf(tf="1H", level=float(h1_demand), kind="demand", strength=6.0, primary=False))

    resistance = sorted(resistance, key=lambda s: s.level)
    support = sorted(support, key=lambda s: s.level)
    return resistance, support


def _select_daily_levels(resistance: List[Shelf], support: List[Shelf]) -> Tuple[float, float, Dict[str, Any]]:
    if not resistance or not support:
        return 0.0, 0.0, {"resistance": [], "support": []}

    ds = max(support, key=lambda s: ((1 if s.tf == "4H" else 0), s.strength)).level
    dr = max(resistance, key=lambda s: ((1 if s.tf == "4H" else 0), s.strength)).level

    daily_support = float(min(ds, dr))
    daily_resistance = float(max(ds, dr))

    htf_out = {
        "resistance": [
            {"tf": s.tf, "level": float(s.level), "strength": float(s.strength), "primary": (s.level == dr)}
            for s in resistance
        ],
        "support": [
            {"tf": s.tf, "level": float(s.level), "strength": float(s.strength), "primary": (s.level == ds)}
            for s in support
        ],
    }
    return daily_support, daily_resistance, htf_out


def _pick_trigger_candidates(
    *,
    px: float,
    daily_support: float,
    daily_resistance: float,
    r30_high: float,
    r30_low: float,
    f24_vah: float,
    f24_val: float,
) -> Tuple[float, float, Dict[str, Any]]:
    # Min distance from ANCHOR (0.5%)
    min_from_px = 0.005
    min_level_spacing = 0.0015

    bo_base = max([v for v in [r30_high, f24_vah] if v and v > 0] or [0.0])
    bd_base = min([v for v in [r30_low, f24_val] if v and v > 0] or [0.0])

    if bo_base <= 0:
        bo_base = daily_support + 0.7 * (daily_resistance - daily_support)
    if bd_base <= 0:
        bd_base = daily_support + 0.3 * (daily_resistance - daily_support)

    bo = max(bo_base, px * (1 + min_from_px))
    bd = min(bd_base, px * (1 - min_from_px))

    inner_lo = daily_support + _pct(daily_support, min_level_spacing)
    inner_hi = daily_resistance - _pct(daily_resistance, min_level_spacing)
    bo = _clamp(bo, inner_lo, inner_hi)
    bd = _clamp(bd, inner_lo, inner_hi)

    if not (daily_support < bd < bo < daily_resistance):
        mid = _clamp(px, inner_lo, inner_hi)
        span = max((daily_resistance - daily_support) * 0.12, px * 0.01)
        bd = _clamp(mid - span / 2, inner_lo, inner_hi)
        bo = _clamp(mid + span / 2, inner_lo, inner_hi)

        if bo < px * (1 + min_from_px):
            bo = _clamp(px * (1 + min_from_px), inner_lo, inner_hi)
        if bd > px * (1 - min_from_px):
            bd = _clamp(px * (1 - min_from_px), inner_lo, inner_hi)

        if not (daily_support < bd < bo < daily_resistance):
            bd = daily_support + 0.4 * (daily_resistance - daily_support)
            bo = daily_support + 0.6 * (daily_resistance - daily_support)

    def bump_up(x: float, ref: float) -> float:
        return max(x, ref + ref * min_level_spacing)

    def bump_down(x: float, ref: float) -> float:
        return min(x, ref - ref * min_level_spacing)

    if not _spacing_ok(bd, daily_support, min_level_spacing):
        bd = bump_up(bd, daily_support)
    if not _spacing_ok(bo, daily_resistance, min_level_spacing):
        bo = bump_down(bo, daily_resistance)
    if not _spacing_ok(bo, bd, min_level_spacing):
        bo = bump_up(bo, bd)

    intraday = {
        "breakout_side": [{"level": float(bo_base), "strength": 6.0}],
        "breakdown_side": [{"level": float(bd_base), "strength": 6.0}],
    }
    return float(bo), float(bd), intraday


def compute_sse_levels(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    ORIGINAL FUNCTION: Used for the Day Trading Suite.
    Does not use automated candle scanning. Relies on inputs.
    """
    def f(k: str, default: float = 0.0) -> float:
        try:
            v = inputs.get(k, default)
            return float(v) if v is not None else float(default)
        except Exception:
            return float(default)

    px = f("last_price", 0.0)
    anchor_px = f("session_open_price", 0.0)
    reference_price = anchor_px if anchor_px > 0 else px

    resistance, support = _build_htf_shelves(
        h4_supply=f("h4_supply"),
        h4_demand=f("h4_demand"),
        h1_supply=f("h1_supply"),
        h1_demand=f("h1_demand"),
    )

    daily_support, daily_resistance, htf_out = _select_daily_levels(resistance, support)

    if reference_price <= 0:
        reference_price = (f("f24_poc") or f("weekly_poc") or (daily_support + daily_resistance) / 2.0)

    bo, bd, intraday = _pick_trigger_candidates(
        px=reference_price, 
        daily_support=daily_support,
        daily_resistance=daily_resistance,
        r30_high=f("r30_high"),
        r30_low=f("r30_low"),
        f24_vah=f("f24_vah"),
        f24_val=f("f24_val"),
    )

    if not (daily_support < bd < bo < daily_resistance):
        band = max(daily_resistance - daily_support, 1.0)
        bd = daily_support + 0.45 * band
        bo = daily_support + 0.55 * band

    return {
        "levels": {
            "daily_support": float(daily_support),
            "daily_resistance": float(daily_resistance),
            "breakout_trigger": float(bo),
            "breakdown_trigger": float(bd),
            "range30m_high": float(f("r30_high")),
            "range30m_low": float(f("r30_low")),
        },
        "htf_shelves": htf_out,
        "intraday_shelves": intraday,
    }


# -------------------------------------------------------------------------
# PART 2: NEW INVESTING ENGINE (The "S Jan" Suite)
# -------------------------------------------------------------------------

def _find_smart_zones(candles: List[Dict[str, float]], timeframe: str) -> List[Shelf]:
    """
    Scans raw OHLCV candle data to identify Pivot-based Supply and Demand zones.
    Logic: Looks for a 3-candle Fractal (High > Left and High > Right).
    """
    zones = []
    if not candles or len(candles) < 3:
        return zones

    # Strength Mapping
    # Monthly = 10.0 (King), Weekly = 9.0 (Queen)
    base_strength = 10.0 if "M" in timeframe else 9.0

    # Lookback logic: Standard 3-candle fractal (1 left, 1 right)
    for i in range(1, len(candles) - 1):
        prev = candles[i - 1]
        curr = candles[i]
        next_c = candles[i + 1]

        # 1. DETECT SUPPLY (Pivot High)
        if curr['high'] > prev['high'] and curr['high'] > next_c['high']:
            # Zone Definition: Top = Wick High, Bottom = Body Top (Open or Close)
            # This is the "Order Block" logic.
            zone_top = curr['high']
            zone_bottom = max(curr['open'], curr['close'])
            
            # Create Shelf
            zones.append(Shelf(
                tf=timeframe,
                level=zone_bottom, # We track the "entrance" to the zone as the level
                kind="supply",
                strength=base_strength,
                primary=True,
                zone_top=zone_top,
                zone_bottom=zone_bottom
            ))

        # 2. DETECT DEMAND (Pivot Low)
        if curr['low'] < prev['low'] and curr['low'] < next_c['low']:
            # Zone Definition: Top = Body Bottom, Bottom = Wick Low
            zone_top = min(curr['open'], curr['close'])
            zone_bottom = curr['low']

            zones.append(Shelf(
                tf=timeframe,
                level=zone_top, # Entrance level
                kind="demand",
                strength=base_strength,
                primary=True,
                zone_top=zone_top,
                zone_bottom=zone_bottom
            ))
            
    return zones


def _grade_market_structure(price: float, supply_zones: List[Shelf], demand_zones: List[Shelf]) -> Dict[str, Any]:
    """
    Determines the 'Grade' or 'Bias' based on current price location relative to zones.
    """
    # Sort zones by proximity to current price
    active_supply = [z for z in supply_zones if z.level > price]
    active_demand = [z for z in demand_zones if z.level < price]

    # Find nearest zones
    nearest_supply = min(active_supply, key=lambda z: z.level) if active_supply else None
    nearest_demand = max(active_demand, key=lambda z: z.level) if active_demand else None

    # Basic Grading Logic
    bias = "NEUTRAL"
    grade = "C"

    if nearest_supply and nearest_demand:
        range_dist = nearest_supply.level - nearest_demand.level
        pos_in_range = (price - nearest_demand.level) / range_dist if range_dist > 0 else 0.5
        
        if pos_in_range > 0.8:
            bias = "BEARISH_TEST"  # Testing Supply
            grade = "B+"
        elif pos_in_range < 0.2:
            bias = "BULLISH_TEST"  # Testing Demand
            grade = "B+"
        else:
            bias = "RANGING"
            grade = "C"
            
    # Check for breakouts (if price is above the last known supply pivot)
    # This assumes 'supply_zones' contains historical pivots.
    # If price > nearest_supply.zone_top (the wick), it's a Break of Structure (BOS)
    
    return {
        "bias": bias,
        "grade": grade,
        "nearest_supply_level": nearest_supply.level if nearest_supply else None,
        "nearest_demand_level": nearest_demand.level if nearest_demand else None
    }


def compute_investing_levels(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    NEW FUNCTION: S Jan Investing Engine.
    Takes raw candle data, finds zones automatically, and grades the structure.
    
    Expected Inputs:
      - 'monthly_candles': List of dicts {'open', 'high', 'low', 'close'}
      - 'weekly_candles': List of dicts
      - 'current_price': float
    """
    monthly_data = inputs.get("monthly_candles", [])
    weekly_data = inputs.get("weekly_candles", [])
    current_price = float(inputs.get("current_price", 0.0))

    # 1. Automate the Search (The "Map")
    m_zones = _find_smart_zones(monthly_data, "Monthly")
    w_zones = _find_smart_zones(weekly_data, "Weekly")
    
    all_supply = [z for z in m_zones + w_zones if z.kind == "supply"]
    all_demand = [z for z in m_zones + w_zones if z.kind == "demand"]

    # 2. Grade the Structure
    grading = _grade_market_structure(current_price, all_supply, all_demand)

    # 3. Format Output for Frontend (JSON)
    # We convert Shelf objects to simple dicts for the API response
    def to_dict(shelves):
        return [
            {
                "tf": s.tf,
                "level": s.level,
                "strength": s.strength,
                "top": s.zone_top,
                "bottom": s.zone_bottom
            } 
            for s in sorted(shelves, key=lambda x: x.level)
        ]

    return {
        "map": {
            "supply_zones": to_dict(all_supply),
            "demand_zones": to_dict(all_demand)
        },
        "structure": grading,
        "meta": {
            "engine": "S_Jan_v1",
            "zones_found": len(all_supply) + len(all_demand)
        }
    }