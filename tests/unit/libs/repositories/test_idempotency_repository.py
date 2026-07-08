"""Unit tests for IdempotencyRepository (V3 Ch8)."""

from __future__ import annotations

import json

from src.libs.contracts.primitives import TenantId
from src.libs.repositories.idempotency import IdempotencyRepository
from tests.fixtures.fake_pg import FakeConnection, FakeCursor


class TestCheck:
    def test_returns_none_when_key_absent(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = IdempotencyRepository(FakeConnection(cursor))

        assert repo.check(TenantId("tenant-a"), "key-1") is None

    def test_returns_decoded_cached_result(self) -> None:
        cursor = FakeCursor(fetchall_results=[[(json.dumps({"ptp_id": "ptp-1"}),)]])
        repo = IdempotencyRepository(FakeConnection(cursor))

        result = repo.check(TenantId("tenant-a"), "key-1")

        assert result == {"ptp_id": "ptp-1"}

    def test_handles_already_decoded_jsonb_dict(self) -> None:
        """psycopg2 auto-decodes JSONB columns to dict, not a JSON string."""
        cursor = FakeCursor(fetchall_results=[[({"ptp_id": "ptp-1"},)]])
        repo = IdempotencyRepository(FakeConnection(cursor))

        assert repo.check(TenantId("tenant-a"), "key-1") == {"ptp_id": "ptp-1"}

    def test_tenant_scoped_and_excludes_expired(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = IdempotencyRepository(FakeConnection(cursor))

        repo.check(TenantId("tenant-a"), "key-1")

        sql, params = cursor.executed[0]
        assert "tenant_id = %s AND key = %s AND expires_at > NOW()" in sql
        assert params[0] == "tenant-a"


class TestRecord:
    def test_first_call_inserts_and_returns_true(self) -> None:
        cursor = FakeCursor(fetchone_results=[("key-1",)])
        conn = FakeConnection(cursor)
        repo = IdempotencyRepository(conn)

        inserted = repo.record(TenantId("tenant-a"), "key-1", "ptp", {"ptp_id": "ptp-1"})

        assert inserted is True
        assert conn.commit_count == 1
        assert "ON CONFLICT (key) DO NOTHING" in cursor.executed[0][0]

    def test_duplicate_key_returns_false_and_does_not_error(self) -> None:
        """Sprint-014 AC (test_idempotency_double_key): second record() for the same key is a no-op."""
        cursor = FakeCursor(fetchone_results=[None])
        repo = IdempotencyRepository(FakeConnection(cursor))

        inserted = repo.record(TenantId("tenant-a"), "key-1", "ptp", {"ptp_id": "ptp-1"})

        assert inserted is False
