"""BackchannelDiscriminator — short-utterance filler classification.

Distinguishes full barge-in turns (the customer is interrupting) from
backchannel fillers ("hmm", "haan", "theek hai") that are < 800 ms.

The discriminator is applied by VADEndpointingService AFTER the EndpointDetector
signals speech end.  If the completed utterance is shorter than the backchannel
threshold, BackchannelDetected is returned and the BargeinDetected signal is
suppressed — the agent continues playing.

Architecture: V1 Ch6.8 (Backchannel Discrimination); DocSuite-02.
"""

from __future__ import annotations

from src.libs.contracts.events.audio_events import BackchannelDetected
from src.libs.contracts.primitives import CallId, TenantId


class BackchannelDiscriminator:
    """Classifies a completed speech segment as backchannel or full turn.

    Classification rule (V1 Ch6.8):
        utterance_duration_ms < backchannel_max_duration_ms  →  backchannel
        utterance_duration_ms ≥ backchannel_max_duration_ms  →  full barge-in

    The ``backchannel_max_duration_ms`` defaults to 800 ms.  Common backchannel
    fillers in Hindi / Hinglish — "hmm" (~200 ms), "haan" (~300 ms),
    "theek hai" (~500 ms) — are well within this boundary.
    """

    def __init__(self, backchannel_max_duration_ms: int = 800) -> None:
        """Construct the discriminator.

        Args:
            backchannel_max_duration_ms: Utterances strictly shorter than this
                                         threshold are classified as backchannels.
                                         Default 800 ms per V1 Ch6.8.
        """
        if backchannel_max_duration_ms <= 0:
            raise ValueError(f"backchannel_max_duration_ms must be > 0, got {backchannel_max_duration_ms}")
        self._max_duration_ms = backchannel_max_duration_ms

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify(
        self,
        duration_ms: int,
        call_id: CallId,
        tenant_id: TenantId,
        detected_at_ms: int,
    ) -> BackchannelDetected | None:
        """Classify a completed utterance as backchannel filler or full turn.

        Args:
            duration_ms:     Duration of the utterance from VADSpeechStart to
                             VADSpeechEnd in milliseconds.
            call_id:         Call identifier.
            tenant_id:       Tenant scope.
            detected_at_ms:  Call-relative ms when the utterance ended.

        Returns:
            BackchannelDetected when the utterance is classified as a filler,
            otherwise None (caller should emit BargeinDetected).
        """
        if duration_ms < self._max_duration_ms:
            return BackchannelDetected(
                call_id=call_id,
                tenant_id=tenant_id,
                detected_at_ms=detected_at_ms,
                duration_ms=duration_ms,
            )
        return None

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def max_duration_ms(self) -> int:
        """Utterances shorter than this threshold are classified as backchannels."""
        return self._max_duration_ms
