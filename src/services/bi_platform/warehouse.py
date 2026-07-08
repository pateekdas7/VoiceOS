"""BIWarehouse — daily BI fact refresh (V5 Ch21, Sprint-024.md).

Aggregates AnalyticsService (``analytics_daily`` rollups: ptp_rate/
recovery_rate) + MeteringService (``usage_events``: per-type totals,
revenue) + ComplianceMonitoring (compliance score) into one
``bi_facts.fact_daily`` row per tenant-day — separate from, and slower-
refreshing than, the operational ``AnalyticsService`` (V5 Ch21 vs V5 Ch11).

Architecture: V5 Ch21 (Business Intelligence Platform — BIWarehouse, daily
refresh at 01:00 UTC).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Protocol

from src.libs.contracts.models.analytics import AnalyticsDailyRollup
from src.libs.contracts.models.bi import BIFactDaily
from src.libs.contracts.models.billing import UsageEvent, UsageType
from src.libs.contracts.primitives import CampaignId, TenantId

from ..metering.aggregator import hour_bucket


class BIRepositoryPort(Protocol):
    def get_or_create_surrogate_key(self, tenant_id: TenantId) -> str: ...

    def upsert_fact_daily(self, fact: BIFactDaily) -> BIFactDaily: ...


class AnalyticsDailyRepositoryPort(Protocol):
    def find_for_day(
        self, tenant_id: TenantId, day: date, campaign_id: CampaignId | None = None
    ) -> AnalyticsDailyRollup | None: ...


class UsageRepositoryPort(Protocol):
    def find_all_between(self, tenant_id: TenantId, since_bucket: str, until_bucket: str) -> tuple[UsageEvent, ...]: ...


class CompliancePort(Protocol):
    """Structural port over ``ComplianceMonitoring``'s compliance-posture score."""

    def compliance_score(self, tenant_id: TenantId) -> float: ...


class _DefaultCompliancePort:
    """Neutral default (score 1.0) when no ``ComplianceMonitoring`` wiring is supplied."""

    def compliance_score(self, tenant_id: TenantId) -> float:
        return 1.0


class BIWarehouse:
    """Refreshes one tenant-day's BI fact row from Analytics + Metering + Compliance."""

    def __init__(
        self,
        bi_repository: BIRepositoryPort,
        analytics_daily_repository: AnalyticsDailyRepositoryPort,
        usage_repository: UsageRepositoryPort,
        compliance: CompliancePort | None = None,
    ) -> None:
        self._bi_repository = bi_repository
        self._analytics_daily_repository = analytics_daily_repository
        self._usage_repository = usage_repository
        self._compliance = compliance or _DefaultCompliancePort()

    def refresh(self, tenant_id: TenantId, day: date) -> BIFactDaily:
        """Recompute and upsert ``tenant_id``'s BI fact row for ``day``.

        Idempotent/re-runnable: upserts on ``(tenant_surrogate_key, day)``
        (migration 0023's unique index), so a retried or manually re-triggered
        refresh never creates a duplicate fact row.
        """
        surrogate_key = self._bi_repository.get_or_create_surrogate_key(tenant_id)

        day_start_bucket = hour_bucket(datetime(day.year, day.month, day.day, 0, 0, tzinfo=UTC))
        day_end_bucket = hour_bucket(datetime(day.year, day.month, day.day, 23, 0, tzinfo=UTC))
        usage_events = self._usage_repository.find_all_between(tenant_id, day_start_bucket, day_end_bucket)

        revenue_minor = sum(event.total_cost_minor for event in usage_events)
        usage_totals = dict.fromkeys(UsageType, 0)
        for event in usage_events:
            usage_totals[event.usage_type] += event.quantity

        rollup = self._analytics_daily_repository.find_for_day(tenant_id, day)
        ptp_rate = rollup.ptp_rate if rollup is not None else 0.0
        recovery_rate = rollup.recovery_rate if rollup is not None else 0.0

        fact = BIFactDaily(
            fact_daily_id=str(uuid.uuid4()),
            tenant_surrogate_key=surrogate_key,
            day=day,
            revenue_minor=revenue_minor,
            usage_call_minutes=usage_totals[UsageType.CALL_MINUTE],
            usage_stt_tokens=usage_totals[UsageType.STT_TOKEN],
            usage_llm_tokens=usage_totals[UsageType.LLM_TOKEN],
            usage_gpu_seconds=usage_totals[UsageType.GPU_SECOND],
            ptp_rate=ptp_rate,
            recovery_rate=recovery_rate,
            compliance_score=self._compliance.compliance_score(tenant_id),
            refreshed_at=datetime.now(UTC),
        )
        return self._bi_repository.upsert_fact_daily(fact)


__all__ = ["BIWarehouse", "CompliancePort"]
