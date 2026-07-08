"""PartyService — borrower/co-borrower/guarantor party management (V5 Ch3.3).

Architecture: V5 Ch3.3 (Party Relationships).
"""

from __future__ import annotations

import uuid

from src.libs.contracts.models.customer import Party, PartyRole
from src.libs.contracts.primitives import CustomerId, TenantId
from src.libs.repositories.party import PartyRepository


class PartyService:
    """Manages the parties (co-borrowers, guarantors, nominees) linked to a customer."""

    def __init__(self, repository: PartyRepository) -> None:
        self._repo = repository

    def add_party(self, tenant_id: TenantId, customer_id: CustomerId, role: PartyRole, name: str) -> Party:
        party = Party(
            party_id=str(uuid.uuid4()),
            customer_id=customer_id,
            role=role,
            name=name,
        )
        return self._repo.create(tenant_id, party)

    def list_parties(self, tenant_id: TenantId, customer_id: CustomerId) -> tuple[Party, ...]:
        return self._repo.find_by_customer(tenant_id, customer_id)
