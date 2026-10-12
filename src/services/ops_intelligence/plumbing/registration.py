"""Event-bus consumer registration for ops_intelligence.plumbing (ADR-006 Sec 4/13.4).

Subscribes ``WebhookIngestService.ingest_compliance_signal`` to
``compliance.violation_alert`` on the existing ``voiceos-events`` bus --
the same ``Consumer.subscribe(event_type, handler)`` pattern
``metering.collector.UsageCollector.register()`` already uses. This is the
only event-bus subscription ``plumbing/`` registers; Alertmanager-sourced
alerts arrive via the HTTP webhook (``webhook_app.py``), not the event bus.
"""

from __future__ import annotations

from typing import Any, Protocol

from .webhook_ingest import WebhookIngestService

COMPLIANCE_VIOLATION_ALERT_EVENT_TYPE = "compliance.violation_alert"


class ConsumerPort(Protocol):
    def subscribe(self, event_type: str, handler: Any) -> None: ...


def register_ops_intelligence_consumers(consumer: ConsumerPort, ingest: WebhookIngestService) -> None:
    """Wire ``ingest.ingest_compliance_signal`` onto the event bus consumer at composition-root time."""
    consumer.subscribe(COMPLIANCE_VIOLATION_ALERT_EVENT_TYPE, ingest.ingest_compliance_signal)


__all__ = ["COMPLIANCE_VIOLATION_ALERT_EVENT_TYPE", "ConsumerPort", "register_ops_intelligence_consumers"]
