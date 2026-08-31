"""Deterministic failure classifier."""

from app.classifier.engine import FailureClassifier, UNRECOGNIZED_CATEGORY
from app.classifier.result import ClassificationCertainty, ClassificationResult

__all__ = [
    "ClassificationCertainty",
    "ClassificationResult",
    "FailureClassifier",
    "UNRECOGNIZED_CATEGORY",
]
