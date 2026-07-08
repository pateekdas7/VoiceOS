"""Persistent data models for the Analytics Platform (Sprint-024, V5 Ch11).

Architecture: V5 Ch11 (Analytics Platform); V5 Ch4.5 (call disposition
vocabulary, shared with the ``saas.call.dispositioned`` domain event).
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import CallId, CampaignId, CustomerId, TenantId


class CallDisposition(BaseModel):
    """A single call's terminal outcome (V5 Ch4.5), the raw fact ``CallAnalytics``
    aggregates from.

    Persisted to the ``call_dispositions`` table (created by migration 0011,
    Sprint-014; had no repository until Sprint-024).
    """

    model_config = ConfigDict(frozen=True)

    disposition_id: str = Field(min_length=1)
    tenant_id: TenantId
    call_id: CallId
    customer_id: CustomerId
    loan_account_id: str
    outcome_code: str
    """Stable disposition code (e.g. 'PTP_MADE', 'PROMISE_BROKEN', 'NOT_REACHABLE')."""
    duration_ms: int = Field(ge=0)
    dispositioned_at: datetime


class AnalyticsDailyRollup(BaseModel):
    """One day's pre-computed rollup — tenant-wide (``campaign_id=None``) or
    per-campaign (V5 Ch11, ``DailyAggregationJob``).

    Persisted to the ``analytics_daily`` table (migration 0022).
    """

    model_config = ConfigDict(frozen=True)

    analytics_daily_id: str = Field(min_length=1)
    tenant_id: TenantId
    day: date
    campaign_id: CampaignId | None = None
    calls_completed: int = Field(default=0, ge=0)
    ptp_count: int = Field(default=0, ge=0)
    ptp_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    avg_duration_ms: int = Field(default=0, ge=0)
    amount_collected_minor: int = Field(default=0, ge=0)
    contactability_rate: float = Field(default=0.0, ge=0.0)
    recovery_rate: float = Field(default=0.0, ge=0.0)
    avg_dpd: float = Field(default=0.0, ge=0.0)
    computed_at: datetime


__all__ = [
    "AnalyticsDailyRollup",
    "CallDisposition",
]
