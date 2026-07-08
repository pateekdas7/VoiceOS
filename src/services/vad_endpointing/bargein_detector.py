"""BargeinDetector — real-time barge-in detection during agent playback.

Monitors VAD probabilities while the agent TTS is playing and emits a
BargeinDetected event when the customer's speech is sustained above the
barge-in threshold for the required duration.

The higher threshold (0.65 vs. the endpointing 0.50) reduces false
positives caused by background noise bleeding through the AEC stage.

The BackchannelDiscriminator in service.py intercepts BargeinDetected
signals from this detector and may reclassify short utterances (< 800 ms)
as BackchannelDetected before propagating externally.

Architecture: V1 Ch6.7 (Barge-in Detection); V1 Ch6.8 (Playback Flush);
              DocSuite-02 (VAD → DialogueManager barge-in signal).
Invariant: RI-1 (barge-in triggers immediate playback flush).
"""

from __future__ import annotations

from src.libs.contracts.events.audio_events import BargeinDetected
from src.libs.contracts.primitives import CallId, TenantId


class BargeinDetector:
    """Detects customer speech interruptions during agent TTS playback.

    Active only when ``playback_active`` is True.  When inactive the detector
    resets and does not emit events, so callers can simply call ``process()``
    every frame without branch logic.

    Barge-in rule (V1 Ch6.7):
        speech_probability > barge_in_threshold  AND
        sustained for ≥ required_duration_ms  during playback
        → BargeinDetected emitted.

    The ``playback_seq`` field on BargeinDetected identifies which TTS clause
    was playing at the time of interruption so the PlaybackScheduler can flush
    from that clause onward.
    """

    def __init__(
        self,
        *,
        barge_in_threshold: float = 0.65,
        required_duration_ms: int = 200,
        frame_size_ms: int = 32,
    ) -> None:
        """Construct the BargeinDetector.

        Args:
            barge_in_threshold:  Minimum speech probability to count as a
                                 barge-in candidate frame.  Higher than the
                                 normal speech threshold (0.50) to reduce
                                 false positives from noise.  Default 0.65.
            required_duration_ms: Consecutive ms above threshold before
                                  BargeinDetected is emitted.  Default 200 ms.
            frame_size_ms:       Duration of one VAD window.  Default 32 ms.
        """
        if not (0.0 < barge_in_threshold <= 1.0):
            raise ValueError(f"barge_in_threshold must be in (0, 1], got {barge_in_threshold}")
        if required_duration_ms <= 0:
            raise ValueError(f"required_duration_ms must be > 0, got {required_duration_ms}")
        if frame_size_ms <= 0:
            raise ValueError(f"frame_size_ms must be > 0, got {frame_size_ms}")

        self._barge_in_threshold = barge_in_threshold
        self._required_duration_ms = required_duration_ms
        self._frame_size_ms = frame_size_ms

        self._playback_active: bool = False
        self._playback_seq: int = 0
        self._sustained_ms: int = 0
        """Consecutive milliseconds above threshold while playback is active."""
        self._fired: bool = False
        """True after BargeinDetected has been emitted for the current candidate."""

    # ------------------------------------------------------------------
    # Playback state control
    # ------------------------------------------------------------------

    def set_playback_active(self, active: bool, playback_seq: int = 0) -> None:
        """Notify the detector about playback state changes.

        Args:
            active:       True when TTS audio is streaming to the caller.
            playback_seq: Sequence number of the currently playing TTS clause.
                          Carried into the BargeinDetected event so downstream
                          components know exactly which clause was interrupted.
        """
        self._playback_active = active
        self._playback_seq = playback_seq
        if not active:
            self._reset_candidate()

    # ------------------------------------------------------------------
    # Per-frame processing
    # ------------------------------------------------------------------

    def process(
        self,
        speech_probability: float,
        call_time_ms: int,
        call_id: CallId,
        tenant_id: TenantId,
    ) -> BargeinDetected | None:
        """Process one VAD window and return BargeinDetected if triggered.

        Args:
            speech_probability: Output of VADEngine.process_window().
            call_time_ms:       Current call position in milliseconds.
            call_id:            Call identifier.
            tenant_id:          Tenant scope.

        Returns:
            BargeinDetected when barge-in is confirmed, otherwise None.
            Returns None when playback is not active.
        """
        if not self._playback_active:
            self._reset_candidate()
            return None

        if speech_probability > self._barge_in_threshold:
            self._sustained_ms += self._frame_size_ms
        else:
            self._reset_candidate()
            return None

        if self._sustained_ms >= self._required_duration_ms and not self._fired:
            self._fired = True
            return BargeinDetected(
                call_id=call_id,
                tenant_id=tenant_id,
                detected_at_ms=call_time_ms,
                playback_seq=self._playback_seq,
            )

        return None

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def sustained_ms(self) -> int:
        """Consecutive milliseconds above threshold in the current candidate."""
        return self._sustained_ms

    @property
    def playback_active(self) -> bool:
        """True when the detector is armed (agent playback is in progress)."""
        return self._playback_active

    def reset(self) -> None:
        """Full reset — call at the start of every new call session."""
        self._playback_active = False
        self._playback_seq = 0
        self._reset_candidate()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_candidate(self) -> None:
        self._sustained_ms = 0
        self._fired = False
