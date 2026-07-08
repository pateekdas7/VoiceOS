"""Domain events for the dialogue and playback pipeline.

Covers turn lifecycle (start → complete), ResponsePlan creation, and TTS
playback state (started → completed → flushed). Consumed by the Dialogue
Manager, Monitoring, Replay, and Audit layers.

Architecture: V1 Ch9 (Dialogue Manager), V1 Ch17 (TTS Playback),
              V2 Ch15 (ResponsePlan); V3 Ch3 (Event Bus); V6 Ch6 EV-1.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..primitives import CallId
from .envelope import DomainEvent


class TurnStarted(DomainEvent):
    """Emitted when the Dialogue Manager creates a new TurnInput for processing.

    Marks the boundary between VAD endpointing (audio phase) and
    intelligence processing (cognition phase). Consumed by the CIL engines
    and tracing infrastructure to measure cognition latency.

    Architecture: V1 Ch9.3; V2 Ch2 (TurnInput spec).
    """

    event_type: Literal["dialogue.turn.started"] = "dialogue.turn.started"
    call_id: CallId
    turn_id: str
    """Unique identifier for this turn (scoped to call_id)."""
    turn_index: int = Field(ge=0)
    """Zero-based position of this turn in the call (used for context windows)."""
    role: str
    """Speaker role: 'customer' | 'agent'."""


class TurnCompleted(DomainEvent):
    """Emitted when a turn's transcript is finalised and handed to the engines.

    ``transcript`` is the final STT output after confidence filtering.
    Consumed by analytics and the conversation replay system.

    Architecture: V1 Ch9.6; V2 Ch2.
    """

    event_type: Literal["dialogue.turn.completed"] = "dialogue.turn.completed"
    call_id: CallId
    turn_id: str
    transcript: str
    """Final STT transcript for this turn."""
    duration_ms: int = Field(ge=0)
    """Time from TurnStarted to TurnCompleted in milliseconds."""
    word_count: int = Field(ge=0)


class ResponsePlanCreated(DomainEvent):
    """Emitted when the Conversation Engine seals a new ResponsePlan (AR-4).

    This is the commit point before any LLM / TTS output is produced.
    Downstream consumers (OutputValidator, GovernanceLayer) verify the plan
    before allowing output to proceed (RI-4, RI-5, RI-6).

    Architecture: V1 Ch10 (Conversation Engine); V2 Ch15 (ResponsePlan);
                  V6 AR-4; Invariant RI-4.
    """

    event_type: Literal["dialogue.response_plan.created"] = "dialogue.response_plan.created"
    call_id: CallId
    turn_id: str
    plan_id: str
    """ResponsePlan.plan_id — stable identifier for this sealed plan."""
    plan_version: int = Field(ge=1)
    """ResponsePlan.version — monotone counter within the call."""
    strategy: str
    """StrategyLabel value selected for this turn."""


class PlaybackStarted(DomainEvent):
    """Emitted when the TTS engine begins streaming audio for a ResponsePlan.

    Architecture: V1 Ch17 (Veena TTS), V1 Ch18 (True Streaming Pipeline).
    """

    event_type: Literal["dialogue.playback.started"] = "dialogue.playback.started"
    call_id: CallId
    plan_id: str
    clause_count: int = Field(ge=0)
    """Number of audio clauses scheduled for playback."""
    voice_id: str
    """TTS voice identifier used for this response."""


class PlaybackCompleted(DomainEvent):
    """Emitted when TTS playback of a ResponsePlan finishes without interruption.

    Architecture: V1 Ch17.
    """

    event_type: Literal["dialogue.playback.completed"] = "dialogue.playback.completed"
    call_id: CallId
    plan_id: str
    played_ms: int = Field(ge=0)
    """Actual audio duration played in milliseconds."""


class PlaybackFlushed(DomainEvent):
    """Emitted when the playback buffer is flushed before completion.

    Typically caused by barge-in (BargeinDetected). The ``reason`` is a
    stable code used to distinguish barge-in from errors.

    Architecture: V1 Ch17.8 (Interrupt Handling).
    """

    event_type: Literal["dialogue.playback.flushed"] = "dialogue.playback.flushed"
    call_id: CallId
    plan_id: str
    reason: str
    """Flush reason: 'barge_in' | 'call_ended' | 'error' | 'timeout'."""
    clauses_played: int = Field(ge=0)
    """Number of clauses completed before flush."""


__all__ = [
    "PlaybackCompleted",
    "PlaybackFlushed",
    "PlaybackStarted",
    "ResponsePlanCreated",
    "TurnCompleted",
    "TurnStarted",
]
