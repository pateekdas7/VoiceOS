"""Business Intelligence Platform — BI warehouse, forecasting, cross-tenant
benchmarking, executive dashboards (V5 Ch21, Sprint-024).

Note the underscore in ``bi_platform`` (not the sprint spec's ``bi-platform``)
— a hyphen is not a valid Python package/module name; every sibling
directory under ``src/services/`` uses underscores. See CHANGELOG.md
Sprint-024 deviations.
"""

from __future__ import annotations

from .benchmarking import BENCHMARKABLE_METRICS, CrossTenantBenchmarking
from .executive_dashboard import ExecutiveDashboard
from .forecasting import ForecastingEngine
from .models import BenchmarkResult, ExecutiveSummary, ForecastPoint, ForecastResult
from .service import BIPlatformService
from .warehouse import BIWarehouse

__all__ = [
    "BENCHMARKABLE_METRICS",
    "BIPlatformService",
    "BIWarehouse",
    "BenchmarkResult",
    "CrossTenantBenchmarking",
    "ExecutiveDashboard",
    "ExecutiveSummary",
    "ForecastPoint",
    "ForecastResult",
    "ForecastingEngine",
]
