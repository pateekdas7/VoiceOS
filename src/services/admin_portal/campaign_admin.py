"""CampaignAdminController -- campaign CRUD + approval workflow (V5 Ch13).

Architecture: V5 Ch13 (Administration Portal); V5 Ch6 (Campaign Management).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.libs.contracts.models.campaign import Campaign
from src.libs.contracts.primitives import CampaignId, TenantId
from src.services.policy_engine.decision import PolicyOutcome

if TYPE_CHECKING:
    from src.services.campaign_management.service import CampaignService
    from src.services.policy_engine.service import PolicyEngineService

_DENIED_OUTCOMES = (PolicyOutcome.DENY, PolicyOutcome.FORBID)


class CampaignApprovalDeniedError(PermissionError):
    """Raised when the Policy Engine denies a campaign-approval request (V5 Ch13)."""


class CampaignAdminController:
    """Campaign read + lifecycle-approval administration (V5 Ch13).

    ``approve`` is PolicyEngine-validated (Sprint-025.md: "every
    administrative mutation ... is PolicyEngine validated where
    applicable") via ``AdminPolicyPack.CAMPAIGN_APPROVAL_REQUIRES_REVIEW`` —
    a campaign that has not been submitted for review cannot be approved,
    expressed as a PDP rule rather than an ad hoc state check.
    """

    def __init__(self, campaign_service: CampaignService, policy_engine: PolicyEngineService | None = None) -> None:
        self._campaigns = campaign_service
        self._policy = policy_engine

    def list_active_campaigns(self, tenant_id: TenantId) -> tuple[Campaign, ...]:
        return self._campaigns.find_active(tenant_id)

    def get_campaign(self, tenant_id: TenantId, campaign_id: CampaignId) -> Campaign | None:
        return self._campaigns.get(tenant_id, campaign_id)

    def submit_for_review(self, tenant_id: TenantId, campaign_id: CampaignId) -> Campaign:
        return self._campaigns.submit_for_review(tenant_id, campaign_id)

    def approve(self, tenant_id: TenantId, campaign_id: CampaignId, approved_by: str) -> Campaign:
        if self._policy is not None:
            campaign = self._campaigns.get(tenant_id, campaign_id)
            status = campaign.status.value if campaign is not None else ""
            decision = self._policy.check_campaign_approval(str(tenant_id), campaign_id, status, subject=approved_by)
            if decision.outcome in _DENIED_OUTCOMES:
                raise CampaignApprovalDeniedError(decision.reason)
        return self._campaigns.approve(tenant_id, campaign_id, approved_by)


__all__ = ["CampaignAdminController", "CampaignApprovalDeniedError"]
