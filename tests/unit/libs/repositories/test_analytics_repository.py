"""Unit tests for AnalyticsDailyRepository (Sprint-024, V5 Ch11)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.libs.contracts.models.analytics import AnalyticsDailyRollup
from src.libs.contracts.primitives import CampaignId, TenantId
from src.libs.repositories.analytics import AnalyticsDailyRepository
from tests.fixtures.fake_pg import FakeConnection, FakeCursor

_NOW = datetime(2026, 7, 6, tzinfo=UTC)
_DAY = date(2026, 7, 1)


class TestAnalyticsDailyRepository:
    def test_upsert_tenant_wide_uses_partial_index(self) -> None:
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        repo = AnalyticsDailyRepository(conn)
        rollup = AnalyticsDailyRollup(
            analytics_daily_id="rollup-1",
            tenant_id=TenantId("tenant-a"),
            day=_DAY,
            calls_completed=10,
            ptp_count=3,
            ptp_rate=0.3,
            computed_at=_NOW,
        )

        repo.upsert(rollup)

        sql, _ = cursor.executed[0]
        assert "campaign_id IS NULL" in sql
        assert conn.commit_count == 1

    def test_upsert_per_campaign_uses_the_other_partial_index(self) -> None:
        cursor = FakeCursor()
        repo = AnalyticsDailyRepository(FakeConnection(cursor))
        rollup = AnalyticsDailyRollup(
            analytics_daily_id="rollup-2",
            tenant_id=TenantId("tenant-a"),
            day=_DAY,
            campaign_id=CampaignId("camp-1"),
            computed_at=_NOW,
        )

        repo.upsert(rollup)

        sql, _ = cursor.executed[0]
        assert "campaign_id IS NOT NULL" in sql

    def test_find_for_day_hydrates(self) -> None:
        row = ("rollup-1", "tenant-a", _DAY, None, 10, 3, 0.3, 30_000, 0, 0.5, 0.4, 0.0, _NOW)
        cursor = FakeCursor(fetchall_results=[[row]])
        repo = AnalyticsDailyRepository(FakeConnection(cursor))

        rollup = repo.find_for_day(TenantId("tenant-a"), _DAY)

        assert rollup is not None
        assert rollup.ptp_rate == 0.3
        assert rollup.campaign_id is None

    def test_find_range_orders_by_day(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = AnalyticsDailyRepository(FakeConnection(cursor))
        repo.find_range(TenantId("tenant-a"), _DAY, _DAY)
        sql, _ = cursor.executed[0]
        assert "ORDER BY day" in sql
