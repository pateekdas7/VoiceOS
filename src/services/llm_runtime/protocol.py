"""LLMAdapter — abstract protocol for LLM inference adapters.

All LLM backends implement this protocol.  The rest of the system depends
only on LLMAdapter — never on a concrete model class.

Architecture: V1 Ch13 (LLM Runtime); DocSuite-02 (Interface Contracts).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from src.libs.contracts.response_plan import ResponsePlan
from src.libs.contracts.streaming import TokenChunk


@runtime_checkable
class LLMAdapter(Protocol):
    """Streaming LLM inference adapter interface.

    Implementations wrap a specific LLM backend (e.g. vLLM) and expose
    a uniform token-streaming contract.

    The adapter MUST:
    - Validate the prompt hash against PromptContract (RI-7) before submission.
    - Request VRAM from the GPU Scheduler before inference (V7 Ch6).
    - Accept a pre-built prompt string — it does NOT build prompts (PromptBuilder
      is Sprint-012).

    Architecture: V1 Ch13.
    """

    async def generate_stream(
        self,
        prompt: str,
        response_plan: ResponsePlan,
        max_tokens: int,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[TokenChunk]:
        """Generate a response, streaming tokens as they are produced.

        Args:
            prompt:        Pre-built prompt string from PromptBuilder.
            response_plan: The sealed ResponsePlan for this turn (for context
                           and RI-7 plan_id binding).
            max_tokens:    Maximum tokens to generate.
            cancel_event:  Optional asyncio.Event. When set by the caller
                           (stable-suffix orchestrator, on cancel-and-refire)
                           the adapter MUST stop iterating the upstream
                           token stream at the next yield boundary and
                           tear down any HTTP connection cleanly. When
                           None (default) the stream runs to completion
                           or exception, preserving pre-existing behavior.

        Yields:
            TokenChunk for each token.  The final chunk has ``finish_reason``
            set to ``'stop'`` or ``'length'``.
        """
        ...
