"""CampaignService — CRUD + lifecycle façade for dialler campaigns (V5 Ch6).

Architecture: V5 Ch6 (Campaign Management).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.models.campaign import (
    ABTestVariant,
    AudienceCriteria,
    Campaign,
    CampaignStatus,
    RetryPolicy,
)
from src.libs.contracts.primitives import CampaignId, TenantId
from src.libs.event_bus.publisher import Publisher

from .lifecycle import CampaignLifecycle

if TYPE_CHECKING:
    from src.libs.repositories.campaign import CampaignRepository


class PromptPinLookupPort(Protocol):
    """Structural port onto ``PromptVersionRepository`` (V5 Ch14) -- lets
    ``CampaignService`` check for a pinned prompt version without depending
    on the concrete AI Configuration repository."""

    def pinned_version_id(self, tenant_id: TenantId, campaign_id: str) -> str | None: ...


class CampaignPromptNotPinnedError(ValueError):
    """Raised by ``activate()`` when the campaign has no pinned prompt version
    (Sprint-025 Part-3: "every campaign references a pinned immutable prompt
    version rather than editable prompt text")."""


class CampaignService:
    """CRUD + lifecycle transitions for campaigns (V5 Ch6).

    ``prompt_pins`` is additive (Sprint-025 Part-3): when wired, ``activate()``
    refuses to promote a campaign to ACTIVE until a prompt version has been
    pinned via ``PromptVersioningService.pin()`` -- ``None`` preserves
    pre-Sprint-025 behavior (activation never checks for a pin).
    """

    def __init__(
        self,
        repository: CampaignRepository,
        publisher: Publisher | None = None,
        audit_logger: AuditLogger | None = None,
        prompt_pins: PromptPinLookupPort | None = None,
    ) -> None:
        self._repo = repository
        self._publisher = publisher
        self._audit_logger = audit_logger
        self._prompt_pins = prompt_pins

    def create(
        self,
        tenant_id: TenantId,
        name: str,
        audience_criteria: AudienceCriteria,
        retry_policy: RetryPolicy,
        created_by: str,
        *,
        description: str = "",
        daily_start_hour: int = 9,
        daily_end_hour: int = 18,
        timezone: str = "Asia/Kolkata",
        default_strategy: str = "",
    ) -> Campaign:
        """Create a new campaign in ``DRAFT`` status."""
        now = datetime.now(UTC)
        campaign = Campaign(
            campaign_id=CampaignId(str(uuid.uuid4())),
            tenant_id=tenant_id,
            name=name,
            description=description,
            status=CampaignStatus.DRAFT,
            audience_criteria=audience_criteria,
            retry_policy=retry_policy,
            daily_start_hour=daily_start_hour,
            daily_end_hour=daily_end_hour,
            timezone=timezone,
            default_strategy=default_strategy,
            created_at=now,
            updated_at=now,
            created_by=created_by,
        )
        return self._repo.create(campaign)

    def add_variant(self, tenant_id: TenantId, campaign_id: CampaignId, variant: ABTestVariant) -> ABTestVariant:
        """Register an A/B test variant for a campaign (any status before ACTIVE)."""
        return self._repo.create_variant(campaign_id, variant)

    def get(self, tenant_id: TenantId, campaign_id: CampaignId) -> Campaign | None:
        return self._repo.get(tenant_id, campaign_id)

    def find_active(self, tenant_id: TenantId) -> tuple[Campaign, ...]:
        return self._repo.find_active_for_tenant(tenant_id)

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def submit_for_review(self, tenant_id: TenantId, campaign_id: CampaignId) -> Campaign:
        return self._transition(tenant_id, campaign_id, CampaignStatus.REVIEW, actor_id="system")

    def approve(self, tenant_id: TenantId, campaign_id: CampaignId, approved_by: str) -> Campaign:
        return self._transition(tenant_id, campaign_id, CampaignStatus.APPROVED, actor_id=approved_by)

    def activate(
        self, tenant_id: TenantId, campaign_id: CampaignId, started_by: str, target_call_count: int
    ) -> Campaign:
        """APPROVED -> ACTIVE. Publishes ``CampaignStarted`` (V5 Ch6).

        Raises :class:`CampaignPromptNotPinnedError` when ``prompt_pins`` is
        wired and no prompt version has been pinned for this campaign yet.
        """
        if self._prompt_pins is not None and self._prompt_pins.pinned_version_id(tenant_id, campaign_id) is None:
            raise CampaignPromptNotPinnedError(
                f"campaign {campaign_id} has no pinned prompt version -- "
                "call PromptVersioningService.pin() before activation"
            )
        campaign = self._transition(tenant_id, campaign_id, CampaignStatus.ACTIVE, actor_id=started_by)
        self._repo.update_counts(tenant_id, campaign_id, target_call_count, campaign.completed_call_count)
        if self._publisher is not None:
            self._publisher.publish(
                event_type="saas.campaign.started",
                tenant_id=tenant_id,
                payload={
                    "campaign_id": campaign_id,
                    "campaign_name": campaign.name,
                    "target_call_count": target_call_count,
                    "started_by": started_by,
                },
                correlation_id=campaign_id,
            )
        return self._require(tenant_id, campaign_id)

    def pause(self, tenant_id: TenantId, campaign_id: CampaignId) -> Campaign:
        return self._transition(tenant_id, campaign_id, CampaignStatus.PAUSED, actor_id="system")

    def resume(self, tenant_id: TenantId, campaign_id: CampaignId) -> Campaign:
        return self._transition(tenant_id, campaign_id, CampaignStatus.ACTIVE, actor_id="system")

    def complete(self, tenant_id: TenantId, campaign_id: CampaignId) -> Campaign:
        """ACTIVE -> COMPLETED. Publishes ``CampaignCompleted`` (V5 Ch6, Sprint-025)."""
        campaign = self._transition(tenant_id, campaign_id, CampaignStatus.COMPLETED, actor_id="system")
        if self._publisher is not None:
            self._publisher.publish(
                event_type="saas.campaign.completed",
                tenant_id=tenant_id,
                payload={
                    "campaign_id": campaign_id,
                    "completed_call_count": campaign.completed_call_count,
                },
                correlation_id=campaign_id,
            )
        return campaign

    def archive(self, tenant_id: TenantId, campaign_id: CampaignId) -> Campaign:
        return self._transition(tenant_id, campaign_id, CampaignStatus.ARCHIVED, actor_id="system")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _transition(
        self, tenant_id: TenantId, campaign_id: CampaignId, target: CampaignStatus, *, actor_id: str
    ) -> Campaign:
        campaign = self._require(tenant_id, campaign_id)
        CampaignLifecycle.validate_transition(campaign.status, target)
        self._repo.update_status(tenant_id, campaign_id, target)
        if self._audit_logger is not None:
            self._audit_logger.record(
                tenant_id,
                actor_id,
                "campaign.lifecycle_transition",
                "Campaign",
                campaign_id,
                "SUCCESS",
                event_payload={"from": campaign.status.value, "to": target.value},
            )
        return self._require(tenant_id, campaign_id)

    def _require(self, tenant_id: TenantId, campaign_id: CampaignId) -> Campaign:
        campaign = self._repo.get(tenant_id, campaign_id)
        if campaign is None:
            raise ValueError(f"campaign not found: {campaign_id}")
        return campaign
