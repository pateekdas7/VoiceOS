"""BillingService — subscription management + invoice generation facade (V5 Ch9).

The single entry point every other VoiceOS component (Admin Portal API,
MeteringService, BIWarehouse) uses for billing — composes
``SubscriptionManager``/``EntitlementEngine``/``InvoiceGenerator``/
``PaymentProcessor`` exactly like ``PolicyEngineService`` composes the PDP
(same "library-class facade, no standalone HTTP listener until Sprint-026"
precedent — see CPU_NODE_STATE.md §8.1).

Architecture: V5 Ch9 (Billing Platform).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.libs.contracts.models.billing import BillingSubscription, Invoice, SubscriptionTier
from src.libs.contracts.primitives import TenantId
from src.libs.event_bus.publisher import Publisher

from .entitlement import EntitlementEngine
from .invoice import InvoiceGenerator
from .metrics import record_invoice_issued
from .payment import PaymentProcessor, PaymentResult
from .subscription import SubscriptionManager

BILLING_INVOICE_GENERATED_EVENT_TYPE = "saas.billing.invoice_generated"


class BillingService:
    """Facade over subscription management, entitlements, invoicing, and payment."""

    def __init__(
        self,
        subscription_manager: SubscriptionManager,
        entitlement_engine: EntitlementEngine,
        invoice_generator: InvoiceGenerator,
        payment_processor: PaymentProcessor | None = None,
        publisher: Publisher | None = None,
    ) -> None:
        self._subscriptions = subscription_manager
        self._entitlements = entitlement_engine
        self._invoices = invoice_generator
        self._payments = payment_processor or PaymentProcessor()
        self._publisher = publisher

    def create_subscription(
        self,
        tenant_id: TenantId,
        tier: SubscriptionTier,
        contract_start: datetime | None = None,
        contract_end: datetime | None = None,
        base_fee_minor: int | None = None,
        currency: str = "INR",
    ) -> BillingSubscription:
        return self._subscriptions.create_subscription(
            tenant_id, tier, contract_start, contract_end, base_fee_minor, currency
        )

    def get_subscription(self, tenant_id: TenantId) -> BillingSubscription | None:
        return self._subscriptions.get_subscription(tenant_id)

    @property
    def entitlements(self) -> EntitlementEngine:
        return self._entitlements

    def generate_monthly_invoice(self, tenant_id: TenantId, period_start: datetime, period_end: datetime) -> Invoice:
        """Generate + persist the tenant's invoice for the given billing period."""
        subscription = self._subscriptions.get_subscription(tenant_id)
        if subscription is None:
            raise ValueError(f"no billing subscription found for tenant {tenant_id}")

        invoice = self._invoices.generate_invoice(tenant_id, subscription, period_start, period_end)
        record_invoice_issued(subscription.tier.value)

        if self._publisher is not None:
            self._publisher.publish(
                event_type=BILLING_INVOICE_GENERATED_EVENT_TYPE,
                tenant_id=tenant_id,
                payload={
                    "invoice_id": invoice.invoice_id,
                    "billing_period_start": invoice.billing_period_start.isoformat(),
                    "billing_period_end": invoice.billing_period_end.isoformat(),
                    "total_amount_minor": invoice.total_minor,
                    "currency": invoice.currency,
                    "line_item_count": len(invoice.line_items),
                },
                correlation_id=invoice.invoice_id,
            )
        return invoice

    def charge_invoice(self, invoice: Invoice) -> PaymentResult:
        return self._payments.charge(invoice.tenant_id, invoice.total_minor, invoice.currency)

    def check_entitlement(
        self,
        tenant_id: TenantId,
        tier: SubscriptionTier,
        usage_type: Any,
        current_period_quantity: int,
        trial_expired: bool = False,
    ) -> bool:
        return self._entitlements.is_permitted(tenant_id, tier, usage_type, current_period_quantity, trial_expired)


__all__ = ["BillingService"]
