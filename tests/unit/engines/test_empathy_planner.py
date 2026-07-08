"""Unit tests for EmpathyPlanner.

Covers required named tests, tone/pacing rules, language register selection,
acknowledgment phrases, and acceptance criteria.
Architecture: V2 Ch14.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.engines.empathy.engine import EmpathyPlanner
from src.libs.contracts.streaming import (
    EmpathyConfig,
    LanguageRegister,
    Pacing,
    Sentiment,
    StressLevel,
    Tone,
)


@pytest.fixture
def planner() -> EmpathyPlanner:
    return EmpathyPlanner()


# ---------------------------------------------------------------------------
# Required named test (AC)
# ---------------------------------------------------------------------------


def test_empathy_high_stress_slow_pacing(planner: EmpathyPlanner) -> None:
    """StressLevel.HIGH → pacing=SLOW, tone=EMPATHETIC."""
    config = planner.plan(
        stress_level=StressLevel.HIGH,
        sentiment=Sentiment.NEGATIVE,
    )
    assert config.pacing == Pacing.SLOW
    assert config.tone == Tone.EMPATHETIC


# ---------------------------------------------------------------------------
# Stress-level rules
# ---------------------------------------------------------------------------


def test_critical_stress_empathetic_slow(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.CRITICAL, Sentiment.HOSTILE)
    assert config.tone == Tone.EMPATHETIC
    assert config.pacing == Pacing.SLOW


def test_high_stress_empathetic_slow(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.HIGH, Sentiment.NEGATIVE)
    assert config.tone == Tone.EMPATHETIC
    assert config.pacing == Pacing.SLOW


def test_medium_stress_reassuring_normal(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.MEDIUM, Sentiment.NEUTRAL)
    assert config.tone == Tone.REASSURING
    assert config.pacing == Pacing.NORMAL


def test_low_stress_positive_sentiment_empathetic(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.LOW, Sentiment.POSITIVE)
    assert config.tone == Tone.EMPATHETIC
    assert config.pacing == Pacing.NORMAL


def test_low_stress_neutral_sentiment_neutral(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.LOW, Sentiment.NEUTRAL)
    assert config.tone == Tone.NEUTRAL
    assert config.pacing == Pacing.NORMAL


def test_low_stress_negative_sentiment_reassuring(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.LOW, Sentiment.NEGATIVE)
    assert config.tone == Tone.REASSURING
    assert config.pacing == Pacing.NORMAL


def test_low_stress_hostile_sentiment_firm_slow(planner: EmpathyPlanner) -> None:
    """Low stress + hostile sentiment (edge case) → FIRM + SLOW."""
    config = planner.plan(StressLevel.LOW, Sentiment.HOSTILE)
    assert config.tone == Tone.FIRM
    assert config.pacing == Pacing.SLOW


# ---------------------------------------------------------------------------
# Acknowledgment phrase rules
# ---------------------------------------------------------------------------


def test_critical_stress_has_acknowledgment(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.CRITICAL, Sentiment.HOSTILE)
    assert config.acknowledgment_phrase is not None
    assert len(config.acknowledgment_phrase) > 0


def test_high_stress_has_acknowledgment(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.HIGH, Sentiment.NEGATIVE)
    assert config.acknowledgment_phrase is not None
    assert len(config.acknowledgment_phrase) > 0


def test_medium_stress_has_acknowledgment(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.MEDIUM, Sentiment.NEUTRAL)
    assert config.acknowledgment_phrase is not None


def test_low_stress_no_acknowledgment(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.LOW, Sentiment.NEUTRAL)
    assert config.acknowledgment_phrase is None


# ---------------------------------------------------------------------------
# Language register selection
# ---------------------------------------------------------------------------


def test_hindi_language_gives_colloquial(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.LOW, Sentiment.NEUTRAL, preferred_language="hi-IN")
    assert config.language_register == LanguageRegister.COLLOQUIAL


def test_english_language_gives_formal(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.LOW, Sentiment.NEUTRAL, preferred_language="en-IN")
    assert config.language_register == LanguageRegister.FORMAL


def test_mixed_language_gives_semi_formal(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.LOW, Sentiment.NEUTRAL, preferred_language="other")
    assert config.language_register == LanguageRegister.SEMI_FORMAL


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------


def test_returns_empathy_config(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.MEDIUM, Sentiment.NEUTRAL)
    assert isinstance(config, EmpathyConfig)


def test_all_fields_populated(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.HIGH, Sentiment.NEGATIVE, preferred_language="hi-IN")
    assert isinstance(config.tone, Tone)
    assert isinstance(config.pacing, Pacing)
    assert isinstance(config.language_register, LanguageRegister)


def test_config_is_frozen(planner: EmpathyPlanner) -> None:
    config = planner.plan(StressLevel.LOW, Sentiment.NEUTRAL)
    with pytest.raises(ValidationError):
        config.tone = Tone.FIRM  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_deterministic_same_inputs(planner: EmpathyPlanner) -> None:
    kwargs = {
        "stress_level": StressLevel.HIGH,
        "sentiment": Sentiment.NEGATIVE,
        "preferred_language": "hi-IN",
    }
    c1 = planner.plan(**kwargs)  # type: ignore[arg-type]
    c2 = planner.plan(**kwargs)  # type: ignore[arg-type]
    assert c1.tone == c2.tone
    assert c1.pacing == c2.pacing
    assert c1.language_register == c2.language_register
    assert c1.acknowledgment_phrase == c2.acknowledgment_phrase


# ---------------------------------------------------------------------------
# All stress levels produce valid configs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stress,sentiment",
    [
        (StressLevel.LOW, Sentiment.NEUTRAL),
        (StressLevel.LOW, Sentiment.POSITIVE),
        (StressLevel.LOW, Sentiment.NEGATIVE),
        (StressLevel.LOW, Sentiment.HOSTILE),
        (StressLevel.MEDIUM, Sentiment.NEUTRAL),
        (StressLevel.MEDIUM, Sentiment.NEGATIVE),
        (StressLevel.HIGH, Sentiment.NEGATIVE),
        (StressLevel.CRITICAL, Sentiment.HOSTILE),
    ],
)
def test_all_stress_sentiment_combinations(
    planner: EmpathyPlanner,
    stress: StressLevel,
    sentiment: Sentiment,
) -> None:
    config = planner.plan(stress, sentiment)
    assert isinstance(config, EmpathyConfig)
    assert config.tone in list(Tone)
    assert config.pacing in list(Pacing)
