"""AudienceSelector — SQL-based cohort builder for campaign dialling lists (V5 Ch6.2).

Architecture: V5 Ch6 (Campaign Management — Audience Selection); V4 Ch2 (consent).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.libs.contracts.context import ConsentStatus
from src.libs.contracts.models.campaign import Campaign, CampaignAudienceMember
from src.libs.contracts.models.consent import ConsentType
from src.libs.contracts.primitives import CustomerId, TenantId

if TYPE_CHECKING:
    from src.libs.repositories.campaign_audience import CampaignAudienceRepository
    from src.libs.repositories.consent import ConsentRepository
    from src.libs.repositories.loan_account import LoanAccountRepository


class AudienceSelector:
    """Builds a campaign's materialized audience cohort (V5 Ch6.2).

    Applies, in order: DPD/outstanding/product filters (SQL cohort query),
    do-not-disturb exclusion, consent validation, and deduplication against
    any campaign the customer already belongs to (a customer is only ever
    assigned to one campaign at a time — first-claimed wins, since the
    ``Campaign`` contract has no explicit priority field to rank against).

    DND has no existing concept anywhere in the codebase (Sprint-023
    finding) — this uses a ``ConsentType.CONTACT`` == ``REVOKED`` proxy
    (revoking contact consent is the closest existing "do not call me"
    signal) rather than inventing a new column ahead of a real DND registry.
    """

    def __init__(
        self,
        loan_account_repository: LoanAccountRepository,
        consent_repository: ConsentRepository | None = None,
        campaign_audience_repository: CampaignAudienceRepository | None = None,
    ) -> None:
        self._loan_repo = loan_account_repository
        self._consent_repo = consent_repository
        self._audience_repo = campaign_audience_repository

    def select(self, tenant_id: TenantId, campaign: Campaign) -> tuple[CampaignAudienceMember, ...]:
        """Select and persist the audience cohort for ``campaign``."""
        criteria = campaign.audience_criteria
        candidates = self._loan_repo.select_cohort(
            tenant_id,
            criteria.min_dpd,
            criteria.max_dpd,
            criteria.min_outstanding_minor,
            criteria.product_types,
        )
        now = datetime.now(UTC)
        members: list[CampaignAudienceMember] = []
        for customer_id, loan_account_id, _dpd in candidates:
            if self._already_in_another_campaign(tenant_id, customer_id, campaign.campaign_id):
                continue
            on_dnd = self._on_dnd(tenant_id, customer_id)
            if criteria.exclude_dnc and on_dnd:
                continue
            if not self._has_consent(tenant_id, customer_id):
                continue
            member = CampaignAudienceMember(
                campaign_audience_id=str(uuid.uuid4()),
                campaign_id=campaign.campaign_id,
                tenant_id=tenant_id,
                customer_id=customer_id,
                loan_account_id=loan_account_id,
                variant_id=None,
                dnd=on_dnd,
                included_at=now,
            )
            if self._audience_repo is not None:
                self._audience_repo.create(member)
            members.append(member)
        return tuple(members)

    def _already_in_another_campaign(self, tenant_id: TenantId, customer_id: str, campaign_id: str) -> bool:
        if self._audience_repo is None:
            return False
        existing = self._audience_repo.find_active_campaigns_for_customer(tenant_id, customer_id)
        return any(cid != campaign_id for cid in existing)

    def _has_consent(self, tenant_id: TenantId, customer_id: str) -> bool:
        if self._consent_repo is None:
            return True
        consent = self._consent_repo.check_consent(tenant_id, CustomerId(customer_id), ConsentType.VOICE_RECORDING)
        return consent is not None and consent.status == ConsentStatus.GRANTED

    def _on_dnd(self, tenant_id: TenantId, customer_id: str) -> bool:
        if self._consent_repo is None:
            return False
        consent = self._consent_repo.check_consent(tenant_id, CustomerId(customer_id), ConsentType.CONTACT)
        return consent is not None and consent.status == ConsentStatus.REVOKED
