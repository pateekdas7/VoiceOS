"""BillingAdminController -- subscription, invoice, usage administration (V5 Ch13).

Architecture: V5 Ch13 (Administration Portal); V5 Ch9 (Billing Platform).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from src.libs.contracts.models.billing import BillingSubscription, Invoice, UsageType
from src.libs.contracts.primitives import TenantId

if TYPE_CHECKING:
    from src.libs.repositories.billing import InvoiceRepository
    from src.services.billing.service import BillingService
    from src.services.metering.aggregator import UsageAggregator


class BillingAdminController:
    """Subscription + invoice + usage-report read administration (V5 Ch13).

    ``usage_summary`` reuses ``UsageAggregator`` (Sprint-024's metering
    platform, V5 Ch10) rather than a second, parallel usage-totals query.
    """

    def __init__(
        self,
        billing_service: BillingService,
        invoice_repository: InvoiceRepository,
        usage_aggregator: UsageAggregator | None = None,
    ) -> None:
        self._billing = billing_service
        self._invoices = invoice_repository
        self._usage = usage_aggregator

    def get_subscription(self, tenant_id: TenantId) -> BillingSubscription | None:
        return self._billing.get_subscription(tenant_id)

    def list_invoices(self, tenant_id: TenantId) -> tuple[Invoice, ...]:
        return self._invoices.list_invoices(tenant_id)

    def usage_summary(self, tenant_id: TenantId, period_start: datetime, period_end: datetime) -> dict[UsageType, int]:
        """Total usage per :class:`UsageType` in ``[period_start, period_end)``. Raises if unwired."""
        if self._usage is None:
            raise UsageReportingNotConfiguredError("no UsageAggregator wired into this BillingAdminController")
        return self._usage.aggregate_period(tenant_id, period_start, period_end)


class UsageReportingNotConfiguredError(RuntimeError):
    """Raised by ``usage_summary`` when no :class:`UsageAggregator` backend was supplied."""


__all__ = ["BillingAdminController", "UsageReportingNotConfiguredError"]
