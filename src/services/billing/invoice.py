"""InvoiceGenerator — monthly invoice calculation (V5 Ch9, Sprint-024.md).

Aggregates uninvoiced ``usage_events`` (already priced at collection time by
``MeteringService`` against the same :class:`~src.services.billing.rate_card.RateCard`)
into one :class:`Invoice` per tenant per billing period: base subscription
fee + one line item per usage dimension actually consumed.

Architecture: V5 Ch9 (Billing Platform — InvoiceGenerator, JSONB line-item
persistence on ``invoices`` — see CHANGELOG.md Sprint-024 deviations for why
this repo's pre-existing ``invoices`` table, not a new ``billing_invoices``
table, is what "JSONB invoice persistence" targets).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Protocol

from src.libs.contracts.models.billing import (
    BillingSubscription,
    Invoice,
    InvoiceLineItem,
    InvoiceStatus,
    UsageEvent,
    UsageType,
)
from src.libs.contracts.primitives import TenantId

from .rate_card import DEFAULT_RATE_CARD, RateCard


class UsageRepositoryPort(Protocol):
    def find_uninvoiced(
        self, tenant_id: TenantId, *, since_bucket: str = "", until_bucket: str = ""
    ) -> tuple[UsageEvent, ...]: ...

    def mark_invoiced(self, tenant_id: TenantId, usage_event_ids: tuple[str, ...], invoice_id: str) -> int: ...


class InvoiceRepositoryPort(Protocol):
    def create_invoice(self, invoice: Invoice) -> Invoice: ...


class InvoiceGenerator:
    """Computes and persists one tenant's monthly invoice (V5 Ch9 InvoiceGenerator)."""

    def __init__(
        self,
        usage_repository: UsageRepositoryPort,
        invoice_repository: InvoiceRepositoryPort,
        rate_card: RateCard = DEFAULT_RATE_CARD,
    ) -> None:
        self._usage_repository = usage_repository
        self._invoice_repository = invoice_repository
        self._rate_card = rate_card

    def generate_invoice(
        self,
        tenant_id: TenantId,
        subscription: BillingSubscription,
        period_start: datetime,
        period_end: datetime,
    ) -> Invoice:
        """Generate (and persist) the invoice for ``[period_start, period_end)``.

        Every uninvoiced usage event in the period is grouped by
        ``usage_type``; each group becomes one :class:`InvoiceLineItem`
        using that group's *already-recorded* ``total_cost_minor`` sum (the
        authoritative amount metered at collection time) — the rate card is
        consulted only for the line item's human-readable description, never
        to re-price usage a second time.
        """
        since_bucket = period_start.strftime("%Y-%m-%dT%H:00:00Z")
        until_bucket = period_end.strftime("%Y-%m-%dT%H:00:00Z")
        events = self._usage_repository.find_uninvoiced(tenant_id, since_bucket=since_bucket, until_bucket=until_bucket)

        quantities: dict[UsageType, int] = defaultdict(int)
        totals: dict[UsageType, int] = defaultdict(int)
        for event in events:
            quantities[event.usage_type] += event.quantity
            totals[event.usage_type] += event.total_cost_minor

        line_items: list[InvoiceLineItem] = []
        if subscription.base_fee_minor > 0:
            line_items.append(
                InvoiceLineItem(
                    usage_type=None,
                    description=f"Subscription base fee ({subscription.tier.value})",
                    quantity=1,
                    unit_cost_minor=subscription.base_fee_minor,
                    total_minor=subscription.base_fee_minor,
                )
            )
        for usage_type, quantity in sorted(quantities.items(), key=lambda kv: kv[0].value):
            entry = self._rate_card.entries.get(usage_type)
            description = entry.description if entry is not None else usage_type.value
            total = totals[usage_type]
            line_items.append(
                InvoiceLineItem(
                    usage_type=usage_type,
                    description=description,
                    quantity=quantity,
                    unit_cost_minor=(total // quantity) if quantity else 0,
                    total_minor=total,
                )
            )

        subtotal_minor = sum(item.total_minor for item in line_items)
        tax_minor = 0
        total_minor = subtotal_minor + tax_minor
        now = datetime.now(UTC)

        invoice = Invoice(
            invoice_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            billing_period_start=period_start,
            billing_period_end=period_end,
            subtotal_minor=subtotal_minor,
            tax_minor=tax_minor,
            total_minor=total_minor,
            currency=subscription.currency,
            status=InvoiceStatus.DRAFT,
            line_items=tuple(line_items),
            created_at=now,
            updated_at=now,
        )
        invoice = self._invoice_repository.create_invoice(invoice)

        event_ids = tuple(event.usage_event_id for event in events)
        if event_ids:
            self._usage_repository.mark_invoiced(tenant_id, event_ids, invoice.invoice_id)

        return invoice


__all__ = ["InvoiceGenerator", "InvoiceRepositoryPort", "UsageRepositoryPort"]
