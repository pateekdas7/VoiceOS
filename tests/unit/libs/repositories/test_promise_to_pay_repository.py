"""Unit tests for PromiseToPayRepository — idempotent PTP creation (V3 Ch8, EV-7)."""

from __future__ import annotations

from datetime import UTC, datetime

from src.libs.contracts.models.collections import PromiseToPay, PTPStatus
from src.libs.contracts.primitives import CallId, CustomerId, TenantId
from src.libs.repositories.promise_to_pay import PromiseToPayRepository
from tests.fixtures.fake_pg import FakeConnection, FakeCursor

_NOW = datetime(2026, 7, 4, tzinfo=UTC)


def _ptp() -> PromiseToPay:
    return PromiseToPay(
        ptp_id="ptp-1",
        tenant_id=TenantId("tenant-a"),
        call_id=CallId("call-1"),
        customer_id=CustomerId("cust-1"),
        loan_account_id="loan-1",
        promised_amount_minor=50_000_00,
        currency="INR",
        promise_date=_NOW,
        recorded_at=_NOW,
        updated_at=_NOW,
    )


class TestCreateIdempotent:
    def test_new_key_inserts_and_returns_created_true(self) -> None:
        cursor = FakeCursor(fetchone_results=[("ptp-1",)])
        conn = FakeConnection(cursor)
        repo = PromiseToPayRepository(conn)

        ptp, created = repo.create_idempotent(_ptp(), idempotency_key="call-1:turn-1")

        assert created is True
        assert ptp.ptp_id == "ptp-1"
        assert conn.commit_count == 1
        assert "ON CONFLICT (idempotency_key) DO NOTHING" in cursor.executed[0][0]

    def test_duplicate_key_returns_existing_record_without_second_insert(self) -> None:
        """Sprint-014 AC: test_ptp_create_returns_existing_on_duplicate_key."""
        existing_row = (
            "ptp-EXISTING",
            "tenant-a",
            "call-1",
            "cust-1",
            "loan-1",
            50_000_00,
            "INR",
            _NOW,
            "PENDING",
            "",
            _NOW,
            _NOW,
        )
        cursor = FakeCursor(fetchone_results=[None], fetchall_results=[[existing_row]])
        repo = PromiseToPayRepository(FakeConnection(cursor))

        ptp, created = repo.create_idempotent(_ptp(), idempotency_key="call-1:turn-1")

        assert created is False
        assert ptp.ptp_id == "ptp-EXISTING"
        # Only the original INSERT statement was issued — the conflict path only reads.
        insert_statements = [sql for sql, _ in cursor.executed if "INSERT" in sql]
        assert len(insert_statements) == 1

    def test_none_key_never_conflicts(self) -> None:
        cursor = FakeCursor(fetchone_results=[("ptp-1",)])
        repo = PromiseToPayRepository(FakeConnection(cursor))

        _, created = repo.create_idempotent(_ptp(), idempotency_key=None)

        assert created is True


class TestUpdateStatus:
    def test_scopes_by_tenant_and_ptp_id(self) -> None:
        cursor = FakeCursor()
        repo = PromiseToPayRepository(FakeConnection(cursor))

        repo.update_status(TenantId("tenant-a"), "ptp-1", PTPStatus.KEPT)

        sql, params = cursor.executed[0]
        assert "WHERE tenant_id = %s AND ptp_id = %s" in sql
        assert params == ("KEPT", "tenant-a", "ptp-1")


class TestFindByLoan:
    def test_returns_matching_ptps(self) -> None:
        row = (
            "ptp-1",
            "tenant-a",
            "call-1",
            "cust-1",
            "loan-1",
            50_000_00,
            "INR",
            _NOW,
            "PENDING",
            "",
            _NOW,
            _NOW,
        )
        cursor = FakeCursor(fetchall_results=[[row]])
        repo = PromiseToPayRepository(FakeConnection(cursor))

        results = repo.find_by_loan(TenantId("tenant-a"), "loan-1")

        assert len(results) == 1
        assert results[0].loan_account_id == "loan-1"
