"""PaymentProcessor — payment gateway abstraction (V5 Ch9, Sprint-024.md).

Stripe/Razorpay stubs only — no real gateway credentials exist for this
project (same self-managed/no-cloud-account precedent as Sprint-018's mTLS
CA and Sprint-019's self-hosted Vault standing in for a cloud KMS). Real
gateway wiring (API keys, webhooks) is out of this sprint's scope; the
``PaymentGatewayPort`` Protocol is the seam a real Stripe/Razorpay SDK client
plugs into later without changing ``PaymentProcessor`` callers.

Architecture: V5 Ch9 (Billing Platform — PaymentProcessor, extensible
provider interface).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .metrics import record_payment_failure


class PaymentStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass(frozen=True)
class PaymentResult:
    status: PaymentStatus
    provider: str
    reference: str
    reason: str = ""


class PaymentGatewayPort(Protocol):
    """Structural port every payment provider (real or stub) implements."""

    name: str

    def charge(self, tenant_id: str, amount_minor: int, currency: str) -> PaymentResult: ...


class StripeGateway:
    """Stub Stripe provider — always SUCCEEDS (no real Stripe account configured)."""

    name = "stripe"

    def charge(self, tenant_id: str, amount_minor: int, currency: str) -> PaymentResult:
        return PaymentResult(status=PaymentStatus.SUCCESS, provider=self.name, reference=f"stripe_{uuid.uuid4().hex}")


class RazorpayGateway:
    """Stub Razorpay provider — always SUCCEEDS (no real Razorpay account configured)."""

    name = "razorpay"

    def charge(self, tenant_id: str, amount_minor: int, currency: str) -> PaymentResult:
        return PaymentResult(status=PaymentStatus.SUCCESS, provider=self.name, reference=f"razorpay_{uuid.uuid4().hex}")


class PaymentProcessor:
    """Charges a tenant's invoice total through the configured gateway."""

    def __init__(self, gateway: PaymentGatewayPort | None = None) -> None:
        self._gateway: PaymentGatewayPort = gateway or StripeGateway()

    def charge(self, tenant_id: str, amount_minor: int, currency: str) -> PaymentResult:
        result = self._gateway.charge(tenant_id, amount_minor, currency)
        if result.status == PaymentStatus.FAILED:
            record_payment_failure(result.provider)
        return result


__all__ = [
    "PaymentGatewayPort",
    "PaymentProcessor",
    "PaymentResult",
    "PaymentStatus",
    "RazorpayGateway",
    "StripeGateway",
]
