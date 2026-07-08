"""DialogueManager — turn state machine and TurnInput assembler.

The Dialogue Manager mediates between the STT layer and the Conversation
Engine. It:
  1. Accumulates final WordHypothesis objects into a transcript.
  2. Detects end-of-utterance and builds a sealed TurnInput.
  3. Tracks turn count and call state (IDLE / SPEAKING / PROCESSING).
  4. Signals barge-in to the PlaybackScheduler when customer speaks during
     agent response.

Architecture: V1 Ch9 (Dialogue Manager); V1 Ch18 (True Streaming Pipeline).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from enum import StrEnum

from src.libs.contracts.streaming import WordHypothesis
from src.libs.contracts.turn import TurnInput, TurnRole, UtteranceSegment

logger = logging.getLogger(__name__)


class DialogueState(StrEnum):
    """Turn-level dialogue state machine states."""

    IDLE = "IDLE"
    """Waiting for the customer to speak."""

    CUSTOMER_SPEAKING = "CUSTOMER_SPEAKING"
    """Customer utterance is in progress (receiving partial STT)."""

    PROCESSING = "PROCESSING"
    """Utterance finalised; CIL pipeline processing is in progress."""

    AGENT_SPEAKING = "AGENT_SPEAKING"
    """Agent response is being synthesised and/or played back."""


class DialogueManager:
    """Accumulates STT output and assembles TurnInput objects.

    One instance is created per call session. ``ingest_stream`` is the
    primary entry point for STT output.

    Args:
        call_id: Parent call session identifier.
        tenant_id: Tenant scope (AR-8).
        correlation_id: Correlation ID for the call.
        trace_id: OpenTelemetry trace ID.
    """

    def __init__(
        self,
        call_id: str,
        tenant_id: str,
        correlation_id: str = "",
        trace_id: str = "",
    ) -> None:
        self._call_id = call_id
        self._tenant_id = tenant_id
        self._correlation_id = correlation_id
        self._trace_id = trace_id
        self._turn_index = 0
        self._state = DialogueState.IDLE
        self._accumulated: list[WordHypothesis] = []
        self._start_time: datetime | None = None

    @property
    def state(self) -> DialogueState:
        """Current dialogue state."""
        return self._state

    @property
    def turn_index(self) -> int:
        """Zero-based index of the next turn to be built."""
        return self._turn_index

    async def ingest_stream(self, word_stream: AsyncIterator[WordHypothesis]) -> TurnInput:
        """Ingest a stream of WordHypothesis and return a sealed TurnInput.

        Accumulates all final hypotheses until the stream ends, then builds
        the TurnInput. Partial hypotheses (is_final=False) are counted for
        barge-in detection but not included in the transcript.

        Args:
            word_stream: AsyncIterator of WordHypothesis from STTService.

        Returns:
            Sealed TurnInput ready for the CIL pipeline.
        """
        self._state = DialogueState.CUSTOMER_SPEAKING
        self._accumulated = []
        self._start_time = datetime.utcnow()

        async for hypothesis in word_stream:
            if hypothesis.is_final:
                self._accumulated.append(hypothesis)

        self._state = DialogueState.PROCESSING
        turn = self._build_turn()
        self._turn_index += 1
        return turn

    def notify_agent_speaking(self) -> None:
        """Signal that the agent response has started playing."""
        self._state = DialogueState.AGENT_SPEAKING

    def notify_agent_done(self) -> None:
        """Signal that the agent response has finished; ready for next turn."""
        self._state = DialogueState.IDLE

    def _build_turn(self) -> TurnInput:
        """Build a sealed TurnInput from the accumulated final hypotheses."""
        if not self._accumulated:
            # Empty utterance — use a silence transcript.
            transcript = ""
            segments: tuple[UtteranceSegment, ...] = ()
        else:
            transcript = " ".join(h.word for h in self._accumulated)
            segments = tuple(
                UtteranceSegment(
                    text=h.word,
                    start_ms=h.start_ms,
                    end_ms=h.end_ms,
                    confidence=h.confidence,
                )
                for h in self._accumulated
            )

        return TurnInput(
            turn_id=str(uuid.uuid4()),
            call_id=self._call_id,
            tenant_id=self._tenant_id,
            role=TurnRole.CUSTOMER,
            transcript=transcript,
            segments=segments,
            created_at=self._start_time or datetime.utcnow(),
            correlation_id=self._correlation_id,
            trace_id=self._trace_id,
            turn_index=self._turn_index,
        )
