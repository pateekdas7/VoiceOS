"""Prometheus metrics for the Contact Center Platform (V5 Ch7 §"Observability").

Architecture: V5 Ch7 (Contact Center Platform).
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

TRANSFER_COUNT: Counter = Counter(
    "voiceos_contact_center_transfers_total",
    "Total AI -> human live transfers, by reason.",
    labelnames=["reason"],
)

SUPERVISOR_INTERVENTIONS: Counter = Counter(
    "voiceos_contact_center_supervisor_interventions_total",
    "Total supervisor actions, by action type (monitor/barge_in/override).",
    labelnames=["action"],
)

HANDLE_TIME_SECONDS: Histogram = Histogram(
    "voiceos_contact_center_handle_time_seconds",
    "Time a human agent spends handling a transferred call, in seconds.",
)


def record_transfer(reason: str) -> None:
    TRANSFER_COUNT.labels(reason=reason).inc()


def record_supervisor_intervention(action: str) -> None:
    SUPERVISOR_INTERVENTIONS.labels(action=action).inc()


def record_handle_time(seconds: float) -> None:
    HANDLE_TIME_SECONDS.observe(seconds)
