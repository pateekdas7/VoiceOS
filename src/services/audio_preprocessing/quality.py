"""AudioQualityMetrics — SNR estimation and ERLE measurement.

Provides utility functions and a stateful metrics tracker for measuring audio
preprocessing quality per V1 Ch5:

  - Signal-to-Noise Ratio (SNR): ratio of signal power to noise power (dB).
  - Echo Return Loss Enhancement (ERLE): ratio of echo power before and after
    AEC cancellation (dB).

These metrics are used internally by AudioPreprocessorService to populate the
Prometheus gauges in metrics.py and to validate acceptance criteria in tests.

Architecture: V1 Ch5 (audio quality metrics — SNR, ERLE);
              DocSuite-02 (preprocessing quality targets).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

_EPSILON: float = 1e-10


def compute_rms(pcm: bytes) -> float:
    """Compute the RMS amplitude of PCM16LE audio in the range [0.0, 1.0].

    Args:
        pcm: Raw PCM16LE bytes.

    Returns:
        RMS in [0.0, 1.0] relative to full scale.  Returns 0.0 for empty input.
    """
    if not pcm or len(pcm) < 2:
        return 0.0
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    mean_sq: float = float(np.mean(samples**2))
    return math.sqrt(mean_sq) / 32768.0


def compute_rms_dbfs(pcm: bytes) -> float:
    """Compute the RMS level of PCM16LE audio in dBFS.

    Returns:
        dBFS value.  Returns -120.0 dBFS for silence/empty input.
    """
    rms = compute_rms(pcm)
    if rms < _EPSILON:
        return -120.0
    return 20.0 * math.log10(rms)


def compute_snr_db(signal_pcm: bytes, noise_pcm: bytes) -> float:
    """Estimate SNR (dB) from separate signal and noise PCM16LE buffers.

    Args:
        signal_pcm: Clean or reference signal PCM16LE bytes.
        noise_pcm:  Noise-only PCM16LE bytes of the same length.

    Returns:
        SNR in dB.  Returns 0.0 if either buffer is silent.
    """
    sig_rms = compute_rms(signal_pcm)
    noise_rms = compute_rms(noise_pcm)
    if sig_rms < _EPSILON or noise_rms < _EPSILON:
        return 0.0
    return 20.0 * math.log10(sig_rms / noise_rms)


def compute_erle_db(echo_before_pcm: bytes, echo_after_pcm: bytes) -> float:
    """Compute Echo Return Loss Enhancement (ERLE) in dB.

    ERLE = 10 * log10(E[d^2] / E[e^2])

    where d is the near-end signal before AEC and e is the residual error
    after AEC.  Higher ERLE = more echo suppression.

    Args:
        echo_before_pcm: Near-end signal before AEC (PCM16LE).
        echo_after_pcm:  Near-end signal after AEC (PCM16LE, residual).

    Returns:
        ERLE in dB.  Returns 0.0 if either buffer is silent.
    """
    before_samples = np.frombuffer(echo_before_pcm, dtype="<i2").astype(np.float64)
    after_samples = np.frombuffer(echo_after_pcm, dtype="<i2").astype(np.float64)

    power_before: float = float(np.mean(before_samples**2))
    power_after: float = float(np.mean(after_samples**2))

    if power_before < _EPSILON:
        return 0.0
    if power_after < _EPSILON:
        # Perfect (or near-perfect) cancellation — residual below numerical floor
        return 60.0

    return 10.0 * math.log10(power_before / power_after)


@dataclass
class AudioQualityMetrics:
    """Stateful accumulator for per-session audio quality measurements.

    Tracks a running average of SNR and ERLE across frames using exponential
    moving average (EMA) smoothing.

    Architecture: V1 Ch5 (audio quality metrics — per-call averages).
    """

    _ema_alpha: float = field(default=0.1, repr=False)
    _avg_snr_db: float = field(default=0.0, init=False, repr=False)
    _avg_erle_db: float = field(default=0.0, init=False, repr=False)
    _frame_count: int = field(default=0, init=False, repr=False)

    def update_snr(self, snr_db: float) -> None:
        """Update the running SNR estimate with a new measurement.

        Args:
            snr_db: Instantaneous SNR in dB.
        """
        self._frame_count += 1
        if self._frame_count == 1:
            self._avg_snr_db = snr_db
        else:
            self._avg_snr_db = (1.0 - self._ema_alpha) * self._avg_snr_db + self._ema_alpha * snr_db

    def update_erle(self, erle_db: float) -> None:
        """Update the running ERLE estimate with a new measurement.

        Args:
            erle_db: Instantaneous ERLE in dB.
        """
        if self._frame_count == 0:
            self._avg_erle_db = erle_db
        else:
            self._avg_erle_db = (1.0 - self._ema_alpha) * self._avg_erle_db + self._ema_alpha * erle_db

    @property
    def avg_snr_db(self) -> float:
        """Exponential moving average SNR in dB."""
        return self._avg_snr_db

    @property
    def avg_erle_db(self) -> float:
        """Exponential moving average ERLE in dB."""
        return self._avg_erle_db

    @property
    def frame_count(self) -> int:
        """Total number of SNR observations recorded."""
        return self._frame_count
