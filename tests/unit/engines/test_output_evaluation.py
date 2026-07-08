"""Unit tests for OutputEvaluationEngine."""

from __future__ import annotations

import uuid
from datetime import datetime

from src.engines.output_evaluation import OutputEvaluationEngine, TurnQualityScore
from src.libs.contracts.response_plan import (
    FactMap,
    MustNotSayItem,
    ResponsePlan,
    StrategyAction,
    StrategyLabel,
)
from src.libs.contracts.turn import TurnInput, TurnRole


def _make_turn(transcript: str = "main payment karna chahta hoon") -> TurnInput:
    return TurnInput(
        turn_id=str(uuid.uuid4()),
        call_id="call-001",
        tenant_id="tenant-001",
        role=TurnRole.CUSTOMER,
        transcript=transcript,
        segments=(),
        created_at=datetime.utcnow(),
        correlation_id="corr-001",
        trace_id="trace-001",
        turn_index=0,
    )


def _make_plan(
    facts: FactMap | None = None,
    must_not_say: tuple[MustNotSayItem, ...] = (),
) -> ResponsePlan:
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id="call-001",
        tenant_id="tenant-001",
        created_at=datetime.utcnow(),
        strategy=StrategyAction(action=StrategyLabel.ASK),
        facts=facts or {},
        must_not_say=must_not_say,
    )


class TestOutputEvaluationEngine:
    def test_scores_non_empty_response(self) -> None:
        engine = OutputEvaluationEngine()
        turn = _make_turn()
        plan = _make_plan()
        score = engine.score(turn, "Aapki baat samajh mein aayi. Aaj payment karein.", plan)
        assert isinstance(score, TurnQualityScore)
        assert 0.0 <= score.coherence <= 1.0
        assert 0.0 <= score.policy_compliance <= 1.0
        assert 0.0 <= score.empathy <= 1.0
        assert 0.0 <= score.factual_accuracy <= 1.0

    def test_empty_output_coherence_is_zero(self) -> None:
        engine = OutputEvaluationEngine()
        score = engine.score(_make_turn(), "", _make_plan())
        assert score.coherence == 0.0

    def test_prohibited_phrase_lowers_policy_score(self) -> None:
        engine = OutputEvaluationEngine()
        plan = _make_plan()
        score = engine.score(_make_turn(), "We will take you to court!", plan)
        assert score.policy_compliance < 1.0

    def test_must_not_say_pattern_lowers_policy_score(self) -> None:
        engine = OutputEvaluationEngine()
        plan = _make_plan(
            must_not_say=(
                MustNotSayItem(
                    item_id="NO_THREATS",
                    description="no threats",
                    pattern=r"\bthreat\b",
                ),
            )
        )
        score = engine.score(_make_turn(), "This is a threat to you.", plan)
        assert score.policy_compliance < 1.0

    def test_factual_amount_within_tolerance_scores_high(self) -> None:
        engine = OutputEvaluationEngine()
        plan = _make_plan(facts={"outstanding_balance_minor": 50_000})  # ₹500
        score = engine.score(_make_turn(), "Aapka outstanding ₹500 hai.", plan)
        assert score.factual_accuracy == 1.0

    def test_factual_amount_deviation_scores_low(self) -> None:
        engine = OutputEvaluationEngine()
        plan = _make_plan(facts={"outstanding_balance_minor": 50_000})  # ₹500
        # Output says ₹5000 — 10x deviation → score drops.
        score = engine.score(_make_turn(), "Aapka outstanding ₹5000 hai.", plan)
        assert score.factual_accuracy < 1.0

    def test_turn_id_in_score(self) -> None:
        engine = OutputEvaluationEngine()
        turn = _make_turn()
        score = engine.score(turn, "Theek hai, main help karunga.", _make_plan())
        assert score.turn_id == turn.turn_id
