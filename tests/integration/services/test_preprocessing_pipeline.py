"""Integration tests for the Audio Preprocessing Pipeline (Sprint-006).

Validates end-to-end flow:
  Media Gateway -> Audio Session Manager -> Audio Preprocessing Pipeline

Required integration test (Sprint-006.md):
  test_full_pipeline_on_audio_fixture  -- run audio through full pipeline,
                                          output is 16kHz, no clipping

Additional integration tests cover:
  - Multi-frame continuous processing (clock continuity)
  - PLC frames from ASM passing correctly into preprocessing
  - Pipeline consistency across sequential frames

Architecture: V1 Ch3 (MG), V1 Ch4 (ASM), V1 Ch5 (Preprocessing);
              DocSuite-02 (end-to-end interface contracts).
"""

from __future__ import annotations

import math
import struct

import numpy as np

from src.libs.contracts.audio import AudioConfig, AudioFrame, Encoding, SampleRate
from src.services.audio_preprocessing.quality import compute_rms_dbfs
from src.services.audio_preprocessing.service import AudioPreprocessorService
from src.services.audio_session_manager.service import AudioSessionManagerService
from tests.fixtures.audio import FakeRTPStream, make_wav_bytes

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_CONFIG_8K = AudioConfig(
    sample_rate=SampleRate.RATE_8K,
    encoding=Encoding.PCM16LE,
    channels=1,
    frame_duration_ms=20,
)


def _pcm16le_from_wav(wav_bytes: bytes) -> bytes:
    """Extract PCM data from a WAV file and convert 8-bit samples to PCM16LE.

    The make_wav_bytes fixture produces 8-bit unsigned PCM. This function
    converts to signed 16-bit little-endian PCM by scaling [0,255] -> [-32768,32767].

    Args:
        wav_bytes: Valid WAV file bytes (8-bit mono PCM).

    Returns:
        PCM16LE bytes.
    """
    # WAV header: RIFF(12) + fmt(24) + data header(8) = 44 bytes
    wav_header_size = 44
    raw_8bit = wav_bytes[wav_header_size:]
    # Convert 8-bit unsigned [0,255] to 16-bit signed [-32768, 32767]
    samples_8 = np.frombuffer(raw_8bit, dtype=np.uint8).astype(np.float64)
    samples_16 = (samples_8 - 128.0) * 256.0
    return samples_16.astype("<i2").tobytes()


def _pcm_max_abs(pcm: bytes) -> int:
    """Return the maximum absolute int16 sample value in ``pcm``."""
    n = len(pcm) // 2
    if n == 0:
        return 0
    samples = struct.unpack_from(f"<{n}h", pcm)
    return int(max(abs(s) for s in samples))


# ===========================================================================
# Required integration test
# ===========================================================================


class TestFullPipelineOnAudioFixture:
    """Sprint-006 required integration test: full pipeline on an audio fixture."""

    def test_full_pipeline_on_audio_fixture(self) -> None:
        """Required: run test WAV through the full pipeline.

        Verifies:
          - Output sample rate is 16 kHz.
          - No clipping: abs(max_sample) < 32768.
          - Output has twice the byte length of the input (2x upsample).
        """
        # Generate a test WAV with 160 samples of 440 Hz sine
        wav = make_wav_bytes(sample_rate=8000, num_samples=160, silence=False)
        pcm16 = _pcm16le_from_wav(wav)

        frame = AudioFrame(
            pcm_data=pcm16,
            seq=0,
            rtp_ts=0,
            recv_ts=0.0,
            config=_CONFIG_8K,
        )

        svc = AudioPreprocessorService()
        svc.start()
        try:
            result = svc.process_frame("integration-wav", frame)
        finally:
            svc.stop()

        # AC-1: output is 16 kHz
        assert result.config.sample_rate == SampleRate.RATE_16K

        # No clipping: abs(max) < 32768 (i.e., no sample equals -32768)
        max_abs = _pcm_max_abs(result.pcm_data)
        assert max_abs < 32768, f"Clipping detected: max sample {max_abs} == 32768"

        # Output has double the samples
        assert len(result.pcm_data) == len(frame.pcm_data) * 2

    def test_full_pipeline_silence_fixture(self) -> None:
        """Silent WAV through full pipeline produces no clipping and 16kHz output."""
        wav = make_wav_bytes(sample_rate=8000, num_samples=160, silence=True)
        pcm16 = _pcm16le_from_wav(wav)

        frame = AudioFrame(pcm_data=pcm16, seq=0, rtp_ts=0, recv_ts=0.0, config=_CONFIG_8K)
        svc = AudioPreprocessorService()
        result = svc.process_frame("integration-silence", frame)

        assert result.config.sample_rate == SampleRate.RATE_16K
        assert _pcm_max_abs(result.pcm_data) < 32768


# ===========================================================================
# MG -> ASM -> Preprocessing integration tests
# ===========================================================================


class TestMediaGatewayToPreprocessingPipeline:
    """End-to-end: frames flow from ASM session into the preprocessing pipeline."""

    def test_asm_output_frames_processed_by_pipeline(self) -> None:
        """Frames emitted by AudioSession.push_frame() pass correctly into the pipeline."""
        asm_svc = AudioSessionManagerService()
        asm_svc.start()
        pp_svc = AudioPreprocessorService()
        pp_svc.start()

        call_id = "integration-asm-pp-001"
        session = asm_svc.create_session(call_id, "tenant-001")

        # Build a PCM16LE frame (8 kHz, 20ms)
        pcm = bytes([0x10, 0x00] * 160)  # value=16 per sample
        frame = AudioFrame(pcm_data=pcm, seq=0, rtp_ts=0, recv_ts=0.0, config=_CONFIG_8K)

        output_frames = session.push_frame(frame)
        assert output_frames, "ASM should emit at least one frame"

        for asm_frame in output_frames:
            result = pp_svc.process_frame(call_id, asm_frame)
            assert result.config.sample_rate == SampleRate.RATE_16K

        asm_svc.release_session(call_id)
        pp_svc.stop()
        asm_svc.stop()

    def test_plc_frames_from_asm_pass_through_pipeline(self) -> None:
        """PLC frames (is_plc=True) from ASM are processed by the pipeline unchanged."""
        asm_svc = AudioSessionManagerService()
        asm_svc.start()
        pp_svc = AudioPreprocessorService()
        pp_svc.start()

        call_id = "integration-plc-001"
        session = asm_svc.create_session(call_id, "tenant-001")

        # First frame (seq=0): activates session
        pcm = bytes([0x20, 0x00] * 160)
        f0 = AudioFrame(pcm_data=pcm, seq=0, rtp_ts=0, recv_ts=0.0, config=_CONFIG_8K)
        session.push_frame(f0)

        # Gap: seq jumps from 0 to 3 -> ASM synthesises 2 PLC frames for seq=1,2
        f3 = AudioFrame(pcm_data=pcm, seq=3, rtp_ts=3 * 160, recv_ts=0.06, config=_CONFIG_8K)
        plc_and_real = session.push_frame(f3)

        plc_frames = [fr for fr in plc_and_real if fr.is_plc]
        assert plc_frames, "ASM should have synthesised PLC frames for the gap"

        # Pipeline accepts PLC frames (is_plc is preserved)
        for pf in plc_frames:
            result = pp_svc.process_frame(call_id, pf)
            assert result.config.sample_rate == SampleRate.RATE_16K
            assert result.is_plc is True  # PLC flag is propagated

        asm_svc.release_session(call_id)
        pp_svc.stop()
        asm_svc.stop()

    def test_multiple_consecutive_frames_maintain_16k_output(self) -> None:
        """5 consecutive frames all produce valid 16 kHz output."""
        pp_svc = AudioPreprocessorService()
        pp_svc.start()

        stream = FakeRTPStream(is_speech=True)
        # Convert mulaw frames to PCM16LE for preprocessing (simplified: treat as 8-bit)
        for i in range(5):
            rtp_frame = stream.generate_frame()
            # Map 8-bit mulaw samples to PCM16LE (scale 0..255 -> -32768..32767)
            raw = np.frombuffer(rtp_frame.pcm_data, dtype=np.uint8).astype(np.float64)
            pcm16 = ((raw - 128.0) * 256.0).astype("<i2").tobytes()
            frame = AudioFrame(
                pcm_data=pcm16,
                seq=rtp_frame.seq,
                rtp_ts=rtp_frame.rtp_ts,
                recv_ts=rtp_frame.recv_ts,
                config=_CONFIG_8K,
            )
            result = pp_svc.process_frame(f"stream-{i}", frame)
            assert result.config.sample_rate == SampleRate.RATE_16K
            assert len(result.pcm_data) == len(frame.pcm_data) * 2

        pp_svc.stop()

    def test_pipeline_agc_warms_up_across_frames(self) -> None:
        """AGC gain converges across consecutive frames (gain state persists)."""
        # NS disabled: stationary sine would be suppressed as noise, masking the AGC test
        pp_svc = AudioPreprocessorService(enabled_stages={"agc", "resample"})

        # Very quiet input: -50 dBFS
        amp = 32768.0 * 10.0 ** (-50.0 / 20.0) * math.sqrt(2.0)
        n = 160
        t = np.arange(n, dtype=np.float64) / 8000.0
        pcm_float = amp * np.sin(2.0 * np.pi * 440.0 * t)
        pcm = np.clip(pcm_float, -32768.0, 32767.0).astype("<i2").tobytes()

        input_dbfs = compute_rms_dbfs(pcm)
        assert input_dbfs < -40.0, f"Test setup failed: input {input_dbfs:.1f} dBFS not < -40 dBFS"

        # Process 20 frames to allow AGC gain to increase
        last_result: AudioFrame | None = None
        for i in range(20):
            frame = AudioFrame(pcm_data=pcm, seq=i, rtp_ts=i * n, recv_ts=0.0, config=_CONFIG_8K)
            last_result = pp_svc.process_frame(f"agc-warmup-{i}", frame)

        assert last_result is not None
        # Decode 16kHz output back to 8kHz equivalent by taking every other sample
        output_pcm = last_result.pcm_data
        output_dbfs = compute_rms_dbfs(output_pcm)

        # After 20 frames of AGC warmup, output should be notably louder than input
        assert output_dbfs > input_dbfs + 5.0, (
            f"AGC did not amplify across frames: in {input_dbfs:.1f} dBFS, out {output_dbfs:.1f} dBFS"
        )

    def test_preprocessing_pipeline_start_stop_lifecycle(self) -> None:
        """Service lifecycle: start -> process -> stop runs cleanly."""
        svc = AudioPreprocessorService()
        assert not svc.is_running

        svc.start()
        assert svc.is_running

        frame = AudioFrame(pcm_data=bytes(320), seq=0, rtp_ts=0, recv_ts=0.0, config=_CONFIG_8K)
        result = svc.process_frame("lifecycle-test", frame)
        assert result.config.sample_rate == SampleRate.RATE_16K

        svc.stop()
        assert not svc.is_running
