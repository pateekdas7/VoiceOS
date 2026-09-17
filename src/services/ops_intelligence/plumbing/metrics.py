"""Prometheus metrics for ops_intelligence.plumbing (ADR-006 Sec 3.0/13.1).

Standard RED metrics via the shared factory, plus a gauge for currently-open
alerts by status -- this sub-component's own health signal, independent of
anything in ``reasoning/``.
"""

from __future__ import annotations

from prometheus_client import Gauge

from src.libs.observability.metrics import get_red_metrics

red_metrics = get_red_metrics("ops_intelligence_plumbing")

open_alerts_gauge = Gauge(
    "voiceos_ops_intelligence_open_alerts",
    "Currently open (non-resolved) alerts in alert_history, by status and source.",
    labelnames=["status", "source"],
)


def record_open_alerts(status: str, source: str, count: int) -> None:
    open_alerts_gauge.labels(status=status, source=source).set(count)


__all__ = ["open_alerts_gauge", "record_open_alerts", "red_metrics"]
