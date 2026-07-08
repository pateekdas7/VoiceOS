"""ReportScheduler — cron-driven daily aggregation runs (V5 Ch12, Sprint-024.md).

The actual crontab entry (or K8s CronJob, post-Sprint-026) invokes a thin
script that calls :meth:`ReportScheduler.run_daily` — this class is the
callable + history-tracking half; process scheduling itself is a deployment
concern (``scripts/run_daily_aggregation.py``).

Architecture: V5 Ch12 (Reporting Platform — cron scheduling, report history).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import NamedTuple, Protocol

from src.libs.contracts.models.analytics import AnalyticsDailyRollup
from src.libs.contracts.primitives import CampaignId, TenantId


class AggregationJobPort(Protocol):
    def run_for_day(
        self, tenant_id: TenantId, day: date, campaign_id: CampaignId | None = None
    ) -> AnalyticsDailyRollup: ...


class ScheduledRunRecord(NamedTuple):
    tenant_id: str
    day: date
    campaign_id: str | None
    ran_at: datetime


class ReportScheduler:
    """Runs ``DailyAggregationJob`` on a schedule and tracks run history."""

    def __init__(self, aggregation_job: AggregationJobPort) -> None:
        self._job = aggregation_job
        self._history: list[ScheduledRunRecord] = []

    def run_daily(self, tenant_id: TenantId, day: date, campaign_id: CampaignId | None = None) -> AnalyticsDailyRollup:
        """Run (or re-run) the daily aggregation for ``day`` and record it in history."""
        rollup = self._job.run_for_day(tenant_id, day, campaign_id)
        self._history.append(
            ScheduledRunRecord(
                tenant_id=str(tenant_id),
                day=day,
                campaign_id=str(campaign_id) if campaign_id is not None else None,
                ran_at=datetime.now(UTC),
            )
        )
        return rollup

    def history(self) -> tuple[ScheduledRunRecord, ...]:
        return tuple(self._history)


__all__ = ["AggregationJobPort", "ReportScheduler", "ScheduledRunRecord"]
