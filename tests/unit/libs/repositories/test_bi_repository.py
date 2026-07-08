"""Unit tests for BIRepository (Sprint-024, V5 Ch21)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.libs.contracts.models.bi import BIFactDaily
from src.libs.contracts.primitives import TenantId
from src.libs.repositories.bi import BIRepository
from tests.fixtures.fake_pg import FakeConnection, FakeCursor

_NOW = datetime(2026, 7, 6, tzinfo=UTC)
_DAY = date(2026, 7, 1)


class TestBIRepository:
    def test_get_or_create_surrogate_key_creates_when_absent(self) -> None:
        cursor = FakeCursor(fetchone_results=[None])
        conn = FakeConnection(cursor)
        repo = BIRepository(conn)

        key = repo.get_or_create_surrogate_key(TenantId("tenant-a"))

        assert key
        assert conn.commit_count == 1
        insert_sql, _ = cursor.executed[1]
        assert "INSERT INTO bi_facts.dim_tenant" in insert_sql

    def test_get_or_create_surrogate_key_reuses_existing(self) -> None:
        cursor = FakeCursor(fetchone_results=[("surrogate-existing",)])
        repo = BIRepository(FakeConnection(cursor))

        key = repo.get_or_create_surrogate_key(TenantId("tenant-a"))

        assert key == "surrogate-existing"

    def test_upsert_fact_daily_commits(self) -> None:
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        repo = BIRepository(conn)
        fact = BIFactDaily(
            fact_daily_id="fact-1",
            tenant_surrogate_key="surrogate-1",
            day=_DAY,
            revenue_minor=1000,
            refreshed_at=_NOW,
        )

        repo.upsert_fact_daily(fact)

        assert "INSERT INTO bi_facts.fact_daily" in cursor.executed[0][0]
        assert conn.commit_count == 1

    def test_find_fact_for_tenant_resolves_surrogate_first(self) -> None:
        dim_row = ("surrogate-1",)
        fact_row = ("fact-1", "surrogate-1", _DAY, 1000, 0, 0, 0, 0, 0.0, 0.0, 0.0, _NOW)
        # Two sequential single-row SELECTs (dim_tenant lookup, then fact_daily lookup) -> two
        # queued fetchone() results, in call order.
        cursor = FakeCursor(fetchone_results=[dim_row, fact_row])
        repo = BIRepository(FakeConnection(cursor))

        fact = repo.find_fact_for_tenant(TenantId("tenant-a"), _DAY)

        assert fact is not None
        assert fact.revenue_minor == 1000

    def test_find_fact_for_tenant_none_when_no_dim_row(self) -> None:
        cursor = FakeCursor(fetchone_results=[None])
        repo = BIRepository(FakeConnection(cursor))
        assert repo.find_fact_for_tenant(TenantId("tenant-a"), _DAY) is None

    def test_find_all_facts_for_day(self) -> None:
        fact_row = ("fact-1", "surrogate-1", _DAY, 1000, 0, 0, 0, 0, 0.0, 0.0, 0.0, _NOW)
        cursor = FakeCursor(fetchall_results=[[fact_row]])
        repo = BIRepository(FakeConnection(cursor))

        facts = repo.find_all_facts_for_day(_DAY)

        assert len(facts) == 1
        assert facts[0].tenant_surrogate_key == "surrogate-1"

    def test_find_facts_for_surrogate_orders_by_day(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = BIRepository(FakeConnection(cursor))
        repo.find_facts_for_surrogate("surrogate-1", _DAY, _DAY)
        sql, _ = cursor.executed[0]
        assert "ORDER BY day" in sql
