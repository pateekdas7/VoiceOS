"""Unit tests for BargeinDetector and BackchannelDiscriminator (Sprint-007).

Covers all barge-in / backchannel acceptance criteria from Sprint-007.md:

  AC-1  test_bargein_threshold         — 0.70 prob for 250 ms during playback → BargeinDetected
  AC-2  test_backchannel_suppression   — 500 ms utterance during playback → BackchannelDetected
  AC-3  test_bargein_not_fired_no_playback — barge-in inactive when playback off
  AC-4  test_bargein_resets_on_silence — speech below threshold resets candidate
  AC-5  test_backchannel_long_utterance_not_suppressed — ≥ 800 ms → not a backchannel
  AC-6  test_bargein_event_fields      — BargeinDetected carries call_id/tenant_id/timestamp

Architecture: V6 Ch9 (Testing Standards); V1 Ch6 (Barge-in & Backchannel).
"""

from __future__ import annotations

from src.libs.contracts.events.audio_events import BackchannelDetected, BargeinDetected
from src.libs.contracts.primitives import CallId, TenantId
from src.services.vad_endpointing.backchannel import BackchannelDiscriminator
from src.services.vad_endpointing.bargein_detector import BargeinDetector

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_CALL_ID = CallId("test-call-bargein-001")
_TENANT_ID = TenantId("10000000-0000-0000-0000-000000000002")

_FRAME_MS = 32
_HIGH_PROB = 0.85  # above barge-in threshold 0.65
_LOW_PROB = 0.20  # below barge-in threshold 0.65


def _feed_bargein(
    detector: BargeinDetector,
    n_frames: int,
    probability: float,
    start_call_time_ms: int = 0,
) -> list[BargeinDetected]:
    """Feed ``n_frames`` VAD windows and collect BargeinDetected events."""
    results: list[BargeinDetected] = []
    for i in range(n_frames):
        t = start_call_time_ms + i * _FRAME_MS
        event = detector.process(probability, t, _CALL_ID, _TENANT_ID)
        if event is not None:
            results.append(event)
    return results


# ---------------------------------------------------------------------------
# Required test AC-1: test_bargein_threshold
# ---------------------------------------------------------------------------


class TestBargeinThreshold:
    """AC-1: speech at 0.70 probability for 250 ms during playback → BargeinDetected."""

    def test_bargein_threshold(self) -> None:
        """Required test: 0.70 probability sustained for 250 ms → BargeinDetected emitted."""
        detector = BargeinDetector(
            barge_in_threshold=0.65,
            required_duration_ms=200,
            frame_size_ms=_FRAME_MS,
        )
        detector.set_playback_active(True, playback_seq=0)

        # Feed 8 frames x 32 ms = 256 ms of probability 0.70 (> 0.65)
        results = _feed_bargein(detector, n_frames=8, probability=0.70)

        assert len(results) >= 1, "Expected BargeinDetected after 256 ms of speech at p=0.70, got 0 events"
        assert isinstance(results[0], BargeinDetected)

    def test_bargein_fires_exactly_at_threshold(self) -> None:
        """BargeinDetected fires once at 200 ms, not again on subsequent frames."""
        detector = BargeinDetector(
            barge_in_threshold=0.65,
            required_duration_ms=200,
            frame_size_ms=_FRAME_MS,
        )
        detector.set_playback_active(True)

        # Feed enough frames to exceed required_duration_ms
        results = _feed_bargein(detector, n_frames=20, probability=0.70)

        # Exactly one event must be emitted (not one per frame)
        assert len(results) == 1, f"BargeinDetected should fire exactly once, got {len(results)} events"

    def test_bargein_just_below_threshold_no_event(self) -> None:
        """Speech at exactly the barge_in_threshold is NOT sufficient (strictly greater)."""
        detector = BargeinDetector(
            barge_in_threshold=0.65,
            required_duration_ms=200,
            frame_size_ms=_FRAME_MS,
        )
        detector.set_playback_active(True)

        # 0.65 is NOT above the threshold (> 0.65 required)
        results = _feed_bargein(detector, n_frames=20, probability=0.65)
        assert len(results) == 0, "Speech exactly at threshold should not trigger barge-in"

    def test_bargein_not_fired_no_playback(self) -> None:
        """Required test: barge-in must not fire when playback is inactive."""
        detector = BargeinDetector(
            barge_in_threshold=0.65,
            required_duration_ms=200,
            frame_size_ms=_FRAME_MS,
        )
        # playback_active defaults to False — do not call set_playback_active

        results = _feed_bargein(detector, n_frames=20, probability=0.90)
        assert len(results) == 0, "BargeinDetected must not fire when playback is inactive"


# ---------------------------------------------------------------------------
# Required test AC-2: test_backchannel_suppression
# ---------------------------------------------------------------------------


class TestBackchannelSuppression:
    """AC-2: 500 ms utterance during playback → BackchannelDetected (not BargeinDetected)."""

    def test_backchannel_suppression(self) -> None:
        """Required test: 500 ms utterance → BackchannelDetected emitted by discriminator."""
        discriminator = BackchannelDiscriminator(backchannel_max_duration_ms=800)

        # 500 ms < 800 ms threshold → classified as backchannel
        event = discriminator.classify(
            duration_ms=500,
            call_id=_CALL_ID,
            tenant_id=_TENANT_ID,
            detected_at_ms=5000,
        )

        assert event is not None, "Expected BackchannelDetected for 500 ms utterance, got None"
        assert isinstance(event, BackchannelDetected)
        assert event.duration_ms == 500
        assert event.call_id == _CALL_ID
        assert event.tenant_id == _TENANT_ID
        assert event.detected_at_ms == 5000

    def test_backchannel_boundary_just_below(self) -> None:
        """799 ms utterance is a backchannel (< 800 ms threshold)."""
        discriminator = BackchannelDiscriminator(backchannel_max_duration_ms=800)
        event = discriminator.classify(799, _CALL_ID, _TENANT_ID, 0)
        assert event is not None
        assert isinstance(event, BackchannelDetected)

    def test_backchannel_boundary_at_threshold(self) -> None:
        """800 ms utterance is NOT a backchannel (≥ threshold → full barge-in)."""
        discriminator = BackchannelDiscriminator(backchannel_max_duration_ms=800)
        event = discriminator.classify(800, _CALL_ID, _TENANT_ID, 0)
        assert event is None, "800 ms utterance must not be classified as backchannel"

    def test_backchannel_long_utterance_not_suppressed(self) -> None:
        """Required test: ≥ 800 ms utterance is NOT a backchannel → returns None."""
        discriminator = BackchannelDiscriminator(backchannel_max_duration_ms=800)
        event = discriminator.classify(1200, _CALL_ID, _TENANT_ID, 0)
        assert event is None, "1200 ms utterance must not be suppressed as backchannel"

    def test_backchannel_very_short_utterance(self) -> None:
        """Very short utterance (200 ms 'hmm') is a backchannel."""
        discriminator = BackchannelDiscriminator(backchannel_max_duration_ms=800)
        event = discriminator.classify(200, _CALL_ID, _TENANT_ID, 1000)
        assert event is not None
        assert isinstance(event, BackchannelDetected)
        assert event.duration_ms == 200


# ---------------------------------------------------------------------------
# Additional BargeinDetector tests
# ---------------------------------------------------------------------------


class TestBargeinDetectorBehaviour:
    """Additional correctness tests for BargeinDetector."""

    def test_bargein_resets_on_silence(self) -> None:
        """Required test: speech below threshold resets the candidate accumulator."""
        detector = BargeinDetector(
            barge_in_threshold=0.65,
            required_duration_ms=200,
            frame_size_ms=_FRAME_MS,
        )
        detector.set_playback_active(True)

        # 3 frames of high probability (96 ms — not enough yet)
        _feed_bargein(detector, n_frames=3, probability=0.70)
        assert detector.sustained_ms == 96

        # One low-prob frame → reset candidate
        detector.process(0.30, 96, _CALL_ID, _TENANT_ID)
        assert detector.sustained_ms == 0

        # Now we need 200 ms fresh to trigger
        results = _feed_bargein(detector, n_frames=7, probability=0.70, start_call_time_ms=128)
        assert len(results) == 1  # fires after fresh 200 ms accumulation

    def test_bargein_event_fields(self) -> None:
        """Required test: BargeinDetected carries correct call_id, tenant_id, detected_at_ms."""
        call_id = CallId("fieldcheck-bargein")
        tenant_id = TenantId("30000000-0000-0000-0000-000000000003")
        detector = BargeinDetector(
            barge_in_threshold=0.65,
            required_duration_ms=200,
            frame_size_ms=_FRAME_MS,
        )
        detector.set_playback_active(True, playback_seq=7)

        results: list[BargeinDetected] = []
        for i in range(10):
            t = 1000 + i * _FRAME_MS
            event = detector.process(0.80, t, call_id, tenant_id)
            if event is not None:
                results.append(event)

        assert len(results) == 1
        ev = results[0]
        assert ev.call_id == call_id
        assert ev.tenant_id == tenant_id
        assert ev.detected_at_ms >= 1000
        assert ev.playback_seq == 7

    def test_bargein_deactivates_on_playback_stop(self) -> None:
        """Disabling playback cancels any pending barge-in candidate."""
        detector = BargeinDetector(
            barge_in_threshold=0.65,
            required_duration_ms=200,
            frame_size_ms=_FRAME_MS,
        )
        detector.set_playback_active(True)

        # Start accumulating
        _feed_bargein(detector, n_frames=3, probability=0.80)
        assert detector.sustained_ms > 0

        # Playback stops before threshold reached
        detector.set_playback_active(False)
        assert detector.sustained_ms == 0
        assert not detector.playback_active

        # Even high-probability frames should not trigger
        results = _feed_bargein(detector, n_frames=20, probability=0.90)
        assert len(results) == 0

    def test_bargein_playback_seq_propagated(self) -> None:
        """playback_seq from set_playback_active is carried into the event."""
        detector = BargeinDetector(required_duration_ms=200, frame_size_ms=_FRAME_MS)
        detector.set_playback_active(True, playback_seq=42)

        results = _feed_bargein(detector, n_frames=10, probability=0.90)
        assert len(results) == 1
        assert results[0].playback_seq == 42

    def test_bargein_reset_clears_all_state(self) -> None:
        """reset() fully resets the detector for a new call."""
        detector = BargeinDetector(required_duration_ms=200, frame_size_ms=_FRAME_MS)
        detector.set_playback_active(True)
        _feed_bargein(detector, n_frames=3, probability=0.90)
        detector.reset()
        assert not detector.playback_active
        assert detector.sustained_ms == 0


# ---------------------------------------------------------------------------
# BackchannelDiscriminator additional tests
# ---------------------------------------------------------------------------


class TestBackchannelDiscriminator:
    """Additional correctness tests for BackchannelDiscriminator."""

    def test_custom_threshold(self) -> None:
        """Custom backchannel_max_duration_ms is honoured."""
        discriminator = BackchannelDiscriminator(backchannel_max_duration_ms=500)
        assert discriminator.classify(499, _CALL_ID, _TENANT_ID, 0) is not None
        assert discriminator.classify(500, _CALL_ID, _TENANT_ID, 0) is None

    def test_zero_duration_is_backchannel(self) -> None:
        """Zero-duration utterance is classified as backchannel."""
        discriminator = BackchannelDiscriminator(backchannel_max_duration_ms=800)
        event = discriminator.classify(0, _CALL_ID, _TENANT_ID, 0)
        assert event is not None
        assert event.duration_ms == 0

    def test_constructor_rejects_zero_threshold(self) -> None:
        """backchannel_max_duration_ms=0 raises ValueError."""
        import pytest

        with pytest.raises(ValueError):
            BackchannelDiscriminator(backchannel_max_duration_ms=0)
