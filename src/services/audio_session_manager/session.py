"""AudioSession — per-call session state machine.

Each call has exactly one AudioSession instance.  It manages the complete
media-plane lifecycle from the first RTP packet (CONNECTING → ACTIVE) to
orderly teardown (ENDING → CLOSED), including barge-in interruption.

Responsibilities per V1 Ch4:
  - Hold the AdaptiveJitterBuffer, PacketLossConcealer, and SessionClock.
  - Detect sequence gaps; fill them with PLC frames before the gap arrives.
  - Emit output frames in correct sequence order from the jitter buffer.
  - Enforce state-machine transitions; reject frames outside active states.

Architecture: V1 Ch4 (AudioSession — session state machine + lifecycle).
"""

from __future__ import annotations

from enum import StrEnum

from src.libs.contracts.audio import AudioFrame
from src.services.audio_session_manager.clock import SessionClock
from src.services.audio_session_manager.jitter_buffer import AdaptiveJitterBuffer
from src.services.audio_session_manager.plc import PacketLossConcealer


class SessionState(StrEnum):
    """Audio session lifecycle states.

    Transitions:
        CONNECTING → ACTIVE     on first frame received
        ACTIVE     → BARGE_IN   on BargeinDetected event
        BARGE_IN   → ACTIVE     when barge-in ends
        ACTIVE     → ENDING     on session end signal
        BARGE_IN   → ENDING     on session end signal
        ENDING     → CLOSED     after clean resource release
    """

    CONNECTING = "CONNECTING"
    ACTIVE = "ACTIVE"
    BARGE_IN = "BARGE_IN"
    ENDING = "ENDING"
    CLOSED = "CLOSED"


_MEDIA_ACTIVE_STATES: frozenset[SessionState] = frozenset({SessionState.ACTIVE, SessionState.BARGE_IN})


class AudioSession:
    """Per-call audio session with state machine lifecycle management.

    Owns the jitter buffer, PLC, and clock for one call leg.  Callers push
    raw frames in arrival order; the session reorders, fills gaps with PLC,
    and returns frames ready for the audio preprocessor.

    Architecture: V1 Ch4 (Audio Session Manager — AudioSession).
    """

    def __init__(self, call_id: str, tenant_id: str) -> None:
        """Create a new session in CONNECTING state.

        Args:
            call_id:   Unique call identifier (matches Media Gateway call_id).
            tenant_id: Owning tenant (AR-8 — all resources are tenant-scoped).
        """
        self._call_id = call_id
        self._tenant_id = tenant_id
        self._state: SessionState = SessionState.CONNECTING
        self._jitter_buffer = AdaptiveJitterBuffer()
        self._plc = PacketLossConcealer()
        self._clock = SessionClock()
        self._last_seq: int | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def call_id(self) -> str:
        """Unique call identifier."""
        return self._call_id

    @property
    def tenant_id(self) -> str:
        """Owning tenant identifier."""
        return self._tenant_id

    @property
    def state(self) -> SessionState:
        """Current lifecycle state."""
        return self._state

    @property
    def jitter_buffer(self) -> AdaptiveJitterBuffer:
        """The session's jitter buffer (exposed for metrics and testing)."""
        return self._jitter_buffer

    @property
    def clock(self) -> SessionClock:
        """The session clock (exposed for metrics and testing)."""
        return self._clock

    # ------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------

    def push_frame(self, frame: AudioFrame) -> list[AudioFrame]:
        """Process an incoming audio frame from the transport layer.

        Transitions CONNECTING → ACTIVE on the first frame and anchors the
        session clock.  Detects sequence gaps and fills them with PLC frames.
        Pushes both real and PLC frames into the jitter buffer, then pops and
        returns all frames that are ready in sequence order.

        Frames received while in ENDING or CLOSED state are silently dropped.

        Args:
            frame: Incoming raw audio frame from the transport adapter.

        Returns:
            Ordered list of frames (real + PLC) ready for the preprocessor.
        """
        if self._state == SessionState.CONNECTING:
            self._state = SessionState.ACTIVE
            self._clock.anchor(frame.rtp_ts, frame.recv_ts)

        if self._state not in _MEDIA_ACTIVE_STATES:
            return []

        # Detect sequence gap and synthesise PLC frames for the missing range.
        if self._last_seq is not None:
            expected = self._last_seq + 1
            if frame.seq > expected:
                gap = frame.seq - expected
                rtp_per_frame = frame.config.frame_duration_ms * (frame.config.sample_rate.value // 1000)
                plc_start_rtp = frame.rtp_ts - gap * rtp_per_frame
                for plc_frame in self._plc.conceal(gap, expected, plc_start_rtp):
                    self._jitter_buffer.push(plc_frame)

        self._plc.feed(frame)
        self._jitter_buffer.push(frame)
        self._last_seq = frame.seq

        output: list[AudioFrame] = []
        while True:
            ready = self._jitter_buffer.pop()
            if ready is None:
                break
            output.append(ready)
        return output

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def on_barge_in(self) -> None:
        """Handle BargeinDetected event — transition ACTIVE → BARGE_IN."""
        if self._state == SessionState.ACTIVE:
            self._state = SessionState.BARGE_IN

    def on_barge_in_end(self) -> None:
        """Handle barge-in completion — transition BARGE_IN → ACTIVE."""
        if self._state == SessionState.BARGE_IN:
            self._state = SessionState.ACTIVE

    def begin_ending(self) -> None:
        """Initiate orderly shutdown — transition to ENDING.

        Accepted from ACTIVE or BARGE_IN states only.
        """
        if self._state in _MEDIA_ACTIVE_STATES:
            self._state = SessionState.ENDING

    def close(self) -> None:
        """Complete session closure — transition ENDING → CLOSED.

        Drains and discards any remaining jitter buffer contents so that
        buffer memory is released (RI-3).  No-op if not in ENDING state.
        """
        if self._state == SessionState.ENDING:
            self._jitter_buffer.drain()
            self._state = SessionState.CLOSED
