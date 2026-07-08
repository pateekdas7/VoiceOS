"""Unit tests for AuditRepository — append-only immutability (V4 Ch11)."""

from __future__ import annotations

import pytest

from src.libs.contracts.primitives import TenantId
from src.libs.repositories.audit import AuditRepository, ImmutableAuditLogError
from tests.fixtures.fake_pg import FakeConnection, FakeCursor


class TestAppend:
    def test_inserts_and_commits(self) -> None:
        cursor = FakeCursor(fetchone_results=[None])
        conn = FakeConnection(cursor)
        repo = AuditRepository(conn)

        repo.append(TenantId("tenant-a"), "agent-1", "customer.create", "customer", "cust-1", "SUCCESS")

        insert_statements = [sql for sql, _params in cursor.executed if "INSERT INTO audit_log" in sql]
        assert len(insert_statements) == 1
        assert conn.commit_count == 1

    def test_serializes_event_payload_as_jsonb(self) -> None:
        cursor = FakeCursor(fetchone_results=[None])
        repo = AuditRepository(FakeConnection(cursor))

        repo.append(
            TenantId("tenant-a"),
            "agent-1",
            "customer.create",
            "customer",
            "cust-1",
            "SUCCESS",
            event_payload={"field": "value"},
        )

        _sql, params = next(entry for entry in cursor.executed if "INSERT INTO audit_log" in entry[0])
        assert '"field": "value"' in params[7]

    def test_chains_hash_from_prior_row(self) -> None:
        """Sprint-020 (V4 Ch11): append() reads the tenant's latest hash and chains off it."""
        cursor = FakeCursor(fetchone_results=[("a" * 64,)])
        repo = AuditRepository(FakeConnection(cursor))

        new_hash = repo.append(TenantId("tenant-a"), "agent-1", "customer.create", "customer", "cust-1", "SUCCESS")

        select_sql, _ = cursor.executed[0]
        assert "FOR UPDATE" in select_sql
        _insert_sql, params = cursor.executed[1]
        assert params[-2] == "a" * 64  # prev_hash
        assert params[-1] == new_hash  # hash

    def test_first_event_for_tenant_chains_from_genesis(self) -> None:
        """No prior row for this tenant -> prev_hash is the genesis hash, not NULL."""
        from src.libs.repositories.audit import GENESIS_HASH

        cursor = FakeCursor(fetchone_results=[None])
        repo = AuditRepository(FakeConnection(cursor))

        repo.append(TenantId("tenant-a"), "agent-1", "customer.create", "customer", "cust-1", "SUCCESS")

        _insert_sql, params = cursor.executed[1]
        assert params[-2] == GENESIS_HASH


class TestImmutability:
    def test_update_always_raises(self) -> None:
        """Sprint-014 AC (test_audit_no_update): AuditRepository.update() raises."""
        repo = AuditRepository(FakeConnection())

        with pytest.raises(ImmutableAuditLogError, match="append-only"):
            repo.update()

    def test_delete_always_raises(self) -> None:
        repo = AuditRepository(FakeConnection())

        with pytest.raises(ImmutableAuditLogError, match="append-only"):
            repo.delete()

    def test_update_raises_before_touching_the_connection(self) -> None:
        """No SQL is ever issued for update() — the guard fires in pure Python."""
        cursor = FakeCursor()
        repo = AuditRepository(FakeConnection(cursor))

        with pytest.raises(ImmutableAuditLogError):
            repo.update(anything="ignored")

        assert cursor.executed == []


class TestFindByResource:
    def test_scopes_by_tenant_resource_type_and_id(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = AuditRepository(FakeConnection(cursor))

        repo.find_by_resource(TenantId("tenant-a"), "customer", "cust-1")

        sql, params = cursor.executed[0]
        assert "tenant_id = %s AND resource_type = %s AND resource_id = %s" in sql
        assert params == ("tenant-a", "customer", "cust-1")
