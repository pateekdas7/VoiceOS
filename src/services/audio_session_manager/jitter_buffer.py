"""AdaptiveJitterBuffer — reorders out-of-order RTP frames and adapts delay.

Stores incoming audio frames indexed by RTP sequence number and releases
them in strict sequence order.  The target delay is adapted based on the
observed inter-arrival jitter so the buffer stays as shallow as possible
while still absorbing network variation.

Architecture: V1 Ch4 (AdaptiveJitterBuffer — target delay, min/max bounds,
              reorder window, overflow / RI-3 bounded memory).
"""

from __future__ import annotations

from collections import deque

from src.libs.contracts.audio import AudioFrame


class AdaptiveJitterBuffer:
    """Adaptive jitter buffer for RTP audio frames.

    Frames are accepted via ``push()`` in any arrival order and returned
    via ``pop()`` (streaming) or ``drain()`` (batch) in ascending sequence
    number order.

    Overflow protection (RI-3): when the buffer would exceed ``max_depth``
    the oldest frame (lowest sequence number) is dropped before the new
    frame is inserted.  This bounds memory at ``max_depth`` frames
    regardless of how many packets are pushed.

    Jitter adaptation: every ``push()`` updates the running mean jitter
    estimate from inter-arrival intervals.  The ``target_delay_ms``
    adjusts within [``min_delay_ms``, ``max_delay_ms``] accordingly.

    Architecture: V1 Ch4 (Audio Session Manager — AdaptiveJitterBuffer).
    """

    def __init__(
        self,
        target_delay_ms: int = 80,
        min_delay_ms: int = 20,
        max_delay_ms: int = 200,
        max_reorder: int = 5,
        max_depth: int = 50,
    ) -> None:
        """Initialise the jitter buffer.

        Args:
            target_delay_ms: Initial target delay in milliseconds.
            min_delay_ms:    Minimum allowed target delay.
            max_delay_ms:    Maximum allowed target delay.
            max_reorder:     Maximum number of out-of-order sequence numbers
                             to tolerate before considering a packet lost.
            max_depth:       Hard maximum number of buffered frames (RI-3).
        """
        self._target_delay_ms = target_delay_ms
        self._min_delay_ms = min_delay_ms
        self._max_delay_ms = max_delay_ms
        self._max_reorder = max_reorder
        self._max_depth = max_depth

        self._buffer: dict[int, AudioFrame] = {}
        self._next_seq: int | None = None

        # Jitter estimation: last 50 inter-arrival samples
        self._jitter_samples: deque[float] = deque(maxlen=50)
        self._last_arrival_s: float | None = None

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def push(self, frame: AudioFrame) -> None:
        """Add a frame to the buffer.

        If the buffer is at max_depth, the frame with the lowest sequence
        number (the oldest) is evicted first (RI-3: bounded memory).

        Args:
            frame: Audio frame to buffer.
        """
        if len(self._buffer) >= self._max_depth:
            oldest_seq = min(self._buffer)
            del self._buffer[oldest_seq]

        self._buffer[frame.seq] = frame
        self._update_jitter(frame)

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def pop(self) -> AudioFrame | None:
        """Remove and return the next frame in sequence order.

        On the first call the sequence counter is initialised to the
        minimum sequence number currently in the buffer.  Returns None
        when the expected next frame is not present.

        Returns:
            Next frame in sequence order, or None.
        """
        if not self._buffer:
            return None

        if self._next_seq is None:
            self._next_seq = min(self._buffer)

        frame = self._buffer.pop(self._next_seq, None)
        if frame is not None:
            self._next_seq += 1
        return frame

    def drain(self) -> list[AudioFrame]:
        """Return all buffered frames sorted by sequence number and clear the buffer.

        Returns:
            All buffered frames in ascending sequence order.
        """
        frames = [self._buffer[seq] for seq in sorted(self._buffer)]
        self._buffer.clear()
        self._next_seq = None
        return frames

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def depth(self) -> int:
        """Current number of frames in the buffer."""
        return len(self._buffer)

    @property
    def target_delay_ms(self) -> int:
        """Current adaptive target delay in milliseconds."""
        return self._target_delay_ms

    @property
    def max_reorder(self) -> int:
        """Maximum out-of-order sequence distance tolerated."""
        return self._max_reorder

    def current_jitter_ms(self) -> float:
        """Mean inter-arrival jitter estimate in milliseconds.

        Returns 0.0 until at least two frames have been pushed.
        """
        if not self._jitter_samples:
            return 0.0
        return sum(self._jitter_samples) / len(self._jitter_samples)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_jitter(self, frame: AudioFrame) -> None:
        """Update jitter estimate and adapt target_delay_ms."""
        now = frame.recv_ts
        if self._last_arrival_s is not None:
            interval_ms = (now - self._last_arrival_s) * 1000.0
            expected_ms = float(frame.config.frame_duration_ms)
            jitter = abs(interval_ms - expected_ms)
            self._jitter_samples.append(jitter)
            avg = sum(self._jitter_samples) / len(self._jitter_samples)
            new_target = int(avg * 2.0 + expected_ms)
            self._target_delay_ms = max(self._min_delay_ms, min(new_target, self._max_delay_ms))
        self._last_arrival_s = now
