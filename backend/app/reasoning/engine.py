"""Qwen 3.5 reasoning layer via Ollama.

This module communicates with a locally running Ollama server to produce
structured explanations of recovery decisions that have ALREADY been made
by the deterministic policy engine.

Architecture invariant
~~~~~~~~~~~~~~~~~~~~~~
The reasoning layer is an *explainer*, not a decision-maker:

* It receives the policy decision as input and explains it.
* It CANNOT authorize automatic recovery.
* It CANNOT override a policy denial.
* It CANNOT increase retry limits or bypass safety caps.
* It CANNOT execute payments, call Razorpay, or send notifications.

If Ollama is unavailable, times out, or returns invalid output the
reasoner returns a safe deterministic fallback that preserves the
original policy decision unchanged.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.classifier.result import ClassificationResult
from app.config import settings
from app.models.payment_event import FailedTransactionEvent
from app.policy.result import PolicyDecision
from app.reasoning.result import ReasoningResult

logger = logging.getLogger(__name__)

# Default timeout for Ollama HTTP calls (seconds).
_DEFAULT_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# System prompt — instructs Qwen about its role and constraints.
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a payment-failure reasoning assistant embedded in a bounded recovery \
system. Your role is strictly to EXPLAIN decisions that have already been made \
by a deterministic policy engine. You do NOT make decisions.

CRITICAL CONSTRAINTS — you MUST obey ALL of these:
1. The policy decision provided to you is AUTHORITATIVE and FINAL.
2. You CANNOT authorize an action that the policy denied.
3. You CANNOT increase recovery limits or retry counts.
4. You CANNOT override escalation or manual-review decisions.
5. You CANNOT recommend executing a payment, calling an API, or sending \
   notifications.
6. If the policy denied recovery (automatic_recovery_allowed=false), you MUST \
   acknowledge the denial and explain why it is correct.

OUTPUT FORMAT — respond with ONLY a JSON object, no markdown fencing:
{
  "recommendation": "<short action recommendation consistent with the policy>",
  "explanation": "<2-3 sentence explanation of WHY the payment failed and WHY the policy decision is appropriate>",
  "confidence": <float 0.0 to 1.0>
}
"""


def _build_user_prompt(
    payment_event: FailedTransactionEvent,
    classification: ClassificationResult,
    policy_decision: PolicyDecision,
) -> str:
    """Build the user prompt containing the structured context."""
    return json.dumps(
        {
            "payment_event": {
                "event_id": payment_event.event_id,
                "amount": payment_event.amount,
                "currency": payment_event.currency,
                "payment_method": payment_event.payment_method.value,
                "error_code": payment_event.error_code,
                "error_description": payment_event.error_description,
                "attempt_number": payment_event.attempt_number,
                "type": payment_event.type.value,
            },
            "classification": {
                "category": classification.category.value,
                "confidence": classification.confidence,
                "rule_id": classification.rule_id,
                "reason": classification.reason,
            },
            "policy_decision": {
                "action": policy_decision.action.value,
                "automatic_recovery_allowed": policy_decision.automatic_recovery_allowed,
                "reason": policy_decision.reason,
                "rule_id": policy_decision.rule_id,
                "escalation_required": policy_decision.escalation_required,
                "max_retries_for_category": policy_decision.max_retries_for_category,
                "current_attempt": policy_decision.current_attempt,
                "amount_limit": policy_decision.amount_limit,
            },
        },
        indent=2,
    )


def _build_fallback(
    policy_decision: PolicyDecision,
    model_id: str,
    error_msg: str,
    classification: ClassificationResult | None = None,
) -> ReasoningResult:
    """Return a safe deterministic fallback that preserves the policy decision.

    This is called whenever Ollama is unavailable, times out, or returns
    output that cannot be parsed into a valid structured response.

    The fallback NEVER authorizes recovery — ``policy_action_allowed`` is
    copied verbatim from the policy engine's ``automatic_recovery_allowed``.
    """
    classification_context = (
        f"Payment classified as {classification.category.value}: {classification.reason}. "
        if classification is not None
        else "The failure classification was unavailable. "
    )

    return ReasoningResult(
        success=False,
        recommendation=f"Follow policy decision: {policy_decision.action.value}",
        explanation=(
            f"{classification_context}"
            f"The deterministic policy decision is {policy_decision.action.value}: "
            f"{policy_decision.reason}."
        ),
        confidence=0.0,
        model_id=model_id,
        policy_action_allowed=policy_decision.automatic_recovery_allowed,
        is_fallback=True,
        error=error_msg,
    )


def _parse_ollama_response(
    raw_body: dict[str, Any],
    policy_decision: PolicyDecision,
    model_id: str,
    classification: ClassificationResult | None = None,
) -> ReasoningResult:
    """Parse the Ollama chat completion response into a ReasoningResult.

    If the model output is malformed or missing required fields, a safe
    fallback is returned.
    """
    try:
        message = raw_body.get("message", {})
        content = message.get("content", "")

        # Strip markdown code fencing if present
        cleaned = content.strip()
        if cleaned.startswith("```"):
            # Remove first line (```json or ```) and last line (```)
            lines = cleaned.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError, AttributeError) as exc:
        logger.warning("Ollama returned unparseable content: %s", exc)
        return _build_fallback(
            policy_decision,
            model_id,
            f"Malformed model output: {exc}",
            classification=classification,
        )

    # Validate required fields
    recommendation = parsed.get("recommendation")
    explanation = parsed.get("explanation")
    confidence = parsed.get("confidence")

    if not isinstance(recommendation, str) or not recommendation.strip():
        return _build_fallback(
            policy_decision,
            model_id,
            "Model output missing 'recommendation' field",
            classification=classification,
        )
    if not isinstance(explanation, str) or not explanation.strip():
        return _build_fallback(
            policy_decision,
            model_id,
            "Model output missing 'explanation' field",
            classification=classification,
        )

    try:
        confidence_val = float(confidence)
        if not (0.0 <= confidence_val <= 1.0):
            raise ValueError("confidence out of range")
    except (TypeError, ValueError):
        return _build_fallback(
            policy_decision,
            model_id,
            f"Model output has invalid 'confidence' value: {confidence!r}",
            classification=classification,
        )

    # SAFETY: The reasoning layer MUST NOT upgrade a policy denial to allowed.
    # policy_action_allowed is ALWAYS copied from the policy engine.
    return ReasoningResult(
        success=True,
        recommendation=recommendation.strip(),
        explanation=explanation.strip(),
        confidence=confidence_val,
        model_id=model_id,
        policy_action_allowed=policy_decision.automatic_recovery_allowed,
        is_fallback=False,
        error=None,
    )


class RecoveryReasoner:
    """Qwen 3.5 reasoning layer via Ollama.

    Communicates with a locally running Ollama server to generate
    structured explanations of recovery decisions.

    The reasoner is fully isolated from the deterministic policy engine:
    it cannot mutate payment events, change policy decisions, or
    authorize recovery actions.

    Args:
        ollama_base_url: Base URL of the Ollama server.
            Defaults to ``settings.ollama_base_url``.
        ollama_model: Model identifier to use.
            Defaults to ``settings.ollama_model``.
        timeout: HTTP timeout in seconds for the Ollama call.
    """

    def __init__(
        self,
        ollama_base_url: str | None = None,
        ollama_model: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = (
            ollama_base_url if ollama_base_url is not None else settings.ollama_base_url
        )
        self._model = (
            ollama_model if ollama_model is not None else settings.ollama_model
        )
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        """The configured Ollama base URL."""
        return self._base_url

    @property
    def model(self) -> str:
        """The configured Ollama model identifier."""
        return self._model

    def analyze(
        self,
        payment_event: FailedTransactionEvent,
        classification: ClassificationResult,
        policy_decision: PolicyDecision,
    ) -> ReasoningResult:
        """Produce a structured reasoning explanation for a recovery decision.

        This method:
        1. Sends the payment context + policy decision to Ollama/Qwen.
        2. Parses the structured response.
        3. Returns a ``ReasoningResult`` that ALWAYS preserves the policy
           engine's ``automatic_recovery_allowed`` flag.

        On ANY failure (network, timeout, malformed response), returns a
        safe deterministic fallback.  Never raises, never crashes the
        recovery pipeline, never authorizes recovery on its own.

        Args:
            payment_event: The failed payment transaction event.
            classification: The classifier's result.
            policy_decision: The authoritative policy decision.

        Returns:
            A ``ReasoningResult`` — always safe to use.
        """
        if policy_decision is None:
            return ReasoningResult(
                success=False,
                recommendation="No action (Missing policy decision)",
                explanation="Cannot reason about a missing policy decision. Escalation required.",
                confidence=0.0,
                model_id=self._model,
                policy_action_allowed=False,
                is_fallback=True,
                error="policy_decision is None",
            )

        user_prompt = _build_user_prompt(
            payment_event, classification, policy_decision
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            # Reasoning is advisory only; disable extended chain-of-thought so
            # the dashboard receives the bounded JSON explanation promptly.
            "think": False,
            "format": "json",
            "options": {"num_predict": 128},
        }

        try:
            response = httpx.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            raw_body = response.json()
        except httpx.TimeoutException:
            logger.warning("Ollama request timed out after %.1fs", self._timeout)
            return _build_fallback(
                policy_decision,
                self._model,
                f"Ollama request timed out after {self._timeout}s",
                classification=classification,
            )
        except httpx.HTTPStatusError as exc:
            logger.warning("Ollama returned HTTP %d", exc.response.status_code)
            return _build_fallback(
                policy_decision,
                self._model,
                f"Ollama HTTP error: {exc.response.status_code}",
                classification=classification,
            )
        except httpx.ConnectError:
            logger.warning("Could not connect to Ollama at %s", self._base_url)
            return _build_fallback(
                policy_decision,
                self._model,
                f"Could not connect to Ollama at {self._base_url}",
                classification=classification,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unexpected error calling Ollama: %s", exc)
            return _build_fallback(
                policy_decision,
                self._model,
                f"Unexpected error: {exc}",
                classification=classification,
            )

        return _parse_ollama_response(
            raw_body, policy_decision, self._model, classification=classification
        )
