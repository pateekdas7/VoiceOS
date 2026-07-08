"""BillingRepository, UsageRepository, InvoiceRepository — subscription billing +
usage metering + invoicing (V5 Ch7, Ch8, Ch9).

Architecture: V5 Ch7 (Usage Metering); V5 Ch8 (Billing & Invoicing); V5 Ch9
(Billing Platform, Sprint-024).
"""

from __future__ import annotations

import json
from typing import Any

from ..contracts.models.billing import (
    BillingSubscription,
    Invoice,
    InvoiceLineItem,
    InvoiceStatus,
    SubscriptionTier,
    UsageEvent,
    UsageType,
)
from ..contracts.primitives import TenantId
from .base import BaseRepository

_SUBSCRIPTIONS_TABLE = "billing_subscriptions"
_USAGE_TABLE = "usage_events"
_INVOICES_TABLE = "invoices"

_INVOICE_COLUMNS = (
    "invoice_id",
    "tenant_id",
    "billing_period_start",
    "billing_period_end",
    "subtotal_minor",
    "tax_minor",
    "total_minor",
    "currency",
    "status",
    "issued_at",
    "paid_at",
    "due_date",
    "payment_reference",
    "line_items",
    "created_at",
    "updated_at",
)

_SUBSCRIPTION_COLUMNS = (
    "subscription_id",
    "tenant_id",
    "tier",
    "rate_card_version",
    "contract_start",
    "contract_end",
    "base_fee_minor",
    "currency",
    "is_active",
    "created_at",
    "updated_at",
)

_USAGE_COLUMNS = (
    "usage_event_id",
    "tenant_id",
    "usage_type",
    "quantity",
    "unit_cost_minor",
    "total_cost_minor",
    "currency",
    "resource_id",
    "occurred_at_bucket",
    "invoice_id",
)


class BillingRepository(BaseRepository):
    """Tenant-scoped queries for the ``billing_subscriptions`` domain."""

    def create_subscription(self, subscription: BillingSubscription) -> BillingSubscription:
        """Insert a tenant's billing subscription (one per tenant — uq_billing_subscriptions_tenant)."""
        self._execute(
            f"""
            INSERT INTO {_SUBSCRIPTIONS_TABLE} (
                subscription_id, tenant_id, tier, rate_card_version, contract_start, contract_end,
                base_fee_minor, currency, is_active, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                subscription.subscription_id,
                subscription.tenant_id,
                subscription.tier.value,
                subscription.rate_card_version,
                subscription.contract_start,
                subscription.contract_end,
                subscription.base_fee_minor,
                subscription.currency,
                subscription.is_active,
                subscription.created_at,
                subscription.updated_at,
            ),
        )
        self._commit()
        return subscription

    def get_subscription(self, tenant_id: TenantId) -> BillingSubscription | None:
        """Fetch the (single) active billing subscription for a tenant."""
        row = self._tenant_select_one(_SUBSCRIPTIONS_TABLE, _SUBSCRIPTION_COLUMNS, tenant_id)
        return self._hydrate_subscription(row) if row is not None else None

    def _hydrate_subscription(self, row: tuple[Any, ...]) -> BillingSubscription:
        (
            subscription_id,
            tenant_id,
            tier,
            rate_card_version,
            contract_start,
            contract_end,
            base_fee_minor,
            currency,
            is_active,
            created_at,
            updated_at,
        ) = row
        return BillingSubscription(
            subscription_id=str(subscription_id),
            tenant_id=TenantId(tenant_id),
            tier=SubscriptionTier(tier),
            rate_card_version=rate_card_version,
            contract_start=contract_start,
            contract_end=contract_end,
            base_fee_minor=base_fee_minor,
            currency=currency,
            is_active=is_active,
            created_at=created_at,
            updated_at=updated_at,
        )


class UsageRepository(BaseRepository):
    """Tenant-scoped queries for the ``usage_events`` domain."""

    def record_usage(self, event: UsageEvent) -> UsageEvent:
        """Record a billable usage event, idempotently by ``usage_event_id`` (Sprint-024 AC).

        ``ON CONFLICT ... DO NOTHING`` makes duplicate submission of the same
        ``usage_event_id`` (e.g. an at-least-once EventBus redelivery)
        collapse to a single stored row, mechanically, rather than relying on
        every caller to pre-check existence.
        """
        self._execute(
            f"""
            INSERT INTO {_USAGE_TABLE} (
                usage_event_id, tenant_id, usage_type, quantity, unit_cost_minor, total_cost_minor,
                currency, resource_id, occurred_at_bucket, invoice_id, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (usage_event_id) DO NOTHING
            """,
            (
                event.usage_event_id,
                event.tenant_id,
                event.usage_type.value,
                event.quantity,
                event.unit_cost_minor,
                event.total_cost_minor,
                event.currency,
                event.resource_id,
                event.occurred_at_bucket,
                event.invoice_id or None,
            ),
        )
        self._commit()
        return event

    def find_uninvoiced(
        self,
        tenant_id: TenantId,
        *,
        since_bucket: str = "",
        until_bucket: str = "",
    ) -> tuple[UsageEvent, ...]:
        """Find usage events not yet attached to an invoice, scoped to ``tenant_id``.

        ``since_bucket``/``until_bucket`` (inclusive ISO-8601 UTC hour
        buckets, e.g. ``'2026-07-01T00:00:00Z'``) optionally restrict to a
        billing period — ``occurred_at_bucket`` is a zero-padded ISO-8601
        string, so lexicographic comparison is equivalent to chronological
        comparison.
        """
        extra_where = "invoice_id IS NULL"
        extra_params: list[Any] = []
        if since_bucket:
            extra_where += " AND occurred_at_bucket >= %s"
            extra_params.append(since_bucket)
        if until_bucket:
            extra_where += " AND occurred_at_bucket <= %s"
            extra_params.append(until_bucket)
        rows = self._tenant_select(
            _USAGE_TABLE,
            _USAGE_COLUMNS,
            tenant_id,
            extra_where=extra_where,
            extra_params=extra_params,
        )
        return tuple(self._hydrate_usage(row) for row in rows)

    def find_all_between(self, tenant_id: TenantId, since_bucket: str, until_bucket: str) -> tuple[UsageEvent, ...]:
        """Find every usage event in ``[since_bucket, until_bucket]``, invoiced or not.

        Unlike :meth:`find_uninvoiced`, this does not filter by invoice
        status — used by ``BIWarehouse.refresh()`` (Sprint-024), which needs
        a day's total usage/revenue regardless of whether it has been
        billed yet.
        """
        rows = self._tenant_select(
            _USAGE_TABLE,
            _USAGE_COLUMNS,
            tenant_id,
            extra_where="occurred_at_bucket >= %s AND occurred_at_bucket <= %s",
            extra_params=(since_bucket, until_bucket),
        )
        return tuple(self._hydrate_usage(row) for row in rows)

    def mark_invoiced(self, tenant_id: TenantId, usage_event_ids: tuple[str, ...], invoice_id: str) -> int:
        """Attach ``invoice_id`` to the given usage events, scoped to ``tenant_id``.

        Returns the number of rows updated. A no-op (returns 0) when
        ``usage_event_ids`` is empty — never issues an unconditional update.
        """
        if not usage_event_ids:
            return 0
        placeholders = ", ".join(["%s"] * len(usage_event_ids))
        return self._tenant_update(
            _USAGE_TABLE,
            ("invoice_id",),
            (invoice_id,),
            tenant_id,
            extra_where=f"usage_event_id IN ({placeholders})",
            extra_params=usage_event_ids,
        )

    def _hydrate_usage(self, row: tuple[Any, ...]) -> UsageEvent:
        (
            usage_event_id,
            tenant_id,
            usage_type,
            quantity,
            unit_cost_minor,
            total_cost_minor,
            currency,
            resource_id,
            occurred_at_bucket,
            invoice_id,
        ) = row
        return UsageEvent(
            usage_event_id=str(usage_event_id),
            tenant_id=TenantId(tenant_id),
            usage_type=UsageType(usage_type),
            quantity=quantity,
            unit_cost_minor=unit_cost_minor,
            total_cost_minor=total_cost_minor,
            currency=currency,
            resource_id=resource_id or "",
            occurred_at_bucket=occurred_at_bucket,
            invoice_id=str(invoice_id) if invoice_id else "",
        )


class InvoiceRepository(BaseRepository):
    """Tenant-scoped queries for the ``invoices`` domain (Sprint-024, V5 Ch9).

    ``invoices`` was created by Sprint-014 (migration 0013) but had no
    repository/CRUD wired to it until now — Sprint-024's ``InvoiceGenerator``
    is the first consumer.
    """

    def create_invoice(self, invoice: Invoice) -> Invoice:
        """Insert a newly generated invoice (typically in ``DRAFT`` status)."""
        self._execute(
            f"""
            INSERT INTO {_INVOICES_TABLE} (
                invoice_id, tenant_id, billing_period_start, billing_period_end,
                subtotal_minor, tax_minor, total_minor, currency, status,
                issued_at, paid_at, due_date, payment_reference, line_items,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                invoice.invoice_id,
                invoice.tenant_id,
                invoice.billing_period_start,
                invoice.billing_period_end,
                invoice.subtotal_minor,
                invoice.tax_minor,
                invoice.total_minor,
                invoice.currency,
                invoice.status.value,
                invoice.issued_at,
                invoice.paid_at,
                invoice.due_date,
                invoice.payment_reference,
                json.dumps([item.model_dump(mode="json") for item in invoice.line_items]),
                invoice.created_at,
                invoice.updated_at,
            ),
        )
        self._commit()
        return invoice

    def get_invoice(self, tenant_id: TenantId, invoice_id: str) -> Invoice | None:
        """Fetch a single invoice by ID, scoped to ``tenant_id``."""
        row = self._tenant_select_one(
            _INVOICES_TABLE,
            _INVOICE_COLUMNS,
            tenant_id,
            extra_where="invoice_id = %s",
            extra_params=(invoice_id,),
        )
        return self._hydrate_invoice(row) if row is not None else None

    def list_invoices(self, tenant_id: TenantId) -> tuple[Invoice, ...]:
        """List all invoices for a tenant, most recent billing period first."""
        rows = self._tenant_select(
            _INVOICES_TABLE,
            _INVOICE_COLUMNS,
            tenant_id,
            order_by="billing_period_start DESC",
        )
        return tuple(self._hydrate_invoice(row) for row in rows)

    def mark_issued(self, tenant_id: TenantId, invoice_id: str, issued_at: Any) -> int:
        """Transition an invoice from ``DRAFT`` to ``ISSUED``. Returns rows affected."""
        return self._tenant_update(
            _INVOICES_TABLE,
            ("status", "issued_at", "updated_at"),
            (InvoiceStatus.ISSUED.value, issued_at, issued_at),
            tenant_id,
            extra_where="invoice_id = %s",
            extra_params=(invoice_id,),
        )

    def _hydrate_invoice(self, row: tuple[Any, ...]) -> Invoice:
        (
            invoice_id,
            tenant_id,
            billing_period_start,
            billing_period_end,
            subtotal_minor,
            tax_minor,
            total_minor,
            currency,
            status,
            issued_at,
            paid_at,
            due_date,
            payment_reference,
            line_items_json,
            created_at,
            updated_at,
        ) = row
        line_items_raw = json.loads(line_items_json) if isinstance(line_items_json, str) else (line_items_json or [])
        return Invoice(
            invoice_id=str(invoice_id),
            tenant_id=TenantId(tenant_id),
            billing_period_start=billing_period_start,
            billing_period_end=billing_period_end,
            subtotal_minor=subtotal_minor,
            tax_minor=tax_minor,
            total_minor=total_minor,
            currency=currency,
            status=InvoiceStatus(status),
            issued_at=issued_at,
            paid_at=paid_at,
            due_date=due_date,
            payment_reference=payment_reference or "",
            line_items=tuple(InvoiceLineItem(**item) for item in line_items_raw),
            created_at=created_at,
            updated_at=updated_at,
        )
