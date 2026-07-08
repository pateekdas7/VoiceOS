"""AGCStage — Automatic Gain Control targeting -18 dBFS RMS.

Implements a two-stage AGC:
  1. Gain computation: measures frame RMS and derives the linear gain needed
     to bring the signal to ``target_dbfs`` (default -18 dBFS), bounded by
     ``max_gain_db`` (default 30 dB) and ``min_gain_db`` (default -40 dB).
  2. Soft-knee compression: applies a gain smoothed by an attack/release
     envelope to prevent abrupt level jumps between consecutive frames.

The AGC passes silent frames (RMS below ``silence_threshold_dbfs``) unchanged
to avoid amplifying background noise in pauses.

Architecture: V1 Ch5 §5.3 (AGCStage — target -18 dBFS, max gain 30 dB);
              DocSuite-02 (AudioPreprocessor interface contract).
"""

from __future__ import annotations

import math

import numpy as np

from src.libs.contracts.audio import AudioFrame
from src.services.audio_preprocessing.pipeline import ProcessingStage

# Default AGC parameters (per V1 Ch5 §5.3)
_TARGET_DBFS: float = -18.0
_MAX_GAIN_DB: float = 30.0
_MIN_GAIN_DB: float = -40.0
_SILENCE_THRESHOLD_DBFS: float = -60.0

# Attack/release smoothing factors (per-frame)
# Attack = fast (react quickly to loud transients to prevent clipping)
# Release = slow (return to target gradually to avoid pumping)
_ATTACK_COEFF: float = 0.9
_RELEASE_COEFF: float = 0.1

_INT16_MIN: float = -32768.0
_INT16_MAX: float = 32767.0

_EPSILON: float = 1e-10


class AGCStage(ProcessingStage):
    """Automatic Gain Control with soft-knee compression.

    Operates on PCM16LE frames at any sample rate (no resampling is performed).
    A smoothed gain is maintained across frames to prevent inter-frame level
    discontinuities.

    Architecture: V1 Ch5 §5.3.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        target_dbfs: float = _TARGET_DBFS,
        max_gain_db: float = _MAX_GAIN_DB,
        min_gain_db: float = _MIN_GAIN_DB,
        silence_threshold_dbfs: float = _SILENCE_THRESHOLD_DBFS,
        attack_coeff: float = _ATTACK_COEFF,
        release_coeff: float = _RELEASE_COEFF,
    ) -> None:
        """Initialise the AGC stage.

        Args:
            enabled:                Whether the stage is active.
            target_dbfs:            RMS target level in dBFS (default -18 dBFS).
            max_gain_db:            Maximum gain applied (default +30 dB).
            min_gain_db:            Minimum gain applied (default -40 dB).
            silence_threshold_dbfs: Frames below this RMS level are passed
                                    through unchanged (no boost).
            attack_coeff:           Exponential smoothing coefficient for gain
                                    increase (attack).  Closer to 1 = faster.
            release_coeff:          Exponential smoothing coefficient for gain
                                    decrease (release).  Closer to 1 = faster.
        """
        self._enabled = enabled
        self._target_linear = 10.0 ** (target_dbfs / 20.0)
        self._max_gain = 10.0 ** (max_gain_db / 20.0)
        self._min_gain = 10.0 ** (min_gain_db / 20.0)
        self._silence_floor = 10.0 ** (silence_threshold_dbfs / 20.0)
        self._attack = attack_coeff
        self._release = release_coeff

        # Smoothed gain state (starts at unity)
        self._smoothed_gain: float = 1.0

    # ------------------------------------------------------------------
    # ProcessingStage interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "agc"

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------

    def process(self, frame: AudioFrame) -> AudioFrame:
        """Apply automatic gain control to ``frame``.

        Args:
            frame: Input audio frame (PCM16LE, any sample rate).

        Returns:
            Gain-normalized frame with the same config as input.
        """
        if not self._enabled:
            return frame

        samples = np.frombuffer(frame.pcm_data, dtype="<i2").astype(np.float64)

        rms = self._rms(samples)
        if rms < self._silence_floor:
            # Silent frame: pass through without amplification
            return frame

        # Desired gain to reach target level
        desired_gain = self._target_linear / (rms + _EPSILON)
        desired_gain = max(self._min_gain, min(self._max_gain, desired_gain))

        # Smooth the gain using attack/release envelope
        if desired_gain < self._smoothed_gain:
            # Gain reduction (attack — fast)
            self._smoothed_gain = self._attack * desired_gain + (1.0 - self._attack) * self._smoothed_gain
        else:
            # Gain increase (release — slow)
            self._smoothed_gain = self._release * desired_gain + (1.0 - self._release) * self._smoothed_gain

        # Apply gain with hard clipping at int16 range
        amplified = samples * self._smoothed_gain
        clipped = np.clip(amplified, _INT16_MIN, _INT16_MAX)
        out_pcm = clipped.astype("<i2").tobytes()

        return frame.model_copy(update={"pcm_data": out_pcm})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rms(samples: np.ndarray) -> float:
        """Compute RMS amplitude of a float64 sample array."""
        if len(samples) == 0:
            return 0.0
        mean_sq: float = float(np.mean(samples**2))
        return math.sqrt(mean_sq) / 32768.0  # normalise to [0, 1]
