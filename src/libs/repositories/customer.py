"""CustomerRepository — authoritative CRM customer records (V5 Ch3).

Architecture: V5 Ch3 (Customer CRM); AR-8 (tenant isolation); V4 Ch8
(Encryption Architecture — optional field-level encryption, Sprint-019).
"""

from __future__ import annotations

import json
from typing import Any

from src.libs.circuit_breaker.breaker import CircuitBreaker
from src.libs.encryption import EncryptedPayload, EncryptionService

from ..contracts.models.customer import Address, Customer, CustomerContact
from ..contracts.primitives import CustomerId, TenantId
from .base import BaseRepository

_TABLE = "customers"
_CONTACTS_TABLE = "customer_contacts"
_ADDRESSES_TABLE = "customer_addresses"

_CUSTOMER_COLUMNS = (
    "customer_id",
    "tenant_id",
    "crm_id",
    "name",
    "name_encrypted",
    "preferred_language",
    "is_active",
    "data_erasure_requested",
    "created_at",
    "updated_at",
)


class CustomerRepository(BaseRepository):
    """Tenant-scoped CRUD + lookup queries for the ``customers`` domain.

    ``encryption_service`` is optional/additive (default ``None``, same
    wiring precedent as ``breaker`` since Sprint-016): when provided, every
    write additionally populates the record's ``*_encrypted`` column
    (migration 0016) alongside the pre-existing plaintext column — kept per
    Sprint-019.md's own rollback procedure ("each PII column has both the
    original and `_encrypted` during transition"). Reads prefer the
    encrypted column (decrypting) when present, falling back to plaintext
    for rows written before encryption was wired in.

    Known limitation: ``find_by_phone`` matches on the plaintext
    ``customer_contacts.value`` column only — it cannot find a contact
    whose plaintext value has been cleared post-encryption, since this
    repository does not implement a blind-index/HMAC lookup column.
    Tracked as a follow-up (searchable encryption is out of scope for
    Sprint-019).
    """

    def __init__(
        self,
        conn: Any,
        breaker: CircuitBreaker | None = None,
        encryption_service: EncryptionService | None = None,
    ) -> None:
        super().__init__(conn, breaker)
        self._encryption_service = encryption_service

    def create(self, customer: Customer) -> Customer:
        """Insert a new customer record along with its contacts and address.

        Args:
            customer: The customer to persist. ``customer.tenant_id`` scopes
                every row written by this call.

        Returns:
            The same ``customer`` (returned for a fluent call style).
        """
        name_encrypted = self._encrypt_field(customer.name, customer.tenant_id)
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                customer_id, tenant_id, crm_id, name, name_encrypted, preferred_language,
                is_active, data_erasure_requested, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                customer.customer_id,
                customer.tenant_id,
                customer.crm_id,
                customer.name,
                name_encrypted,
                customer.preferred_language,
                customer.is_active,
                customer.data_erasure_requested,
                customer.created_at,
                customer.updated_at,
            ),
        )
        for contact in customer.contacts:
            value_encrypted = self._encrypt_field(contact.value, customer.tenant_id)
            self._execute(
                f"""
                INSERT INTO {_CONTACTS_TABLE} (
                    customer_id, tenant_id, contact_type, value, value_encrypted,
                    is_primary, is_dnc, consent_captured
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    customer.customer_id,
                    customer.tenant_id,
                    contact.contact_type,
                    contact.value,
                    value_encrypted,
                    contact.is_primary,
                    contact.is_dnc,
                    contact.consent_captured,
                ),
            )
        if customer.address is not None:
            address_encrypted = self._encrypt_field(
                json.dumps(
                    {
                        "line1": customer.address.line1,
                        "line2": customer.address.line2,
                        "city": customer.address.city,
                        "state": customer.address.state,
                        "pincode": customer.address.pincode,
                    }
                ),
                customer.tenant_id,
            )
            self._execute(
                f"""
                INSERT INTO {_ADDRESSES_TABLE} (
                    customer_id, tenant_id, line1, line2, city, state, pincode, country, address_encrypted
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    customer.customer_id,
                    customer.tenant_id,
                    customer.address.line1,
                    customer.address.line2,
                    customer.address.city,
                    customer.address.state,
                    customer.address.pincode,
                    customer.address.country,
                    address_encrypted,
                ),
            )
        self._commit()
        return customer

    # ------------------------------------------------------------------
    # Encryption helpers
    # ------------------------------------------------------------------

    def _encrypt_field(self, plaintext: str, tenant_id: str) -> bytes | None:
        if self._encryption_service is None or not plaintext:
            return None
        payload = self._encryption_service.encrypt(plaintext.encode("utf-8"), tenant_id)
        return payload.to_bytes()

    def _decrypt_field(self, encrypted: Any, plaintext_fallback: str | None, tenant_id: str) -> str | None:
        if self._encryption_service is None or encrypted is None:
            return plaintext_fallback
        payload = EncryptedPayload.from_bytes(bytes(encrypted))
        return self._encryption_service.decrypt(payload, tenant_id).decode("utf-8")

    def get(self, tenant_id: TenantId, customer_id: CustomerId) -> Customer | None:
        """Fetch a customer by ID, scoped to ``tenant_id``."""
        row = self._tenant_select_one(
            _TABLE,
            _CUSTOMER_COLUMNS,
            tenant_id,
            extra_where="customer_id = %s",
            extra_params=(customer_id,),
        )
        if row is None:
            return None
        return self._hydrate(row, tenant_id)

    def find_by_external_id(self, tenant_id: TenantId, crm_id: str) -> Customer | None:
        """Find a customer by the external CRM identifier (RI-5 authoritative key)."""
        row = self._tenant_select_one(
            _TABLE,
            _CUSTOMER_COLUMNS,
            tenant_id,
            extra_where="crm_id = %s",
            extra_params=(crm_id,),
        )
        if row is None:
            return None
        return self._hydrate(row, tenant_id)

    def list_for_tenant(self, tenant_id: TenantId, limit: int = 200) -> tuple[Customer, ...]:
        """All customers for ``tenant_id``, most recently created first (CRM list view)."""
        rows = self._tenant_select(
            _TABLE,
            _CUSTOMER_COLUMNS,
            tenant_id,
            order_by="created_at DESC",
            limit=limit,
        )
        return tuple(self._hydrate(row, tenant_id) for row in rows)

    def find_by_phone(self, tenant_id: TenantId, phone: str) -> Customer | None:
        """Find a customer by a contact phone/email value.

        Joins through ``customer_contacts`` — tenant-scoped on both the
        contacts lookup and the final customer fetch, so a match in another
        tenant's contact rows can never leak a customer record.
        """
        cur = self._execute(
            f"""
            SELECT customer_id FROM {_CONTACTS_TABLE}
            WHERE tenant_id = %s AND value = %s
            LIMIT 1
            """,
            (tenant_id, phone),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return self.get(tenant_id, CustomerId(row[0]))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _hydrate(self, row: tuple[Any, ...], tenant_id: TenantId) -> Customer:
        (
            customer_id,
            row_tenant_id,
            crm_id,
            name,
            name_encrypted,
            preferred_language,
            is_active,
            data_erasure_requested,
            created_at,
            updated_at,
        ) = row
        name = self._decrypt_field(name_encrypted, name, tenant_id)

        contact_rows = self._tenant_select(
            _CONTACTS_TABLE,
            ("contact_type", "value", "value_encrypted", "is_primary", "is_dnc", "consent_captured"),
            tenant_id,
            extra_where="customer_id = %s",
            extra_params=(customer_id,),
        )
        contacts = tuple(
            CustomerContact(
                contact_type=c_type,
                value=self._decrypt_field(value_encrypted, value, tenant_id) or "",
                is_primary=is_primary,
                is_dnc=is_dnc,
                consent_captured=consent_captured,
            )
            for c_type, value, value_encrypted, is_primary, is_dnc, consent_captured in contact_rows
        )

        address_row = self._tenant_select_one(
            _ADDRESSES_TABLE,
            ("line1", "line2", "city", "state", "pincode", "country", "address_encrypted"),
            tenant_id,
            extra_where="customer_id = %s",
            extra_params=(customer_id,),
        )
        address = None
        if address_row is not None:
            line1, line2, city, state, pincode, country, address_encrypted = address_row
            if self._encryption_service is not None and address_encrypted is not None:
                payload = EncryptedPayload.from_bytes(bytes(address_encrypted))
                decoded = json.loads(self._encryption_service.decrypt(payload, tenant_id).decode("utf-8"))
                line1, line2, city, state, pincode = (
                    decoded["line1"],
                    decoded["line2"],
                    decoded["city"],
                    decoded["state"],
                    decoded["pincode"],
                )
            address = Address(
                line1=line1 or "",
                line2=line2 or "",
                city=city or "",
                state=state or "",
                pincode=pincode or "",
                country=country,
            )

        return Customer(
            customer_id=CustomerId(customer_id),
            tenant_id=TenantId(row_tenant_id),
            crm_id=crm_id,
            name=name or "",
            preferred_language=preferred_language,
            contacts=contacts,
            address=address,
            is_active=is_active,
            created_at=created_at,
            updated_at=updated_at,
            data_erasure_requested=data_erasure_requested,
        )
