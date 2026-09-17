"""Unit tests for WhisperHTTPAdapter (Path-A Phase 3).

All tests use httpx.MockTransport — no real network or GPU required.

Architecture: V1 Ch8; Path-A consolidation Phase 3.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import httpx
import pytest

from src.libs.circuit_breaker.breaker import CircuitBreaker, CircuitBreakerConfig, CircuitOpenError, CircuitState
from src.libs.contracts.audio import AudioConfig, AudioFrame, Encoding, SampleRate
from src.libs.contracts.streaming import WordHypothesis
from src.services.gpu_scheduler.admission import AdmissionDecision
from src.services.gpu_scheduler.scheduler import GPUScheduler
from src.services.gpu_scheduler.vram_ledger import AllocationToken
from src.services.stt.adapters.whisper_http_adapter import WhisperHTTPAdapter
from src.services.stt.protocol import STTAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_frame(pcm_data: bytes = b"\x00\x00" * 320) -> AudioFrame:
    return AudioFrame(
        pcm_data=pcm_data,
        seq=0,
        rtp_ts=0,
        recv_ts=0.0,
        config=AudioConfig(sample_rate=SampleRate.RATE_16K, encoding=Encoding.PCM16LE),
    )


async def _frames_gen(frames: list[AudioFrame]) -> AsyncIterator[AudioFrame]:
    for f in frames:
        yield f


def _make_fake_scheduler(approve: bool = True) -> MagicMock:
    scheduler = MagicMock(spec=GPUScheduler)
    if approve:
        token = AllocationToken(token_id="tok-1", device_id="gpu-0", model_id="whisper-large-v3-turbo", vram_mb=6144)
        scheduler.request_allocation.return_value = (AdmissionDecision.APPROVE, token)
    else:
        scheduler.request_allocation.return_value = (AdmissionDecision.REJECT, None)
    scheduler.release_allocation.return_value = None
    return scheduler


def _mock_transcribe_response(
    words: list[tuple[str, float, int, int, bool]] | None = None,
    status_code: int = 200,
) -> httpx.MockTransport:
    """A MockTransport standing in for the GPU node's /transcribe endpoint,
    matching deployment/gpu/services/stt/server.py's TranscribeResponse shape."""
    if words is None:
        words = [("namaste", 0.95, 0, 300, False), ("aap", 0.90, 300, 600, False), ("kaise", 0.88, 600, 1000, True)]

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/transcribe"
        body = json.loads(request.content)
        assert "audio_b64" in body
        assert "language" in body
        assert "beam_size" in body
        if status_code != 200:
            return httpx.Response(status_code, json={"detail": "error"})
        return httpx.Response(
            200,
            json={
                "words": [
                    {"word": w, "confidence": c, "start_ms": s, "end_ms": e, "is_final": f}
                    for w, c, s, e, f in words
                ],
                "language": "hi",
                "duration_ms": 1000,
                "latency_ms": 42.0,
            },
        )

    return httpx.MockTransport(_handler)


def _patch_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    """Patch httpx.AsyncClient so WhisperHTTPAdapter's `async with
    httpx.AsyncClient(timeout=...)` uses this test's MockTransport instead
    of a real network connection."""
    real_client_cls = httpx.AsyncClient

    def _client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_client_cls(transport=transport, timeout=5.0)

    monkeypatch.setattr("httpx.AsyncClient", _client_factory)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_whisper_http_adapter_satisfies_stt_protocol() -> None:
    adapter = WhisperHTTPAdapter(gpu_scheduler=_make_fake_scheduler())
    assert isinstance(adapter, STTAdapter)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transcribe_stream_yields_words_from_http_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _mock_transcribe_response())
    adapter = WhisperHTTPAdapter(gpu_scheduler=_make_fake_scheduler(), base_url="http://gpu-node:8100")

    gen = await adapter.transcribe_stream(_frames_gen([_make_frame()]), language="hi")
    results: list[WordHypothesis] = [hyp async for hyp in gen]

    assert len(results) == 3
    assert results[0].word == "namaste"
    assert results[0].is_final is False
    assert results[-1].word == "kaise"
    assert results[-1].is_final is True
    assert all(0.0 <= r.confidence <= 1.0 for r in results)


@pytest.mark.asyncio
async def test_gpu_scheduler_called_before_and_after_http_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _mock_transcribe_response())
    scheduler = _make_fake_scheduler()
    adapter = WhisperHTTPAdapter(gpu_scheduler=scheduler)

    gen = await adapter.transcribe_stream(_frames_gen([_make_frame()]), language="en")
    async for _ in gen:
        pass

    scheduler.request_allocation.assert_called_once_with(
        service="stt", model="whisper-large-v3-turbo", required_vram_mb=6144
    )
    scheduler.release_allocation.assert_called_once()


@pytest.mark.asyncio
async def test_request_body_matches_gpu_server_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies the exact request shape deployment/gpu/services/stt/server.py's
    TranscribeRequest expects: audio_b64 (base64 PCM16LE), language, beam_size."""
    seen: dict[str, object] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"words": [], "language": "hi", "duration_ms": 0, "latency_ms": 1.0})

    _patch_client(monkeypatch, httpx.MockTransport(_handler))
    adapter = WhisperHTTPAdapter(gpu_scheduler=_make_fake_scheduler(), beam_size=3)

    pcm = b"\x01\x02" * 100
    gen = await adapter.transcribe_stream(_frames_gen([_make_frame(pcm)]), language="hi")
    async for _ in gen:
        pass

    import base64

    assert seen["language"] == "hi"
    assert seen["beam_size"] == 3
    assert base64.b64decode(seen["audio_b64"]) == pcm  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# VRAM rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raises_on_vram_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _mock_transcribe_response())
    adapter = WhisperHTTPAdapter(gpu_scheduler=_make_fake_scheduler(approve=False))

    gen = await adapter.transcribe_stream(_frames_gen([_make_frame()]), language="en")
    with pytest.raises(RuntimeError, match="rejected"):
        async for _ in gen:
            pass


# ---------------------------------------------------------------------------
# HTTP error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raises_on_http_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _mock_transcribe_response(status_code=500))
    scheduler = _make_fake_scheduler()
    adapter = WhisperHTTPAdapter(gpu_scheduler=scheduler)

    gen = await adapter.transcribe_stream(_frames_gen([_make_frame()]), language="en")
    with pytest.raises(httpx.HTTPStatusError):
        async for _ in gen:
            pass
    # VRAM must still be released even though the HTTP call failed.
    scheduler.release_allocation.assert_called_once()


# ---------------------------------------------------------------------------
# CircuitBreaker wiring (Sprint-016 pattern, same as WhisperAdapter/VeenaAdapter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_repeated_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _mock_transcribe_response(status_code=500))
    breaker = CircuitBreaker("stt-http", CircuitBreakerConfig(failure_threshold=1))
    adapter = WhisperHTTPAdapter(gpu_scheduler=_make_fake_scheduler(), breaker=breaker)

    gen = await adapter.transcribe_stream(_frames_gen([_make_frame()]), language="en")
    with pytest.raises(httpx.HTTPStatusError):
        async for _ in gen:
            pass

    assert breaker.state == CircuitState.OPEN

    gen2 = await adapter.transcribe_stream(_frames_gen([_make_frame()]), language="en")
    with pytest.raises(CircuitOpenError):
        async for _ in gen2:
            pass
