"""Unit tests for CampaignRepository (V5 Ch6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from src.libs.contracts.models.campaign import AudienceCriteria, Campaign, CampaignStatus, RetryPolicy
from src.libs.contracts.primitives import CampaignId, TenantId
from src.libs.repositories.campaign import CampaignRepository
from tests.fixtures.fake_pg import FakeConnection, FakeCursor

_NOW = datetime(2026, 7, 4, tzinfo=UTC)

_CAMPAIGN_ROW: tuple[Any, ...] = (
    "camp-1",
    "tenant-a",
    "July Collections",
    "",
    "ACTIVE",
    json.dumps({"min_dpd": 30}),
    3,
    24,
    [],
    [],
    None,
    None,
    9,
    18,
    "Asia/Kolkata",
    "",
    100,
    0,
    _NOW,
    _NOW,
    "admin-1",
)


def _campaign() -> Campaign:
    return Campaign(
        campaign_id=CampaignId("camp-1"),
        tenant_id=TenantId("tenant-a"),
        name="July Collections",
        status=CampaignStatus.ACTIVE,
        audience_criteria=AudienceCriteria(min_dpd=30),
        retry_policy=RetryPolicy(),
        created_at=_NOW,
        updated_at=_NOW,
        created_by="admin-1",
    )


class TestCreate:
    def test_inserts_and_commits(self) -> None:
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        repo = CampaignRepository(conn)

        repo.create(_campaign())

        assert "INSERT INTO campaigns" in cursor.executed[0][0]
        assert conn.commit_count == 1


class TestGet:
    def test_hydrates_campaign(self) -> None:
        cursor = FakeCursor(fetchall_results=[[_CAMPAIGN_ROW]])
        repo = CampaignRepository(FakeConnection(cursor))

        campaign = repo.get(TenantId("tenant-a"), "camp-1")

        assert campaign is not None
        assert campaign.status == CampaignStatus.ACTIVE
        assert campaign.audience_criteria.min_dpd == 30


class TestFindActiveForTenant:
    def test_filters_to_active_status(self) -> None:
        cursor = FakeCursor(fetchall_results=[[_CAMPAIGN_ROW]])
        repo = CampaignRepository(FakeConnection(cursor))

        campaigns = repo.find_active_for_tenant(TenantId("tenant-a"))

        assert len(campaigns) == 1
        sql, params = cursor.executed[0]
        assert "status = %s" in sql
        assert params[-1] == "ACTIVE"


class TestUpdateStatus:
    def test_scoped_update(self) -> None:
        cursor = FakeCursor()
        repo = CampaignRepository(FakeConnection(cursor))

        repo.update_status(TenantId("tenant-a"), "camp-1", CampaignStatus.PAUSED)

        sql, params = cursor.executed[0]
        assert "WHERE tenant_id = %s AND campaign_id = %s" in sql
        assert params == ("PAUSED", "tenant-a", "camp-1")
