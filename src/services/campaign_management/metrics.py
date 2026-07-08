"""Prometheus metrics for Campaign Management (V5 Ch6 §"Observability").

Architecture: V5 Ch6 (Campaign Management).
"""

from __future__ import annotations

from prometheus_client import Counter

CALLS_DISPATCHED: Counter = Counter(
    "voiceos_campaign_calls_dispatched_total",
    "Total campaign call dispatch requests, by campaign_id.",
    labelnames=["campaign_id"],
)

COMPLETION_RATE: Counter = Counter(
    "voiceos_campaign_completions_total",
    "Total completed campaign contact attempts, by campaign_id.",
    labelnames=["campaign_id"],
)

PTP_RATE_BY_VARIANT: Counter = Counter(
    "voiceos_campaign_ptp_by_variant_total",
    "Total PTPs created per campaign A/B variant.",
    labelnames=["campaign_id", "variant_id"],
)


def record_call_dispatched(campaign_id: str) -> None:
    CALLS_DISPATCHED.labels(campaign_id=campaign_id).inc()


def record_completion(campaign_id: str) -> None:
    COMPLETION_RATE.labels(campaign_id=campaign_id).inc()


def record_ptp_by_variant(campaign_id: str, variant_id: str) -> None:
    PTP_RATE_BY_VARIANT.labels(campaign_id=campaign_id, variant_id=variant_id).inc()
