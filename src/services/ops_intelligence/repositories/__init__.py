"""Concrete Postgres repository adapters for ops_intelligence (ADR-006).

Sits outside both ``plumbing/`` and ``reasoning/`` -- a persistence-adapter
layer implementing the Protocol ports each sub-component defines, injected
at composition-root time, never imported by the sub-components themselves.
"""

from __future__ import annotations

from .alert_repository import PostgresAlertRepository
from .capacity_forecast_repository import PostgresCapacityForecastRepository
from .insight_repository import PostgresInsightRepository
from .pattern_signature_repository import PostgresPatternSignatureRepository
from .report_repository import PostgresReportRepository

__all__ = [
    "PostgresAlertRepository",
    "PostgresCapacityForecastRepository",
    "PostgresInsightRepository",
    "PostgresPatternSignatureRepository",
    "PostgresReportRepository",
]
