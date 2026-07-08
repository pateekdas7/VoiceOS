"""Unit tests for Sprint-009 TTS adapter service and ClauseSplitter.

All tests use mocked backends — no real Veena server or GPU required.

Streaming adapter (ADR-001): tests patch _stream_clause (replaces _call_veena)
since the adapter now yields AudioClause objects per 85ms chunk rather than
per buffered full clause.

Architecture: V1 Ch15-17; Sprint-009 acceptance criteria.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from src.libs.circuit_breaker.breaker import CircuitBreaker, CircuitBreakerConfig, CircuitOpenError, CircuitState
from src.libs.contracts.streaming import AudioClause, VoiceConfig
from src.services.gpu_scheduler.admission import AdmissionDecision
from src.services.gpu_scheduler.scheduler import GPUScheduler
from src.services.gpu_scheduler.vram_ledger import AllocationToken
from src.services.tts.adapters.veena_adapter import VeenaAdapter
from src.services.tts.clause_splitter import ClauseSplitter
from src.services.tts.protocol import TTSAdapter
from src.services.tts.service import TTSService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_voice_config() -> VoiceConfig:
    return VoiceConfig(
        pitch_shift=0.0,
        rate_scale=1.0,
        energy_scale=1.0,
        pause_ms_after_clause=150,
        language="hi-IN",
    )


def _make_fake_scheduler(approve: bool = True) -> GPUScheduler:
    scheduler = MagicMock(spec=GPUScheduler)
    if approve:
        token = AllocationToken(
            token_id="tok-3",
            device_id="gpu-0",
            model_id="veena",
            vram_mb=2048,
        )
        scheduler.request_allocation.return_value = (AdmissionDecision.APPROVE, token)
    else:
        scheduler.request_allocation.return_value = (AdmissionDecision.REJECT, None)
    scheduler.release_allocation.return_value = None
    return scheduler


async def _text_gen(texts: list[str]) -> AsyncIterator[str]:
    for t in texts:
        yield t


def _mock_stream_clause(
    fake_audio: bytes,
    clause_idx: int = 0,
    is_final: bool = True,
) -> Callable[..., AsyncIterator[AudioClause]]:
    """Return an async generator function that yields a single AudioClause with fake_audio."""

    async def _gen(
        text: str,
        voice_config: VoiceConfig,
        clause_idx: int,
        is_final_clause: bool,
    ) -> AsyncIterator[AudioClause]:
        yield AudioClause(
            audio_data=fake_audio,
            sample_rate=24000,
            text=text,
            clause_index=clause_idx,
            is_final=is_final_clause,
        )

    return _gen


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_tts_adapter_protocol_is_runtime_checkable() -> None:
    """TTSAdapter protocol is runtime_checkable."""
    assert getattr(TTSAdapter, "_is_runtime_protocol", False) is True


def test_veena_adapter_satisfies_tts_protocol() -> None:
    """VeenaAdapter structurally satisfies TTSAdapter Protocol."""
    scheduler = _make_fake_scheduler()
    adapter = VeenaAdapter(gpu_scheduler=scheduler)
    assert isinstance(adapter, TTSAdapter)


# ---------------------------------------------------------------------------
# ClauseSplitter — boundary detection
# ---------------------------------------------------------------------------


def test_clause_splitter_english_comma() -> None:
    """ClauseSplitter splits English text into 3 clauses on comma boundaries.

    Sprint-009: 'clause splitter splits on boundary characters'.
    Input: "Well, we understand your situation, and we'd like to help."
    Expected: 3 clauses (2 from feed, 1 from flush).
    """
    splitter = ClauseSplitter()
    text = "Well, we understand your situation, and we'd like to help."
    clauses = splitter.feed(text)
    remainder = splitter.flush()

    all_clauses = clauses[:]
    if remainder:
        all_clauses.append(remainder)

    assert len(all_clauses) == 3, f"Expected 3 clauses, got {len(all_clauses)}: {all_clauses}"


def test_clause_splitter_hindi_boundary() -> None:
    """ClauseSplitter splits Hindi text on Devanagari full stop (।).

    Sprint-009: 'clause splitter splits on boundary characters'.
    Input: "आपका बकाया राशि है। क्या आप भुगतान कर सकते हैं?"
    Expected: 2 clauses (1 from feed, 1 from flush).
    """
    splitter = ClauseSplitter()
    text = "आपका बकाया राशि है। क्या आप भुगतान कर सकते हैं?"
    clauses = splitter.feed(text)
    remainder = splitter.flush()

    all_clauses = clauses[:]
    if remainder:
        all_clauses.append(remainder)

    assert len(all_clauses) == 2, f"Expected 2 clauses, got {len(all_clauses)}: {all_clauses}"


def test_clause_splitter_strong_boundary_period() -> None:
    """ClauseSplitter splits on '. ' boundary."""
    splitter = ClauseSplitter()
    clauses = splitter.feed("First sentence. Second sentence.")
    remainder = splitter.flush()
    all_clauses = clauses + ([remainder] if remainder else [])
    assert len(all_clauses) == 2
    assert "First sentence" in all_clauses[0]


def test_clause_splitter_strong_boundary_question() -> None:
    """ClauseSplitter splits on '? ' boundary."""
    splitter = ClauseSplitter()
    clauses = splitter.feed("Are you there? I wanted to ask.")
    remainder = splitter.flush()
    all_clauses = clauses + ([remainder] if remainder else [])
    assert len(all_clauses) == 2


def test_clause_splitter_flush_returns_none_when_empty() -> None:
    """ClauseSplitter.flush() returns None when buffer is empty."""
    splitter = ClauseSplitter()
    result = splitter.flush()
    assert result is None


def test_clause_splitter_reset_clears_buffer() -> None:
    """ClauseSplitter.reset() discards buffered content."""
    splitter = ClauseSplitter()
    splitter.feed("Partial text without boundary")
    splitter.reset()
    assert splitter.flush() is None


def test_clause_splitter_incremental_feed() -> None:
    """ClauseSplitter works across multiple incremental feed() calls."""
    splitter = ClauseSplitter()
    clauses: list[str] = []
    clauses += splitter.feed("Hello, ")
    clauses += splitter.feed("world. How are you?")
    remainder = splitter.flush()
    if remainder:
        clauses.append(remainder)
    assert len(clauses) >= 2


# ---------------------------------------------------------------------------
# test_veena_adapter_streams_clauses (required AC test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_veena_adapter_streams_clauses() -> None:
    """VeenaAdapter.synthesize_stream yields AudioClause objects.

    Sprint-009: 'text chunks stream, adapter yields AudioClause per clause'.
    Patches _stream_clause (streaming adapter, ADR-001).
    """
    fake_audio = b"\x00\x01" * 1024

    scheduler = _make_fake_scheduler()
    adapter = VeenaAdapter(gpu_scheduler=scheduler)

    with patch.object(adapter, "_stream_clause", side_effect=_mock_stream_clause(fake_audio)):
        text_chunks: AsyncIterator[str] = _text_gen(["I understand ", "your concern. ", "We can help you."])
        gen = await adapter.synthesize_stream(text_chunks, _make_voice_config())
        results: list[AudioClause] = []
        async for clause in gen:
            results.append(clause)

    assert len(results) >= 1
    assert all(isinstance(c, AudioClause) for c in results)
    assert results[-1].is_final is True
    assert all(c.sample_rate == 24000 for c in results)
    assert all(len(c.audio_data) > 0 for c in results)


# ---------------------------------------------------------------------------
# test_gpu_scheduler_called_before_inference (TTS variant) — required AC test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gpu_scheduler_called_before_tts_inference() -> None:
    """GPU Scheduler acquire is called before Veena synthesis."""
    scheduler = _make_fake_scheduler()
    adapter = VeenaAdapter(gpu_scheduler=scheduler)

    with patch.object(adapter, "_stream_clause", side_effect=_mock_stream_clause(b"\x00" * 256)):
        text_chunks: AsyncIterator[str] = _text_gen(["Test clause."])
        gen = await adapter.synthesize_stream(text_chunks, _make_voice_config())
        async for _ in gen:
            pass

    cast(Any, scheduler.request_allocation).assert_called_once_with(
        service="tts",
        model="veena",
        required_vram_mb=2048,
    )
    cast(Any, scheduler.release_allocation).assert_called_once()


# ---------------------------------------------------------------------------
# VRAM rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_veena_adapter_raises_on_vram_rejection() -> None:
    """VeenaAdapter raises RuntimeError when GPU Scheduler rejects."""
    scheduler = _make_fake_scheduler(approve=False)
    adapter = VeenaAdapter(gpu_scheduler=scheduler)

    text_chunks: AsyncIterator[str] = _text_gen(["test"])
    gen = await adapter.synthesize_stream(text_chunks, _make_voice_config())

    with pytest.raises(RuntimeError, match="rejected"):
        async for _ in gen:
            pass


# ---------------------------------------------------------------------------
# AudioClause fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audio_clause_fields_correct() -> None:
    """AudioClause has correct audio_data, sample_rate, text, clause_index, is_final."""
    scheduler = _make_fake_scheduler()
    adapter = VeenaAdapter(gpu_scheduler=scheduler)

    with patch.object(adapter, "_stream_clause", side_effect=_mock_stream_clause(b"\xab\xcd" * 100)):
        text_chunks: AsyncIterator[str] = _text_gen(["Namaste!"])
        gen = await adapter.synthesize_stream(text_chunks, _make_voice_config())
        results = [c async for c in gen]

    assert results[0].clause_index == 0
    assert results[-1].is_final is True
    assert results[0].sample_rate == 24000
    assert isinstance(results[0].audio_data, bytes)
    assert len(results[0].audio_data) > 0


# ---------------------------------------------------------------------------
# Streaming: multiple chunks per clause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_veena_adapter_yields_multiple_chunks_per_clause() -> None:
    """VeenaAdapter yields multiple AudioClause chunks per clause (85ms each).

    With streaming synthesis, a single clause produces multiple AudioClause
    objects (one per 85.33ms chunk from the server).
    """
    fake_chunk_1 = b"\x01" * 8192  # 85ms chunk
    fake_chunk_2 = b"\x02" * 8192  # 85ms chunk
    fake_chunk_3 = b"\x03" * 4096  # partial final chunk (silence + remainder)

    async def _multi_chunk_stream(
        text: str,
        voice_config: VoiceConfig,
        clause_idx: int,
        is_final_clause: bool,
    ) -> AsyncIterator[AudioClause]:
        for i, data in enumerate([fake_chunk_1, fake_chunk_2, fake_chunk_3]):
            yield AudioClause(
                audio_data=data,
                sample_rate=24000,
                text=text,
                clause_index=clause_idx,
                is_final=is_final_clause and i == 2,
            )

    scheduler = _make_fake_scheduler()
    adapter = VeenaAdapter(gpu_scheduler=scheduler)

    with patch.object(adapter, "_stream_clause", side_effect=_multi_chunk_stream):
        text_chunks: AsyncIterator[str] = _text_gen(["Hello there."])
        gen = await adapter.synthesize_stream(text_chunks, _make_voice_config())
        results = [c async for c in gen]

    assert len(results) == 3
    assert results[0].audio_data == fake_chunk_1
    assert results[1].audio_data == fake_chunk_2
    assert results[2].audio_data == fake_chunk_3
    assert results[0].is_final is False
    assert results[1].is_final is False
    assert results[2].is_final is True


# ---------------------------------------------------------------------------
# CircuitBreaker wiring (Sprint-016)
# ---------------------------------------------------------------------------


def _raise_connection_error(*_args: object, **_kwargs: object) -> None:
    raise ConnectionError("Veena unreachable")


@pytest.mark.asyncio
async def test_veena_adapter_circuit_breaker_opens_on_repeated_connection_failure() -> None:
    """Sprint-016: VeenaAdapter's optional breaker trips OPEN on sustained connection failure."""
    scheduler = _make_fake_scheduler()
    breaker = CircuitBreaker("tts", CircuitBreakerConfig(failure_threshold=1))
    adapter = VeenaAdapter(gpu_scheduler=scheduler, breaker=breaker)

    with patch.object(VeenaAdapter, "_open_stream", side_effect=_raise_connection_error):
        text_chunks: AsyncIterator[str] = _text_gen(["Hello world."])
        gen = await adapter.synthesize_stream(text_chunks, _make_voice_config())
        with pytest.raises(ConnectionError):
            async for _ in gen:
                pass

    assert breaker.state == CircuitState.OPEN

    with patch.object(VeenaAdapter, "_open_stream", side_effect=_raise_connection_error) as mock_open:
        text_chunks = _text_gen(["Hello again."])
        gen = await adapter.synthesize_stream(text_chunks, _make_voice_config())
        with pytest.raises(CircuitOpenError):
            async for _ in gen:
                pass
        mock_open.assert_not_called()


# ---------------------------------------------------------------------------
# TTSService
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tts_service_delegates_to_adapter() -> None:
    """TTSService.synthesize_stream delegates to the underlying adapter."""
    scheduler = _make_fake_scheduler()
    adapter = VeenaAdapter(gpu_scheduler=scheduler)
    service = TTSService.create(adapter)

    with patch.object(adapter, "_stream_clause", side_effect=_mock_stream_clause(b"\x00" * 256)):
        text_chunks: AsyncIterator[str] = _text_gen(["Hello world."])
        gen = await service.synthesize_stream(text_chunks, _make_voice_config())
        results = [c async for c in gen]

    assert len(results) >= 1
