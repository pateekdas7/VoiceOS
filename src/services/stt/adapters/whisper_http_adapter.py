"""WhisperHTTPAdapter — STT adapter calling the remote Whisper HTTP server.

Path-A consolidation, Phase 3. The pre-existing `WhisperAdapter` (see
whisper_adapter.py) expects an in-process faster-whisper `WhisperModel` —
it was never actually usable on the CPU node (no GPU there), and no
HTTP-based STT adapter existed anywhere in this repo despite
`deployment/gpu/services/stt/server.py`'s own docstring saying it serves
"the CPU-side WhisperAdapter" over HTTP. This adapter is that missing
piece: it calls the GPU node's real `/transcribe` endpoint (port 8100),
mirroring the httpx + GPUScheduler + CircuitBreaker pattern already
established by `vLLMAdapter`/`VeenaAdapter` (the two adapters that DO
already call the GPU node over HTTP) rather than the in-process pattern
`WhisperAdapter` uses.

GPU VRAM accounting: request_allocation()/release_allocation() against the
shared GPUScheduler is still performed here (same as WhisperAdapter) even
though inference happens on a different machine — the ledger tracks this
CPU-node process's outstanding requests against the GPU node's known
capacity budget, exactly as vLLMAdapter/VeenaAdapter already do for LLM/TTS.

Architecture: V1 Ch8 (STT); V7 Ch6 (GPU fleet).
"""

from __future__ import annotations

import base64
import io
import logging
import time
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any

from src.libs.circuit_breaker.breaker import CircuitBreaker
from src.libs.contracts.audio import AudioFrame
from src.libs.contracts.streaming import WordHypothesis
from src.services.gpu_scheduler.admission import AdmissionDecision
from src.services.gpu_scheduler.scheduler import GPUScheduler

if TYPE_CHECKING:
    import httpx

_WHISPER_MODEL_NAME = "whisper-large-v3-turbo"
_WHISPER_VRAM_MB = 6144
_DEFAULT_BASE_URL = "http://localhost:8100"

logger = logging.getLogger(__name__)


class STTRetryExhaustedError(RuntimeError):
    """Raised when all STT retry attempts are exhausted (Phase 9c).

    Caught by CallOrchestrator._run_turns() to trigger a clarify_ask_repeat
    response and increment the consecutive-failure counter. After 3 consecutive
    failures the orchestrator initiates a graceful hangup.
    """


class WhisperHTTPAdapter:
    """STT adapter calling the GPU node's Whisper HTTP inference server.

    Implements the same `STTAdapter` structural protocol as `WhisperAdapter`
    (`src/services/stt/protocol.py`) — either can be injected into
    `STTService` interchangeably.

    Architecture: V1 Ch8.
    """

    def __init__(
        self,
        gpu_scheduler: GPUScheduler,
        base_url: str = _DEFAULT_BASE_URL,
        vram_mb: int = _WHISPER_VRAM_MB,
        model_name: str = _WHISPER_MODEL_NAME,
        beam_size: int = 5,
        timeout: float = 30.0,
        breaker: CircuitBreaker | None = None,
        gpu_secret: str | None = None,
        max_retries: int = 1,
        retry_backoff_s: float = 0.5,
    ) -> None:
        """Initialise the adapter.

        Args:
            gpu_scheduler:   GPU Scheduler for VRAM ledger accounting.
            base_url:        Base URL of the GPU node's STT server (port 8100).
            vram_mb:         VRAM to reserve before inference (scheduler ledger).
            model_name:      Model identifier used in scheduler reservation.
            beam_size:       Beam size sent to the server's /transcribe endpoint.
            timeout:         httpx request timeout in seconds.
            breaker:         Optional CircuitBreaker guarding the HTTP call.
            gpu_secret:      Shared secret sent as X-GPU-Secret header (Phase 9a).
                             None means no authentication header is sent — for
                             deployments where the GPU node is on a private
                             network without additional auth.
            max_retries:     How many additional attempts to make after the first
                             failure (Phase 9c). Default 1 = one retry.
            retry_backoff_s: Seconds to wait between retry attempts (Phase 9c).
        """
        self._gpu_scheduler = gpu_scheduler
        self._base_url = base_url.rstrip("/")
        self._vram_mb = vram_mb
        self._model_name = model_name
        self._beam_size = beam_size
        self._timeout = timeout
        self._breaker = breaker
        self._gpu_secret = gpu_secret
        self._max_retries = max_retries
        self._retry_backoff_s = retry_backoff_s

    async def transcribe_stream(
        self,
        audio_frames: AsyncIterator[AudioFrame],
        language: str,
    ) -> AsyncIterator[WordHypothesis]:
        """Transcribe an audio frame stream via the GPU node's HTTP server.

        Collects all frames into a PCM16LE buffer, POSTs it (base64-encoded)
        to the server's /transcribe endpoint in one request, then yields
        per-word hypotheses from the JSON response. Non-streaming HTTP (the
        server returns the full word list in one response) — the streaming
        contract this method exposes is over WordHypothesis yielding, same
        as WhisperAdapter, not over the underlying HTTP transport.

        Args:
            audio_frames: Async stream of PCM16LE frames at 16 kHz.
            language:     BCP-47 language code (e.g. 'en', 'hi').

        Yields:
            WordHypothesis for each word. The last hypothesis has
            ``is_final=True``.

        Raises:
            RuntimeError: If GPU Scheduler rejects the VRAM request.
            httpx.HTTPStatusError: If the server returns a non-2xx response.
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
            import asyncio

            pcm_bytes = await self._collect_frames(audio_frames)
            infer_start = time.monotonic()
            last_exc: BaseException | None = None
            for attempt in range(self._max_retries + 1):
                try:
                    words_payload = await self._call_transcribe(pcm_bytes, language)
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt < self._max_retries:
                        logger.warning(
                            "STT attempt %d/%d failed (%s: %s), retrying in %.1fs",
                            attempt + 1,
                            self._max_retries + 1,
                            type(exc).__name__,
                            exc,
                            self._retry_backoff_s,
                        )
                        await asyncio.sleep(self._retry_backoff_s)
            if last_exc is not None:
                stt_requests_total.labels(status="error").inc()
                raise STTRetryExhaustedError(
                    f"STT failed after {self._max_retries + 1} attempts"
                ) from last_exc
            stt_latency_ms.observe((time.monotonic() - infer_start) * 1000)
            stt_requests_total.labels(status="success").inc()
        except STTRetryExhaustedError:
            raise
        except Exception:
            stt_requests_total.labels(status="error").inc()
            raise
        finally:
            self._gpu_scheduler.release_allocation(token)

        for word in words_payload:
            yield WordHypothesis(
                word=word["word"],
                confidence=word["confidence"],
                start_ms=word["start_ms"],
                end_ms=word["end_ms"],
                is_final=word["is_final"],
            )

    async def _collect_frames(self, audio_frames: AsyncIterator[AudioFrame]) -> bytes:
        """Collect all PCM frames into a single bytes buffer."""
        buf = io.BytesIO()
        async for frame in audio_frames:
            buf.write(frame.pcm_data)
        return buf.getvalue()

    async def _call_transcribe(self, pcm_bytes: bytes, language: str) -> list[dict[str, Any]]:
        """POST the collected audio to the GPU node's /transcribe endpoint.

        Matches deployment/gpu/services/stt/server.py's TranscribeRequest/
        TranscribeResponse contract exactly: {"audio_b64", "language",
        "beam_size"} in, {"words": [{"word","confidence","start_ms",
        "end_ms","is_final"}], "language", "duration_ms", "latency_ms"} out.
        """
        import httpx

        payload = {
            "audio_b64": base64.b64encode(pcm_bytes).decode("ascii"),
            "language": language or "",
            "beam_size": self._beam_size,
        }
        url = f"{self._base_url}/transcribe"
        headers: dict[str, str] = {}
        if self._gpu_secret:
            headers["X-GPU-Secret"] = self._gpu_secret

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            if self._breaker is not None:
                response = await self._breaker.call(self._post, client, url, payload, headers or None)
            else:
                response = await self._post(client, url, payload, headers or None)
            data: dict[str, Any] = response.json()
        words: list[dict[str, Any]] = data["words"]
        return words

    @staticmethod
    async def _post(
        client: httpx.AsyncClient,
        url: str,
        payload: Mapping[str, object],
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """POST and validate the response status.

        This is the unit the circuit breaker wraps — see
        ``vLLMAdapter._open_stream``/``VeenaAdapter._open_stream`` for the
        identical rationale.
        The optional ``headers`` argument carries the X-GPU-Secret auth header
        (Phase 9a) when a shared GPU secret is configured.
        """
        response = await client.post(url, json=payload, headers=dict(headers) if headers else {})
        response.raise_for_status()
        return response
