"""CampaignAudienceRepository — materialized campaign cohort records (Sprint-023).

Architecture: V5 Ch6 (Campaign Engine — audience selection).
"""

from __future__ import annotations

from typing import Any

from ..contracts.models.campaign import CampaignAudienceMember
from ..contracts.primitives import CampaignId, TenantId
from .base import BaseRepository

_TABLE = "campaign_audiences"

_COLUMNS = (
    "campaign_audience_id",
    "campaign_id",
    "tenant_id",
    "customer_id",
    "loan_account_id",
    "variant_id",
    "dnd",
    "included_at",
    "excluded_reason",
)


class CampaignAudienceRepository(BaseRepository):
    """Tenant-scoped CRUD for a campaign's materialized audience cohort."""

    def create(self, member: CampaignAudienceMember) -> CampaignAudienceMember:
        """Insert one audience member. Raises on a (campaign_id, customer_id) duplicate."""
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                campaign_audience_id, campaign_id, tenant_id, customer_id,
                loan_account_id, variant_id, dnd, included_at, excluded_reason
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                member.campaign_audience_id,
                member.campaign_id,
                member.tenant_id,
                member.customer_id,
                member.loan_account_id,
                member.variant_id,
                member.dnd,
                member.included_at,
                member.excluded_reason,
            ),
        )
        self._commit()
        return member

    def find_by_campaign(self, tenant_id: TenantId, campaign_id: CampaignId) -> tuple[CampaignAudienceMember, ...]:
        """All audience members for one campaign, scoped to ``tenant_id``."""
        rows = self._tenant_select(
            _TABLE,
            _COLUMNS,
            tenant_id,
            extra_where="campaign_id = %s",
            extra_params=(campaign_id,),
            order_by="included_at ASC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def find_active_campaigns_for_customer(self, tenant_id: TenantId, customer_id: str) -> tuple[str, ...]:
        """Campaign IDs a customer is currently a member of (any exclusion status), for dedup checks."""
        rows = self._tenant_select(
            _TABLE,
            ("campaign_id",),
            tenant_id,
            extra_where="customer_id = %s",
            extra_params=(customer_id,),
        )
        return tuple(row[0] for row in rows)

    def get(self, tenant_id: TenantId, campaign_id: CampaignId, customer_id: str) -> CampaignAudienceMember | None:
        row = self._tenant_select_one(
            _TABLE,
            _COLUMNS,
            tenant_id,
            extra_where="campaign_id = %s AND customer_id = %s",
            extra_params=(campaign_id, customer_id),
        )
        return self._hydrate(row) if row is not None else None

    def exclude(self, tenant_id: TenantId, campaign_id: CampaignId, customer_id: str, reason: str) -> None:
        """Mark an audience member excluded (e.g. dedup to a higher-priority campaign)."""
        self._tenant_update(
            _TABLE,
            ("excluded_reason",),
            (reason,),
            tenant_id,
            extra_where="campaign_id = %s AND customer_id = %s",
            extra_params=(campaign_id, customer_id),
        )

    def _hydrate(self, row: tuple[Any, ...]) -> CampaignAudienceMember:
        (
            campaign_audience_id,
            campaign_id,
            tenant_id,
            customer_id,
            loan_account_id,
            variant_id,
            dnd,
            included_at,
            excluded_reason,
        ) = row
        return CampaignAudienceMember(
            campaign_audience_id=str(campaign_audience_id),
            campaign_id=CampaignId(str(campaign_id)),
            tenant_id=TenantId(str(tenant_id)),
            customer_id=str(customer_id),
            loan_account_id=loan_account_id,
            variant_id=str(variant_id) if variant_id is not None else None,
            dnd=dnd,
            included_at=included_at,
            excluded_reason=excluded_reason or "",
        )
