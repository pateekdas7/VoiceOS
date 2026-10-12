"""Repository ports for reasoning/'s four owned tables (ADR-006 Sec 7).

Real deployments back these with tenant-scoped Postgres repositories
(migrations 0032/0034/0035/0036); unit tests inject in-memory fakes.
"""

from __future__ import annotations

from typing import Protocol

from src.services.ops_intelligence.models import Insight, InsightCategory, PatternSignature, Report, ReportType
from src.services.ops_intelligence.reasoning.capacity_planner import CapacityForecast


class InsightRepositoryPort(Protocol):
    def create(self, insight: Insight) -> Insight: ...

    def get(self, insight_id: str) -> Insight | None: ...

    def list(
        self, *, tenant_id: str | None = None, category: InsightCategory | None = None, limit: int = 50
    ) -> tuple[Insight, ...]: ...


class ReportRepositoryPort(Protocol):
    def create(self, report: Report) -> Report: ...

    def get(self, report_id: str) -> Report | None: ...

    def list(
        self, *, tenant_id: str | None = None, report_type: ReportType | None = None, limit: int = 50
    ) -> tuple[Report, ...]: ...


class CapacityForecastRepositoryPort(Protocol):
    def create(self, forecast: CapacityForecast) -> CapacityForecast: ...

    def latest(self, resource: str, *, tenant_id: str | None = None) -> CapacityForecast | None: ...

    def history(
        self, resource: str, *, tenant_id: str | None = None, limit: int = 12
    ) -> tuple[CapacityForecast, ...]: ...


class PatternSignatureRepositoryPort(Protocol):
    """Backs the recurring-issue detection design (ADR-006 Sec 3.5) -- plain hash/counter, no ML."""

    def find(self, fingerprint_hash: str, *, tenant_id: str | None) -> PatternSignature | None: ...

    def upsert_occurrence(self, signature: PatternSignature) -> PatternSignature: ...


__all__ = [
    "CapacityForecastRepositoryPort",
    "InsightRepositoryPort",
    "PatternSignatureRepositoryPort",
    "ReportRepositoryPort",
]
