"""VeenaAdapter — TTS adapter consuming the streaming Veena HTTP inference server.

Connects to the Veena TTS service (FastAPI on port 8200) via chunked HTTP.
Uses httpx streaming client to receive 85.33ms PCM chunks as they are decoded
server-side, yielding AudioClause objects as each chunk arrives.

Streaming design (ADR-001):
  The server returns Transfer-Encoding: chunked with one PCM16LE chunk per
  SNAC super-frame (85.33ms). The adapter reads chunks via httpx aiter_bytes()
  and yields an AudioClause per chunk. A look-ahead buffer ensures the final
  chunk in the final clause is marked is_final=True.

  Phase E correction: earlier revisions of this docstring described the
  wire payload as "float32 LE PCM". The rest of the pipeline
  (``AudioOutput.convert`` uses ``audioop.ratecv(pcm, 2, 1, ...)``, i.e. 2
  bytes per sample) has always treated it as 16-bit — that is the real
  wire format. ``StartupBufferGate._DEFAULT_BYTES_PER_SAMPLE`` was
  corrected from 4 to 2 to match, so buffered-mode duration accounting
  no longer half-counts the audio.

Barge-in / generation cancellation (Phase E):
  This adapter has no direct knowledge of the playback generation. The
  cancellation channel is the pipeline's async iteration: when
  ``TrueStreamingPipeline`` detects a generation advance or barge-in it
  breaks out of ``async for audio_clause in clause_stream``. That
  triggers the ``async for raw_chunk in resp.aiter_bytes()`` generator
  in ``_stream_clause`` to unwind, which runs the
  ``finally: await resp.aclose()`` block and closes the underlying HTTP
  connection. GPU-side generation stops once the socket read side stops
  draining chunks. In practice this happens within one super-frame
  (~85 ms) — well inside a human perception window and consistent with
  "stop producing stale generation audio as soon as practical".

VRAM: Requests 7,974 MB from GPU Scheduler before inference (Veena 3B BF16 + SNAC measured footprint).
Model: maya-research/Veena (3B params, BF16, SNAC codec, 24 kHz) — production model retained.

Architecture: V1 Ch15-17 (Speech Rendering / TTS); V7 Ch6 (GPU fleet); ADR-001.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING

from src.libs.circuit_breaker.breaker import CircuitBreaker
from src.libs.contracts.streaming import AudioClause, VoiceConfig
from src.services.gpu_scheduler.admission import AdmissionDecision
from src.services.gpu_scheduler.scheduler import GPUScheduler

if TYPE_CHECKING:
    import httpx

_VEENA_MODEL_NAME = "veena"
_VEENA_VRAM_MB = 7974  # measured: Veena 3B BF16 + SNAC 24kHz = 7,974 MB actual footprint
_DEFAULT_BASE_URL = "http://localhost:8200"
_SAMPLE_RATE = 24000


class VeenaAdapter:
    """TTS adapter wrapping the streaming Veena HTTP inference server.

    Architecture: V1 Ch15-17; ADR-001.
    """

    def __init__(
        self,
        gpu_scheduler: GPUScheduler,
        base_url: str = _DEFAULT_BASE_URL,
        speaker: str = "maitri",
        vram_mb: int = _VEENA_VRAM_MB,
        model_name: str = _VEENA_MODEL_NAME,
        timeout: float = 60.0,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._gpu_scheduler = gpu_scheduler
        self._base_url = base_url.rstrip("/")
        self._speaker = speaker
        self._vram_mb = vram_mb
        self._model_name = model_name
        self._timeout = timeout
        # Optional CircuitBreaker guarding the Veena HTTP connection (Sprint-016,
        # V3 Ch14 §14.2). None (default) preserves pre-Sprint-016 behavior.
        self._breaker = breaker

    async def synthesize_stream(
        self,
        text_chunks: AsyncIterator[str],
        voice_config: VoiceConfig,
    ) -> AsyncIterator[AudioClause]:
        """Synthesise an LLM text stream into streaming audio clauses via Veena.

        Each clause is sent to the Veena server which streams back 85.33ms PCM
        chunks as they are decoded. An AudioClause is yielded per chunk so the
        PlaybackScheduler can begin playback before synthesis is complete.

        Args:
            text_chunks:  Async stream of text chunks from the LLM.
            voice_config: Prosody parameters (pitch, rate, pause, language).

        Yields:
            AudioClause per 85.33ms audio chunk, with is_final=True on the last
            chunk of the last clause.

        Raises:
            RuntimeError: If GPU Scheduler rejects the VRAM request.
        """
        return self._synthesize_gen(text_chunks, voice_config)

    async def _synthesize_gen(
        self,
        text_chunks: AsyncIterator[str],
        voice_config: VoiceConfig,
    ) -> AsyncIterator[AudioClause]:
        from src.services.tts.metrics import (
            gpu_allocation_time_ms,
            tts_clauses_total,
            tts_first_clause_latency_ms,
            tts_full_synthesis_latency_ms,
            tts_requests_total,
        )

        alloc_start = time.monotonic()
        decision, token = self._gpu_scheduler.request_allocation(
            service="tts",
            model=self._model_name,
            required_vram_mb=self._vram_mb,
        )
        alloc_elapsed = (time.monotonic() - alloc_start) * 1000
        gpu_allocation_time_ms.observe(alloc_elapsed)

        if decision != AdmissionDecision.APPROVE or token is None:
            tts_requests_total.labels(status="rejected").inc()
            raise RuntimeError(f"GPU Scheduler rejected VRAM for {self._model_name} ({self._vram_mb} MB)")

        start = time.monotonic()
        first_yielded = False

        # Phase C (Sprint-030): the adapter no longer splits text. Sentence
        # segmentation is performed by the single authoritative ClauseSplitter
        # in TrueStreamingPipeline. The adapter concatenates all incoming
        # text_chunks into one clause and issues one Veena request.
        try:
            parts: list[str] = []
            async for chunk in text_chunks:
                if chunk:
                    parts.append(chunk)
            clause_text = "".join(parts).strip()

            if clause_text:
                async for audio_clause in self._stream_clause(
                    clause_text,
                    voice_config,
                    clause_idx=0,
                    is_final_clause=True,
                ):
                    if not first_yielded:
                        tts_first_clause_latency_ms.observe((time.monotonic() - start) * 1000)
                        first_yielded = True
                    tts_clauses_total.inc()
                    yield audio_clause

            tts_full_synthesis_latency_ms.observe((time.monotonic() - start) * 1000)
            tts_requests_total.labels(status="success").inc()

        except Exception:
            tts_requests_total.labels(status="error").inc()
            raise
        finally:
            self._gpu_scheduler.release_allocation(token)

    async def _stream_clause(
        self,
        text: str,
        voice_config: VoiceConfig,
        clause_idx: int,
        is_final_clause: bool,
    ) -> AsyncIterator[AudioClause]:
        """Stream one clause from the Veena server, yielding AudioClause per 85ms chunk.

        Uses look-ahead buffering: the current chunk is held until the next arrives,
        so the last chunk can be marked is_final=True correctly.

        Args:
            text: Clause text to synthesise.
            voice_config: Prosody parameters.
            clause_idx: 0-based index of this clause in the turn.
            is_final_clause: True if this is the last clause in the turn.

        Yields:
            AudioClause per 85.33ms PCM chunk.
        """
        import httpx

        payload = {
            "text": text,
            "speaker": self._speaker,
            "voice_config": {
                "pitch_shift": voice_config.pitch_shift,
                "rate_scale": voice_config.rate_scale,
                "energy_scale": voice_config.energy_scale,
                "pause_ms_after_clause": voice_config.pause_ms_after_clause,
                "language": voice_config.language,
            },
        }

        pending: bytes | None = None
        url = f"{self._base_url}/synthesize"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            # See vLLMAdapter._open_stream: the breaker guards only connection
            # establishment, never the streaming body (preserves true
            # chunk-by-chunk playback, V1 Ch15-17).
            if self._breaker is not None:
                resp = await self._breaker.call(self._open_stream, client, url, payload)
            else:
                resp = await self._open_stream(client, url, payload)
            try:
                async for raw_chunk in resp.aiter_bytes():
                    if not raw_chunk:
                        continue
                    if pending is not None:
                        yield AudioClause(
                            audio_data=pending,
                            sample_rate=_SAMPLE_RATE,
                            text=text,
                            clause_index=clause_idx,
                            is_final=False,
                        )
                    pending = raw_chunk
            finally:
                await resp.aclose()

        # Yield the last buffered chunk — marked is_final only if last clause
        if pending is not None:
            yield AudioClause(
                audio_data=pending,
                sample_rate=_SAMPLE_RATE,
                text=text,
                clause_index=clause_idx,
                is_final=is_final_clause,
            )

    @staticmethod
    async def _open_stream(client: httpx.AsyncClient, url: str, payload: Mapping[str, object]) -> httpx.Response:
        """Open the streaming POST to Veena and validate the response status.

        This is the unit the circuit breaker wraps — see
        ``vLLMAdapter._open_stream`` for the identical rationale.
        """
        request = client.build_request("POST", url, json=payload)
        response = await client.send(request, stream=True)
        response.raise_for_status()
        return response
