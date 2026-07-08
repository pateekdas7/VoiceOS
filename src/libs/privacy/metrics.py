"""Prometheus metrics for the Privacy Architecture (V4 Ch9 §9.17 Observability).

Architecture: V4 Ch9 §9.17 ("Minimization coverage, purpose-check pass
rate, rights-request volumes/SLAs, erasure completeness/verification,
retention-job results").
"""

from __future__ import annotations

from prometheus_client import Counter

PURPOSE_CHECKS_TOTAL: Counter = Counter(
    "voiceos_privacy_purpose_checks_total",
    "Total PrivacyEngine.check_purpose() calls, by outcome.",
    labelnames=["outcome"],
)
"""Counter: 'allowed' | 'denied', per check_purpose() call."""

ERASURE_JOBS_TOTAL: Counter = Counter(
    "voiceos_privacy_erasure_jobs_total",
    "Total DataErasureJob executions, by outcome.",
    labelnames=["outcome"],
)
"""Counter: 'completed' | 'consent_not_revoked', per erasure job run."""

RETENTION_EXPIRED_TOTAL: Counter = Counter(
    "voiceos_privacy_retention_expired_total",
    "Total records flagged expired by RetentionScheduler, by data class.",
    labelnames=["data_class"],
)
"""Counter: incremented per flagged-expired record."""


def record_purpose_check(allowed: bool) -> None:
    PURPOSE_CHECKS_TOTAL.labels(outcome="allowed" if allowed else "denied").inc()


def record_erasure_job(outcome: str) -> None:
    ERASURE_JOBS_TOTAL.labels(outcome=outcome).inc()


def record_retention_expired(data_class: str, count: int = 1) -> None:
    RETENTION_EXPIRED_TOTAL.labels(data_class=data_class).inc(count)
