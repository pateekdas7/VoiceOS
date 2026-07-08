"""CrossTenantBenchmarking — anonymized cross-tenant percentile comparison (V5 Ch21).

Reads exclusively via ``bi_facts.fact_daily``'s surrogate key
(``BIRepository.find_all_facts_for_day`` never selects ``tenant_id`` or
joins ``dim_tenant``) — a tenant's own percentile is computed by locating
its own surrogate key in the anonymized value set, so no other tenant's
identity is ever resolvable from the result (Sprint-024 AC).

Architecture: V5 Ch21 (Business Intelligence Platform — CrossTenantBenchmarking).
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from src.libs.contracts.models.bi import BIFactDaily
from src.libs.contracts.primitives import TenantId

from .models import BenchmarkResult

BENCHMARKABLE_METRICS = (
    "revenue_minor",
    "usage_call_minutes",
    "usage_stt_tokens",
    "usage_llm_tokens",
    "usage_gpu_seconds",
    "ptp_rate",
    "recovery_rate",
    "compliance_score",
)


class UnknownBenchmarkMetricError(ValueError):
    pass


class BIRepositoryPort(Protocol):
    def get_or_create_surrogate_key(self, tenant_id: TenantId) -> str: ...

    def find_all_facts_for_day(self, day: date) -> tuple[BIFactDaily, ...]: ...


class CrossTenantBenchmarking:
    """Computes a tenant's anonymized percentile rank for one BI metric on a given day."""

    def __init__(self, bi_repository: BIRepositoryPort) -> None:
        self._bi_repository = bi_repository

    def get_benchmark(self, metric: str, tenant_id: TenantId, day: date) -> BenchmarkResult:
        if metric not in BENCHMARKABLE_METRICS:
            raise UnknownBenchmarkMetricError(
                f"{metric!r} is not benchmarkable, expected one of {BENCHMARKABLE_METRICS}"
            )

        surrogate_key = self._bi_repository.get_or_create_surrogate_key(tenant_id)
        facts = self._bi_repository.find_all_facts_for_day(day)
        values_by_key = {fact.tenant_surrogate_key: getattr(fact, metric) for fact in facts}

        if surrogate_key not in values_by_key or not values_by_key:
            return BenchmarkResult(metric=metric, tenant_value=0.0, percentile=0.0, sample_size=len(values_by_key))

        tenant_value = float(values_by_key[surrogate_key])
        all_values = sorted(values_by_key.values())
        rank = sum(1 for value in all_values if value <= tenant_value)
        percentile = (rank / len(all_values)) * 100.0
        return BenchmarkResult(
            metric=metric, tenant_value=tenant_value, percentile=percentile, sample_size=len(values_by_key)
        )


__all__ = ["BENCHMARKABLE_METRICS", "CrossTenantBenchmarking", "UnknownBenchmarkMetricError"]
