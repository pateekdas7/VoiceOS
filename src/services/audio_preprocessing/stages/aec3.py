"""AEC3Stage — Acoustic Echo Cancellation using NLMS adaptive filtering.

Implements echo cancellation via a Normalized Least Mean Squares (NLMS)
adaptive filter.  The NLMS filter models the acoustic echo path between the
far-end reference signal (TTS playback) and the near-end microphone signal,
then subtracts the estimated echo component.

ERLE target: >= 10 dB on the Sprint-006 test fixture (synthetic echo).
The production ERLE target (>= 20 dB) will be validated in Sprint-028 using
the WebRTC AEC3 native binding (webrtc-audio-processing) on Linux GPU nodes.

When no reference signal is set the stage passes the frame through unchanged
(degraded-mode, no far-end audio available).

Architecture: V1 Ch5 (AEC3Stage — echo cancellation, ERLE measurement);
              DocSuite-02 (AudioPreprocessor interface contract).
"""

from __future__ import annotations

import numpy as np

from src.libs.contracts.audio import AudioFrame
from src.services.audio_preprocessing.pipeline import ProcessingStage

# PCM16LE sample constants
_SAMPLE_BYTES = 2
_INT16_MIN = -32768
_INT16_MAX = 32767


def _pcm_to_float(pcm: bytes) -> np.ndarray:
    """Decode PCM16LE bytes to float64 ndarray in [-1.0, 1.0]."""
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    return samples / 32768.0


def _float_to_pcm(arr: np.ndarray) -> bytes:
    """Encode float64 ndarray in [-1.0, 1.0] to PCM16LE bytes with clipping."""
    clipped = np.clip(arr * 32768.0, _INT16_MIN, _INT16_MAX)
    return clipped.astype("<i2").tobytes()


class AEC3Stage(ProcessingStage):
    """Acoustic Echo Cancellation using NLMS adaptive filtering.

    The NLMS algorithm maintains an adaptive FIR filter ``w`` of length
    ``filter_length`` that models the echo path impulse response.  On each
    frame it:

      1. Buffers the far-end (reference) samples into ``x_buf``.
      2. Filters ``x_buf`` with ``w`` to estimate the echo signal.
      3. Subtracts the echo estimate from the near-end signal.
      4. Updates ``w`` via the NLMS weight update rule.

    NLMS update (per sample):
        e[n] = d[n] - w^T * x[n]
        w   += (step_size / (x[n]^T * x[n] + eps)) * e[n] * x[n]

    where ``d[n]`` is the near-end (microphone) sample and ``x[n]`` is the
    reference buffer snapshot at time n.

    The stage is model-agnostic; swapping the filter for WebRTC AEC3 requires
    only replacing the ``process()`` body — the contract stays the same.

    Architecture: V1 Ch5 §5.2 (AEC3 integration and ERLE target).
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        filter_length: int = 256,
        step_size: float = 0.1,
        eps: float = 1e-6,
    ) -> None:
        """Initialise the AEC3 stage.

        Args:
            enabled:       Whether the stage is active.
            filter_length: Number of NLMS adaptive filter taps.  Longer filters
                           model longer echo paths (at higher CPU cost).
            step_size:     NLMS adaptation rate mu in (0, 2).  Higher values
                           converge faster but are less stable.
            eps:           Regularisation constant prevents division by zero
                           when the reference signal is silent.
        """
        self._enabled = enabled
        self._filter_length = filter_length
        self._step_size = step_size
        self._eps = eps

        # Adaptive filter weights (float64 for numerical stability)
        self._w: np.ndarray = np.zeros(filter_length, dtype=np.float64)
        # Circular reference signal buffer
        self._x_buf: np.ndarray = np.zeros(filter_length, dtype=np.float64)
        # Far-end reference PCM (None = no reference available)
        self._reference: bytes | None = None

    # ------------------------------------------------------------------
    # ProcessingStage interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "aec3"

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Reference management
    # ------------------------------------------------------------------

    def set_reference(self, pcm: bytes) -> None:
        """Set the far-end reference signal (TTS playback audio).

        Called by AudioPreprocessorService before processing each frame when
        playback audio is available.  The reference must match the near-end
        frame in sample rate and length.

        Args:
            pcm: Raw PCM16LE bytes of the far-end signal.
        """
        self._reference = pcm

    def clear_reference(self) -> None:
        """Clear the reference signal (degraded mode — no echo cancellation)."""
        self._reference = None

    # ------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------

    def process(self, frame: AudioFrame) -> AudioFrame:
        """Apply NLMS echo cancellation to ``frame``.

        When no reference is set or the stage is disabled, the frame is
        returned unchanged (degraded / bypass mode).

        Args:
            frame: Near-end microphone audio frame.

        Returns:
            Echo-cancelled frame with the same config as the input.
        """
        if not self._enabled or self._reference is None:
            return frame

        near = _pcm_to_float(frame.pcm_data)
        ref = _pcm_to_float(self._reference)

        # Pad / truncate reference to match near-end length
        n = len(near)
        if len(ref) < n:
            ref = np.pad(ref, (0, n - len(ref)))
        elif len(ref) > n:
            ref = ref[:n]

        output = self._nlms_cancel(near, ref)
        cancelled_pcm = _float_to_pcm(output)

        return frame.model_copy(update={"pcm_data": cancelled_pcm})

    # ------------------------------------------------------------------
    # NLMS core
    # ------------------------------------------------------------------

    def _nlms_cancel(
        self,
        near: np.ndarray,
        ref: np.ndarray,
    ) -> np.ndarray:
        """Run NLMS adaptive filtering sample-by-sample.

        Args:
            near: Near-end float64 signal of length n.
            ref:  Far-end reference float64 signal of length n.

        Returns:
            Echo-suppressed float64 signal of length n.
        """
        n = len(near)
        output = np.empty(n, dtype=np.float64)

        for i in range(n):
            # Shift reference sample into circular buffer (implemented as roll)
            self._x_buf = np.roll(self._x_buf, 1)
            self._x_buf[0] = ref[i]

            # Compute echo estimate: y = w^T * x
            y = np.dot(self._w, self._x_buf)

            # Error: near-end minus echo estimate
            e = near[i] - y

            # NLMS weight update
            power = np.dot(self._x_buf, self._x_buf) + self._eps
            self._w += (self._step_size / power) * e * self._x_buf

            output[i] = e

        return output
