"""OpsIntelligenceService -- the facade, and the ONLY place the reasoning-model kill switch is checked.

ADR-006 Sec 3.0/9: exactly two call sites ever reach ``reasoning/`` --
(1) the scheduled analysis CronJob (:meth:`run_scheduled_analysis`), and
(2) the BFF read endpoints backing the AI Insights/AI Reports dashboards
(:meth:`is_reasoning_enabled`, called by the route handler before it queries
anything in ``reasoning/``). Both check
``feature_flags.is_enabled("ops_intelligence_reasoning", ...)`` first. No
other code path in this repository may invoke ``reasoning/`` directly --
this is what makes "flag off => zero reasoning/ code executes" a small,
auditable claim (two call sites) rather than a repo-wide promise resting on
an unproven mechanism (the flag itself has no other production call site
today, per the Rev 3 audit).

``plumbing/`` (alerts, notifications) is NEVER gated by this flag and has no
dependency on this service at all -- see ``plumbing/__init__.py``.
"""

from __future__ import annotations

from typing import Protocol

from src.libs.contracts.primitives import TenantId
from src.services.ops_intelligence.models import Insight
from src.services.ops_intelligence.reasoning.capacity_planner import CapacityPlanner
from src.services.ops_intelligence.reasoning.evidence_bundler import EvidenceBundler, MetricCheckSpec
from src.services.ops_intelligence.reasoning.insight_service import InsightService
from src.services.ops_intelligence.reasoning.report_generator import ReportGenerator

REASONING_FLAG_NAME = "ops_intelligence_reasoning"
PLATFORM_FLAG_TENANT_ID = TenantId("platform")
"""Pseudo-tenant used only to resolve the GLOBAL scope of REASONING_FLAG_NAME
for platform-wide (not tenant-scoped) analysis runs -- FeatureFlagService.is_enabled()
always requires a TenantId, and no PLAN/COHORT/TENANT row ever exists for this
sentinel, so resolution falls through to the intended GLOBAL row."""


class FeatureFlagPort(Protocol):
    def is_enabled(self, flag_name: str, tenant_id: TenantId) -> bool: ...


class OpsIntelligenceService:
    """Facade over ``reasoning/`` -- gates every entry point behind the kill switch."""

    def __init__(
        self,
        feature_flags: FeatureFlagPort,
        evidence_bundler: EvidenceBundler,
        insight_service: InsightService,
        report_generator: ReportGenerator | None = None,
        capacity_planner: CapacityPlanner | None = None,
        metric_check_specs: tuple[MetricCheckSpec, ...] = (),
    ) -> None:
        self._feature_flags = feature_flags
        self._evidence_bundler = evidence_bundler
        self._insight_service = insight_service
        self._report_generator = report_generator
        self._capacity_planner = capacity_planner
        self._metric_check_specs = metric_check_specs

    def is_reasoning_enabled(self, tenant_id: str | None = None) -> bool:
        """Call site 2 of 2 -- BFF route handlers must call this before touching ``reasoning/``."""
        effective_tenant = TenantId(tenant_id) if tenant_id else PLATFORM_FLAG_TENANT_ID
        return self._feature_flags.is_enabled(REASONING_FLAG_NAME, effective_tenant)

    async def run_scheduled_analysis(self, *, tenant_id: str | None = None) -> tuple[Insight, ...]:
        """Call site 1 of 2 -- the scheduled CronJob trigger.

        Returns an empty tuple (never raises) when the flag is off -- this
        is a normal, expected "AI analysis is disabled" outcome, not an
        error condition.
        """
        if not self.is_reasoning_enabled(tenant_id):
            return ()

        generated: list[Insight] = []
        for spec in self._metric_check_specs:
            bundle = self._evidence_bundler.check_regression(spec, tenant_id=tenant_id)
            if bundle is None:
                continue
            insight = await self._insight_service.generate(bundle)
            if insight is not None:
                generated.append(insight)
        return tuple(generated)

    @property
    def reports(self) -> ReportGenerator | None:
        """Exposed for BFF handlers -- callers MUST check :meth:`is_reasoning_enabled` first."""
        return self._report_generator

    @property
    def capacity(self) -> CapacityPlanner | None:
        """Exposed for BFF handlers -- callers MUST check :meth:`is_reasoning_enabled` first."""
        return self._capacity_planner


__all__ = ["PLATFORM_FLAG_TENANT_ID", "REASONING_FLAG_NAME", "OpsIntelligenceService"]
