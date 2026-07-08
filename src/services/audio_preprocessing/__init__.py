"""Audio Preprocessing Pipeline — cleans 8 kHz telephony audio for VAD/STT.

Receives raw PCM16LE audio frames from the Audio Session Manager and applies
an ordered pipeline of DSP stages to produce clean 16 kHz audio suitable for
Silero VAD and Whisper STT.

Pipeline order (per V1 Ch5 §5.4):
    AEC3  -> removes far-end echo (reference signal required)
    NS    -> suppresses background noise via spectral subtraction
    AGC   -> normalises loudness to -18 dBFS
    Resample -> 8 kHz -> 16 kHz sinc interpolation

Public API:
    AudioPreprocessorService  — pipeline composition + lifecycle
    AudioPipeline             — ordered stage runner
    ProcessingStage           — ABC for custom stage implementations
    AEC3Stage                 — NLMS adaptive echo cancellation
    NSStage                   — spectral subtraction noise suppression
    AGCStage                  — RMS-based automatic gain control
    ResamplerStage            — 8 kHz -> 16 kHz polyphase sinc resampler
    AudioQualityMetrics       — SNR / ERLE accumulator
    compute_rms               — PCM16LE RMS utility
    compute_erle_db           — ERLE measurement utility

Architecture: V1 Ch5 (Audio Preprocessing);
              DocSuite-02 (AudioPreprocessor ↔ VAD interface contract).
"""

from __future__ import annotations

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

__all__ = [
    "AEC3Stage",
    "AGCStage",
    "AudioPipeline",
    "AudioPreprocessorService",
    "AudioQualityMetrics",
    "NSStage",
    "ProcessingStage",
    "ResamplerStage",
    "compute_erle_db",
    "compute_rms",
    "compute_rms_dbfs",
    "compute_snr_db",
]
