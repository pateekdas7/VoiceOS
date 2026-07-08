"""Turn-level conversation contracts.

A conversational turn is the fundamental unit of dialogue: one party speaks,
the other responds. TurnInput is what the Dialogue Manager produces from
finalized STT output and passes to the Conversation Engine / intelligence
engines for processing.

Architecture: V1 Ch9 (Dialogue Manager), V2 Ch2 (CIL Input Spec);
              DocSuite-02 A.2; DocSuite-03 (Data Dictionary).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TurnRole(StrEnum):
    """The speaker role for a conversational turn.

    Distinguishes customer utterances (input to the intelligence layer) from
    agent utterances (produced by the delivery layer). Stored in transcripts
    and decision lineage.
    """

    CUSTOMER = "customer"
    AGENT = "agent"


class UtteranceSegment(BaseModel):
    """A time-aligned segment of a transcribed utterance.

    Produced by the STT adapter (Sprint-009) for each word or phrase
    hypothesis that reaches a final state. Segments compose the full
    transcript stored in TurnInput.

    Architecture: V1 Ch8 (STT streaming output).
    """

    model_config = ConfigDict(frozen=True)

    text: str
    """Transcribed text of this segment."""

    start_ms: int = Field(ge=0)
    """Segment start time in milliseconds relative to call start."""

    end_ms: int = Field(ge=0)
    """Segment end time in milliseconds relative to call start."""

    confidence: float = Field(ge=0.0, le=1.0)
    """ASR confidence score for this segment [0.0, 1.0]."""


class TurnInput(BaseModel):
    """The normalised input to the Conversation Intelligence Layer (CIL).

    Built by the Dialogue Manager after STT finalizes an utterance. Contains
    the full transcript plus metadata needed by every downstream engine
    (IntentEngine, EntityExtractor, EmotionIntelligenceEngine, etc.).

    TurnInput is immutable once built. Downstream engines must not mutate it;
    they read from it and produce their own typed outputs.

    Architecture: V1 Ch9, V2 Ch2; DocSuite-02 A.2.
    """

    model_config = ConfigDict(frozen=True)

    turn_id: str
    """Globally unique turn identifier (UUID). Scopes this turn's events."""

    call_id: str
    """Parent call session identifier. All turns in a call share this ID."""

    tenant_id: str
    """Tenant scope (AR-8). Every data access downstream uses this."""

    role: TurnRole
    """Speaker role for this turn."""

    transcript: str
    """Full, space-joined transcribed text for this turn."""

    segments: tuple[UtteranceSegment, ...]
    """Time-aligned segments composing the transcript, ordered by start_ms."""

    created_at: datetime
    """UTC timestamp when this TurnInput was finalized by the Dialogue Manager."""

    correlation_id: str
    """Correlation ID linking this turn to its parent request chain (EV-8)."""

    trace_id: str
    """OpenTelemetry trace ID for distributed tracing (V3 Ch17)."""

    turn_index: int = Field(ge=0)
    """Zero-based turn number within the call (0 = first customer utterance)."""
