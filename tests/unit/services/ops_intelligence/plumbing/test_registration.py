"""Unit tests for ops_intelligence.plumbing.registration (ADR-006 Sec 4/13.4)."""

from __future__ import annotations

from typing import Any

from src.services.ops_intelligence.plumbing.alert_lifecycle import AlertLifecycleService
from src.services.ops_intelligence.plumbing.registration import (
    COMPLIANCE_VIOLATION_ALERT_EVENT_TYPE,
    register_ops_intelligence_consumers,
)
from src.services.ops_intelligence.plumbing.webhook_ingest import WebhookIngestService

from .test_alert_lifecycle import _FakeAlertRepository, _RecordingNotificationPort


class _FakeConsumer:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def subscribe(self, event_type: str, handler: Any) -> None:
        self.handlers[event_type] = handler


class TestRegisterOpsIntelligenceConsumers:
    def test_subscribes_compliance_signal_handler(self) -> None:
        repo = _FakeAlertRepository()
        ingest = WebhookIngestService(AlertLifecycleService(repo, _RecordingNotificationPort()))
        consumer = _FakeConsumer()

        register_ops_intelligence_consumers(consumer, ingest)

        assert set(consumer.handlers) == {COMPLIANCE_VIOLATION_ALERT_EVENT_TYPE}
        assert consumer.handlers[COMPLIANCE_VIOLATION_ALERT_EVENT_TYPE] == ingest.ingest_compliance_signal

    def test_registered_handler_actually_ingests_an_alert(self) -> None:
        repo = _FakeAlertRepository()
        ingest = WebhookIngestService(AlertLifecycleService(repo, _RecordingNotificationPort()))
        consumer = _FakeConsumer()
        register_ops_intelligence_consumers(consumer, ingest)

        handler = consumer.handlers[COMPLIANCE_VIOLATION_ALERT_EVENT_TYPE]
        handler({"tenant_id": "t1", "signal_type": "COMPLIANCE_SIGNAL_REPEATED_AUTHN_FAILURE", "description": "x"})

        assert len(repo._rows) == 1
