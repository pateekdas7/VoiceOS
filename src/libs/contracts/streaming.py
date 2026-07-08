"""Streaming output types and shared CIL enumerations.

These types enable the True Streaming Pipeline (V1 Ch18): STT streams word
hypotheses, the LLM streams tokens, and TTS streams audio clauses — all
incrementally without waiting for a complete response.

The shared enumerations (Tone, Pacing, LanguageRegister, Sentiment,
StressLevel) are used across the intelligence and delivery layers. They are
defined here so Sprint-009 (AI adapters) can import them without depending
on Sprint-011 (EmpathyPlanner) or Sprint-010 (EmotionEngine).

Architecture: V1 Ch8 (STT), V1 Ch13 (LLM Runtime), V1 Ch17 (Veena TTS),
              V1 Ch18 (True Streaming Pipeline); V1 Ch19-20 (Emotion /
              Adaptive Prosody); Sprint-001 spec (cross-sprint contracts).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Shared enumerations used by emotion intelligence and delivery engines
# ---------------------------------------------------------------------------


class Tone(StrEnum):
    """Agent conversational tone, set by the EmpathyPlanner (Sprint-011).

    Drives prosody and word-choice guidance injected into the LLM prompt.
    Architecture: V1 Ch16 (Voice Style), V2 Ch14 (EmpathyPlanner).
    """

    EMPATHETIC = "empathetic"
    FIRM = "firm"
    FRIENDLY = "friendly"
    FORMAL = "formal"
    REASSURING = "reassuring"
    NEUTRAL = "neutral"


class Pacing(StrEnum):
    """Speech delivery pace, set by the EmpathyPlanner.

    SLOW is used for distressed customers (high arousal, negative sentiment).
    Architecture: V1 Ch16, V1 Ch20 (Adaptive Prosody Engine).
    """

    SLOW = "slow"
    NORMAL = "normal"
    FAST = "fast"


class LanguageRegister(StrEnum):
    """Sociolinguistic register for language generation.

    Drives vocabulary selection and honorific usage in the LLM prompt.
    Architecture: V2 Ch14; DocSuite-07 (Voice Style Guide).
    """

    FORMAL = "formal"
    SEMI_FORMAL = "semi_formal"
    COLLOQUIAL = "colloquial"


class Sentiment(StrEnum):
    """Broad sentiment category derived from the EmotionIntelligenceEngine.

    Architecture: V1 Ch19 (Emotion Intelligence), V2 Ch13.
    """

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    HOSTILE = "hostile"


class StressLevel(StrEnum):
    """Customer stress level estimated from audio features and transcript.

    Used by the EmpathyPlanner to modulate tone and pacing.
    Architecture: V2 Ch13, V2 Ch14.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# EmpathyConfig — output of EmpathyPlanner, input to AdaptiveProsodyEngine
# ---------------------------------------------------------------------------


class EmpathyConfig(BaseModel):
    """Empathy and delivery configuration produced by the EmpathyPlanner (Sprint-011).

    Consumed by the AdaptiveProsodyEngine (Sprint-009) and the TTS layer to
    adjust agent tone and pacing based on the detected customer emotion state.

    Defined in Sprint-001 so Sprint-009 can import it without creating a
    dependency on Sprint-011.

    Architecture: V1 Ch19-20; V2 Ch14; Sprint-001 spec.
    """

    model_config = ConfigDict(frozen=True)

    tone: Tone
    """Target conversational tone for this response."""

    pacing: Pacing
    """Target speech pace for this response."""

    language_register: LanguageRegister
    """Sociolinguistic register for word-choice guidance."""

    acknowledgment_phrase: str | None = None
    """Optional opening acknowledgment phrase (e.g., 'Haan, main samajh sakta hoon')."""


# ---------------------------------------------------------------------------
# VoiceConfig — output of AdaptiveProsodyEngine, input to TTSAdapter
# ---------------------------------------------------------------------------


class VoiceConfig(BaseModel):
    """Prosody parameters passed from AdaptiveProsodyEngine to TTSAdapter.

    Defines the acoustic parameters for the synthesized speech. All values
    are bounded to prevent jarring or unnatural delivery.

    Architecture: V1 Ch20 (Adaptive Prosody Engine), V1 Ch17 (Veena TTS);
                  Sprint-001 spec.
    """

    model_config = ConfigDict(frozen=True)

    pitch_shift: float = Field(default=0.0, ge=-2.0, le=2.0)
    """Pitch shift in semitones relative to the voice profile baseline."""

    rate_scale: float = Field(default=1.0, ge=0.5, le=2.0)
    """Speaking rate scale factor (1.0 = natural, >1.0 = faster)."""

    energy_scale: float = Field(default=1.0, ge=0.5, le=2.0)
    """Volume / energy scale factor relative to the voice profile baseline."""

    pause_ms_after_clause: int = Field(default=150, ge=0, le=1000)
    """Pause duration in milliseconds inserted between synthesized clauses."""

    language: str = "hi-IN"
    """BCP-47 language/locale tag for synthesis (e.g., 'hi-IN', 'en-IN')."""


# ---------------------------------------------------------------------------
# STT streaming output
# ---------------------------------------------------------------------------


class WordHypothesis(BaseModel):
    """A single word or token hypothesis from the STT adapter (Sprint-009).

    The STT adapter streams WordHypothesis objects as partial (is_final=False)
    and final (is_final=True) transcription results. The Dialogue Manager
    accumulates final hypotheses into TurnInput.

    Architecture: V1 Ch8 (STT streaming); V1 Ch18 (True Streaming Pipeline);
                  Sprint-001 spec.
    """

    model_config = ConfigDict(frozen=True)

    word: str
    """The transcribed word or subword token."""

    confidence: float = Field(ge=0.0, le=1.0)
    """ASR confidence for this hypothesis [0.0, 1.0]."""

    start_ms: int = Field(ge=0)
    """Word start time in milliseconds relative to utterance start."""

    end_ms: int = Field(ge=0)
    """Word end time in milliseconds relative to utterance start."""

    is_final: bool
    """True if this is a committed (non-revisable) hypothesis; False if partial."""


# ---------------------------------------------------------------------------
# LLM streaming output
# ---------------------------------------------------------------------------


class TokenChunk(BaseModel):
    """A single token or token batch from the LLM adapter (Sprint-009).

    Streamed token by token from vLLM. The clause builder (Sprint-012)
    accumulates tokens into speakable clauses before dispatching to TTS.

    Architecture: V1 Ch13 (LLM Runtime); V1 Ch18 (True Streaming Pipeline);
                  Sprint-001 spec.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    """Decoded text for this token (may be a sub-word piece)."""

    token_id: int = Field(ge=0)
    """Vocabulary token ID from the model tokenizer."""

    finish_reason: str | None = None
    """Non-None when generation terminates: 'stop', 'length', 'error'."""


# ---------------------------------------------------------------------------
# TTS streaming output
# ---------------------------------------------------------------------------


class AudioClause(BaseModel):
    """A single synthesized audio clause from the TTS adapter (Sprint-009).

    The True Streaming Pipeline (V1 Ch18) dispatches synthesis clause by
    clause so playback can begin before LLM generation completes. Each
    AudioClause is an independently playable unit.

    Architecture: V1 Ch17 (Veena TTS); V1 Ch18 (True Streaming Pipeline);
                  Sprint-001 spec.
    """

    model_config = ConfigDict(frozen=True)

    audio_data: bytes
    """Raw PCM audio bytes for this clause."""

    sample_rate: int = Field(ge=8000, le=48000)
    """Sample rate of the audio_data (typically 24000 for Veena TTS)."""

    text: str
    """The text that was synthesized into this clause (for alignment and replay)."""

    clause_index: int = Field(ge=0)
    """Zero-based index of this clause within the full response. Used for ordering."""

    is_final: bool
    """True if this is the last clause of the response; False if more follow."""
