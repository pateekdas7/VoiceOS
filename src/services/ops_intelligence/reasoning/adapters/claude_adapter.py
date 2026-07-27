"""ClaudeReasoningAdapter -- concrete ReasoningAdapter calling the Claude Messages API.

Credentials are fetched through the existing ``SecretsManager`` provider
abstraction (ADR-006 Sec 3.3) -- no exception carved out to the "no secret
from env, runtime injection only" rule every other credential in this repo
follows. Uses prompt caching (Anthropic's ``cache_control`` block) for the
large, mostly-static system prompt describing the metric catalog/governance
contract, since analysis runs are frequent and that context is repetitive.

If the model's response cannot be parsed as the expected structured JSON,
this adapter NEVER fabricates a result -- it returns a low-confidence,
empty-hypothesis :class:`NarrationResult` whose narrative states the parse
failure. A malformed model turn degrades to "no opinion," never to a
silently-wrong opinion.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from src.services.ops_intelligence.models import ConfidenceLevel, Hypothesis

from .protocol import NarrationRequest, NarrationResult

_logger = logging.getLogger(__name__)

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_SECRET_PATH = "ops_intelligence/anthropic_api_key"
ANTHROPIC_API_VERSION = "2023-06-01"

_SYSTEM_PROMPT = """You are the VoiceOS Intelligent Analysis Layer's reasoning component.

You are given a closed set of verified_facts (real metric/log/trace/event
citations already retrieved by deterministic code). You may NOT invent
additional facts, and you may NOT claim something is a verified_fact -- your
entire output is your own inference, always labeled as hypotheses.

Respond with ONLY a JSON object of this exact shape, nothing else:
{
  "hypotheses": [{"claim": str, "reasoning": str, "confidence": "low"|"medium"|"high"}],
  "recommendation": str or null,
  "confidence_level": "low"|"medium"|"high",
  "narrative": str
}
"""


class SecretFetchPort(Protocol):
    def get_secret(self, path: str, actor_id: str = "system", tenant_id: str | None = None) -> str: ...


class HTTPPostPort(Protocol):
    """Injected HTTP client port -- production uses httpx, tests inject a fake."""

    async def post_json(self, url: str, *, headers: dict[str, str], json_body: dict[str, object], timeout: float) -> dict[str, object]: ...


class HttpxPostAdapter:
    """Production ``HTTPPostPort`` backed by httpx (lazy import, mirrors vLLMAdapter)."""

    async def post_json(
        self, url: str, *, headers: dict[str, str], json_body: dict[str, object], timeout: float
    ) -> dict[str, object]:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=json_body)
            response.raise_for_status()
            result: dict[str, object] = response.json()
            return result


class ClaudeReasoningAdapter:
    """Concrete :class:`ReasoningAdapter` calling Anthropic's Messages API."""

    def __init__(
        self,
        secrets: SecretFetchPort,
        http_client: HTTPPostPort | None = None,
        *,
        base_url: str = DEFAULT_ANTHROPIC_BASE_URL,
        model: str = DEFAULT_MODEL,
        secret_path: str = DEFAULT_SECRET_PATH,
        prompt_version: str = "v1",
        max_tokens: int = 1024,
        timeout: float = 30.0,
    ) -> None:
        self._secrets = secrets
        self._http = http_client or HttpxPostAdapter()
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._secret_path = secret_path
        self._prompt_version = prompt_version
        self._max_tokens = max_tokens
        self._timeout = timeout

    async def narrate(self, request: NarrationRequest) -> NarrationResult:
        api_key = self._secrets.get_secret(self._secret_path, actor_id="ops_intelligence", tenant_id=request.tenant_id)
        user_content = _render_evidence(request)

        body: dict[str, object] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": [{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user_content}],
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }

        try:
            response = await self._http.post_json(
                f"{self._base_url}/v1/messages", headers=headers, json_body=body, timeout=self._timeout
            )
            return self._parse_response(response)
        except Exception as exc:
            _logger.warning("ClaudeReasoningAdapter.narrate failed: %s", exc)
            return _empty_result(self._model, self._prompt_version, reason=str(exc))

    def _parse_response(self, response: dict[str, object]) -> NarrationResult:
        content = response.get("content", [])
        text = ""
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                text = str(first.get("text", ""))

        usage = response.get("usage", {})
        total_tokens = 0
        if isinstance(usage, dict):
            total_tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))

        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return _empty_result(self._model, self._prompt_version, reason="model response was not valid JSON")

        try:
            hypotheses = tuple(
                Hypothesis(
                    claim=str(h["claim"]),
                    reasoning=str(h["reasoning"]),
                    confidence=ConfidenceLevel(str(h["confidence"])),
                )
                for h in parsed.get("hypotheses", [])
            )
            confidence_level = ConfidenceLevel(str(parsed["confidence_level"]))
            narrative = str(parsed["narrative"])
            recommendation_raw = parsed.get("recommendation")
            recommendation = str(recommendation_raw) if recommendation_raw else None
        except (KeyError, ValueError) as exc:
            return _empty_result(self._model, self._prompt_version, reason=f"model response missing/invalid field: {exc}")

        return NarrationResult(
            hypotheses=hypotheses,
            recommendation=recommendation,
            confidence_level=confidence_level,
            narrative=narrative,
            model=self._model,
            prompt_version=self._prompt_version,
            total_tokens=total_tokens,
        )


def _render_evidence(request: NarrationRequest) -> str:
    lines = [f"category: {request.category.value}", f"tenant_id: {request.tenant_id or '(platform-wide)'}", "verified_facts:"]
    for fact in request.verified_facts:
        lines.append(f"  - [{fact.source}] {fact.claim} (query: {fact.query}) = {fact.value} @ {fact.observed_at.isoformat()}")
    if request.candidate_affected_components:
        lines.append(f"candidate_affected_components: {', '.join(request.candidate_affected_components)}")
    return "\n".join(lines)


def _empty_result(model: str, prompt_version: str, *, reason: str) -> NarrationResult:
    return NarrationResult(
        hypotheses=(),
        recommendation=None,
        confidence_level=ConfidenceLevel.LOW,
        narrative=f"Reasoning model unavailable or returned an unparseable response ({reason}). "
        "Only the deterministically-gathered verified_facts are available for this insight.",
        model=model,
        prompt_version=prompt_version,
    )


__all__ = ["DEFAULT_MODEL", "DEFAULT_SECRET_PATH", "ClaudeReasoningAdapter", "HTTPPostPort", "HttpxPostAdapter", "SecretFetchPort"]
