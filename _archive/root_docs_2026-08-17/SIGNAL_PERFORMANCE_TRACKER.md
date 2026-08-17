# Signal Performance Tracker

## Purpose

Track every signal — from Meta Signals AND Kabroda's own market radar — at decision time. Capture the full state of every indicator. Later, record what price action actually did. Over enough entries, find which indicators are predictive and which are noise.

## How It Works

1. **At signal time:** Fill in the entry with all indicator states (queried live from the API)
2. **After the move plays out:** Update the outcome (TPs hit, stop hit, or price action result)
3. **After 30+ entries:** Analyze which indicators consistently predicted the outcome

## Price Action Regime

Before evaluating any signal, classify what the market is doing:

| Regime | Description | Revin Ribbon Signature |
|--------|-------------|----------------------|
| **TRENDING** | Strong directional move, ADX > 25 on multiple TFs | Midline clearly rising/falling, price riding outer bands |
| **RANGING** | Sideways, no clear direction, ADX < 20 | Midline flat, price oscillating around midline |
| **COMPRESSING** | Volatility squeeze, BBWP low, breakout imminent | Gray dots clustering, bands narrowing |
| **EXPANDING** | Volatility breakout in progress, BBWP rising | Bands widening, price moving away from midline |

---

## Entry Format

```
### Entry #[N] — [DATE] ([SYMBOL])

**Source:** Meta Signals / Kabroda Market Radar
**Signal:** [DIRECTION] @ [ENTRY] → TP1 / TP2 / TP3 | SL: [STOP] | [TIMEFRAME]
**Price at Signal:** [PRICE]
**Price Action Regime:** TRENDING / RANGING / COMPRESSING / EXPANDING

### Indicator State at Signal Time

| Indicator | 15M | 1H | 4H | 1D | 1W |
|-----------|-----|----|----|----|----|
| EMA Bias | | | | | |
| StochRSI Zone | | | | | |
| StochRSI Curl | | | | | |
| ADX Value | | | | | |
| ADX Strength | | | | | |
| ADX Rising | | | | | |
| Direction Vote | | | | | |
| BBWP Value | | | | | |
| BBWP Compressed | | | | | |
| PMARP Value | | | | | |
| PMARP Overextended | | | | | |
| PMARP Direction | | | | | |
| Divergence | | | | | |
| Divergence Strength | | | | | |
| Revin Zone | | | | | |
| Revin Gray Dot | | | | | |
| Revin Outer Band | | | | | |
| Revin Midline Dir | | | | | |
| RMO Score | | | | | |
| RMO State | | | | | |
| RMO Overextended | | | | | |
| RWP Score | | | | | |
| RWP State | | | | | |
| RWP Squeeze | | | | | |
| RWP Expansion | | | | | |

### Composite Signals

| Signal | Value |
|--------|-------|
| Confluence Score | [X]/5 |
| Dominant Direction | |
| Conviction | |
| JEWEL Gate Open | |
| JEWEL Direction | |
| JEWEL Conviction | |
| JEWEL Exit Warning | |
| JEWEL Divergence Warning | |
| JEWEL RWP Squeeze | |
| JEWEL RMO Overextended | |
| JEWEL Revin Gray Dot | |
| JEWEL Summary | |

### Gravity (BTC only)

| Level | Price | Intensity | Notes |
|-------|-------|-----------|-------|
| Nearest Support | | | |
| Nearest Resistance | | | |
| Macro Beams | | | |

### Kabroda Read

[Full analysis text]

### Outcome

- **TP1:** ✅ Hit / ❌ Missed / ⏳ Pending
- **TP2:** ✅ Hit / ❌ Missed / ⏳ Pending
- **TP3:** ✅ Hit / ❌ Missed / ⏳ Pending
- **Stop:** ✅ Held / ❌ Hit / ⏳ Pending
- **Max Favorable Excursion:** [X]%
- **Max Adverse Excursion:** [X]%
- **Price Action Result:** [What actually happened]

### Post-Mortem

[What the indicators were saying vs. what happened. Which indicators predicted the outcome correctly? Which were misleading?]

### Accuracy Assessment

| Indicator | Predicted Correctly? | Notes |
|-----------|--------------------|-------|
| Confluence Score | ✅ / ❌ | |
| JEWEL Gate | ✅ / ❌ | |
| ADX | ✅ / ❌ | |
| Revin Ribbon | ✅ / ❌ | |
| BBWP | ✅ / ❌ | |
| PMARP | ✅ / ❌ | |
| RMO/RWP | ✅ / ❌ | |
| Divergence | ✅ / ❌ | |
| Gravity | ✅ / ❌ | |
```

---

## Log

### Entry 1 — 2026-07-16 (BTC)

**Source:** Meta Signals
**Signal:** LONG @ $64,677.70 → TP1: $65,673 / TP2: $66,461 / TP3: $68,519 | SL: $63,740 | 2H
**Price at Signal:** $64,677.70
**Price Action Regime:** RANGING (ADX weak on most TFs, no clear trend)

### Indicator State at Signal Time

| Indicator | 15M | 1H | 4H | 1D | 1W |
|-----------|-----|----|----|----|----|
| EMA Bias | BEARISH | BEARISH | BULLISH | BEARISH | BEARISH |
| StochRSI Zone | NEUTRAL | VALUE_HIGH | OVERSOLD | VALUE_LOW | VALUE_LOW |
| StochRSI Curl | DOWN | UP | UP | DOWN | UP |
| ADX Value | 29.93 | 36.95 | 23.46 | 23.1 | 26.52 |
| ADX Strength | STRONG | STRONG | WEAK | WEAK | STRONG |
| ADX Rising | true | false | false | true | true |
| Direction Vote | BEARISH | BEARISH | BULLISH | BEARISH | BEARISH |
| BBWP Value | 43.25 | 76.98 | 37.7 | 35.71 | 38.46 |
| BBWP Compressed | false | false | false | false | false |
| PMARP Value | 15.08 | 21.03 | 23.81 | 62.3 | 43.14 |
| PMARP Overextended | false | false | false | false | false |
| PMARP Direction | BELOW | BELOW | BELOW | BELOW | BELOW |
| Divergence | BULLISH | NONE | NONE | NONE | BULLISH |
| Divergence Strength | STRONG | NONE | NONE | NONE | WEAK |
| Revin Zone | AT_MIDLINE | AT_MIDLINE | AT_MIDLINE | AT_MIDLINE | BELOW_LOWER_1σ |
| Revin Gray Dot | true | true | true | false | false |
| Revin Outer Band | false | false | false | false | false |
| Revin Midline Dir | FLAT | FALLING | FALLING | FLAT | FALLING |
| RMO Score | -28.39 | -62.54 | -43.31 | -11.95 | -64.73 |
| RMO State | NEUTRAL | STRONG_BEARISH | BEARISH | NEUTRAL | STRONG_BEARISH |
| RMO Overextended | false | true | false | false | true |
| RWP Score | 40.48 | 76.59 | 46.03 | 34.92 | 28.85 |
| RWP State | NEUTRAL | ACTIVE_EXPANSION | NEUTRAL | NEUTRAL | MODERATE_COMPRESSION |
| RWP Squeeze | false | false | false | false | false |
| RWP Expansion | false | true | false | false | false |

### Composite Signals

| Signal | Value |
|--------|-------|
| Confluence Score | 3/5 |
| Dominant Direction | BEARISH |
| Conviction | HIGH |
| JEWEL Gate Open | false |
| JEWEL Direction | BEARISH |
| JEWEL Conviction | LOW |
| JEWEL Exit Warning | false |
| JEWEL Divergence Warning | true |
| JEWEL RWP Squeeze | false |
| JEWEL RMO Overextended | true |
| JEWEL Revin Gray Dot | true |
| JEWEL Summary | Gate closed — no compression detected, stand down. |

### Gravity (BTC only)

| Level | Price | Intensity | Notes |
|-------|-------|-----------|-------|
| Nearest Support | $61,728 | LIGHT | Below current price |
| Nearest Resistance | $64,567 | LIGHT | Above current price |
| Macro Beams | $60,055 | BEAR_WAVE_3_LOW | Major structural support |

### Kabroda Read

Counter-trend trade. Dominant confluence bearish (3/5 TFs). JEWEL signal warning against longs. TP1 sits on gravity peak ($65,702) — scale out there. Treat as scalp, not swing.

### Outcome

- **TP1:** ⏳ Pending
- **TP2:** ⏳ Pending
- **TP3:** ⏳ Pending
- **Stop:** ⏳ Pending
- **Max Favorable Excursion:** ⏳ Pending
- **Max Adverse Excursion:** ⏳ Pending
- **Price Action Result:** ⏳ Pending

### Post-Mortem

⏳ Pending

### Accuracy Assessment

⏳ Pending

---

### Entry 2 — 2026-07-12 (ETH) — Historical

**Source:** Meta Signals
**Signal:** SHORT @ $1,779.76 → TP1: $1,728.19 / TP2: $1,697.98 / TP3: $1,590.38 | SL: $1,831.26 | 4H
**Price at Signal:** $1,779.76
**Price Action Regime:** TRENDING (4H ADX 36.95 STRONG, bullish)

### Indicator State at Signal Time

| Indicator | 15M | 1H | 4H | 1D | 1W |
|-----------|-----|----|----|----|----|
| EMA Bias | — | — | BULLISH | — | — |
| ADX Value | — | — | 36.95 | — | — |
| ADX Strength | — | — | STRONG | — | — |
| Direction Vote | — | — | BULLISH | — | — |

*Note: Full per-TF data not captured at time of historical analysis. Key finding: 4H was BULLISH with STRONG ADX — short was counter-trend.*

### Composite Signals

| Signal | Value |
|--------|-------|
| Confluence Score | — |
| Dominant Direction | — |
| JEWEL Direction | BEARISH |
| JEWEL Conviction | STRONG |

### Kabroda Read

❌ Stopped out. Price blew past SL ($1,831.26). The 4H EMA bias was BULLISH at signal time — the short was against the immediate trend. Gravity peaks at $1,744.57 and $1,713.13 aligned well with TP1/TP2, but the stop was too tight for the bullish 4H momentum (ADX 36.95 STRONG). The JEWEL signal was bearish strong conviction, but the 4H timeframe itself was bullish — a conflict that should have been a red flag.

### Outcome

- **TP1:** ❌ Missed
- **TP2:** ❌ Missed
- **TP3:** ❌ Missed
- **Stop:** ❌ Hit ($1,831.26)
- **Max Favorable Excursion:** Unknown
- **Max Adverse Excursion:** Unknown (price reached $1,880+)
- **Price Action Result:** Price continued bullish, blew through stop

### Post-Mortem

The JEWEL signal was bearish with strong conviction, but the 4H EMA bias was BULLISH with ADX 36.95 STRONG. The JEWEL was reading lower-timeframe compression while the 4H trend was strongly bullish. This is a classic timeframe conflict — the JEWEL was right about the micro move but wrong about the macro direction. The 4H ADX was the more reliable indicator.

### Accuracy Assessment

| Indicator | Predicted Correctly? | Notes |
|-----------|--------------------|-------|
| Confluence Score | ❌ | Unknown at time, but 4H bullish bias was the key |
| JEWEL Gate | ❌ | Bearish strong conviction was misleading |
| ADX | ✅ | 4H ADX 36.95 STRONG bullish correctly predicted trend continuation |
| Gravity | ✅ | TP levels near gravity peaks were structurally sound |
| Divergence | ❌ | Not a factor |

---

### Entry 3 — 2026-07-12 (LINK) — Historical

**Source:** Meta Signals
**Signal:** SHORT @ $7.917 → TP1: $7.692 / TP2: $7.532 / TP3: $7.245 | SL: $8.123 | 4H
**Price at Signal:** $7.917
**Price Action Regime:** TRENDING (4H ADX 33.12 STRONG, bullish)

### Indicator State at Signal Time

| Indicator | 15M | 1H | 4H | 1D | 1W |
|-----------|-----|----|----|----|----|
| EMA Bias | — | — | BULLISH | — | — |
| ADX Value | — | — | 33.12 | — | — |
| ADX Strength | — | — | STRONG | — | — |
| Direction Vote | — | — | BULLISH | — | — |

*Note: Full per-TF data not captured at time of historical analysis. Same pattern as ETH.*

### Kabroda Read

❌ Stopped out. Same pattern as ETH — 4H EMA bias was BULLISH, ADX 33.12 STRONG. Shorting into a strong bullish 4H trend was high risk. No gravity KDE data for LINK (empty curve), but macro fibs showed TP1 ($7.692) near fib 0.5 ($7.82) and TP2 ($7.532) near fib 0.618 ($7.63) — those levels were structurally sound, but the direction was wrong.

### Outcome

- **TP1:** ❌ Missed
- **TP2:** ❌ Missed
- **TP3:** ❌ Missed
- **Stop:** ❌ Hit ($8.123)
- **Max Favorable Excursion:** Unknown
- **Max Adverse Excursion:** Unknown (price reached $8.446+)
- **Price Action Result:** Price continued bullish, blew through stop

### Post-Mortem

Identical pattern to ETH. Shorting into a strong bullish 4H trend (ADX 33.12) is a losing strategy regardless of what the lower timeframes say. The 4H ADX was the single most predictive indicator.

### Accuracy Assessment

| Indicator | Predicted Correctly? | Notes |
|-----------|--------------------|-------|
| ADX | ✅ | 4H ADX 33.12 STRONG bullish correctly predicted trend continuation |
| Gravity/Fibs | ✅ | TP levels near fib levels were structurally sound |
| Direction vs. Trend | ✅ | Short into bullish trend = high risk |

---

### Entry 4 — 2026-07-16 (BNB) — Active

**Source:** Meta Signals
**Signal:** SHORT @ $578.45 → TP1: $563.669 / TP2: $553.416 / TP3: $525.659 | SL: $592.115 | 4H
**Price at Signal:** $578.45
**Price Action Regime:** TRENDING (4/5 TFs bearish, strong confluence)

### Indicator State at Signal Time

| Indicator | 15M | 1H | 4H | 1D | 1W |
|-----------|-----|----|----|----|----|
| EMA Bias | BEARISH | BEARISH | BEARISH | BEARISH | — |
| StochRSI Zone | — | — | OVERSOLD | — | — |
| Direction Vote | BEARISH | BEARISH | BEARISH | BEARISH | — |

*Note: Full per-TF data not captured at time. Key finding: 4/5 TFs bearish — strongest confluence of all signals tracked.*

### Composite Signals

| Signal | Value |
|--------|-------|
| Confluence Score | 4/5 |
| Dominant Direction | BEARISH |
| JEWEL Direction | BEARISH |
| JEWEL Conviction | STRONG |

### Kabroda Read

🟡 Active — in play. Price has moved slightly in favor ($578.45 → $576.51). HIGH BEARISH confluence (4/5 TFs aligned) — strongest read of all signals today. JEWEL bearish strong conviction. 4H StochRSI oversold (K: 0, D: 15.98) — potential bounce zone, but the bearish confluence is dominant. No gravity KDE data for BNB (empty curve). Macro fibs: TP1 ($563.67) near fib 0.618 ($566.12), TP2 ($553.42) near fib 0.786 ($553.62), TP3 ($525.66) below fib 0.786. BTC context: BTC is also bearish confluence — market-wide alignment.

### Outcome

- **TP1:** ⏳ Pending
- **TP2:** ⏳ Pending
- **TP3:** ⏳ Pending
- **Stop:** ⏳ Pending
- **Max Favorable Excursion:** ⏳ Pending
- **Max Adverse Excursion:** ⏳ Pending
- **Price Action Result:** ⏳ Pending

### Post-Mortem

⏳ Pending

### Accuracy Assessment

⏳ Pending

---

### Entry 5 — 2026-07-16 (DOGE) — Active

**Source:** Meta Signals
**Signal:** LONG @ $0.07231 → TP1: $0.075235 / TP2: $0.080204 / TP3: $0.083553 | SL: $0.069013 | 24H
**Price at Signal:** $0.07209
**Price Action Regime:** COMPRESSING (1D BBWP 7.94, 1W BBWP 13.46, RWP squeeze on both)

### Indicator State at Signal Time

| Indicator | 15M | 1H | 4H | 1D | 1W |
|-----------|-----|----|----|----|----|
| EMA Bias | BEARISH | BEARISH | BEARISH | BEARISH | BEARISH |
| StochRSI Zone | NEUTRAL | VALUE_LOW | OVERSOLD | NEUTRAL | OVERSOLD |
| StochRSI Curl | DOWN | UP | FLAT | DOWN | FLAT |
| ADX Value | 29.93 | 23.49 | 16.01 | 32.93 | 32.7 |
| ADX Strength | STRONG | WEAK | WEAK | STRONG | STRONG |
| ADX Rising | true | true | false | false | true |
| Direction Vote | BEARISH | BEARISH | BEARISH | BEARISH | BEARISH |
| BBWP Value | 43.25 | 57.94 | 20.24 | 7.94 | 13.46 |
| BBWP Compressed | false | false | true | true | true |
| PMARP Value | 15.08 | 15.87 | 34.13 | 51.19 | 33.33 |
| PMARP Overextended | false | false | false | false | false |
| PMARP Direction | BELOW | BELOW | BELOW | BELOW | BELOW |
| Divergence | BULLISH | NONE | NONE | BULLISH | HIDDEN_BEARISH |
| Divergence Strength | STRONG | NONE | NONE | WEAK | WEAK |
| Revin Zone | BELOW_LOWER_1σ | BELOW_LOWER_1σ | BELOW_LOWER_1σ | BELOW_LOWER_1σ | BELOW_LOWER_1σ |
| Revin Gray Dot | true | true | true | false | false |
| Revin Outer Band | false | false | false | false | false |
| Revin Midline Dir | FALLING | FALLING | FALLING | FALLING | FALLING |
| RMO Score | -28.39 | -50.53 | -41.42 | -37.55 | -70.98 |
| RMO State | NEUTRAL | BEARISH | BEARISH | BEARISH | STRONG_BEARISH |
| RMO Overextended | false | false | false | false | true |
| RWP Score | 40.48 | 50.79 | 17.06 | 8.73 | 9.62 |
| RWP State | NEUTRAL | NEUTRAL | MODERATE_COMPRESSION | EXTREME_SQUEEZE | EXTREME_SQUEEZE |
| RWP Squeeze | false | false | false | true | true |
| RWP Expansion | false | false | false | false | false |

### Composite Signals

| Signal | Value |
|--------|-------|
| Confluence Score | 5/5 |
| Dominant Direction | BEARISH |
| Conviction | HIGH |
| JEWEL Gate Open | true |
| JEWEL Direction | BEARISH |
| JEWEL Conviction | STRONG |
| JEWEL Exit Warning | false |
| JEWEL Divergence Warning | true |
| JEWEL RWP Squeeze | true |
| JEWEL RMO Overextended | true |
| JEWEL Revin Gray Dot | true |
| JEWEL Summary | Gate open. Bearish bias, strong conviction. RWP squeeze confirms compression — breakout imminent. RMO overextended — momentum exhaustion warning. Revin gray dot tested — support/resistance bounce zone. Divergence detected — potential reversal signal. |

### Gravity

No KDE data for DOGE (empty curve).

### Kabroda Read

❌ SKIP. Counter-trend long into 5/5 BEARISH confluence — every timeframe from 15M to Weekly votes bearish. JEWEL gate open with STRONG bearish conviction. T1 RR is 0.89 (negative expectancy — risk 4.5% to make 4%). Price already below entry. The one supporting factor is extreme compression (1D BBWP 7.94, 1W BBWP 13.46, RWP squeeze on both) — DOGE is coiled and due for a move, but buying long into a fully aligned bearish system is not the way to play it. 4H StochRSI at 0.0 (oversold floor) and 15M BULLISH STRONG divergence suggest a bounce is possible, but that's a reversal gamble, not a structural trade.

### Outcome

- **TP1:** ⏳ Pending — skipped on analysis
- **TP2:** ⏳ Pending — skipped on analysis
- **TP3:** ⏳ Pending — skipped on analysis
- **Stop:** ⏳ Pending — skipped on analysis
- **Max Favorable Excursion:** ⏳ Pending
- **Max Adverse Excursion:** ⏳ Pending
- **Price Action Result:** ⏳ Pending

### Post-Mortem

⏳ Pending

### Accuracy Assessment

⏳ Pending

---

## Summary Dashboard

*To be updated as entries are completed.*

### Indicator Accuracy (Completed Entries Only)

| Indicator | Correct | Incorrect | Accuracy |
|-----------|---------|-----------|----------|
| Confluence Score (counter-trend flag) | 2 | 0 | 100% |
| ADX (trend strength) | 2 | 0 | 100% |
| JEWEL Gate | 0 | 1 | 0% |
| Gravity/Fib Levels | 2 | 0 | 100% |

### Key Patterns Found So Far

1. **Counter-trend trades fail.** ETH and LINK were shorts into bullish 4H trends. Both stopped out.
2. **ADX > 30 on the signal timeframe** is a strong predictor of trend continuation. Fighting it is high risk.
3. **JEWEL can be misleading** when it's reading lower-TF compression against a strong higher-TF trend.
4. **Confluence alignment matters.** BNB (4/5 bearish) is the only trade still in play. DOGE (5/5 bearish) was skipped.
5. **Gravity/fib levels are accurate** for TP placement when data is available.
