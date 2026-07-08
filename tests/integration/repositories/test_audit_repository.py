"""Integration test: AuditRepository against real Postgres (V4 Ch11).

Required named test: test_audit_no_update.

Also validates the DB-level defense-in-depth trigger (migration 0010):
direct SQL UPDATE/DELETE against audit_log is rejected even bypassing the
repository layer entirely.

Skipped when POSTGRES_DSN is not set.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg2
import pytest

from src.libs.contracts.primitives import TenantId
from src.libs.repositories.audit import AuditRepository, ImmutableAuditLogError
from tests.integration.conftest import requires_postgres


@requires_postgres
class TestAuditRepositoryImmutability:
    def test_audit_no_update(self, pg_conn: Any) -> None:
        """AuditRepository.update() raises — the required Sprint-014 AC test."""
        repo = AuditRepository(pg_conn)

        with pytest.raises(ImmutableAuditLogError):
            repo.update()

    def test_audit_no_delete(self, pg_conn: Any) -> None:
        repo = AuditRepository(pg_conn)

        with pytest.raises(ImmutableAuditLogError):
            repo.delete()

    def test_append_and_find_by_resource_round_trip(self, pg_conn: Any) -> None:
        repo = AuditRepository(pg_conn)
        tenant_id = TenantId(str(uuid.uuid4()))
        resource_id = str(uuid.uuid4())

        repo.append(tenant_id, "agent-1", "customer.create", "customer", resource_id, "SUCCESS")

        rows = repo.find_by_resource(tenant_id, "customer", resource_id)
        assert len(rows) == 1
        assert rows[0][2] == "agent-1"  # actor_id column

    def test_db_trigger_rejects_direct_sql_update(self, pg_conn: Any) -> None:
        """Defense-in-depth: even a raw UPDATE bypassing the repository is rejected."""
        tenant_id = str(uuid.uuid4())
        cur = pg_conn.cursor()
        cur.execute(
            "INSERT INTO audit_log (tenant_id, actor_id, action, resource_type, resource_id, outcome) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING audit_id",
            (tenant_id, "agent-1", "customer.create", "customer", "res-1", "SUCCESS"),
        )
        audit_id = cur.fetchone()[0]
        pg_conn.commit()

        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute("UPDATE audit_log SET outcome = 'FAILURE' WHERE audit_id = %s", (audit_id,))
        pg_conn.rollback()

    def test_db_trigger_rejects_direct_sql_delete(self, pg_conn: Any) -> None:
        tenant_id = str(uuid.uuid4())
        cur = pg_conn.cursor()
        cur.execute(
            "INSERT INTO audit_log (tenant_id, actor_id, action, resource_type, resource_id, outcome) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING audit_id",
            (tenant_id, "agent-1", "customer.create", "customer", "res-1", "SUCCESS"),
        )
        audit_id = cur.fetchone()[0]
        pg_conn.commit()

        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute("DELETE FROM audit_log WHERE audit_id = %s", (audit_id,))
        pg_conn.rollback()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
