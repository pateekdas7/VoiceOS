"""Prometheus metrics for the Usage Metering Platform (V5 Ch10, Sprint-024).

Architecture: Sprint-024.md Phase 2 "Update Prometheus metrics: add ...
usage_limit_enforced_total gauge."
"""

from __future__ import annotations

from prometheus_client import Counter

USAGE_EVENTS_RECORDED_TOTAL: Counter = Counter(
    "voiceos_metering_usage_events_recorded_total",
    "Total usage events recorded, by usage type.",
    labelnames=["usage_type"],
)
"""Counter: one increment per UsageCollector-derived UsageEvent write."""

USAGE_LIMIT_ENFORCED_TOTAL: Counter = Counter(
    "voiceos_metering_usage_limit_enforced_total",
    "Total UsageLimitEnforcer.check_and_allow() calls, by outcome.",
    labelnames=["outcome"],
)
"""Counter: 'allowed' | 'blocked', labeled by enforcement outcome."""


def record_usage_event(usage_type: str) -> None:
    USAGE_EVENTS_RECORDED_TOTAL.labels(usage_type=usage_type).inc()


def record_enforcement(allowed: bool) -> None:
    USAGE_LIMIT_ENFORCED_TOTAL.labels(outcome="allowed" if allowed else "blocked").inc()
