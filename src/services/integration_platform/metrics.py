"""Prometheus metrics for the Integration Platform (V5 Ch15, Sprint-025)."""

from __future__ import annotations

from prometheus_client import Counter

WEBHOOK_DELIVERY_ATTEMPTS_TOTAL: Counter = Counter(
    "webhook_delivery_attempts_total",
    "Total webhook delivery attempts, by outcome.",
    labelnames=["outcome"],
)
"""Sprint-025.md Phase 2 health-check requirement: 'Prometheus webhook_delivery_attempts
counter exposed'."""

WEBHOOK_DLQ_TOTAL: Counter = Counter(
    "voiceos_webhook_dlq_total",
    "Total webhook deliveries routed to the DLQ after exhausting retries.",
)


def record_delivery_attempt(outcome: str) -> None:
    WEBHOOK_DELIVERY_ATTEMPTS_TOTAL.labels(outcome=outcome).inc()


def record_dlq_entry() -> None:
    WEBHOOK_DLQ_TOTAL.inc()
