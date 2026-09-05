"""AI-assisted revenue-risk detection and recovery recommendations.

Recommendations are advisory data. They never authorize execution; the
deterministic policy engine remains the sole authority.
"""

from app.recommendation.engine import RecoveryRecommender
from app.recommendation.result import RecoveryRecommendation

__all__ = ["RecoveryRecommendation", "RecoveryRecommender"]
