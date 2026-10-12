"""Prometheus metrics for ops_intelligence.plumbing (ADR-006 Sec 3.0/13.1).

Standard RED metrics via the shared factory, plus a gauge for currently-open
alerts by status -- this sub-component's own health signal, independent of
anything in ``reasoning/``.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge

from src.libs.observability.metrics import get_red_metrics

red_metrics = get_red_metrics("ops_intelligence_plumbing")

open_alerts_gauge = Gauge(
    "voiceos_ops_intelligence_open_alerts",
    "Currently open (non-resolved) alerts in alert_history, by status and source.",
    labelnames=["status", "source"],
)

escalation_scheduler_last_run_gauge = Gauge(
    "voiceos_alert_lifecycle_scheduler_last_run_timestamp",
    "Unix timestamp of the last successful escalation-scheduler tick.",
)

escalation_scheduler_errors_counter = Counter(
    "voiceos_alert_lifecycle_scheduler_errors_total",
    "Count of escalation-scheduler tick failures.",
)

escalations_counter = Counter(
    "voiceos_alert_lifecycle_escalations_total",
    "Count of alerts auto-escalated by the lifecycle scheduler, by severity.",
    labelnames=["severity"],
)


def record_open_alerts(status: str, source: str, count: int) -> None:
    open_alerts_gauge.labels(status=status, source=source).set(count)


def record_scheduler_tick(timestamp: float) -> None:
    escalation_scheduler_last_run_gauge.set(timestamp)


def record_scheduler_error() -> None:
    escalation_scheduler_errors_counter.inc()


def record_escalation(severity: str) -> None:
    escalations_counter.labels(severity=severity).inc()


__all__ = [
    "escalation_scheduler_errors_counter",
    "escalation_scheduler_last_run_gauge",
    "escalations_counter",
    "open_alerts_gauge",
    "record_escalation",
    "record_open_alerts",
    "record_scheduler_error",
    "record_scheduler_tick",
    "red_metrics",
]
