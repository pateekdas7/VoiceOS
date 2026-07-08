"""EvaluationRunService -- triggers an evaluation run for a published prompt version (V5 Ch14.6).

Sprint-025 scope: a deterministic keyword-match scorer over a fixed canned
case set -- verifies the template renders and contains the expected
placeholders/keywords for each case. A full model-graded evaluation harness
(live LLM calls scored by a judge model) is out of scope and documented as
future work, same "documented proxy" precedent as Sprint-024's
``ForecastingEngine`` (exponential smoothing standing in for a full
forecasting model).

Architecture: V5 Ch14.6 (Prompt Evaluation).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from src.libs.contracts.models.ai_config import EvalRunResult, EvalRunStatus, PromptVersion

DEFAULT_EVAL_CASES: tuple[tuple[str, str], ...] = (
    ("negotiate", "negotiate"),
    ("payment", "payment"),
    ("customer", "customer"),
)
"""(case_name, required_keyword) pairs -- a case passes if its keyword
appears (case-insensitively) in the rendered template."""


class EvaluationRunService:
    """Triggers a deterministic evaluation run for a PUBLISHED prompt version (V5 Ch14.6)."""

    def run(self, version: PromptVersion, cases: tuple[tuple[str, str], ...] = DEFAULT_EVAL_CASES) -> EvalRunResult:
        template_lower = version.template.lower()
        passed = sum(1 for _name, keyword in cases if keyword.lower() in template_lower)
        case_count = len(cases)
        score = passed / case_count if case_count else 0.0
        return EvalRunResult(
            eval_run_id=str(uuid.uuid4()),
            prompt_version_id=version.prompt_version_id,
            status=EvalRunStatus.COMPLETED,
            case_count=case_count,
            passed_count=passed,
            score=score,
            run_at=datetime.now(UTC),
        )


__all__ = ["DEFAULT_EVAL_CASES", "EvaluationRunService"]
