"""VAD Engine — Silero VAD v4 ONNX inference and energy-based fallback.

Implements voice activity detection via two model implementations:

  SileroVADModel  — Production model: Silero VAD v4 ONNX (onnxruntime CPU).
                    Stateful LSTM; processes 512-sample (32 ms at 16 kHz) windows.
  EnergyVADModel  — Energy-based VAD for unit testing and fallback scenarios.
                    No ONNX dependency; deterministic given synthetic audio.

Both implement VADModelProtocol so the VADEngine facade is model-agnostic.

Architecture: V1 Ch6 (VAD — Silero VAD, adaptive pause, barge-in);
              DocSuite-02 (VAD ↔ STT interface contract).
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

# ---------------------------------------------------------------------------
# Protocol — model-agnostic interface
# ---------------------------------------------------------------------------


@runtime_checkable
class VADModelProtocol(Protocol):
    """Protocol satisfied by any VAD model implementation.

    Implementors: SileroVADModel (production ONNX), EnergyVADModel (testing).
    """

    def predict(self, pcm_int16: bytes) -> float:
        """Return speech probability [0.0, 1.0] for one PCM window.

        Args:
            pcm_int16: Signed 16-bit PCM bytes for exactly WINDOW_SIZE_SAMPLES
                       samples (512 samples = 1024 bytes) at 16 kHz mono.

        Returns:
            Speech probability in [0.0, 1.0].
        """
        ...

    def reset(self) -> None:
        """Reset any internal recurrent state.

        Must be called at the start of each new call session so that LSTM
        state from the previous call does not bleed into the next.
        """
        ...


# ---------------------------------------------------------------------------
# SileroVADModel — production ONNX inference
# ---------------------------------------------------------------------------


class SileroVADModel:
    """Silero VAD v4 ONNX model via onnxruntime.

    Wraps the LSTM-based Silero VAD v4 ONNX file and manages the hidden/cell
    state tensors across windows.  Call ``reset()`` at the start of every new
    call to zero out the recurrent state.

    Silero VAD v4 IO schema:
        Inputs : ``input``  float32[1, window_size]
                 ``h``      float32[2, 1, 64]   (LSTM hidden state)
                 ``c``      float32[2, 1, 64]   (LSTM cell state)
                 ``sr``     int64 scalar         (sample rate — must be 16000)
        Outputs: ``output`` float32[1, 1]        (speech probability)
                 ``hn``     float32[2, 1, 64]
                 ``cn``     float32[2, 1, 64]

    Architecture: V1 Ch6.2 (Silero VAD model selection and calibration).
    """

    def __init__(self, model_path: str | Path) -> None:
        """Load the ONNX model from disk.

        Args:
            model_path: Path to ``silero_vad.onnx``.

        Raises:
            FileNotFoundError: When the model file does not exist.
            RuntimeError: When onnxruntime cannot load the model.
        """
        import onnxruntime as ort

        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Silero VAD model not found at '{path}'. "
                "Run `python src/services/vad_endpointing/models/download_silero.py` "
                "to download the model (~1 MB)."
            )

        session_opts = ort.SessionOptions()
        session_opts.inter_op_num_threads = 1
        session_opts.intra_op_num_threads = 1
        session_opts.log_severity_level = 3  # suppress warnings

        self._session = ort.InferenceSession(
            str(path),
            sess_options=session_opts,
            providers=["CPUExecutionProvider"],
        )
        self._sr: np.ndarray = np.array(16000, dtype=np.int64)
        self._h: np.ndarray = np.zeros((2, 1, 64), dtype=np.float32)
        self._c: np.ndarray = np.zeros((2, 1, 64), dtype=np.float32)

    def predict(self, pcm_int16: bytes) -> float:
        """Run one 512-sample window through Silero VAD and return speech probability."""
        samples = np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32) / 32768.0
        audio_input = samples[np.newaxis, :]  # shape [1, 512]

        ort_inputs = {
            "input": audio_input,
            "h": self._h,
            "c": self._c,
            "sr": self._sr,
        }
        output, self._h, self._c = self._session.run(["output", "hn", "cn"], ort_inputs)
        return float(output[0][0])

    def reset(self) -> None:
        """Zero-initialise the LSTM recurrent state."""
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)


# ---------------------------------------------------------------------------
# EnergyVADModel — energy-based VAD (unit testing and offline fallback)
# ---------------------------------------------------------------------------


class EnergyVADModel:
    """Energy-based VAD for unit testing and offline environments.

    Uses RMS amplitude of the PCM window to estimate speech probability.
    This model is intentionally simple and deterministic so that tests
    can use synthetic audio (sine waves for speech, zeros for silence)
    and get reliable, reproducible probabilities.

    NOT for production use.  In production always use SileroVADModel.

    Calibration:
        silence (RMS = 0)           → probability ≈ 0.00
        very quiet (RMS < 100)      → probability ≈ 0.00
        noise floor (RMS ~ 500)     → probability ≈ 0.05
        speech signal (RMS ~ 5000)  → probability ≈ 0.70
        loud speech (RMS ≥ 10000)   → probability ≈ 1.00
    """

    # RMS normalisation constant chosen so typical speech (RMS ≈ 5000)
    # maps to ~0.70 probability (above the 0.50 speech threshold and
    # below the 0.65 barge-in threshold at moderate volumes).
    _NORMALISER: float = 7000.0

    def predict(self, pcm_int16: bytes) -> float:
        """Return energy-derived speech probability for one PCM window."""
        n_samples = len(pcm_int16) // 2
        if n_samples == 0:
            return 0.0

        samples: tuple[int, ...] = struct.unpack(f"{n_samples}h", pcm_int16)
        rms: float = (sum(s * s for s in samples) / n_samples) ** 0.5
        return float(min(1.0, rms / self._NORMALISER))

    def reset(self) -> None:
        """No internal state to reset."""


# ---------------------------------------------------------------------------
# VADEngine — facade / window processor
# ---------------------------------------------------------------------------


class VADEngine:
    """Voice activity detection engine facade.

    Wraps a VADModelProtocol implementor and provides the window-level API
    used by VADEndpointingService.  In production supply a SileroVADModel;
    in unit tests supply an EnergyVADModel.

    Window contract (V1 Ch6.2):
        Input : 512 samples x 2 bytes/sample = 1024 bytes of int16 PCM at 16 kHz
        Output: speech_probability float in [0.0, 1.0]
        Latency: p99 < 5 ms per window on CPU (SileroVADModel)

    Thresholds:
        speech_threshold  (default 0.50) — above this → speech frame
        silence_threshold (default 0.35) — below this → silence frame
        Frames between the two thresholds are treated as silence (hysteresis).
    """

    WINDOW_SIZE_SAMPLES: int = 512
    """512 samples = 32 ms at 16 kHz (Silero VAD v4 standard window)."""

    FRAME_DURATION_MS: int = 32
    """Duration of one VAD window in milliseconds."""

    SAMPLE_RATE: int = 16000
    """Required input sample rate."""

    def __init__(
        self,
        model: VADModelProtocol,
        speech_threshold: float = 0.5,
        silence_threshold: float = 0.35,
    ) -> None:
        """Construct the VADEngine with a model and calibrated thresholds.

        Args:
            model:             VAD model implementing VADModelProtocol.
            speech_threshold:  Minimum probability to declare a frame speech.
            silence_threshold: Maximum probability to declare a frame silence.
        """
        if not isinstance(model, VADModelProtocol):
            raise TypeError(f"model must implement VADModelProtocol, got {type(model)}")
        if not (0.0 < silence_threshold < speech_threshold <= 1.0):
            raise ValueError(
                f"Thresholds must satisfy 0 < silence_threshold ({silence_threshold}) "
                f"< speech_threshold ({speech_threshold}) ≤ 1"
            )
        self._model = model
        self.speech_threshold = speech_threshold
        self.silence_threshold = silence_threshold

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def process_window(self, pcm_int16: bytes) -> float:
        """Process one 32 ms (512-sample) PCM window and return speech probability.

        Args:
            pcm_int16: Exactly WINDOW_SIZE_SAMPLES * 2 = 1024 bytes of int16 PCM
                       at 16 kHz mono.  Raises ValueError if length is wrong.

        Returns:
            Speech probability in [0.0, 1.0].

        Raises:
            ValueError: When ``pcm_int16`` is not exactly 1024 bytes.
        """
        expected = self.WINDOW_SIZE_SAMPLES * 2
        if len(pcm_int16) != expected:
            raise ValueError(
                f"VADEngine.process_window requires exactly {expected} bytes "
                f"({self.WINDOW_SIZE_SAMPLES} int16 samples at 16 kHz), "
                f"got {len(pcm_int16)} bytes."
            )
        return self._model.predict(pcm_int16)

    def is_speech(self, probability: float) -> bool:
        """Return True when ``probability`` exceeds the speech threshold."""
        return probability >= self.speech_threshold

    def is_silence(self, probability: float) -> bool:
        """Return True when ``probability`` is below the silence threshold."""
        return probability < self.silence_threshold

    def reset(self) -> None:
        """Reset model recurrent state.  Call at the start of every new call."""
        self._model.reset()
