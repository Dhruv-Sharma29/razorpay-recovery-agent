"""Reasoning layer via NVIDIA NIM.

This module communicates with the NVIDIA NIM API to produce
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

If the NIM API is unavailable, times out, or returns invalid output the
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

# Default timeout for NIM HTTP calls (seconds). Kept short so a slow or
# unreachable NIM endpoint fails over to the deterministic fallback quickly.
_DEFAULT_TIMEOUT = 15.0


# ---------------------------------------------------------------------------
# System prompt — instructs the NIM model about its role and constraints.
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

7. The customer message MUST NOT state amounts, promise a refund or \
   retry, or claim any action has been authorized.

OUTPUT FORMAT — respond with ONLY a JSON object, no markdown fencing:
{
  "recommendation": "<short action recommendation consistent with the policy>",
  "explanation": "<2-3 sentence explanation of WHY the payment failed and WHY the policy decision is appropriate>",
  "confidence": <float 0.0 to 1.0>,
  "root_cause_plain": "<one sentence, plain language, no jargon: why this payment failed>",
  "why_appropriate": "<one sentence: why the policy's action is the right response>",
  "customer_message": "<2 sentences of polite customer-facing copy. No amounts, no promises, no authorization claims>",
  "escalation_summary": "<one sentence for a human reviewer; empty string if not escalated>"
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


# Phrases that would make customer copy make a promise the policy has not
# authorized. Cheaper and more predictable than asking the model twice.
_UNSAFE_CUSTOMER_PHRASES = (
    "refund",
    "authorized",
    "authorised",
    "guarantee",
    "guaranteed",
    "we have charged",
    "we will charge",
)


def _clean_optional(value: Any, *, max_chars: int = 600) -> str | None:
    """Accept a non-empty string, else None.

    An absent or malformed optional field must never fail the whole
    explanation — it just does not appear.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:max_chars]


def _clean_customer_message(value: Any) -> str | None:
    """Customer copy, rejected outright if it over-promises.

    The model may not tell a customer that money moved or that a recovery
    was approved — only the policy decides that, and the message is drafted
    before any retry has run.
    """
    text = _clean_optional(value, max_chars=400)
    if text is None:
        return None
    lowered = text.lower()
    if any(phrase in lowered for phrase in _UNSAFE_CUSTOMER_PHRASES):
        logger.warning("Rejected customer_message containing an unsafe promise")
        return None
    if any(ch.isdigit() for ch in text):
        # Amounts and dates in customer copy are a compliance risk here.
        logger.warning("Rejected customer_message containing digits")
        return None
    return text


def _build_fallback(
    policy_decision: PolicyDecision,
    model_id: str,
    error_msg: str,
    classification: ClassificationResult | None = None,
) -> ReasoningResult:
    """Return a safe deterministic fallback that preserves the policy decision.

    This is called whenever the API is unavailable, times out, or returns
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
        # Grounded in the rule that actually fired, so the operator view is
        # never blank when the model is unavailable. Clearly labelled as the
        # fallback via is_fallback.
        root_cause_plain=(
            classification.reason
            if classification is not None
            else "The failure could not be classified."
        ),
        why_appropriate=policy_decision.reason,
        customer_message=None,
        escalation_summary=(
            policy_decision.reason
            if policy_decision.escalation_required
            else None
        ),
        error=error_msg,
    )


def _parse_nim_response(
    raw_body: dict[str, Any],
    policy_decision: PolicyDecision,
    model_id: str,
    classification: ClassificationResult | None = None,
) -> ReasoningResult:
    """Parse the NIM chat completion response into a ReasoningResult.

    If the model output is malformed or missing required fields, a safe
    fallback is returned.
    """
    try:
        choices = raw_body.get("choices", [])
        if not choices:
            raise ValueError("No choices returned in API response")
        message = choices[0].get("message", {})
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
    except (json.JSONDecodeError, TypeError, AttributeError, ValueError) as exc:
        logger.warning("API returned unparseable content: %s", exc)
        return _build_fallback(
            policy_decision,
            model_id,
            f"Malformed model output: {exc}",
            classification=classification,
        )

    # The response must be a JSON object; a list/scalar is not a valid reply.
    if not isinstance(parsed, dict):
        return _build_fallback(
            policy_decision,
            model_id,
            f"Model output was not a JSON object (got {type(parsed).__name__})",
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
        root_cause_plain=_clean_optional(parsed.get("root_cause_plain")),
        why_appropriate=_clean_optional(parsed.get("why_appropriate")),
        customer_message=_clean_customer_message(parsed.get("customer_message")),
        escalation_summary=_clean_optional(parsed.get("escalation_summary")),
        error=None,
    )


class RecoveryReasoner:
    """Reasoning layer via NVIDIA NIM.

    Communicates with the NVIDIA NIM API to generate
    structured explanations of recovery decisions.

    The reasoner is fully isolated from the deterministic policy engine:
    it cannot mutate payment events, change policy decisions, or
    authorize recovery actions.

    Args:
        nim_api_key: API Key for NIM.
            Defaults to ``settings.nim_api_key``.
        nim_base_url: Base URL of the NIM API.
            Defaults to ``settings.nim_base_url``.
        nim_model: Model identifier to use.
            Defaults to ``settings.nim_model``.
        timeout: HTTP timeout in seconds for the API call.
    """

    def __init__(
        self,
        nim_api_key: str | None = None,
        nim_base_url: str | None = None,
        nim_model: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._api_key = (
            nim_api_key if nim_api_key is not None else settings.nim_api_key
        )
        self._base_url = (
            nim_base_url if nim_base_url is not None else settings.nim_base_url
        )
        self._model = (
            nim_model if nim_model is not None else settings.nim_model
        )
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        """The configured NIM base URL."""
        return self._base_url

    @property
    def model(self) -> str:
        """The configured NIM model identifier."""
        return self._model

    def analyze(
        self,
        payment_event: FailedTransactionEvent,
        classification: ClassificationResult,
        policy_decision: PolicyDecision,
    ) -> ReasoningResult:
        """Produce a structured reasoning explanation for a recovery decision.

        This method:
        1. Sends the payment context + policy decision to NIM.
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

        # No API key configured: skip the network call entirely and return the
        # deterministic fallback immediately (avoids a guaranteed-401 round trip
        # and keeps the demo fast when NIM is not wired up).
        if not (self._api_key and self._api_key.strip()):
            logger.info("NIM_API_KEY not configured; using deterministic fallback")
            return _build_fallback(
                policy_decision,
                self._model,
                "NIM_API_KEY not configured; skipped NIM call",
                classification=classification,
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
            "max_tokens": 1024,
            "temperature": 0.6,
            "top_p": 0.95,
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            raw_body = response.json()
        except httpx.TimeoutException:
            logger.warning("NIM request timed out after %.1fs", self._timeout)
            return _build_fallback(
                policy_decision,
                self._model,
                f"NIM request timed out after {self._timeout}s",
                classification=classification,
            )
        except httpx.HTTPStatusError as exc:
            logger.warning("NIM returned HTTP %d", exc.response.status_code)
            return _build_fallback(
                policy_decision,
                self._model,
                f"NIM HTTP error: {exc.response.status_code}",
                classification=classification,
            )
        except httpx.ConnectError:
            logger.warning("Could not connect to NIM at %s", self._base_url)
            return _build_fallback(
                policy_decision,
                self._model,
                f"Could not connect to NIM at {self._base_url}",
                classification=classification,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unexpected error calling NIM: %s", exc)
            return _build_fallback(
                policy_decision,
                self._model,
                f"Unexpected error: {exc}",
                classification=classification,
            )

        return _parse_nim_response(
            raw_body, policy_decision, self._model, classification=classification
        )
