# CC_WORK_ORDER_METASIGNALS_SHORTS_AND_MAJORS.md
## Turnkey Implementation: P0 Majors Comma Bug & Symmetrical Short-Side Integration

**Status:** P0 Execution Specification  
**Authority:** Andy Directive (2026-10-16 08:15 CT)  
**Author:** DeepSeek (Antigravity Coordinator)  
**Target System:** `KTBB_app_v2` / `kabroda.com`  
**Target Files:** `metasignals_triage.py`, `metasignals_paper_engine.py`, `tests/test_metasignals_paper.py`  
**Prerequisite Reading for CC:** `AGENT_LOG.md` entry `2026-10-16` (in both repos).

---

## 1. Executive Summary & Iron Wall Mandate

This work order delivers two critical system enhancements requested by Andy:
1. **P0 Fix for Majors Parsing (ETH & BTC):** Resolves the silent dropping of ETH and BTC alerts caused by regex digit-matching on prices with thousands-separator commas (`$2,534.63` and `$83,125.80`).
2. **Symmetrical Short-Side Integration:** Extends the MetaSignals 5-Pillar Triage Gate and Paper Engine to trade both Longs and Shorts on Bitunix perpetual futures, grounded in the 26 Short cells evaluated in Study 4.

### The Iron Wall Constraint (Non-Negotiable)
- **Zero Touch** on Bitcoin Traveler (`gate_traveler.py`, `traveler_plan_engine.py`, `battlebox_pipeline.py`).
- **Zero Touch** on Alt Matrix (`alt_matrix_*.py`).
- **Zero Touch** on existing `ExecutorAccount` rows.
- All modifications are strictly isolated to `metasignals_triage.py`, `metasignals_paper_engine.py`, and their unit tests.

---

## 2. PART 1: P0 Majors Comma Parsing Bug (ETH & BTC)

### A. Audit Findings & Empirical Proof
- **Live State:** `GET /api/metasignals/audit?limit=100` returns 54 total plans across 27 distinct altcoins. `SOLUSDT` is present (from `#mafioso-majors`), but **`ETHUSDT` and `BTCUSDT` have exactly 0 plans**.
- **Root Cause:** In `metasignals_triage.py` lines 220–228:
  ```python
  _ALERT_PATTERN = re.compile(
      r"(?P<symbol>[A-Z0-9]+)\s*\|\s*USDT\s*@\s*\$(?P<entry>\d+(?:\.\d+)?)\s*-\s*...",
  )
  _SL_PATTERN = re.compile(r"SL Close Below:\s*(?P<sl>\d+(?:\.\d+)?)")
  _T1_PATTERN = re.compile(r"Target 1:\s*(?P<tp1>\d+(?:\.\d+)?)")
  ```
  `\d+` only matches digits `0-9` and fails on commas `,`. For assets priced over \$1,000, MetaSignals Discord alerts format numbers as `$2,534.63` and `$83,125.80`. The regex fails $\rightarrow$ `parse_discord_alert()` returns `None` $\rightarrow$ `main.py` saves to `MetaSignalsRawAlert` with `parsed: False` and aborts without creating a `MetaSignalsPlan`.

### B. Exact Code Changes in `metasignals_triage.py`
1. **Update Regex Patterns (Lines ~220–228):**
   ```python
   _NUM_RE = r"[\d,]+(?:\.\d+)?"
   _ALERT_PATTERN = re.compile(
       r"(?P<symbol>[A-Z0-9]+)\s*\|\s*USDT\s*@\s*\$(?P<entry>" + _NUM_RE + r")\s*-\s*(?P<timeframe>[0-9A-Za-z]+)\s*-\s*"
       r"(?P<strategy>[0-9A-Za-z][0-9A-Za-z\.\s]*?)" + _NEXT_FIELD_LOOKAHEAD,
       re.IGNORECASE,
   )
   _SL_PATTERN = re.compile(r"SL Close Below:\s*(?P<sl>" + _NUM_RE + r")", re.IGNORECASE)
   _T1_PATTERN = re.compile(r"Target 1:\s*(?P<tp1>" + _NUM_RE + r")", re.IGNORECASE)
   _T2_PATTERN = re.compile(r"Target 2:\s*(?P<tp2>" + _NUM_RE + r")", re.IGNORECASE)
   _T3_PATTERN = re.compile(r"Target 3:\s*(?P<tp3>" + _NUM_RE + r")", re.IGNORECASE)
   ```

2. **Sanitize Commas Before Float Casts in `parse_discord_alert()` (Lines ~397–411):**
   ```python
   entry_price = float(match.group("entry").replace(",", ""))
   soft_stop = float(sl_match.group("sl").replace(",", ""))
   tp1 = float(tp1_match.group("tp1").replace(",", "")) if tp1_match else round(entry_price * 1.04, 4)
   tp2 = float(tp2_match.group("tp2").replace(",", "")) if tp2_match else round(entry_price * 1.08, 4)
   tp3 = float(tp3_match.group("tp3").replace(",", "")) if tp3_match else None
   ```

---

## 3. PART 2: Symmetrical Short-Side Integration

### A. Empirical Backtest Grounding (Study 4 Corpus)
Study 4 evaluated 159 total strategy cells across Binance perpetuals (2024–2026, 9,080 alerts). **26 cells were SHORTs**:
- **4 SHORTLIST Short Cells (Cleared All Bars, Static Stop PF $\ge 1.15$, Max DD $\le 20\%$):**
  1. `Strategy 1.1 G2` (240m / 4H): **Static PF 2.037**, **65.6% WR**, Max DD 5.9%, Streak 5 ($N=122$).
  2. `Strategy 3.1 G3` (240m / 4H): **Static PF 1.984**, **65.5% WR**, Max DD 11.9%, Streak 5 ($N=110$).
  3. `Strategy 1.0.2` (120m / 2H): **Static PF 1.503**, **60.9% WR**, Max DD 7.1%, Streak 7 ($N=115$).
  4. `Strategy 1.1.1` (60m / 1H): **Static PF 1.485**, **57.7% WR**, Max DD 7.7%, Streak 6 ($N=130$).
- **15 WATCHLIST Short Cells (PFs up to 2.80, e.g. `Strategy 1.1 G3 4H` PF 2.80, `Strategy 3.0 G2 4H` PF 2.77, `Strategy 2.0 G2 4H` PF 2.09).**
- **7 DO_NOT_TOUCH Trap Cells (Blacklist):** `1.1 G1 4H`, `2.0 G1 4H`, `1.0 G1 4H`, `3.0 G1 4H` ($N < 50$ noise), and `2.0 G3 4H` (Lose streak 12).

### B. Deterministic Side Detection in `parse_discord_alert()`
Do not rely solely on channel names or emojis. Use **Price Geometry** (100% deterministic):
```python
# Price geometry check:
if soft_stop > entry_price and (tp1 is None or tp1 < entry_price):
    side = "SELL"
elif soft_stop < entry_price and (tp1 is None or tp1 > entry_price):
    side = "BUY"
else:
    # Fallback to emoji/text indicator
    side = "SELL" if "📉" in text or "SHORT" in text.upper() else "BUY"
```

### C. The 5 Pillars Mirrored for Shorts in `metasignals_triage.py`

#### Pillar 1: Macro Trend (Inverted)
- **For `BUY`:** `btc_daily_close > btc_sma200` AND `asset_daily_close > asset_sma200` (or 55 EMA).
- **For `SELL`:** Asset must be in a confirmed structural macro downtrend:
  $$\text{Asset Daily Close} < \text{Asset Daily 200 SMA (or 55 EMA)}$$
  *(Note: BTC macro trend can either be below 200 SMA or neutral; asset downtrend is the binding filter).*
- Veto reason on failure: `VETO_MACRO_ABOVE_200SMA` (for shorts attempting to short an asset in macro bull trend).

#### Pillar 2: Momentum / Energy (Inverted 4H RSI)
- **For `BUY`:** $40.0 \le \text{RSI}_{4\text{H}} \le 62.0$.
- **For `SELL`:** $38.0 \le \text{RSI}_{4\text{H}} \le 60.0$.
  - If $\text{RSI}_{4\text{H}} < 38.0$: Veto on `VETO_MOMENTUM_COLLAPSED` (oversold exhaustion dump; violent squeeze bounce risk).
  - If $\text{RSI}_{4\text{H}} > 60.0$: Veto on `VETO_MOMENTUM_OVERBOUGHT` (bullish momentum breakout; shorting into strong strength).

#### Pillar 3: Timing & Extension ("Don't Chase")
- **For `BUY`:** $\text{Ext}_R = \frac{\text{Mark Price} - \text{Alert Price}}{R_{\text{distance}}}$.
- **For `SELL`:** $\text{Ext}_R = \frac{\text{Alert Price} - \text{Mark Price}}{R_{\text{distance}}}$.
  - If $\text{Ext}_R \le 0.10$: `entry_mode = "MARKET"`.
  - If $0.10 < \text{Ext}_R \le 0.25$: `entry_mode = "LIMIT"` (at alert price).
  - If $\text{Ext}_R > 0.25$: Veto on `VETO_PRICE_OVER_EXTENDED`.

#### Pillar 4: Funding Rate Filter (Inverted)
- **For `BUY`:** $\text{Funding Rate} < +0.0004$ ($+0.04\%$).
- **For `SELL`:** $\text{Funding Rate} > -0.0004$ ($-0.04\%$).
  - If $\text{Funding Rate} \le -0.0004$: Veto on `VETO_FUNDING_OVERCROWDED` (retail shorts are overcrowded and paying longs astronomical fees; massive short squeeze risk).

#### Pillar 5: Strategy Cell Whitelist
Update `PERMITTED_STRATEGY_TIMEFRAME_CELLS` to be keyed by `(strategy, timeframe, direction)`:
- Include the 82 proven LONG cells (Shortlist + Watchlist).
- Include the 19 proven SHORT cells (Shortlist + Watchlist) from Study 4.
- Known trap cells classified as `KNOWN_TRAP_STRATEGY_TIMEFRAME_CELLS`.

#### Hard Stop Geometry for Shorts
$$\text{Hard Stop Price} = \text{Alert Soft Stop} + (0.25 \times \text{ATR}_{14})$$
$$R_{\text{distance}} = \text{Hard Stop Price} - \text{Alert Price}$$
$$\text{Position Size} = \frac{\$50.00}{R_{\text{distance}}}$$

---

### D. Execution & Watcher Math in `metasignals_paper_engine.py`

Update `advance_order()`, `_fill_tp1()`, `_close_full()`, and `mark_unrealized()` to be direction-aware:

1. **Stop & TP Triggers in `advance_order()`:**
   ```python
   is_buy = (order.side == "BUY")
   stop_hit = (mark_price <= order.current_stop) if is_buy else (mark_price >= order.current_stop)
   tp1_hit = (mark_price >= order.tp1_price) if is_buy else (mark_price <= order.tp1_price)
   tp2_hit = (mark_price >= order.tp2_price) if is_buy else (mark_price <= order.tp2_price)
   ```

2. **P&L Calculations in `_fill_tp1()` and `_close_full()`:**
   ```python
   # Direction-aware price delta:
   direction_mult = 1.0 if order.side == "BUY" else -1.0
   leg_pnl = half_qty * (order.tp1_price - order.entry_price) * direction_mult
   ```

3. **Unrealized Mark P&L in `mark_unrealized()`:**
   ```python
   direction_mult = 1.0 if order.side == "BUY" else -1.0
   return order.remaining_qty * (mark_price - order.entry_price) * direction_mult
   ```

4. **Auto-Ratchet Stop to Breakeven on TP1:**
   On TP1 fill for a Short, `current_stop` ratchets **down** to `entry_price`.

---

## 4. CC Double-Check & Audit Verification Checklist

Before shipping to Render:
1. **Double-Check Math:** Run the 5 regex test cases in `scratch/test_fixed_regex.py` covering ETH, BTC, SOL, LTC, and VET. Confirm all pass.
2. **Double-Check Short Geometry:** Verify that on a Short, Hard Stop sits *above* Soft Stop, TP1 sits *below* Entry, and PnL increases as price drops.
3. **Run Existing Test Suite:** Verify that all 879 existing tests pass.
4. **Add Dedicated Short Tests in `tests/test_metasignals_paper.py`:**
   - `test_parse_short_alert_geometry()`
   - `test_triage_short_macro_pass_and_veto()`
   - `test_short_order_tp1_fill_and_breakeven_ratchet()`
   - `test_short_order_stop_hit()`
   - `test_comma_separated_prices_parse_eth_and_btc()`
5. **Replay Past Raw Alerts (Optional Post-Deploy Utility):**
   Run a one-time loop over `MetaSignalsRawAlert` where `parsed == False` to parse and populate the missed historical records.
