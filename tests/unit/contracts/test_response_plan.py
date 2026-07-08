"""Unit tests for src/libs/contracts/response_plan.py.

Tests: ResponsePlan creation with valid fields, immutability (mutation raises),
       JSON serialisation round-trip, schema validation rejects missing required
       fields, NegotiationEnvelope and sub-type coverage.

AC-1, AC-7: ResponsePlan is immutable, fully typed, passes mypy --strict.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from src.libs.contracts.response_plan import (
    DeliverySpec,
    EmotionSpec,
    FactMap,
    IntentLabel,
    IntentSignal,
    MustNotSayItem,
    MustSayItem,
    NegotiationEnvelope,
    NegotiationMoveType,
    PolicyConstraint,
    ResponsePlan,
    RetrievalResult,
    RiskFlag,
    RiskLevel,
    Snippet,
    StrategyAction,
    StrategyLabel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_plan(**overrides: object) -> ResponsePlan:
    defaults: dict[str, object] = {
        "plan_id": "plan-001",
        "version": 1,
        "call_id": "call-abc",
        "tenant_id": "tenant-xyz",
        "created_at": datetime(2026, 6, 30, 10, 0, 0),
    }
    defaults.update(overrides)
    return ResponsePlan(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Basic construction
# ---------------------------------------------------------------------------


class TestResponsePlanCreation:
    def test_minimal_plan(self) -> None:
        plan = make_plan()
        assert plan.plan_id == "plan-001"
        assert plan.version == 1
        assert plan.call_id == "call-abc"
        assert plan.tenant_id == "tenant-xyz"

    def test_defaults_populated(self) -> None:
        plan = make_plan()
        assert plan.intents == ()
        assert plan.entities == {}
        assert plan.retrieval == []
        assert plan.must_say == ()
        assert plan.must_not_say == ()
        assert plan.policy_constraints == ()
        assert plan.risk_flags == ()

    def test_version_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            make_plan(version=0)

    def test_with_intent_signals(self) -> None:
        intents = (IntentSignal(label=IntentLabel.PAYMENT, confidence=0.92),)
        plan = make_plan(intents=intents)
        assert len(plan.intents) == 1
        assert plan.intents[0].label == IntentLabel.PAYMENT

    def test_with_emotion_spec(self) -> None:
        emotion = EmotionSpec(sentiment=0.3, arousal=0.8, dominant_emotion="anxious")
        plan = make_plan(emotion=emotion)
        assert plan.emotion.sentiment == 0.3
        assert plan.emotion.arousal == 0.8

    def test_with_negotiation_envelope(self) -> None:
        env = NegotiationEnvelope(
            floor_minor=100000,
            ceiling_minor=500000,
            move_type=NegotiationMoveType.PARTIAL_PAYMENT,
        )
        plan = make_plan(negotiation_envelope=env)
        assert plan.negotiation_envelope is not None
        assert plan.negotiation_envelope.floor_minor == 100000

    def test_with_must_say_and_must_not_say(self) -> None:
        must_say = (MustSayItem(item_id="RBI-001", text="This call is recorded."),)
        must_not = (MustNotSayItem(item_id="NO-THREAT", description="No threats of legal action"),)
        plan = make_plan(must_say=must_say, must_not_say=must_not)
        assert len(plan.must_say) == 1
        assert plan.must_say[0].item_id == "RBI-001"
        assert len(plan.must_not_say) == 1

    def test_with_policy_constraints(self) -> None:
        constraints = (PolicyConstraint(rule_id="RBI-FC-001", description="RBI Fair Practice"),)
        plan = make_plan(policy_constraints=constraints)
        assert len(plan.policy_constraints) == 1

    def test_with_risk_flags(self) -> None:
        flags = (RiskFlag(flag_id="ABUSE_DETECTED", level=RiskLevel.HIGH, description="Abusive language"),)
        plan = make_plan(risk_flags=flags)
        assert len(plan.risk_flags) == 1
        assert plan.risk_flags[0].level == RiskLevel.HIGH

    def test_with_retrieval_snippets(self) -> None:
        snippets = [Snippet(source="faq_v3", content="EMI info", relevance_score=0.85)]
        plan = make_plan(retrieval=snippets)
        assert len(plan.retrieval) == 1
        assert plan.retrieval[0].source == "faq_v3"

    def test_with_facts(self) -> None:
        facts: FactMap = {"outstanding_balance": 500000, "dpd": 45}
        plan = make_plan(facts=facts)
        assert plan.facts["outstanding_balance"] == 500000

    def test_with_goal_and_strategy(self) -> None:
        strategy = StrategyAction(action=StrategyLabel.NEGOTIATE, rationale="Customer willing")
        plan = make_plan(goal="Secure partial payment", strategy=strategy)
        assert plan.goal == "Secure partial payment"
        assert plan.strategy.action == StrategyLabel.NEGOTIATE

    def test_with_delivery_spec(self) -> None:
        delivery = DeliverySpec(language="en-IN", voice_id="veena-english", target_speaking_rate=0.9)
        plan = make_plan(delivery=delivery)
        assert plan.delivery.language == "en-IN"


# ---------------------------------------------------------------------------
# Immutability (AC-7)  # noqa: ERA001
# ---------------------------------------------------------------------------


class TestResponsePlanImmutability:
    def test_plan_id_mutation_raises(self) -> None:
        plan = make_plan()
        with pytest.raises(ValidationError):
            plan.plan_id = "hacked"  # type: ignore[misc]

    def test_version_mutation_raises(self) -> None:
        plan = make_plan()
        with pytest.raises(ValidationError):
            plan.version = 99  # type: ignore[misc]

    def test_tenant_id_mutation_raises(self) -> None:
        plan = make_plan()
        with pytest.raises(ValidationError):
            plan.tenant_id = "other-tenant"  # type: ignore[misc]

    def test_call_id_mutation_raises(self) -> None:
        plan = make_plan()
        with pytest.raises(ValidationError):
            plan.call_id = "different-call"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------


class TestResponsePlanSerialization:
    def test_json_round_trip_minimal(self) -> None:
        plan = make_plan()
        j = plan.model_dump_json()
        plan2 = ResponsePlan.model_validate_json(j)
        assert plan2.plan_id == plan.plan_id
        assert plan2.version == plan.version
        assert plan2.tenant_id == plan.tenant_id

    def test_json_contains_expected_keys(self) -> None:
        plan = make_plan()
        data = json.loads(plan.model_dump_json())
        assert "plan_id" in data
        assert "version" in data
        assert "call_id" in data
        assert "tenant_id" in data
        assert "created_at" in data

    def test_json_round_trip_with_nested_types(self) -> None:
        intents = (IntentSignal(label=IntentLabel.DISPUTE, confidence=0.75),)
        emotion = EmotionSpec(sentiment=-0.4, arousal=0.7)
        plan = make_plan(intents=intents, emotion=emotion)
        plan2 = ResponsePlan.model_validate_json(plan.model_dump_json())
        assert plan2.intents[0].label == IntentLabel.DISPUTE
        assert plan2.emotion.sentiment == pytest.approx(-0.4)


# ---------------------------------------------------------------------------
# Sub-type edge cases
# ---------------------------------------------------------------------------


class TestSubTypes:
    def test_negotiation_envelope_floor_ceiling(self) -> None:
        env = NegotiationEnvelope(
            floor_minor=50000,
            ceiling_minor=200000,
            move_type=NegotiationMoveType.SETTLEMENT,
            proposed_amount_minor=100000,
        )
        assert env.floor_minor == 50000
        assert env.ceiling_minor == 200000
        assert env.proposed_amount_minor == 100000

    def test_strategy_all_labels(self) -> None:
        for label in StrategyLabel:
            sa = StrategyAction(action=label)
            assert sa.action == label

    def test_retrieval_result_alias(self) -> None:
        """RetrievalResult is an alias for Snippet — same class."""
        assert RetrievalResult is Snippet

    def test_delivery_spec_defaults(self) -> None:
        d = DeliverySpec()
        assert d.language == "hi-IN"
        assert d.voice_id == "veena-default"
        assert d.target_speaking_rate == pytest.approx(1.0)

    def test_risk_levels(self) -> None:
        for level in RiskLevel:
            rf = RiskFlag(flag_id=f"FLAG-{level.value}", level=level, description="test")
            assert rf.level == level

    def test_intent_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            IntentSignal(label=IntentLabel.PAYMENT, confidence=1.1)
        with pytest.raises(ValidationError):
            IntentSignal(label=IntentLabel.PAYMENT, confidence=-0.1)

    def test_emotion_spec_bounds(self) -> None:
        with pytest.raises(ValidationError):
            EmotionSpec(sentiment=1.5, arousal=0.5)
        with pytest.raises(ValidationError):
            EmotionSpec(sentiment=0.0, arousal=-0.1)
