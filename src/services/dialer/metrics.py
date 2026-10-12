"""Prometheus metrics for the Dialer service.

Architecture: V5 Ch6 (Campaign Engine — Dialer, ADR-005 §15).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge

CALLS_PLACED: Counter = Counter(
    "voiceos_dialer_calls_placed_total",
    "Total outbound calls placed via Twilio.",
    labelnames=["tenant_id", "campaign_id"],
)

CALLS_FAILED: Counter = Counter(
    "voiceos_dialer_calls_failed_total",
    "Total outbound call placement failures.",
    labelnames=["tenant_id", "campaign_id"],
)

CALLS_ANSWERED: Counter = Counter(
    "voiceos_dialer_calls_answered_total",
    "Total calls answered by a human.",
    labelnames=["tenant_id", "campaign_id"],
)

CALLS_MACHINE: Counter = Counter(
    "voiceos_dialer_calls_machine_total",
    "Total calls detected as answering machine.",
    labelnames=["tenant_id", "campaign_id"],
)

CALLS_COMPLETED: Counter = Counter(
    "voiceos_dialer_calls_completed_total",
    "Total calls that reached a terminal state.",
    labelnames=["tenant_id", "campaign_id", "status"],
)

CALLS_BLOCKED_DND: Counter = Counter(
    "voiceos_dialer_calls_blocked_dnd_total",
    "Total outbound calls blocked at dial time because the destination is on a DND list.",
    labelnames=["tenant_id", "campaign_id"],
)
"""Counter: dial attempts refused pre-placement due to a DND match. This
is the compliance-relevant signal for RBI FPC / TRAI NDNC enforcement —
a rising rate means the DND list is either freshly loaded (expected right
after an update) or the audience selector is not filtering DND correctly
before leads reach the dialer queue."""

ACTIVE_CALLS: Gauge = Gauge(
    "voiceos_dialer_active_calls",
    "Current number of in-flight dialer calls.",
    labelnames=["tenant_id", "campaign_id"],
)

QUEUE_DEPTH: Gauge = Gauge(
    "voiceos_dialer_queue_depth",
    "Current number of leads waiting in the dialer queue.",
    labelnames=["tenant_id", "campaign_id"],
)

SESSIONS_ACTIVE: Gauge = Gauge(
    "voiceos_dialer_sessions_active",
    "Number of active dialer sessions (running campaigns).",
    labelnames=["tenant_id"],
)


def record_call_placed(tenant_id: str, campaign_id: str) -> None:
    CALLS_PLACED.labels(tenant_id=tenant_id, campaign_id=campaign_id).inc()


def record_call_failed(tenant_id: str, campaign_id: str) -> None:
    CALLS_FAILED.labels(tenant_id=tenant_id, campaign_id=campaign_id).inc()


def record_call_answered(tenant_id: str, campaign_id: str) -> None:
    CALLS_ANSWERED.labels(tenant_id=tenant_id, campaign_id=campaign_id).inc()


def record_call_machine(tenant_id: str, campaign_id: str) -> None:
    CALLS_MACHINE.labels(tenant_id=tenant_id, campaign_id=campaign_id).inc()


def record_call_completed(tenant_id: str, campaign_id: str, status: str) -> None:
    CALLS_COMPLETED.labels(tenant_id=tenant_id, campaign_id=campaign_id, status=status).inc()


def record_call_blocked_dnd(tenant_id: str, campaign_id: str) -> None:
    CALLS_BLOCKED_DND.labels(tenant_id=tenant_id, campaign_id=campaign_id).inc()
