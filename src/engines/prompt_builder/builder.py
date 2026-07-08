"""PromptBuilder — assembles deterministic LLM prompts from ResponsePlan.

The PromptBuilder is a pure function: given the same sealed ResponsePlan and
CustomerContext, it ALWAYS produces the same prompt text and hash (RI-7).
No randomness, no timestamp embedding, no external I/O.

The prompt enforces the Law of Authority (RI-5) by injecting only
authoritative facts from ResponsePlan.facts (sourced from the CRM) into the
instruction layer. The LLM may rephrase but must never contradict these facts.

Architecture: V1 Ch12 (Prompt Builder); V2 Ch15; RI-5; RI-7.
"""

from __future__ import annotations

import hashlib
import logging

from src.libs.contracts.context import CustomerContext
from src.libs.contracts.response_plan import (
    ResponsePlan,
    StrategyLabel,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1.0.0"

_STRATEGY_INSTRUCTIONS: dict[StrategyLabel, str] = {
    StrategyLabel.ASK: "Ask the customer about their ability to make a payment today.",
    StrategyLabel.VERIFY: "Verify the customer's identity by asking for their date of birth or account details.",
    StrategyLabel.NEGOTIATE: "Negotiate a payment amount within the authorized envelope. Do not offer amounts outside the bounds.",
    StrategyLabel.REASSURE: "Reassure the customer. Acknowledge their difficulty and explore hardship options.",
    StrategyLabel.ESCALATE: "Escalate the call to a supervisor. Inform the customer politely.",
    StrategyLabel.TRANSFER: "Transfer the customer to the appropriate department.",
    StrategyLabel.CLOSE: "Close the call professionally with a summary and next steps.",
    StrategyLabel.CONFIRM: "Confirm the payment commitment clearly with date and amount.",
}


def _fmt_minor(amount_minor: int) -> str:
    """Format minor currency units as a human-readable INR string."""
    rupees = amount_minor // 100
    paise = amount_minor % 100
    if paise:
        return f"₹{rupees:,}.{paise:02d}"
    return f"₹{rupees:,}"


class PromptBuilder:
    """Assembles a deterministic system + user prompt pair for the LLM.

    Architecture: V1 Ch12; RI-7 (deterministic prompt).

    Usage:
        builder = PromptBuilder()
        prompt, prompt_hash = builder.build(response_plan, context)
    """

    def build(
        self,
        response_plan: ResponsePlan,
        context: CustomerContext | None = None,
    ) -> tuple[str, str]:
        """Build the LLM prompt and return (prompt_text, sha256_hash).

        The returned hash is the SHA-256 digest of the UTF-8 encoded prompt.
        Same inputs always produce the same hash (RI-7 determinism).

        Args:
            response_plan: The sealed, immutable ResponsePlan for this turn.
            context: Optional CustomerContext for additional facts.

        Returns:
            Tuple of (prompt_text: str, prompt_hash: str).
        """
        parts: list[str] = []

        # --- System preamble ------------------------------------------------
        parts.append("You are a professional collections agent for a regulated NBFC.")
        parts.append("You must follow all instructions exactly and never deviate.")
        parts.append(f"[Plan ID: {response_plan.plan_id}]")
        parts.append(f"[Version: {PROMPT_VERSION}]")

        # --- Goal -----------------------------------------------------------
        if response_plan.goal:
            parts.append(f"\nGOAL: {response_plan.goal}")

        # --- Strategy instruction -------------------------------------------
        strategy_label = response_plan.strategy.action
        instruction = _STRATEGY_INSTRUCTIONS.get(strategy_label, "Assist the customer.")
        parts.append(f"\nINSTRUCTION: {instruction}")

        # --- Authoritative facts (RI-5) ------------------------------------
        facts_section = self._build_facts_section(response_plan, context)
        if facts_section:
            parts.append(f"\nAUTHORITATIVE FACTS (do not contradict these):\n{facts_section}")

        # --- Negotiation envelope (if applicable) --------------------------
        if response_plan.negotiation_envelope is not None:
            env = response_plan.negotiation_envelope
            floor_str = _fmt_minor(env.floor_minor)
            ceiling_str = _fmt_minor(env.ceiling_minor)
            parts.append(
                f"\nNEGOTIATION ENVELOPE: You may offer between {floor_str} and {ceiling_str}. "
                f"Move type: {env.move_type.value}. "
                f"Max concessions: {env.max_concession_count}."
            )

        # --- Tone and delivery guidance ------------------------------------
        delivery = response_plan.delivery
        tone_label = response_plan.emotion.dominant_emotion
        parts.append(
            f"\nDELIVERY: Respond in {delivery.language}. "
            f"Tone: {tone_label}. "
            f"Max {delivery.max_response_tokens} tokens."
        )

        # --- Must-say items ------------------------------------------------
        if response_plan.must_say:
            must_say_lines = "\n".join(f"  - {item.text}" for item in response_plan.must_say)
            parts.append(f"\nYOU MUST SAY (include these in your response):\n{must_say_lines}")

        # --- Must-not-say items --------------------------------------------
        if response_plan.must_not_say:
            must_not_lines = "\n".join(f"  - {item.description}" for item in response_plan.must_not_say)
            parts.append(f"\nYOU MUST NOT SAY:\n{must_not_lines}")

        # --- Retrieval snippets (evidence, not authority) ------------------
        if response_plan.retrieval:
            snippets = "\n".join(f"  [{s.source}]: {s.content[:200]}" for s in response_plan.retrieval[:3])
            parts.append(f"\nKNOWLEDGE (evidence only — do not quote amounts from here):\n{snippets}")

        # --- Intent context ------------------------------------------------
        if response_plan.intents:
            top = response_plan.intents[0]
            parts.append(f"\nCUSTOMER INTENT: {top.label.value} (confidence {top.confidence:.2f})")

        prompt_text = "\n".join(parts)
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()

        logger.debug(
            "PromptBuilder: prompt assembled",
            extra={
                "plan_id": response_plan.plan_id,
                "version": PROMPT_VERSION,
                "hash": prompt_hash[:16],
                "length": len(prompt_text),
            },
        )

        return prompt_text, prompt_hash

    def _build_facts_section(
        self,
        plan: ResponsePlan,
        context: CustomerContext | None,
    ) -> str:
        """Build the authoritative facts section from plan.facts and context."""
        lines: list[str] = []

        # Prefer facts from the sealed plan (already extracted from CRM).
        for key, value in plan.facts.items():
            lines.append(f"  {key}: {value}")

        # Supplement with CustomerContext if available and plan.facts is sparse.
        if context and len(plan.facts) < 3:
            primary = context.primary_party
            lines.append(f"  customer_name: {primary.name}")
            if context.primary_loan:
                loan = context.primary_loan
                bal = loan.outstanding_balance
                lines.append(f"  outstanding_balance: {bal.amount_minor} minor units")
                lines.append(f"  dpd: {loan.dpd}")

        return "\n".join(lines)
