"""Unit tests for PromptBuilder — determinism (RI-7) and content verification."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from src.engines.prompt_builder import PROMPT_VERSION, PromptBuilder
from src.libs.contracts.response_plan import (
    EmotionSpec,
    IntentLabel,
    IntentSignal,
    MustSayItem,
    NegotiationEnvelope,
    NegotiationMoveType,
    PolicyConstraint,
    ResponsePlan,
    RiskFlag,
    RiskLevel,
    StrategyAction,
    StrategyLabel,
)


def _make_plan(**kwargs: Any) -> ResponsePlan:
    defaults: dict[str, object] = {
        "plan_id": str(uuid.uuid4()),
        "version": 1,
        "call_id": "call-001",
        "tenant_id": "tenant-001",
        "created_at": datetime.utcnow(),
        "strategy": StrategyAction(action=StrategyLabel.ASK),
    }
    defaults.update(kwargs)
    return ResponsePlan(**defaults)  # type: ignore[arg-type]


class TestPromptBuilder:
    def test_prompt_builder_deterministic(self) -> None:  # required named test
        """test_prompt_builder_deterministic: same plan → same hash (RI-7)."""
        builder = PromptBuilder()
        plan = _make_plan()
        prompt1, hash1 = builder.build(plan, None)
        prompt2, hash2 = builder.build(plan, None)
        assert hash1 == hash2
        assert prompt1 == prompt2

    def test_different_plans_give_different_hashes(self) -> None:
        builder = PromptBuilder()
        plan_a = _make_plan(goal="collect payment")
        plan_b = _make_plan(goal="verify identity")
        _, hash_a = builder.build(plan_a, None)
        _, hash_b = builder.build(plan_b, None)
        assert hash_a != hash_b

    def test_prompt_contains_plan_id(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan()
        prompt, _ = builder.build(plan, None)
        assert plan.plan_id in prompt

    def test_prompt_contains_strategy_instruction(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(strategy=StrategyAction(action=StrategyLabel.NEGOTIATE))
        prompt, _ = builder.build(plan, None)
        assert "negoti" in prompt.lower()

    def test_prompt_contains_negotiation_envelope(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(
            strategy=StrategyAction(action=StrategyLabel.NEGOTIATE),
            negotiation_envelope=NegotiationEnvelope(
                floor_minor=10_000,
                ceiling_minor=50_000,
                move_type=NegotiationMoveType.PARTIAL_PAYMENT,
            ),
        )
        prompt, _ = builder.build(plan, None)
        assert "NEGOTIATION ENVELOPE" in prompt

    def test_prompt_contains_must_say(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(
            must_say=(
                MustSayItem(
                    item_id="DISCLOSURE_001",
                    text="Yeh call record ki ja rahi hai.",
                ),
            )
        )
        prompt, _ = builder.build(plan, None)
        assert "YOU MUST SAY" in prompt
        assert "Yeh call record ki ja rahi hai." in prompt

    def test_prompt_version_present(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan()
        prompt, _ = builder.build(plan, None)
        assert PROMPT_VERSION in prompt

    def test_prompt_includes_intent_context(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(intents=(IntentSignal(label=IntentLabel.PAYMENT, confidence=0.95),))
        prompt, _ = builder.build(plan, None)
        assert "PAYMENT" in prompt


class TestPromptBuilderIntelligence:
    """Tests that ALL engine outputs are injected into the LLM prompt."""

    # ── Emotion state ─────────────────────────────────────────────────────

    def test_emotion_section_present(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(
            emotion=EmotionSpec(sentiment=-0.7, arousal=0.8, dominant_emotion="frustrated")
        )
        prompt, _ = builder.build(plan, None)
        assert "EMOTION STATE" in prompt

    def test_emotion_negative_sentiment_label(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(
            emotion=EmotionSpec(sentiment=-0.6, arousal=0.5, dominant_emotion="frustrated")
        )
        prompt, _ = builder.build(plan, None)
        assert "NEGATIVE" in prompt

    def test_emotion_positive_sentiment_label(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(
            emotion=EmotionSpec(sentiment=0.8, arousal=0.2, dominant_emotion="cooperative")
        )
        prompt, _ = builder.build(plan, None)
        assert "POSITIVE" in prompt

    def test_emotion_high_arousal_triggers_empathy_directive(self) -> None:
        """High arousal must prompt the LLM to lead with empathy."""
        builder = PromptBuilder()
        plan = _make_plan(
            emotion=EmotionSpec(sentiment=-0.7, arousal=0.85, dominant_emotion="distressed")
        )
        prompt, _ = builder.build(plan, None)
        assert "LEAD WITH EMPATHY" in prompt or "Samajh sakti hoon" in prompt

    def test_emotion_neutral_no_empathy_directive(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(
            emotion=EmotionSpec(sentiment=0.1, arousal=0.2, dominant_emotion="neutral")
        )
        prompt, _ = builder.build(plan, None)
        # neutral + low arousal → empathy directive not forced
        assert "LEAD WITH EMPATHY" not in prompt

    def test_slow_pacing_injected_for_low_speaking_rate(self) -> None:
        from src.libs.contracts.response_plan import DeliverySpec
        builder = PromptBuilder()
        plan = _make_plan(
            delivery=DeliverySpec(target_speaking_rate=0.85)
        )
        prompt, _ = builder.build(plan, None)
        assert "SLOW" in prompt

    # ── All intents ───────────────────────────────────────────────────────

    def test_all_intents_injected(self) -> None:
        """All intents (not just top-1) appear in the prompt."""
        builder = PromptBuilder()
        plan = _make_plan(
            intents=(
                IntentSignal(label=IntentLabel.HARDSHIP, confidence=0.82),
                IntentSignal(label=IntentLabel.DISPUTE, confidence=0.71),
                IntentSignal(label=IntentLabel.PAYMENT, confidence=0.45),
            )
        )
        prompt, _ = builder.build(plan, None)
        assert "HARDSHIP" in prompt
        assert "DISPUTE" in prompt
        assert "PAYMENT" in prompt

    def test_intents_ranked_section_label(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(
            intents=(IntentSignal(label=IntentLabel.CALLBACK, confidence=0.9),)
        )
        prompt, _ = builder.build(plan, None)
        assert "CUSTOMER INTENTS" in prompt

    # ── Entities ──────────────────────────────────────────────────────────

    def test_entities_section_present_when_extracted(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(entities={"amount": "5000", "date": "2026-09-15"})
        prompt, _ = builder.build(plan, None)
        assert "ENTITIES EXTRACTED THIS TURN" in prompt
        assert "5000" in prompt
        assert "2026-09-15" in prompt

    def test_entities_empty_shows_none(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(entities={})
        prompt, _ = builder.build(plan, None)
        assert "ENTITIES EXTRACTED THIS TURN: none" in prompt

    def test_entities_echo_back_directive(self) -> None:
        """When entities present, prompt instructs LLM to echo verbatim."""
        builder = PromptBuilder()
        plan = _make_plan(entities={"amount": "10000"})
        prompt, _ = builder.build(plan, None)
        assert "echo" in prompt.lower() or "verbatim" in prompt.lower()

    # ── Risk flags ────────────────────────────────────────────────────────

    def test_risk_flags_section_present(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(
            risk_flags=(
                RiskFlag(
                    flag_id="HARDSHIP_INDICATOR",
                    level=RiskLevel.MEDIUM,
                    description="Financial hardship detected.",
                ),
            )
        )
        prompt, _ = builder.build(plan, None)
        assert "RISK FLAGS" in prompt
        assert "HARDSHIP_INDICATOR" in prompt

    def test_hardship_flag_triggers_ladder_directive(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(
            risk_flags=(
                RiskFlag(
                    flag_id="HARDSHIP_INDICATOR",
                    level=RiskLevel.MEDIUM,
                    description="Hardship detected.",
                ),
            )
        )
        prompt, _ = builder.build(plan, None)
        assert "ladder" in prompt.lower() or "small-win" in prompt.lower()

    def test_dispute_flag_triggers_check_directive(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(
            risk_flags=(
                RiskFlag(
                    flag_id="DISPUTE_CLAIM",
                    level=RiskLevel.MEDIUM,
                    description="Customer disputes the amount.",
                ),
            )
        )
        prompt, _ = builder.build(plan, None)
        assert "dispute" in prompt.lower()

    def test_no_risk_section_when_no_flags(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(risk_flags=())
        prompt, _ = builder.build(plan, None)
        assert "RISK FLAGS" not in prompt

    # ── Policy constraints ────────────────────────────────────────────────

    def test_policy_constraints_injected(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(
            policy_constraints=(
                PolicyConstraint(
                    rule_id="MUST_NOT_THREATEN",
                    description="Do not threaten legal action.",
                    is_hard_rule=True,
                ),
            )
        )
        prompt, _ = builder.build(plan, None)
        assert "ACTIVE POLICY CONSTRAINTS" in prompt
        assert "MUST_NOT_THREATEN" in prompt

    def test_no_policy_section_when_empty(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(policy_constraints=())
        prompt, _ = builder.build(plan, None)
        assert "ACTIVE POLICY CONSTRAINTS" not in prompt

    # ── Strategy rationale ────────────────────────────────────────────────

    def test_strategy_rationale_injected_when_present(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(
            strategy=StrategyAction(
                action=StrategyLabel.REASSURE,
                rationale="Customer distressed, hardship detected.",
            )
        )
        prompt, _ = builder.build(plan, None)
        assert "STRATEGY RATIONALE" in prompt
        assert "distressed" in prompt

    def test_strategy_rationale_absent_when_empty(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(strategy=StrategyAction(action=StrategyLabel.ASK, rationale=""))
        prompt, _ = builder.build(plan, None)
        assert "STRATEGY RATIONALE" not in prompt

    # ── Negotiation envelope extras ───────────────────────────────────────

    def test_negotiation_proposed_amount_injected(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(
            strategy=StrategyAction(action=StrategyLabel.NEGOTIATE),
            negotiation_envelope=NegotiationEnvelope(
                floor_minor=10_000,
                ceiling_minor=50_000,
                move_type=NegotiationMoveType.PARTIAL_PAYMENT,
                proposed_amount_minor=45_000,
            ),
        )
        prompt, _ = builder.build(plan, None)
        assert "proposed amount" in prompt
        assert "₹450" in prompt  # 45000 minor = ₹450

    def test_finalized_commitment_shows_close_directive(self) -> None:
        builder = PromptBuilder()
        plan = _make_plan(
            strategy=StrategyAction(action=StrategyLabel.NEGOTIATE),
            negotiation_envelope=NegotiationEnvelope(
                floor_minor=10_000,
                ceiling_minor=50_000,
                move_type=NegotiationMoveType.FULL_PAYMENT,
                is_finalized_commitment=True,
            ),
        )
        prompt, _ = builder.build(plan, None)
        assert "COMMITMENT FINALIZED" in prompt

    # ── Determinism preserved with new fields ─────────────────────────────

    def test_determinism_with_all_fields(self) -> None:
        """RI-7: same full plan always produces the same hash."""
        builder = PromptBuilder()
        plan = _make_plan(
            emotion=EmotionSpec(sentiment=-0.5, arousal=0.7, dominant_emotion="stressed"),
            intents=(
                IntentSignal(label=IntentLabel.HARDSHIP, confidence=0.8),
                IntentSignal(label=IntentLabel.PAYMENT, confidence=0.4),
            ),
            entities={"amount": "3000", "date": "2026-09-20"},
            risk_flags=(
                RiskFlag(flag_id="HARDSHIP_INDICATOR", level=RiskLevel.MEDIUM, description="Hardship."),
            ),
            policy_constraints=(
                PolicyConstraint(rule_id="MUST_NOT_THREATEN", description="No threats.", is_hard_rule=True),
            ),
            strategy=StrategyAction(action=StrategyLabel.REASSURE, rationale="Customer distressed."),
        )
        _, hash1 = builder.build(plan, None)
        _, hash2 = builder.build(plan, None)
        assert hash1 == hash2
