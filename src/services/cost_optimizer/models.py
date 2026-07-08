"""Result/input types for the Cost Optimization service (V7 Ch15, Sprint-027).

Mirrors the ``bi_platform``/``models.py`` precedent (Sprint-024): the
*persisted* usage-event rows live in ``src.libs.contracts.models.billing``
next to their own repository; this module holds the request/result shapes
this service's own methods take and return.

Architecture: V7 Ch15 (Cost Optimization).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.libs.contracts.primitives import TenantId


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("DateRange.end must not precede DateRange.start")


@dataclass(frozen=True)
class ConversationResourceUsage:
    """Raw per-call resource consumption feeding cost calculation.

    ``tts_gpu_seconds`` is tracked separately from ``gpu_seconds`` (STT/VAD
    GPU time) so the cost report can break TTS out as its own line per
    Sprint-027.md's literal "GPU, STT, LLM, TTS, storage" breakdown, even
    though the billing rate card (Sprint-024) prices all GPU time via the
    single ``GPU_SECOND`` usage type -- TTS synthesis is billed at the same
    per-second rate as any other GPU-seconds, just reported under its own
    category here for operational visibility.
    """

    call_id: str
    tenant_id: TenantId
    gpu_seconds: float
    stt_tokens: int
    llm_tokens: int
    tts_gpu_seconds: float
    storage_mb: int


@dataclass(frozen=True)
class CostReport:
    """Output of ``CostOptimizer.cost_per_conversation()`` -- cost breakdown in minor units."""

    tenant_id: str
    call_count: int
    gpu_cost_minor: int
    stt_cost_minor: int
    llm_cost_minor: int
    tts_cost_minor: int
    storage_cost_minor: int
    currency: str

    @property
    def total_cost_minor(self) -> int:
        return (
            self.gpu_cost_minor
            + self.stt_cost_minor
            + self.llm_cost_minor
            + self.tts_cost_minor
            + self.storage_cost_minor
        )


@dataclass(frozen=True)
class InstanceMixRecommendation:
    """Output of ``InstanceMixOptimizer.optimize_instance_mix()``."""

    spot_ratio: float
    reserved_ratio: float
    on_demand_ratio: float
    rationale: str


@dataclass(frozen=True)
class CostRecommendation:
    """One ranked, actionable savings recommendation from ``CostOptimizer.recommend()``."""

    title: str
    estimated_savings_minor: int
    rationale: str
    priority: str
    """'high' | 'medium' | 'low' -- ranked, most-impactful first by the caller."""


__all__ = [
    "ConversationResourceUsage",
    "CostRecommendation",
    "CostReport",
    "DateRange",
    "InstanceMixRecommendation",
]
