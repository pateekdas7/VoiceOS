"""Unit tests for CallDispositionRepository (Sprint-024, V5 Ch11)."""

from __future__ import annotations

from datetime import UTC, datetime

from src.libs.contracts.models.analytics import CallDisposition
from src.libs.contracts.primitives import CallId, CustomerId, TenantId
from src.libs.repositories.call_disposition import CallDispositionRepository
from tests.fixtures.fake_pg import FakeConnection, FakeCursor

_NOW = datetime(2026, 7, 6, tzinfo=UTC)


class TestCallDispositionRepository:
    def test_record_inserts_and_commits(self) -> None:
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        repo = CallDispositionRepository(conn)
        disposition = CallDisposition(
            disposition_id="disp-1",
            tenant_id=TenantId("tenant-a"),
            call_id=CallId("call-1"),
            customer_id=CustomerId("cust-1"),
            loan_account_id="loan-1",
            outcome_code="PTP_MADE",
            duration_ms=60_000,
            dispositioned_at=_NOW,
        )

        repo.record(disposition)

        assert "INSERT INTO call_dispositions" in cursor.executed[0][0]
        assert conn.commit_count == 1

    def test_find_between_hydrates_rows(self) -> None:
        row = ("disp-1", "tenant-a", "call-1", "cust-1", "loan-1", "PTP_MADE", 60_000, _NOW)
        cursor = FakeCursor(fetchall_results=[[row]])
        repo = CallDispositionRepository(FakeConnection(cursor))

        results = repo.find_between(TenantId("tenant-a"), _NOW, _NOW)

        assert len(results) == 1
        assert results[0].outcome_code == "PTP_MADE"
        assert results[0].duration_ms == 60_000

    def test_find_between_empty(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = CallDispositionRepository(FakeConnection(cursor))
        assert repo.find_between(TenantId("tenant-a"), _NOW, _NOW) == ()
