"""BIDataModel — result/view types for the Business Intelligence Platform (V5 Ch21).

The persistent fact/dimension *tables* (``bi_facts.dim_tenant``/
``bi_facts.fact_daily``) live in ``src/libs/contracts/models/bi.py`` next to
their repository (``src/libs/repositories/bi.py``), matching every other
domain in this codebase (contracts + repository live together, service
layer consumes both). This module holds the *result* types
``ForecastingEngine``/``CrossTenantBenchmarking``/``ExecutiveDashboard``
return — computed views, not persisted rows.

Architecture: V5 Ch21 (Business Intelligence Platform — BIDataModel).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ForecastPoint:
    day: date
    predicted_recovery_rate: float


@dataclass(frozen=True)
class ForecastResult:
    """Output of ``ForecastingEngine.forecast_collections_recovery()``."""

    tenant_id: str
    horizon_days: int
    model: str
    """'exponential_smoothing' | 'arima'."""
    points: tuple[ForecastPoint, ...]


@dataclass(frozen=True)
class BenchmarkResult:
    """Output of ``CrossTenantBenchmarking.get_benchmark()`` — anonymized, no tenant IDs."""

    metric: str
    tenant_value: float
    percentile: float
    """This tenant's percentile rank (0-100) within the platform-wide distribution."""
    sample_size: int
    """Number of tenants in the comparison set (never includes tenant identities)."""


@dataclass(frozen=True)
class ExecutiveSummary:
    """Output of ``ExecutiveDashboard.get_executive_summary()`` — top-level KPIs."""

    tenant_id: str
    gross_recovery_rate: float
    cost_per_conversation_minor: int
    mom_improvement: float
    """Month-over-month change in gross recovery rate, as a fraction (e.g. 0.05 = +5%)."""
    slo_attainment: float
    compliance_score: float


__all__ = ["BenchmarkResult", "ExecutiveSummary", "ForecastPoint", "ForecastResult"]
