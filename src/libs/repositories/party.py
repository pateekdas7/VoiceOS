"""PartyRepository — borrower/co-borrower/guarantor party records (V5 Ch3.3).

Architecture: V5 Ch3.3 (Party Relationships); AR-8 (tenant isolation).
"""

from __future__ import annotations

from typing import Any

from ..contracts.models.customer import Party, PartyRole
from ..contracts.primitives import CustomerId, TenantId
from .base import BaseRepository

_TABLE = "parties"

_PARTY_COLUMNS = ("party_id", "customer_id", "role", "name")


class PartyRepository(BaseRepository):
    """Tenant-scoped CRUD for the ``parties`` domain (V5 Ch3.3).

    The ``parties`` table (migration 0004) stores only ``role``/``name`` per
    party — it has no dedicated per-party contact/address columns (those live
    on ``customers``/``customer_contacts``). ``Party.contacts``/``address``
    are therefore always empty when hydrated from this repository; the
    primary borrower's contact details come from ``CustomerRepository``
    instead (``CustomerContextAssembler`` composes the two).
    """

    def create(self, tenant_id: TenantId, party: Party) -> Party:
        """Insert a party record.

        ``tenant_id`` is a separate argument (not a ``Party`` field — the
        Sprint-002 ``Party`` contract has no ``tenant_id`` of its own; it is
        always scoped through its parent ``Customer``).
        """
        self._execute(
            f"""
            INSERT INTO {_TABLE} (party_id, customer_id, tenant_id, role, name)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (party.party_id, party.customer_id, tenant_id, party.role.value, party.name),
        )
        self._commit()
        return party

    def find_by_customer(self, tenant_id: TenantId, customer_id: CustomerId) -> tuple[Party, ...]:
        rows = self._tenant_select(
            _TABLE,
            _PARTY_COLUMNS,
            tenant_id,
            extra_where="customer_id = %s",
            extra_params=(customer_id,),
            order_by="created_at ASC",
        )
        return tuple(self._hydrate(row, customer_id) for row in rows)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _hydrate(self, row: tuple[Any, ...], customer_id: CustomerId) -> Party:
        party_id, row_customer_id, role, name = row
        return Party(
            party_id=str(party_id),
            customer_id=CustomerId(row_customer_id),
            role=PartyRole(role),
            name=name or "",
        )
