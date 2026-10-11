"""AudioPacer — 24 kHz PCM16LE → 8 kHz G.711 μ-law 20 ms frame packetizer.

Accepts PCM16LE audio chunks at 24 kHz (from the Veena TTS service),
resamples to 8 kHz via integer decimation (factor=3, exact for 24k/8k),
encodes to G.711 μ-law, and emits 20 ms frames (160 bytes each) matching
the Twilio Media Streams playback clock.

Critically, it tracks buffer depth, producer/consumer rate balance, and
underrun events. An underrun (buffer empty when a frame is requested) means
silence is inserted mid-stream — this is the "Na--mas--te" failure mode where
the playback consumer outpaces the TTS producer.

Audio contracts:
  Input  : PCM16LE, 24 kHz, mono, signed 16-bit little-endian
  Output : G.711 μ-law, 8 kHz, mono, 1 byte/sample, 160 bytes per 20 ms frame
  Frame  : 160 bytes = 160 samples at 8 kHz = 20 ms
  Source : 480 samples at 24 kHz = 960 bytes PCM16LE per output frame

Architecture: V1 Ch17 (Veena TTS → telephony), V1 Ch21 (Playback / delivery).
"""

from __future__ import annotations

import collections
import struct
import threading
import time
from collections.abc import Iterator


# ── Audio constants ───────────────────────────────────────────────────────────

SOURCE_RATE: int = 24_000     # Hz — Veena TTS native sample rate
TARGET_RATE: int = 8_000      # Hz — G.711 telephony (Twilio)
FRAME_MS: int = 20            # ms — Twilio Media Streams standard frame
DECIMATE_FACTOR: int = SOURCE_RATE // TARGET_RATE  # = 3 (exact integer division)

# Samples per 20 ms frame at each rate
_SRC_SAMPLES_PER_FRAME: int = SOURCE_RATE * FRAME_MS // 1000   # 480 PCM16 samples
_TGT_SAMPLES_PER_FRAME: int = TARGET_RATE * FRAME_MS // 1000   # 160 μ-law bytes

# Bytes per 20 ms frame at source rate (2 bytes per PCM16 sample)
_SRC_BYTES_PER_FRAME: int = _SRC_SAMPLES_PER_FRAME * 2         # 960 bytes

# Silence frames (all-zero μ-law maps to a specific bit pattern, but 0xFF
# is the μ-law encoding of PCM silence near 0 — use actual μ-law(0)):
_SILENCE_SAMPLE: int = 0xFF  # μ-law encoding of PCM16 value 0 → ~0xFF (or 0x7F depending on spec)
_SILENCE_FRAME: bytes = bytes([0xFF] * _TGT_SAMPLES_PER_FRAME)  # 160 bytes of μ-law silence

assert DECIMATE_FACTOR * TARGET_RATE == SOURCE_RATE, \
    f"Source rate {SOURCE_RATE} must be exact multiple of target rate {TARGET_RATE}"


# ── G.711 μ-law encoder (pure Python, ITU-T G.711 standard algorithm) ────────

# Segment end points (exclusive) in biased-PCM space (sample + BIAS).
# Segment 0: [0x00FF, 0x01FF], seg 1: [0x0200, 0x03FF], ..., seg 7: [0x4000, 0x7FFF]
_ULAW_SEG_END: tuple[int, ...] = (0x00FF, 0x01FF, 0x03FF, 0x07FF, 0x0FFF, 0x1FFF, 0x3FFF, 0x7FFF)
_ULAW_BIAS: int = 0x84   # 132 — added before log quantization
_ULAW_CLIP: int = 32635  # maximum magnitude before clipping


def _lin2ulaw(sample: int) -> int:
    """Encode a signed PCM16 sample to one G.711 μ-law byte.

    Args:
        sample: Signed 16-bit linear PCM value in [-32768, 32767].

    Returns:
        G.711 μ-law compressed value in [0, 255].
    """
    sign = 0x80 if sample < 0 else 0x00
    if sample < 0:
        sample = -sample
    if sample > _ULAW_CLIP:
        sample = _ULAW_CLIP
    sample += _ULAW_BIAS

    seg = 8  # default: beyond range (should not happen after clip)
    for i, end in enumerate(_ULAW_SEG_END):
        if sample <= end:
            seg = i
            break

    if seg >= 8:
        return 0x7F ^ sign  # saturate

    mantissa = (sample >> (seg + 3)) & 0x0F
    return (~(sign | (seg << 4) | mantissa)) & 0xFF


def pcm16le_to_ulaw(pcm16_bytes: bytes) -> bytes:
    """Encode a PCM16LE byte buffer to G.711 μ-law (same sample count, 1 byte/sample).

    Args:
        pcm16_bytes: PCM16LE bytes (must be 2-byte aligned).

    Returns:
        μ-law bytes (len = len(pcm16_bytes) // 2).
    """
    n = len(pcm16_bytes) // 2
    samples = struct.unpack(f"<{n}h", pcm16_bytes)
    return bytes(_lin2ulaw(s) for s in samples)


def ulaw_to_pcm16le(ulaw_bytes: bytes) -> bytes:
    """Decode G.711 μ-law bytes to PCM16LE (for testing/validation)."""
    # Expansion table: 256 entry lookup for fast decode
    result = []
    for byte in ulaw_bytes:
        byte = ~byte & 0xFF
        sign = (byte & 0x80)
        seg = (byte & 0x70) >> 4
        mantissa = byte & 0x0F
        sample = ((mantissa << 3) + _ULAW_BIAS) << seg
        sample -= _ULAW_BIAS
        if sign:
            sample = -sample
        result.append(max(-32768, min(32767, sample)))
    return struct.pack(f"<{len(result)}h", *result)


# ── Resampler: 24 kHz PCM16 → 8 kHz PCM16 (integer decimation) ───────────────


def decimate_pcm16(pcm16_bytes: bytes, factor: int = DECIMATE_FACTOR) -> bytes:
    """Downsample PCM16LE by taking every Nth sample (decimation, no filter).

    Args:
        pcm16_bytes: PCM16LE bytes at source rate (2-byte aligned).
        factor: Decimation factor. factor=3 converts 24 kHz → 8 kHz exactly.

    Returns:
        Decimated PCM16LE bytes (len = len(pcm16_bytes) // (factor * 2) * 2).

    Note: No anti-aliasing filter. Acceptable for speech (telephony Nyquist at
    4 kHz is sufficient) and exact for test/validation.
    """
    n = len(pcm16_bytes) // 2
    samples = struct.unpack(f"<{n}h", pcm16_bytes)
    decimated = samples[::factor]
    if not decimated:
        return b""
    return struct.pack(f"<{len(decimated)}h", *decimated)


# ── AudioPacer ────────────────────────────────────────────────────────────────


class AudioPacer:
    """Converts 24 kHz PCM16LE TTS audio into 20 ms G.711 μ-law Twilio frames.

    The pacer buffers incoming audio and serves it at the telephony playback
    rate (160 bytes of 8 kHz μ-law per 20 ms).

    Thread-safe: feed() may be called from a producer thread while drain_frame()
    is called from the Twilio sender loop.

    Underrun behaviour:
        When drain_frame() is called but the buffer holds fewer samples than
        one frame, the pacer inserts a silence frame AND increments
        underrun_count. Underruns mean "Na--mas--te"-style broken-word gaps.
        The caller MUST check has_underrun / underrun_count after the stream
        ends to detect this failure mode.

    Cancellation:
        Call cancel() to signal that the producer has stopped early (e.g.,
        barge-in). After cancellation, drain_frame() returns None immediately
        so the Twilio sender can stop without draining the remaining buffer.
    """

    def __init__(
        self,
        source_rate: int = SOURCE_RATE,
        target_rate: int = TARGET_RATE,
        frame_ms: int = FRAME_MS,
    ) -> None:
        if source_rate % target_rate != 0:
            raise ValueError(
                f"source_rate ({source_rate}) must be an exact multiple of target_rate ({target_rate})"
            )
        self._decimate: int = source_rate // target_rate
        self._src_samples_per_frame: int = source_rate * frame_ms // 1000
        self._src_bytes_per_frame: int = self._src_samples_per_frame * 2
        self._tgt_samples_per_frame: int = target_rate * frame_ms // 1000

        # Internal PCM16LE buffer at source rate
        self._buf: bytearray = bytearray()
        self._lock: threading.Lock = threading.Lock()
        self._cancelled: threading.Event = threading.Event()

        # Metrics
        self.underrun_count: int = 0
        self.frames_produced: int = 0
        self.frames_silence: int = 0
        self._bytes_fed: int = 0
        self._bytes_drained: int = 0
        self._t_first_feed: float = 0.0
        self._t_last_feed: float = 0.0
        self._t_first_drain: float = 0.0
        self._t_last_drain: float = 0.0
        self._buf_depth_log: collections.deque[tuple[float, int]] = collections.deque(maxlen=1000)

    # ── Producer interface ────────────────────────────────────────────────────

    def feed(self, pcm16_bytes: bytes) -> None:
        """Add PCM16LE audio to the internal buffer.

        Args:
            pcm16_bytes: PCM16LE bytes at source_rate (must be 2-byte aligned).

        Raises:
            ValueError: If data is not 2-byte aligned.
        """
        if len(pcm16_bytes) % 2 != 0:
            raise ValueError(
                f"PCM16LE data must be 2-byte aligned, got {len(pcm16_bytes)} bytes"
            )
        now = time.monotonic()
        with self._lock:
            self._buf.extend(pcm16_bytes)
            self._bytes_fed += len(pcm16_bytes)
            if self._t_first_feed == 0.0:
                self._t_first_feed = now
            self._t_last_feed = now
            self._buf_depth_log.append((now, len(self._buf) // 2))

    def cancel(self) -> None:
        """Signal cancellation. drain_frame() returns None immediately after this."""
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    # ── Consumer interface ────────────────────────────────────────────────────

    def drain_frame(self) -> bytes | None:
        """Consume one 20 ms frame of G.711 μ-law audio (160 bytes at 8 kHz).

        Returns:
            160 bytes of G.711 μ-law audio, or a silence frame if buffer was
            too shallow (underrun), or None if cancelled.

        Side effects:
            Increments underrun_count and frames_silence on buffer underrun.
            Increments frames_produced on every call (including underruns).
        """
        if self._cancelled.is_set():
            return None

        now = time.monotonic()
        if self._t_first_drain == 0.0:
            self._t_first_drain = now
        self._t_last_drain = now

        with self._lock:
            available_bytes = len(self._buf)
            is_underrun = available_bytes < self._src_bytes_per_frame

            if is_underrun:
                # Take whatever we have; pad to one frame with zeros
                raw = bytes(self._buf)
                self._buf.clear()
                if len(raw) < self._src_bytes_per_frame:
                    raw = raw.ljust(self._src_bytes_per_frame, b"\x00")
            else:
                raw = bytes(self._buf[: self._src_bytes_per_frame])
                del self._buf[: self._src_bytes_per_frame]
                self._bytes_drained += self._src_bytes_per_frame

            depth = len(self._buf) // 2
            self._buf_depth_log.append((now, depth))

        if is_underrun:
            self.underrun_count += 1
            self.frames_silence += 1

        # Resample: 24 kHz → 8 kHz (decimate by 3)
        pcm8k = decimate_pcm16(raw, self._decimate)
        # Encode: PCM16LE → G.711 μ-law
        ulaw = pcm16le_to_ulaw(pcm8k)
        self.frames_produced += 1
        return ulaw

    def drain_all_frames(self) -> Iterator[bytes]:
        """Drain all complete 20 ms frames from the current buffer.

        Yields G.711 μ-law frames until the buffer has fewer than one frame's
        worth of samples remaining. Does not drain partial frames.
        """
        while not self._cancelled.is_set():
            with self._lock:
                has_frame = len(self._buf) >= self._src_bytes_per_frame
            if not has_frame:
                break
            frame = self.drain_frame()
            if frame is None:
                break
            yield frame

    # ── Monitoring ────────────────────────────────────────────────────────────

    @property
    def buffer_depth_samples(self) -> int:
        """Current buffer depth in PCM16 samples at source rate."""
        with self._lock:
            return len(self._buf) // 2

    @property
    def buffer_depth_ms(self) -> float:
        """Current buffer depth in milliseconds of audio at source rate."""
        return self.buffer_depth_samples / SOURCE_RATE * 1000

    @property
    def has_underrun(self) -> bool:
        """True if at least one underrun has occurred."""
        return self.underrun_count > 0

    @property
    def total_audio_fed_ms(self) -> float:
        """Total milliseconds of audio fed to the pacer (at source rate)."""
        return (self._bytes_fed // 2) / SOURCE_RATE * 1000

    @property
    def total_audio_drained_ms(self) -> float:
        """Total milliseconds of audio successfully drained (no underrun frames)."""
        return (self._bytes_drained // 2) / SOURCE_RATE * 1000

    @property
    def producer_rate_ms_per_s(self) -> float:
        """Audio produced per real-time second (ms of audio / s of wall time)."""
        elapsed = self._t_last_feed - self._t_first_feed
        if elapsed <= 0 or self._bytes_fed == 0:
            return 0.0
        audio_ms = (self._bytes_fed // 2) / SOURCE_RATE * 1000
        return audio_ms / elapsed

    @property
    def peak_buffer_depth_ms(self) -> float:
        """Peak buffer depth seen during this session (ms)."""
        if not self._buf_depth_log:
            return 0.0
        return max(d for _, d in self._buf_depth_log) / SOURCE_RATE * 1000

    def report(self) -> dict[str, object]:
        """Return a monitoring snapshot of the pacer state."""
        return {
            "buffer_depth_ms": round(self.buffer_depth_ms, 1),
            "peak_buffer_depth_ms": round(self.peak_buffer_depth_ms, 1),
            "frames_produced": self.frames_produced,
            "frames_silence": self.frames_silence,
            "underrun_count": self.underrun_count,
            "has_underrun": self.has_underrun,
            "total_audio_fed_ms": round(self.total_audio_fed_ms, 1),
            "total_audio_drained_ms": round(self.total_audio_drained_ms, 1),
            "producer_rate_ms_per_s": round(self.producer_rate_ms_per_s, 1),
            "cancelled": self.is_cancelled,
        }


# ── Twilio frame builder ──────────────────────────────────────────────────────


def build_twilio_media_message(ulaw_frame: bytes, stream_sid: str) -> dict[str, object]:
    """Build a Twilio Media Streams outbound 'media' message from a μ-law frame.

    Args:
        ulaw_frame: G.711 μ-law bytes (typically 160 bytes for 20 ms at 8 kHz).
        stream_sid: The Twilio stream SID (required on every outbound message).

    Returns:
        JSON-serialisable dict ready for websocket.send_json().
    """
    import base64

    return {
        "event": "media",
        "streamSid": stream_sid,
        "media": {
            "payload": base64.b64encode(ulaw_frame).decode("ascii"),
        },
    }


def validate_ulaw_frame(frame: bytes, expected_bytes: int = _TGT_SAMPLES_PER_FRAME) -> bool:
    """Validate that a frame is a plausible G.711 μ-law frame.

    Args:
        frame: The bytes to validate.
        expected_bytes: Expected frame size (default 160 for 20 ms at 8 kHz).

    Returns:
        True if the frame has the correct size and all bytes are valid μ-law values.
    """
    if len(frame) != expected_bytes:
        return False
    # All byte values 0–255 are valid μ-law, so the only check is size.
    return True
