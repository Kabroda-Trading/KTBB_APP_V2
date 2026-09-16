"""Unit coverage for study_indicators.py -- the byte-identical ports of the
traveler study's rsi_series()/bbwp_series() (lab_exhaustion_conditions.py,
Kabroda AI Brain repo). Hand-computed vectors here lock the formula down
for this repo's own test suite (no dependency on the Brain repo's data
files, which live outside this repo and aren't available in every
environment this suite runs in) -- the byte-identity check against the
study's own real committed data was run separately and reported in
AGENT_LOG.md, not repeated here on every test run.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import study_indicators as si


def test_rsi_series_first_bar_is_none():
    # diff() has no prior bar at index 0 -- pandas' own .diff() semantics.
    result = si.rsi_series([10.0, 12.0], period=2)
    assert result[0] is None


def test_rsi_series_hand_computed_period_2():
    # Hand-verified against the exact pandas recurrence
    # (ewm(alpha=1/period, adjust=False).mean(): y[0]=x[0]; y[t]=y[t-1]+alpha*(x[t]-y[t-1])):
    #   closes = [10, 12, 11, 13], period=2, alpha=0.5
    #   i=1: d=+2 -> up_ewm=2 (first obs), dn_ewm=0 (first obs) -> dn==0 -> RSI None
    #   i=2: d=-1 -> up_ewm=2+0.5*(0-2)=1.0, dn_ewm=0+0.5*(1-0)=0.5 -> rs=2.0 -> RSI=100-100/3=66.6667
    #   i=3: d=+2 -> up_ewm=1.0+0.5*(2-1.0)=1.5, dn_ewm=0.5+0.5*(0-0.5)=0.25 -> rs=6.0 -> RSI=100-100/7=85.7143
    result = si.rsi_series([10.0, 12.0, 11.0, 13.0], period=2)
    assert result[0] is None
    assert result[1] is None
    assert result[2] == pytest.approx(66.66666667, abs=1e-6)
    assert result[3] == pytest.approx(85.71428571, abs=1e-6)


def test_rsi_series_all_up_moves_gives_none_not_fabricated_100():
    # dn_ewm stays exactly 0.0 the whole way -- pandas' dn.replace(0, np.nan)
    # makes this NaN (None here), NOT a fabricated 100.0. This is the exact
    # behavior distinction from a naive "avoid divide by zero -> return 100"
    # implementation, which would silently diverge from the study's real output.
    result = si.rsi_series([10.0, 11.0, 12.0, 13.0, 14.0], period=2)
    assert result[1] is None
    assert result[2] is None
    assert result[3] is None
    assert result[4] is None


def test_rsi_series_too_short_returns_all_none():
    assert si.rsi_series([], period=14) == []
    assert si.rsi_series([5.0], period=14) == [None]


def test_bbwp_series_hand_computed():
    # closes = [1,2,3,4,5,4,3,2,1], period=3, lookback=3.
    # Rolling sample std (ddof=1) at each fully-populated window:
    #   i=2 [1,2,3]->1.0  i=3 [2,3,4]->1.0  i=4 [3,4,5]->1.0
    #   i=5 [4,5,4]->sqrt(1/3)=0.57735...  i=6 [5,4,3]->1.0
    #   i=7 [4,3,2]->1.0  i=8 [3,2,1]->1.0
    # Rolling max of that std series (lookback=3) is only defined once 3 real
    # std values exist in the window, i.e. from i=4 onward, and equals 1.0
    # everywhere here (the 0.577 dip at i=5 never becomes the local max).
    closes = [1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0]
    result = si.bbwp_series(closes, period=3, lookback=3)
    assert result[0] is None and result[1] is None and result[2] is None and result[3] is None
    assert result[4] == pytest.approx(100.0, abs=1e-6)
    assert result[5] == pytest.approx(57.73502692, abs=1e-4)
    assert result[6] == pytest.approx(100.0, abs=1e-6)
    assert result[7] == pytest.approx(100.0, abs=1e-6)
    assert result[8] == pytest.approx(100.0, abs=1e-6)


def test_bbwp_series_too_short_returns_all_none():
    assert si.bbwp_series([1.0, 2.0], period=3, lookback=3) == [None, None]


def test_c5_momentum_decay_true_when_rsi_below_prior():
    rsi = [50.0, 51.0, 52.0, 53.0, 54.0, 55.0, 40.0]  # index 6 < index 0 (6 bars prior)
    assert si.c5_momentum_decay(rsi, 6, lookback_bars=6) is True


def test_c5_momentum_decay_false_when_rsi_rising_or_equal():
    rsi = [50.0, 51.0, 52.0, 53.0, 54.0, 55.0, 56.0]
    assert si.c5_momentum_decay(rsi, 6, lookback_bars=6) is False


def test_c5_momentum_decay_false_before_enough_history():
    rsi = [50.0, 51.0, 52.0]
    assert si.c5_momentum_decay(rsi, 2, lookback_bars=6) is False


def test_c5_momentum_decay_false_on_none_values():
    rsi = [None, 51.0, 52.0, 53.0, 54.0, 55.0, 40.0]
    assert si.c5_momentum_decay(rsi, 6, lookback_bars=6) is False


def test_bbwp_burn_true_above_threshold_and_falling():
    bbwp = [80.0, 75.0]  # 75 > 70 AND falling from 80
    assert si.bbwp_burn(bbwp, 1, threshold=70.0) is True


def test_bbwp_burn_false_above_threshold_but_rising():
    bbwp = [60.0, 75.0]  # 75 > 70 but rising from 60
    assert si.bbwp_burn(bbwp, 1, threshold=70.0) is False


def test_bbwp_burn_false_below_threshold():
    bbwp = [65.0, 60.0]
    assert si.bbwp_burn(bbwp, 1, threshold=70.0) is False


def test_bbwp_burn_false_on_none_values():
    assert si.bbwp_burn([None, 75.0], 1, threshold=70.0) is False
    assert si.bbwp_burn([80.0, None], 1, threshold=70.0) is False
