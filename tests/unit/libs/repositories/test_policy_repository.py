"""Unit tests for PolicyRepository — Postgres fallback tier (V4 Ch4, Sprint-017)."""

from __future__ import annotations

from src.libs.repositories.policy import PolicyRepository
from tests.fixtures.fake_pg import FakeConnection, FakeCursor


class TestLoadActiveRuleIds:
    def test_global_scope_query(self) -> None:
        cursor = FakeCursor(fetchall_results=[[("RBI-CALLING-HOURS",), ("RBI-CALLING-FREQUENCY",)]])
        repo = PolicyRepository(FakeConnection(cursor))

        rule_ids = repo.load_active_rule_ids("global", None)

        sql, params = cursor.executed[0]
        assert "scope = %s AND active = TRUE" in sql
        assert params == ("global",)
        assert rule_ids == ("RBI-CALLING-HOURS", "RBI-CALLING-FREQUENCY")

    def test_tenant_scope_query(self) -> None:
        cursor = FakeCursor(fetchall_results=[[("TENANT-RULE-1",)]])
        repo = PolicyRepository(FakeConnection(cursor))

        rule_ids = repo.load_active_rule_ids("tenant", "tenant-1")

        sql, params = cursor.executed[0]
        assert "tenant_id = %s" in sql
        assert params == ("tenant", "tenant-1")
        assert rule_ids == ("TENANT-RULE-1",)

    def test_campaign_scope_query(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = PolicyRepository(FakeConnection(cursor))

        rule_ids = repo.load_active_rule_ids("campaign", "campaign-1")

        sql, params = cursor.executed[0]
        assert "campaign_id = %s" in sql
        assert params == ("campaign", "campaign-1")
        assert rule_ids == ()


class TestUpsertPolicy:
    def test_upsert_commits(self) -> None:
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        repo = PolicyRepository(conn)

        repo.upsert_policy("RBI-CALLING-HOURS", "rbi", "global")

        assert "INSERT INTO policies" in cursor.executed[0][0]
        assert "ON CONFLICT" in cursor.executed[0][0]
        assert conn.commit_count == 1

    def test_upsert_tenant_scope_sets_tenant_id(self) -> None:
        cursor = FakeCursor()
        repo = PolicyRepository(FakeConnection(cursor))

        repo.upsert_policy("CUSTOM-RULE", "custom", "tenant", scope_id="tenant-1")

        _, params = cursor.executed[0]
        assert params == ("CUSTOM-RULE", "custom", "tenant", "tenant-1", None, True)

    def test_upsert_campaign_scope_sets_campaign_id(self) -> None:
        cursor = FakeCursor()
        repo = PolicyRepository(FakeConnection(cursor))

        repo.upsert_policy("CUSTOM-RULE", "custom", "campaign", scope_id="campaign-1")

        _, params = cursor.executed[0]
        assert params == ("CUSTOM-RULE", "custom", "campaign", None, "campaign-1", True)
