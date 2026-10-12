"""ops_intelligence.reasoning -- the killable, AI-gated sub-component (ADR-006 Sec 3.0).

Every module here that calls a reasoning-model adapter must go through
:mod:`adapters` -- no other code path in this package (or ``..plumbing``)
may call an LLM. Gated at exactly two call sites (the scheduled analysis
CronJob and the AI Insights/AI Reports BFF endpoints) by
``feature_flags.is_enabled("ops_intelligence_reasoning", ...)`` -- see
``service.py``.
"""

from __future__ import annotations

from .adapters import ClaudeReasoningAdapter, NarrationRequest, NarrationResult, ReasoningAdapter
from .capacity_planner import CapacityForecast, CapacityPlanner, MetricsHistoryPort
from .evidence_bundler import EvidenceBundle, EvidenceBundler, MetricCheckSpec, MetricSample
from .insight_service import CostObservabilityPort, InsightService, UsageEventPublisherPort
from .report_generator import ReportGenerator

__all__ = [
    "CapacityForecast",
    "CapacityPlanner",
    "ClaudeReasoningAdapter",
    "CostObservabilityPort",
    "EvidenceBundle",
    "EvidenceBundler",
    "InsightService",
    "MetricCheckSpec",
    "MetricSample",
    "MetricsHistoryPort",
    "NarrationRequest",
    "NarrationResult",
    "ReasoningAdapter",
    "ReportGenerator",
    "UsageEventPublisherPort",
]
