"""Unit tests for the Billing Platform (Sprint-024, V5 Ch9).

All tests run fully in-process — no live Postgres/Redis required (Phase 1).
Repository interactions use small in-memory fake doubles, mirroring the
``_Fake*Repository`` precedent from ``test_crm.py``/``test_tenant_management.py``.

Required named tests (Sprint-024.md):
    test_invoice_calculation — 500 call-minutes -> correct invoice total
    test_entitlement_blocks_on_limit — usage at limit -> DENY from PolicyEngine
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.libs.contracts.models.billing import (
    BillingSubscription,
    Invoice,
    InvoiceStatus,
    SubscriptionTier,
    UsageEvent,
    UsageType,
)
from src.libs.contracts.primitives import TenantId
from src.services.billing.entitlement import EntitlementEngine
from src.services.billing.invoice import InvoiceGenerator
from src.services.billing.payment import PaymentProcessor, PaymentStatus, RazorpayGateway, StripeGateway
from src.services.billing.rate_card import DEFAULT_RATE_CARD, TIER_USAGE_LIMITS, TRIAL_MAX_DAYS
from src.services.billing.service import BillingService
from src.services.billing.subscription import SubscriptionManager
from src.services.policy_engine.decision import PolicyOutcome
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.service import PolicyEngineService

_NOW = datetime(2026, 7, 6, tzinfo=UTC)
_TENANT = TenantId("tenant-a")


class _FakeBillingRepository:
    def __init__(self) -> None:
        self._subscriptions: dict[str, BillingSubscription] = {}

    def create_subscription(self, subscription: BillingSubscription) -> BillingSubscription:
        self._subscriptions[subscription.tenant_id] = subscription
        return subscription

    def get_subscription(self, tenant_id: TenantId) -> BillingSubscription | None:
        return self._subscriptions.get(tenant_id)


class _FakeUsageRepository:
    def __init__(self) -> None:
        self.events: dict[str, UsageEvent] = {}

    def record_usage(self, event: UsageEvent) -> UsageEvent:
        self.events.setdefault(event.usage_event_id, event)
        return self.events[event.usage_event_id]

    def find_uninvoiced(
        self, tenant_id: TenantId, *, since_bucket: str = "", until_bucket: str = ""
    ) -> tuple[UsageEvent, ...]:
        return tuple(
            e
            for e in self.events.values()
            if not e.invoice_id
            and e.tenant_id == tenant_id
            and (not since_bucket or e.occurred_at_bucket >= since_bucket)
            and (not until_bucket or e.occurred_at_bucket <= until_bucket)
        )

    def mark_invoiced(self, tenant_id: TenantId, usage_event_ids: tuple[str, ...], invoice_id: str) -> int:
        count = 0
        for event_id in usage_event_ids:
            event = self.events.get(event_id)
            if event is not None:
                self.events[event_id] = event.model_copy(update={"invoice_id": invoice_id})
                count += 1
        return count


class _FakeInvoiceRepository:
    def __init__(self) -> None:
        self.invoices: dict[str, Invoice] = {}

    def create_invoice(self, invoice: Invoice) -> Invoice:
        self.invoices[invoice.invoice_id] = invoice
        return invoice


def _usage_event(
    usage_event_id: str, usage_type: UsageType, quantity: int, bucket: str = "2026-07-01T00:00:00Z"
) -> UsageEvent:
    """Build a UsageEvent priced from the real rate card, exactly as UsageCollector would."""
    entry = DEFAULT_RATE_CARD.entries[usage_type]
    return UsageEvent(
        usage_event_id=usage_event_id,
        tenant_id=_TENANT,
        usage_type=usage_type,
        quantity=quantity,
        unit_cost_minor=entry.price_per_unit_minor // entry.unit_size,
        total_cost_minor=entry.total_cost_minor(quantity),
        currency="INR",
        occurred_at_bucket=bucket,
    )


class TestInvoiceGenerator:
    def test_invoice_calculation(self) -> None:
        """500 call-minutes + 1M STT tokens + 500K LLM tokens -> correct GROWTH invoice total."""
        usage_repo = _FakeUsageRepository()
        invoice_repo = _FakeInvoiceRepository()
        generator = InvoiceGenerator(usage_repo, invoice_repo)

        usage_repo.record_usage(_usage_event("u-call", UsageType.CALL_MINUTE, 500))
        usage_repo.record_usage(_usage_event("u-stt", UsageType.STT_TOKEN, 1_000_000))
        usage_repo.record_usage(_usage_event("u-llm", UsageType.LLM_TOKEN, 500_000))

        subscription = BillingSubscription(
            subscription_id="sub-1",
            tenant_id=_TENANT,
            tier=SubscriptionTier.GROWTH,
            rate_card_version="v1",
            contract_start=_NOW,
            base_fee_minor=0,
            currency="INR",
            created_at=_NOW,
            updated_at=_NOW,
        )

        period_start = datetime(2026, 7, 1, tzinfo=UTC)
        period_end = datetime(2026, 7, 2, tzinfo=UTC)
        invoice = generator.generate_invoice(_TENANT, subscription, period_start, period_end)

        expected_call_total = DEFAULT_RATE_CARD.cost_minor(UsageType.CALL_MINUTE, 500)
        expected_stt_total = DEFAULT_RATE_CARD.cost_minor(UsageType.STT_TOKEN, 1_000_000)
        expected_llm_total = DEFAULT_RATE_CARD.cost_minor(UsageType.LLM_TOKEN, 500_000)
        assert invoice.total_minor == expected_call_total + expected_stt_total + expected_llm_total
        assert invoice.status == InvoiceStatus.DRAFT
        assert len(invoice.line_items) == 3
        # Every invoiced usage event is now attached to this invoice.
        assert all(e.invoice_id == invoice.invoice_id for e in usage_repo.events.values())
        assert invoice_repo.invoices[invoice.invoice_id] is invoice

    def test_invoice_includes_base_fee_line_item(self) -> None:
        usage_repo = _FakeUsageRepository()
        invoice_repo = _FakeInvoiceRepository()
        generator = InvoiceGenerator(usage_repo, invoice_repo)
        subscription = BillingSubscription(
            subscription_id="sub-2",
            tenant_id=_TENANT,
            tier=SubscriptionTier.ENTERPRISE,
            rate_card_version="v1",
            contract_start=_NOW,
            base_fee_minor=50_000_00,
            currency="INR",
            created_at=_NOW,
            updated_at=_NOW,
        )

        invoice = generator.generate_invoice(
            _TENANT, subscription, datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 2, tzinfo=UTC)
        )

        assert invoice.total_minor == 50_000_00
        assert len(invoice.line_items) == 1
        assert invoice.line_items[0].usage_type is None

    def test_usage_event_idempotent(self) -> None:
        """Same event_id submitted twice -> one usage_event record (fake in-memory repository)."""
        usage_repo = _FakeUsageRepository()
        event = _usage_event("dup-1", UsageType.CALL_MINUTE, 10)

        usage_repo.record_usage(event)
        usage_repo.record_usage(event)

        assert len(usage_repo.events) == 1


class TestEntitlementEngine:
    def _engine(self) -> EntitlementEngine:
        return EntitlementEngine(PolicyEngineService(PolicyEngine()))

    def test_entitlement_blocks_on_limit(self) -> None:
        """Usage at the GROWTH tier's call-minute limit -> DENY from the real PolicyEngine."""
        engine = self._engine()
        limit = TIER_USAGE_LIMITS[SubscriptionTier.GROWTH][UsageType.CALL_MINUTE]
        assert limit is not None

        decision = engine.check_usage(_TENANT, SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, limit)

        assert decision.outcome == PolicyOutcome.DENY
        assert not engine.is_permitted(_TENANT, SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, limit)

    def test_entitlement_permits_under_limit(self) -> None:
        engine = self._engine()
        decision = engine.check_usage(_TENANT, SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, 10)
        assert decision.outcome == PolicyOutcome.PERMIT
        assert engine.is_permitted(_TENANT, SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, 10)

    def test_entitlement_unlimited_for_enterprise(self) -> None:
        engine = self._engine()
        assert engine.limit_for(SubscriptionTier.ENTERPRISE, UsageType.CALL_MINUTE) is None
        decision = engine.check_usage(_TENANT, SubscriptionTier.ENTERPRISE, UsageType.CALL_MINUTE, 10_000_000)
        assert decision.outcome == PolicyOutcome.PERMIT

    def test_entitlement_blocks_expired_trial(self) -> None:
        engine = self._engine()
        decision = engine.check_usage(_TENANT, SubscriptionTier.TRIAL, UsageType.CALL_MINUTE, 1, trial_expired=True)
        assert decision.outcome == PolicyOutcome.DENY


class TestSubscriptionManager:
    def test_create_subscription_uses_rate_card_base_fee(self) -> None:
        manager = SubscriptionManager(_FakeBillingRepository())
        sub = manager.create_subscription(_TENANT, SubscriptionTier.ENTERPRISE, contract_start=_NOW)
        assert sub.base_fee_minor == 50_000_00
        assert manager.get_subscription(_TENANT) is sub

    def test_trial_expiry(self) -> None:
        manager = SubscriptionManager(_FakeBillingRepository())
        sub = manager.create_subscription(_TENANT, SubscriptionTier.TRIAL, contract_start=_NOW)

        assert not manager.is_trial_expired(sub, as_of=_NOW + timedelta(days=TRIAL_MAX_DAYS - 1))
        assert manager.is_trial_expired(sub, as_of=_NOW + timedelta(days=TRIAL_MAX_DAYS + 1))

    def test_non_trial_never_expires(self) -> None:
        manager = SubscriptionManager(_FakeBillingRepository())
        sub = manager.create_subscription(_TENANT, SubscriptionTier.GROWTH, contract_start=_NOW)
        assert not manager.is_trial_expired(sub, as_of=_NOW + timedelta(days=3650))


class TestPaymentProcessor:
    def test_default_gateway_is_stripe_and_succeeds(self) -> None:
        processor = PaymentProcessor()
        result = processor.charge(_TENANT, 1_000_00, "INR")
        assert result.status == PaymentStatus.SUCCESS
        assert result.provider == "stripe"

    def test_razorpay_gateway_succeeds(self) -> None:
        processor = PaymentProcessor(RazorpayGateway())
        result = processor.charge(_TENANT, 500_00, "INR")
        assert result.status == PaymentStatus.SUCCESS
        assert result.provider == "razorpay"

    def test_stripe_gateway_direct(self) -> None:
        gateway = StripeGateway()
        result = gateway.charge(_TENANT, 100, "INR")
        assert result.reference.startswith("stripe_")


class TestBillingService:
    def _service(self) -> tuple[BillingService, _FakeBillingRepository, _FakeUsageRepository]:
        billing_repo = _FakeBillingRepository()
        usage_repo = _FakeUsageRepository()
        invoice_repo = _FakeInvoiceRepository()
        service = BillingService(
            SubscriptionManager(billing_repo),
            EntitlementEngine(PolicyEngineService(PolicyEngine())),
            InvoiceGenerator(usage_repo, invoice_repo),
        )
        return service, billing_repo, usage_repo

    def test_generate_monthly_invoice_raises_without_subscription(self) -> None:
        service, _, _ = self._service()
        with pytest.raises(ValueError, match="no billing subscription"):
            service.generate_monthly_invoice(
                _TENANT, datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 2, tzinfo=UTC)
            )

    def test_generate_monthly_invoice_succeeds(self) -> None:
        service, _, usage_repo = self._service()
        service.create_subscription(_TENANT, SubscriptionTier.GROWTH, contract_start=_NOW)
        usage_repo.record_usage(_usage_event("u-1", UsageType.CALL_MINUTE, 100))

        invoice = service.generate_monthly_invoice(
            _TENANT, datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 2, tzinfo=UTC)
        )

        assert invoice.total_minor == DEFAULT_RATE_CARD.cost_minor(UsageType.CALL_MINUTE, 100)
