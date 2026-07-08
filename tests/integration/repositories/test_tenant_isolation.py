"""Integration test: tenant isolation enforcement (AR-8, Sprint-014 AC).

Required named test: test_tenant_isolation_customer.

Skipped when POSTGRES_DSN is not set.
Architecture: V6 Ch7; AR-8 (tenant isolation).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from src.libs.contracts.models.customer import Customer
from src.libs.contracts.primitives import CustomerId, TenantId
from src.libs.repositories.customer import CustomerRepository
from tests.integration.conftest import requires_postgres

_NOW = datetime(2026, 7, 4, tzinfo=UTC)


@requires_postgres
class TestTenantIsolation:
    def test_tenant_isolation_customer(self, pg_conn: Any) -> None:
        """A customer created under tenant-1 must not be visible from tenant-2."""
        repo = CustomerRepository(pg_conn)
        tenant_1 = TenantId(str(uuid.uuid4()))
        tenant_2 = TenantId(str(uuid.uuid4()))
        customer_id = CustomerId(str(uuid.uuid4()))
        shared_crm_id = f"crm-{uuid.uuid4()}"

        try:
            repo.create(
                Customer(
                    customer_id=customer_id,
                    tenant_id=tenant_1,
                    crm_id=shared_crm_id,
                    name="Tenant-1 Customer",
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )

            # Visible to its own tenant.
            assert repo.get(tenant_1, customer_id) is not None
            assert repo.find_by_external_id(tenant_1, shared_crm_id) is not None

            # Invisible under a different tenant — same customer_id, same crm_id.
            assert repo.get(tenant_2, customer_id) is None
            assert repo.find_by_external_id(tenant_2, shared_crm_id) is None
        finally:
            cur = pg_conn.cursor()
            cur.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
            pg_conn.commit()

    def test_find_by_phone_does_not_leak_across_tenants(self, pg_conn: Any) -> None:
        repo = CustomerRepository(pg_conn)
        tenant_1 = TenantId(str(uuid.uuid4()))
        tenant_2 = TenantId(str(uuid.uuid4()))
        customer_id = CustomerId(str(uuid.uuid4()))
        phone = f"+91{uuid.uuid4().int % 10_000_000_000:010d}"

        from src.libs.contracts.models.customer import CustomerContact

        try:
            repo.create(
                Customer(
                    customer_id=customer_id,
                    tenant_id=tenant_1,
                    crm_id=f"crm-{uuid.uuid4()}",
                    name="Tenant-1 Customer",
                    contacts=(CustomerContact(contact_type="MOBILE", value=phone, is_primary=True),),
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )

            assert repo.find_by_phone(tenant_1, phone) is not None
            assert repo.find_by_phone(tenant_2, phone) is None
        finally:
            cur = pg_conn.cursor()
            cur.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
            pg_conn.commit()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
