"""AudioPipeline — ordered stage runner for the audio preprocessing pipeline.

Composes an ordered sequence of ProcessingStage objects and runs each enabled
stage in order on every AudioFrame.  Stages are configurable: any stage can be
disabled at construction time without modifying code (AC-5 / AC-6).

Architecture: V1 Ch5 (Audio Preprocessing — pipeline composition);
              DocSuite-02 (AudioPreprocessor interface contract).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from src.libs.contracts.audio import AudioFrame


class ProcessingStage(ABC):
    """Abstract base class for a single pipeline processing stage.

    Each stage takes one AudioFrame and returns one (possibly transformed)
    AudioFrame.  Stages that are disabled must pass the frame through unchanged.

    Architecture: V1 Ch5 (stage interface contract; AC-6 — stage isolation).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable stage identifier (e.g. 'aec3', 'ns', 'agc', 'resample')."""

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """If False the stage is bypassed: ``process`` returns the frame unchanged."""

    @abstractmethod
    def process(self, frame: AudioFrame) -> AudioFrame:
        """Apply this stage's transformation to ``frame``.

        Args:
            frame: Input audio frame.

        Returns:
            Transformed frame (or the same frame unchanged when disabled).
        """


class AudioPipeline:
    """Runs an ordered sequence of ProcessingStage objects on each AudioFrame.

    Pipeline order: AEC3 -> NS -> AGC -> Resample  (V1 Ch5 §5.4).
    All DSP stages (AEC3, NS, AGC) operate at 8 kHz; ResamplerStage converts
    to 16 kHz as the final step.

    Usage::

        pipeline = AudioPipeline(stages=[aec3, ns, agc, resampler])
        output = pipeline.process(frame)

    Architecture: V1 Ch5 (pipeline composition and stage ordering).
    """

    def __init__(self, stages: list[ProcessingStage]) -> None:
        """Construct the pipeline from an ordered list of stages.

        Args:
            stages: Ordered list of ProcessingStage objects.  Empty list is
                    valid (pass-through pipeline).
        """
        self._stages = list(stages)

    @property
    def stages(self) -> list[ProcessingStage]:
        """Ordered list of stages (read-only copy)."""
        return list(self._stages)

    def process(self, frame: AudioFrame) -> AudioFrame:
        """Run all enabled stages in sequence on ``frame``.

        Args:
            frame: Input audio frame from the Audio Session Manager.

        Returns:
            Fully preprocessed AudioFrame (16 kHz PCM16LE after ResamplerStage).
        """
        current = frame
        for stage in self._stages:
            if stage.enabled:
                current = stage.process(current)
        return current

    def process_timed(self, frame: AudioFrame) -> tuple[AudioFrame, float]:
        """Run all enabled stages and return the output frame with elapsed ms.

        Returns:
            Tuple of (output_frame, elapsed_ms).
        """
        t0 = time.perf_counter()
        result = self.process(frame)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return result, elapsed_ms
