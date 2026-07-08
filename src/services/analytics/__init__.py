"""Analytics Platform — call/campaign analytics, real-time dashboards, daily rollups (V5 Ch11, Sprint-024)."""

from __future__ import annotations

from .aggregation import DailyAggregationJob
from .call_analytics import CallAnalytics
from .campaign_analytics import CampaignAnalytics
from .realtime import RealtimeAnalytics
from .service import AnalyticsService

__all__ = [
    "AnalyticsService",
    "CallAnalytics",
    "CampaignAnalytics",
    "DailyAggregationJob",
    "RealtimeAnalytics",
]
