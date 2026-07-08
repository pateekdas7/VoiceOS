"""OpsAnalytics -- combined technical + business KPI synthesis (V7 Ch21, Sprint-027).

Architecture: V7 Ch21 (Operational Analytics).
"""

from __future__ import annotations

from typing import Protocol

from src.libs.contracts.primitives import TenantId

from .models import BusinessKPIs, DateRange, OperatorScorecard, UnitEconomicsReport
from .technical_kpis import TechnicalKPISynthesizer
from .unit_economics import UnitEconomics


class BusinessKPIPort(Protocol):
    """Structural port over platform-wide business KPIs for a window.

    A real deployment backs this with the Analytics/Billing/Collections
    platforms (Sprint-022/023/024); unit tests inject a static fixture.
    """

    def revenue_minor(self, date_range: DateRange) -> int: ...
    def collections_recovered_minor(self, date_range: DateRange) -> int: ...
    def cost_per_call_minor(self, date_range: DateRange) -> int: ...


class OpsAnalytics:
    """The operator-facing executive view combining technical and business KPIs."""

    def __init__(
        self,
        technical_synthesizer: TechnicalKPISynthesizer,
        business_kpis: BusinessKPIPort,
        unit_economics: UnitEconomics,
    ) -> None:
        self._technical_synthesizer = technical_synthesizer
        self._business_kpis = business_kpis
        self._unit_economics = unit_economics

    def get_operator_scorecard(self, date_range: DateRange) -> OperatorScorecard:
        """Combined technical + business KPI scorecard for ``date_range`` (platform-wide)."""
        technical = self._technical_synthesizer.synthesize(date_range)
        business = BusinessKPIs(
            revenue_minor=self._business_kpis.revenue_minor(date_range),
            collections_recovered_minor=self._business_kpis.collections_recovered_minor(date_range),
            cost_per_call_minor=self._business_kpis.cost_per_call_minor(date_range),
        )
        return OperatorScorecard(date_range=date_range, technical_kpis=technical, business_kpis=business)

    def unit_economics(self, tenant_id: TenantId, date_range: DateRange) -> UnitEconomicsReport:
        """Cost-per-conversation, margin-per-conversation, break-even analysis for one tenant."""
        return self._unit_economics.unit_economics(tenant_id, date_range)


__all__ = ["BusinessKPIPort", "OpsAnalytics"]
