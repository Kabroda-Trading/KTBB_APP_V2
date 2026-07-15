"""
Krown Trading Bible (KTB) - Quantitative Indicators Package

Contains core quantitative indicators developed and popularized in Krown Trading:
- BBWP (Bollinger Band Width Percentile) for volatility compression/expansion
- PMARP (Price Moving Average Ratio Percentile) for mean deviation/extension
- RSI & Divergence algorithms
- Moving Average systems & dominant trend evaluation
- Revin Ribbons (R-Squared Suite): Adaptive support/resistance envelopes
- RMO (Revin Momentum Oscillator): Multi-dimensional momentum composite
- RWP (Revin Width Percentile): Volatility regime percentile tracking
"""

from .revin_ribbons import calculate_revin_ribbons, analyze_ribbon_state
from .rmo import calculate_rmo, analyze_rmo_state
from .rwp import calculate_rwp, analyze_rwp_state
from .revin_suite_engine import compute_revin_suite
from .ema_ribbon import calculate_ema_ribbon, analyze_ema_ribbon

