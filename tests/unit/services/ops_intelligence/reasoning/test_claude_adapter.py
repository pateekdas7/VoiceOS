"""Unit tests for ClaudeReasoningAdapter (ADR-006 Sec 3.2/3.3/13.11)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from src.services.ops_intelligence.models import ConfidenceLevel, InsightCategory, VerifiedFact
from src.services.ops_intelligence.reasoning.adapters.claude_adapter import ClaudeReasoningAdapter
from src.services.ops_intelligence.reasoning.adapters.protocol import NarrationRequest


class _FakeSecrets:
    def get_secret(self, path: str, actor_id: str = "system", tenant_id: str | None = None) -> str:
        return "fake-api-key"


class _FakeHTTP:
    def __init__(self, response_body: dict[str, object] | None = None, *, raises: Exception | None = None) -> None:
        self._response_body = response_body
        self._raises = raises
        self.last_call: dict[str, object] | None = None

    async def post_json(
        self, url: str, *, headers: dict[str, str], json_body: dict[str, object], timeout: float
    ) -> dict[str, object]:
        self.last_call = {"url": url, "headers": headers, "json_body": json_body}
        if self._raises is not None:
            raise self._raises
        assert self._response_body is not None
        return self._response_body


def _claude_response(payload: dict[str, object]) -> dict[str, object]:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def _request() -> NarrationRequest:
    return NarrationRequest(
        category=InsightCategory.REGRESSION,
        tenant_id=None,
        verified_facts=(
            VerifiedFact(
                claim="TTS first-clause latency p95 rose from 620ms to 910ms",
                source="prometheus",
                query="histogram_quantile(0.95, voiceos_tts_first_clause_latency_ms)",
                value="910",
                observed_at=datetime.now(UTC),
            ),
        ),
        candidate_affected_components=("tts",),
    )


class TestSuccessfulNarration:
    @pytest.mark.asyncio
    async def test_parses_well_formed_response(self) -> None:
        http = _FakeHTTP(
            _claude_response(
                {
                    "hypotheses": [
                        {"claim": "GPU thermal throttling", "reasoning": "matches known TT-025 pattern", "confidence": "medium"}
                    ],
                    "recommendation": "Check GPU temperature telemetry",
                    "confidence_level": "medium",
                    "narrative": "TTS latency regressed, likely thermal throttling.",
                }
            )
        )
        adapter = ClaudeReasoningAdapter(_FakeSecrets(), http)
        result = await adapter.narrate(_request())

        assert result.confidence_level == ConfidenceLevel.MEDIUM
        assert len(result.hypotheses) == 1
        assert result.hypotheses[0].claim == "GPU thermal throttling"
        assert result.recommendation == "Check GPU temperature telemetry"
        assert "thermal throttling" in result.narrative

    @pytest.mark.asyncio
    async def test_sends_api_key_and_evidence_in_request(self) -> None:
        http = _FakeHTTP(_claude_response({"hypotheses": [], "recommendation": None, "confidence_level": "low", "narrative": "x"}))
        adapter = ClaudeReasoningAdapter(_FakeSecrets(), http)
        await adapter.narrate(_request())

        assert http.last_call is not None
        assert http.last_call["headers"]["x-api-key"] == "fake-api-key"
        user_message = http.last_call["json_body"]["messages"][0]["content"]
        assert "voiceos_tts_first_clause_latency_ms" in user_message

    @pytest.mark.asyncio
    async def test_null_recommendation_is_preserved(self) -> None:
        http = _FakeHTTP(_claude_response({"hypotheses": [], "recommendation": None, "confidence_level": "low", "narrative": "no issue"}))
        adapter = ClaudeReasoningAdapter(_FakeSecrets(), http)
        result = await adapter.narrate(_request())
        assert result.recommendation is None


class TestFailureModes:
    """ADR-006 Sec 13.11 -- reasoning model unavailable / malformed response never crashes the analysis pass."""

    @pytest.mark.asyncio
    async def test_http_error_degrades_to_empty_result(self) -> None:
        http = _FakeHTTP(raises=ConnectionError("model unavailable"))
        adapter = ClaudeReasoningAdapter(_FakeSecrets(), http)
        result = await adapter.narrate(_request())

        assert result.confidence_level == ConfidenceLevel.LOW
        assert result.hypotheses == ()
        assert result.recommendation is None
        assert "unavailable" in result.narrative or "Reasoning model unavailable" in result.narrative

    @pytest.mark.asyncio
    async def test_non_json_response_degrades_to_empty_result(self) -> None:
        http = _FakeHTTP({"content": [{"type": "text", "text": "not json at all"}]})
        adapter = ClaudeReasoningAdapter(_FakeSecrets(), http)
        result = await adapter.narrate(_request())
        assert result.hypotheses == ()
        assert result.confidence_level == ConfidenceLevel.LOW

    @pytest.mark.asyncio
    async def test_missing_required_field_degrades_to_empty_result(self) -> None:
        http = _FakeHTTP(_claude_response({"hypotheses": []}))  # missing confidence_level/narrative
        adapter = ClaudeReasoningAdapter(_FakeSecrets(), http)
        result = await adapter.narrate(_request())
        assert result.hypotheses == ()
        assert result.confidence_level == ConfidenceLevel.LOW

    @pytest.mark.asyncio
    async def test_invalid_confidence_value_degrades_to_empty_result(self) -> None:
        http = _FakeHTTP(
            _claude_response({"hypotheses": [], "recommendation": None, "confidence_level": "extremely-sure", "narrative": "x"})
        )
        adapter = ClaudeReasoningAdapter(_FakeSecrets(), http)
        result = await adapter.narrate(_request())
        assert result.confidence_level == ConfidenceLevel.LOW
        assert result.hypotheses == ()

    @pytest.mark.asyncio
    async def test_empty_content_list_degrades_to_empty_result(self) -> None:
        http = _FakeHTTP({"content": []})
        adapter = ClaudeReasoningAdapter(_FakeSecrets(), http)
        result = await adapter.narrate(_request())
        assert result.hypotheses == ()
