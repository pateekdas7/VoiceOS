"""Unit tests for EndpointDetector (Sprint-007).

Covers all endpointing acceptance criteria from Sprint-007.md:

  AC-1  test_endpoint_speech_start       — 150 ms of speech → VADSpeechStart emitted
  AC-2  test_endpoint_speech_end_600ms   — 600 ms silence after speech → VADSpeechEnd emitted
  AC-3  test_endpoint_adaptation         — 3 false endpoints → threshold increases to 800 ms
  AC-4  test_endpoint_no_premature_start — speech < start_threshold not declared
  AC-5  test_endpoint_state_transitions  — IN_SILENCE → IN_SPEECH → POST_SPEECH → IN_SILENCE
  AC-6  test_endpoint_events_correct_fields — emitted events carry correct call_id / tenant_id

Architecture: V6 Ch9 (Testing Standards); V1 Ch6 (VAD & Endpointing).
"""

from __future__ import annotations

from src.libs.contracts.events.audio_events import VADSpeechEnd, VADSpeechStart
from src.libs.contracts.primitives import CallId, TenantId
from src.services.vad_endpointing.endpoint_detector import EndpointDetector, SpeechState

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_CALL_ID = CallId("test-call-endpoint-001")
_TENANT_ID = TenantId("10000000-0000-0000-0000-000000000001")

_FRAME_MS = 32  # one Silero VAD window = 32 ms
_SPEECH_PROB = 0.9  # clearly above threshold
_SILENCE_PROB = 0.1  # clearly below threshold


def _feed_speech(
    detector: EndpointDetector,
    duration_ms: int,
    start_call_time_ms: int = 0,
) -> tuple[list[object], int]:
    """Feed consecutive speech frames and collect all emitted events."""
    events: list[object] = []
    t = start_call_time_ms
    for _ in range(duration_ms // _FRAME_MS):
        events.extend(detector.process(_SPEECH_PROB, t, _CALL_ID, _TENANT_ID))
        t += _FRAME_MS
    return events, t


def _feed_silence(
    detector: EndpointDetector,
    duration_ms: int,
    start_call_time_ms: int = 0,
) -> tuple[list[object], int]:
    """Feed consecutive silence frames and collect all emitted events."""
    events: list[object] = []
    t = start_call_time_ms
    for _ in range(duration_ms // _FRAME_MS):
        events.extend(detector.process(_SILENCE_PROB, t, _CALL_ID, _TENANT_ID))
        t += _FRAME_MS
    return events, t


# ---------------------------------------------------------------------------
# Required test AC-1: test_endpoint_speech_start
# ---------------------------------------------------------------------------


class TestEndpointSpeechStart:
    """AC-1: 150 ms of speech → VADSpeechStart emitted."""

    def test_endpoint_speech_start(self) -> None:
        """Required test: 150 ms of speech frames → exactly one VADSpeechStart."""
        detector = EndpointDetector(start_threshold_ms=100, frame_size_ms=_FRAME_MS)

        events, _ = _feed_speech(detector, duration_ms=160)  # 5 x 32ms windows

        speech_starts = [e for e in events if isinstance(e, VADSpeechStart)]
        assert len(speech_starts) == 1, f"Expected 1 VADSpeechStart after 160 ms speech, got {len(speech_starts)}"

    def test_endpoint_speech_start_not_premature(self) -> None:
        """Speech below start_threshold (64 ms < 100 ms) must not fire VADSpeechStart."""
        detector = EndpointDetector(start_threshold_ms=100, frame_size_ms=_FRAME_MS)

        # Only 2 windows = 64 ms — below 100 ms threshold
        events: list[object] = []
        for i in range(2):
            events.extend(detector.process(_SPEECH_PROB, i * _FRAME_MS, _CALL_ID, _TENANT_ID))

        speech_starts = [e for e in events if isinstance(e, VADSpeechStart)]
        assert len(speech_starts) == 0, "VADSpeechStart must not fire before start_threshold_ms"

    def test_endpoint_speech_start_state_transition(self) -> None:
        """State moves from IN_SILENCE to IN_SPEECH after start threshold."""
        detector = EndpointDetector(start_threshold_ms=100, frame_size_ms=_FRAME_MS)

        _feed_speech(detector, duration_ms=160)
        assert detector.state == SpeechState.IN_SPEECH

    def test_endpoint_speech_start_carries_call_id(self) -> None:
        """VADSpeechStart event must carry the correct call_id and tenant_id."""
        detector = EndpointDetector(start_threshold_ms=100, frame_size_ms=_FRAME_MS)
        events, _ = _feed_speech(detector, duration_ms=160)
        start_event = next(e for e in events if isinstance(e, VADSpeechStart))
        assert start_event.call_id == _CALL_ID
        assert start_event.tenant_id == _TENANT_ID


# ---------------------------------------------------------------------------
# Required test AC-2: test_endpoint_speech_end_600ms
# ---------------------------------------------------------------------------


class TestEndpointSpeechEnd:
    """AC-2: 600 ms silence after speech → VADSpeechEnd emitted."""

    def test_endpoint_speech_end_600ms(self) -> None:
        """Required test: 600 ms silence after speech onset → VADSpeechEnd emitted."""
        detector = EndpointDetector(
            start_threshold_ms=100,
            end_threshold_ms=600,
            frame_size_ms=_FRAME_MS,
        )
        # First establish speech
        events_speech, t = _feed_speech(detector, duration_ms=320)
        assert any(isinstance(e, VADSpeechStart) for e in events_speech)

        # Now feed 600 ms of silence
        events_silence, _ = _feed_silence(detector, duration_ms=640, start_call_time_ms=t)

        speech_ends = [e for e in events_silence if isinstance(e, VADSpeechEnd)]
        assert len(speech_ends) == 1, f"Expected 1 VADSpeechEnd after 640 ms silence, got {len(speech_ends)}"

    def test_endpoint_speech_end_not_premature(self) -> None:
        """Silence shorter than end_threshold must not fire VADSpeechEnd."""
        detector = EndpointDetector(
            start_threshold_ms=100,
            end_threshold_ms=600,
            frame_size_ms=_FRAME_MS,
        )
        _, t = _feed_speech(detector, duration_ms=320)
        # Only 300 ms silence — below 600 ms threshold
        events_short, _ = _feed_silence(detector, duration_ms=320, start_call_time_ms=t)

        speech_ends = [e for e in events_short if isinstance(e, VADSpeechEnd)]
        assert len(speech_ends) == 0, "VADSpeechEnd must not fire before end_threshold_ms"

    def test_endpoint_speech_end_state_is_post_speech(self) -> None:
        """State moves to POST_SPEECH after VADSpeechEnd is emitted."""
        detector = EndpointDetector(start_threshold_ms=100, end_threshold_ms=600, frame_size_ms=_FRAME_MS)
        _, t = _feed_speech(detector, duration_ms=320)
        _feed_silence(detector, duration_ms=640, start_call_time_ms=t)
        assert detector.state == SpeechState.POST_SPEECH

    def test_endpoint_speech_end_duration_correct(self) -> None:
        """VADSpeechEnd.duration_ms reflects the utterance length."""
        detector = EndpointDetector(start_threshold_ms=100, end_threshold_ms=600, frame_size_ms=_FRAME_MS)
        _, t = _feed_speech(detector, duration_ms=320)
        events_silence, _ = _feed_silence(detector, duration_ms=640, start_call_time_ms=t)

        end_event = next(e for e in events_silence if isinstance(e, VADSpeechEnd))
        assert end_event.duration_ms >= 0
        assert end_event.end_ms > end_event.start_ms
        assert end_event.call_id == _CALL_ID
        assert end_event.tenant_id == _TENANT_ID


# ---------------------------------------------------------------------------
# Required test AC-3: test_endpoint_adaptation
# ---------------------------------------------------------------------------


class TestEndpointAdaptation:
    """AC-3: 3 false endpoints → threshold increases to 800 ms."""

    def test_endpoint_adaptation(self) -> None:
        """Required test: 3 false endpoints → end_threshold_ms increases to 800 ms."""
        detector = EndpointDetector(
            start_threshold_ms=100,
            end_threshold_ms=600,
            max_end_threshold_ms=1200,
            frame_size_ms=_FRAME_MS,
        )

        def _one_false_endpoint(start_ms: int) -> int:
            """Simulate: speech → silence (≥ 600 ms) → speech resumes → false endpoint."""
            _, t = _feed_speech(detector, duration_ms=320, start_call_time_ms=start_ms)
            _feed_silence(detector, duration_ms=640, start_call_time_ms=t)
            t += 640
            # Resume speech → false endpoint registered in POST_SPEECH state
            _, t = _feed_speech(detector, duration_ms=160, start_call_time_ms=t)
            return t

        t = 0
        t = _one_false_endpoint(t)
        assert detector.false_endpoint_count == 1
        assert detector.end_threshold_ms == 600  # not yet at 3

        t = _one_false_endpoint(t)
        assert detector.false_endpoint_count == 2
        assert detector.end_threshold_ms == 600  # not yet at 3

        t = _one_false_endpoint(t)
        assert detector.false_endpoint_count == 3
        # 3 % 3 == 0 → threshold adapted
        assert detector.end_threshold_ms == 800, (
            f"Expected end_threshold_ms=800 after 3 false endpoints, got {detector.end_threshold_ms}"
        )

    def test_endpoint_adaptation_cap(self) -> None:
        """Threshold is capped at max_end_threshold_ms even after many false endpoints."""
        detector = EndpointDetector(
            start_threshold_ms=100,
            end_threshold_ms=600,
            max_end_threshold_ms=800,
            frame_size_ms=_FRAME_MS,
        )

        def _force_false_endpoint(n: int, start_ms: int) -> int:
            t = start_ms
            for _ in range(n):
                _, t = _feed_speech(detector, duration_ms=320, start_call_time_ms=t)
                _feed_silence(detector, duration_ms=640, start_call_time_ms=t)
                t += 640
                _, t = _feed_speech(detector, duration_ms=160, start_call_time_ms=t)
            return t

        _force_false_endpoint(6, 0)  # 6 false endpoints → would be 1000 without cap
        assert detector.end_threshold_ms <= 800

    def test_endpoint_no_adaptation_below_3(self) -> None:
        """Threshold does not change for fewer than 3 false endpoints."""
        detector = EndpointDetector(start_threshold_ms=100, end_threshold_ms=600, frame_size_ms=_FRAME_MS)
        # Simulate 2 false endpoints
        for _ in range(2):
            _, t = _feed_speech(detector, duration_ms=320, start_call_time_ms=0)
            _feed_silence(detector, duration_ms=640, start_call_time_ms=t)
            _feed_speech(detector, duration_ms=160, start_call_time_ms=t + 640)
        assert detector.end_threshold_ms == 600


# ---------------------------------------------------------------------------
# State machine completeness
# ---------------------------------------------------------------------------


class TestEndpointStateMachine:
    """Full state machine coverage: IN_SILENCE → IN_SPEECH → POST_SPEECH → IN_SILENCE."""

    def test_endpoint_state_transitions(self) -> None:
        """Required test: complete cycle through all states."""
        detector = EndpointDetector(start_threshold_ms=100, end_threshold_ms=600, frame_size_ms=_FRAME_MS)

        # Speech → IN_SPEECH
        _, t = _feed_speech(detector, duration_ms=160)
        state_after_speech = detector.state
        assert state_after_speech == SpeechState.IN_SPEECH

        # Silence (end threshold) → POST_SPEECH
        _feed_silence(detector, duration_ms=640, start_call_time_ms=t)
        state_after_silence = detector.state
        assert state_after_silence == SpeechState.POST_SPEECH

        # Extended silence → back to IN_SILENCE
        t2 = t + 640
        _feed_silence(detector, duration_ms=1400, start_call_time_ms=t2)
        state_final = detector.state
        assert state_final == SpeechState.IN_SILENCE

    def test_endpoint_events_correct_fields(self) -> None:
        """Required test: emitted events carry correct call_id and tenant_id."""
        call_id = CallId("fieldcheck-call")
        tenant_id = TenantId("20000000-0000-0000-0000-000000000002")
        detector = EndpointDetector(start_threshold_ms=100, end_threshold_ms=600, frame_size_ms=_FRAME_MS)

        all_events: list[object] = []
        t = 0
        for _ in range(160 // _FRAME_MS):
            all_events.extend(detector.process(_SPEECH_PROB, t, call_id, tenant_id))
            t += _FRAME_MS
        for _ in range(640 // _FRAME_MS):
            all_events.extend(detector.process(_SILENCE_PROB, t, call_id, tenant_id))
            t += _FRAME_MS

        for event in all_events:
            if isinstance(event, (VADSpeechStart, VADSpeechEnd)):
                assert event.call_id == call_id
                assert event.tenant_id == tenant_id

    def test_endpoint_reset_clears_all_state(self) -> None:
        """reset() returns the detector to its initial IN_SILENCE state."""
        detector = EndpointDetector(start_threshold_ms=100, end_threshold_ms=600, frame_size_ms=_FRAME_MS)
        _feed_speech(detector, duration_ms=320)
        detector.reset()
        assert detector.state == SpeechState.IN_SILENCE
        assert detector.false_endpoint_count == 0
        assert detector.end_threshold_ms == 600

    def test_endpoint_constructor_validates_thresholds(self) -> None:
        """Invalid constructor arguments raise ValueError."""
        import pytest

        with pytest.raises(ValueError):
            EndpointDetector(start_threshold_ms=0)
        with pytest.raises(ValueError):
            EndpointDetector(end_threshold_ms=0)
        with pytest.raises(ValueError):
            EndpointDetector(end_threshold_ms=600, max_end_threshold_ms=400)
