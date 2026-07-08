"""Unit tests for CustomerRepository (V5 Ch3)."""

from __future__ import annotations

from datetime import UTC, datetime

from src.libs.contracts.models.customer import Address, Customer, CustomerContact
from src.libs.contracts.primitives import CustomerId, TenantId
from src.libs.encryption import (
    CryptoShredder,
    EncryptionService,
    EnvelopeEncryption,
    InMemoryDEKStore,
)
from src.libs.repositories.customer import CustomerRepository
from tests.fixtures.fake_kms import FakeKMSClient
from tests.fixtures.fake_pg import FakeConnection, FakeCursor

_NOW = datetime(2026, 7, 4, tzinfo=UTC)


def _customer(**overrides: object) -> Customer:
    defaults: dict[str, object] = {
        "customer_id": CustomerId("cust-1"),
        "tenant_id": TenantId("tenant-a"),
        "crm_id": "crm-123",
        "name": "Asha Rao",
        "preferred_language": "hi",
        "contacts": (CustomerContact(contact_type="MOBILE", value="+919876543210", is_primary=True),),
        "address": Address(line1="1 MG Road", city="Bengaluru", state="KA", pincode="560001"),
        "is_active": True,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(overrides)
    return Customer(**defaults)  # type: ignore[arg-type]


class TestCreate:
    def test_inserts_customer_contact_and_address_then_commits(self) -> None:
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        repo = CustomerRepository(conn)

        repo.create(_customer())

        statements = [sql for sql, _ in cursor.executed]
        assert any("INSERT INTO customers" in s for s in statements)
        assert any("INSERT INTO customer_contacts" in s for s in statements)
        assert any("INSERT INTO customer_addresses" in s for s in statements)
        assert conn.commit_count == 1

    def test_skips_address_insert_when_none(self) -> None:
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        repo = CustomerRepository(conn)

        repo.create(_customer(address=None))

        statements = [sql for sql, _ in cursor.executed]
        assert not any("INSERT INTO customer_addresses" in s for s in statements)


class TestGet:
    def test_returns_none_when_missing(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = CustomerRepository(FakeConnection(cursor))

        assert repo.get(TenantId("tenant-a"), CustomerId("missing")) is None

    def test_hydrates_full_customer_with_contacts_and_address(self) -> None:
        main_row = (
            "cust-1",
            "tenant-a",
            "crm-123",
            "Asha Rao",
            None,
            "hi",
            True,
            False,
            _NOW,
            _NOW,
        )
        contact_rows = [("MOBILE", "+919876543210", None, True, False, True)]
        address_rows = [("1 MG Road", "", "Bengaluru", "KA", "560001", "IN", None)]
        cursor = FakeCursor(fetchall_results=[[main_row], contact_rows, address_rows])
        repo = CustomerRepository(FakeConnection(cursor))

        customer = repo.get(TenantId("tenant-a"), CustomerId("cust-1"))

        assert customer is not None
        assert customer.crm_id == "crm-123"
        assert len(customer.contacts) == 1
        assert customer.contacts[0].value == "+919876543210"
        assert customer.address is not None
        assert customer.address.city == "Bengaluru"

    def test_tenant_id_scopes_the_lookup(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = CustomerRepository(FakeConnection(cursor))

        repo.get(TenantId("tenant-a"), CustomerId("cust-1"))

        sql, params = cursor.executed[0]
        assert "tenant_id = %s" in sql
        assert params[0] == "tenant-a"


class TestFindByExternalId:
    def test_scopes_by_tenant_and_crm_id(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = CustomerRepository(FakeConnection(cursor))

        repo.find_by_external_id(TenantId("tenant-a"), "crm-123")

        sql, params = cursor.executed[0]
        assert "tenant_id = %s AND crm_id = %s" in sql
        assert params == ("tenant-a", "crm-123", 1)


class TestFindByPhone:
    def test_returns_none_when_no_contact_matches(self) -> None:
        cursor = FakeCursor(fetchone_results=[None])
        repo = CustomerRepository(FakeConnection(cursor))

        assert repo.find_by_phone(TenantId("tenant-a"), "+910000000000") is None

    def test_cross_tenant_contact_never_leaks(self) -> None:
        """A contact value that exists only under another tenant must not resolve."""
        cursor = FakeCursor(fetchone_results=[None])
        repo = CustomerRepository(FakeConnection(cursor))

        result = repo.find_by_phone(TenantId("tenant-b"), "+919876543210")

        assert result is None
        sql, params = cursor.executed[0]
        assert "tenant_id = %s" in sql
        assert params[0] == "tenant-b"

    def test_resolves_customer_via_contact_match(self) -> None:
        main_row = ("cust-1", "tenant-a", "crm-123", "Asha Rao", None, "hi", True, False, _NOW, _NOW)
        cursor = FakeCursor(
            fetchone_results=[("cust-1",)],
            fetchall_results=[[main_row], [], []],
        )
        repo = CustomerRepository(FakeConnection(cursor))

        customer = repo.find_by_phone(TenantId("tenant-a"), "+919876543210")

        assert customer is not None
        assert customer.customer_id == "cust-1"


class TestEncryptionWiring:
    """Sprint-019: optional EncryptionService wiring (V4 Ch8)."""

    def _encryption_service(self) -> EncryptionService:
        dek_store = InMemoryDEKStore()
        envelope = EnvelopeEncryption(FakeKMSClient(), dek_store)
        return EncryptionService(envelope, CryptoShredder(dek_store))

    def test_create_populates_encrypted_columns_when_service_provided(self) -> None:
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        repo = CustomerRepository(conn, encryption_service=self._encryption_service())

        repo.create(_customer())

        insert_customers = next(
            sql_params for sql_params in cursor.executed if "INSERT INTO customers" in sql_params[0]
        )
        _, params = insert_customers
        # name_encrypted is the 5th positional value (customer_id, tenant_id, crm_id, name, name_encrypted, ...)
        assert params[4] is not None
        assert params[4] != _customer().name.encode("utf-8")

    def test_create_without_service_leaves_encrypted_columns_null(self) -> None:
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        repo = CustomerRepository(conn)

        repo.create(_customer())

        insert_customers = next(
            sql_params for sql_params in cursor.executed if "INSERT INTO customers" in sql_params[0]
        )
        _, params = insert_customers
        assert params[4] is None

    def test_round_trip_through_repository_decrypts_transparently(self) -> None:
        encryption_service = self._encryption_service()
        write_cursor = FakeCursor()
        write_conn = FakeConnection(write_cursor)
        write_repo = CustomerRepository(write_conn, encryption_service=encryption_service)
        write_repo.create(_customer())

        name_encrypted = next(p for sql, p in write_cursor.executed if "INSERT INTO customers" in sql)[4]
        contact_row = next(p for sql, p in write_cursor.executed if "INSERT INTO customer_contacts" in sql)
        value_encrypted = contact_row[4]
        address_row = next(p for sql, p in write_cursor.executed if "INSERT INTO customer_addresses" in sql)
        address_encrypted = address_row[8]

        main_row = ("cust-1", "tenant-a", "crm-123", "Asha Rao", name_encrypted, "hi", True, False, _NOW, _NOW)
        contact_rows = [("MOBILE", "+919876543210", value_encrypted, True, False, True)]
        address_rows = [("1 MG Road", "", "Bengaluru", "KA", "560001", "IN", address_encrypted)]
        read_cursor = FakeCursor(fetchall_results=[[main_row], contact_rows, address_rows])
        read_repo = CustomerRepository(FakeConnection(read_cursor), encryption_service=encryption_service)

        customer = read_repo.get(TenantId("tenant-a"), CustomerId("cust-1"))

        assert customer is not None
        assert customer.name == "Asha Rao"
        assert customer.contacts[0].value == "+919876543210"
        assert customer.address is not None
        assert customer.address.city == "Bengaluru"
