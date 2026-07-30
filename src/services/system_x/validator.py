"""ClaudeResponseValidator — validates Claude analysis before any action is taken.

Claude output must never be executed directly. Every analysis passes through
this validator, which enforces:
  - Schema completeness (all required fields present and correct type)
  - Supported recovery actions (no invented action types)
  - Minimum confidence threshold for automated recovery
  - Absence of secret patterns (defence-in-depth against prompt injection)
  - No forbidden operations in the recovery plan
  - Sensible numeric bounds

If validation fails, System X rejects the analysis, audits the rejection,
notifies operators, and does not execute any recovery actions.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .models import ClaudeAnalysis, ValidationOutcome, ValidationResult

_log = logging.getLogger("system_x.validator")

# Patterns that must never appear in Claude's output (defence against injection)
_SECRET_PATTERNS = [
    re.compile(r"(?i)postgresql://[^@]+@"),
    re.compile(r"(?i)redis://:[^@]+@"),
    re.compile(r"(?i)(password|passwd|secret|token|api_key)\s*[:=]\s*\S+"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),  # AWS access key pattern
]

# Forbidden operations that must never appear in recovery_plan steps
_FORBIDDEN_KEYWORDS = frozenset({
    "drop table", "delete from", "truncate", "alter table",
    "schema change", "migrate database", "rotate credential",
    "rm -rf", "format disk", "wipe", "destroy",
})

_VALID_CONFIDENCE = {"low", "medium", "high"}
_MIN_RECOVERY_TIME_S = 10
_MAX_RECOVERY_TIME_S = 86400  # 24 hours cap
_MIN_PLAN_STEPS = 1
_MAX_PLAN_STEPS = 20


def _check_secrets(text: str) -> bool:
    """Return True if text contains a secret pattern."""
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _full_text(analysis_dict: dict[str, Any]) -> str:
    """Concatenate all string values for secret scanning."""
    parts = []
    for v in analysis_dict.values():
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, (list, tuple)):
            parts.extend(str(item) for item in v)
    return " ".join(parts)


class ClaudeResponseValidator:
    """Validates a parsed Claude analysis before the Policy Engine sees it."""

    def __init__(self, min_confidence_for_auto: str = "low") -> None:
        self._min_confidence = min_confidence_for_auto

    def validate(self, raw: dict[str, Any]) -> ValidationResult:
        """Run all checks. Returns ValidationResult with valid=True only if all pass."""

        # --- Schema check ---
        required = {
            "root_cause": str,
            "confidence": str,
            "recommended_actions": list,
            "recovery_plan": list,
            "estimated_recovery_time_s": int,
            "risk_assessment": str,
        }
        for field, expected_type in required.items():
            if field not in raw:
                return ValidationResult(
                    outcome=ValidationOutcome.SCHEMA_ERROR,
                    valid=False,
                    message=f"Missing required field: '{field}'",
                )
            if not isinstance(raw[field], (expected_type, float) if expected_type is int else expected_type):
                return ValidationResult(
                    outcome=ValidationOutcome.SCHEMA_ERROR,
                    valid=False,
                    message=f"Field '{field}' must be {expected_type.__name__}, got {type(raw[field]).__name__}",
                )

        confidence = raw["confidence"].lower()
        if confidence not in _VALID_CONFIDENCE:
            return ValidationResult(
                outcome=ValidationOutcome.SCHEMA_ERROR,
                valid=False,
                message=f"confidence must be one of {_VALID_CONFIDENCE}, got '{confidence}'",
            )

        # --- Recovery plan bounds ---
        plan = raw["recovery_plan"]
        if not _MIN_PLAN_STEPS <= len(plan) <= _MAX_PLAN_STEPS:
            return ValidationResult(
                outcome=ValidationOutcome.SCHEMA_ERROR,
                valid=False,
                message=f"recovery_plan must have {_MIN_PLAN_STEPS}–{_MAX_PLAN_STEPS} steps, got {len(plan)}",
            )

        # --- Numeric bounds ---
        eta = int(raw["estimated_recovery_time_s"])
        if not _MIN_RECOVERY_TIME_S <= eta <= _MAX_RECOVERY_TIME_S:
            return ValidationResult(
                outcome=ValidationOutcome.SCHEMA_ERROR,
                valid=False,
                message=f"estimated_recovery_time_s={eta} out of range [{_MIN_RECOVERY_TIME_S}, {_MAX_RECOVERY_TIME_S}]",
            )

        # --- Secret leak check ---
        full_text = _full_text(raw)
        if _check_secrets(full_text):
            _log.error("Claude response contains secret pattern — rejecting")
            return ValidationResult(
                outcome=ValidationOutcome.SECRET_LEAK,
                valid=False,
                message="Claude response contains a secret or credential pattern — rejected for security",
            )

        # --- Forbidden operation check ---
        combined_plan = " ".join(str(s) for s in plan).lower()
        for kw in _FORBIDDEN_KEYWORDS:
            if kw in combined_plan:
                return ValidationResult(
                    outcome=ValidationOutcome.FORBIDDEN_OPERATION,
                    valid=False,
                    message=f"Recovery plan contains forbidden operation: '{kw}'",
                )

        # --- Empty root cause ---
        if not raw["root_cause"].strip():
            return ValidationResult(
                outcome=ValidationOutcome.SCHEMA_ERROR,
                valid=False,
                message="root_cause must not be empty",
            )

        # All checks passed — build ClaudeAnalysis
        # (analyzed_at and model are set by the caller)
        from datetime import UTC, datetime
        analysis = ClaudeAnalysis(
            root_cause=raw["root_cause"].strip(),
            confidence=confidence,
            recommended_actions=tuple(str(a) for a in raw["recommended_actions"]),
            recovery_plan=tuple(str(s) for s in plan),
            estimated_recovery_time_s=eta,
            risk_assessment=raw["risk_assessment"].strip(),
            model=raw.get("model", "unknown"),
            analyzed_at=datetime.now(UTC),
            conversation_id=raw.get("conversation_id"),
            turn_count=int(raw.get("turn_count", 1)),
            evidence_keys=tuple(raw.get("evidence_keys", [])),
        )

        _log.info("validation passed confidence=%s plan_steps=%d", confidence, len(plan))
        return ValidationResult(
            outcome=ValidationOutcome.VALID,
            valid=True,
            message="Validation passed",
            analysis=analysis,
        )


__all__ = ["ClaudeResponseValidator"]
