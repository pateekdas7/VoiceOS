"""vLLMAdapter — LLM adapter using vLLM's OpenAI-compatible streaming API.

Connects to a vLLM server and streams TokenChunk objects via Server-Sent
Events (SSE).  The adapter never builds prompts — it receives a pre-built
prompt string from the PromptBuilder (Sprint-012).

RI-7: The prompt hash is validated before every submission.
VRAM: Requests 16,384 MB from GPU Scheduler before inference.

Model: Qwen2.5-7B-Instruct-FP8-dynamic (RedHatAI), served via vLLM with
  --dtype auto --gpu-memory-utilization 0.55 --served-model-name qwen2.5-7b-instruct-fp8.

Architecture: V1 Ch13 (LLM Runtime); V7 Ch6 (GPU fleet).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING

from src.libs.circuit_breaker.breaker import CircuitBreaker
from src.libs.contracts.response_plan import ResponsePlan
from src.libs.contracts.streaming import TokenChunk
from src.services.gpu_scheduler.admission import AdmissionDecision
from src.services.gpu_scheduler.scheduler import GPUScheduler
from src.services.llm_runtime.prompt_contract import PromptContract

if TYPE_CHECKING:
    import httpx

_QWEN_MODEL_NAME = "qwen2.5-7b"
_QWEN_VRAM_MB = 16384
_DEFAULT_BASE_URL = "http://localhost:8000"
_DEFAULT_SERVED_MODEL = "qwen2.5-7b-instruct-fp8"


class vLLMAdapter:  # noqa: N801
    """LLM adapter streaming tokens from a vLLM OpenAI-compatible server.

    Architecture: V1 Ch13.
    """

    def __init__(
        self,
        gpu_scheduler: GPUScheduler,
        prompt_contract: PromptContract,
        base_url: str = _DEFAULT_BASE_URL,
        served_model_name: str = _DEFAULT_SERVED_MODEL,
        vram_mb: int = _QWEN_VRAM_MB,
        model_name: str = _QWEN_MODEL_NAME,
        temperature: float = 0.3,
        top_p: float = 0.9,
        timeout: float = 60.0,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._gpu_scheduler = gpu_scheduler
        self._prompt_contract = prompt_contract
        self._base_url = base_url.rstrip("/")
        self._served_model_name = served_model_name
        self._vram_mb = vram_mb
        self._model_name = model_name
        self._temperature = temperature
        self._top_p = top_p
        self._timeout = timeout
        # Optional CircuitBreaker guarding the vLLM HTTP connection (Sprint-016,
        # V3 Ch14 §14.2). None (default) preserves pre-Sprint-016 behavior.
        self._breaker = breaker

    async def generate_stream(
        self,
        prompt: str,
        response_plan: ResponsePlan,
        max_tokens: int,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[TokenChunk]:
        """Stream tokens from vLLM, enforcing RI-7 and GPU Scheduler.

        When cancel_event is set mid-stream, the SSE loop exits at the next
        yield boundary and the HTTP response is closed cleanly — vLLM's
        server-side aborts the generation on client disconnect, so no
        server-side cancel API is required.
        """
        return self._generate_gen(prompt, response_plan, max_tokens, cancel_event)

    async def _generate_gen(
        self,
        prompt: str,
        response_plan: ResponsePlan,
        max_tokens: int,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[TokenChunk]:
        from src.services.llm_runtime.metrics import (
            llm_completion_latency_ms,
            llm_prompt_hash_validations_total,
            llm_requests_total,
            llm_ttft_ms,
        )

        # ── RI-7: validate prompt hash before submission ──────────────────────
        prompt_hash = PromptContract.hash_prompt(prompt)
        try:
            self._prompt_contract.validate(prompt_hash)
            llm_prompt_hash_validations_total.labels(outcome="pass").inc()
        except Exception:
            llm_prompt_hash_validations_total.labels(outcome="fail").inc()
            llm_requests_total.labels(status="ri7_violation").inc()
            raise

        # ── GPU Scheduler VRAM acquisition ────────────────────────────────────
        decision, token = self._gpu_scheduler.request_allocation(
            service="llm",
            model=self._model_name,
            required_vram_mb=self._vram_mb,
        )
        if decision != AdmissionDecision.APPROVE or token is None:
            llm_requests_total.labels(status="rejected").inc()
            raise RuntimeError(f"GPU Scheduler rejected VRAM for {self._model_name} ({self._vram_mb} MB)")

        try:
            async for chunk in self._stream_vllm(prompt, max_tokens, llm_ttft_ms, llm_completion_latency_ms, cancel_event):
                yield chunk
            llm_requests_total.labels(status="success").inc()
        except Exception:
            llm_requests_total.labels(status="error").inc()
            raise
        finally:
            self._gpu_scheduler.release_allocation(token)

    async def _stream_vllm(
        self,
        prompt: str,
        max_tokens: int,
        ttft_histogram: object,
        completion_histogram: object,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[TokenChunk]:
        import httpx

        payload = {
            "model": self._served_model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "stream": True,
        }

        url = f"{self._base_url}/v1/chat/completions"
        start = time.monotonic()
        first_token = True

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            # The circuit breaker guards only connection establishment (V3 Ch14
            # §14.9) — once the response stream is open, it flows unguarded so
            # true token-by-token streaming (and the TTFT budget, V1 Ch23) is
            # preserved; a breaker wrapping the entire generator would force
            # buffering the whole response, which is not acceptable here.
            if self._breaker is not None:
                resp = await self._breaker.call(self._open_stream, client, url, payload)
            else:
                resp = await self._open_stream(client, url, payload)
            try:
                async for line in resp.aiter_lines():
                    # Cancel-token check BEFORE processing each SSE line —
                    # if the orchestrator flipped the event while we were
                    # blocked awaiting the next chunk, bail out immediately.
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choice = obj.get("choices", [{}])[0]
                    delta = choice.get("delta", {})
                    text = delta.get("content") or ""
                    finish_reason: str | None = choice.get("finish_reason")

                    if text or finish_reason:
                        if first_token and text:
                            ttft_ms_val = (time.monotonic() - start) * 1000
                            if hasattr(ttft_histogram, "observe"):
                                ttft_histogram.observe(ttft_ms_val)
                            first_token = False

                        yield TokenChunk(
                            text=text,
                            token_id=0,
                            finish_reason=finish_reason,
                        )
                        # Cancel-token check AFTER yielding — if the
                        # consumer set the event synchronously during the
                        # yield (e.g. downstream noticed the prefix
                        # extended materially), stop before draining the
                        # next SSE line.
                        if cancel_event is not None and cancel_event.is_set():
                            break
            finally:
                # aclose() ends the HTTP stream cleanly; vLLM's server
                # aborts the in-flight generation on client disconnect, so
                # no explicit server-side cancel RPC is required.
                await resp.aclose()

        elapsed = (time.monotonic() - start) * 1000
        if hasattr(completion_histogram, "observe"):
            completion_histogram.observe(elapsed)

    @staticmethod
    async def _open_stream(client: httpx.AsyncClient, url: str, payload: Mapping[str, object]) -> httpx.Response:
        """Open the streaming POST to vLLM and validate the response status.

        This is the unit the circuit breaker wraps: a connection failure or
        non-2xx status counts as a breaker failure, while the subsequent
        token stream itself is not breaker-guarded (see :meth:`_stream_vllm`).
        """
        request = client.build_request("POST", url, json=payload)
        response = await client.send(request, stream=True)
        response.raise_for_status()
        return response
