"""Unit tests for PromptBuilder — determinism (RI-7) and content verification."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from src.engines.prompt_builder import PROMPT_VERSION, PromptBuilder
from src.libs.contracts.response_plan import (
    IntentLabel,
    IntentSignal,
    MustSayItem,
    NegotiationEnvelope,
    NegotiationMoveType,
    ResponsePlan,
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
