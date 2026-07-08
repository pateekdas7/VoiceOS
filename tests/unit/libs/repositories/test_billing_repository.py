"""Unit tests for BillingRepository, UsageRepository, and InvoiceRepository (V5 Ch7, Ch8, Ch9)."""

from __future__ import annotations

from datetime import UTC, datetime

from src.libs.contracts.models.billing import (
    BillingSubscription,
    Invoice,
    InvoiceLineItem,
    InvoiceStatus,
    SubscriptionTier,
    UsageEvent,
    UsageType,
)
from src.libs.contracts.primitives import TenantId
from src.libs.repositories.billing import BillingRepository, InvoiceRepository, UsageRepository
from tests.fixtures.fake_pg import FakeConnection, FakeCursor

_NOW = datetime(2026, 7, 4, tzinfo=UTC)


class TestBillingRepository:
    def test_create_subscription_inserts_and_commits(self) -> None:
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        repo = BillingRepository(conn)
        sub = BillingSubscription(
            subscription_id="sub-1",
            tenant_id=TenantId("tenant-a"),
            tier=SubscriptionTier.GROWTH,
            rate_card_version="2026-Q3",
            contract_start=_NOW,
            created_at=_NOW,
            updated_at=_NOW,
        )

        repo.create_subscription(sub)

        assert "INSERT INTO billing_subscriptions" in cursor.executed[0][0]
        assert conn.commit_count == 1

    def test_get_subscription_hydrates(self) -> None:
        row = ("sub-1", "tenant-a", "GROWTH", "2026-Q3", _NOW, None, 0, "INR", True, _NOW, _NOW)
        cursor = FakeCursor(fetchall_results=[[row]])
        repo = BillingRepository(FakeConnection(cursor))

        sub = repo.get_subscription(TenantId("tenant-a"))

        assert sub is not None
        assert sub.tier == SubscriptionTier.GROWTH

    def test_get_subscription_none_when_absent(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = BillingRepository(FakeConnection(cursor))

        assert repo.get_subscription(TenantId("tenant-a")) is None


class TestUsageRepository:
    def test_record_usage_inserts_and_commits(self) -> None:
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        repo = UsageRepository(conn)
        event = UsageEvent(
            usage_event_id="usage-1",
            tenant_id=TenantId("tenant-a"),
            usage_type=UsageType.CALL_MINUTE,
            quantity=5,
            unit_cost_minor=10,
            total_cost_minor=50,
            currency="INR",
            occurred_at_bucket="2026-07-04T14:00:00Z",
        )

        repo.record_usage(event)

        assert "INSERT INTO usage_events" in cursor.executed[0][0]
        assert conn.commit_count == 1

    def test_find_uninvoiced_filters_null_invoice_id(self) -> None:
        row = ("usage-1", "tenant-a", "CALL_MINUTE", 5, 10, 50, "INR", "", "2026-07-04T14:00:00Z", None)
        cursor = FakeCursor(fetchall_results=[[row]])
        repo = UsageRepository(FakeConnection(cursor))

        events = repo.find_uninvoiced(TenantId("tenant-a"))

        assert len(events) == 1
        sql, _ = cursor.executed[0]
        assert "invoice_id IS NULL" in sql

    def test_find_uninvoiced_applies_bucket_range(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = UsageRepository(FakeConnection(cursor))

        repo.find_uninvoiced(
            TenantId("tenant-a"), since_bucket="2026-07-01T00:00:00Z", until_bucket="2026-07-02T00:00:00Z"
        )

        sql, params = cursor.executed[0]
        assert "occurred_at_bucket >= %s" in sql
        assert "occurred_at_bucket <= %s" in sql
        assert params[-2:] == ("2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z")

    def test_mark_invoiced_no_op_when_empty(self) -> None:
        cursor = FakeCursor()
        repo = UsageRepository(FakeConnection(cursor))

        affected = repo.mark_invoiced(TenantId("tenant-a"), (), "invoice-1")

        assert affected == 0
        assert cursor.executed == []

    def test_mark_invoiced_updates_matching_events(self) -> None:
        cursor = FakeCursor(rowcount=2)
        repo = UsageRepository(FakeConnection(cursor))

        affected = repo.mark_invoiced(TenantId("tenant-a"), ("u-1", "u-2"), "invoice-1")

        assert affected == 2
        sql, params = cursor.executed[0]
        assert "usage_event_id IN (%s, %s)" in sql
        assert params[0] == "invoice-1"

    def test_find_all_between_ignores_invoice_status(self) -> None:
        row = ("usage-1", "tenant-a", "CALL_MINUTE", 5, 10, 50, "INR", "", "2026-07-04T14:00:00Z", "invoice-1")
        cursor = FakeCursor(fetchall_results=[[row]])
        repo = UsageRepository(FakeConnection(cursor))

        events = repo.find_all_between(TenantId("tenant-a"), "2026-07-04T00:00:00Z", "2026-07-04T23:00:00Z")

        assert len(events) == 1
        assert events[0].invoice_id == "invoice-1"
        sql, _ = cursor.executed[0]
        assert "invoice_id IS NULL" not in sql


class TestInvoiceRepository:
    def test_create_invoice_inserts_and_commits(self) -> None:
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        repo = InvoiceRepository(conn)
        invoice = Invoice(
            invoice_id="inv-1",
            tenant_id=TenantId("tenant-a"),
            billing_period_start=_NOW,
            billing_period_end=_NOW,
            subtotal_minor=1000,
            total_minor=1000,
            currency="INR",
            line_items=(
                InvoiceLineItem(
                    usage_type=UsageType.CALL_MINUTE,
                    description="Call minutes",
                    quantity=5,
                    unit_cost_minor=200,
                    total_minor=1000,
                ),
            ),
            created_at=_NOW,
            updated_at=_NOW,
        )

        repo.create_invoice(invoice)

        assert "INSERT INTO invoices" in cursor.executed[0][0]
        assert conn.commit_count == 1

    def test_get_invoice_hydrates_line_items(self) -> None:
        line_items_json = (
            '[{"usage_type": "CALL_MINUTE", "description": "Call minutes", '
            '"quantity": 5, "unit_cost_minor": 200, "total_minor": 1000}]'
        )
        row = (
            "inv-1",
            "tenant-a",
            _NOW,
            _NOW,
            1000,
            0,
            1000,
            "INR",
            "DRAFT",
            None,
            None,
            None,
            "",
            line_items_json,
            _NOW,
            _NOW,
        )
        cursor = FakeCursor(fetchall_results=[[row]])
        repo = InvoiceRepository(FakeConnection(cursor))

        invoice = repo.get_invoice(TenantId("tenant-a"), "inv-1")

        assert invoice is not None
        assert invoice.status == InvoiceStatus.DRAFT
        assert len(invoice.line_items) == 1
        assert invoice.line_items[0].usage_type == UsageType.CALL_MINUTE

    def test_get_invoice_none_when_absent(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = InvoiceRepository(FakeConnection(cursor))
        assert repo.get_invoice(TenantId("tenant-a"), "missing") is None

    def test_mark_issued_updates_status(self) -> None:
        cursor = FakeCursor(rowcount=1)
        repo = InvoiceRepository(FakeConnection(cursor))

        affected = repo.mark_issued(TenantId("tenant-a"), "inv-1", _NOW)

        assert affected == 1
        sql, _ = cursor.executed[0]
        assert "UPDATE invoices" in sql
        assert "status" in sql
