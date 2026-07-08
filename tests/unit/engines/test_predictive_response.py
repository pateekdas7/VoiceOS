"""Unit tests for PredictiveResponseEngine."""

from __future__ import annotations

import uuid
from datetime import datetime

from src.engines.predictive_response import PredictiveResponseEngine
from src.libs.contracts.response_plan import (
    IntentLabel,
    ResponsePlan,
    StrategyAction,
    StrategyLabel,
)
from src.libs.contracts.streaming import WordHypothesis


def _make_plan(intent_label: IntentLabel = IntentLabel.PAYMENT) -> ResponsePlan:
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id="call-001",
        tenant_id="tenant-001",
        created_at=datetime.utcnow(),
        strategy=StrategyAction(action=StrategyLabel.ASK),
    )


def _make_word(word: str, is_final: bool = False) -> WordHypothesis:
    return WordHypothesis(word=word, confidence=0.95, start_ms=0, end_ms=100, is_final=is_final)


class TestPredictiveResponseEngine:
    def test_predictive_response_cache_hit(self) -> None:  # required named test
        """test_predictive_response_cache_hit: cached plan returned when intent matches."""
        engine = PredictiveResponseEngine()
        # Feed partial words containing payment keyword.
        engine.update_partial(_make_word("pay"))
        engine.update_partial(_make_word("payment"))
        engine.update_partial(_make_word("karna"))
        engine.update_partial(_make_word("chahta"))
        engine.update_partial(_make_word("hoon"))

        # Cache a plan for PAYMENT intent.
        plan = _make_plan(IntentLabel.PAYMENT)
        engine.prime_cache(IntentLabel.PAYMENT, plan)

        # When confirmed intent matches → cache HIT.
        result = engine.get_cached_plan(IntentLabel.PAYMENT)
        assert result is not None
        assert result.plan_id == plan.plan_id

    def test_cache_miss_on_different_intent(self) -> None:
        engine = PredictiveResponseEngine()
        engine.update_partial(_make_word("pay"))
        plan = _make_plan(IntentLabel.PAYMENT)
        engine.prime_cache(IntentLabel.PAYMENT, plan)
        # Confirmed as DISPUTE → miss.
        result = engine.get_cached_plan(IntentLabel.DISPUTE)
        assert result is None

    def test_invalidate_clears_cache(self) -> None:
        engine = PredictiveResponseEngine()
        engine.update_partial(_make_word("payment"))
        plan = _make_plan(IntentLabel.PAYMENT)
        engine.prime_cache(IntentLabel.PAYMENT, plan)
        engine.invalidate()
        result = engine.get_cached_plan(IntentLabel.PAYMENT)
        assert result is None

    def test_reset_allows_new_prediction(self) -> None:
        engine = PredictiveResponseEngine()
        engine.invalidate()
        engine.reset()
        engine.update_partial(_make_word("dispute"))
        # Predict DISPUTE keyword.
        result = engine.get_cached_plan(IntentLabel.DISPUTE)
        assert result is None  # No plan primed yet — just checking no crash.

    def test_no_crash_on_empty_stream(self) -> None:
        engine = PredictiveResponseEngine()
        result = engine.get_cached_plan(IntentLabel.PAYMENT)
        assert result is None
