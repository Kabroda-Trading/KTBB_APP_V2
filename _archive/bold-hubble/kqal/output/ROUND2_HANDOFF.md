# Round 2 Handoff — Revin Suite (R-Squared) + EMA Fibonacci Ribbon

## What Was Built

### 1. `indicators/revin_ribbons.py` — Revin Ribbons (Pillar 1)
- 21-period EMA midline (equilibrium)
- ±1.0σ inner bands (gray dot support zone)
- ±2.5σ and ±3.5σ outer bands (exhaustion zones)
- `analyze_ribbon_state()` → zone classification, gray dot test, midline direction

### 2. `indicators/rmo.py` — Revin Momentum Oscillator (Pillar 2)
- Composite -100 to +100 from 4 sub-components:
  - Duration score (bars since last pivot flip)
  - Magnitude score (move in ATR units)
  - Separation score (EMA 8/21 ribbon spread)
  - RSI score (normalized)
- `analyze_rmo_state()` → STRONG_BULLISH / BULLISH / NEUTRAL / BEARISH / STRONG_BEARISH

### 3. `indicators/rwp.py` — Revin Width Percentile (Pillar 3)
- Percentile rank of Revin Ribbons band width over 252-bar lookback
- `analyze_rwp_state()` → EXTREME_SQUEEZE (≤10%), MODERATE_COMPRESSION, ACTIVE_EXPANSION, EXTREME_EXPANSION

### 4. `indicators/revin_suite_engine.py` — Unified Engine
- `compute_revin_suite()` runs all 3 pillars in one call
- `_compute_combined_signal()` synthesizes into: BOUNCE_SETUP, EXHAUSTION_WARNING, TREND_CONFIRMED, SQUEEZE_WATCH, NEUTRAL

### 5. `indicators/ema_ribbon.py` — Fibonacci EMA Ribbon
- EMA 5/21/55/377 (Fibonacci sequence)
- `analyze_ema_ribbon()` → BULLISH/BEARISH/MIXED/COMPRESSED alignment
- Ribbon spread % and dominant EMA detection

### 6. `indicators/__init__.py` — Updated exports

## Smoke Test Results
```
All modules import OK
Ribbons midline[-1]: 52906.1
Ribbons lower_1σ[-1]: 52697.68
RMO[-1]: 22.37
RWP[-1]: 43.25
Combined signal: NEUTRAL
RWP at 5%: EXTREME_SQUEEZE | squeeze: True
RMO at 75: STRONG_BULLISH | overextended: True
EMA Ribbon alignment: MIXED
ALL TESTS PASSED
```

## What to Review
1. **revin_ribbons.py** — Are the StDev calculations correct? The `calculate_stdev` from `bbwp.py` uses population StDev (divides by N, not N-1). Is that the right choice for Krown's methodology?
2. **rmo.py** — The ATR calculation uses SMA of True Range. Krown may use Wilder's smoothed ATR. Worth checking.
3. **rwp.py** — Uses the same percentile rank logic as BBWP. Should be consistent.
4. **revin_suite_engine.py** — The combined signal priority logic. Does the order make sense?
5. **ema_ribbon.py** — The alignment detection (all EMAs rising/falling + correct order). Is this too strict?

## Not Yet Wired
These modules are standalone. They still need to be wired into:
- `mtf_confluence_scanner.py`
- `gravity_engine.py` (candidate context)
- `krown_to_kabroda_bridge.py` (indicator mapping)
- `krown_system.py` (strategy evaluation)

That's for a future round.
