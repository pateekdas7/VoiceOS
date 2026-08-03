"""OptimizationPlaybook — documented procedures for each pipeline stage bottleneck (V3 Ch19).

Architecture: V3 Ch19 (Performance Engineering).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from src.libs.performance_engineering.benchmarks import STAGE_BUDGETS_MS


@dataclass(frozen=True)
class OptimizationAction:
    """One concrete optimization action for a specific stage bottleneck."""

    stage: str
    description: str
    expected_improvement_pct: float
    """Estimated p95 improvement in percent — used to prioritize actions."""


class OptimizationPlaybook:
    """Static catalog of optimization procedures for each pipeline stage (V3 Ch19 §"Optimization Loops").

    Call ``suggest(stage, observed_p95_ms, budget_ms)`` to get a prioritized
    list of actions for a stage that is over budget.
    """

    _PLAYBOOKS: ClassVar[dict[str, list[OptimizationAction]]] = {
        "stt": [
            OptimizationAction("stt", "Pre-warm Whisper inference sessions per active call slot", 10.0),
            OptimizationAction("stt", "Enable FP8 quantization on Whisper encoder", 20.0),
            OptimizationAction("stt", "Reduce beam search width (beam=1 greedy for collections)", 15.0),
            OptimizationAction("stt", "Downsize model: large-v3 → medium (accuracy trade-off)", 30.0),
        ],
        "cil": [
            OptimizationAction("cil", "Cache entity extraction results per (session, turn) key", 40.0),
            OptimizationAction("cil", "Batch NER inference across concurrent calls", 20.0),
            OptimizationAction("cil", "Move intent classification to a smaller distilled model", 25.0),
        ],
        "llm_ttft": [
            OptimizationAction("llm_ttft", "Enable vLLM prefix caching for system prompt tokens", 30.0),
            OptimizationAction("llm_ttft", "Reduce system prompt token count by ≥ 30 %", 15.0),
            OptimizationAction("llm_ttft", "Increase vLLM max_num_seqs for higher batching efficiency", 10.0),
            OptimizationAction("llm_ttft", "Switch to INT4 quantization (quality trade-off)", 25.0),
        ],
        "tts_first_clause": [
            OptimizationAction("tts_first_clause", "Pre-buffer common greeting phrases as cached audio", 35.0),
            OptimizationAction("tts_first_clause", "Limit first LLM clause to ≤ 8 words before TTS handoff", 25.0),
            OptimizationAction("tts_first_clause", "Reduce TTS chunk size to 256 samples for faster first byte", 15.0),
        ],
    }

    @classmethod
    def suggest(
        cls,
        stage: str,
        observed_p95_ms: float,
        budget_ms: float | None = None,
    ) -> list[OptimizationAction]:
        """Return prioritized optimization actions for ``stage``.

        Actions are returned sorted by ``expected_improvement_pct`` (highest first),
        so callers can take the top-N actions for the quickest expected gain.

        If ``stage`` is unknown, returns an empty list.
        """
        if stage not in cls._PLAYBOOKS:
            return []
        effective_budget = budget_ms if budget_ms is not None else STAGE_BUDGETS_MS.get(stage, 0.0)
        _ = observed_p95_ms - effective_budget  # retained for future threshold-driven filtering
        actions = list(cls._PLAYBOOKS[stage])
        actions.sort(key=lambda a: a.expected_improvement_pct, reverse=True)
        return actions

    @classmethod
    def all_stages(cls) -> list[str]:
        """Return the list of stages the playbook covers."""
        return list(cls._PLAYBOOKS)
