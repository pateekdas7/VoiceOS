"""Unit tests for IntentEngine and IntentModel.

Required named tests (per Sprint-010 spec):
  - test_intent_all_labels
  - test_intent_latency_benchmark

Architecture: V2 Ch3; DocSuite-08.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from src.engines.intent.engine import IntentEngine
from src.engines.intent.labels import IntentLabel, IntentSignal
from src.engines.intent.model import IntentModel
from src.engines.intent.result import IntentResult
from src.libs.contracts.turn import TurnInput, TurnRole, UtteranceSegment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_turn(transcript: str, turn_id: str = "t-001") -> TurnInput:
    return TurnInput(
        turn_id=turn_id,
        call_id="call-001",
        tenant_id="tenant-001",
        role=TurnRole.CUSTOMER,
        transcript=transcript,
        segments=(UtteranceSegment(text=transcript, start_ms=0, end_ms=1000, confidence=0.95),),
        created_at=datetime.now(tz=UTC),
        correlation_id="corr-001",
        trace_id="trace-001",
        turn_index=0,
    )


def _engine_for_label(label: IntentLabel) -> IntentEngine:
    """Build an IntentEngine whose mock model returns the given label as top."""
    label_order = list(IntentLabel)
    scores = [0.0] * 13
    idx = label_order.index(label)
    scores[idx] = 10.0
    model = IntentModel.from_mock(scores=scores)
    return IntentEngine(model=model)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def keyword_engine() -> IntentEngine:
    """IntentEngine using keyword-based classification (no ONNX file)."""
    return IntentEngine(model=IntentModel())


# ---------------------------------------------------------------------------
# Required named tests
# ---------------------------------------------------------------------------


def test_intent_all_labels() -> None:
    """Verify each of the 13 intent labels can be classified correctly.

    Uses injected mock scores to guarantee the expected label wins.
    """
    for label in IntentLabel:
        engine = _engine_for_label(label)
        turn = _make_turn(transcript="some utterance", turn_id=f"t-{label.value}")
        result = engine.classify(turn)
        assert isinstance(result, IntentResult)
        assert result.label == label, f"Expected {label}, got {result.label}"
        assert 0.0 <= result.confidence <= 1.0
        assert len(result.raw_scores) == 13


def test_intent_latency_benchmark() -> None:
    """100 classifications must complete in < 3 seconds total (p99 < 30ms).

    Uses mock model to measure engine overhead only.
    """
    model = IntentModel.from_mock()
    engine = IntentEngine(model=model)
    turn = _make_turn("main ab paise de dunga")

    start = time.perf_counter()
    for _ in range(100):
        engine.classify(turn)
    elapsed = time.perf_counter() - start

    assert elapsed < 3.0, f"100 classifications took {elapsed:.3f}s (limit: 3.0s)"


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------


def test_classify_returns_intent_result() -> None:
    model = IntentModel.from_mock()
    engine = IntentEngine(model=model)
    turn = _make_turn("hello")
    result = engine.classify(turn)
    assert isinstance(result, IntentResult)


def test_classify_to_signal_round_trip() -> None:
    """IntentResult.to_signal() produces a valid IntentSignal."""
    model = IntentModel.from_mock()
    engine = IntentEngine(model=model)
    turn = _make_turn("test")
    result = engine.classify(turn)
    signal = result.to_signal()
    assert isinstance(signal, IntentSignal)
    assert signal.label == result.label
    assert signal.confidence == result.confidence


def test_classify_empty_transcript() -> None:
    """Empty transcript returns a valid result (OTHER or SILENCE)."""
    model = IntentModel.from_mock()
    engine = IntentEngine(model=model)
    turn = _make_turn("")
    result = engine.classify(turn)
    assert result.label in IntentLabel
    assert 0.0 <= result.confidence <= 1.0


def test_classify_preserves_call_context() -> None:
    """Result is tied to the turn's call context (fields available)."""
    model = IntentModel.from_mock()
    engine = IntentEngine(model=model)
    turn = _make_turn("main kal de dunga", turn_id="t-ctx")
    result = engine.classify(turn)
    assert result.label in IntentLabel
    # source_span should be a non-empty substring of transcript
    assert result.source_span


def test_model_from_mock_default_uniform() -> None:
    """from_mock() with no args returns uniform distribution."""
    model = IntentModel.from_mock()
    scores = model.infer("anything")
    assert len(scores) == 13
    assert all(isinstance(s, float) for s in scores)


def test_model_from_mock_with_scores() -> None:
    """from_mock(scores) returns exactly those scores."""
    custom = [float(i) for i in range(13)]
    model = IntentModel.from_mock(scores=custom)
    result = model.infer("test")
    assert result == custom


def test_keyword_mode_payment() -> None:
    """Keyword mode: 'payment' triggers PAYMENT label."""
    engine = IntentEngine(model=IntentModel())
    turn = _make_turn("I want to make a payment today")
    result = engine.classify(turn)
    assert result.label == IntentLabel.PAYMENT


def test_keyword_mode_promise_to_pay() -> None:
    """Keyword mode: 'de dunga' triggers PROMISE_TO_PAY."""
    engine = IntentEngine(model=IntentModel())
    turn = _make_turn("haan main kal de dunga")
    result = engine.classify(turn)
    assert result.label == IntentLabel.PROMISE_TO_PAY


def test_keyword_mode_disconnect() -> None:
    """Keyword mode: 'disconnect' triggers DISCONNECT."""
    engine = IntentEngine(model=IntentModel())
    turn = _make_turn("please disconnect the call")
    result = engine.classify(turn)
    assert result.label == IntentLabel.DISCONNECT


def test_keyword_mode_silence() -> None:
    """Keyword mode: empty transcript triggers SILENCE."""
    engine = IntentEngine(model=IntentModel())
    turn = _make_turn("")
    result = engine.classify(turn)
    assert result.label == IntentLabel.SILENCE


def test_keyword_mode_dispute() -> None:
    """Keyword mode: 'galat' triggers DISPUTE."""
    engine = IntentEngine(model=IntentModel())
    turn = _make_turn("yeh galat hai main nahi manta")
    result = engine.classify(turn)
    assert result.label == IntentLabel.DISPUTE


def test_keyword_mode_other_fallback() -> None:
    """Keyword mode: unmatched text returns OTHER."""
    engine = IntentEngine(model=IntentModel())
    turn = _make_turn("zyxwvutsrqponmlkjihgfedcba")
    result = engine.classify(turn)
    assert result.label == IntentLabel.OTHER


def test_raw_scores_length() -> None:
    """Raw scores tuple always has exactly 13 elements."""
    model = IntentModel.from_mock()
    engine = IntentEngine(model=model)
    turn = _make_turn("test")
    result = engine.classify(turn)
    assert len(result.raw_scores) == 13


def test_confidence_sums_to_approx_one() -> None:
    """Softmax probabilities across all labels sum to ~1.0."""
    model = IntentModel.from_mock([float(i) for i in range(13)])
    engine = IntentEngine(model=model)
    turn = _make_turn("test")
    result = engine.classify(turn)
    total = sum(result.raw_scores)
    assert abs(total - 1.0) < 1e-5, f"Probabilities sum to {total}, expected ~1.0"


def test_intent_label_enum_has_13_members() -> None:
    """IntentLabel must define exactly 13 members (V2 Ch3)."""
    assert len(list(IntentLabel)) == 13


def test_classify_multiturn_stability() -> None:
    """Engine produces consistent results across multiple turns."""
    model = IntentModel.from_mock()
    engine = IntentEngine(model=model)
    results = [engine.classify(_make_turn("test", turn_id=f"t-{i}")) for i in range(5)]
    labels = {r.label for r in results}
    # All same label (deterministic mock)
    assert len(labels) == 1
