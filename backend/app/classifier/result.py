"""Structured output of the deterministic failure classifier."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.models.payment_event import FailureCategory


class ClassificationCertainty(str, Enum):
    """How strongly the matched rule supports the category."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ClassificationResult(BaseModel):
    """Explainable classification of a failed payment event."""

    category: FailureCategory = Field(
        ...,
        description="Normalized failure category from the project taxonomy",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Deterministic confidence implied by the matched rule",
    )
    certainty: ClassificationCertainty = Field(
        ...,
        description="Coarse certainty band derived from the matched rule",
    )
    reason: str = Field(
        ...,
        description="Human-readable explanation of why this category was chosen",
    )
    rule_id: str = Field(
        ...,
        description="Stable identifier of the rule that fired",
    )
    source_field: str = Field(
        ...,
        description="Primary input field used for the match (error_code, error_description, or none)",
    )
