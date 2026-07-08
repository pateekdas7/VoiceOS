"""Unit tests for Sprint-009 AdaptiveProsodyEngine.

Architecture: V1 Ch20; Sprint-009 acceptance criteria.
"""

from __future__ import annotations

import pytest

from src.engines.prosody.engine import AdaptiveProsodyEngine
from src.libs.contracts.streaming import (
    EmpathyConfig,
    LanguageRegister,
    Pacing,
    Tone,
    VoiceConfig,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> AdaptiveProsodyEngine:
    return AdaptiveProsodyEngine()


# ---------------------------------------------------------------------------
# test_prosody_high_distress_mapping (required AC test)
# ---------------------------------------------------------------------------


def test_prosody_high_distress_mapping(engine: AdaptiveProsodyEngine) -> None:
    """EMPATHETIC+SLOW maps to rate_scale=0.85 and pause_ms=350.

    Sprint-009: 'HIGH_DISTRESS → rate_scale=0.85, pause_ms=350 (with language=en)'.
    Note: EmpathyConfig has tone+pacing, not an 'emotion' field.
    HIGH_DISTRESS = EMPATHETIC tone + SLOW pacing.
    Using language='en' to avoid Hindi language modifier.
    """
    empathy = EmpathyConfig(
        tone=Tone.EMPATHETIC,
        pacing=Pacing.SLOW,
        language_register=LanguageRegister.SEMI_FORMAL,
    )
    vc = engine.translate(empathy, language="en")

    assert isinstance(vc, VoiceConfig)
    assert vc.rate_scale == pytest.approx(0.85, abs=1e-4), (
        f"Expected rate_scale=0.85 for HIGH_DISTRESS (EMPATHETIC+SLOW), got {vc.rate_scale}"
    )
    assert vc.pause_ms_after_clause == 350, f"Expected pause_ms=350 for HIGH_DISTRESS, got {vc.pause_ms_after_clause}"
    assert vc.pitch_shift == pytest.approx(-0.5, abs=1e-4)


# ---------------------------------------------------------------------------
# test_prosody_neutral_mapping (required AC test)
# ---------------------------------------------------------------------------


def test_prosody_neutral_mapping(engine: AdaptiveProsodyEngine) -> None:
    """NEUTRAL+NORMAL maps to rate_scale=1.0 and pause_ms=150.

    Sprint-009: 'NEUTRAL → rate_scale=1.0, pause_ms=150 (with language=en)'.
    """
    empathy = EmpathyConfig(
        tone=Tone.NEUTRAL,
        pacing=Pacing.NORMAL,
        language_register=LanguageRegister.FORMAL,
    )
    vc = engine.translate(empathy, language="en")

    assert vc.rate_scale == pytest.approx(1.0, abs=1e-4)
    assert vc.pause_ms_after_clause == 150
    assert vc.pitch_shift == pytest.approx(0.0, abs=1e-4)


# ---------------------------------------------------------------------------
# test_prosody_language_modifier (required AC test)
# ---------------------------------------------------------------------------


def test_prosody_language_modifier_hindi(engine: AdaptiveProsodyEngine) -> None:
    """Hindi language modifier reduces rate_scale by 3%.

    Sprint-009: 'Hindi language reduces rate_scale'.
    """
    empathy = EmpathyConfig(
        tone=Tone.NEUTRAL,
        pacing=Pacing.NORMAL,
        language_register=LanguageRegister.FORMAL,
    )
    vc_en = engine.translate(empathy, language="en")
    vc_hi = engine.translate(empathy, language="hi")

    # Hindi modifier is 0.97 so rate_scale should be lower for Hindi
    assert vc_hi.rate_scale < vc_en.rate_scale
    assert vc_hi.rate_scale == pytest.approx(1.0 * 0.97, abs=1e-4)


def test_prosody_language_modifier_hi_in(engine: AdaptiveProsodyEngine) -> None:
    """'hi-IN' applies the same modifier as 'hi' (0.97)."""
    empathy = EmpathyConfig(
        tone=Tone.NEUTRAL,
        pacing=Pacing.NORMAL,
        language_register=LanguageRegister.FORMAL,
    )
    vc = engine.translate(empathy, language="hi-IN")
    assert vc.rate_scale == pytest.approx(1.0 * 0.97, abs=1e-4)


def test_prosody_language_modifier_hinglish(engine: AdaptiveProsodyEngine) -> None:
    """'hi-en' applies a 0.98 modifier (code-switch safe)."""
    empathy = EmpathyConfig(
        tone=Tone.NEUTRAL,
        pacing=Pacing.NORMAL,
        language_register=LanguageRegister.SEMI_FORMAL,
    )
    vc = engine.translate(empathy, language="hi-en")
    assert vc.rate_scale == pytest.approx(1.0 * 0.98, abs=1e-4)


# ---------------------------------------------------------------------------
# test_prosody_voice_config_passthrough (required AC test)
# ---------------------------------------------------------------------------


def test_prosody_voice_config_passthrough(engine: AdaptiveProsodyEngine) -> None:
    """translate() returns a VoiceConfig with correct language field.

    Sprint-009: 'language is passed through to VoiceConfig'.
    """
    empathy = EmpathyConfig(
        tone=Tone.FRIENDLY,
        pacing=Pacing.FAST,
        language_register=LanguageRegister.COLLOQUIAL,
    )
    vc = engine.translate(empathy, language="en-IN", turn_index=2)

    assert isinstance(vc, VoiceConfig)
    assert vc.language == "en-IN"
    assert vc.energy_scale == pytest.approx(1.0, abs=1e-4)


# ---------------------------------------------------------------------------
# Additional coverage — ENGAGED and other table entries
# ---------------------------------------------------------------------------


def test_prosody_engaged_mapping(engine: AdaptiveProsodyEngine) -> None:
    """FRIENDLY+FAST (ENGAGED) maps to rate_scale=1.05 and pause_ms=100."""
    empathy = EmpathyConfig(
        tone=Tone.FRIENDLY,
        pacing=Pacing.FAST,
        language_register=LanguageRegister.COLLOQUIAL,
    )
    vc = engine.translate(empathy, language="en")
    assert vc.rate_scale == pytest.approx(1.05, abs=1e-4)
    assert vc.pause_ms_after_clause == 100
    assert vc.pitch_shift == pytest.approx(0.1, abs=1e-4)


def test_prosody_reassuring_mapping(engine: AdaptiveProsodyEngine) -> None:
    """REASSURING+SLOW maps to rate_scale=0.88 and pause_ms=300."""
    empathy = EmpathyConfig(
        tone=Tone.REASSURING,
        pacing=Pacing.SLOW,
        language_register=LanguageRegister.SEMI_FORMAL,
    )
    vc = engine.translate(empathy, language="en")
    assert vc.rate_scale == pytest.approx(0.88, abs=1e-4)
    assert vc.pause_ms_after_clause == 300


def test_prosody_fallback_unmapped_combination(engine: AdaptiveProsodyEngine) -> None:
    """Unmapped tone+pacing falls back to per-axis defaults."""
    empathy = EmpathyConfig(
        tone=Tone.EMPATHETIC,
        pacing=Pacing.FAST,
        language_register=LanguageRegister.FORMAL,
    )
    vc = engine.translate(empathy, language="en")
    assert isinstance(vc, VoiceConfig)
    assert vc.rate_scale > 0
    assert vc.pause_ms_after_clause > 0


def test_prosody_unknown_language_no_modifier(engine: AdaptiveProsodyEngine) -> None:
    """Unknown language tag applies no modifier (defaults to 1.0)."""
    empathy = EmpathyConfig(
        tone=Tone.NEUTRAL,
        pacing=Pacing.NORMAL,
        language_register=LanguageRegister.FORMAL,
    )
    vc_unknown = engine.translate(empathy, language="de")
    vc_en = engine.translate(empathy, language="en")
    # German has no modifier, so same as English baseline
    assert vc_unknown.rate_scale == pytest.approx(vc_en.rate_scale, abs=1e-4)


def test_prosody_high_distress_hindi(engine: AdaptiveProsodyEngine) -> None:
    """HIGH_DISTRESS in Hindi applies both base mapping and Hindi rate modifier."""
    empathy = EmpathyConfig(
        tone=Tone.EMPATHETIC,
        pacing=Pacing.SLOW,
        language_register=LanguageRegister.SEMI_FORMAL,
    )
    vc = engine.translate(empathy, language="hi")
    # Base: 0.85, Hindi modifier: 0.97 → 0.85 * 0.97 = 0.8245
    assert vc.rate_scale == pytest.approx(0.85 * 0.97, abs=1e-4)
    assert vc.pause_ms_after_clause == 350
