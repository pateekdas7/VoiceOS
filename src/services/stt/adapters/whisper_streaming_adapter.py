"""WhisperStreamingAdapter -- STT over WebSocket."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse, urlunparse

from src.libs.circuit_breaker.breaker import CircuitBreaker
from src.libs.contracts.audio import AudioFrame
from src.libs.contracts.streaming import WordHypothesis
from src.services.gpu_scheduler.admission import AdmissionDecision
from src.services.gpu_scheduler.scheduler import GPUScheduler

_WHISPER_MODEL_NAME = "whisper-large-v3-turbo"
_WHISPER_VRAM_MB = 6144
_DEFAULT_BASE_URL = "http://localhost:8100"


def _http_to_ws_url(base_url: str, path: str = "/transcribe_ws") -> str:
    """Translate an http(s) base URL to ws(s) with the given path."""
    parsed = urlparse(base_url.rstrip("/"))
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


class WhisperStreamingAdapter:
    """STT adapter streaming PCM frames over a WebSocket."""

    def __init__(
        self,
        gpu_scheduler: GPUScheduler,
        base_url: str = _DEFAULT_BASE_URL,
        vram_mb: int = _WHISPER_VRAM_MB,
        model_name: str = _WHISPER_MODEL_NAME,
        timeout: float = 30.0,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._gpu_scheduler = gpu_scheduler
        self._base_url = base_url.rstrip("/")
        self._vram_mb = vram_mb
        self._model_name = model_name
        self._timeout = timeout
        self._breaker = breaker


    async def transcribe_stream(
        self,
        audio_frames: AsyncIterator[AudioFrame],
        language: str,
    ) -> AsyncIterator[WordHypothesis]:
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
        gpu_allocation_time_ms.observe((time.monotonic() - alloc_start) * 1000)
        if decision != AdmissionDecision.APPROVE or token is None:
            stt_requests_total.labels(status="rejected").inc()
            raise RuntimeError(
                f"GPU Scheduler rejected VRAM request for {self._model_name} ({self._vram_mb} MB)"
            )

        lang_q = language or ""
        ws_url = _http_to_ws_url(self._base_url) + "?language=" + lang_q
        infer_start = time.monotonic()
        try:
            if self._breaker is not None:
                gen = await self._breaker.call(self._stream_words, audio_frames, ws_url)
            else:
                gen = self._stream_words(audio_frames, ws_url)
            async for hyp in gen:
                yield hyp
            stt_latency_ms.observe((time.monotonic() - infer_start) * 1000)
            stt_requests_total.labels(status="success").inc()
        except Exception:
            stt_requests_total.labels(status="error").inc()
            raise
        finally:
            self._gpu_scheduler.release_allocation(token)

    async def _stream_words(
        self,
        audio_frames: AsyncIterator[AudioFrame],
        ws_url: str,
    ) -> AsyncIterator[WordHypothesis]:
        import websockets

        async with websockets.connect(ws_url, max_size=None, ping_interval=20) as ws:
            sender = asyncio.create_task(self._sender(audio_frames, ws))
            try:
                while True:
                    try:
                        raw = await ws.recv()
                    except websockets.ConnectionClosedOK:
                        break
                    except websockets.ConnectionClosedError:
                        break
                    if isinstance(raw, (bytes, bytearray)):
                        continue
                    try:
                        payload = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    if "error" in payload:
                        raise RuntimeError(f"STT WS server error: {payload['error']}")
                    words = payload.get("words") or []
                    is_partial_msg = bool(payload.get("partial", True))
                    for w in words:
                        yield WordHypothesis(
                            word=w["word"],
                            confidence=float(w.get("confidence", 0.0)),
                            start_ms=int(w.get("start_ms", 0)),
                            end_ms=int(w.get("end_ms", 0)),
                            is_final=bool(w.get("is_final", not is_partial_msg)),
                        )
                    if not is_partial_msg:
                        break
            finally:
                if not sender.done():
                    sender.cancel()
                    try:
                        await sender
                    except (asyncio.CancelledError, Exception):
                        pass

    @staticmethod
    async def _sender(audio_frames, ws) -> None:
        try:
            async for frame in audio_frames:
                pcm = frame.pcm_data
                if not pcm:
                    continue
                try:
                    await ws.send(pcm)
                except Exception:
                    return
        finally:
            try:
                await ws.send(json.dumps({"type": "eos"}))
            except Exception:
                pass
