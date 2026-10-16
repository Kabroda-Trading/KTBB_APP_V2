# CC_WORK_ORDER_METASIGNALS_BBWP_EXHAUSTION.md — BBWP Volatility Compression Early Exit

**Target:** `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2` (`kabroda.com`)  
**Origin:** `Kabroda Meta Signals AI` (DeepSeek / Antigravity session 2026-10-16)  
**Status:** READY FOR IMPLEMENTATION & TESTING  
**Reference Artifacts:** `Kabroda Meta Signals AI/studies/exhaustion_exit_backtest/REPORT.md` (Commit `825c26a`)

---

## 1. Executive Summary & Empirical Findings (Study 6)

In response to Andy's inquiry regarding momentum-decay exits (analogous to Bitcoin Traveler's C5/BBWP stack in `mgmt_e1_stack.py`), Study 6 backtested all 343 approved trades from Study 5 (2024–2026, 250 Longs, 93 Shorts).

### The Key Empirical Results:
1. **RSI Momentum Decay (C5) Fails on Altcoins:**
   - Exiting on RSI decay (RSI lower than 6 bars ago) caused severe false alarms on altcoins, choking off **63 winning trades** and dropping win rate from **54.5% down to 42.9%**.
   - **Ruling: Do NOT implement RSI decay (C5).**
2. **BBWP Volatility Compression (`BBWP <= 10.0`) Is a Verified Edge on BOTH Exit Engines:**
   - **On the Currently Deployed Engine (Card TP1 + Card TP2):**
     - Deployed Base: **+12.12 R**, PF 1.07, Max DD 20.0 R
     - Deployed + BBWP <= 10: **+13.71 R**, PF 1.10, Max DD 17.2 R (**+1.59 R / +13% gain**, cuts Max DD by 2.8 R)
   - **On the 21 EMA Runner Engine (Card TP1 + 21 EMA Runner):**
     - Runner Base: **+18.03 R**, PF 1.11, Max DD 21.9 R
     - Runner + BBWP <= 10: **+26.87 R**, PF 1.20, Max DD 19.7 R (**+8.84 R / +49% gain**, cuts Max DD by 2.2 R)
   - In both cases, BBWP compression before TP1 cuts average loss on stopped trades from **-1.04 R down to -0.83 R**, saving **35 trades** from full hard stops.

---

## 2. Technical Specification & Implementation Details

### A. The BBWP Indicator Algorithm
Add to `market_data.py` (or as a helper in `metasignals_paper_engine.py`):
```python
def calculate_bbwp(close_prices: List[float], bb_period: int = 13, bb_std: float = 2.0, lookback: int = 252) -> Optional[float]:
    """Calculates Krown's Bollinger Band Width Percentile (BBWP) for the latest bar.
    BBW = (Upper Band - Lower Band) / Middle Band (SMA 13, 2.0 std).
    BBWP = percentile rank of current BBW over the past `lookback` bars.
    Returns value between 0.0 and 100.0, or None if insufficient history (< 20 bars).
    """
    n = len(close_prices)
    if n < bb_period:
        return None
    
    # Calculate SMA and StDev
    bbw = []
    for i in range(bb_period - 1, n):
        window = close_prices[i - bb_period + 1 : i + 1]
        sma = sum(window) / bb_period
        variance = sum((x - sma) ** 2 for x in window) / bb_period
        stdev = math.sqrt(variance)
        if sma > 0:
            val = (2.0 * bb_std * stdev) / sma
            bbw.append(val)
        else:
            bbw.append(None)
            
    valid_bbw = [x for x in bbw if x is not None]
    if len(valid_bbw) < 20:
        return None
        
    start_idx = max(0, len(valid_bbw) - lookback)
    hist = valid_bbw[start_idx:]
    curr_val = hist[-1]
    smaller_count = sum(1 for x in hist if x < curr_val)
    return round((smaller_count / len(hist)) * 100.0, 2)
```

### B. Pre-TP1 Early Exit in `metasignals_paper_engine.py`
In `advance_order(db: Session, order: MetaSignalsPaperOrder, mark_price: float, bbwp_val: Optional[float] = None, now: Optional[datetime.datetime] = None)`:

```python
    if order.state == "OPEN":
        # 1. Hard Stop Check (Orderbook Priority 1)
        stop_hit = (mark_price <= order.current_stop) if is_buy else (mark_price >= order.current_stop)
        if stop_hit:
            _close_full(db, account, order, order.current_stop, "STOP", TAKER_FEE_RATE, now)
            return "CLOSED_STOP"

        # 2. BBWP Dead-Tape Compression Exit (Priority 2)
        # If volatility compresses to extreme bottom 10th percentile before TP1,
        # momentum has died; exit at market to avoid a full -1.04R flush.
        if bbwp_val is not None and bbwp_val <= 10.0:
            _close_full(db, account, order, mark_price, "EXHAUSTION_BBWP", TAKER_FEE_RATE, now)
            return "CLOSED_EXHAUSTION_BBWP"

        # 3. TP1 Hit Check (Priority 3)
        tp1_hit = (mark_price >= order.tp1_price) if is_buy else (mark_price <= order.tp1_price)
        if tp1_hit:
            _fill_tp1(db, account, order, now)
            return "TP1_FILL"
        return None
```

### C. Integrating with `metasignals_watcher.py`
In `metasignals_watcher.py`:
- For each open order in state `OPEN`:
  - When the native timeframe bar closes (or checked every few minutes against Bitunix confirmed candles), calculate `bbwp_val`.
  - Pass `bbwp_val` into `advance_order(db, order_row, price, bbwp_val=bbwp_val)`.
  - If `bbwp_val <= 10.0`, order closes with `CLOSED_EXHAUSTION_BBWP`.

---

## 3. Scope & Constraints
- **State `OPEN` Only:** The BBWP compression exit applies **exclusively to state `OPEN`** (before TP1). Once TP1 is banked and the stop is at Breakeven (`HALF_CLOSED_BE`), the trade is already risk-free and should run until Breakeven or TP2 / 21 EMA.
- **Fail Closed / Safe Defaults:** If candle data for BBWP is temporarily unavailable, `bbwp_val` is `None` and the order continues monitoring normally.
- **Test Coverage:** Add dedicated tests in `tests/test_metasignals_paper.py` verifying that:
  1. `bbwp_val <= 10.0` in state `OPEN` triggers `CLOSED_EXHAUSTION_BBWP`.
  2. `bbwp_val > 10.0` proceeds normally.
  3. `bbwp_val <= 10.0` in state `HALF_CLOSED_BE` is ignored (preserving runners).
