"""CustomerService — authoritative CRM customer CRUD + search (V5 Ch3).

Architecture: V5 Ch3 (Customer CRM); Invariant RI-5 (Law of Authority).
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.libs.contracts.events.saas_events import CustomerCreated
from src.libs.contracts.models.customer import Customer
from src.libs.contracts.primitives import CustomerId, TenantId
from src.libs.event_bus.publisher import Publisher

from . import metrics
from .repository import CRMRepositories


class CustomerService:
    """CRUD + lookup queries over the authoritative ``customers`` domain (V5 Ch3)."""

    def __init__(self, repositories: CRMRepositories, publisher: Publisher | None = None) -> None:
        self._repos = repositories
        self._publisher = publisher

    def create(self, customer: Customer) -> Customer:
        """Create a new customer record. ``customer.crm_id`` must be unique per tenant."""
        created = self._repos.customer.create(customer)
        if self._publisher is not None:
            event = CustomerCreated(
                tenant_id=created.tenant_id,
                customer_id=created.customer_id,
                crm_id=created.crm_id,
                name=created.name,
                preferred_language=created.preferred_language,
            )
            self._publisher.publish(
                event_type=event.event_type,
                tenant_id=created.tenant_id,
                payload=event.model_dump(mode="json"),
                correlation_id=str(created.customer_id),
            )
        return created

    def get(self, tenant_id: TenantId, customer_id: CustomerId) -> Customer | None:
        return self._repos.customer.get(tenant_id, customer_id)

    def find_by_external_id(self, tenant_id: TenantId, crm_id: str) -> Customer | None:
        return self._repos.customer.find_by_external_id(tenant_id, crm_id)

    def find_by_phone(self, tenant_id: TenantId, phone: str) -> Customer | None:
        return self._repos.customer.find_by_phone(tenant_id, phone)

    def list_for_tenant(self, tenant_id: TenantId) -> tuple[Customer, ...]:
        return self._repos.customer.list_for_tenant(tenant_id)

    def record_count(self, tenant_id: TenantId, count: int) -> None:
        """Refresh the ``voiceos_crm_customer_count`` gauge for ``tenant_id`` (V5 §4.17)."""
        metrics.set_customer_count(str(tenant_id), count)

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)
