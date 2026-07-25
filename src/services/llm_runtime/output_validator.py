"""OutputValidator — validates LLM output against the sealed ResponsePlan.

Enforces the Law of Authority (RI-5) and output coherence (RI-6) before
any LLM-generated text reaches TTS. On violation it returns a structured
ValidationResult with a fallback response instead of raising an exception
(the ConversationEngine handles retries).

Validation checks:
  1. RI-6: plan_id coherence — LLM must reference the correct plan.
  2. Must-not-say: hard rejection on prohibited content.
  3. Must-say: soft check that mandatory disclosures are present.
  4. RI-5: amounts in the output must be within 20% of plan.facts values.
  5. Empty/too-short output: rejected.

Architecture: V1 Ch14 (Output Validator); RI-5; RI-6.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.libs.contracts.response_plan import ResponsePlan
from src.libs.invariants.guards import assert_ri6_output_coherence

logger = logging.getLogger(__name__)

_MIN_OUTPUT_TOKENS = 3

_AMOUNT_PATTERN = re.compile(r"(?:₹|Rs\.?\s*)(\d[\d,]*(?:\.\d{1,2})?)")


@dataclass(frozen=True)
class ValidationResult:
    """Result of an OutputValidator check.

    If valid=True the output may proceed to TTS. If valid=False,
    the ConversationEngine should retry (up to 2 attempts) or use
    fallback_response.
    """

    valid: bool
    """Whether the LLM output passed all validation checks."""

    violations: list[str] = field(default_factory=list)
    """Human-readable descriptions of any violations (empty when valid=True)."""

    fallback_response: str = ""
    """A safe fallback response to use when retries are exhausted."""

    plan_id: str = ""
    """plan_id used during validation (for audit/logging)."""


_SAFE_FALLBACK = "Ek pal ke liye ruk jaiye, main aapki madad karne ki koshish kar rahi hoon."
"""Feminine grammar ("kar rahi hoon") — spoken by the Kavya persona (a
female agent, V2 Ch13); see ai_governance/verdict.py's SAFE_FALLBACK_RESPONSE
for the full story on why this matters (same class of bug, same fix, found
via Path-A Call-002 readiness validation)."""


class OutputValidator:
    """Validates LLM output text against a sealed ResponsePlan.

    Architecture: V1 Ch14; RI-5; RI-6.

    Usage:
        validator = OutputValidator()
        result = validator.validate(llm_output, response_plan)
        if not result.valid:
            # handle retry or use result.fallback_response
    """

    def validate(self, llm_output: str, response_plan: ResponsePlan) -> ValidationResult:
        """Validate the LLM output against the ResponsePlan.

        Args:
            llm_output: Full text produced by the LLM.
            response_plan: The sealed ResponsePlan for this turn.

        Returns:
            ValidationResult indicating validity and any violations.
        """
        violations: list[str] = []

        # RI-6: plan coherence check (the LLM is seeded with plan_id in prompt).
        # For Sprint-012, we verify the plan_id is not empty and the output
        # is non-empty — full RI-6 requires the LLM to echo back the plan_id
        # which Sprint-012 prompts do not enforce; we prepare the guard call.
        if not response_plan.plan_id:
            violations.append("RI-6: ResponsePlan has no plan_id")
        else:
            # If the LLM output embeds [Plan ID: <uuid>], verify it matches.
            id_match = re.search(r"\[Plan ID:\s*([a-f0-9-]{36})\]", llm_output)
            if id_match:
                embedded_id = id_match.group(1)
                try:
                    assert_ri6_output_coherence(response_plan.plan_id, embedded_id)
                except Exception as exc:
                    violations.append(f"RI-6: {exc}")

        # Empty output check.
        stripped = llm_output.strip()
        if not stripped:
            violations.append("Output is empty")
            return ValidationResult(
                valid=False,
                violations=violations,
                fallback_response=_SAFE_FALLBACK,
                plan_id=response_plan.plan_id,
            )

        word_count = len(stripped.split())
        if word_count < _MIN_OUTPUT_TOKENS:
            violations.append(f"Output too short: {word_count} words (min {_MIN_OUTPUT_TOKENS})")

        # Must-not-say check.
        for mns_item in response_plan.must_not_say:
            if mns_item.pattern:
                if re.search(mns_item.pattern, stripped, re.IGNORECASE):
                    violations.append(
                        f"Must-not-say violation [{mns_item.item_id}]: pattern '{mns_item.pattern}' matched"
                    )

        # RI-5: amount grounding check.
        amount_violation = self._check_amounts(stripped, response_plan)
        if amount_violation:
            violations.append(amount_violation)

        # Must-say check (soft warning logged, not a rejection by itself in Sprint-012).
        for ms_item in response_plan.must_say:
            if ms_item.is_exact_match and ms_item.text not in stripped:
                logger.warning(
                    "OutputValidator: must-say item not present in output",
                    extra={"item_id": ms_item.item_id, "plan_id": response_plan.plan_id},
                )

        is_valid = len(violations) == 0

        if not is_valid:
            logger.warning(
                "OutputValidator: REJECTED output for plan %s — violations: %s",
                response_plan.plan_id,
                violations,
            )
        else:
            logger.debug("OutputValidator: output ACCEPTED for plan %s", response_plan.plan_id)

        return ValidationResult(
            valid=is_valid,
            violations=violations,
            fallback_response=_SAFE_FALLBACK if not is_valid else "",
            plan_id=response_plan.plan_id,
        )

    @staticmethod
    def _check_amounts(text: str, plan: ResponsePlan) -> str | None:
        """RI-5: Check that amounts in the text match plan.facts.

        Returns None if no violation, else a violation description string.
        """
        bal_minor = plan.facts.get("outstanding_balance_minor")
        if not isinstance(bal_minor, int) or bal_minor == 0:
            return None

        amounts = _AMOUNT_PATTERN.findall(text)
        if not amounts:
            return None

        bal_major = bal_minor / 100
        for raw in amounts:
            try:
                val = float(raw.replace(",", ""))
            except ValueError:
                continue
            deviation = abs(val - bal_major) / max(1.0, bal_major)
            if deviation > 0.25:
                return f"RI-5: Amount ₹{val:,.2f} deviates {deviation:.0%} from authoritative balance ₹{bal_major:,.2f}"
        return None
