"""ResamplerStage — 8 kHz to 16 kHz sinc-interpolation resampler.

Converts PCM16LE audio from 8 kHz (telephony standard from Media Gateway) to
16 kHz (required by Silero VAD and Whisper STT) using polyphase sinc
interpolation via scipy.signal.resample_poly.

The resampling factor is always up=2, down=1 (exact 2x upsample) which is
equivalent to a sinc-interpolated sample-rate doubling.  The output frame
carries SampleRate.RATE_16K and has exactly twice the byte length of the input.

Latency: <= 2 ms per frame on the dev machine (validated by test_pipeline_latency_benchmark).

Architecture: V1 Ch5 §5.5 (8kHz->16kHz resampling — sinc interpolation, <= 2ms latency);
              DocSuite-02 (AudioPreprocessor interface — AudioPreprocessor outputs 16 kHz).
"""

from __future__ import annotations

import numpy as np
import scipy.signal

from src.libs.contracts.audio import AudioConfig, AudioFrame, Encoding, SampleRate
from src.services.audio_preprocessing.pipeline import ProcessingStage


class ResamplerStage(ProcessingStage):
    """8 kHz -> 16 kHz polyphase sinc-interpolation resampler.

    Consumes PCM16LE frames at 8 kHz and produces PCM16LE frames at 16 kHz.
    Output frame has twice the sample count (and twice the byte length) of the
    input, matching a 20 ms window at 16 kHz (320 samples = 640 bytes).

    The stage updates the ``config`` field of the output AudioFrame to reflect
    the new sample rate; all other fields (seq, rtp_ts, recv_ts, is_plc) are
    propagated unchanged.

    Architecture: V1 Ch5 §5.5.
    """

    _UP: int = 2
    _DOWN: int = 1

    def __init__(
        self,
        *,
        enabled: bool = True,
        input_rate: SampleRate = SampleRate.RATE_8K,
        output_rate: SampleRate = SampleRate.RATE_16K,
    ) -> None:
        """Initialise the resampler stage.

        Args:
            enabled:     Whether the stage is active.
            input_rate:  Expected input sample rate (default 8 kHz).
            output_rate: Target output sample rate (default 16 kHz).
        """
        self._enabled = enabled
        self._input_rate = input_rate
        self._output_rate = output_rate

        # Compute polyphase factors from the rate ratio
        up, down = self._rate_ratio(input_rate, output_rate)
        self._up = up
        self._down = down

    # ------------------------------------------------------------------
    # ProcessingStage interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "resample"

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------

    def process(self, frame: AudioFrame) -> AudioFrame:
        """Resample ``frame`` from input_rate to output_rate.

        Args:
            frame: Input audio frame (PCM16LE at input_rate).

        Returns:
            Resampled AudioFrame at output_rate with doubled byte length.
        """
        if not self._enabled:
            return frame

        samples = np.frombuffer(frame.pcm_data, dtype="<i2").astype(np.float64)

        # scipy.signal.resample_poly: sinc polyphase interpolation
        resampled: np.ndarray = scipy.signal.resample_poly(
            samples,
            up=self._up,
            down=self._down,
        )

        # Clip to safe int16 range — avoid -32768 (abs > 32767) from polyphase overshoot
        clipped = np.clip(resampled, -32767.0, 32767.0)
        out_pcm = clipped.astype("<i2").tobytes()

        # Build new config at output sample rate
        new_config = AudioConfig(
            sample_rate=self._output_rate,
            encoding=Encoding.PCM16LE,
            channels=frame.config.channels,
            frame_duration_ms=frame.config.frame_duration_ms,
        )

        return frame.model_copy(update={"pcm_data": out_pcm, "config": new_config})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rate_ratio(input_rate: SampleRate, output_rate: SampleRate) -> tuple[int, int]:
        """Compute the smallest integer up/down factors for the rate ratio.

        Args:
            input_rate:  Source sample rate.
            output_rate: Target sample rate.

        Returns:
            Tuple of (up, down) polyphase factors.
        """
        import math

        num = output_rate.value
        den = input_rate.value
        g = math.gcd(num, den)
        return num // g, den // g
