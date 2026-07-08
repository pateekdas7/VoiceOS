"""Unit tests for Sprint-009 STT adapter service.

All tests use mocked backends — no real Whisper model or GPU required.

Architecture: V1 Ch8; Sprint-009 acceptance criteria.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest

from src.libs.circuit_breaker.breaker import CircuitBreaker, CircuitBreakerConfig, CircuitOpenError, CircuitState
from src.libs.contracts.audio import AudioConfig, AudioFrame, Encoding, SampleRate
from src.libs.contracts.streaming import WordHypothesis
from src.services.gpu_scheduler.admission import AdmissionDecision
from src.services.gpu_scheduler.scheduler import GPUScheduler
from src.services.gpu_scheduler.vram_ledger import AllocationToken
from src.services.stt.adapters.whisper_adapter import WhisperAdapter
from src.services.stt.protocol import STTAdapter
from src.services.stt.service import STTService, STTServiceConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_frame(pcm_data: bytes = b"\x00\x00" * 320) -> AudioFrame:
    return AudioFrame(
        pcm_data=pcm_data,
        seq=0,
        rtp_ts=0,
        recv_ts=0.0,
        config=AudioConfig(
            sample_rate=SampleRate.RATE_16K,
            encoding=Encoding.PCM16LE,
        ),
    )


async def _frames_gen(frames: list[AudioFrame]) -> AsyncIterator[AudioFrame]:
    for f in frames:
        yield f


def _make_fake_scheduler(approve: bool = True) -> MagicMock:
    scheduler = MagicMock(spec=GPUScheduler)
    if approve:
        token = AllocationToken(
            token_id="tok-1",
            device_id="gpu-0",
            model_id="whisper-large-v3-turbo",
            vram_mb=6144,
        )
        scheduler.request_allocation.return_value = (AdmissionDecision.APPROVE, token)
    else:
        scheduler.request_allocation.return_value = (AdmissionDecision.REJECT, None)
    scheduler.release_allocation.return_value = None
    return scheduler


def _make_whisper_model(
    words: list[tuple[str, float, float, float]] | None = None,
) -> MagicMock:
    """Build a fake WhisperModel that returns word-level segments."""
    if words is None:
        words = [("hello", 0.95, 0.0, 0.5), ("world", 0.92, 0.5, 1.0)]

    model = MagicMock()
    fake_words = []
    for text, prob, start, end in words:
        w = MagicMock()
        w.word = text
        w.probability = prob
        w.start = start
        w.end = end
        fake_words.append(w)

    seg = MagicMock()
    seg.text = " ".join(t for t, *_ in words)
    seg.words = fake_words
    seg.start = words[0][2] if words else 0.0
    seg.end = words[-1][3] if words else 0.0

    model.transcribe.return_value = ([seg], MagicMock())
    return model


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_stt_adapter_protocol_is_runtime_checkable() -> None:
    """STTAdapter protocol is runtime_checkable."""
    assert getattr(STTAdapter, "_is_runtime_protocol", False) is True


def test_whisper_adapter_satisfies_stt_protocol() -> None:
    """WhisperAdapter structurally satisfies STTAdapter Protocol."""
    scheduler = _make_fake_scheduler()
    adapter = WhisperAdapter(model=_make_whisper_model(), gpu_scheduler=scheduler)
    assert isinstance(adapter, STTAdapter)


# ---------------------------------------------------------------------------
# test_whisper_adapter_streams_words (required AC test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whisper_adapter_streams_words() -> None:
    """WhisperAdapter.transcribe_stream yields WordHypothesis per word.

    Sprint-009 acceptance criteria: 'feed fake audio, adapter streams WordHypothesis'.
    """
    words = [("namaste", 0.95, 0.0, 0.3), ("aap", 0.90, 0.3, 0.6), ("kaise", 0.88, 0.6, 1.0)]
    model = _make_whisper_model(words)
    scheduler = _make_fake_scheduler()
    adapter = WhisperAdapter(model=model, gpu_scheduler=scheduler)

    frames = [_make_frame()]
    gen = await adapter.transcribe_stream(_frames_gen(frames), language="hi")
    results: list[WordHypothesis] = []
    async for hyp in gen:
        results.append(hyp)

    assert len(results) == 3
    assert results[0].word == "namaste"
    assert results[0].is_final is False
    assert results[-1].is_final is True
    assert all(0.0 <= r.confidence <= 1.0 for r in results)
    assert all(r.start_ms >= 0 for r in results)
    assert all(r.end_ms >= 0 for r in results)


# ---------------------------------------------------------------------------
# test_gpu_scheduler_called_before_inference (required AC test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gpu_scheduler_called_before_inference() -> None:
    """GPU Scheduler acquire is called before Whisper inference.

    Sprint-009: 'mock GPU Scheduler, verify acquire() called'.
    """
    scheduler = _make_fake_scheduler()
    model = _make_whisper_model()
    adapter = WhisperAdapter(model=model, gpu_scheduler=scheduler)

    frames = [_make_frame()]
    gen = await adapter.transcribe_stream(_frames_gen(frames), language="en")
    async for _ in gen:
        pass

    scheduler.request_allocation.assert_called_once_with(
        service="stt",
        model="whisper-large-v3-turbo",
        required_vram_mb=6144,
    )
    scheduler.release_allocation.assert_called_once()


# ---------------------------------------------------------------------------
# VRAM rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whisper_adapter_raises_on_vram_rejection() -> None:
    """WhisperAdapter raises RuntimeError when GPU Scheduler rejects."""
    scheduler = _make_fake_scheduler(approve=False)
    model = _make_whisper_model()
    adapter = WhisperAdapter(model=model, gpu_scheduler=scheduler)

    frames = [_make_frame()]
    gen = await adapter.transcribe_stream(_frames_gen(frames), language="en")

    with pytest.raises(RuntimeError, match="rejected"):
        async for _ in gen:
            pass


# ---------------------------------------------------------------------------
# CircuitBreaker wiring (Sprint-016)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whisper_adapter_circuit_breaker_opens_on_repeated_inference_failure() -> None:
    """Sprint-016: WhisperAdapter's optional breaker trips OPEN on sustained executor failure."""
    scheduler = _make_fake_scheduler()
    model = MagicMock()
    model.transcribe.side_effect = RuntimeError("whisper executor crashed")
    breaker = CircuitBreaker("stt", CircuitBreakerConfig(failure_threshold=1))
    adapter = WhisperAdapter(model=model, gpu_scheduler=scheduler, breaker=breaker)

    frames = [_make_frame()]
    gen = await adapter.transcribe_stream(_frames_gen(frames), language="en")
    with pytest.raises(RuntimeError, match="crashed"):
        async for _ in gen:
            pass

    assert breaker.state == CircuitState.OPEN

    frames = [_make_frame()]
    gen = await adapter.transcribe_stream(_frames_gen(frames), language="en")
    with pytest.raises(CircuitOpenError):
        async for _ in gen:
            pass
    model.transcribe.assert_called_once()  # the second call never reached the executor


# ---------------------------------------------------------------------------
# STTService
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stt_service_delegates_to_adapter() -> None:
    """STTService.transcribe_stream delegates to the underlying adapter."""
    scheduler = _make_fake_scheduler()
    model = _make_whisper_model([("test", 0.9, 0.0, 0.5)])
    adapter = WhisperAdapter(model=model, gpu_scheduler=scheduler)
    service = STTService.create(adapter, STTServiceConfig(default_language="en"))

    frames = [_make_frame()]
    gen = await service.transcribe_stream(_frames_gen(frames))
    results = [hyp async for hyp in gen]

    assert len(results) == 1
    assert results[0].word == "test"


@pytest.mark.asyncio
async def test_stt_service_uses_default_language_when_empty() -> None:
    """STTService falls back to configured default_language when caller passes ''."""
    scheduler = _make_fake_scheduler()
    model = _make_whisper_model()
    adapter = WhisperAdapter(model=model, gpu_scheduler=scheduler)
    config = STTServiceConfig(default_language="hi")
    service = STTService.create(adapter, config)

    frames = [_make_frame()]
    gen = await service.transcribe_stream(_frames_gen(frames), language="")
    async for _ in gen:
        pass

    call_kwargs = model.transcribe.call_args
    assert call_kwargs is not None
    assert call_kwargs.kwargs.get("language") == "hi"


# ---------------------------------------------------------------------------
# Word hypothesis fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_word_hypothesis_fields_correct() -> None:
    """WordHypothesis has correct word, confidence, start_ms, end_ms."""
    words = [("payment", 0.87, 0.5, 1.2)]
    model = _make_whisper_model(words)
    scheduler = _make_fake_scheduler()
    adapter = WhisperAdapter(model=model, gpu_scheduler=scheduler)

    gen = await adapter.transcribe_stream(_frames_gen([_make_frame()]), language="en")
    results = [hyp async for hyp in gen]

    assert results[0].word == "payment"
    assert abs(results[0].confidence - 0.87) < 0.01
    assert results[0].start_ms == 500
    assert results[0].end_ms == 1200
