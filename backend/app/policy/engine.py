"""Deterministic recovery policy engine.

The policy engine is the sole authority for recovery decisions.
It receives a payment event and a classification result, and returns
a structured policy decision.

It does NOT:
- execute the action
- call Razorpay
- call NIM / Nemotron
- mutate the payment event
- use randomness

Decision Policy Table (Section 4.3 of the implementation plan):

| Root Cause              | Action                        | Limit           | Escalation               |
|-------------------------|-------------------------------|-----------------|---------------------------|
| Insufficient funds      | Retry after 24h               | max 2 retries   | 3rd failure → escalate    |
| Expired/paused mandate  | Trigger re-authorization      | 1 attempt       | no response → escalate    |
| Bank/gateway timeout    | Immediate retry once          | 1 retry         | fails again → escalate    |
| Card declined           | Switch payment method         | 1 switch        | no alt → escalate         |
| Auth/OTP failure        | Resend auth prompt            | 1 resend        | fails again → escalate    |
| Unknown                 | No auto action                | 0               | always escalate           |

Global stopping rules:
- Hard cap: no more than 3 total automated attempts per transaction, ever.
- Amount cap: auto-recovery disabled above configured ₹ threshold.
- Every escalation and stop is logged with a reason string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.classifier.result import ClassificationResult
from app.models.payment_event import FailedTransactionEvent, FailureCategory
from app.policy.result import (
    EscalationReason,
    PolicyAction,
    PolicyDecision,
    RecommendationStatus,
)
from app.recommendation.result import RecoveryRecommendation

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Global hard cap: no more than 3 total automated attempts per transaction.
GLOBAL_MAX_ATTEMPTS = 3

# Default amount limit if none provided (from config: 500000 paise = ₹5,000)
DEFAULT_AMOUNT_LIMIT = 500000


# ---------------------------------------------------------------------------
# Per-category policy rules
# ---------------------------------------------------------------------------


# How far the advisor may move a retry from the category default. A cooldown
# exists because retrying too soon fails — halving it is the most impatience
# the rule tolerates, and tripling it the most delay, beyond which a customer
# has moved on. Stated once here rather than per category so the bound is one
# rule to audit instead of seven.
_COOLDOWN_MIN_FACTOR = 0.5
_COOLDOWN_MAX_FACTOR = 3.0


@dataclass(frozen=True)
class _CategoryPolicy:
    """Policy definition for a single failure category."""

    category: FailureCategory
    action: PolicyAction
    max_retries: int
    rule_id: str
    reason_template: str
    # How long to wait before the authorized action may run. 0 means run now.
    cooldown_seconds: int = 0
    # Equally-authorised alternatives for the same cause. The advisor may
    # choose among these; it can never widen the set.
    alternatives: tuple[PolicyAction, ...] = ()


_CATEGORY_POLICIES: dict[FailureCategory, _CategoryPolicy] = {
    FailureCategory.INSUFFICIENT_FUNDS: _CategoryPolicy(
        category=FailureCategory.INSUFFICIENT_FUNDS,
        action=PolicyAction.SCHEDULED_RETRY,
        max_retries=2,
        cooldown_seconds=86_400,
        rule_id="policy.insufficient_funds.retry_24h",
        reason_template=(
            "Insufficient funds: retry after 24h cooldown permitted "
            "(attempt {attempt}/{max_retries})"
        ),
    ),
    FailureCategory.EXPIRED_CARD: _CategoryPolicy(
        category=FailureCategory.EXPIRED_CARD,
        action=PolicyAction.TRIGGER_REAUTHORIZATION,
        max_retries=1,
        rule_id="policy.expired_card.reauthorize",
        reason_template=(
            "Expired card/mandate: trigger re-authorization request "
            "(attempt {attempt}/{max_retries})"
        ),
    ),
    FailureCategory.NETWORK_ERROR: _CategoryPolicy(
        category=FailureCategory.NETWORK_ERROR,
        action=PolicyAction.IMMEDIATE_RETRY,
        max_retries=1,
        rule_id="policy.network_error.immediate_retry",
        alternatives=(PolicyAction.SCHEDULED_RETRY,),
        reason_template=(
            "Network/gateway error: immediate retry once "
            "(attempt {attempt}/{max_retries})"
        ),
    ),
    FailureCategory.BANK_DECLINE: _CategoryPolicy(
        category=FailureCategory.BANK_DECLINE,
        action=PolicyAction.SWITCH_PAYMENT_METHOD,
        max_retries=1,
        rule_id="policy.bank_decline.switch_method",
        alternatives=(PolicyAction.SCHEDULED_RETRY,),
        reason_template=(
            "Card declined by issuer: switch to alternate payment method "
            "(attempt {attempt}/{max_retries})"
        ),
    ),
    FailureCategory.AUTHENTICATION_FAILURE: _CategoryPolicy(
        category=FailureCategory.AUTHENTICATION_FAILURE,
        action=PolicyAction.RESEND_AUTH_PROMPT,
        max_retries=1,
        rule_id="policy.auth_failure.resend_prompt",
        alternatives=(PolicyAction.SCHEDULED_RETRY,),
        reason_template=(
            "Authentication/OTP failure: resend authentication prompt "
            "(attempt {attempt}/{max_retries})"
        ),
    ),
    FailureCategory.OVERDUE_RECEIVABLE: _CategoryPolicy(
        category=FailureCategory.OVERDUE_RECEIVABLE,
        action=PolicyAction.SEND_PAYMENT_REMINDER,
        max_retries=3,
        # Chasing an invoice daily is harassment; 72h between reminders.
        cooldown_seconds=259_200,
        rule_id="policy.overdue_receivable.reminder_sequence",
        reason_template=(
            "Overdue receivable: send a payment reminder after a 72h cooldown "
            "(reminder {attempt}/{max_retries})"
        ),
    ),
    FailureCategory.UNKNOWN: _CategoryPolicy(
        category=FailureCategory.UNKNOWN,
        action=PolicyAction.NO_ACTION,
        max_retries=0,
        rule_id="policy.unknown.escalate",
        reason_template="Unknown failure: no automatic action permitted",
    ),
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class RecoveryPolicyEngine:
    """Deterministic, bounded recovery policy engine.

    The engine evaluates a payment event and its classification to produce
    a policy decision. It never executes actions, calls APIs, or uses
    randomness.

    Args:
        amount_limit: Auto-recovery disabled above this amount (paise).
            Defaults to the value from Settings.auto_recovery_amount_limit.
    """

    def __init__(self, amount_limit: int | None = None) -> None:
        # Honor the configured limit (AUTO_RECOVERY_AMOUNT_LIMIT) when the
        # caller does not pass one explicitly, instead of a hardcoded constant.
        if amount_limit is not None:
            self._amount_limit = amount_limit
        else:
            self._amount_limit = settings.auto_recovery_amount_limit

    @property
    def amount_limit(self) -> int:
        return self._amount_limit

    def evaluate(
        self,
        payment_event: FailedTransactionEvent,
        classification: ClassificationResult | None,
        recommendation: RecoveryRecommendation | None = None,
    ) -> PolicyDecision:
        """Evaluate a payment event against recovery policy.

        Args:
            payment_event: The failed payment transaction event.
            classification: The classifier's result, or None if missing.

        Returns:
            A PolicyDecision with the prescribed action. The optional model
            recommendation is only evaluated as an untrusted candidate.

        The original payment_event is never mutated.
        """
        amount = payment_event.amount
        attempt = payment_event.attempt_number

        # --- Guard: missing classification ---
        if classification is None:
            return self._escalate(
                category=FailureCategory.UNKNOWN,
                reason="Classification is missing; cannot make a policy decision",
                rule_id="policy.guard.missing_classification",
                escalation_reason=EscalationReason.MISSING_CLASSIFICATION,
                attempt=attempt,
                amount=amount,
                max_retries=0,
            )

        category = classification.category

        # --- Guard: invalid / unrecognized category ---
        if category not in _CATEGORY_POLICIES:
            return self._escalate(
                category=category,
                reason=(
                    f"Failure category '{category.value}' has no matching policy rule; "
                    "escalating as a safety measure"
                ),
                rule_id="policy.guard.invalid_classification",
                escalation_reason=EscalationReason.INVALID_CLASSIFICATION,
                attempt=attempt,
                amount=amount,
                max_retries=0,
            )

        cat_policy = _CATEGORY_POLICIES[category]

        # --- Global stopping rule: amount cap ---
        if amount <= 0 or amount > self._amount_limit:
            return self._escalate(
                category=category,
                reason=(
                    f"Transaction amount ₹{amount / 100:,.2f} is invalid or exceeds the automatic recovery "
                    f"limit of ₹{self._amount_limit / 100:,.2f}; escalating to manual review"
                ),
                rule_id="policy.guard.amount_cap",
                escalation_reason=EscalationReason.AMOUNT_EXCEEDS_LIMIT,
                attempt=attempt,
                amount=amount,
                max_retries=cat_policy.max_retries,
            )

        # --- Global stopping rule: hard cap on attempts ---
        if attempt <= 0 or attempt >= GLOBAL_MAX_ATTEMPTS:
            return self._escalate(
                category=category,
                reason=(
                    f"Transaction attempt {attempt} is invalid or has reached the global hard cap of "
                    f"{GLOBAL_MAX_ATTEMPTS} attempts; no further automatic recovery"
                ),
                rule_id="policy.guard.global_attempt_cap",
                escalation_reason=EscalationReason.GLOBAL_ATTEMPT_CAP,
                attempt=attempt,
                amount=amount,
                max_retries=cat_policy.max_retries,
            )

        # --- Unknown always escalates ---
        if category == FailureCategory.UNKNOWN:
            return self._escalate(
                category=category,
                reason=cat_policy.reason_template,
                rule_id=cat_policy.rule_id,
                escalation_reason=EscalationReason.UNKNOWN_FAILURE,
                attempt=attempt,
                amount=amount,
                max_retries=cat_policy.max_retries,
            )

        # --- Per-category retry limit ---
        if attempt > cat_policy.max_retries:
            return self._escalate(
                category=category,
                reason=(
                    f"Retry limit exhausted for {category.value}: "
                    f"attempt {attempt} exceeds maximum {cat_policy.max_retries} retries"
                ),
                rule_id=f"policy.{category.value}.retry_limit_exhausted",
                escalation_reason=EscalationReason.RETRY_LIMIT_EXHAUSTED,
                attempt=attempt,
                amount=amount,
                max_retries=cat_policy.max_retries,
            )

        # --- Allowed: return the prescribed recovery action ---
        reason = cat_policy.reason_template.format(
            attempt=attempt, max_retries=cat_policy.max_retries
        )
        return PolicyDecision(
            action=cat_policy.action,
            automatic_recovery_allowed=True,
            reason=reason,
            rule_id=cat_policy.rule_id,
            failure_category=category,
            escalation_required=False,
            escalation_reason=None,
            max_retries_for_category=cat_policy.max_retries,
            current_attempt=attempt,
            amount=amount,
            cooldown_seconds=cat_policy.cooldown_seconds,
            cooldown_min_seconds=int(
                cat_policy.cooldown_seconds * _COOLDOWN_MIN_FACTOR
            ),
            cooldown_max_seconds=int(
                cat_policy.cooldown_seconds * _COOLDOWN_MAX_FACTOR
            ),
            permitted_actions=[cat_policy.action, *cat_policy.alternatives],
            amount_limit=self._amount_limit,
            recommendation_status=self._recommendation_status(
                recommendation, category, cat_policy
            )[0],
            recommendation_reason=self._recommendation_status(
                recommendation, category, cat_policy
            )[1],
        )

    # --- Internal helpers ---

    def _escalate(
        self,
        *,
        category: FailureCategory,
        reason: str,
        rule_id: str,
        escalation_reason: EscalationReason,
        attempt: int,
        amount: int,
        max_retries: int,
    ) -> PolicyDecision:
        """Build an escalation decision. All escalations route here."""
        return PolicyDecision(
            action=PolicyAction.ESCALATE,
            automatic_recovery_allowed=False,
            reason=reason,
            rule_id=rule_id,
            failure_category=category,
            escalation_required=True,
            escalation_reason=escalation_reason,
            max_retries_for_category=max_retries,
            current_attempt=attempt,
            amount=amount,
            amount_limit=self._amount_limit,
        )

    @staticmethod
    def permitted_actions_for(
        category: FailureCategory | None,
    ) -> tuple[PolicyAction, ...]:
        """The actions this cause could be authorised to take.

        Published so the advisor can be asked to choose from the real menu
        instead of the whole enum. This narrows what the model may propose;
        it never widens what policy will allow. The authoritative check still
        happens in ``evaluate`` — this is a hint, not a grant.
        """
        if category is None or category not in _CATEGORY_POLICIES:
            return ()
        cat_policy = _CATEGORY_POLICIES[category]
        return (cat_policy.action, *cat_policy.alternatives)

    @staticmethod
    def cooldown_window_for(category: FailureCategory | None) -> dict[str, int]:
        """The wait this cause tolerates, published for the advisor.

        Empty when the cause has no cooldown, which is how the advisor is told
        there is no timing decision to make here.
        """
        if category is None or category not in _CATEGORY_POLICIES:
            return {}
        default = _CATEGORY_POLICIES[category].cooldown_seconds
        if default <= 0:
            return {}
        return {
            "default_seconds": default,
            "min_seconds": int(default * _COOLDOWN_MIN_FACTOR),
            "max_seconds": int(default * _COOLDOWN_MAX_FACTOR),
        }

    @staticmethod
    def _recommendation_status(
        recommendation: RecoveryRecommendation | None,
        category: FailureCategory,
        category_policy: _CategoryPolicy,
    ) -> tuple[RecommendationStatus, str]:
        """Classify an advisory recommendation without changing policy output."""
        if recommendation is None or not recommendation.success:
            return (
                RecommendationStatus.UNAVAILABLE,
                "No live AI recommendation was available; deterministic policy used",
            )
        if recommendation.suggested_cause != category:
            return (
                RecommendationStatus.REJECTED,
                "AI cause did not match the independently verified failure category",
            )
        if recommendation.suggested_action is None:
            return (
                RecommendationStatus.REJECTED,
                "AI did not provide a candidate action",
            )
        if recommendation.suggested_action != category_policy.action:
            return (
                RecommendationStatus.CONSTRAINED,
                (
                    f"AI suggested {recommendation.suggested_action.value}; policy "
                    f"constrained the action to {category_policy.action.value}"
                ),
            )
        return (
            RecommendationStatus.ACCEPTED,
            "AI recommendation matched the independently verified policy action",
        )
