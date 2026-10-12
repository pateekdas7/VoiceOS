"""Integration test: TenantRepository.list_all() against real Postgres (ADR-005 Sec 6.1).

Skipped when POSTGRES_DSN is not set.
Architecture: ADR-005 Sec 6.1 (Admin Dashboard "Clients").
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from src.libs.contracts.models.tenant import Tenant, TenantStatus
from src.libs.contracts.primitives import TenantId
from src.libs.repositories.tenant import TenantRepository
from tests.integration.conftest import requires_postgres

_NOW = datetime(2026, 7, 26, tzinfo=UTC)


def _make_tenant(slug: str) -> Tenant:
    return Tenant(
        tenant_id=TenantId(str(uuid.uuid4())),
        slug=slug,
        display_name=f"IT {slug}",
        subscription_tier="GROWTH",
        status=TenantStatus.TRIAL,
        created_at=_NOW,
        updated_at=_NOW,
    )


@requires_postgres
class TestTenantRepositoryListAll:
    def test_list_all_includes_newly_created_tenants(self, pg_conn: Any) -> None:
        repo = TenantRepository(pg_conn)
        tenant_a = _make_tenant(f"it-a-{uuid.uuid4()}")
        tenant_b = _make_tenant(f"it-b-{uuid.uuid4()}")

        try:
            repo.create(tenant_a)
            repo.create(tenant_b)

            all_tenants = repo.list_all()
            slugs = {t.slug for t in all_tenants}
            assert tenant_a.slug in slugs
            assert tenant_b.slug in slugs
        finally:
            _delete(pg_conn, tenant_a.tenant_id)
            _delete(pg_conn, tenant_b.tenant_id)


def _delete(conn: Any, tenant_id: str) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
    conn.commit()
