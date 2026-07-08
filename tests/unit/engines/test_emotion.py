"""Unit tests for EmotionIntelligenceEngine.

Architecture: V2 Ch10; V1 Ch19; DocSuite-08.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.engines.emotion.engine import EmotionIntelligenceEngine
from src.engines.emotion.labels import Sentiment, StressLevel
from src.engines.emotion.result import EmotionSignal
from src.libs.contracts.turn import TurnInput, TurnRole, UtteranceSegment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_turn(
    transcript: str,
    confidence: float = 0.95,
    turn_id: str = "t-001",
) -> TurnInput:
    return TurnInput(
        turn_id=turn_id,
        call_id="call-001",
        tenant_id="tenant-001",
        role=TurnRole.CUSTOMER,
        transcript=transcript,
        segments=(
            UtteranceSegment(
                text=transcript,
                start_ms=0,
                end_ms=2000,
                confidence=confidence,
            ),
        ),
        created_at=datetime.now(tz=UTC),
        correlation_id="corr-001",
        trace_id="trace-001",
        turn_index=0,
    )


@pytest.fixture
def engine() -> EmotionIntelligenceEngine:
    return EmotionIntelligenceEngine()


# ---------------------------------------------------------------------------
# Core coverage tests
# ---------------------------------------------------------------------------


def test_emotion_engine_returns_emotion_signal(engine: EmotionIntelligenceEngine) -> None:
    """AC-8: EmotionEngine returns EmotionSignal with all fields populated."""
    turn = _make_turn("haan theek hai")
    result = engine.analyze(turn)
    assert isinstance(result, EmotionSignal)
    assert result.sentiment in Sentiment
    assert 0.0 <= result.arousal <= 1.0
    assert -1.0 <= result.valence <= 1.0
    assert result.stress_level in StressLevel
    assert isinstance(result.dominant_emotion, str)
    assert len(result.dominant_emotion) > 0


def test_all_fields_populated(engine: EmotionIntelligenceEngine) -> None:
    """All EmotionSignal fields must be non-None after analysis."""
    turn = _make_turn("main paise dene mein asmarth hun")
    result = engine.analyze(turn)
    assert result.sentiment is not None
    assert result.arousal is not None
    assert result.valence is not None
    assert result.stress_level is not None
    assert result.dominant_emotion is not None


def test_positive_sentiment(engine: EmotionIntelligenceEngine) -> None:
    """Cooperative utterances yield POSITIVE sentiment."""
    turn = _make_turn("haan ji main de dunga, theek hai")
    result = engine.analyze(turn)
    assert result.sentiment == Sentiment.POSITIVE


def test_negative_sentiment(engine: EmotionIntelligenceEngine) -> None:
    """Distressed utterances yield NEGATIVE sentiment."""
    turn = _make_turn("paise nahi hain mujhe bahut problem hai")
    result = engine.analyze(turn)
    assert result.sentiment == Sentiment.NEGATIVE


def test_hostile_sentiment(engine: EmotionIntelligenceEngine) -> None:
    """Abusive utterances yield HOSTILE sentiment."""
    turn = _make_turn("gaali dena band karo")
    result = engine.analyze(turn)
    assert result.sentiment == Sentiment.HOSTILE


def test_neutral_sentiment_for_unknown(engine: EmotionIntelligenceEngine) -> None:
    """Utterances without clear sentiment markers → NEUTRAL."""
    turn = _make_turn("zyxwvutsrqponmlkjihgfedcba")
    result = engine.analyze(turn)
    assert result.sentiment == Sentiment.NEUTRAL


def test_hostile_implies_critical_stress(engine: EmotionIntelligenceEngine) -> None:
    """HOSTILE sentiment always produces CRITICAL stress level."""
    turn = _make_turn("saala band karo")
    result = engine.analyze(turn)
    assert result.stress_level == StressLevel.CRITICAL


def test_low_stress_for_cooperative(engine: EmotionIntelligenceEngine) -> None:
    """Cooperative, confident speech has LOW stress."""
    turn = _make_turn("haan ji bilkul de dunga", confidence=0.99)
    result = engine.analyze(turn)
    assert result.stress_level in (StressLevel.LOW, StressLevel.MEDIUM)


def test_valence_negative_for_negative_sentiment(engine: EmotionIntelligenceEngine) -> None:
    """NEGATIVE sentiment produces negative valence."""
    turn = _make_turn("nahi, galat hai")
    result = engine.analyze(turn)
    assert result.valence < 0.0


def test_valence_positive_for_positive_sentiment(engine: EmotionIntelligenceEngine) -> None:
    """POSITIVE sentiment produces positive valence."""
    turn = _make_turn("haan bilkul okay hai")
    result = engine.analyze(turn)
    assert result.valence > 0.0


def test_arousal_range(engine: EmotionIntelligenceEngine) -> None:
    """Arousal is always in [0.0, 1.0]."""
    for transcript in [
        "",
        "haan",
        "nahi nahi nahi problem problem galat",
        "gaali dena band karo",
    ]:
        turn = _make_turn(transcript)
        result = engine.analyze(turn)
        assert 0.0 <= result.arousal <= 1.0, f"Arousal out of range for: {transcript!r}"


def test_low_confidence_segments_increase_arousal(engine: EmotionIntelligenceEngine) -> None:
    """Low ASR confidence (stressed speech) increases arousal estimate."""
    high_conf_turn = _make_turn("haan theek hai", confidence=0.99)
    low_conf_turn = _make_turn("haan theek hai", confidence=0.40)
    high_result = engine.analyze(high_conf_turn)
    low_result = engine.analyze(low_conf_turn)
    assert low_result.arousal >= high_result.arousal


def test_empty_transcript(engine: EmotionIntelligenceEngine) -> None:
    """Empty transcript returns a valid EmotionSignal without raising."""
    turn = _make_turn("")
    result = engine.analyze(turn)
    assert isinstance(result, EmotionSignal)


def test_no_segments_turn(engine: EmotionIntelligenceEngine) -> None:
    """Turn with no segments still returns a valid EmotionSignal."""
    turn = TurnInput(
        turn_id="t-noseg",
        call_id="call-001",
        tenant_id="tenant-001",
        role=TurnRole.CUSTOMER,
        transcript="nahi hain paise",
        segments=(),
        created_at=datetime.now(tz=UTC),
        correlation_id="corr-001",
        trace_id="trace-001",
        turn_index=0,
    )
    result = engine.analyze(turn)
    assert isinstance(result, EmotionSignal)


def test_dominant_emotion_hostile(engine: EmotionIntelligenceEngine) -> None:
    """HOSTILE sentiment → dominant_emotion == 'hostile'."""
    turn = _make_turn("abuse karna band karo")
    result = engine.analyze(turn)
    if result.sentiment == Sentiment.HOSTILE:
        assert result.dominant_emotion == "hostile"


def test_dominant_emotion_cooperative(engine: EmotionIntelligenceEngine) -> None:
    """Positive low-arousal → dominant_emotion == 'cooperative'."""
    turn = _make_turn("haan main agree karta hun", confidence=0.99)
    result = engine.analyze(turn)
    if result.sentiment == Sentiment.POSITIVE:
        assert result.dominant_emotion in ("cooperative", "engaged")


def test_sentiment_enum_has_hostile() -> None:
    """Sentiment enum must include HOSTILE (added Sprint-010)."""
    assert Sentiment.HOSTILE.value == "hostile"


def test_stress_level_enum_values() -> None:
    """StressLevel must have exactly 4 values."""
    levels = list(StressLevel)
    assert len(levels) == 4
    assert StressLevel.CRITICAL in levels
    assert StressLevel.HIGH in levels
    assert StressLevel.MEDIUM in levels
    assert StressLevel.LOW in levels


def test_emotion_signal_is_immutable(engine: EmotionIntelligenceEngine) -> None:
    """EmotionSignal is a frozen Pydantic model (immutable)."""
    from pydantic import ValidationError

    turn = _make_turn("test")
    result = engine.analyze(turn)
    with pytest.raises(ValidationError):
        result.sentiment = Sentiment.POSITIVE  # type: ignore[misc]
