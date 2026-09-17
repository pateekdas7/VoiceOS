"""Integration test: CampaignRepository.find_all_for_tenant() against real Postgres
(ADR-005 Sec 6.2 -- Client "Campaigns" list).

Skipped when POSTGRES_DSN is not set.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from src.libs.contracts.models.campaign import AudienceCriteria, Campaign, CampaignStatus, RetryPolicy
from src.libs.contracts.models.tenant import Tenant
from src.libs.contracts.primitives import CampaignId, TenantId
from src.libs.repositories.campaign import CampaignRepository
from src.libs.repositories.tenant import TenantRepository
from tests.integration.conftest import requires_postgres

_NOW = datetime(2026, 7, 26, tzinfo=UTC)


def _make_campaign(tenant_id: TenantId, name: str, status: CampaignStatus) -> Campaign:
    return Campaign(
        campaign_id=CampaignId(str(uuid.uuid4())),
        tenant_id=tenant_id,
        name=name,
        status=status,
        audience_criteria=AudienceCriteria(),
        retry_policy=RetryPolicy(),
        created_at=_NOW,
        updated_at=_NOW,
        created_by="integration-test",
    )


@requires_postgres
class TestCampaignRepositoryListAll:
    def test_list_all_includes_every_status(self, pg_conn: Any) -> None:
        tenant_repo = TenantRepository(pg_conn)
        campaign_repo = CampaignRepository(pg_conn)

        tenant = Tenant(
            tenant_id=TenantId(str(uuid.uuid4())),
            slug=f"it-campaigns-{uuid.uuid4()}",
            display_name="IT Campaigns Tenant",
            subscription_tier="GROWTH",
            created_at=_NOW,
            updated_at=_NOW,
        )
        draft = _make_campaign(tenant.tenant_id, "Draft Campaign", CampaignStatus.DRAFT)
        review = _make_campaign(tenant.tenant_id, "Review Campaign", CampaignStatus.REVIEW)

        try:
            tenant_repo.create(tenant)
            campaign_repo.create(draft)
            campaign_repo.create(review)

            all_campaigns = campaign_repo.find_all_for_tenant(tenant.tenant_id)
            names = {c.name for c in all_campaigns}
            assert names == {"Draft Campaign", "Review Campaign"}

            active_only = campaign_repo.find_active_for_tenant(tenant.tenant_id)
            assert active_only == ()
        finally:
            _delete_campaign(pg_conn, draft.campaign_id)
            _delete_campaign(pg_conn, review.campaign_id)
            _delete_tenant(pg_conn, tenant.tenant_id)


def _delete_campaign(conn: Any, campaign_id: str) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM campaigns WHERE campaign_id = %s", (campaign_id,))
    conn.commit()


def _delete_tenant(conn: Any, tenant_id: str) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
    conn.commit()
