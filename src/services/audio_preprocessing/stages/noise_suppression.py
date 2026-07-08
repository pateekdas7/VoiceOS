"""NSStage — Noise Suppression via spectral subtraction with adaptive noise floor.

Implements single-channel noise suppression using the classic spectral
subtraction algorithm with adaptive noise floor estimation (Martin 1994 / Boll
1979).  The noise floor is estimated from the minimum statistics of the power
spectral density over a sliding window of frames.

Backend selection via ``VOICEOS_NS_BACKEND`` environment variable:
    - ``"spectral"`` (default): spectral subtraction (this implementation).
    - Future: ``"rnnoise"`` or ``"webrtc"`` via native bindings (Sprint-028).

Architecture: V1 Ch5 §5.3 (NS integration and configurable backend);
              DocSuite-02 (AudioPreprocessor interface contract).
"""

from __future__ import annotations

import os
from collections import deque

import numpy as np

from src.libs.contracts.audio import AudioFrame
from src.services.audio_preprocessing.pipeline import ProcessingStage

# Supported backend identifiers
_BACKEND_SPECTRAL = "spectral"
_VALID_BACKENDS = frozenset({_BACKEND_SPECTRAL})

# Spectral subtraction parameters
_FFT_SIZE = 256
_NOISE_FLOOR_FRAMES = 20
_OVERSUBTRACTION_FACTOR = 1.0
_SPECTRAL_FLOOR = 0.01


class NSStage(ProcessingStage):
    """Noise Suppression using spectral subtraction with noise floor tracking.

    The algorithm operates in the frequency domain:

      1. Frame the input signal into an FFT_SIZE-sample segment with zero-padding.
      2. Compute the magnitude spectrum: |X(k)|.
      3. Estimate the noise PSD as the minimum magnitude over the last
         ``NOISE_FLOOR_FRAMES`` frames (per-bin).
      4. Subtract: |S(k)| = max(|X(k)| - alpha * |N(k)|, beta * |X(k)|)
         where alpha is the over-subtraction factor and beta is the spectral
         floor (prevents musical noise artefacts going below the noise level).
      5. Reconstruct via IFFT using the original signal phase.

    Architecture: V1 Ch5 §5.3.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        backend: str | None = None,
        oversubtraction: float = _OVERSUBTRACTION_FACTOR,
        spectral_floor: float = _SPECTRAL_FLOOR,
        noise_floor_frames: int = _NOISE_FLOOR_FRAMES,
    ) -> None:
        """Initialise the NS stage.

        Args:
            enabled:            Whether the stage is active.
            backend:            Backend override.  Reads ``VOICEOS_NS_BACKEND``
                                env var if None.  Defaults to 'spectral'.
            oversubtraction:    Over-subtraction factor (alpha).  Higher values
                                remove more noise but may cause distortion.
            spectral_floor:     Minimum ratio of output to input magnitude
                                (beta).  Prevents total silence in voiced regions.
            noise_floor_frames: Number of consecutive frames used to track the
                                minimum noise PSD estimate.
        """
        self._enabled = enabled
        self._oversubtraction = oversubtraction
        self._spectral_floor = spectral_floor
        self._noise_floor_frames = noise_floor_frames

        raw_backend = backend or os.environ.get("VOICEOS_NS_BACKEND", _BACKEND_SPECTRAL)
        self._backend = raw_backend.lower().strip()

        # Per-bin noise magnitude history for minimum statistics
        half = _FFT_SIZE // 2 + 1
        self._noise_history: deque[np.ndarray] = deque(maxlen=noise_floor_frames)
        # Running noise floor estimate (magnitude spectrum)
        self._noise_floor: np.ndarray = np.zeros(half, dtype=np.float64)

    # ------------------------------------------------------------------
    # ProcessingStage interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "ns"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def backend(self) -> str:
        """Active backend identifier."""
        return self._backend

    # ------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------

    def process(self, frame: AudioFrame) -> AudioFrame:
        """Apply spectral subtraction noise suppression to ``frame``.

        Args:
            frame: Input audio frame (PCM16LE, any sample rate).

        Returns:
            Noise-suppressed frame with same config as input.
        """
        if not self._enabled:
            return frame

        pcm = frame.pcm_data
        n_samples = len(pcm) // 2
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)

        # Zero-pad to FFT_SIZE if frame is shorter
        padded_len = max(n_samples, _FFT_SIZE)
        work = np.zeros(padded_len, dtype=np.float64)
        work[:n_samples] = samples

        # Frequency domain
        spectrum = np.fft.rfft(work, n=_FFT_SIZE)
        mag = np.abs(spectrum)
        phase = np.angle(spectrum)

        # Update noise floor with current magnitude estimate
        self._noise_history.append(mag.copy())
        if len(self._noise_history) >= self._noise_floor_frames:
            self._noise_floor = np.min(np.stack(list(self._noise_history)), axis=0)
        else:
            # During warm-up, use minimum of available frames
            self._noise_floor = np.min(np.stack(list(self._noise_history)), axis=0)

        # Spectral subtraction
        floor_limit = self._spectral_floor * mag
        suppressed_mag = np.maximum(mag - self._oversubtraction * self._noise_floor, floor_limit)

        # Reconstruct signal from suppressed magnitude + original phase
        suppressed = suppressed_mag * np.exp(1j * phase)
        reconstructed = np.fft.irfft(suppressed, n=_FFT_SIZE)

        # Trim back to original frame length
        out_samples = reconstructed[:n_samples]

        # Clip to int16 range and re-encode
        clipped = np.clip(out_samples, -32768.0, 32767.0)
        out_pcm = clipped.astype("<i2").tobytes()

        return frame.model_copy(update={"pcm_data": out_pcm})
