# sse_engine.py
# ---------------------------------------------------------
# SSE DAY TRADING ENGINE (RESTORED)
# Pure Logic for DMR / BattleBox. No Investing Code.
# ---------------------------------------------------------
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

@dataclass(frozen=True)
class Shelf:
    tf: str
    level: float
    kind: str
    strength: float
    primary: bool = False

def _pct(x: float, p: float) -> float: return x * p
def _spacing_ok(a: float, b: float, min_pct: float) -> bool: return True if (a<=0 or b<=0) else abs(a-b) >= min(a,b)*min_pct
def _clamp(x: float, lo: float, hi: float) -> float: return max(lo, min(hi, x))

def _build_htf_shelves(h4_supply, h4_demand, h1_supply, h1_demand) -> Tuple[List[Shelf], List[Shelf]]:
    res, sup = [], []
    if h4_supply and h4_supply > 0: res.append(Shelf("4H", float(h4_supply), "supply", 8.0, True))
    if h1_supply and h1_supply > 0: res.append(Shelf("1H", float(h1_supply), "supply", 6.0, False))
    if h4_demand and h4_demand > 0: sup.append(Shelf("4H", float(h4_demand), "demand", 8.0, True))
    if h1_demand and h1_demand > 0: sup.append(Shelf("1H", float(h1_demand), "demand", 6.0, False))
    return sorted(res, key=lambda s: s.level), sorted(sup, key=lambda s: s.level)

def _select_daily_levels(resistance: List[Shelf], support: List[Shelf]) -> Tuple[float, float, Dict]:
    if not resistance or not support: return 0.0, 0.0, {"resistance": [], "support": []}
    ds = max(support, key=lambda s: ((1 if s.tf=="4H" else 0), s.strength)).level
    dr = max(resistance, key=lambda s: ((1 if s.tf=="4H" else 0), s.strength)).level
    
    return float(min(ds, dr)), float(max(ds, dr)), {
        "resistance": [{"tf": s.tf, "level": s.level, "strength": s.strength} for s in resistance],
        "support": [{"tf": s.tf, "level": s.level, "strength": s.strength} for s in support]
    }

def _pick_trigger_candidates(*, px, daily_support, daily_resistance, r30_high, r30_low, f24_vah, f24_val) -> Tuple[float, float, Dict]:
    min_from_px = 0.005
    min_level_spacing = 0.0015
    
    bo_base = max([v for v in [r30_high, f24_vah] if v > 0] or [0.0])
    bd_base = min([v for v in [r30_low, f24_val] if v > 0] or [0.0])
    
    if bo_base <= 0: bo_base = daily_support + 0.7 * (daily_resistance - daily_support)
    if bd_base <= 0: bd_base = daily_support + 0.3 * (daily_resistance - daily_support)
    
    bo = max(bo_base, px * (1 + min_from_px))
    bd = min(bd_base, px * (1 - min_from_px))
    
    return float(bo), float(bd), {
        "breakout_side": [{"level": float(bo_base), "strength": 6.0}],
        "breakdown_side": [{"level": float(bd_base), "strength": 6.0}]
    }

def compute_sse_levels(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Standard Engine for Day Trading Suite.
    """
    def f(k, d=0.0):
        try: return float(inputs.get(k, d) or d)
        except: return float(d)

    px = f("last_price")
    ref_px = f("session_open_price") or px
    
    res, sup = _build_htf_shelves(f("h4_supply"), f("h4_demand"), f("h1_supply"), f("h1_demand"))
    ds, dr, htf_out = _select_daily_levels(res, sup)
    
    if ref_px <= 0: ref_px = (ds + dr) / 2.0
    
    bo, bd, intraday = _pick_trigger_candidates(
        px=ref_px, daily_support=ds, daily_resistance=dr,
        r30_high=f("r30_high"), r30_low=f("r30_low"),
        f24_vah=f("f24_vah"), f24_val=f("f24_val")
    )
    
    return {
        "levels": {
            "daily_support": ds, "daily_resistance": dr,
            "breakout_trigger": bo, "breakdown_trigger": bd,
            "range30m_high": f("r30_high"), "range30m_low": f("r30_low")
        },
        "htf_shelves": htf_out,
        "intraday_shelves": intraday
    }