"""PromptBuilder — assembles deterministic LLM prompts from ResponsePlan.

The PromptBuilder is a pure function: given the same sealed ResponsePlan and
CustomerContext, it ALWAYS produces the same prompt text and hash (RI-7).
No randomness, no timestamp embedding, no external I/O.

The prompt enforces the Law of Authority (RI-5) by injecting only
authoritative facts from ResponsePlan.facts (sourced from the CRM) into the
instruction layer. The LLM may rephrase but must never contradict these facts.

Every intelligence engine output is injected so the LLM has the same complete
picture as the scripted FSM golden path — emotion state, all intents, extracted
entities, risk flags, policy constraints, empathy pacing, and strategy rationale.

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
from src.engines.prompt_builder.kavya_persona import build_system_prompt

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v3.0.0"

_STRATEGY_INSTRUCTIONS: dict[StrategyLabel, str] = {
    StrategyLabel.ASK: (
        "Use the consultative approach: (1) Acknowledge any stress you sense before asking. "
        "(2) Frame the urgency using CIBIL impact and daily interest accrual — facts, not threats. "
        "(3) Use option closing: offer two specific dates ('5 ko ya 10 ko?'), never an open 'kab?'. "
        "(4) If the customer stalls, ladder down: full payment → partial → any amount → date only. "
        "Goal: get a specific date and amount commitment before ending this turn."
    ),
    StrategyLabel.VERIFY: (
        "Verify the customer's identity warmly — not like a security guard. "
        "Ask for their date of birth or last four digits of account in a friendly tone. "
        "Frame it as: 'Quick confirmation so I can pull up your details.'"
    ),
    StrategyLabel.NEGOTIATE: (
        "You are negotiating within the authorized envelope (floor and ceiling are in NEGOTIATION ENVELOPE below). "
        "Technique: (1) Start at the ceiling (full outstanding). "
        "(2) If customer counters below floor, empathize and bridge: 'Samajh sakti hoon — "
        "lekin minimum [floor] hona chahiye, kya possible hai?' "
        "(3) After max 2 concessions, hold firm. "
        "(4) Mention CIBIL and daily interest as legitimate urgency — never legal threats. "
        "(5) Close with two specific dates once amount is agreed."
    ),
    StrategyLabel.REASSURE: (
        "Customer is distressed. Technique: (1) Lead with genuine empathy — one full empathy sentence. "
        "(2) Normalize the situation: 'Aisa aata jaata rehta hai.' "
        "(3) Position yourself as their ally: 'Main chahti hoon aapka account settle ho.' "
        "(4) Only after empathy, offer the smallest possible win: 'Kuch bhi possible hai? "
        "Even ₹500 aaj bhi chalega.' "
        "(5) Mention fresh-start benefit: 'Account clear hone ke baad fresh start ho jaata hai.'"
    ),
    StrategyLabel.ESCALATE: (
        "Escalate to a supervisor. Be calm, professional, and non-confrontational. "
        "Say: 'Aapki baat mein supervisor se karaati hoon — ek second.' "
        "Never explain why you are escalating to the customer."
    ),
    StrategyLabel.TRANSFER: (
        "Transfer to the appropriate department. "
        "Say: 'Main aapko sahi team se connect karti hoon.' "
        "Keep it brief and warm."
    ),
    StrategyLabel.CLOSE: (
        "Close the call with a confident summary of what was committed. "
        "Recap: date, amount, and next step in one sentence. "
        "End with: 'Our side se koi aur call nahi aayegi — commitment note ho gayi. Thanks!' "
        "Never trail off or ask open questions at close."
    ),
    StrategyLabel.CONFIRM: (
        "Confirm the commitment the customer just made. "
        "Echo back the exact date and exact amount they stated. "
        "Then close: 'Perfect — note kar liya. Koi follow-up nahi hoga.' "
        "If date or amount is missing, ask for the one that is missing before confirming."
    ),
}

# Sentiment float thresholds → human-readable category
_SENTIMENT_NEGATIVE_THRESHOLD = -0.25
_SENTIMENT_POSITIVE_THRESHOLD = 0.25

# Arousal float thresholds
_AROUSAL_HIGH_THRESHOLD = 0.65
_AROUSAL_MEDIUM_THRESHOLD = 0.35

# Speaking rate below this → SLOW pacing
_SLOW_RATE_THRESHOLD = 0.95


def _fmt_minor(amount_minor: int) -> str:
    """Format minor currency units as a human-readable INR string."""
    rupees = amount_minor // 100
    paise = amount_minor % 100
    if paise:
        return f"₹{rupees:,}.{paise:02d}"
    return f"₹{rupees:,}"


def _sentiment_label(sentiment_float: float) -> str:
    if sentiment_float <= _SENTIMENT_NEGATIVE_THRESHOLD:
        return "NEGATIVE"
    if sentiment_float >= _SENTIMENT_POSITIVE_THRESHOLD:
        return "POSITIVE"
    return "NEUTRAL"


def _arousal_label(arousal: float) -> str:
    if arousal >= _AROUSAL_HIGH_THRESHOLD:
        return "HIGH"
    if arousal >= _AROUSAL_MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def _pacing_label(target_speaking_rate: float) -> str:
    return "SLOW" if target_speaking_rate < _SLOW_RATE_THRESHOLD else "NORMAL"


class PromptBuilder:
    """Assembles a deterministic system + user prompt pair for the LLM.

    Injects ALL intelligence engine outputs from ResponsePlan so the LLM has
    the same complete picture as the scripted FSM golden path: emotion state,
    all intents, extracted entities, risk flags, policy constraints, empathy
    pacing, strategy rationale, and negotiation context.

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

        # --- System preamble (Kavya persona — full rules injected) ----------
        lender_name = response_plan.facts.get("lender_name", "")
        if not lender_name and context:
            lender_name = getattr(context, "lender_name", "") or ""
        parts.append(build_system_prompt(lender_name or "the lender"))
        parts.append(f"[Plan ID: {response_plan.plan_id}]")
        parts.append(f"[Version: {PROMPT_VERSION}]")

        # --- Goal -----------------------------------------------------------
        if response_plan.goal:
            parts.append(f"\nGOAL: {response_plan.goal}")

        # --- Strategy instruction + rationale --------------------------------
        strategy_label = response_plan.strategy.action
        instruction = _STRATEGY_INSTRUCTIONS.get(strategy_label, "Assist the customer.")
        parts.append(f"\nINSTRUCTION: {instruction}")
        if response_plan.strategy.rationale:
            parts.append(f"STRATEGY RATIONALE: {response_plan.strategy.rationale}")

        # --- Emotion state (full picture for LLM) ---------------------------
        parts.append(self._build_emotion_section(response_plan))

        # --- All customer intents (ranked) ----------------------------------
        if response_plan.intents:
            parts.append(self._build_intents_section(response_plan))

        # --- Entities extracted this turn -----------------------------------
        parts.append(self._build_entities_section(response_plan))

        # --- Risk flags (if any) -------------------------------------------
        if response_plan.risk_flags:
            parts.append(self._build_risk_section(response_plan))

        # --- Policy constraints (agent awareness) ---------------------------
        if response_plan.policy_constraints:
            parts.append(self._build_policy_section(response_plan))

        # --- Authoritative facts (RI-5) ------------------------------------
        facts_section = self._build_facts_section(response_plan, context)
        if facts_section:
            parts.append(f"\nAUTHORITATIVE FACTS (do not contradict these):\n{facts_section}")

        # --- Negotiation envelope (if applicable) --------------------------
        if response_plan.negotiation_envelope is not None:
            parts.append(self._build_negotiation_section(response_plan))

        # --- Tone and delivery guidance ------------------------------------
        delivery = response_plan.delivery
        pacing = _pacing_label(delivery.target_speaking_rate)
        tone_label = response_plan.emotion.dominant_emotion
        parts.append(
            f"\nDELIVERY: Respond in {delivery.language}. "
            f"Tone: {tone_label}. "
            f"Pacing: {pacing}. "
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

        # --- Sales Intelligence block (Phase 2 — appended, never replaces) --
        if response_plan.sales_state is not None:
            sales_block = self._build_sales_block(response_plan.sales_state)
            if sales_block:
                parts.append(sales_block)
        prompt_text = "\n".join(parts)
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()

        logger.debug(
            "PromptBuilder: prompt assembled",
            extra={
                "plan_id": response_plan.plan_id,
                "version": PROMPT_VERSION,
                "hash": prompt_hash[:16],
                "length": len(prompt_text),
                "intents": len(response_plan.intents),
                "risk_flags": len(response_plan.risk_flags),
                "entities": len(response_plan.entities),
            },
        )

        return prompt_text, prompt_hash

    # -----------------------------------------------------------------------
    # Section builders
    # -----------------------------------------------------------------------

    def _build_emotion_section(self, plan: ResponsePlan) -> str:
        emotion = plan.emotion
        sentiment_cat = _sentiment_label(emotion.sentiment)
        arousal_cat = _arousal_label(emotion.arousal)
        pacing = _pacing_label(plan.delivery.target_speaking_rate)
        lines = [
            "\nEMOTION STATE (detected this turn):",
            f"  dominant: {emotion.dominant_emotion}",
            f"  sentiment: {sentiment_cat} ({emotion.sentiment:+.2f})",
            f"  arousal: {arousal_cat} ({emotion.arousal:.2f})",
            f"  recommended pacing: {pacing}",
        ]
        if arousal_cat == "HIGH" or sentiment_cat == "NEGATIVE":
            lines.append(
                "  → LEAD WITH EMPATHY before any ask. "
                "Use 'Samajh sakti hoon' or equivalent. One full empathy sentence first."
            )
        return "\n".join(lines)

    def _build_intents_section(self, plan: ResponsePlan) -> str:
        lines = ["\nCUSTOMER INTENTS (ranked by confidence):"]
        for i, intent in enumerate(plan.intents, 1):
            span = f" — \"{intent.source_span}\"" if intent.source_span else ""
            lines.append(f"  {i}. {intent.label.value} ({intent.confidence:.2f}){span}")
        return "\n".join(lines)

    def _build_entities_section(self, plan: ResponsePlan) -> str:
        if not plan.entities:
            return "\nENTITIES EXTRACTED THIS TURN: none"
        lines = ["\nENTITIES EXTRACTED THIS TURN:"]
        for key, value in plan.entities.items():
            lines.append(f"  {key}: {value}")
        lines.append(
            "  → If amount or date was extracted above, the customer stated it explicitly. "
            "Echo it back verbatim when confirming."
        )
        return "\n".join(lines)

    def _build_risk_section(self, plan: ResponsePlan) -> str:
        lines = ["\nRISK FLAGS (active — adjust approach accordingly):"]
        for flag in plan.risk_flags:
            lines.append(f"  [{flag.level.value}] {flag.flag_id} — {flag.description}")
        # Specific behavioral guidance per flag
        flag_ids = {f.flag_id for f in plan.risk_flags}
        if "ABUSE_DETECTED" in flag_ids:
            lines.append("  → CRITICAL: De-escalate immediately. Do not engage with abuse. Prepare to transfer.")
        if "HARDSHIP_INDICATOR" in flag_ids:
            lines.append("  → Activate small-win ladder: full → partial → any amount → date only.")
        if "DISPUTE_CLAIM" in flag_ids:
            lines.append("  → Acknowledge the dispute, do not argue. Offer to check and call back.")
        if "ELDERLY_VULNERABLE" in flag_ids:
            lines.append("  → Extra patience. Speak slowly. Never pressure.")
        return "\n".join(lines)

    def _build_policy_section(self, plan: ResponsePlan) -> str:
        lines = ["\nACTIVE POLICY CONSTRAINTS (hard rules — never violate):"]
        for constraint in plan.policy_constraints:
            hard = "HARD" if constraint.is_hard_rule else "SOFT"
            lines.append(f"  [{hard}] {constraint.rule_id}: {constraint.description}")
        return "\n".join(lines)

    def _build_negotiation_section(self, plan: ResponsePlan) -> str:
        env = plan.negotiation_envelope
        floor_str = _fmt_minor(env.floor_minor)
        ceiling_str = _fmt_minor(env.ceiling_minor)
        lines = [
            f"\nNEGOTIATION ENVELOPE:",
            f"  floor: {floor_str}  ceiling: {ceiling_str}",
            f"  move type: {env.move_type.value}",
            f"  max concessions: {env.max_concession_count}",
        ]
        if env.proposed_amount_minor is not None:
            lines.append(f"  proposed amount: {_fmt_minor(env.proposed_amount_minor)}")
        if env.proposed_date is not None:
            lines.append(f"  proposed date: {env.proposed_date.isoformat()}")
        if env.is_finalized_commitment:
            lines.append(
                "  → COMMITMENT FINALIZED: echo back exact amount and date to confirm. "
                "Then close confidently — no more negotiation."
            )
        else:
            lines.append(
                "  → Start at ceiling. Bridge to floor if customer counters low. "
                "After max concessions, hold firm."
            )
        return "\n".join(lines)

    def _build_sales_block(self, sales_state: dict) -> str:
        """Build the SALES INTELLIGENCE block injected after existing strategy content.

        ADDITIVE — never replaces existing strategy/empathy/risk sections.
        Hides internal system names from the LLM; only actionable instructions exposed.
        """
        lines: list[str] = ["\nSALES INTELLIGENCE:"]

        stage = sales_state.get("lead_stage", "")
        objective = sales_state.get("current_objective", "")
        if stage:
            lines.append(f"Stage: {stage}")
        if objective:
            lines.append(f"Objective: {objective}")

        known_lines: list[str] = []
        confirmed = sales_state.get("confirmed_fields", [])
        if "LOCATION" in confirmed and sales_state.get("location"):
            known_lines.append(f"  - Location: {', '.join(sales_state['location'])}")
        if "PROPERTY_TYPE" in confirmed and sales_state.get("property_type"):
            known_lines.append(f"  - Property type: {', '.join(sales_state['property_type'])}")
        if "BUDGET" in confirmed:
            b_min = sales_state.get("budget_min")
            b_max = sales_state.get("budget_max")
            if b_min and b_max and b_min == b_max:
                known_lines.append(f"  - Budget: ₹{b_max // 100000}L")
            elif b_min and b_max:
                known_lines.append(f"  - Budget: ₹{b_min // 100000}L–₹{b_max // 100000}L")
        if "PURPOSE" in confirmed and sales_state.get("purpose"):
            known_lines.append(f"  - Purpose: {sales_state['purpose']}")
        if "TIMELINE" in confirmed and sales_state.get("timeline"):
            known_lines.append(f"  - Timeline: {sales_state['timeline']}")
        if "DECISION_MAKER" in confirmed and sales_state.get("decision_maker"):
            known_lines.append(f"  - Decision maker: {sales_state['decision_maker']}")
        if "FINANCING" in confirmed and sales_state.get("financing_status"):
            known_lines.append(f"  - Financing: {sales_state['financing_status']}")

        if known_lines:
            lines.append("\nKnown requirements:")
            lines.extend(known_lines)

        next_q = sales_state.get("next_question")
        next_action = sales_state.get("next_action", "")
        lines.append(
            f"\nMissing (ask this turn): {next_q if next_q else 'All key requirements known'}"
        )
        lines.append(f"NEXT ACTION: {next_action}")

        if next_q:
            field_labels = {
                "PURPOSE": "their purpose (self-use or investment)",
                "LOCATION": "which areas/locations they prefer",
                "PROPERTY_TYPE": "what property type they are looking for (e.g. 2BHK, 3BHK)",
                "BUDGET": "their budget range",
                "TIMELINE": "when they are planning to buy",
                "DECISION_MAKER": "who will be making the final decision",
                "FINANCING": "how they plan to finance the purchase (cash or home loan)",
                "PREFERRED_LOCALITY": "their preferred locality within the area",
                "SITE_VISIT_INTEREST": "if they would like to schedule a site visit",
                "COMPETITOR_CONSIDERATION": "if they are also looking at other projects",
            }
            label = field_labels.get(next_q, next_q.lower().replace("_", " "))
            lines.append(
                f"\nINSTRUCTION: Ask ONE natural conversational question to find out {label}. "
                "Sound natural. Do NOT mention scores, stages, or system names."
            )
        elif next_action == "HANDLE_OBJECTION":
            lines.append(
                "\nINSTRUCTION: Address the customer's concern empathetically before "
                "resuming qualification."
            )
        elif next_action == "HUMAN_HANDOFF":
            lines.append(
                "\nINSTRUCTION: Politely indicate you will connect them with a specialist "
                "who can help them further."
            )
        elif next_action == "OFFER_SITE_VISIT":
            lines.append(
                "\nINSTRUCTION: Invite the customer for a site visit in a warm, natural way."
            )
        elif next_action == "CONFIRM_SITE_VISIT":
            lines.append(
                "\nINSTRUCTION: Confirm the site visit details and provide next steps."
            )

        return "\n".join(lines)

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
