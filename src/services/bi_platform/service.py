"""BIPlatformService — facade over BIWarehouse/ForecastingEngine/CrossTenantBenchmarking/
ExecutiveDashboard (V5 Ch21).

Architecture: V5 Ch21 (Business Intelligence Platform).
"""

from __future__ import annotations

from datetime import date

from src.libs.contracts.models.bi import BIFactDaily
from src.libs.contracts.primitives import TenantId

from .benchmarking import CrossTenantBenchmarking
from .executive_dashboard import ExecutiveDashboard
from .forecasting import ForecastingEngine
from .models import BenchmarkResult, ExecutiveSummary, ForecastResult
from .warehouse import BIWarehouse


class BIPlatformService:
    """Facade composing the Business Intelligence Platform's four components."""

    def __init__(
        self,
        warehouse: BIWarehouse,
        forecasting: ForecastingEngine,
        benchmarking: CrossTenantBenchmarking,
        executive_dashboard: ExecutiveDashboard,
    ) -> None:
        self.warehouse = warehouse
        self.forecasting = forecasting
        self.benchmarking = benchmarking
        self.executive_dashboard = executive_dashboard

    def refresh(self, tenant_id: TenantId, day: date) -> BIFactDaily:
        return self.warehouse.refresh(tenant_id, day)

    def forecast_collections_recovery(self, tenant_id: TenantId, horizon_days: int) -> ForecastResult:
        return self.forecasting.forecast_collections_recovery(tenant_id, horizon_days)

    def get_benchmark(self, metric: str, tenant_id: TenantId, day: date) -> BenchmarkResult:
        return self.benchmarking.get_benchmark(metric, tenant_id, day)

    def get_executive_summary(self, tenant_id: TenantId, day: date | None = None) -> ExecutiveSummary:
        return self.executive_dashboard.get_executive_summary(tenant_id, day)


__all__ = ["BIPlatformService"]
