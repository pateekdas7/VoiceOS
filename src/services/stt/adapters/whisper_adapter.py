"""WhisperAdapter — STT adapter wrapping Whisper via faster-whisper.

Implements STTAdapter using the faster-whisper library (CTranslate2-based).
Streams WordHypothesis objects from Whisper segment/word timestamps.

VRAM: Requests 6,144 MB from GPU Scheduler before inference; releases on
completion.  The actual loaded footprint is ~900 MB (int8_float16, CTranslate2
manages its own CUDA memory outside torch.cuda.memory_allocated).

Model: Whisper large-v3-turbo, compute_type=int8_float16 (INT8 weights,
FP16 activations).  FP8 is not supported by CTranslate2 on NVIDIA L4 (SM 8.9).

Architecture: V1 Ch8 (STT); V7 Ch6 (GPU fleet).
"""

from __future__ import annotations

import asyncio
import io
import struct
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from src.libs.circuit_breaker.breaker import CircuitBreaker
from src.libs.contracts.audio import AudioFrame
from src.libs.contracts.streaming import WordHypothesis
from src.services.gpu_scheduler.admission import AdmissionDecision
from src.services.gpu_scheduler.scheduler import GPUScheduler

if TYPE_CHECKING:
    pass

_WHISPER_MODEL_NAME = "whisper-large-v3-turbo"
_WHISPER_VRAM_MB = 6144


class WhisperAdapter:
    """STT adapter wrapping a faster-whisper WhisperModel.

    The model instance is passed in at construction time so it can be
    mocked in tests without requiring faster-whisper to be installed in
    the test environment.

    Architecture: V1 Ch8.
    """

    def __init__(
        self,
        model: Any,
        gpu_scheduler: GPUScheduler,
        vram_mb: int = _WHISPER_VRAM_MB,
        model_name: str = _WHISPER_MODEL_NAME,
        beam_size: int = 5,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        """Initialise the adapter.

        Args:
            model:        A faster-whisper WhisperModel instance (or mock).
            gpu_scheduler: GPU Scheduler for VRAM acquisition.
            vram_mb:      VRAM to reserve before inference (scheduler ledger).
            model_name:   Model identifier used in scheduler reservation.
            beam_size:    Beam size passed to WhisperModel.transcribe().
            breaker:      Optional CircuitBreaker guarding the Whisper executor
                call (Sprint-016, V3 Ch14 §14.2 — the STT model-executor
                dependency). None (default) preserves pre-Sprint-016 behavior.
        """
        self._model = model
        self._gpu_scheduler = gpu_scheduler
        self._vram_mb = vram_mb
        self._model_name = model_name
        self._beam_size = beam_size
        self._breaker = breaker

    async def transcribe_stream(
        self,
        audio_frames: AsyncIterator[AudioFrame],
        language: str,
    ) -> AsyncIterator[WordHypothesis]:
        """Transcribe an audio frame stream, yielding WordHypothesis per word.

        Collects all frames into a PCM buffer, runs Whisper in a thread
        executor (Whisper is synchronous), then yields per-word hypotheses
        from the segment word timestamps.

        Args:
            audio_frames: Async stream of PCM16LE frames at 16 kHz.
            language:     BCP-47 language code (e.g. 'en', 'hi').

        Yields:
            WordHypothesis for each word.  The last hypothesis has
            ``is_final=True``.

        Raises:
            RuntimeError: If GPU Scheduler rejects the VRAM request.
        """
        return self._transcribe_gen(audio_frames, language)

    async def _transcribe_gen(
        self,
        audio_frames: AsyncIterator[AudioFrame],
        language: str,
    ) -> AsyncIterator[WordHypothesis]:
        from src.services.stt.metrics import (
            gpu_allocation_time_ms,
            stt_latency_ms,
            stt_requests_total,
        )

        alloc_start = time.monotonic()
        decision, token = self._gpu_scheduler.request_allocation(
            service="stt",
            model=self._model_name,
            required_vram_mb=self._vram_mb,
        )
        alloc_elapsed = (time.monotonic() - alloc_start) * 1000
        gpu_allocation_time_ms.observe(alloc_elapsed)

        if decision != AdmissionDecision.APPROVE or token is None:
            stt_requests_total.labels(status="rejected").inc()
            raise RuntimeError(f"GPU Scheduler rejected VRAM request for {self._model_name} ({self._vram_mb} MB)")

        try:
            pcm_bytes = await self._collect_frames(audio_frames)
            infer_start = time.monotonic()
            if self._breaker is not None:
                words = await self._breaker.call(self._run_whisper_async, pcm_bytes, language)
            else:
                words = await self._run_whisper_async(pcm_bytes, language)
            stt_latency_ms.observe((time.monotonic() - infer_start) * 1000)
            stt_requests_total.labels(status="success").inc()
        finally:
            self._gpu_scheduler.release_allocation(token)

        total = len(words)
        for idx, (word, confidence, start_ms, end_ms) in enumerate(words):
            is_final = idx == total - 1
            yield WordHypothesis(
                word=word,
                confidence=confidence,
                start_ms=start_ms,
                end_ms=end_ms,
                is_final=is_final,
            )

    async def _collect_frames(self, audio_frames: AsyncIterator[AudioFrame]) -> bytes:
        """Collect all PCM frames into a single bytes buffer."""
        buf = io.BytesIO()
        async for frame in audio_frames:
            buf.write(frame.pcm_data)
        return buf.getvalue()

    async def _run_whisper_async(self, pcm_bytes: bytes, language: str) -> list[tuple[str, float, int, int]]:
        """Async wrapper around :meth:`_run_whisper`, run off the event loop thread.

        This is the unit the circuit breaker guards — the STT model executor
        (V3 Ch14 §14.2) — so repeated Whisper failures fail fast instead of
        continuing to submit work to a broken executor.
        """
        return await asyncio.get_event_loop().run_in_executor(None, self._run_whisper, pcm_bytes, language)

    def _run_whisper(self, pcm_bytes: bytes, language: str) -> list[tuple[str, float, int, int]]:
        """Synchronous Whisper transcription — runs in thread executor.

        Returns:
            List of (word, confidence, start_ms, end_ms) tuples.
        """
        pcm_float = _pcm16le_to_float32(pcm_bytes)
        segments, _ = self._model.transcribe(
            pcm_float,
            language=language if language else None,
            beam_size=self._beam_size,
            word_timestamps=True,
        )
        words: list[tuple[str, float, int, int]] = []
        for seg in segments:
            if seg.words:
                for w in seg.words:
                    words.append(
                        (
                            w.word.strip(),
                            float(w.probability),
                            int(w.start * 1000),
                            int(w.end * 1000),
                        )
                    )
            else:
                text = seg.text.strip()
                if text:
                    words.append(
                        (
                            text,
                            0.9,
                            int(seg.start * 1000),
                            int(seg.end * 1000),
                        )
                    )
        return words


def _pcm16le_to_float32(pcm_bytes: bytes) -> Any:
    """Convert PCM16LE bytes to a float32 numpy array normalised to [-1, 1]."""
    import numpy as np

    n_samples = len(pcm_bytes) // 2
    samples = struct.unpack(f"<{n_samples}h", pcm_bytes[: n_samples * 2])
    arr = np.array(samples, dtype=np.float32) / 32768.0
    return arr
