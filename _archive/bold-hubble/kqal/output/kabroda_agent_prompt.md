# KQAL Improvement Queue — 2026-07-15 17:46 UTC

## Current Alignment Score: 7.2/10

### Queue Summary
- **Total Items:** 10
- **High Priority:** 3
- **Medium Priority:** 6
- **Low Priority:** 1
- **Total Estimated Alignment Gain:** +10.6

## High Priority Items

### IMP-001: Add Revin Ribbons Indicator
**Impact:** +1.5 alignment score | **Effort:** MEDIUM | **Category:** MISSING_INDICATOR
**Status:** NOT_BUILT | **Krown Frequency:** 8/15 videos
**Estimated Alignment Gain:** +1.5

**Why:** Krown references Revin Ribbons midband in 8/15 videos as his primary trend-bias indicator. Kabroda has no equivalent. The Revin Ribbons suite includes a 21-period EMA midline, ±1.0/±2.5/±3.5 StDev bands, RWP (Revin Width Percentile), and RMO (Revin Momentum Oscillator).

**Build instructions:**

Build a Revin Ribbons indicator module:
1. Create indicators/revin_ribbons.py with:
   - 21-period EMA midline calculation
   - ±1.0, ±2.5, ±3.5 Standard Deviation bands
   - RWP (Revin Width Percentile) — same calc as BBWP on Revin bands
   - RMO (Revin Momentum Oscillator) — composite -100/+100 score
2. Wire into mtf_confluence_scanner.py
3. Add to gravity_engine.py candidate context
4. Add to pipeline/krown_to_kabroda_bridge.py indicator mapping

### IMP-002: SSE-into-TSA Target Wiring
**Impact:** +1.5 alignment score | **Effort:** MEDIUM | **Category:** ARCHITECTURE_GAP
**Status:** NOT_BUILT | **Krown Frequency:** 7/15 videos
**Dependencies:** IMP-001
**Estimated Alignment Gain:** +1.5

**Why:** SSE (Support/Resistance) levels from Krown's S/R detection are not wired into TSA (Target) computation. This means Kabroda generates targets without considering key S/R levels, producing unrealistic profit targets.

**Build instructions:**

Wire SSE S/R levels into target computation:
1. Create a target_computation module that reads SSE levels
2. Implement nearest-S/R target logic: take profit at next major S/R level
3. Implement S/R cluster target: zone where multiple S/R levels converge
4. Add Fibonacci extension targets filtered by S/R proximity
5. Wire into pipeline/krown_to_kabroda_bridge.py trade setup generation
6. Update strategy evaluation to use S/R-aware targets

### IMP-003: Position Sizing Module
**Impact:** +1.5 alignment score | **Effort:** MEDIUM | **Category:** MISSING_FEATURE
**Status:** NOT_BUILT | **Krown Frequency:** 9/15 videos
**Estimated Alignment Gain:** +1.5

**Why:** Kabroda has no position sizing mechanism. Krown emphasizes risk-based sizing (1-2% risk per trade, volatility-adjusted). Without sizing, Kabroda cannot generate actionable trade plans with proper risk management.

**Build instructions:**

Build a position sizing module:
1. Create strategies/position_sizing.py with:
   - Fixed fractional sizing (1-2% risk per trade)
   - Volatility-adjusted sizing (ATR-based position scaling)
   - Kelly Criterion option for optimal growth
2. Add account balance input parameter
3. Wire into trade setup generation in pipeline
4. Add to strategy evaluation output

---

## Medium Priority Items

### IMP-004: Fibonacci EMA Ribbon (5/21/55/377)
**Impact:** +1.0 alignment score | **Effort:** SMALL | **Category:** MISSING_INDICATOR
**Status:** PARTIAL | **Krown Frequency:** 6/15 videos
**Estimated Alignment Gain:** +1.0

**Why:** Krown uses a Fibonacci-based EMA ribbon (5, 21, 55, 377) for multi-timeframe trend alignment. Kabroda currently uses 9/21/35/55 SMA. The Fib sequence provides better harmonic alignment with Krown's methodology.

**Build instructions:**

Update the moving average system to support Fibonacci EMA ribbon:
1. Add 5 EMA and 377 EMA to the MA configuration
2. Replace 9 SMA with 5 EMA for ultra-fast trend
3. Replace 35 SMA with 55 EMA for intermediate trend
4. Add 377 EMA for macro trend context
5. Update trend_volatility.py to use Fib EMAs in trend scoring
6. Update krown_settings_and_rules.json with new MA settings

### IMP-005: Three Drives Divergence Detection
**Impact:** +1.0 alignment score | **Effort:** MEDIUM | **Category:** MISSING_PATTERN
**Status:** PARTIAL | **Krown Frequency:** 5/15 videos
**Estimated Alignment Gain:** +1.0

**Why:** Krown requires 3 drives (swing points) to confirm divergence patterns. Kabroda's rsi_divergence.py only detects single divergences between 2 points. Adding 3-drive detection would align with Krown's methodology and reduce false signals.

**Build instructions:**

Enhance rsi_divergence.py with Three Drives divergence detection:
1. Add function detect_three_drives(highs, lows, rsi_values) -> List[Dict]
2. Implement swing point detection with configurable pivot lookback (default 3)
3. Detect 3-drive bullish pattern: price LL, HL, LL with RSI making higher lows
4. Detect 3-drive bearish pattern: price HH, LH, HH with RSI making lower highs
5. Add confidence scoring based on harmonic ratios between drives
6. Wire into pipeline/krown_to_kabroda_bridge.py divergence mapping

### IMP-006: Live Exhaustion Monitor
**Impact:** +1.0 alignment score | **Effort:** MEDIUM | **Category:** MISSING_FEATURE
**Status:** NOT_BUILT | **Krown Frequency:** 4/15 videos
**Estimated Alignment Gain:** +1.0

**Why:** In-trade runner exhaustion signals are not monitored. Krown teaches that runners should be monitored for momentum exhaustion (PMARP > 95%, BBWP > 85%, RSI divergence). Kabroda has no in-trade monitoring capability.

**Build instructions:**

Build a live exhaustion monitor:
1. Create strategies/exhaustion_monitor.py with:
   - PMARP overextension detection (> 95%)
   - BBWP blow-off detection (> 85%)
   - RSI divergence confirmation on each new bar
2. Implement alert levels: WATCH, WARNING, EXIT
3. Add trailing stop adjustment logic on exhaustion signals
4. Wire into pipeline output as in-trade advisory

### IMP-007: Entry Mechanics Model
**Impact:** +1.0 alignment score | **Effort:** MEDIUM | **Category:** MISSING_FEATURE
**Status:** NOT_BUILT | **Krown Frequency:** 5/15 videos
**Estimated Alignment Gain:** +1.0

**Why:** Kabroda lacks a formal entry mechanics model. Krown distinguishes between trigger entries (aggressive), confirm entries (conservative), and retest entries (optimal). Modeling these would improve entry precision.

**Build instructions:**

Build an entry mechanics model:
1. Create strategies/entry_mechanics.py with:
   - Trigger entry: enter on first signal bar close
   - Confirm entry: enter after confirmation candle
   - Retest entry: enter on retest of broken level
2. Add configurable entry style per strategy
3. Wire into trade setup generation
4. Add to strategy evaluation output

### IMP-008: News/Event Calendar Integration
**Impact:** +0.8 alignment score | **Effort:** SMALL | **Category:** MISSING_FEATURE
**Status:** NOT_BUILT | **Krown Frequency:** 3/15 videos
**Estimated Alignment Gain:** +0.8

**Why:** No FOMC or economic event awareness. Krown frequently references FOMC weeks, NFP releases, and CPI data as volatility catalysts. Kabroda should be aware of upcoming events to adjust position sizing and avoid trading into high-impact news.

**Build instructions:**

Add news/event calendar awareness:
1. Create a simple event calendar module that reads from a JSON config
2. Add FOMC, NFP, CPI, and other high-impact event dates
3. Implement event proximity check: days until next major event
4. Add position sizing adjustment: reduce size near events
5. Wire into strategy evaluation as a risk modifier

### IMP-009: Stand-Down Re-Arm Alerter
**Impact:** +0.5 alignment score | **Effort:** SMALL | **Category:** MISSING_FEATURE
**Status:** NOT_BUILT | **Krown Frequency:** 2/15 videos
**Estimated Alignment Gain:** +0.5

**Why:** After a trade is taken, Kabroda has no mechanism to alert when conditions improve for re-entry. Krown teaches waiting for re-arm (RSI reset, price returning to value zone). This would alert when re-arm conditions are met.

**Build instructions:**

Build a stand-down re-arm alerter:
1. Create strategies/rearm_alerter.py
2. Monitor for RSI reset to 40-50 range after overbought/oversold
3. Monitor for price return to value zone (between 20 & 50 MA)
4. Monitor for BBWP re-compression after expansion
5. Generate alert when re-arm conditions are met
6. Wire into pipeline output

---

## Low Priority Items

### IMP-010: Runner Mechanic (Partial Profit Trailing)
**Impact:** +0.8 alignment score | **Effort:** MEDIUM | **Category:** MISSING_FEATURE
**Status:** NOT_BUILT | **Krown Frequency:** 4/15 videos
**Estimated Alignment Gain:** +0.8

**Why:** Kabroda has no runner/partial profit management. Krown teaches taking partial profits at key levels and trailing the remainder. This would add partial take-profit and trailing stop logic.

**Build instructions:**

Build a runner mechanic module:
1. Create strategies/runner_mechanic.py with:
   - Partial take-profit levels (e.g., 33% at 1:1, 33% at 1.618, 34% runner)
   - Trailing stop logic (ATR-based or MA-based trail)
   - Breakeven stop after first target hit
2. Add configurable profit split per strategy
3. Wire into trade setup generation
4. Add to strategy evaluation output

---

## Alignment Report Context

```json
{
  "alignment_score": 7.2,
  "total_checks": 15,
  "passed_checks": 11,
  "failed_checks": 4,
  "gaps_found": 3,
  "generated_at": "2026-07-15T17:46:58.779696+00:00"
}
```

## Validation Report Context

```json
{
  "total_patterns_checked": 8,
  "patterns_matched": 5,
  "patterns_missing": 3,
  "generated_at": "2026-07-15T17:46:58.779713+00:00"
}
```

---
*Generated by KQAL Improvement Queue at 2026-07-15T17:46:58.780109+00:00*
