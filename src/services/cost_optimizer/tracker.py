"""ConversationCostTracker -- aggregates GPU-seconds, STT/LLM tokens per call (V7 Ch15).

Prices resource usage via the existing Sprint-024 ``DEFAULT_RATE_CARD``
(``STT_TOKEN``/``LLM_TOKEN``/``GPU_SECOND``/``STORAGE_MB`` entries) rather
than inventing a second pricing table -- one rate card, used by both the
billing invoice path (Sprint-024) and this operational cost-visibility
path (Sprint-027).

Architecture: V7 Ch15 (Cost Optimization).
"""

from __future__ import annotations

from typing import Protocol

from src.libs.contracts.models.billing import UsageType
from src.libs.contracts.primitives import TenantId
from src.services.billing.rate_card import DEFAULT_RATE_CARD, RateCard

from .models import ConversationResourceUsage, CostReport, DateRange


class ConversationUsageSource(Protocol):
    """Injected port supplying raw per-call resource usage for a tenant/date-range.

    A real deployment backs this with Prometheus range-query results
    (GPU-seconds/tokens per call, joined by ``call_id``); Phase 1 unit
    tests inject a static in-memory fixture (Sprint-027.md's own "Usage
    data: Static fixture" mock-backend note).
    """

    def usage_for(self, tenant_id: TenantId, date_range: DateRange) -> tuple[ConversationResourceUsage, ...]: ...


class ConversationCostTracker:
    """Prices raw per-call resource usage into a :class:`CostReport`."""

    def __init__(self, usage_source: ConversationUsageSource, *, rate_card: RateCard = DEFAULT_RATE_CARD) -> None:
        self._usage_source = usage_source
        self._rate_card = rate_card

    def cost_report(self, tenant_id: TenantId, date_range: DateRange) -> CostReport:
        """Aggregate + price every call's resource usage in ``date_range`` for ``tenant_id``."""
        calls = self._usage_source.usage_for(tenant_id, date_range)

        gpu_cost = sum(self._rate_card.cost_minor(UsageType.GPU_SECOND, round(c.gpu_seconds)) for c in calls)
        stt_cost = sum(self._rate_card.cost_minor(UsageType.STT_TOKEN, c.stt_tokens) for c in calls)
        llm_cost = sum(self._rate_card.cost_minor(UsageType.LLM_TOKEN, c.llm_tokens) for c in calls)
        tts_cost = sum(self._rate_card.cost_minor(UsageType.GPU_SECOND, round(c.tts_gpu_seconds)) for c in calls)
        storage_cost = sum(self._rate_card.cost_minor(UsageType.STORAGE_MB, c.storage_mb) for c in calls)

        return CostReport(
            tenant_id=str(tenant_id),
            call_count=len(calls),
            gpu_cost_minor=gpu_cost,
            stt_cost_minor=stt_cost,
            llm_cost_minor=llm_cost,
            tts_cost_minor=tts_cost,
            storage_cost_minor=storage_cost,
            currency=self._rate_card.currency,
        )


__all__ = ["ConversationCostTracker", "ConversationUsageSource"]
