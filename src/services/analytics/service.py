"""AnalyticsService — query API facade (V5 Ch11).

Architecture: V5 Ch11 (Analytics Platform).
"""

from __future__ import annotations

from datetime import date, datetime

from src.libs.contracts.models.analytics import AnalyticsDailyRollup
from src.libs.contracts.primitives import CampaignId, TenantId

from .aggregation import DailyAggregationJob
from .call_analytics import CallAnalytics
from .campaign_analytics import CampaignAnalytics
from .realtime import RealtimeAnalytics


class AnalyticsService:
    """Facade composing ``CallAnalytics``/``CampaignAnalytics``/``RealtimeAnalytics``/``DailyAggregationJob``."""

    def __init__(
        self,
        call_analytics: CallAnalytics,
        campaign_analytics: CampaignAnalytics,
        realtime_analytics: RealtimeAnalytics,
        aggregation_job: DailyAggregationJob,
    ) -> None:
        self.calls = call_analytics
        self.campaigns = campaign_analytics
        self.realtime = realtime_analytics
        self.aggregation = aggregation_job

    def campaign_summary(self, tenant_id: TenantId, campaign_id: CampaignId) -> dict[str, float]:
        return {
            "ptp_rate": self.campaigns.ptp_rate(tenant_id, campaign_id),
            "contactability_rate": self.campaigns.contactability_rate(tenant_id, campaign_id),
            "conversion_rate": self.campaigns.conversion_rate(tenant_id, campaign_id),
        }

    def run_daily_aggregation(
        self, tenant_id: TenantId, day: date, campaign_id: CampaignId | None = None
    ) -> AnalyticsDailyRollup:
        return self.aggregation.run_for_day(tenant_id, day, campaign_id)

    def dashboard_snapshot(
        self,
        tenant_id: TenantId,
        window_start: datetime,
        window_end: datetime,
        campaign_id: CampaignId | None = None,
    ) -> dict[str, object]:
        return self.realtime.snapshot(tenant_id, window_start, window_end, campaign_id)


__all__ = ["AnalyticsService"]
