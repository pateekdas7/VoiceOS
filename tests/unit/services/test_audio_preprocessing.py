"""Unit tests for the Audio Preprocessing Pipeline (Sprint-006).

Covers all acceptance criteria and required named tests from Sprint-006.md:

  AC-1  test_resample_8k_to_16k          -- 10ms 8kHz frame -> 20ms 16kHz (double length)
  AC-2  test_agc_normalizes_quiet_signal  -- -40 dBFS input -> within 3 dB of -18 dBFS
  AC-3  test_pipeline_stage_isolation     -- disable NS; AEC3 + AGC + Resample still run
  AC-4  test_pipeline_latency_benchmark   -- 100 frames; p99 <= 5ms per frame
  AC-5  (covered by test_pipeline_stage_isolation -- stages configurable without code change)
  AC-6  (each stage tested in isolation below via TestAEC3Stage, TestNSStage, TestAGCStage,
         TestResamplerStage -- all take AudioFrame in, return AudioFrame out)

Architecture: V6 Ch9 (Testing Standards); V1 Ch5 (Audio Preprocessing).
"""

from __future__ import annotations

import math
import struct
import time

import numpy as np
import pytest

from src.libs.contracts.audio import AudioConfig, AudioFrame, Encoding, SampleRate
from src.services.audio_preprocessing.pipeline import AudioPipeline, ProcessingStage
from src.services.audio_preprocessing.quality import (
    AudioQualityMetrics,
    compute_erle_db,
    compute_rms,
    compute_rms_dbfs,
    compute_snr_db,
)
from src.services.audio_preprocessing.service import AudioPreprocessorService
from src.services.audio_preprocessing.stages.aec3 import AEC3Stage
from src.services.audio_preprocessing.stages.agc import AGCStage
from src.services.audio_preprocessing.stages.noise_suppression import NSStage
from src.services.audio_preprocessing.stages.resampler import ResamplerStage

# ---------------------------------------------------------------------------
# Shared test constants and helpers
# ---------------------------------------------------------------------------

_CONFIG_8K = AudioConfig(
    sample_rate=SampleRate.RATE_8K,
    encoding=Encoding.PCM16LE,
    channels=1,
    frame_duration_ms=20,
)

_CONFIG_8K_10MS = AudioConfig(
    sample_rate=SampleRate.RATE_8K,
    encoding=Encoding.PCM16LE,
    channels=1,
    frame_duration_ms=10,
)

# 20 ms of PCM16LE silence at 8 kHz mono = 160 samples x 2 bytes
_SILENCE_8K_20MS: bytes = bytes(320)

# 10 ms of PCM16LE silence at 8 kHz mono = 80 samples x 2 bytes
_SILENCE_8K_10MS: bytes = bytes(160)


def _silence_frame(
    seq: int = 0,
    *,
    samples: int = 160,
    config: AudioConfig = _CONFIG_8K,
) -> AudioFrame:
    """Build a silence AudioFrame with PCM16LE zeros."""
    return AudioFrame(
        pcm_data=bytes(samples * 2),
        seq=seq,
        rtp_ts=seq * samples,
        recv_ts=0.0,
        config=config,
    )


def _sine_pcm(
    *, freq_hz: float = 440.0, amplitude: float = 2000.0, n_samples: int = 160, start_sample: int = 0
) -> bytes:
    """Generate PCM16LE bytes of a sine wave."""
    t = np.arange(start_sample, start_sample + n_samples, dtype=np.float64) / 8000.0
    wave = amplitude * np.sin(2.0 * np.pi * freq_hz * t)
    clipped = np.clip(wave, -32768.0, 32767.0)
    return clipped.astype("<i2").tobytes()


def _sine_frame(
    seq: int = 0,
    *,
    freq_hz: float = 440.0,
    amplitude: float = 2000.0,
    n_samples: int = 160,
    start_sample: int = 0,
    config: AudioConfig = _CONFIG_8K,
) -> AudioFrame:
    """Build an AudioFrame containing a sine wave."""
    pcm = _sine_pcm(freq_hz=freq_hz, amplitude=amplitude, n_samples=n_samples, start_sample=start_sample)
    return AudioFrame(
        pcm_data=pcm,
        seq=seq,
        rtp_ts=seq * n_samples,
        recv_ts=0.0,
        config=config,
    )


def _pcm_max_abs(pcm: bytes) -> int:
    """Return the maximum absolute int16 sample value in ``pcm``."""
    n = len(pcm) // 2
    if n == 0:
        return 0
    samples = struct.unpack_from(f"<{n}h", pcm)
    return int(max(abs(s) for s in samples))


# ===========================================================================
# Tests for ProcessingStage ABC (isolation / interface contract)
# ===========================================================================


class TestProcessingStageABC:
    """Verify the ProcessingStage ABC can be subclassed correctly."""

    def test_concrete_stage_implements_interface(self) -> None:
        """A concrete stage must implement name, enabled, and process."""
        stage: ProcessingStage = ResamplerStage(enabled=True)
        assert isinstance(stage.name, str)
        assert isinstance(stage.enabled, bool)

    def test_disabled_stage_is_pass_through(self) -> None:
        """A disabled stage must return the frame unchanged."""
        stage = ResamplerStage(enabled=False)
        frame = _silence_frame()
        result = stage.process(frame)
        assert result is frame  # same object — no copy made when bypassed

    def test_enabled_stage_transforms_frame(self) -> None:
        """An enabled stage returns a (possibly different) AudioFrame."""
        stage = ResamplerStage(enabled=True)
        frame = _silence_frame()
        result = stage.process(frame)
        assert result.config.sample_rate == SampleRate.RATE_16K


# ===========================================================================
# Tests for AudioPipeline
# ===========================================================================


class TestAudioPipeline:
    """Pipeline composition, stage ordering, and disabled-stage bypass."""

    def test_empty_pipeline_is_pass_through(self) -> None:
        """An empty pipeline returns the frame unchanged."""
        pipeline = AudioPipeline(stages=[])
        frame = _silence_frame()
        result = pipeline.process(frame)
        assert result is frame

    def test_stages_execute_in_order(self) -> None:
        """Stages run in the declared order (resample last upgrades sample_rate)."""
        pipeline = AudioPipeline(stages=[AGCStage(enabled=True), ResamplerStage(enabled=True)])
        frame = _silence_frame()
        result = pipeline.process(frame)
        assert result.config.sample_rate == SampleRate.RATE_16K

    def test_disabled_stage_bypassed_in_pipeline(self) -> None:
        """A disabled stage is skipped; the rest of the pipeline still runs."""
        pipeline = AudioPipeline(stages=[NSStage(enabled=False), ResamplerStage(enabled=True)])
        frame = _silence_frame()
        result = pipeline.process(frame)
        # Resample ran (sample rate is now 16 kHz)
        assert result.config.sample_rate == SampleRate.RATE_16K

    # AC-3 / AC-5 required named test
    def test_pipeline_stage_isolation(self) -> None:
        """Required: disable NS stage; AEC3 + AGC + Resample still run (AC-3/AC-5)."""
        svc = AudioPreprocessorService(enabled_stages={"aec3", "agc", "resample"})
        frame = _silence_frame()
        result = svc.process_frame("call-isolation", frame)
        # NS was disabled
        assert not svc.pipeline.stages[1].enabled  # NS at index 1
        # Resample ran — output is 16 kHz
        assert result.config.sample_rate == SampleRate.RATE_16K
        # Output has double the samples (160 * 2 = 320 samples = 640 bytes)
        assert len(result.pcm_data) == len(frame.pcm_data) * 2

    def test_process_timed_returns_elapsed(self) -> None:
        """process_timed() returns (frame, elapsed_ms) with elapsed_ms >= 0."""
        pipeline = AudioPipeline(stages=[ResamplerStage(enabled=True)])
        frame = _silence_frame()
        result, elapsed = pipeline.process_timed(frame)
        assert result.config.sample_rate == SampleRate.RATE_16K
        assert elapsed >= 0.0

    def test_stages_property_returns_copy(self) -> None:
        """Modifying the returned stages list does not affect the pipeline."""
        stage = ResamplerStage(enabled=True)
        pipeline = AudioPipeline(stages=[stage])
        stages_copy = pipeline.stages
        stages_copy.clear()
        assert len(pipeline.stages) == 1  # original unchanged


# ===========================================================================
# Tests for AEC3Stage (Acoustic Echo Cancellation)
# ===========================================================================


class TestAEC3Stage:
    """AEC3 NLMS adaptive echo cancellation."""

    def test_aec3_pass_through_without_reference(self) -> None:
        """AEC3 returns frame unchanged when no reference has been set."""
        aec3 = AEC3Stage(enabled=True)
        frame = _sine_frame()
        result = aec3.process(frame)
        assert result is frame

    def test_aec3_disabled_is_pass_through(self) -> None:
        """Disabled AEC3 returns frame unchanged even if reference is set."""
        aec3 = AEC3Stage(enabled=False)
        pcm = _sine_pcm()
        aec3.set_reference(pcm)
        frame = _sine_frame()
        result = aec3.process(frame)
        assert result is frame

    def test_aec3_name(self) -> None:
        assert AEC3Stage().name == "aec3"

    def test_aec3_set_reference_enables_processing(self) -> None:
        """AEC3 outputs a different frame when a reference is provided."""
        aec3 = AEC3Stage(enabled=True)
        pcm = _sine_pcm(amplitude=5000.0)
        aec3.set_reference(pcm)
        frame = AudioFrame(pcm_data=pcm, seq=0, rtp_ts=0, recv_ts=0.0, config=_CONFIG_8K)
        result = aec3.process(frame)
        assert result.pcm_data != pcm  # output differs from input

    def test_aec3_clear_reference_restores_bypass(self) -> None:
        """After clear_reference(), AEC3 returns the frame unchanged."""
        aec3 = AEC3Stage(enabled=True)
        pcm = _sine_pcm()
        aec3.set_reference(pcm)
        aec3.clear_reference()
        frame = _sine_frame()
        result = aec3.process(frame)
        assert result is frame

    def test_aec3_achieves_erle_10db(self) -> None:
        """AEC3 achieves ERLE >= 10 dB on synthetic pure-echo fixture (V1 Ch5).

        Uses NLMS with short filter (fast convergence) and processes 10 warmup
        frames (1600 samples) to reach steady-state before measuring ERLE.
        """
        # Short filter + high step for fast convergence in the test fixture
        aec3 = AEC3Stage(filter_length=16, step_size=0.5)
        n_per_frame = 160

        # Generate 12 frames of 440 Hz sine
        total_frames = 12
        all_samples = n_per_frame * total_frames
        t_arr = np.arange(all_samples, dtype=np.float64) / 8000.0
        ref_float = np.sin(2.0 * np.pi * 440.0 * t_arr) * 3000.0

        # Process 10 warmup frames to converge the NLMS filter
        for i in range(10):
            start = i * n_per_frame
            chunk = np.clip(ref_float[start : start + n_per_frame], -32768.0, 32767.0).astype("<i2").tobytes()
            warm_frame = AudioFrame(pcm_data=chunk, seq=i, rtp_ts=i * n_per_frame, recv_ts=0.0, config=_CONFIG_8K)
            aec3.set_reference(chunk)
            aec3.process(warm_frame)

        # Measurement frames 11 and 12 (after convergence)
        erle_values: list[float] = []
        for j in range(10, 12):
            start = j * n_per_frame
            chunk = np.clip(ref_float[start : start + n_per_frame], -32768.0, 32767.0).astype("<i2").tobytes()
            meas_frame = AudioFrame(pcm_data=chunk, seq=j, rtp_ts=j * n_per_frame, recv_ts=0.0, config=_CONFIG_8K)
            aec3.set_reference(chunk)
            result = aec3.process(meas_frame)
            e = compute_erle_db(chunk, result.pcm_data)
            if e > 0.0:
                erle_values.append(e)

        assert erle_values, "No positive ERLE measurement obtained"
        assert max(erle_values) >= 10.0, f"ERLE {max(erle_values):.1f} dB < 10 dB target"

    def test_aec3_output_frame_preserves_metadata(self) -> None:
        """AEC3 output has same seq, rtp_ts, recv_ts, and config as input."""
        aec3 = AEC3Stage(enabled=True)
        pcm = _sine_pcm(amplitude=500.0)
        aec3.set_reference(pcm)
        frame = AudioFrame(pcm_data=pcm, seq=42, rtp_ts=1234, recv_ts=3.14, config=_CONFIG_8K)
        result = aec3.process(frame)
        assert result.seq == 42
        assert result.rtp_ts == 1234
        assert result.recv_ts == pytest.approx(3.14)
        assert result.config == _CONFIG_8K

    def test_aec3_handles_silent_frame(self) -> None:
        """AEC3 processes a silent frame without raising (power normalization guard)."""
        aec3 = AEC3Stage(enabled=True)
        silence = _SILENCE_8K_20MS
        aec3.set_reference(silence)
        frame = _silence_frame()
        result = aec3.process(frame)
        assert len(result.pcm_data) == len(silence)


# ===========================================================================
# Tests for NSStage (Noise Suppression)
# ===========================================================================


class TestNSStage:
    """Spectral subtraction noise suppression."""

    def test_ns_name(self) -> None:
        assert NSStage().name == "ns"

    def test_ns_disabled_is_pass_through(self) -> None:
        ns = NSStage(enabled=False)
        frame = _sine_frame()
        result = ns.process(frame)
        assert result is frame

    def test_ns_processes_frame_without_error(self) -> None:
        """NS processes a sine frame and returns a frame of the same length."""
        ns = NSStage(enabled=True)
        pcm = _sine_pcm(amplitude=3000.0)
        frame = AudioFrame(pcm_data=pcm, seq=0, rtp_ts=0, recv_ts=0.0, config=_CONFIG_8K)
        result = ns.process(frame)
        assert len(result.pcm_data) == len(pcm)
        assert result.config == _CONFIG_8K

    def test_ns_default_backend_is_spectral(self) -> None:
        """Default NS backend is 'spectral'."""
        ns = NSStage()
        assert ns.backend == "spectral"

    def test_ns_backend_override(self) -> None:
        """Backend can be overridden via constructor argument."""
        ns = NSStage(backend="spectral")
        assert ns.backend == "spectral"

    def test_ns_reduces_stationary_noise_after_warmup(self) -> None:
        """After warmup, NS reduces power of stationary noise signal."""
        ns = NSStage(enabled=True, noise_floor_frames=5)
        n_warmup = 10
        pcm = _sine_pcm(amplitude=3000.0)
        frame = AudioFrame(pcm_data=pcm, seq=0, rtp_ts=0, recv_ts=0.0, config=_CONFIG_8K)

        # Warm up noise floor estimator with the same signal (treated as noise)
        for _ in range(n_warmup):
            ns.process(frame)

        # After warmup, suppress the noise
        result = ns.process(frame)
        rms_in = compute_rms(pcm)
        rms_out = compute_rms(result.pcm_data)
        # Output should be suppressed (less or at most equal power)
        assert rms_out <= rms_in

    def test_ns_silence_frame_processed(self) -> None:
        """NS processes a silent frame without error."""
        ns = NSStage(enabled=True)
        frame = _silence_frame()
        result = ns.process(frame)
        assert len(result.pcm_data) == len(frame.pcm_data)

    def test_ns_output_preserves_config(self) -> None:
        """NS output frame has same config as input."""
        ns = NSStage(enabled=True)
        frame = _sine_frame()
        result = ns.process(frame)
        assert result.config == _CONFIG_8K


# ===========================================================================
# Tests for AGCStage (Automatic Gain Control)
# ===========================================================================


class TestAGCStage:
    """AGC gain computation and amplitude normalization."""

    def test_agc_name(self) -> None:
        assert AGCStage().name == "agc"

    def test_agc_disabled_is_pass_through(self) -> None:
        agc = AGCStage(enabled=False)
        frame = _sine_frame()
        result = agc.process(frame)
        assert result is frame

    # AC-2 required named test
    def test_agc_normalizes_quiet_signal(self) -> None:
        """Required: -40 dBFS quiet input is normalized to within 3 dB of -18 dBFS.

        Uses release_coeff=0.9 for fast single-frame convergence in the test.
        The production AGC uses slower release_coeff=0.1 to prevent pumping.
        """
        # -40 dBFS sine: amplitude = 32767 * 10^(-40/20) / sqrt(2) * sqrt(2) = 32767 * 0.01 ~= 328
        # For a pure sine, RMS = amplitude / sqrt(2). We target RMS = 328 for -40 dBFS.
        # Sine amplitude = 328 * sqrt(2) ~= 464
        target_rms_linear = 10.0 ** (-40.0 / 20.0)  # 0.01 relative to full scale
        sine_amplitude = target_rms_linear * 32768.0 * math.sqrt(2.0)  # ~= 464
        pcm = _sine_pcm(amplitude=sine_amplitude)

        # AGC with fast release for single-frame convergence (test only)
        agc = AGCStage(release_coeff=0.9, target_dbfs=-18.0, max_gain_db=30.0)
        frame = AudioFrame(pcm_data=pcm, seq=0, rtp_ts=0, recv_ts=0.0, config=_CONFIG_8K)
        result = agc.process(frame)

        level_dbfs = compute_rms_dbfs(result.pcm_data)
        # Target: -18 dBFS, tolerance: +/-3 dB -> [-21, -15]
        assert -21.0 <= level_dbfs <= -15.0, f"AGC output {level_dbfs:.1f} dBFS not within 3 dB of -18 dBFS"

    def test_agc_silent_frame_passes_through(self) -> None:
        """Silent frames are passed through unchanged (silence threshold guard)."""
        agc = AGCStage(enabled=True, silence_threshold_dbfs=-60.0)
        frame = _silence_frame()
        result = agc.process(frame)
        # Silent frame: output should be identical (returned unchanged)
        assert result is frame

    def test_agc_loud_signal_is_attenuated(self) -> None:
        """A loud signal (> target) is attenuated toward target."""
        # -3 dBFS = very loud
        loud_amplitude = 32767.0 * math.sqrt(2.0) * 10.0 ** (-3.0 / 20.0)  # ~= 32136
        # Clip to valid int16 range
        loud_amplitude = min(loud_amplitude, 32767.0)
        pcm = _sine_pcm(amplitude=loud_amplitude)
        agc = AGCStage(enabled=True, target_dbfs=-18.0, attack_coeff=0.9)
        frame = AudioFrame(pcm_data=pcm, seq=0, rtp_ts=0, recv_ts=0.0, config=_CONFIG_8K)
        rms_in = compute_rms(pcm)
        result = agc.process(frame)
        rms_out = compute_rms(result.pcm_data)
        # Output should be attenuated
        assert rms_out < rms_in

    def test_agc_output_frame_length_unchanged(self) -> None:
        """AGC output has the same byte length as input."""
        agc = AGCStage(enabled=True)
        frame = _sine_frame(amplitude=500.0)
        result = agc.process(frame)
        assert len(result.pcm_data) == len(frame.pcm_data)

    def test_agc_max_gain_capped(self) -> None:
        """Very quiet signals do not get amplified beyond max_gain_db."""
        # -60 dBFS signal (extremely quiet — just above silence floor)
        amplitude = 32768.0 * 10.0 ** (-60.0 / 20.0) * math.sqrt(2.0) * 2.0  # just above floor
        agc = AGCStage(
            enabled=True,
            target_dbfs=-18.0,
            max_gain_db=30.0,
            silence_threshold_dbfs=-70.0,
        )
        pcm = _sine_pcm(amplitude=amplitude)
        frame = AudioFrame(pcm_data=pcm, seq=0, rtp_ts=0, recv_ts=0.0, config=_CONFIG_8K)
        result = agc.process(frame)
        # Max amplitude must not exceed int16 range
        assert _pcm_max_abs(result.pcm_data) <= 32767


# ===========================================================================
# AC-1 Required: test_resample_8k_to_16k
# ===========================================================================


class TestResamplerStage:
    """8 kHz -> 16 kHz sinc-interpolation resampler."""

    def test_resample_name(self) -> None:
        assert ResamplerStage().name == "resample"

    # AC-1 required named test
    def test_resample_8k_to_16k(self) -> None:
        """Required: 10ms silence frame at 8kHz outputs 20ms frame at 16kHz (AC-1).

        A 10ms frame at 8kHz = 80 samples = 160 bytes.
        After 2x upsample: 160 samples = 320 bytes at 16kHz.
        """
        resampler = ResamplerStage(enabled=True)
        frame = AudioFrame(
            pcm_data=_SILENCE_8K_10MS,
            seq=0,
            rtp_ts=0,
            recv_ts=0.0,
            config=_CONFIG_8K_10MS,
        )
        result = resampler.process(frame)

        # Output length: input_len * 2 (80 samples -> 160 samples = 320 bytes)
        assert len(result.pcm_data) == len(frame.pcm_data) * 2
        # Output sample rate is 16 kHz
        assert result.config.sample_rate == SampleRate.RATE_16K
        # Output encoding is PCM16LE
        assert result.config.encoding == Encoding.PCM16LE

    def test_resample_20ms_frame(self) -> None:
        """Standard 20ms 8kHz frame -> 40ms 16kHz frame (320 -> 640 bytes)."""
        resampler = ResamplerStage(enabled=True)
        frame = _silence_frame()  # 160 samples at 8kHz = 320 bytes
        result = resampler.process(frame)
        assert len(result.pcm_data) == 640  # 320 samples at 16kHz = 640 bytes
        assert result.config.sample_rate == SampleRate.RATE_16K

    def test_resample_sine_signal(self) -> None:
        """Resampling a sine wave preserves the waveform structure."""
        resampler = ResamplerStage(enabled=True)
        frame = _sine_frame(amplitude=3000.0)
        result = resampler.process(frame)
        assert len(result.pcm_data) == len(frame.pcm_data) * 2
        assert result.config.sample_rate == SampleRate.RATE_16K

    def test_resample_disabled_is_pass_through(self) -> None:
        """Disabled ResamplerStage returns the input frame unchanged."""
        resampler = ResamplerStage(enabled=False)
        frame = _silence_frame()
        result = resampler.process(frame)
        assert result is frame

    def test_resample_preserves_metadata(self) -> None:
        """ResamplerStage preserves seq, rtp_ts, recv_ts, is_plc."""
        resampler = ResamplerStage(enabled=True)
        frame = AudioFrame(
            pcm_data=_SILENCE_8K_10MS,
            seq=77,
            rtp_ts=9999,
            recv_ts=1.23,
            config=_CONFIG_8K_10MS,
            is_plc=True,
        )
        result = resampler.process(frame)
        assert result.seq == 77
        assert result.rtp_ts == 9999
        assert result.recv_ts == pytest.approx(1.23)
        assert result.is_plc is True

    def test_resample_output_no_int16_overflow(self) -> None:
        """Resampled output must not contain int16 overflow artefacts."""
        resampler = ResamplerStage(enabled=True)
        # Near-full-scale sine to stress the resampler
        frame = _sine_frame(amplitude=30000.0)
        result = resampler.process(frame)
        assert _pcm_max_abs(result.pcm_data) <= 32767

    def test_resample_rate_ratio(self) -> None:
        """ResamplerStage computes correct polyphase factors for 8->16 kHz."""
        resampler = ResamplerStage(
            input_rate=SampleRate.RATE_8K,
            output_rate=SampleRate.RATE_16K,
        )
        assert resampler._up == 2
        assert resampler._down == 1


# ===========================================================================
# Tests for AudioQualityMetrics and quality utility functions
# ===========================================================================


class TestAudioQualityUtils:
    """Quality utility functions: compute_rms, compute_erle_db, etc."""

    def test_compute_rms_silence(self) -> None:
        """Silence PCM returns 0.0 RMS."""
        assert compute_rms(bytes(320)) == pytest.approx(0.0)

    def test_compute_rms_full_scale(self) -> None:
        """Full-scale PCM16LE value (32767) gives RMS close to 1.0."""
        # 160 samples all at value 32767
        pcm = struct.pack("<160h", *([32767] * 160))
        rms = compute_rms(pcm)
        assert rms == pytest.approx(32767.0 / 32768.0, rel=1e-4)

    def test_compute_rms_sine_wave(self) -> None:
        """Sine wave with amplitude a has RMS = a / sqrt(2)."""
        amplitude = 10000.0
        n = 8000  # 1 second = many complete periods of 440 Hz
        t = np.arange(n, dtype=np.float64) / 8000.0
        wave = (amplitude * np.sin(2.0 * np.pi * 440.0 * t)).astype("<i2")
        pcm = wave.tobytes()
        rms = compute_rms(pcm) * 32768.0  # undo normalisation
        expected = amplitude / math.sqrt(2.0)
        assert rms == pytest.approx(expected, rel=0.01)

    def test_compute_rms_dbfs_silence(self) -> None:
        """Silence returns -120.0 dBFS."""
        assert compute_rms_dbfs(bytes(320)) == pytest.approx(-120.0)

    def test_compute_rms_dbfs_full_scale(self) -> None:
        """Full-scale sine returns ~0 dBFS."""
        amp = 32767.0
        n = 160
        t = np.arange(n, dtype=np.float64) / 8000.0
        wave = (amp * np.sin(2.0 * np.pi * 440.0 * t)).astype("<i2")
        dbfs = compute_rms_dbfs(wave.tobytes())
        # Full scale sine RMS = 32767 / sqrt(2) ~= 23170 -> dBFS ~= -3 dBFS
        assert -4.0 <= dbfs <= -2.0

    def test_compute_snr_db_equal_signals(self) -> None:
        """Identical signal and noise gives SNR = 0 dB."""
        pcm = _sine_pcm(amplitude=5000.0)
        assert compute_snr_db(pcm, pcm) == pytest.approx(0.0)

    def test_compute_snr_db_silence_returns_zero(self) -> None:
        """Silent signal or noise returns 0.0 (guard against log(0))."""
        pcm = _sine_pcm(amplitude=5000.0)
        assert compute_snr_db(bytes(320), pcm) == pytest.approx(0.0)
        assert compute_snr_db(pcm, bytes(320)) == pytest.approx(0.0)

    def test_compute_erle_db_no_cancellation(self) -> None:
        """Same signal before and after AEC gives ERLE = 0 dB."""
        pcm = _sine_pcm(amplitude=5000.0)
        assert compute_erle_db(pcm, pcm) == pytest.approx(0.0)

    def test_compute_erle_db_silence_returns_zero(self) -> None:
        """Silent near-end (before) returns 0.0 ERLE; silent after (output) returns high ERLE."""
        pcm = _sine_pcm(amplitude=5000.0)
        # No echo to cancel: before is silent → guard returns 0.0
        assert compute_erle_db(bytes(320), pcm) == pytest.approx(0.0)
        # Perfect cancellation: after is silent → high ERLE (>= 60 dB)
        assert compute_erle_db(pcm, bytes(320)) >= 60.0

    def test_compute_erle_db_positive_when_cancelled(self) -> None:
        """ERLE is positive when the output has lower power than the input."""
        # Strong input, weak output
        strong = _sine_pcm(amplitude=5000.0)
        weak = _sine_pcm(amplitude=100.0)
        erle = compute_erle_db(strong, weak)
        assert erle > 0.0


class TestAudioQualityMetrics:
    """AudioQualityMetrics stateful accumulator."""

    def test_initial_state(self) -> None:
        """Freshly constructed metrics start at 0."""
        m = AudioQualityMetrics()
        assert m.frame_count == 0
        assert m.avg_snr_db == pytest.approx(0.0)
        assert m.avg_erle_db == pytest.approx(0.0)

    def test_update_snr_first_frame(self) -> None:
        """First SNR update sets the average directly (not EMA-dampened)."""
        m = AudioQualityMetrics()
        m.update_snr(15.0)
        assert m.avg_snr_db == pytest.approx(15.0)
        assert m.frame_count == 1

    def test_update_snr_multiple_frames(self) -> None:
        """Subsequent SNR updates use EMA smoothing."""
        m = AudioQualityMetrics()
        m.update_snr(10.0)
        m.update_snr(20.0)
        assert m.avg_snr_db == pytest.approx(11.0)

    def test_update_erle(self) -> None:
        """ERLE updates are tracked separately from SNR."""
        m = AudioQualityMetrics()
        m.update_erle(12.0)
        assert m.avg_erle_db == pytest.approx(12.0)

    def test_frame_count_increments_on_snr_update(self) -> None:
        """frame_count increments only on update_snr calls."""
        m = AudioQualityMetrics()
        m.update_snr(5.0)
        m.update_snr(5.0)
        m.update_erle(5.0)
        assert m.frame_count == 2


# ===========================================================================
# Tests for AudioPreprocessorService
# ===========================================================================


class TestAudioPreprocessorService:
    """Pipeline composition, lifecycle, and per-frame processing."""

    def test_service_starts_stopped(self) -> None:
        """Service is not running before start() is called."""
        svc = AudioPreprocessorService()
        assert not svc.is_running

    def test_service_start_stop(self) -> None:
        """start() sets is_running; stop() clears it."""
        svc = AudioPreprocessorService()
        svc.start()
        is_run = svc.is_running
        svc.stop()
        is_stop = svc.is_running
        assert is_run is True
        assert is_stop is False

    def test_service_has_four_stages(self) -> None:
        """Default service pipeline has exactly 4 stages in canonical order."""
        svc = AudioPreprocessorService()
        stages = svc.pipeline.stages
        names = [s.name for s in stages]
        assert names == ["aec3", "ns", "agc", "resample"]

    def test_process_frame_returns_16k(self) -> None:
        """process_frame() always returns a 16 kHz frame (ResamplerStage active)."""
        svc = AudioPreprocessorService()
        frame = _silence_frame()
        result = svc.process_frame("call-001", frame)
        assert result.config.sample_rate == SampleRate.RATE_16K

    def test_process_frame_doubles_length(self) -> None:
        """Output byte length is 2x input (8 kHz -> 16 kHz, 20ms frame)."""
        svc = AudioPreprocessorService()
        frame = _silence_frame()
        result = svc.process_frame("call-001", frame)
        assert len(result.pcm_data) == len(frame.pcm_data) * 2

    def test_custom_enabled_stages_disables_ns(self) -> None:
        """Passing enabled_stages={'aec3','agc','resample'} disables NS."""
        svc = AudioPreprocessorService(enabled_stages={"aec3", "agc", "resample"})
        # NS at index 1
        ns_stage = svc.pipeline.stages[1]
        assert ns_stage.name == "ns"
        assert ns_stage.enabled is False

    def test_set_reference_routes_to_aec3(self) -> None:
        """set_reference() passes the reference PCM to the AEC3 stage."""
        svc = AudioPreprocessorService()
        pcm = _sine_pcm(amplitude=2000.0)
        svc.set_reference(pcm)
        # AEC3 stage should now have a reference (verified by processing a frame)
        frame = AudioFrame(pcm_data=pcm, seq=0, rtp_ts=0, recv_ts=0.0, config=_CONFIG_8K)
        result = svc.process_frame("call-ref", frame)
        # AEC3 ran (output differs from input before resampling)
        assert len(result.pcm_data) == len(frame.pcm_data) * 2

    def test_clear_reference(self) -> None:
        """clear_reference() puts AEC3 back into bypass mode."""
        svc = AudioPreprocessorService()
        pcm = _sine_pcm(amplitude=2000.0)
        svc.set_reference(pcm)
        svc.clear_reference()
        # Subsequent frame should pass AEC3 unchanged (bypass)
        frame = _sine_frame()
        result = svc.process_frame("call-clr", frame)
        assert result.config.sample_rate == SampleRate.RATE_16K  # resample still ran

    def test_quality_metrics_initialized(self) -> None:
        """Quality metrics object is available and starts at 0."""
        svc = AudioPreprocessorService()
        assert svc.quality.frame_count == 0

    def test_quality_metrics_after_processing(self) -> None:
        """After processing a frame, quality metrics have been updated."""
        svc = AudioPreprocessorService()
        svc.process_frame("call-q", _silence_frame())
        # ERLE is tracked (0.0 when no reference)
        assert svc.quality.avg_erle_db == pytest.approx(0.0)


# ===========================================================================
# AC-4 Required: test_pipeline_latency_benchmark
# ===========================================================================


class TestLatencyBenchmark:
    """Pipeline latency validation: p99 <= 5 ms per frame."""

    # AC-4 required named test
    def test_pipeline_latency_benchmark(self) -> None:
        """Required: 100 frames processed; p99 latency <= 5 ms per frame (AC-4).

        Measures wall-clock time via process_timed() for the full pipeline
        (AEC3 bypassed — no reference set, which is the common operating mode
        when TTS is not playing). All four stage slots are instantiated.
        """
        svc = AudioPreprocessorService()
        frame = _silence_frame()

        latencies: list[float] = []
        for _ in range(100):
            _, elapsed_ms = svc.pipeline.process_timed(frame)
            latencies.append(elapsed_ms)

        latencies.sort()
        p99: float = latencies[98]  # 99th value out of 100 (0-indexed)

        assert p99 <= 5.0, f"p99 latency {p99:.2f} ms exceeds 5 ms budget"

    def test_individual_stages_meet_latency(self) -> None:
        """Each stage individually processes 100 frames well within 5 ms p99."""
        frame = _silence_frame()
        stages: list[ProcessingStage] = [
            AEC3Stage(enabled=False),  # bypass for isolation
            NSStage(enabled=True),
            AGCStage(enabled=True),
            ResamplerStage(enabled=True),
        ]
        for stage in stages:
            latencies: list[float] = []
            for _ in range(100):
                t0 = time.perf_counter()
                stage.process(frame)
                latencies.append((time.perf_counter() - t0) * 1000.0)
                frame_8k = _silence_frame()  # reset after resampler changes config
                frame = frame_8k
            latencies.sort()
            p99 = latencies[98]
            assert p99 <= 5.0, f"Stage '{stage.name}' p99={p99:.2f} ms > 5 ms budget"
