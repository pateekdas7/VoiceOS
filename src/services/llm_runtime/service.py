"""LLMService — lifecycle manager for the LLM adapter.

Architecture: V1 Ch13; V7 Ch6.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from src.libs.contracts.response_plan import ResponsePlan
from src.libs.contracts.streaming import TokenChunk
from src.services.llm_runtime.protocol import LLMAdapter


@dataclass
class LLMServiceConfig:
    """Configuration for the LLM service."""

    model_name: str = "qwen2.5-7b"
    """Scheduler model identifier."""

    served_model_name: str = "qwen2.5-7b-instruct-fp8"
    """Model name as served by the inference server."""

    vram_mb: int = 16384
    """VRAM reservation per inference in MB."""

    base_url: str = "http://localhost:8000"
    """Base URL of the LLM inference server."""

    default_max_tokens: int = 150
    """Default max_tokens if caller does not specify."""


class LLMService:
    """Lifecycle-managed façade around an LLMAdapter.

    Architecture: V1 Ch13.
    """

    def __init__(self, adapter: LLMAdapter, config: LLMServiceConfig) -> None:
        self._adapter = adapter
        self._config = config

    @classmethod
    def create(cls, adapter: LLMAdapter, config: LLMServiceConfig | None = None) -> LLMService:
        """Factory: build an LLMService from an adapter."""
        return cls(adapter=adapter, config=config or LLMServiceConfig())

    async def generate_stream(
        self,
        prompt: str,
        response_plan: ResponsePlan,
        max_tokens: int = 0,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[TokenChunk]:
        """Generate tokens, delegating to the adapter.

        Args:
            prompt:        Pre-built prompt string.
            response_plan: Sealed ResponsePlan for this turn.
            max_tokens:    Token limit; uses config default if 0.
            cancel_event:  Optional asyncio.Event threaded through to the
                           adapter — when set mid-stream the SSE loop
                           exits at the next yield boundary (stable-suffix
                           orchestrator cancel-and-refire).

        Yields:
            TokenChunk objects from the adapter.
        """
        tokens = max_tokens or self._config.default_max_tokens
        return await self._adapter.generate_stream(
            prompt, response_plan, tokens, cancel_event=cancel_event
        )

    @property
    def adapter(self) -> LLMAdapter:
        """The underlying LLMAdapter."""
        return self._adapter

    @property
    def config(self) -> LLMServiceConfig:
        """The service configuration."""
        return self._config
