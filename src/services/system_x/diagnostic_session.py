"""DiagnosticSession — bidirectional agentic investigation loop with Claude.

System X opens a tool-use session with Claude. Claude may call evidence tools
(query_metrics, query_logs, check_service_health, etc.) to gather information
before issuing a final diagnosis via finalize_diagnosis. System X is Claude's
secure runtime agent — Claude never touches production systems directly.

Architecture:
    1. System X builds initial incident package and sends it to Claude
    2. Claude calls evidence tools to gather more information
    3. System X executes each tool via EvidenceCollector (secrets-redacted)
    4. System X returns tool results to Claude
    5. Repeat until Claude calls finalize_diagnosis or MAX_TURNS reached
    6. System X archives the full conversation as part of the incident record
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from .evidence_collector import EvidenceCollector, TOOL_DEFINITIONS
from .models import ClaudeAnalysis, DiagnosticTurn

_log = logging.getLogger("system_x.diagnostic_session")

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-opus-4-7"
_MAX_TURNS = 8          # safety limit on back-and-forth turns
_SESSION_TIMEOUT_S = 120.0

_SYSTEM_PROMPT = """\
You are the System X diagnostic engine for VoiceOS, an enterprise AI voice platform.

You have been given an incident report. Your job is to:
1. Investigate the incident using the available evidence tools
2. Build a complete picture of the root cause
3. When you have enough information, call finalize_diagnosis

You have access to these tools:
- query_metrics: get Prometheus metrics for specific services
- query_logs: get recent log lines from services
- check_service_health: probe service health endpoints
- query_incident_history: look up past incidents for a service
- query_redis_info: get Redis operational stats

Investigation principles:
- Start by checking the health of affected services
- Then look at metrics (latency, error rates)
- Check logs for error messages
- Compare with historical incidents
- Build evidence before concluding

When you are confident in your diagnosis, call finalize_diagnosis with:
- root_cause: one clear technical sentence
- confidence: low | medium | high
- recovery_plan: ordered list of specific actionable steps
- recommended_actions: high-level guidance
- estimated_recovery_time_s: realistic integer seconds
- risk_assessment: what could go wrong during recovery

Security rules (NEVER violate):
- Never include passwords, secrets, tokens, API keys, or credentials in your responses
- Never suggest database schema changes or credential modifications
- Never suggest actions that could cause data loss
- If you see [REDACTED] in tool results, do not request that information
"""

# finalize_diagnosis tool definition — calling this ends the session
_FINALIZE_TOOL: dict[str, Any] = {
    "name": "finalize_diagnosis",
    "description": "Submit your final diagnosis and recovery plan. Call this when you have enough evidence.",
    "input_schema": {
        "type": "object",
        "properties": {
            "root_cause": {
                "type": "string",
                "description": "Concise technical root cause (one sentence)",
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Confidence in root cause assessment",
            },
            "recovery_plan": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ordered list of specific recovery steps",
            },
            "recommended_actions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "High-level recommended actions for operators",
            },
            "estimated_recovery_time_s": {
                "type": "integer",
                "description": "Estimated seconds to recovery",
            },
            "risk_assessment": {
                "type": "string",
                "description": "What could go wrong during recovery",
            },
        },
        "required": ["root_cause", "confidence", "recovery_plan", "recommended_actions",
                     "estimated_recovery_time_s", "risk_assessment"],
    },
}

_ALL_TOOLS = TOOL_DEFINITIONS + [_FINALIZE_TOOL]


class DiagnosticSession:
    """Runs one complete diagnostic investigation with Claude.

    Each incident gets its own session. The session is stateless between
    calls — all history is carried in the messages list.
    """

    def __init__(self, api_key: str, evidence_collector: EvidenceCollector) -> None:
        self._api_key = api_key
        self._evidence = evidence_collector
        self._conversation_id = str(uuid.uuid4())

    async def run(self, incident_package: dict) -> tuple[ClaudeAnalysis, list[DiagnosticTurn]]:
        """Run the diagnostic session. Returns (ClaudeAnalysis, turn history).

        Raises RuntimeError if no diagnosis was produced within MAX_TURNS.
        """
        messages: list[dict] = [
            {"role": "user", "content": json.dumps(incident_package, indent=2, default=str)}
        ]
        turns: list[DiagnosticTurn] = []
        finalized: dict | None = None
        model_used = _MODEL

        async with httpx.AsyncClient(timeout=_SESSION_TIMEOUT_S) as client:
            for turn_n in range(_MAX_TURNS):
                response_raw = await self._call_api(client, messages)
                model_used = response_raw.get("model", _MODEL)
                stop_reason = response_raw.get("stop_reason", "end_turn")
                content_blocks = response_raw.get("content", [])

                # Build assistant message
                messages.append({"role": "assistant", "content": content_blocks})

                tools_called: list[str] = []
                evidence_fetched: list[str] = []
                tool_results: list[dict] = []

                for block in content_blocks:
                    if block.get("type") != "tool_use":
                        continue
                    tool_name = block["name"]
                    tool_id = block["id"]
                    tool_input = block.get("input", {})
                    tools_called.append(tool_name)

                    if tool_name == "finalize_diagnosis":
                        finalized = tool_input
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": json.dumps({"status": "diagnosis_recorded"}),
                        })
                    else:
                        result = await self._evidence.execute(tool_name, tool_input)
                        evidence_fetched.append(tool_name)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": json.dumps(result, default=str),
                        })

                # Summarize this turn for the audit record
                text_blocks = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
                turns.append(DiagnosticTurn(
                    turn=turn_n,
                    role="assistant",
                    tools_called=tuple(tools_called),
                    evidence_fetched=tuple(evidence_fetched),
                    content_summary=(text_blocks[0][:300] if text_blocks else "(tool calls only)"),
                ))

                if finalized is not None:
                    _log.info("conversation=%s diagnosis finalized after %d turns", self._conversation_id, turn_n + 1)
                    break

                if stop_reason == "end_turn" and not tool_results:
                    # Claude finished without calling finalize — try to parse last text block
                    _log.warning("conversation=%s Claude ended without finalize_diagnosis", self._conversation_id)
                    finalized = self._extract_json_from_text(text_blocks)
                    break

                if tool_results:
                    messages.append({"role": "user", "content": tool_results})

        if finalized is None:
            raise RuntimeError(
                f"Claude diagnostic session ended after {_MAX_TURNS} turns without a finalize_diagnosis call"
            )

        analysis = ClaudeAnalysis(
            root_cause=str(finalized.get("root_cause", "Unknown")),
            confidence=str(finalized.get("confidence", "low")),
            recommended_actions=tuple(finalized.get("recommended_actions", [])),
            recovery_plan=tuple(finalized.get("recovery_plan", [])),
            estimated_recovery_time_s=int(finalized.get("estimated_recovery_time_s", 300)),
            risk_assessment=str(finalized.get("risk_assessment", "")),
            model=model_used,
            analyzed_at=datetime.now(UTC),
            conversation_id=self._conversation_id,
            turn_count=len(turns),
            evidence_keys=tuple(t for turn in turns for t in turn.evidence_fetched),
        )
        return analysis, turns

    async def _call_api(self, client: httpx.AsyncClient, messages: list[dict]) -> dict:
        response = await client.post(
            _ANTHROPIC_API_URL,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": _MODEL,
                "max_tokens": 2048,
                "system": [{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                "tools": _ALL_TOOLS,
                "messages": messages,
            },
        )
        response.raise_for_status()
        return response.json()

    def _extract_json_from_text(self, text_blocks: list[str]) -> dict | None:
        """Last-resort: attempt to parse finalize_diagnosis fields from raw text."""
        import re
        for text in text_blocks:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        return None


__all__ = ["DiagnosticSession"]
