"""Unit tests for ConsentRepository (V4 Ch2 DPDP)."""

from __future__ import annotations

from datetime import UTC, datetime

from src.libs.contracts.context import ConsentStatus
from src.libs.contracts.models.consent import ConsentType
from src.libs.contracts.primitives import CustomerId, TenantId
from src.libs.repositories.consent import ConsentRepository
from tests.fixtures.fake_pg import FakeConnection, FakeCursor

_NOW = datetime(2026, 7, 4, tzinfo=UTC)

_CONSENT_ROW = (
    "consent-1",
    "tenant-a",
    "cust-1",
    "CONTACT",
    "GRANTED",
    _NOW,
    None,
    None,
    _NOW,
    _NOW,
)


class TestCheckConsent:
    def test_returns_none_when_no_record(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = ConsentRepository(FakeConnection(cursor))

        assert repo.check_consent(TenantId("tenant-a"), CustomerId("cust-1"), ConsentType.CONTACT) is None

    def test_returns_current_state(self) -> None:
        cursor = FakeCursor(fetchall_results=[[_CONSENT_ROW]])
        repo = ConsentRepository(FakeConnection(cursor))

        consent = repo.check_consent(TenantId("tenant-a"), CustomerId("cust-1"), ConsentType.CONTACT)

        assert consent is not None
        assert consent.status == ConsentStatus.GRANTED

    def test_tenant_scoped(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = ConsentRepository(FakeConnection(cursor))

        repo.check_consent(TenantId("tenant-a"), CustomerId("cust-1"), ConsentType.CONTACT)

        sql, params = cursor.executed[0]
        assert "tenant_id = %s" in sql
        assert params[0] == "tenant-a"


class TestRecordGrant:
    def test_upserts_and_appends_history_then_commits(self) -> None:
        cursor = FakeCursor(fetchone_results=[_CONSENT_ROW])
        conn = FakeConnection(cursor)
        repo = ConsentRepository(conn)

        consent = repo.record_grant(TenantId("tenant-a"), CustomerId("cust-1"), ConsentType.CONTACT, "agent-1", "ivr")

        assert consent.status == ConsentStatus.GRANTED
        statements = [sql for sql, _ in cursor.executed]
        assert any("ON CONFLICT (customer_id, consent_type)" in s for s in statements)
        assert any("INSERT INTO consent_records" in s for s in statements)
        assert conn.commit_count == 1


class TestRecordRevoke:
    def test_records_revocation(self) -> None:
        revoked_row = (
            "consent-1",
            "tenant-a",
            "cust-1",
            "CONTACT",
            "REVOKED",
            _NOW,
            _NOW,
            None,
            _NOW,
            _NOW,
        )
        cursor = FakeCursor(fetchone_results=[revoked_row])
        repo = ConsentRepository(FakeConnection(cursor))

        consent = repo.record_revoke(TenantId("tenant-a"), CustomerId("cust-1"), ConsentType.CONTACT, "agent-1", "ivr")

        assert consent.status == ConsentStatus.REVOKED
