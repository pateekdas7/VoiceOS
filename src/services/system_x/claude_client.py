"""SystemXClaudeClient — runs bidirectional diagnostic sessions with Claude.

Wraps DiagnosticSession to provide a clean async interface used by the
controller. All investigation is iterative: Claude calls evidence tools,
System X fetches data, Claude continues until it issues finalize_diagnosis.

Validation runs before the ClaudeAnalysis is returned to the controller —
this is the last firewall before the Policy Engine sees the result.
"""
from __future__ import annotations

import logging

from .diagnostic_session import DiagnosticSession
from .evidence_collector import EvidenceCollector
from .models import ClaudeAnalysis, DiagnosticTurn
from .validator import ClaudeResponseValidator

_log = logging.getLogger("system_x.claude_client")


class SystemXClaudeClient:
    def __init__(
        self,
        api_key: str,
        evidence_collector: EvidenceCollector,
        validator: ClaudeResponseValidator | None = None,
    ) -> None:
        self._api_key = api_key
        self._evidence = evidence_collector
        self._validator = validator or ClaudeResponseValidator()

    async def analyze_incident(
        self, package: dict
    ) -> tuple[ClaudeAnalysis, list[DiagnosticTurn]]:
        """Run a full diagnostic session. Returns (ClaudeAnalysis, turn_history).

        Raises ValueError if validation fails after the session completes.
        """
        session = DiagnosticSession(self._api_key, self._evidence)
        analysis, turns = await session.run(package)

        # Validate the analysis produced by Claude before returning
        raw_dict = {
            "root_cause": analysis.root_cause,
            "confidence": analysis.confidence,
            "recommended_actions": list(analysis.recommended_actions),
            "recovery_plan": list(analysis.recovery_plan),
            "estimated_recovery_time_s": analysis.estimated_recovery_time_s,
            "risk_assessment": analysis.risk_assessment,
            "model": analysis.model,
            "conversation_id": analysis.conversation_id,
            "turn_count": analysis.turn_count,
            "evidence_keys": list(analysis.evidence_keys),
        }
        result = self._validator.validate(raw_dict)
        if not result.valid:
            _log.error(
                "Claude analysis validation failed: %s — %s",
                result.outcome, result.message,
            )
            raise ValueError(f"Claude response validation failed [{result.outcome}]: {result.message}")

        # result.analysis is the validated ClaudeAnalysis
        assert result.analysis is not None
        return result.analysis, turns

    async def close(self) -> None:
        pass  # httpx client is per-session; nothing to close at this level


__all__ = ["SystemXClaudeClient"]
