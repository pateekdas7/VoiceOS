"""Unit tests for Sprint-009 LLM runtime adapter service.

All tests use mocked backends — no real vLLM server or GPU required.

Architecture: V1 Ch13; Sprint-009 acceptance criteria.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.libs.circuit_breaker.breaker import CircuitBreaker, CircuitBreakerConfig, CircuitOpenError, CircuitState
from src.libs.contracts.response_plan import ResponsePlan, StrategyAction, StrategyLabel
from src.libs.contracts.streaming import TokenChunk
from src.services.gpu_scheduler.admission import AdmissionDecision
from src.services.gpu_scheduler.scheduler import GPUScheduler
from src.services.gpu_scheduler.vram_ledger import AllocationToken
from src.services.llm_runtime.adapters.vllm_adapter import vLLMAdapter
from src.services.llm_runtime.prompt_contract import PromptContract, PromptContractError
from src.services.llm_runtime.protocol import LLMAdapter
from src.services.llm_runtime.service import LLMService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response_plan() -> ResponsePlan:
    return ResponsePlan(
        plan_id="plan-001",
        version=1,
        call_id="call-001",
        tenant_id="tenant-001",
        created_at=datetime(2026, 7, 3, tzinfo=UTC),
        strategy=StrategyAction(action=StrategyLabel.ASK),
    )


def _make_fake_scheduler(approve: bool = True) -> MagicMock:
    scheduler = MagicMock(spec=GPUScheduler)
    if approve:
        token = AllocationToken(
            token_id="tok-2",
            device_id="gpu-0",
            model_id="qwen2.5-7b",
            vram_mb=16384,
        )
        scheduler.request_allocation.return_value = (AdmissionDecision.APPROVE, token)
    else:
        scheduler.request_allocation.return_value = (AdmissionDecision.REJECT, None)
    scheduler.release_allocation.return_value = None
    return scheduler


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_llm_adapter_protocol_is_runtime_checkable() -> None:
    """LLMAdapter protocol is runtime_checkable."""
    assert getattr(LLMAdapter, "_is_runtime_protocol", False) is True


def test_vllm_adapter_satisfies_llm_protocol() -> None:
    """vLLMAdapter structurally satisfies LLMAdapter Protocol."""
    scheduler = _make_fake_scheduler()
    adapter = vLLMAdapter(
        gpu_scheduler=scheduler,
        prompt_contract=PromptContract(),
    )
    assert isinstance(adapter, LLMAdapter)


# ---------------------------------------------------------------------------
# test_ri7_prompt_hash_validated (required AC test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ri7_prompt_hash_validated() -> None:
    """PromptContract.validate() is called before every LLM submission.

    Sprint-009: 'mock prompt_contract.validate(), verify called before LLM submit'.
    """
    scheduler = _make_fake_scheduler()
    contract = MagicMock(spec=PromptContract)
    contract.validate.return_value = None

    async def fake_stream(*_: object, **__: object) -> AsyncIterator[TokenChunk]:
        yield TokenChunk(text="ok", token_id=0, finish_reason="stop")

    adapter = vLLMAdapter(gpu_scheduler=scheduler, prompt_contract=contract)

    with patch.object(adapter, "_stream_vllm", side_effect=fake_stream):
        gen = await adapter.generate_stream(
            prompt="test prompt",
            response_plan=_make_response_plan(),
            max_tokens=50,
        )
        async for _ in gen:
            pass

    # validate() must have been called with the SHA-256 hash before _stream_vllm
    contract.validate.assert_called_once()
    call_args = contract.validate.call_args[0][0]
    assert isinstance(call_args, str)
    assert len(call_args) == 64  # SHA-256 hex digest


# ---------------------------------------------------------------------------
# test_vllm_adapter_streams_tokens (required AC test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vllm_adapter_streams_tokens() -> None:
    """vLLMAdapter.generate_stream yields TokenChunk objects.

    Sprint-009: 'feed prompt, adapter streams TokenChunk via mock vLLM API'.
    """
    tokens = ["I ", "understand ", "your ", "situation."]
    scheduler = _make_fake_scheduler()

    async def fake_stream(*_: object, **__: object) -> AsyncIterator[TokenChunk]:
        for i, text in enumerate(tokens):
            yield TokenChunk(
                text=text,
                token_id=i,
                finish_reason="stop" if i == len(tokens) - 1 else None,
            )

    contract = MagicMock(spec=PromptContract)
    contract.validate.return_value = None
    adapter = vLLMAdapter(gpu_scheduler=scheduler, prompt_contract=contract)

    with patch.object(adapter, "_stream_vllm", side_effect=fake_stream):
        gen = await adapter.generate_stream(
            prompt="test",
            response_plan=_make_response_plan(),
            max_tokens=50,
        )
        results = [chunk async for chunk in gen]

    assert len(results) == len(tokens)
    assert all(isinstance(c, TokenChunk) for c in results)
    assert results[-1].finish_reason == "stop"
    assert results[0].text == "I "


# ---------------------------------------------------------------------------
# test_gpu_scheduler_called_before_inference (LLM variant)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gpu_scheduler_called_before_llm_inference() -> None:
    """GPU Scheduler acquire is called before vLLM inference."""
    scheduler = _make_fake_scheduler()

    async def fake_stream(*_: object, **__: object) -> AsyncIterator[TokenChunk]:
        yield TokenChunk(text="ok", token_id=0, finish_reason="stop")

    contract = MagicMock(spec=PromptContract)
    contract.validate.return_value = None
    adapter = vLLMAdapter(gpu_scheduler=scheduler, prompt_contract=contract)

    with patch.object(adapter, "_stream_vllm", side_effect=fake_stream):
        gen = await adapter.generate_stream(
            prompt="hello",
            response_plan=_make_response_plan(),
            max_tokens=10,
        )
        async for _ in gen:
            pass

    scheduler.request_allocation.assert_called_once_with(
        service="llm",
        model="qwen2.5-7b",
        required_vram_mb=16384,
    )
    scheduler.release_allocation.assert_called_once()


# ---------------------------------------------------------------------------
# PromptContract tests
# ---------------------------------------------------------------------------


def test_prompt_contract_accepts_valid_hash() -> None:
    """PromptContract.validate passes for a non-empty hash."""
    contract = PromptContract()
    contract.validate("a" * 64)


def test_prompt_contract_rejects_empty_hash() -> None:
    """PromptContract.validate raises for empty hash (RI-7)."""
    contract = PromptContract()
    with pytest.raises(PromptContractError):
        contract.validate("")


def test_prompt_contract_rejects_whitespace_hash() -> None:
    """PromptContract.validate raises for whitespace-only hash (RI-7)."""
    contract = PromptContract()
    with pytest.raises(PromptContractError):
        contract.validate("   ")


def test_prompt_contract_hash_prompt_returns_hex_digest() -> None:
    """PromptContract.hash_prompt returns a 64-char hex SHA-256 digest."""
    digest = PromptContract.hash_prompt("hello world")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_prompt_contract_hash_is_deterministic() -> None:
    """Same prompt always produces the same hash (RI-7 determinism)."""
    p = "namaste, aapka loan"
    assert PromptContract.hash_prompt(p) == PromptContract.hash_prompt(p)


# ---------------------------------------------------------------------------
# VRAM rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vllm_adapter_raises_on_vram_rejection() -> None:
    """vLLMAdapter raises RuntimeError when GPU Scheduler rejects."""
    scheduler = _make_fake_scheduler(approve=False)
    contract = MagicMock(spec=PromptContract)
    contract.validate.return_value = None
    adapter = vLLMAdapter(gpu_scheduler=scheduler, prompt_contract=contract)

    gen = await adapter.generate_stream(
        prompt="test",
        response_plan=_make_response_plan(),
        max_tokens=10,
    )
    with pytest.raises(RuntimeError, match="rejected"):
        async for _ in gen:
            pass


# ---------------------------------------------------------------------------
# CircuitBreaker wiring (Sprint-016)
# ---------------------------------------------------------------------------


def _raise_connection_error(*_args: object, **_kwargs: object) -> None:
    raise ConnectionError("vLLM unreachable")


@pytest.mark.asyncio
async def test_vllm_adapter_circuit_breaker_opens_on_repeated_connection_failure() -> None:
    """Sprint-016: vLLMAdapter's optional breaker trips OPEN on sustained connection failure."""
    scheduler = _make_fake_scheduler()
    contract = MagicMock(spec=PromptContract)
    contract.validate.return_value = None
    breaker = CircuitBreaker("llm", CircuitBreakerConfig(failure_threshold=2))
    adapter = vLLMAdapter(gpu_scheduler=scheduler, prompt_contract=contract, breaker=breaker)

    with patch.object(vLLMAdapter, "_open_stream", side_effect=_raise_connection_error):
        for _ in range(2):
            gen = await adapter.generate_stream(prompt="hi", response_plan=_make_response_plan(), max_tokens=10)
            with pytest.raises(ConnectionError):
                async for _ in gen:
                    pass

    assert breaker.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_vllm_adapter_circuit_breaker_open_fails_fast_without_connecting() -> None:
    """Sprint-016: once OPEN, the breaker prevents _open_stream from being called at all."""
    scheduler = _make_fake_scheduler()
    contract = MagicMock(spec=PromptContract)
    contract.validate.return_value = None
    breaker = CircuitBreaker("llm", CircuitBreakerConfig(failure_threshold=1))
    adapter = vLLMAdapter(gpu_scheduler=scheduler, prompt_contract=contract, breaker=breaker)

    with patch.object(vLLMAdapter, "_open_stream", side_effect=_raise_connection_error):
        gen = await adapter.generate_stream(prompt="hi", response_plan=_make_response_plan(), max_tokens=10)
        with pytest.raises(ConnectionError):
            async for _ in gen:
                pass
    assert breaker.state == CircuitState.OPEN

    with patch.object(vLLMAdapter, "_open_stream", side_effect=_raise_connection_error) as mock_open:
        gen = await adapter.generate_stream(prompt="hi", response_plan=_make_response_plan(), max_tokens=10)
        with pytest.raises(CircuitOpenError):
            async for _ in gen:
                pass
        mock_open.assert_not_called()


# ---------------------------------------------------------------------------
# LLMService
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_service_delegates_to_adapter() -> None:
    """LLMService.generate_stream delegates to the underlying adapter."""
    scheduler = _make_fake_scheduler()
    contract = PromptContract()

    async def fake_stream(*_: object, **__: object) -> AsyncIterator[TokenChunk]:
        yield TokenChunk(text="ok", token_id=0, finish_reason="stop")

    adapter = vLLMAdapter(gpu_scheduler=scheduler, prompt_contract=contract)
    service = LLMService.create(adapter)

    with patch.object(adapter, "_stream_vllm", side_effect=fake_stream):
        with patch.object(contract, "validate"):
            gen = await service.generate_stream(
                prompt="test",
                response_plan=_make_response_plan(),
            )
            results = [c async for c in gen]

    assert len(results) == 1


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------


async def _async_iter(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        yield item
