"""Prometheus metrics for the Billing Platform (V5 Ch9, Sprint-024).

Architecture: Sprint-024.md Phase 2 "Update Prometheus metrics: add
mrr_total, invoices_issued, usage_limit_enforced_total gauges."
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge

MRR_TOTAL: Gauge = Gauge(
    "voiceos_billing_mrr_total_minor",
    "Monthly recurring revenue across all active subscriptions, in minor currency units.",
)
"""Gauge: sum of active subscriptions' base_fee_minor. Set by BillingService on subscription changes."""

INVOICES_ISSUED_TOTAL: Counter = Counter(
    "voiceos_billing_invoices_issued_total",
    "Total invoices issued, by tenant tier.",
    labelnames=["tier"],
)
"""Counter: one increment per InvoiceGenerator.generate_invoice() call."""

PAYMENT_FAILURES_TOTAL: Counter = Counter(
    "voiceos_billing_payment_failures_total",
    "Total failed payment attempts, by provider.",
    labelnames=["provider"],
)
"""Counter: one increment per PaymentProcessor.charge() failure."""


def record_invoice_issued(tier: str) -> None:
    INVOICES_ISSUED_TOTAL.labels(tier=tier).inc()


def record_payment_failure(provider: str) -> None:
    PAYMENT_FAILURES_TOTAL.labels(provider=provider).inc()


def set_mrr_total(total_minor: int) -> None:
    MRR_TOTAL.set(total_minor)
