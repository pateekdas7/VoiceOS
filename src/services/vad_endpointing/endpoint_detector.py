"""EndpointDetector — adaptive silence-threshold endpointing state machine.

Tracks consecutive speech/silence frames from VAD probability outputs and
emits VADSpeechStart / VADSpeechEnd domain events.  The end threshold adapts
per-call: if the speaker pauses frequently (generating false endpoints), the
detector increases the silence dwell time so that natural pauses no longer
prematurely close the utterance.

Architecture: V1 Ch6 (VAD & Endpointing — adaptive pause detection);
              DocSuite-02 (VAD ↔ STT interface contract).
"""

from __future__ import annotations

from enum import StrEnum

from src.libs.contracts.events.audio_events import VADSpeechEnd, VADSpeechStart
from src.libs.contracts.events.envelope import DomainEvent
from src.libs.contracts.primitives import CallId, TenantId


class SpeechState(StrEnum):
    """Running state of the endpointing state machine."""

    IN_SILENCE = "IN_SILENCE"
    """No speech detected; waiting for utterance onset."""

    IN_SPEECH = "IN_SPEECH"
    """Utterance in progress; monitoring for end-of-speech silence."""

    POST_SPEECH = "POST_SPEECH"
    """End-of-speech silence detected; monitoring to confirm or detect false endpoint."""


class EndpointDetector:
    """Adaptive endpointing state machine driven by VAD speech probabilities.

    State transitions:
        IN_SILENCE  → IN_SPEECH     : consecutive_speech_ms ≥ start_threshold_ms
                                      → emits VADSpeechStart
        IN_SPEECH   → POST_SPEECH   : consecutive_silence_ms ≥ end_threshold_ms
                                      → emits VADSpeechEnd
        POST_SPEECH → IN_SPEECH     : speech resumes (false endpoint)
                                      → increments false_endpoint_count
                                      → adapts end_threshold_ms when count % 3 == 0
        POST_SPEECH → IN_SILENCE    : extended silence confirms utterance is over

    Adaptation rule (V1 Ch6.5):
        Every 3 false endpoints: end_threshold_ms += 200, capped at max_end_threshold_ms.

    Architecture: V1 Ch6.5 (Adaptive Pause Detection); DocSuite-02.
    """

    def __init__(
        self,
        *,
        start_threshold_ms: int = 100,
        end_threshold_ms: int = 600,
        max_end_threshold_ms: int = 1200,
        frame_size_ms: int = 32,
    ) -> None:
        """Construct the EndpointDetector with configurable thresholds.

        Args:
            start_threshold_ms:   Consecutive speech milliseconds required before
                                  declaring speech start.  Default 100 ms.
            end_threshold_ms:     Initial consecutive silence milliseconds required
                                  before declaring speech end.  Default 600 ms.
            max_end_threshold_ms: Upper bound for the adaptive end threshold.
                                  Default 1200 ms.
            frame_size_ms:        Duration of one VAD window in ms.  Default 32 ms.
        """
        if start_threshold_ms <= 0:
            raise ValueError(f"start_threshold_ms must be > 0, got {start_threshold_ms}")
        if end_threshold_ms <= 0:
            raise ValueError(f"end_threshold_ms must be > 0, got {end_threshold_ms}")
        if max_end_threshold_ms < end_threshold_ms:
            raise ValueError(
                f"max_end_threshold_ms ({max_end_threshold_ms}) must be >= end_threshold_ms ({end_threshold_ms})"
            )
        if frame_size_ms <= 0:
            raise ValueError(f"frame_size_ms must be > 0, got {frame_size_ms}")

        self._start_threshold_ms = start_threshold_ms
        self._end_threshold_ms = end_threshold_ms
        self._max_end_threshold_ms = max_end_threshold_ms
        self._frame_size_ms = frame_size_ms

        self._state: SpeechState = SpeechState.IN_SILENCE
        self._consecutive_speech_ms: int = 0
        self._consecutive_silence_ms: int = 0
        self._false_endpoint_count: int = 0
        self._speech_start_ms: int = 0
        """Call-relative ms when the current utterance onset was detected."""

    # ------------------------------------------------------------------
    # Core processing
    # ------------------------------------------------------------------

    def process(
        self,
        speech_probability: float,
        call_time_ms: int,
        call_id: CallId,
        tenant_id: TenantId,
        energy_db: float = 0.0,
        speech_threshold: float = 0.5,
    ) -> list[DomainEvent]:
        """Process one VAD window and return any emitted events.

        Args:
            speech_probability: Output of VADEngine.process_window().
            call_time_ms:       Current position in the call in milliseconds
                                (monotonically increasing, measured from call start).
            call_id:            Call identifier for event fields.
            tenant_id:          Tenant scope for event fields.
            energy_db:          Short-term energy in dBFS (for VADSpeechStart.energy_db).
            speech_threshold:   Probability above which a frame is considered speech.

        Returns:
            List of zero or more DomainEvent objects (VADSpeechStart / VADSpeechEnd).
        """
        is_speech = speech_probability >= speech_threshold

        if is_speech:
            self._consecutive_speech_ms += self._frame_size_ms
            self._consecutive_silence_ms = 0
        else:
            self._consecutive_silence_ms += self._frame_size_ms
            self._consecutive_speech_ms = 0

        return self._advance_state(is_speech, call_time_ms, call_id, tenant_id, energy_db)

    def _advance_state(
        self,
        is_speech: bool,
        call_time_ms: int,
        call_id: CallId,
        tenant_id: TenantId,
        energy_db: float,
    ) -> list[DomainEvent]:
        events: list[DomainEvent] = []

        if self._state is SpeechState.IN_SILENCE:
            if self._consecutive_speech_ms >= self._start_threshold_ms:
                # Utterance onset confirmed
                self._speech_start_ms = max(0, call_time_ms - self._consecutive_speech_ms)
                self._state = SpeechState.IN_SPEECH
                events.append(
                    VADSpeechStart(
                        call_id=call_id,
                        tenant_id=tenant_id,
                        start_ms=self._speech_start_ms,
                        energy_db=energy_db,
                    )
                )

        elif self._state is SpeechState.IN_SPEECH:
            if self._consecutive_silence_ms >= self._end_threshold_ms:
                # Silence dwell exceeded → end of utterance
                end_ms = call_time_ms
                duration_ms = max(0, end_ms - self._speech_start_ms)
                self._state = SpeechState.POST_SPEECH
                events.append(
                    VADSpeechEnd(
                        call_id=call_id,
                        tenant_id=tenant_id,
                        start_ms=self._speech_start_ms,
                        end_ms=end_ms,
                        duration_ms=duration_ms,
                    )
                )

        elif self._state is SpeechState.POST_SPEECH:
            if is_speech:
                # Speech resumed after apparent end → false endpoint
                self._false_endpoint_count += 1
                if self._false_endpoint_count % 3 == 0:
                    # Adapt threshold upward every 3 false endpoints
                    self._end_threshold_ms = min(
                        self._end_threshold_ms + 200,
                        self._max_end_threshold_ms,
                    )
                self._state = SpeechState.IN_SPEECH
                # Reset silence counter so we don't immediately re-endpoint
                self._consecutive_silence_ms = 0
            elif self._consecutive_silence_ms >= self._end_threshold_ms * 2:
                # Extended silence confirms the utterance is truly over → reset
                self._state = SpeechState.IN_SILENCE

        return events

    @property
    def state(self) -> SpeechState:
        """Current state machine state."""
        return self._state

    @property
    def end_threshold_ms(self) -> int:
        """Current adaptive end threshold in milliseconds."""
        return self._end_threshold_ms

    @property
    def false_endpoint_count(self) -> int:
        """Total false endpoints detected since last reset."""
        return self._false_endpoint_count

    def reset(self) -> None:
        """Reset all state (call at start of each new session)."""
        self._state = SpeechState.IN_SILENCE
        self._consecutive_speech_ms = 0
        self._consecutive_silence_ms = 0
        self._false_endpoint_count = 0
        self._speech_start_ms = 0
