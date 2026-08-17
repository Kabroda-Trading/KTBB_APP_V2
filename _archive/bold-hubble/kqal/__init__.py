"""
KQAL — Kabroda Quality Assurance Layer

The core scoring engine that measures alignment between Kabroda's actual state
and Krown's quantitative framework. Produces a 0-10 alignment score with
per-dimension breakdown, gap analysis, and actionable recommendations.

Dimensions:
  A. Bias Alignment (0-2 pts)
  B. Strategy Alignment (0-2 pts)
  C. Indicator Alignment (0-2 pts)
  D. Confluence Alignment (0-2 pts)
  E. Execution Alignment (0-2 pts)
"""

from .alignment_engine import (
    compute_alignment_score,
    score_bias_alignment,
    score_strategy_alignment,
    score_indicator_alignment,
    score_confluence_alignment,
    score_execution_alignment,
    identify_gaps,
    generate_recommendations,
    AlignmentReport,
)

__all__ = [
    "compute_alignment_score",
    "score_bias_alignment",
    "score_strategy_alignment",
    "score_indicator_alignment",
    "score_confluence_alignment",
    "score_execution_alignment",
    "identify_gaps",
    "generate_recommendations",
    "AlignmentReport",
]
