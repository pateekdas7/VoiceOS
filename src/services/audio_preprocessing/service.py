"""AudioPreprocessorService — pipeline composition and lifecycle management.

Owns one AudioPipeline per service instance (global, not per-call).  In the
VoiceOS runtime a single preprocessing service is started during boot and all
calls share it.  The service:

  1. Builds the ordered pipeline: AEC3 -> NS -> AGC -> Resample.
  2. Exposes ``process_frame(call_id, frame)`` as the main entry point.
  3. Optionally accepts a far-end reference PCM via ``set_reference(pcm)``
     before calling ``process_frame`` when playback audio is available.
  4. Updates Prometheus metrics after each frame.

Stage configurability (AC-5):
  Pass ``enabled_stages`` to disable one or more stages at construction time
  without touching any stage code.  Any subset of stage names is valid:

    svc = AudioPreprocessorService(enabled_stages={"aec3", "agc", "resample"})
    # NS disabled; the other three stages run normally.

Architecture: V1 Ch5 (AudioPreprocessorService — pipeline composition);
              DocSuite-02 (AudioPreprocessor ↔ VAD interface contract).
"""

from __future__ import annotations

from src.libs.contracts.audio import AudioFrame
from src.services.audio_preprocessing.metrics import (
    increment_frames,
    set_active_pipelines,
    update_pipeline_metrics,
)
from src.services.audio_preprocessing.pipeline import AudioPipeline
from src.services.audio_preprocessing.quality import AudioQualityMetrics, compute_erle_db
from src.services.audio_preprocessing.stages.aec3 import AEC3Stage
from src.services.audio_preprocessing.stages.agc import AGCStage
from src.services.audio_preprocessing.stages.noise_suppression import NSStage
from src.services.audio_preprocessing.stages.resampler import ResamplerStage

# Default stage set
_ALL_STAGES: frozenset[str] = frozenset({"aec3", "ns", "agc", "resample"})

# Global instance counter for the active-pipelines metric
_instance_count: int = 0


class AudioPreprocessorService:
    """Composes the full preprocessing pipeline and exposes a per-frame API.

    Each instance owns one AudioPipeline with four stages in canonical order.
    Stage enablement is fixed at construction time and cannot be changed at
    runtime (immutable pipeline composition per V6 Ch2).

    Usage::

        service = AudioPreprocessorService()
        service.start()

        # Optional: provide far-end reference for AEC
        service.set_reference(far_end_pcm)

        output_frame = service.process_frame("call-123", input_frame)
        service.stop()

    Architecture: V1 Ch5 (AudioPreprocessorService).
    """

    def __init__(
        self,
        *,
        enabled_stages: frozenset[str] | set[str] | None = None,
    ) -> None:
        """Build the preprocessing pipeline.

        Args:
            enabled_stages: Set of stage names to enable.  Names not in the
                            set are disabled (bypass mode).  Defaults to all
                            four stages: {"aec3", "ns", "agc", "resample"}.
        """
        active: frozenset[str] = frozenset(enabled_stages) if enabled_stages is not None else _ALL_STAGES

        self._aec3 = AEC3Stage(enabled="aec3" in active)
        self._ns = NSStage(enabled="ns" in active)
        self._agc = AGCStage(enabled="agc" in active)
        self._resampler = ResamplerStage(enabled="resample" in active)

        self._pipeline = AudioPipeline(stages=[self._aec3, self._ns, self._agc, self._resampler])

        self._quality = AudioQualityMetrics()
        self._running: bool = False

        global _instance_count
        _instance_count += 1
        set_active_pipelines(_instance_count)

    # ------------------------------------------------------------------
    # Service lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Mark the service as running (idempotent)."""
        self._running = True

    def stop(self) -> None:
        """Mark the service as stopped and decrement the active-pipelines gauge."""
        self._running = False
        global _instance_count
        _instance_count = max(0, _instance_count - 1)
        set_active_pipelines(_instance_count)

    @property
    def is_running(self) -> bool:
        """True between ``start()`` and ``stop()``."""
        return self._running

    # ------------------------------------------------------------------
    # Reference signal management (for AEC3)
    # ------------------------------------------------------------------

    def set_reference(self, pcm: bytes) -> None:
        """Provide the far-end (TTS playback) reference to the AEC3 stage.

        Must be called before ``process_frame()`` for each frame where
        playback audio is available.  When not called, AEC3 operates in
        degraded (bypass) mode for that frame.

        Args:
            pcm: PCM16LE bytes of the far-end playback signal.
        """
        self._aec3.set_reference(pcm)

    def clear_reference(self) -> None:
        """Clear the AEC3 reference signal (AEC degraded mode for next frame)."""
        self._aec3.clear_reference()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def pipeline(self) -> AudioPipeline:
        """The underlying AudioPipeline (for testing and metrics)."""
        return self._pipeline

    @property
    def quality(self) -> AudioQualityMetrics:
        """Running quality metrics for this service instance."""
        return self._quality

    # ------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------

    def process_frame(self, call_id: str, frame: AudioFrame) -> AudioFrame:
        """Process one audio frame through the full preprocessing pipeline.

        Pipeline order: AEC3 -> NS -> AGC -> Resample.

        Args:
            call_id: Call identifier for Prometheus label attribution.
            frame:   Input AudioFrame (PCM16LE at 8 kHz from ASM).

        Returns:
            Preprocessed AudioFrame (PCM16LE at 16 kHz, gain-normalised).
        """
        raw_pcm = frame.pcm_data

        output, elapsed_ms = self._pipeline.process_timed(frame)

        # Compute ERLE from input vs. output of AEC stage (when AEC active)
        if self._aec3.enabled and self._aec3._reference is not None:
            erle = compute_erle_db(raw_pcm, output.pcm_data if self._aec3.enabled else raw_pcm)
        else:
            erle = 0.0

        self._quality.update_erle(erle)
        update_pipeline_metrics(call_id, elapsed_ms, erle, self._quality.avg_snr_db)
        increment_frames(call_id)

        return output
