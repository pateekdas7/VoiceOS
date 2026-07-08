"""Unit tests for src/services/ops_analytics/ (Sprint-027)."""

from __future__ import annotations

from datetime import date

import pytest

from src.libs.contracts.primitives import TenantId
from src.services.cost_optimizer.models import CostReport
from src.services.cost_optimizer.models import DateRange as CostDateRange
from src.services.ops_analytics import (
    DateRange,
    OpsAnalytics,
    TechnicalKPISynthesizer,
    UnitEconomics,
)

TENANT = TenantId("22222222-2222-2222-2222-222222222222")
RANGE = DateRange(start=date(2026, 7, 1), end=date(2026, 7, 8))


class _StaticObservability:
    def __init__(self, availability: float = 0.9995, slo_attainment: float = 0.99) -> None:
        self._availability = availability
        self._slo_attainment = slo_attainment

    def availability_ratio(self, date_range: DateRange) -> float:
        return self._availability

    def incident_resolution_seconds(self, date_range: DateRange) -> tuple[float, ...]:
        return (300.0, 600.0, 450.0)

    def time_between_failures_seconds(self, date_range: DateRange) -> tuple[float, ...]:
        return (86400.0, 172800.0)

    def slo_attainment_ratio(self, date_range: DateRange) -> float:
        return self._slo_attainment


class _StaticBusinessKPIs:
    def revenue_minor(self, date_range: DateRange) -> int:
        return 500_000

    def collections_recovered_minor(self, date_range: DateRange) -> int:
        return 1_200_000

    def cost_per_call_minor(self, date_range: DateRange) -> int:
        return 50


class _StaticCostSource:
    def __init__(self, total_cost_minor: int) -> None:
        self._total_cost_minor = total_cost_minor

    def cost_per_conversation(self, tenant_id: TenantId, date_range: CostDateRange) -> CostReport:
        return CostReport(
            tenant_id=str(tenant_id),
            call_count=100,
            gpu_cost_minor=self._total_cost_minor,
            stt_cost_minor=0,
            llm_cost_minor=0,
            tts_cost_minor=0,
            storage_cost_minor=0,
            currency="INR",
        )


class _StaticRevenueSource:
    def __init__(self, revenue_minor: int, call_count: int) -> None:
        self._revenue_minor = revenue_minor
        self._call_count = call_count

    def revenue_minor(self, tenant_id: TenantId, date_range: DateRange) -> int:
        return self._revenue_minor

    def call_count(self, tenant_id: TenantId, date_range: DateRange) -> int:
        return self._call_count


class TestTechnicalKPISynthesizer:
    def test_synthesize_computes_mttr_mtbf(self) -> None:
        synthesizer = TechnicalKPISynthesizer(_StaticObservability())
        kpis = synthesizer.synthesize(RANGE)

        assert kpis.mttr_seconds == pytest.approx((300 + 600 + 450) / 3)
        assert kpis.mtbf_seconds == pytest.approx((86400 + 172800) / 2)
        assert kpis.availability == 0.9995
        assert kpis.slo_attainment == 0.99

    def test_synthesize_handles_no_incidents(self) -> None:
        class _NoIncidents(_StaticObservability):
            def incident_resolution_seconds(self, date_range: DateRange) -> tuple[float, ...]:
                return ()

            def time_between_failures_seconds(self, date_range: DateRange) -> tuple[float, ...]:
                return ()

        synthesizer = TechnicalKPISynthesizer(_NoIncidents())
        kpis = synthesizer.synthesize(RANGE)
        assert kpis.mttr_seconds == 0.0
        assert kpis.mtbf_seconds == 0.0


class TestUnitEconomics:
    def test_unit_economics_positive_margin(self) -> None:
        unit_econ = UnitEconomics(
            _StaticCostSource(total_cost_minor=5000), _StaticRevenueSource(revenue_minor=20000, call_count=100)
        )
        report = unit_econ.unit_economics(TENANT, RANGE)

        assert report.cost_per_conversation_minor == 50
        assert report.revenue_per_conversation_minor == 200
        assert report.margin_per_conversation_minor == 150
        assert report.break_even_calls == 0

    def test_unit_economics_negative_margin_computes_break_even(self) -> None:
        unit_econ = UnitEconomics(
            _StaticCostSource(total_cost_minor=30000), _StaticRevenueSource(revenue_minor=10000, call_count=100)
        )
        report = unit_econ.unit_economics(TENANT, RANGE)

        assert report.cost_per_conversation_minor == 300
        assert report.revenue_per_conversation_minor == 100
        assert report.margin_per_conversation_minor == -200
        assert report.break_even_calls > 0

    def test_unit_economics_zero_calls_no_division_error(self) -> None:
        unit_econ = UnitEconomics(
            _StaticCostSource(total_cost_minor=0), _StaticRevenueSource(revenue_minor=0, call_count=0)
        )
        report = unit_econ.unit_economics(TENANT, RANGE)
        assert report.cost_per_conversation_minor == 0
        assert report.revenue_per_conversation_minor == 0


class TestOpsAnalyticsFacade:
    def _build(self) -> OpsAnalytics:
        return OpsAnalytics(
            TechnicalKPISynthesizer(_StaticObservability()),
            _StaticBusinessKPIs(),
            UnitEconomics(
                _StaticCostSource(total_cost_minor=5000), _StaticRevenueSource(revenue_minor=20000, call_count=100)
            ),
        )

    def test_ops_analytics_scorecard_fields(self) -> None:
        """Scorecard has all required technical + business KPI fields (Sprint-027.md required test)."""
        ops = self._build()
        scorecard = ops.get_operator_scorecard(RANGE)
        as_dict = scorecard.as_dict()

        assert "technical_kpis" in as_dict
        assert "business_kpis" in as_dict
        assert scorecard.technical_kpis.availability == 0.9995
        assert scorecard.business_kpis.revenue_minor == 500_000

    def test_unit_economics_delegates(self) -> None:
        ops = self._build()
        report = ops.unit_economics(TENANT, RANGE)
        assert report.tenant_id == str(TENANT)
        assert report.cost_per_conversation_minor == 50
