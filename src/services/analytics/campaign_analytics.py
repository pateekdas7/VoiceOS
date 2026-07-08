"""CampaignAnalytics — per-campaign conversion/contactability/recovery metrics (V5 Ch11).

Backed by ``campaign_results`` (Sprint-023) — the per-contact-attempt
outcome table ``ABTestingFramework`` already writes to.

Architecture: V5 Ch11 (Analytics Platform — CampaignAnalytics).
"""

from __future__ import annotations

from typing import Protocol

from src.libs.contracts.models.campaign import CampaignResult
from src.libs.contracts.primitives import CampaignId, TenantId

_UNREACHABLE_OUTCOMES = frozenset({"NOT_REACHABLE", "NO_ANSWER"})


class CampaignResultRepositoryPort(Protocol):
    def find_by_campaign(self, tenant_id: TenantId, campaign_id: CampaignId) -> tuple[CampaignResult, ...]: ...


class CampaignAnalytics:
    """Per-campaign conversion/contactability/PTP-rate analytics."""

    def __init__(self, repository: CampaignResultRepositoryPort) -> None:
        self._repository = repository

    def results_for(self, tenant_id: TenantId, campaign_id: CampaignId) -> tuple[CampaignResult, ...]:
        return self._repository.find_by_campaign(tenant_id, campaign_id)

    def ptp_rate(self, tenant_id: TenantId, campaign_id: CampaignId) -> float:
        """Promises-to-pay created / calls completed for this campaign."""
        results = self.results_for(tenant_id, campaign_id)
        if not results:
            return 0.0
        ptps = sum(1 for r in results if r.ptp_created)
        return ptps / len(results)

    def contactability_rate(self, tenant_id: TenantId, campaign_id: CampaignId) -> float:
        results = self.results_for(tenant_id, campaign_id)
        if not results:
            return 0.0
        contacted = sum(1 for r in results if r.outcome_code not in _UNREACHABLE_OUTCOMES)
        return contacted / len(results)

    def conversion_rate(self, tenant_id: TenantId, campaign_id: CampaignId) -> float:
        """Same measure as ``ptp_rate`` — kept as a distinct, spec-named method
        (V5 Ch11 names both "conversion" and "promise rate" as KPIs)."""
        return self.ptp_rate(tenant_id, campaign_id)

    def amount_collected_minor(self, tenant_id: TenantId, campaign_id: CampaignId) -> int:
        """Placeholder for settlement/PTP-amount aggregation (not yet joined to
        ``campaign_results`` — Sprint-024 scope only wires the outcome/PTP-count
        signals that ``campaign_results`` itself carries; amount aggregation
        requires a join to ``promises_to_pay``/``settlements`` left for a
        follow-up sprint)."""
        return 0


__all__ = ["CampaignAnalytics", "CampaignResultRepositoryPort"]
