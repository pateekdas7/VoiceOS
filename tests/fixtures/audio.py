"""Audio test fixtures for VoiceOS.

Provides FakeRTPStream for generating synthetic mu-law audio frames and
make_wav_bytes for creating minimal valid WAV byte data — both used across
unit, integration, and e2e tests.

Architecture: V1 Ch3 (Media Gateway outputs); DocSuite-02 A.1.
"""

from __future__ import annotations

import math
import struct
import time
from collections.abc import Iterator

from src.libs.contracts.audio import AudioConfig, AudioFrame, Encoding, SampleRate

# ---------------------------------------------------------------------------
# μ-law audio helpers
# ---------------------------------------------------------------------------

_MULAW_SILENCE: int = 0xFF
"""G.711 μ-law digital silence byte value."""

_SAMPLES_PER_FRAME_8K_20MS: int = 160
"""8000 Hz x 20 ms = 160 samples per RTP frame (telephony standard)."""


def _make_silence_payload(num_samples: int) -> bytes:
    """Create a μ-law silence payload (all 0xFF bytes)."""
    return bytes([_MULAW_SILENCE] * num_samples)


def _make_speech_payload(num_samples: int, *, seed: int = 0) -> bytes:
    """Create a pseudo-speech μ-law payload.

    Uses a 440 Hz sine wave quantized to 8-bit μ-law range to produce
    non-silence bytes that trigger VAD. Not real speech — only for testing.
    """
    result = bytearray(num_samples)
    for i in range(num_samples):
        # 440 Hz sine → 8-bit μ-law range (0x00..0xFE, excluding silence byte 0xFF)
        sample_f = math.sin(2.0 * math.pi * 440.0 * (i + seed) / 8000.0)
        result[i] = int(127 + 126 * sample_f) & 0xFF
    return bytes(result)


def make_wav_bytes(
    *,
    sample_rate: int = 8000,
    num_samples: int = 160,
    silence: bool = True,
) -> bytes:
    """Create a minimal valid WAV file as bytes (8-bit mono PCM).

    Useful for producing file-system audio fixtures. WAV structure:
    RIFF header (12 B) + fmt chunk (24 B) + data chunk (8 B + samples).

    Args:
        sample_rate: Sample rate in Hz (default 8000 for telephony).
        num_samples: Number of audio samples.
        silence: If True, fill with PCM silence (0x80); else sine wave.

    Returns:
        Bytes of a valid mono 8-bit WAV file.
    """
    channels = 1
    bits_per_sample = 8
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8

    if silence:
        audio_data = bytes([0x80] * num_samples)
    else:
        audio_data = bytes(
            int(128 + 127 * math.sin(2.0 * math.pi * 440.0 * i / sample_rate)) & 0xFF for i in range(num_samples)
        )

    data_size = len(audio_data)
    riff_size = 36 + data_size  # 36 = fmt chunk (24) + data header (8) + WAVE tag (4)

    riff_header = struct.pack("<4sI4s", b"RIFF", riff_size, b"WAVE")
    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,  # PCM fmt chunk body is always 16 bytes
        1,  # PCM format type
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
    )
    data_chunk = struct.pack("<4sI", b"data", data_size) + audio_data

    return riff_header + fmt_chunk + data_chunk


# ---------------------------------------------------------------------------
# FakeRTPStream
# ---------------------------------------------------------------------------


class FakeRTPStream:
    """Generates synthetic μ-law audio frames at configurable RTP sequence.

    Simulates the output of the Media Gateway (V1 Ch3) for use in VAD,
    preprocessing, and STT unit and integration tests. Produces valid
    AudioFrame instances per V1 Appendix A / DocSuite-02 A.1.

    Sequencing follows RFC 3550:
    - seq increments by 1 per frame, wraps at 2^16 (65536).
    - rtp_ts increments by samples_per_frame (160 for 8 kHz / 20 ms).

    Encoding: MULAW at 8 kHz, 20 ms / frame, mono.
    Silence representation: 0xFF per G.711 μ-law convention.
    Speech representation: 440 Hz sine-encoded bytes (non-silence).
    """

    SAMPLES_PER_FRAME: int = _SAMPLES_PER_FRAME_8K_20MS
    SEQ_WRAP: int = 0x10000  # 2^16, per RFC 3550 §5.1

    def __init__(
        self,
        *,
        sample_rate: SampleRate = SampleRate.RATE_8K,
        frame_duration_ms: int = 20,
        start_seq: int = 0,
        start_rtp_ts: int = 0,
        is_speech: bool = False,
    ) -> None:
        self._config = AudioConfig(
            sample_rate=sample_rate,
            encoding=Encoding.MULAW,
            channels=1,
            frame_duration_ms=frame_duration_ms,
        )
        self._seq: int = start_seq
        self._rtp_ts: int = start_rtp_ts
        self._is_speech: bool = is_speech
        self._samples_per_frame: int = int(sample_rate.value * frame_duration_ms / 1000)
        self._frame_count: int = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> AudioConfig:
        """The AudioConfig used for all generated frames."""
        return self._config

    @property
    def current_seq(self) -> int:
        """The next RTP sequence number that will be assigned."""
        return self._seq

    @property
    def current_rtp_ts(self) -> int:
        """The next RTP timestamp that will be assigned."""
        return self._rtp_ts

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def toggle_speech(self, *, is_speech: bool) -> None:
        """Switch between silence and speech frame generation.

        Args:
            is_speech: True for speech frames, False for silence frames.
        """
        self._is_speech = is_speech

    # ------------------------------------------------------------------
    # Frame generation
    # ------------------------------------------------------------------

    def generate_frame(self) -> AudioFrame:
        """Generate a single μ-law frame with the current speech/silence state.

        Returns:
            A new AudioFrame with auto-incremented seq and rtp_ts.
        """
        if self._is_speech:
            pcm_data = _make_speech_payload(self._samples_per_frame, seed=self._frame_count)
        else:
            pcm_data = _make_silence_payload(self._samples_per_frame)

        frame = AudioFrame(
            pcm_data=pcm_data,
            seq=self._seq,
            rtp_ts=self._rtp_ts,
            recv_ts=time.monotonic(),
            config=self._config,
        )

        self._seq = (self._seq + 1) % self.SEQ_WRAP
        self._rtp_ts += self._samples_per_frame
        self._frame_count += 1

        return frame

    def generate_frames(
        self,
        count: int,
        *,
        is_speech: bool | None = None,
    ) -> Iterator[AudioFrame]:
        """Generate a sequence of frames.

        Args:
            count: Number of frames to generate.
            is_speech: If given, overrides the speech/silence state for this
                       batch only; the original state is restored afterwards.

        Yields:
            AudioFrame instances in ascending seq order.
        """
        if is_speech is not None:
            original = self._is_speech
            self._is_speech = is_speech
            try:
                for _ in range(count):
                    yield self.generate_frame()
            finally:
                self._is_speech = original
        else:
            for _ in range(count):
                yield self.generate_frame()
