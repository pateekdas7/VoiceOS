"""Persistent data models for dialler campaign management.

Campaigns are the scheduling and orchestration unit for outbound collection
calls. They define who to call, when, how often to retry, and what
strategy to apply.

Architecture: V5 Ch6 (Campaign Engine); V5 Ch4 (Collections).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import CampaignId, TenantId


class CampaignStatus(StrEnum):
    """Lifecycle status of a campaign (V5 Ch6, reworked Sprint-023).

    Migration 0011 (Sprint-014) shipped a placeholder 6-value set
    (``DRAFT``/``SCHEDULED``/``ACTIVE``/``PAUSED``/``COMPLETED``/
    ``CANCELLED``) ahead of the service that actually enforces campaign
    lifecycle transitions. Sprint-023's ``CampaignLifecycle`` requires a
    review/approval gate before a campaign may go ``ACTIVE``, so migration
    0020 reworks the underlying CHECK constraint to this 7-state machine
    (same "complete the partially-built column" precedent as migration
    0018's ``tenants.status`` rework — see CHANGELOG.md).
    """

    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class RetryPolicy(BaseModel):
    """Retry configuration for a campaign (V5 Ch6.4)."""

    model_config = ConfigDict(frozen=True)

    max_attempts: int = Field(default=3, ge=1, le=10)
    """Maximum call attempts per contact per campaign."""
    retry_interval_hours: int = Field(default=24, ge=1)
    """Minimum hours between retry attempts for the same contact."""
    retry_on_outcomes: tuple[str, ...] = Field(default=())
    """Disposition codes that trigger a retry (empty = retry on all non-terminal codes)."""
    do_not_retry_on_outcomes: tuple[str, ...] = Field(default=())
    """Disposition codes that permanently stop retries for a contact."""


class AudienceCriteria(BaseModel):
    """Filter criteria used to build the campaign dialling list (V5 Ch6.2)."""

    model_config = ConfigDict(frozen=True)

    min_dpd: int = Field(default=0, ge=0)
    """Minimum days-past-due to include in campaign."""
    max_dpd: int | None = None
    """Maximum days-past-due (None = no upper limit)."""
    product_types: tuple[str, ...] = Field(default=())
    """Product types to include (empty = all products)."""
    min_outstanding_minor: int = Field(default=0, ge=0)
    """Minimum outstanding amount in minor currency units."""
    exclude_ptp_active: bool = True
    """Exclude customers who already have an active PTP."""
    exclude_dnc: bool = True
    """Exclude contacts on the do-not-contact list."""


class ABTestVariant(BaseModel):
    """A/B test variant configuration for campaign strategy experiments (V5 Ch6.6)."""

    model_config = ConfigDict(frozen=True)

    variant_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    """Human-readable variant name (e.g. 'control', 'treatment_a')."""
    strategy_override: str = ""
    """StrategyLabel override for this variant (empty = use campaign default)."""
    traffic_weight: int = Field(default=50, ge=1, le=100)
    """Percentage of traffic allocated to this variant (must sum to 100 across variants)."""


class Campaign(BaseModel):
    """A dialler campaign targeting a segment of delinquent customers (V5 Ch6).

    The campaign defines the audience, schedule, retry policy, and call
    strategy. Active campaigns drive the dialler queue. Completed campaigns
    feed the analytics pipeline.
    """

    model_config = ConfigDict(frozen=True)

    campaign_id: CampaignId
    tenant_id: TenantId
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    status: CampaignStatus = CampaignStatus.DRAFT
    audience_criteria: AudienceCriteria
    retry_policy: RetryPolicy
    scheduled_start: datetime | None = None
    """UTC timestamp when the campaign should begin dialling (None = manual start)."""
    scheduled_end: datetime | None = None
    """UTC timestamp when dialling should stop (None = until audience exhausted)."""
    daily_start_hour: int = Field(default=9, ge=0, le=23)
    """Hour of day (tenant timezone) when dialling may begin."""
    daily_end_hour: int = Field(default=18, ge=0, le=23)
    """Hour of day (tenant timezone) when dialling must stop."""
    timezone: str = Field(default="Asia/Kolkata")
    """IANA timezone for daily window calculation."""
    default_strategy: str = ""
    """Default StrategyLabel applied to all calls in this campaign."""
    ab_variants: tuple[ABTestVariant, ...] = Field(default=())
    """A/B test variants (empty = no A/B testing)."""
    target_call_count: int = Field(default=0, ge=0)
    """Audience size after filtering (populated at campaign launch)."""
    completed_call_count: int = Field(default=0, ge=0)
    """Number of completed call attempts so far."""
    created_at: datetime
    updated_at: datetime
    created_by: str = Field(min_length=1)


class CampaignAudienceMember(BaseModel):
    """One customer's membership in a campaign's materialized cohort (Sprint-023).

    Produced by ``AudienceSelector`` and persisted so a customer is never
    dialled by more than one campaign at a time (dedup to the
    highest-priority campaign) and so DND/exclusion state is auditable.
    """

    model_config = ConfigDict(frozen=True)

    campaign_audience_id: str
    campaign_id: CampaignId
    tenant_id: TenantId
    customer_id: str
    loan_account_id: str | None = None
    variant_id: str | None = None
    """Assigned A/B variant, if the campaign has ``ab_variants`` configured."""
    dnd: bool = False
    """Whether this customer is on the do-not-disturb list (excludes from dialling)."""
    included_at: datetime
    excluded_reason: str = ""
    """Non-empty when this member was excluded post-selection (e.g. 'DND', 'DUPLICATE_HIGHER_PRIORITY')."""


class CampaignResult(BaseModel):
    """Outcome of a single campaign contact attempt (Sprint-023).

    Feeds ``ABTestingFramework``'s per-variant PTP-rate/completion-rate
    metrics and the campaign's ``completed_call_count``.
    """

    model_config = ConfigDict(frozen=True)

    campaign_result_id: str
    campaign_id: CampaignId
    tenant_id: TenantId
    variant_id: str | None = None
    customer_id: str
    call_id: str | None = None
    outcome_code: str
    """Stable disposition code — same vocabulary as ``CallDispositioned.outcome_code``."""
    ptp_created: bool = False
    completed_at: datetime
    created_at: datetime


__all__ = [
    "ABTestVariant",
    "AudienceCriteria",
    "Campaign",
    "CampaignAudienceMember",
    "CampaignResult",
    "CampaignStatus",
    "RetryPolicy",
]
