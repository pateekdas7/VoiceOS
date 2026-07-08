"""Prometheus metrics for SecretsManager (V4 Ch7 §7.17 Observability).

Architecture: V4 Ch7 §7.17 ("Secret access (who/what/when), rotation
success/age ... revocation events").
"""

from __future__ import annotations

from prometheus_client import Counter

SECRET_ACCESSES_TOTAL: Counter = Counter(
    "voiceos_secret_accesses_total",
    "Total secret fetches, by path (never by value).",
    labelnames=["path"],
)
"""Counter: one increment per SecretsManager.get_secret() call."""

SECRET_ROTATIONS_TOTAL: Counter = Counter(
    "voiceos_secret_rotations_total",
    "Total secret rotations performed.",
    labelnames=["path"],
)
"""Counter: one increment per SecretRotator rotation."""

SECRET_REVOCATIONS_TOTAL: Counter = Counter(
    "voiceos_secret_revocations_total",
    "Total emergency secret revocations.",
    labelnames=["path"],
)
"""Counter: one increment per EmergencyRevocation.revoke()."""


def record_secret_access(path: str) -> None:
    SECRET_ACCESSES_TOTAL.labels(path=path).inc()


def record_rotation(path: str) -> None:
    SECRET_ROTATIONS_TOTAL.labels(path=path).inc()


def record_revocation(path: str) -> None:
    SECRET_REVOCATIONS_TOTAL.labels(path=path).inc()
