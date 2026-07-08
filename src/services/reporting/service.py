"""ReportingService — report definition + scheduling facade (V5 Ch12).

Architecture: V5 Ch12 (Reporting Platform).
"""

from __future__ import annotations

from datetime import date

from src.libs.contracts.models.analytics import AnalyticsDailyRollup
from src.libs.contracts.primitives import CampaignId, TenantId

from .exporter import ExportService
from .scheduler import ReportScheduler, ScheduledRunRecord
from .templates import ReportData


class ReportingService:
    """Facade composing ``ReportScheduler`` and ``ExportService``."""

    def __init__(self, scheduler: ReportScheduler, exporter: ExportService) -> None:
        self.scheduler = scheduler
        self.exporter = exporter

    def run_scheduled_aggregation(
        self, tenant_id: TenantId, day: date, campaign_id: CampaignId | None = None
    ) -> AnalyticsDailyRollup:
        return self.scheduler.run_daily(tenant_id, day, campaign_id)

    def run_history(self) -> tuple[ScheduledRunRecord, ...]:
        return self.scheduler.history()

    def export(self, report: ReportData, fmt: str) -> bytes:
        return self.exporter.export(report, fmt)


__all__ = ["ReportingService"]
