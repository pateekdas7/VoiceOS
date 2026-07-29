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

# SaaS-level campaign lifecycle counters — referenced by Prometheus recording rules
# (monitoring/prometheus/recording_rules.yml voiceos_business_recording group).
SAAS_CAMPAIGN_ACTIVATED: Counter = Counter(
    "voiceos_saas_campaign_activated_total",
    "Total campaigns transitioned to ACTIVE state.",
    labelnames=["tenant_id"],
)

SAAS_CAMPAIGN_COMPLETED: Counter = Counter(
    "voiceos_saas_campaign_completed_total",
    "Total campaigns transitioned to COMPLETED state.",
    labelnames=["tenant_id"],
)


def record_call_dispatched(campaign_id: str) -> None:
    CALLS_DISPATCHED.labels(campaign_id=campaign_id).inc()


def record_completion(campaign_id: str) -> None:
    COMPLETION_RATE.labels(campaign_id=campaign_id).inc()


def record_ptp_by_variant(campaign_id: str, variant_id: str) -> None:
    PTP_RATE_BY_VARIANT.labels(campaign_id=campaign_id, variant_id=variant_id).inc()


def record_campaign_activated(tenant_id: str) -> None:
    SAAS_CAMPAIGN_ACTIVATED.labels(tenant_id=tenant_id).inc()


def record_campaign_completed(tenant_id: str) -> None:
    SAAS_CAMPAIGN_COMPLETED.labels(tenant_id=tenant_id).inc()
