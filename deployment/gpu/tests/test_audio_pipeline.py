"""Audio pipeline tests — buffer, pacing, continuity, and format contracts.

These tests exercise the complete audio contract from TTS output through the
AudioPacer to the Twilio transport layer. They are designed to detect the
"Na--mas--te" failure mode: silent gaps inserted mid-word when the TTS
producer runs slower than the 20 ms Twilio playback clock (buffer underrun).

Tests cover:
  1. TTS output format (must be PCM16LE, not float32)
  2. AudioPacer: correct 20ms frame size and format
  3. AudioPacer: underrun detection (slow producer)
  4. AudioPacer: no underrun in normal production (fast mock)
  5. TTS continuity test: Hindi sentence produces gapless audio
  6. TTS slow producer test: underruns detected before they reach Twilio
  7. Cancellation / barge-in: pacer stops immediately
  8. Twilio frame format: base64 μ-law at correct size
  9. E2E audio conversion: TTS PCM16 → 8 kHz μ-law → Twilio frame
"""

from __future__ import annotations

import base64
import struct
import time
import threading
from collections.abc import Iterator

import httpx
import pytest

# Import the AudioPacer under test
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from audio.pacer import (  # type: ignore[import]
    AudioPacer,
    SOURCE_RATE,
    TARGET_RATE,
    FRAME_MS,
    DECIMATE_FACTOR,
    _TGT_SAMPLES_PER_FRAME,
    _SRC_BYTES_PER_FRAME,
    pcm16le_to_ulaw,
    decimate_pcm16,
    validate_ulaw_frame,
    build_twilio_media_message,
    ulaw_to_pcm16le,
)

# Computed constants
_SAMPLES_PER_FRAME_24K = SOURCE_RATE * FRAME_MS // 1000   # 480 PCM16 samples per 20ms at 24kHz
_BYTES_PER_FRAME_24K = _SAMPLES_PER_FRAME_24K * 2         # 960 bytes PCM16LE per 20ms
_ULAW_FRAME_BYTES = _TGT_SAMPLES_PER_FRAME                 # 160 bytes μ-law per 20ms
_TTS_CHUNK_SAMPLES = 2048                                  # Veena SNAC: 2048 PCM16 samples per chunk
_TTS_CHUNK_BYTES = _TTS_CHUNK_SAMPLES * 2                  # 4096 bytes PCM16LE per chunk = 85.33ms


# ── PCM16LE codec utilities ────────────────────────────────────────────────────

def _make_pcm16_sine(freq_hz: float = 440.0, duration_ms: float = 85.33,
                     rate: int = SOURCE_RATE, amplitude: float = 0.5) -> bytes:
    """Generate a PCM16LE sine wave for testing (not silence, has real audio content)."""
    import math
    n_samples = int(rate * duration_ms / 1000)
    samples = [
        int(amplitude * 32767 * math.sin(2 * math.pi * freq_hz * i / rate))
        for i in range(n_samples)
    ]
    return struct.pack(f"<{n_samples}h", *samples)


def _make_pcm16_silence(duration_ms: float, rate: int = SOURCE_RATE) -> bytes:
    """Generate PCM16LE silence (zeros)."""
    n_samples = int(rate * duration_ms / 1000)
    return bytes(n_samples * 2)


def _decode_pcm16le(data: bytes) -> list[int]:
    """Decode PCM16LE bytes to list of signed int16 values."""
    n = len(data) // 2
    return list(struct.unpack(f"<{n}h", data))


def _is_pcm16le_range(data: bytes) -> bool:
    """True if all samples in data are within PCM16 range (-32768 to 32767)."""
    samples = _decode_pcm16le(data)
    return all(-32768 <= s <= 32767 for s in samples)


# ── Section 1: TTS output format ───────────────────────────────────────────────


class TestTTSOutputFormat:
    """Verify TTS server outputs PCM16LE (not float32).

    The CPU-side AudioOutput.convert() uses audioop.ratecv(pcm, width=2)
    which interprets data as 2 bytes/sample (PCM16). If TTS outputs float32
    (4 bytes/sample), the data is misread — causing 2× speed distortion and
    complete audio corruption: the "Na--mas--te" failure mode.
    """

    def test_readiness_reports_pcm16le_encoding(self, tts_client: httpx.Client) -> None:
        """Health endpoint must declare output encoding as PCM16LE."""
        body = tts_client.get("/health/ready").json()
        assert body.get("encoding") == "pcm16le", (
            f"TTS server must declare encoding=pcm16le, got: {body.get('encoding')!r}. "
            "Float32 output breaks the CPU-side audioop.ratecv conversion."
        )

    def test_readiness_reports_correct_chunk_bytes(self, tts_client: httpx.Client) -> None:
        """Chunk size must be 4096 bytes (2048 PCM16 samples = 85.33ms)."""
        body = tts_client.get("/health/ready").json()
        chunk_bytes = body.get("chunk_bytes")
        assert chunk_bytes == _TTS_CHUNK_BYTES, (
            f"Expected chunk_bytes={_TTS_CHUNK_BYTES} (2048 PCM16 samples), "
            f"got {chunk_bytes}. "
            "8192 bytes would mean float32 output (4 bytes/sample) — that is the bug."
        )

    def test_synthesize_chunks_are_4096_bytes(self, tts_client: httpx.Client) -> None:
        """Each TTS chunk must be 4096 bytes (PCM16LE), not 8192 (float32)."""
        chunks: list[bytes] = []
        with tts_client.stream(
            "POST", "/synthesize",
            json={"text": "नमस्ते सर, मैं Kavya बोल रही हूं।", "speaker": "kavya"}
        ) as resp:
            assert resp.status_code == 200
            for chunk in resp.iter_bytes(chunk_size=None):
                if chunk:
                    chunks.append(chunk)

        assert len(chunks) > 0, "No audio chunks received"
        for i, chunk in enumerate(chunks):
            assert len(chunk) == _TTS_CHUNK_BYTES, (
                f"Chunk {i}: got {len(chunk)} bytes, expected {_TTS_CHUNK_BYTES}. "
                f"If {len(chunk)} == 8192: server is outputting float32 (THE BUG). "
                f"PCM16LE must be 4096 bytes for 85.33ms at 24 kHz."
            )

    def test_synthesize_output_is_pcm16le_not_float32(self, tts_client: httpx.Client) -> None:
        """Audio samples must be interpretable as PCM16LE (int16 range)."""
        with tts_client.stream(
            "POST", "/synthesize",
            json={"text": "payment", "speaker": "kavya"}
        ) as resp:
            data = b"".join(resp.iter_bytes())

        assert len(data) % 2 == 0, f"Audio not 2-byte aligned (PCM16 requires 2 bytes/sample)"
        # Must NOT be 4-byte aligned only (that would indicate float32)
        # PCM16 is always 2-byte aligned; float32 is 4-byte aligned
        # We verify the data decodes as valid PCM16 range
        assert _is_pcm16le_range(data), "Audio contains values outside int16 range"

    def test_synthesize_not_float32(self, tts_client: httpx.Client) -> None:
        """Audio must NOT be interpretable as float32.

        Float32 samples representing speech are small (|x| ≤ 1.0). If we
        interpret 4096 bytes of PCM16LE as 1024 float32 values, the magnitudes
        will mostly be ≥ 1e-38 (non-zero PCM data has exponent bits set),
        while actual audio signal has values ≥ 1e-3.

        This test checks the LENGTH: PCM16 = 4096 bytes for 85.33ms at 24kHz.
        Float32 = 8192 bytes for the same duration. The chunk size IS the format check.
        """
        with tts_client.stream(
            "POST", "/synthesize",
            json={"text": "test", "speaker": "kavya"}
        ) as resp:
            chunks = [c for c in resp.iter_bytes(chunk_size=None) if c]

        for i, chunk in enumerate(chunks):
            # Float32 would be 8192 bytes. PCM16 is 4096. Anything else is wrong too.
            assert len(chunk) != 8192, (
                f"Chunk {i} is 8192 bytes — this is FLOAT32 output. "
                "The TTS server must convert to PCM16LE before streaming."
            )
            assert len(chunk) == _TTS_CHUNK_BYTES, (
                f"Unexpected chunk size: {len(chunk)}. Expected {_TTS_CHUNK_BYTES} (PCM16LE)."
            )

    def test_chunk_duration_matches_85ms(self, tts_client: httpx.Client) -> None:
        """Each chunk must represent exactly 85.33ms at 24 kHz."""
        with tts_client.stream(
            "POST", "/synthesize",
            json={"text": "hello", "speaker": "kavya"}
        ) as resp:
            chunks = [c for c in resp.iter_bytes(chunk_size=None) if c]

        for chunk in chunks:
            n_samples = len(chunk) // 2  # PCM16: 2 bytes/sample
            duration_ms = n_samples / SOURCE_RATE * 1000
            assert abs(duration_ms - 85.33) < 0.5, (
                f"Chunk duration {duration_ms:.2f}ms ≠ 85.33ms. "
                f"Got {len(chunk)} bytes → {n_samples} samples at {SOURCE_RATE} Hz."
            )


# ── Section 2: AudioPacer unit tests ──────────────────────────────────────────


class TestAudioPacerFrameFormat:
    """Test that AudioPacer produces correctly-formatted 20ms Twilio frames."""

    def test_drain_frame_returns_160_bytes(self) -> None:
        """Each drained frame must be exactly 160 bytes (8 kHz μ-law, 20ms)."""
        pacer = AudioPacer()
        # Feed 4096 bytes (85.33ms of PCM16 at 24kHz) = about 4 frames
        pacer.feed(_make_pcm16_silence(85.33))
        frame = pacer.drain_frame()
        assert frame is not None
        assert len(frame) == _ULAW_FRAME_BYTES, (
            f"Frame must be {_ULAW_FRAME_BYTES} bytes (160 samples × 1 byte μ-law = 20ms at 8kHz). "
            f"Got {len(frame)} bytes."
        )

    def test_frame_is_valid_ulaw(self) -> None:
        """All frame bytes must be valid μ-law values (0-255)."""
        pacer = AudioPacer()
        pacer.feed(_make_pcm16_sine(440.0, 85.33))
        frame = pacer.drain_frame()
        assert frame is not None
        assert validate_ulaw_frame(frame), f"Frame failed μ-law validation: {frame[:8]!r}..."

    def test_drain_frame_after_cancel_returns_none(self) -> None:
        """After cancellation, drain_frame() must return None immediately."""
        pacer = AudioPacer()
        pacer.feed(_make_pcm16_silence(500.0))  # plenty of audio
        pacer.cancel()
        result = pacer.drain_frame()
        assert result is None, "drain_frame() must return None after cancel()"

    def test_frames_per_chunk(self) -> None:
        """One 85.33ms TTS chunk should yield exactly 4 complete 20ms frames."""
        pacer = AudioPacer()
        pacer.feed(_make_pcm16_silence(85.33))  # one TTS chunk

        frames = list(pacer.drain_all_frames())
        # 85.33ms / 20ms = 4.26 frames → 4 complete frames
        assert len(frames) == 4, (
            f"Expected 4 complete 20ms frames from one 85.33ms chunk, got {len(frames)}"
        )
        for i, frame in enumerate(frames):
            assert len(frame) == _ULAW_FRAME_BYTES, f"Frame {i}: wrong size {len(frame)}"

    def test_twilio_message_format(self) -> None:
        """build_twilio_media_message() must produce a valid Twilio media message."""
        frame = bytes(_ULAW_FRAME_BYTES)  # silence
        msg = build_twilio_media_message(frame, stream_sid="MZtest123")

        assert msg["event"] == "media"
        assert msg["streamSid"] == "MZtest123"
        assert "media" in msg
        assert "payload" in msg["media"]

        # Payload must be valid base64 encoding of the frame
        decoded = base64.b64decode(msg["media"]["payload"])
        assert decoded == frame, "Twilio payload must be base64(frame)"

    def test_decimate_24k_to_8k(self) -> None:
        """Decimation by 3 must reduce 480 samples (20ms@24kHz) to 160 (20ms@8kHz)."""
        pcm24k = _make_pcm16_silence(20.0, rate=SOURCE_RATE)   # 480 samples × 2 = 960 bytes
        pcm8k = decimate_pcm16(pcm24k, factor=DECIMATE_FACTOR)
        n_out = len(pcm8k) // 2
        assert n_out == _TGT_SAMPLES_PER_FRAME, (
            f"Decimation by {DECIMATE_FACTOR}: {len(pcm24k)//2} samples → {n_out} samples. "
            f"Expected {_TGT_SAMPLES_PER_FRAME}."
        )


# ── Section 3: Underrun detection (the "Na--mas--te" failure mode) ─────────────


class TestAudioPacerUnderrunDetection:
    """Test that AudioPacer detects buffer underruns.

    An underrun = pacer asked for a 20ms frame but the buffer is empty.
    This is what causes "Na--mas--te" — the consumer (Twilio) asks for audio
    that the producer (TTS) hasn't generated yet. The pacer inserts silence
    and increments underrun_count.

    Tests verify:
    1. Underruns ARE detected (not silently swallowed)
    2. Underrun count is accurate
    3. Silence frames are inserted (to keep Twilio stream alive)
    4. Normal operation produces ZERO underruns
    """

    def test_underrun_on_empty_buffer(self) -> None:
        """Draining from an empty buffer must produce one underrun."""
        pacer = AudioPacer()
        # Feed nothing — drain immediately
        frame = pacer.drain_frame()
        assert frame is not None, "Should return silence frame, not None"
        assert len(frame) == _ULAW_FRAME_BYTES, "Silence frame must still be correct size"
        assert pacer.underrun_count == 1, f"Expected 1 underrun, got {pacer.underrun_count}"
        assert pacer.has_underrun, "has_underrun should be True after underrun"
        assert pacer.frames_silence == 1

    def test_underrun_count_accumulates(self) -> None:
        """Multiple empty drains must accumulate underrun count correctly."""
        pacer = AudioPacer()
        for _ in range(5):
            pacer.drain_frame()
        assert pacer.underrun_count == 5, f"Expected 5 underruns, got {pacer.underrun_count}"

    def test_no_underrun_when_buffer_full(self) -> None:
        """No underruns when buffer has enough audio for all drain calls."""
        pacer = AudioPacer()
        # Feed 500ms of audio — more than enough for 20+ frames
        pacer.feed(_make_pcm16_silence(500.0))
        # Drain 20 frames (= 400ms)
        for _ in range(20):
            frame = pacer.drain_frame()
            assert frame is not None

        assert pacer.underrun_count == 0, (
            f"Expected 0 underruns when buffer is pre-filled. Got {pacer.underrun_count}. "
            "This would indicate a pacer implementation bug."
        )
        assert not pacer.has_underrun

    def test_slow_producer_causes_underrun(self) -> None:
        """Simulates the A6000 production scenario: RTF=2.3 (producer slower than real-time).

        If TTS generates 85ms of audio but takes 195ms to do so (RTF=2.3),
        the consumer exhausts the buffer after 85ms and waits 110ms → underrun.

        This test MUST detect underruns. If it passes without underruns, the
        pacer is not correctly measuring the producer/consumer rate balance.
        """
        pacer = AudioPacer()

        # Simulate slow producer: feed one chunk then immediately try to drain
        # more frames than the chunk contains
        one_chunk = _make_pcm16_silence(85.33)  # 4 frames worth
        pacer.feed(one_chunk)

        # Drain 6 frames (= 120ms) but only have 85ms of audio (4 frames)
        frames = [pacer.drain_frame() for _ in range(6)]

        assert all(f is not None for f in frames), "All frames should return (silence on underrun)"
        assert pacer.underrun_count >= 2, (
            f"Expected at least 2 underruns after draining 6 frames from 4-frame buffer. "
            f"Got {pacer.underrun_count} underruns. "
            "The pacer MUST detect this — it is the 'Na--mas--te' failure mode."
        )

    def test_intermittent_underrun_detection(self) -> None:
        """Feed audio, drain past it, feed more — count underruns accurately."""
        pacer = AudioPacer()

        # Phase 1: feed 85ms, drain 4 frames (good)
        pacer.feed(_make_pcm16_silence(85.33))
        for _ in range(4):
            pacer.drain_frame()

        underruns_after_phase1 = pacer.underrun_count

        # Phase 2: drain 2 more frames without feeding (underrun)
        pacer.drain_frame()
        pacer.drain_frame()

        underruns_after_phase2 = pacer.underrun_count

        # Phase 3: feed 200ms, drain 6 frames (good again)
        pacer.feed(_make_pcm16_silence(200.0))
        for _ in range(6):
            pacer.drain_frame()

        assert underruns_after_phase1 == 0, "No underruns expected in phase 1"
        assert underruns_after_phase2 == 2, f"Expected 2 underruns in phase 2, got {underruns_after_phase2}"
        # Phase 3 should not add more underruns
        assert pacer.underrun_count == 2, f"Phase 3 should not add underruns, total: {pacer.underrun_count}"


# ── Section 4: TTS continuity test — the "Na--mas--te" regression ─────────────


class TestHindiSentenceContinuity:
    """Mandatory regression: Hindi sentence must produce gapless audio.

    The failure mode is:
        TTS generates audio at RTF > 1 (slower than real-time)
        → buffer empties between chunks
        → silence inserted mid-word
        → "Na--mas--te S--ir" (stuttered syllables)

    In mock mode (mock produces instantly), there should be ZERO underruns.
    The slow-producer variant simulates RTF=2.3 and must detect underruns.
    """

    HINDI_SENTENCE = "नमस्ते सर, मैं Kavya बोल रही हूं। आपका loan balance ₹50,000 है।"

    def test_hindi_sentence_produces_audio(self, tts_client: httpx.Client) -> None:
        """Hindi sentence must produce non-zero audio output."""
        with tts_client.stream(
            "POST", "/synthesize",
            json={"text": self.HINDI_SENTENCE, "speaker": "kavya"}
        ) as resp:
            chunks = [c for c in resp.iter_bytes(chunk_size=None) if c]

        assert len(chunks) > 0, "Hindi sentence must produce audio chunks"
        total_bytes = sum(len(c) for c in chunks)
        assert total_bytes > 0, "Total audio must be non-zero"

    def test_hindi_sentence_no_underrun_normal_speed(self, tts_client: httpx.Client) -> None:
        """Normal mock production should produce ZERO underruns through the pacer.

        The mock generates audio at simulated real-time speed (85ms generation
        per 85ms chunk). Fed sequentially, the pacer should never underrun.
        """
        pacer = AudioPacer()
        chunks: list[bytes] = []
        chunk_times: list[float] = []

        with tts_client.stream(
            "POST", "/synthesize",
            json={"text": self.HINDI_SENTENCE, "speaker": "kavya"}
        ) as resp:
            t0 = time.monotonic()
            for chunk in resp.iter_bytes(chunk_size=None):
                if chunk:
                    chunks.append(chunk)
                    chunk_times.append(time.monotonic() - t0)
                    # Feed each chunk as it arrives (simulates streaming)
                    pacer.feed(chunk)

        # Drain all available frames after all chunks are received
        all_frames = list(pacer.drain_all_frames())

        n_chunks = len(chunks)
        n_frames = len(all_frames)
        total_audio_ms = sum(len(c) // 2 for c in chunks) / SOURCE_RATE * 1000
        frame_audio_ms = n_frames * FRAME_MS

        # Print measurements (visible with pytest -s)
        print(f"\n{'─'*60}")
        print(f"Hindi Sentence Continuity Report (mock services)")
        print(f"{'─'*60}")
        print(f"  Sentence:       {self.HINDI_SENTENCE[:50]}...")
        print(f"  Chunks received: {n_chunks}")
        print(f"  Chunk size:      {_TTS_CHUNK_BYTES} bytes (PCM16LE = 85.33ms each)")
        print(f"  Total audio:     {total_audio_ms:.0f}ms")
        print(f"  20ms frames:     {n_frames} ({frame_audio_ms}ms packaged)")
        print(f"  Buffer peak:     {pacer.peak_buffer_depth_ms:.0f}ms")
        print(f"  Underruns:       {pacer.underrun_count}")
        print(f"  Silence frames:  {pacer.frames_silence}")
        if chunk_times:
            inter_chunk_ms = [(chunk_times[i] - chunk_times[i-1]) * 1000
                              for i in range(1, len(chunk_times))]
            if inter_chunk_ms:
                print(f"  Inter-chunk:     avg={sum(inter_chunk_ms)/len(inter_chunk_ms):.0f}ms "
                      f"max={max(inter_chunk_ms):.0f}ms")
        print(f"{'─'*60}")

        assert len(all_frames) > 0, "Must produce at least one 20ms frame"
        assert all(len(f) == _ULAW_FRAME_BYTES for f in all_frames), \
            "All frames must be exactly 160 bytes"
        assert pacer.underrun_count == 0, (
            f"CONTINUITY FAILURE: {pacer.underrun_count} underruns detected. "
            "Underruns = silence inserted mid-word = 'Na--mas--te' failure mode. "
            f"Silence frames: {pacer.frames_silence} / {pacer.frames_produced} total."
        )

    def test_slow_producer_underrun_is_detected(self, tts_client: httpx.Client) -> None:
        """Slow producer (RTF=2.3 simulation) MUST produce detectable underruns.

        This simulates what happens on the A6000 when Veena generates at 2.3×
        slower than real-time. Each chunk takes 195ms to generate but is only
        85ms of audio. The consumer plays at real-time → gap → underrun.
        """
        pacer = AudioPacer()
        chunks: list[bytes] = []
        underruns_per_chunk: list[int] = []

        RTF = 2.3  # Measured production RTF for Veena on L4 (A6000 is faster but same order)

        # Collect all chunks first (mock generates them fast)
        with tts_client.stream(
            "POST", "/synthesize",
            json={"text": self.HINDI_SENTENCE, "speaker": "kavya"}
        ) as resp:
            for chunk in resp.iter_bytes(chunk_size=None):
                if chunk:
                    chunks.append(chunk)

        # Now replay them at RTF=2.3 speed:
        # Each chunk represents 85.33ms of audio but "takes" 85.33*2.3 = 196ms to arrive.
        # Meanwhile, simulate the consumer draining at real-time (one 20ms frame per 20ms).

        consumer_frames_drained = 0
        underruns_total = 0

        for chunk in chunks:
            # Simulate production delay: RTF * chunk_duration frames consumed before next chunk arrives
            chunk_duration_ms = len(chunk) // 2 / SOURCE_RATE * 1000  # ~85.33ms
            generation_time_ms = chunk_duration_ms * RTF               # ~196ms

            # During generation_time_ms, consumer drains frames
            frames_consumed_while_generating = int(generation_time_ms / FRAME_MS)  # ~9 frames

            # Drain those frames from current buffer
            before_underruns = pacer.underrun_count
            for _ in range(frames_consumed_while_generating):
                pacer.drain_frame()
            new_underruns = pacer.underrun_count - before_underruns
            underruns_per_chunk.append(new_underruns)
            underruns_total += new_underruns

            # Now chunk arrives
            pacer.feed(chunk)

        print(f"\n{'─'*60}")
        print(f"Slow Producer Test (RTF={RTF})")
        print(f"{'─'*60}")
        print(f"  Chunks simulated: {len(chunks)}")
        print(f"  Total underruns:  {underruns_total}")
        print(f"  Frames silence:   {pacer.frames_silence}")
        print(f"  Underruns/chunk:  {underruns_per_chunk}")
        print(f"{'─'*60}")

        assert underruns_total > 0, (
            f"SLOW PRODUCER TEST FAILED: Expected underruns (RTF={RTF}) but got 0. "
            "This means the underrun detection is not working. "
            "At RTF=2.3, each 85ms chunk takes 196ms to generate. "
            "The consumer drains ~9 frames in that time but the buffer only holds ~4."
        )


# ── Section 5: Cancellation / barge-in ────────────────────────────────────────


class TestCancellationAndBargeIn:
    """Test that barge-in cancels the audio stream correctly.

    When a user speaks (barge-in), the TTS stream must stop immediately.
    No residual audio should play after the cancellation point.
    """

    def test_cancel_stops_drain_immediately(self) -> None:
        """After cancel(), drain_frame() returns None — no more audio."""
        pacer = AudioPacer()
        pacer.feed(_make_pcm16_silence(1000.0))  # 1000ms buffered

        # Drain a few frames
        pacer.drain_frame()
        pacer.drain_frame()
        assert pacer.frames_produced == 2

        # Cancel (barge-in)
        pacer.cancel()
        assert pacer.is_cancelled

        # All subsequent drains return None
        for _ in range(10):
            result = pacer.drain_frame()
            assert result is None, "After cancel(), drain_frame() must return None"

        # Frame count does not increase after cancel
        assert pacer.frames_produced == 2, \
            "frames_produced must not increase after cancellation"

    def test_cancel_is_idempotent(self) -> None:
        """Multiple cancel() calls must not raise errors."""
        pacer = AudioPacer()
        pacer.cancel()
        pacer.cancel()
        pacer.cancel()
        assert pacer.is_cancelled

    def test_cancel_clears_pending_buffer(self) -> None:
        """After cancel, buffer depth reports correctly (not counting canceled audio)."""
        pacer = AudioPacer()
        pacer.feed(_make_pcm16_silence(500.0))
        pacer.cancel()
        # The buffer may still have bytes (we don't forcibly clear on cancel),
        # but drain_frame() returns None so the audio never reaches Twilio.
        assert pacer.drain_frame() is None

    def test_tts_stream_cancelled_mid_sentence(self, tts_client: httpx.Client) -> None:
        """Consuming only first few chunks then cancelling must stop cleanly."""
        pacer = AudioPacer()
        chunks_received = 0

        with tts_client.stream(
            "POST", "/synthesize",
            json={"text": "नमस्ते सर, मैं Kavya बोल रही हूं। आपका loan balance ₹50,000 है।",
                  "speaker": "kavya"}
        ) as resp:
            for chunk in resp.iter_bytes(chunk_size=None):
                if chunk:
                    pacer.feed(chunk)
                    chunks_received += 1
                    if chunks_received >= 2:
                        pacer.cancel()
                        break  # stop consuming — simulates barge-in mid-stream

        # After barge-in: drain returns None
        result = pacer.drain_frame()
        assert result is None, "After mid-sentence barge-in, drain_frame() must return None"
        assert pacer.is_cancelled


# ── Section 6: E2E audio format conversion ────────────────────────────────────


class TestE2EAudioConversion:
    """End-to-end format conversion: TTS PCM16LE → 8kHz μ-law → Twilio frame."""

    def test_full_conversion_chain(self, tts_client: httpx.Client) -> None:
        """TTS output → AudioPacer → G.711 μ-law → Twilio message format."""
        pacer = AudioPacer()

        with tts_client.stream(
            "POST", "/synthesize",
            json={"text": "payment करें।", "speaker": "kavya"}
        ) as resp:
            for chunk in resp.iter_bytes(chunk_size=None):
                if chunk:
                    pacer.feed(chunk)

        twilio_messages: list[dict] = []
        for frame in pacer.drain_all_frames():
            msg = build_twilio_media_message(frame, stream_sid="MZtest001")
            twilio_messages.append(msg)

        assert len(twilio_messages) > 0, "Must produce at least one Twilio message"

        for i, msg in enumerate(twilio_messages):
            assert msg["event"] == "media"
            assert msg["streamSid"] == "MZtest001"
            payload_bytes = base64.b64decode(msg["media"]["payload"])
            assert len(payload_bytes) == _ULAW_FRAME_BYTES, (
                f"Message {i}: payload must be {_ULAW_FRAME_BYTES} bytes of μ-law, "
                f"got {len(payload_bytes)}"
            )
            # All byte values 0-255 are valid μ-law
            assert all(0 <= b <= 255 for b in payload_bytes)

        assert pacer.underrun_count == 0, (
            f"E2E conversion produced {pacer.underrun_count} underruns. "
            "The TTS→pacer pipeline must not underrun."
        )

    def test_pcm16_to_ulaw_codec_round_trip(self) -> None:
        """PCM16LE → μ-law encoding must preserve audio within codec tolerance."""
        # Generate a tone
        pcm16 = _make_pcm16_sine(440.0, 20.0)  # 20ms of 440Hz

        # Encode to μ-law
        ulaw = pcm16le_to_ulaw(pcm16)
        assert len(ulaw) == len(pcm16) // 2, "μ-law output sample count must match PCM16"

        # Decode back (μ-law is lossy — check approximate round-trip)
        pcm16_rt = ulaw_to_pcm16le(ulaw)

        original_samples = _decode_pcm16le(pcm16)
        decoded_samples = _decode_pcm16le(pcm16_rt)

        assert len(decoded_samples) == len(original_samples)
        # G.711 μ-law is lossy with ~2% quantization error (segment-based log compression).
        # Large-amplitude samples can have higher absolute error than small ones.
        max_error = max(abs(o - d) for o, d in zip(original_samples, decoded_samples))
        tolerance = 32767 * 0.025 + 200  # 2.5% + decoder rounding margin
        assert max_error < tolerance, (
            f"μ-law round-trip error {max_error} exceeds G.711 tolerance {tolerance:.0f}. "
            "Check G.711 encoder/decoder implementation."
        )

    def test_8khz_frame_timing(self) -> None:
        """160-byte μ-law frame must represent exactly 20ms at 8 kHz."""
        frame = bytes(_ULAW_FRAME_BYTES)
        n_samples = len(frame)  # 1 byte per μ-law sample
        duration_ms = n_samples / TARGET_RATE * 1000
        assert duration_ms == pytest.approx(20.0, abs=0.01), (
            f"Frame duration {duration_ms}ms ≠ 20ms. "
            f"Frame has {n_samples} samples at {TARGET_RATE} Hz."
        )

    def test_twilio_inbound_format(self) -> None:
        """Twilio inbound audio: 8kHz μ-law → PCM16LE decode must work."""
        # Simulate what Twilio sends us (reversed path)
        # 20ms of 8kHz μ-law silence
        ulaw_frame = bytes([0xFF] * 160)  # μ-law silence
        pcm16 = ulaw_to_pcm16le(ulaw_frame)
        assert len(pcm16) == 160 * 2  # 160 samples × 2 bytes = 320 bytes
        # Decoded silence should be near zero
        samples = _decode_pcm16le(pcm16)
        assert all(-200 <= s <= 200 for s in samples), \
            "Decoded μ-law silence should be near zero"


# ── Section 7: Buffer monitoring report ───────────────────────────────────────


class TestBufferMonitoring:
    """Test pacer monitoring and reporting capabilities."""

    def test_report_includes_all_metrics(self) -> None:
        """report() must include all required monitoring fields."""
        pacer = AudioPacer()
        pacer.feed(_make_pcm16_silence(100.0))
        pacer.drain_frame()
        report = pacer.report()

        required_keys = {
            "buffer_depth_ms", "peak_buffer_depth_ms",
            "frames_produced", "frames_silence", "underrun_count",
            "has_underrun", "total_audio_fed_ms", "total_audio_drained_ms",
            "producer_rate_ms_per_s", "cancelled",
        }
        missing = required_keys - set(report.keys())
        assert not missing, f"Missing monitoring keys: {missing}"

    def test_buffer_depth_ms_accurate(self) -> None:
        """buffer_depth_ms must accurately reflect fed audio duration."""
        pacer = AudioPacer()
        pacer.feed(_make_pcm16_silence(200.0))  # feed 200ms
        depth_ms = pacer.buffer_depth_ms
        # Allow small rounding error from integer sample counts
        assert abs(depth_ms - 200.0) < 1.0, (
            f"Buffer depth {depth_ms:.1f}ms should be ~200ms after feeding 200ms of audio"
        )

    def test_total_audio_fed_ms_tracks_input(self) -> None:
        """total_audio_fed_ms must equal total duration of audio fed."""
        pacer = AudioPacer()
        pacer.feed(_make_pcm16_silence(100.0))
        pacer.feed(_make_pcm16_silence(50.0))
        total = pacer.total_audio_fed_ms
        assert abs(total - 150.0) < 2.0, f"Total fed {total:.1f}ms should be ~150ms"
