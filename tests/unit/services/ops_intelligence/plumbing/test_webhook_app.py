"""Unit tests for the Alertmanager webhook ASGI app (ADR-006 Sec 4)."""

from __future__ import annotations

from starlette.testclient import TestClient

from src.services.ops_intelligence.models import AlertSource
from src.services.ops_intelligence.plumbing.alert_lifecycle import AlertLifecycleService
from src.services.ops_intelligence.plumbing.webhook_app import create_webhook_app
from src.services.ops_intelligence.plumbing.webhook_ingest import WebhookIngestService

from .test_alert_lifecycle import _FakeAlertRepository, _RecordingNotificationPort


class TestWebhookApp:
    def test_post_alertmanager_webhook_ingests_alerts(self) -> None:
        repo = _FakeAlertRepository()
        lifecycle = AlertLifecycleService(repo, _RecordingNotificationPort())
        ingest = WebhookIngestService(lifecycle)
        client = TestClient(create_webhook_app(ingest))

        response = client.post(
            "/webhooks/alertmanager",
            json={"alerts": [{"labels": {"alertname": "GPUUnavailable", "severity": "critical"}, "annotations": {}}]},
        )

        assert response.status_code == 200
        assert response.json() == {"ingested": 1}
        assert len(repo._rows) == 1
        assert next(iter(repo._rows.values())).source == AlertSource.ALERTMANAGER

    def test_post_empty_alerts_batch_ingests_nothing(self) -> None:
        repo = _FakeAlertRepository()
        lifecycle = AlertLifecycleService(repo, _RecordingNotificationPort())
        ingest = WebhookIngestService(lifecycle)
        client = TestClient(create_webhook_app(ingest))

        response = client.post("/webhooks/alertmanager", json={"alerts": []})

        assert response.status_code == 200
        assert response.json() == {"ingested": 0}

    def test_get_is_not_allowed(self) -> None:
        repo = _FakeAlertRepository()
        lifecycle = AlertLifecycleService(repo, _RecordingNotificationPort())
        client = TestClient(create_webhook_app(WebhookIngestService(lifecycle)))
        response = client.get("/webhooks/alertmanager")
        assert response.status_code == 405
