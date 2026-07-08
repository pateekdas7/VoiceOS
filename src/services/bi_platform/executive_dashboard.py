"""ExecutiveDashboard — top-level KPI API for the Admin Portal (V5 Ch21, Sprint-024.md).

``cost_per_conversation`` and ``slo_attainment`` have no dedicated authoritative
source yet (infra cost accounting and SLO/uptime monitoring are Sprint-026/027
scope) — both are computed as documented, best-effort proxies from what
``bi_facts.fact_daily`` already carries, never fabricated: ``cost_per_conversation``
is revenue-per-call-minute (the closest available per-conversation unit
economics signal) and ``slo_attainment`` defaults to 1.0 (no SLO breach signal
available yet) rather than inventing a number with no basis.

Architecture: V5 Ch21 (Business Intelligence Platform — ExecutiveDashboard).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Protocol

from src.libs.contracts.models.bi import BIFactDaily
from src.libs.contracts.primitives import TenantId

from .models import ExecutiveSummary

DEFAULT_SLO_ATTAINMENT = 1.0
"""Neutral default until Sprint-027 wires real SLO/uptime monitoring signals in."""


class BIRepositoryPort(Protocol):
    def find_fact_for_tenant(self, tenant_id: TenantId, day: date) -> BIFactDaily | None: ...


class ExecutiveDashboard:
    """Assembles the top-level executive KPI summary from ``bi_facts.fact_daily``."""

    def __init__(self, bi_repository: BIRepositoryPort) -> None:
        self._bi_repository = bi_repository

    def get_executive_summary(self, tenant_id: TenantId, day: date | None = None) -> ExecutiveSummary:
        as_of = day or date.today()
        today_fact = self._bi_repository.find_fact_for_tenant(tenant_id, as_of)
        prior_fact = self._bi_repository.find_fact_for_tenant(tenant_id, as_of - timedelta(days=30))

        gross_recovery_rate = today_fact.recovery_rate if today_fact is not None else 0.0
        prior_recovery_rate = prior_fact.recovery_rate if prior_fact is not None else 0.0
        mom_improvement = gross_recovery_rate - prior_recovery_rate

        cost_per_conversation_minor = 0
        if today_fact is not None and today_fact.usage_call_minutes > 0:
            cost_per_conversation_minor = today_fact.revenue_minor // today_fact.usage_call_minutes

        compliance_score = today_fact.compliance_score if today_fact is not None else 0.0

        return ExecutiveSummary(
            tenant_id=str(tenant_id),
            gross_recovery_rate=gross_recovery_rate,
            cost_per_conversation_minor=cost_per_conversation_minor,
            mom_improvement=mom_improvement,
            slo_attainment=DEFAULT_SLO_ATTAINMENT,
            compliance_score=compliance_score,
        )


__all__ = ["DEFAULT_SLO_ATTAINMENT", "ExecutiveDashboard"]
