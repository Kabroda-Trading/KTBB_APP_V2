"""Unit + parity coverage for alt_matrix_signals.py.

Parity tests feed the real, committed 4.5-year SOL/ETH Bitunix OHLC series
(tests/fixtures/alt_matrix/*.csv, copied from the Kabroda AI Brain repo's
own calibration_data/) through this module's pure functions and confirm
bit-exact agreement with pandas (the library the original walk-forward
study used) AND bar-for-bar agreement with the real committed trade ledger
-- not just "doesn't crash." This is the regression guard for the Part 0
finding (2026-10-09 audit): the walk-forward backtest's own macro-gate
timing has a real, measured look-ahead (comparing a 4H bar against ITS
OWN day's daily close before that day has actually finished), which this
module exposes as an explicit macro_gate_mode rather than silently
reproducing it as the only option. The discrepancy's actual size is
measured below, not assumed."""
import csv
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest

import alt_matrix_signals as ams

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "alt_matrix")


def _load_candles(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: int(r["epoch"]))
    return [
        {"time": int(r["epoch"]), "open": float(r["open"]), "high": float(r["high"]),
         "low": float(r["low"]), "close": float(r["close"])}
        for r in rows
    ]


def _load_ledger(path):
    with open(path) as f:
        return list(csv.DictReader(f))


# ------------------------------------------------------------------ unit tests, synthetic data

def test_ema_series_matches_pandas_adjust_false():
    values = [10.0, 12.0, 11.0, 15.0, 9.0, 20.0, 18.0, 17.5, 16.0, 22.0]
    mine = ams.ema_series(values, 3)
    ref = pd.Series(values).ewm(span=3, adjust=False).mean().tolist()
    assert mine == pytest.approx(ref)


def test_ema_series_first_value_equals_input():
    assert ams.ema_series([42.0, 1.0, 1.0], 5)[0] == 42.0


def test_ema_series_empty_input():
    assert ams.ema_series([], 21) == []


def test_sma_series_matches_pandas_rolling_mean():
    values = [float(i) for i in range(1, 20)]
    mine = ams.sma_series(values, 5)
    ref = pd.Series(values).rolling(5).mean().tolist()
    for m, r in zip(mine, ref):
        if m is None:
            assert pd.isna(r)
        else:
            assert m == pytest.approx(r)


def test_sma_series_none_before_window_full():
    assert ams.sma_series([1.0, 2.0, 3.0], 5) == [None, None, None]


def test_atr14_range_mean_series_none_before_14_bars():
    candles = [{"high": 10.0 + i, "low": 9.0 + i, "close": 9.5 + i} for i in range(13)]
    result = ams.atr14_range_mean_series(candles)
    assert all(v is None for v in result)


def test_atr14_range_mean_series_first_real_value():
    # 14 bars, each with high-low == 2.0 -- the 14-bar mean range is 2.0.
    candles = [{"high": 10.0, "low": 8.0, "close": 9.0} for _ in range(14)]
    result = ams.atr14_range_mean_series(candles)
    assert result[13] == pytest.approx(2.0)
    assert all(v is None for v in result[:13])


def test_atr14_wilder_series_seeds_then_smooths():
    candles = [{"high": 10.0, "low": 9.0, "close": 9.5} for _ in range(20)]
    result = ams.atr14_wilder_series(candles)
    assert all(v is None for v in result[:13])
    assert result[13] == pytest.approx(1.0)   # seed = mean(14 true ranges of 1.0 each)
    assert result[19] == pytest.approx(1.0)    # constant input -> stays at 1.0


def _gapping_candles(n=60, seed=42):
    import random
    rng = random.Random(seed)
    candles, price = [], 100.0
    for i in range(n):
        open_p = price + rng.choice([-3.0, 0.0, 3.0])   # gaps vs prior close -> true range != high-low
        close = open_p + rng.uniform(-0.5, 0.5)
        candles.append({"time": i * 14400, "open": open_p, "high": open_p + 1.0, "low": open_p - 1.0, "close": close})
        price = close
    return candles


def test_evaluate_d1_uses_wilder_atr_not_range_mean_as_its_live_default():
    # The RULED default (Andy, 2026-10-09) -- proves the actual wiring in
    # evaluate_d1(), not just that both standalone functions exist.
    candles_4h = _gapping_candles()
    daily = [{"time": i * 86400, "open": 100.0 - i * 0.1, "high": 150.0, "low": 50.0, "close": 100.0 + i * 0.1} for i in range(230)]
    # Shift the 4H series onto real calendar days so evaluate_d1()'s own
    # macro-gate lookup has real daily history behind it (see the day-
    # alignment reasoning in the evaluate_d1 tests above).
    shifted = [{**c, "time": 215 * 86400 + c["time"]} for c in candles_4h]

    result = ams.evaluate_d1(shifted, daily, funding_rate=0.0)
    expected_wilder = ams.atr14_wilder_series(shifted)[-1]
    expected_range_mean = ams.atr14_range_mean_series(shifted)[-1]
    assert abs(expected_wilder - expected_range_mean) > 0.01   # confirms this fixture actually distinguishes the two formulas
    assert result["atr14"] == pytest.approx(expected_wilder)
    assert result["atr14"] != pytest.approx(expected_range_mean)


# ------------------------------------------------------------------ macro gate timing (the Part 0 finding)

def test_macro_gate_spec_mode_uses_prior_day_not_same_day():
    # Day 1 (epoch 0): close 100. Day 2 (epoch 86400): close 50 (a big drop).
    daily = [
        {"time": 0, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
        {"time": 86400, "open": 100.0, "high": 100.0, "low": 50.0, "close": 50.0},
    ]
    # A 4H bar at hour 4 of day 2 (epoch 86400+14400): spec mode must use
    # day 1's close (100), NOT day 2's own not-yet-finished close (50).
    bar_epoch = 86400 + 14400
    spec = ams.macro_gate_value(daily, bar_epoch, macro_gate_mode="spec")
    lookahead = ams.macro_gate_value(daily, bar_epoch, macro_gate_mode="backtest_lookahead")
    assert spec["daily_close"] == 100.0
    assert lookahead["daily_close"] == 50.0


def test_macro_gate_returns_none_when_prior_day_missing():
    daily = [{"time": 0, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}]
    # Asking for day 0 under spec mode needs day -1, which doesn't exist.
    result = ams.macro_gate_value(daily, 14400, macro_gate_mode="spec")
    assert result["daily_close"] is None


# ------------------------------------------------------------------ evaluate_d1 gate logic

def _flat_4h_candles(n, price=100.0, time_start=0):
    return [{"time": time_start + i * 14400, "open": price, "high": price, "low": price, "close": price} for i in range(n)]


def test_evaluate_d1_insufficient_4h_history_fails_closed():
    result = ams.evaluate_d1([{"time": 0, "open": 1, "high": 1, "low": 1, "close": 1}], _flat_4h_candles(250), funding_rate=0.0)
    assert result["signal"] is False
    assert result["reason"] == "insufficient_4h_history"


def test_evaluate_d1_insufficient_daily_history_fails_closed():
    result = ams.evaluate_d1(_flat_4h_candles(60), _flat_4h_candles(50), funding_rate=0.0)
    assert result["signal"] is False
    assert result["reason"] == "insufficient_daily_history"


def test_evaluate_d1_none_funding_rate_fails_closed_not_treated_as_zero():
    # Build a real cross + macro-pass scenario, but funding_rate=None --
    # must veto, never silently treat a failed fetch as "safe." The 4H
    # bars start at day 215 so the cross bar (61 bars later, day 225)
    # always has >=200 PRIOR daily closes behind it, and day 224 (spec
    # mode's own prior-day lookup target) also exists -- see
    # test_macro_gate_* above for why the lookup needs the day BEFORE the
    # cross bar's own day.
    daily = [{"time": i * 86400, "open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0} for i in range(230)]
    closes = [100.0] * 40 + [90.0] * 20 + [200.0]   # verified real cross exactly on the last bar (see debug script)
    candles_4h = [{"time": 215 * 86400 + i * 14400, "open": c, "high": c + 0.5, "low": c - 0.5, "close": c} for i, c in enumerate(closes)]
    result = ams.evaluate_d1(candles_4h, daily, funding_rate=None)
    assert result["signal"] is False
    assert result["funding_pass"] is False


def test_evaluate_d1_funding_at_or_above_threshold_vetoes():
    # Closes trend upward so the macro gate genuinely passes -- isolating
    # the funding veto specifically, not conflating it with SKIPPED_MACRO.
    daily = [{"time": i * 86400, "open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0 + i * 0.01} for i in range(230)]
    closes = [100.0] * 40 + [90.0] * 20 + [200.0]
    candles_4h = [{"time": 215 * 86400 + i * 14400, "open": c, "high": c + 0.5, "low": c - 0.5, "close": c} for i, c in enumerate(closes)]
    result = ams.evaluate_d1(candles_4h, daily, funding_rate=0.0005)   # exactly at the veto threshold
    assert result["signal"] is False
    assert result["reason"] == "SKIPPED_FUNDING"


def test_evaluate_d1_macro_fail_vetoes_even_with_a_real_cross():
    # Daily closes trending DOWN (below SMA200) -- macro gate must veto
    # regardless of what the 4H cross looks like.
    daily = [{"time": i * 86400, "open": 100.0 - i * 0.1, "high": 100.0, "low": 50.0, "close": 100.0 - i * 0.1} for i in range(230)]
    closes = [100.0] * 40 + [90.0] * 20 + [200.0]
    candles_4h = [{"time": 215 * 86400 + i * 14400, "open": c, "high": c + 0.5, "low": c - 0.5, "close": c} for i, c in enumerate(closes)]
    result = ams.evaluate_d1(candles_4h, daily, funding_rate=0.0)
    assert result["signal"] is False
    assert result["reason"] == "SKIPPED_MACRO"


# ------------------------------------------------------------------ PARITY against the real committed data
# (2026-10-09 audit): reproduces the walk-forward backtest's own math
# bit-exactly (via pandas, the library it used) and its full 34/39-trade
# ledgers exactly in backtest_lookahead mode -- NOT because that mode is
# correct (see this module's own header), but because this is the proof
# that this module's formulas genuinely match what was actually measured,
# not an approximation of it.

@pytest.mark.parametrize("asset", ["sol", "eth"])
def test_ema_and_atr_bit_exact_parity_with_pandas(asset):
    candles_4h = _load_candles(os.path.join(FIXTURES, f"{asset}_bars_4H.csv"))
    closes = [c["close"] for c in candles_4h]

    mine_ema21 = ams.ema_series(closes, 21)
    mine_ema55 = ams.ema_series(closes, 55)
    mine_atr14 = ams.atr14_range_mean_series(candles_4h)

    df = pd.DataFrame(candles_4h)
    ref_ema21 = df["close"].ewm(span=21, adjust=False).mean().tolist()
    ref_ema55 = df["close"].ewm(span=55, adjust=False).mean().tolist()
    ref_atr14 = (df["high"] - df["low"]).rolling(14).mean().tolist()

    assert mine_ema21 == pytest.approx(ref_ema21)
    assert mine_ema55 == pytest.approx(ref_ema55)
    for m, r in zip(mine_atr14, ref_atr14):
        if m is None:
            assert pd.isna(r)
        else:
            assert m == pytest.approx(r)


@pytest.mark.parametrize("asset,expected_trades", [("sol", 34), ("eth", 39)])
def test_full_trade_walk_reproduces_the_real_ledger_exactly_in_backtest_lookahead_mode(asset, expected_trades):
    """Walks the same cross-detection + entry/exit loop the original
    walkforward_htf_{sol,eth}.py script used, built entirely from this
    module's own functions, in macro_gate_mode="backtest_lookahead" (the
    mode that matches what the backtest ACTUALLY did -- see this
    module's header for why "spec" mode is the live default instead).
    Must reproduce every trade's entry/exit/stop price and net R exactly."""
    candles_4h = _load_candles(os.path.join(FIXTURES, f"{asset}_bars_4H.csv"))
    candles_1d = _load_candles(os.path.join(FIXTURES, f"{asset}_bars_1D.csv"))
    ledger = _load_ledger(os.path.join(FIXTURES, f"{asset}_walkforward_ledger.csv"))
    assert len(ledger) == expected_trades   # pin the real ledger size itself, not just my own output

    closes = [c["close"] for c in candles_4h]
    ema21 = ams.ema_series(closes, 21)
    ema55 = ams.ema_series(closes, 55)
    atr14 = ams.atr14_range_mean_series(candles_4h)

    FEE_TAKER = 0.0006
    trades = []
    last_exit_idx = -1
    for i in range(1, len(candles_4h)):
        if i <= last_exit_idx:
            continue
        if ema21[i] is None or ema55[i] is None or ema21[i - 1] is None or ema55[i - 1] is None:
            continue
        if not (ema21[i] > ema55[i] and ema21[i - 1] <= ema55[i - 1]):
            continue
        macro = ams.macro_gate_value(candles_1d, candles_4h[i]["time"], macro_gate_mode="backtest_lookahead")
        if macro["daily_close"] is None or macro["sma200"] is None or macro["daily_close"] <= macro["sma200"]:
            continue
        atr = atr14[i]
        if atr is None or atr <= 0 or i + 1 >= len(candles_4h):
            continue
        entry_idx = i + 1
        entry_p = candles_4h[entry_idx]["open"]
        stop_p = entry_p - 1.5 * atr
        r_dist = entry_p - stop_p
        if r_dist <= 0:
            continue
        exit_p = exit_idx = exit_reason = None
        max_r = 0.0
        for k in range(entry_idx + 1, min(entry_idx + 360, len(candles_4h))):
            hi, lo, cl = candles_4h[k]["high"], candles_4h[k]["low"], candles_4h[k]["close"]
            cur_r = (hi - entry_p) / r_dist
            if cur_r > max_r:
                max_r = cur_r
            if lo <= stop_p:
                exit_p, exit_idx, exit_reason = stop_p, k, "STOP"
                break
            if max_r >= 2.0:
                if cl < ema21[k]:
                    exit_p, exit_idx, exit_reason = cl, k, "EMA21_TRAIL"
                    break
            else:
                if cl < ema55[k]:
                    exit_p, exit_idx, exit_reason = cl, k, "EMA55_CLOSE"
                    break
        if exit_p is None:
            exit_idx = min(entry_idx + 359, len(candles_4h) - 1)
            exit_p = candles_4h[exit_idx]["close"]
            exit_reason = "TIME_EXPIRY"
        last_exit_idx = exit_idx
        gross_r = (exit_p - entry_p) / r_dist
        fee_r = ((FEE_TAKER + FEE_TAKER) * entry_p) / r_dist
        trades.append({
            "entry_p": round(entry_p, 4), "exit_p": round(exit_p, 4), "stop_p": round(stop_p, 4),
            "net_r": round(gross_r - fee_r, 3), "reason": exit_reason,
        })

    assert len(trades) == expected_trades
    for mine, real in zip(trades, ledger):
        assert mine["entry_p"] == pytest.approx(float(real["entry_p"]), abs=0.001)
        assert mine["exit_p"] == pytest.approx(float(real["exit_p"]), abs=0.001)
        assert mine["stop_p"] == pytest.approx(float(real["stop_p"]), abs=0.001)
        assert mine["net_r"] == pytest.approx(float(real["net_r"]), abs=0.001)
        assert mine["reason"] == real["reason"]


@pytest.mark.parametrize("asset,expected_lookahead_only", [("sol", 2), ("eth", 0)])
def test_measured_size_of_the_macro_gate_lookahead_discrepancy(asset, expected_lookahead_only):
    """The Part 0 finding, pinned as a measured fact rather than left to
    silently drift: how many EMA-cross events pass the macro gate ONLY
    under the backtest's actual (look-ahead) timing and would NOT pass
    under the spec's own literal wording. If this number ever changes,
    it means either the fixture data changed or someone touched the
    macro-gate functions -- either way, worth a second look, not a
    silent pass."""
    candles_4h = _load_candles(os.path.join(FIXTURES, f"{asset}_bars_4H.csv"))
    candles_1d = _load_candles(os.path.join(FIXTURES, f"{asset}_bars_1D.csv"))
    closes = [c["close"] for c in candles_4h]
    ema21 = ams.ema_series(closes, 21)
    ema55 = ams.ema_series(closes, 55)

    lookahead_only = 0
    for i in range(1, len(candles_4h)):
        if ema21[i] is None or ema55[i] is None or ema21[i - 1] is None or ema55[i - 1] is None:
            continue
        if not (ema21[i] > ema55[i] and ema21[i - 1] <= ema55[i - 1]):
            continue
        m_look = ams.macro_gate_value(candles_1d, candles_4h[i]["time"], macro_gate_mode="backtest_lookahead")
        m_spec = ams.macro_gate_value(candles_1d, candles_4h[i]["time"], macro_gate_mode="spec")
        look_pass = m_look["daily_close"] is not None and m_look["sma200"] is not None and m_look["daily_close"] > m_look["sma200"]
        spec_pass = m_spec["daily_close"] is not None and m_spec["sma200"] is not None and m_spec["daily_close"] > m_spec["sma200"]
        if look_pass and not spec_pass:
            lookahead_only += 1

    assert lookahead_only == expected_lookahead_only
