"""DailyAggregationJob — pre-computes daily rollups for fast dashboard reads (V5 Ch11).

Sourced from ``call_dispositions`` + ``campaign_results`` (both already
populated by Sprint-014/023) — no new raw-fact tables needed, only the
``analytics_daily`` rollup this job upserts into (migration 0022).

``amount_collected_minor``/``avg_dpd`` are left at 0 in this rollup: they
require joining ``promises_to_pay``/``loan_accounts`` (DPD, disbursed
amounts), which is out of Sprint-024's file list — see
``CampaignAnalytics.amount_collected_minor``'s docstring for the same
scoping note.

Architecture: V5 Ch11 (Analytics Platform — DailyAggregationJob).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from src.libs.contracts.models.analytics import AnalyticsDailyRollup, CallDisposition
from src.libs.contracts.models.campaign import CampaignResult
from src.libs.contracts.primitives import CampaignId, TenantId

_UNREACHABLE_OUTCOMES = frozenset({"NOT_REACHABLE", "NO_ANSWER"})
_RECOVERY_OUTCOMES = frozenset({"PTP_MADE", "SETTLEMENT_AGREED"})


class CallDispositionRepositoryPort(Protocol):
    def find_between(self, tenant_id: TenantId, start: datetime, end: datetime) -> tuple[CallDisposition, ...]: ...


class CampaignResultRepositoryPort(Protocol):
    def find_between(self, tenant_id: TenantId, start: datetime, end: datetime) -> tuple[CampaignResult, ...]: ...

    def find_by_campaign(self, tenant_id: TenantId, campaign_id: CampaignId) -> tuple[CampaignResult, ...]: ...


class AnalyticsDailyRepositoryPort(Protocol):
    def upsert(self, rollup: AnalyticsDailyRollup) -> AnalyticsDailyRollup: ...


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


class DailyAggregationJob:
    """Computes and upserts one day's tenant-wide (or per-campaign) analytics rollup."""

    def __init__(
        self,
        call_disposition_repository: CallDispositionRepositoryPort,
        campaign_result_repository: CampaignResultRepositoryPort,
        analytics_daily_repository: AnalyticsDailyRepositoryPort,
    ) -> None:
        self._dispositions = call_disposition_repository
        self._campaign_results = campaign_result_repository
        self._analytics_daily = analytics_daily_repository

    def run_for_day(
        self, tenant_id: TenantId, day: date, campaign_id: CampaignId | None = None
    ) -> AnalyticsDailyRollup:
        """Compute and persist the rollup for ``day`` — tenant-wide if ``campaign_id`` is None."""
        start, end = _day_bounds(day)

        if campaign_id is None:
            dispositions = self._dispositions.find_between(tenant_id, start, end)
            results = self._campaign_results.find_between(tenant_id, start, end)
        else:
            dispositions = ()
            all_results = self._campaign_results.find_by_campaign(tenant_id, campaign_id)
            results = tuple(r for r in all_results if start <= r.completed_at < end)

        calls_completed = len(dispositions) if campaign_id is None else len(results)
        ptp_count = sum(1 for r in results if r.ptp_created)
        ptp_rate = (ptp_count / len(results)) if results else 0.0
        avg_duration_ms = int(sum(d.duration_ms for d in dispositions) / len(dispositions)) if dispositions else 0
        contactability_rate = (
            (sum(1 for d in dispositions if d.outcome_code not in _UNREACHABLE_OUTCOMES) / len(dispositions))
            if dispositions
            else 0.0
        )
        recovery_rate = (
            (sum(1 for d in dispositions if d.outcome_code in _RECOVERY_OUTCOMES) / len(dispositions))
            if dispositions
            else 0.0
        )

        rollup = AnalyticsDailyRollup(
            analytics_daily_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            day=day,
            campaign_id=campaign_id,
            calls_completed=calls_completed,
            ptp_count=ptp_count,
            ptp_rate=ptp_rate,
            avg_duration_ms=avg_duration_ms,
            amount_collected_minor=0,
            contactability_rate=contactability_rate,
            recovery_rate=recovery_rate,
            avg_dpd=0.0,
            computed_at=datetime.now(UTC),
        )
        return self._analytics_daily.upsert(rollup)


__all__ = ["DailyAggregationJob"]
