"""Unit tests for src/services/ops_intelligence/plumbing/webhook_ingest.py (ADR-006 Sec 4/13.4).

Verifies both pre-existing alert sources (Alertmanager, ComplianceAlerter)
converge on the one lifecycle model without either source's own detection
logic being reimplemented here.
"""

from __future__ import annotations

from src.services.ops_intelligence.models import AlertSource, AlertStatus, Severity
from src.services.ops_intelligence.plumbing.alert_lifecycle import AlertLifecycleService
from src.services.ops_intelligence.plumbing.webhook_ingest import WebhookIngestService

from .test_alert_lifecycle import _FakeAlertRepository, _RecordingNotificationPort


def _build() -> tuple[WebhookIngestService, _FakeAlertRepository]:
    repo = _FakeAlertRepository()
    lifecycle = AlertLifecycleService(repo, _RecordingNotificationPort())
    return WebhookIngestService(lifecycle), repo


class TestAlertmanagerIngest:
    def test_single_alert_batch(self) -> None:
        ingest, repo = _build()
        payload = {
            "alerts": [
                {
                    "fingerprint": "am-fp-1",
                    "labels": {"alertname": "GPUUnavailable", "severity": "critical", "tenant_id": ""},
                    "annotations": {"summary": "GPU node down"},
                }
            ]
        }
        results = ingest.ingest_alertmanager_webhook(payload)
        assert len(results) == 1
        assert results[0].source == AlertSource.ALERTMANAGER
        assert results[0].severity == Severity.CRITICAL
        assert len(repo._rows) == 1

    def test_multi_alert_batch(self) -> None:
        ingest, repo = _build()
        payload = {
            "alerts": [
                {"labels": {"alertname": "DiskAlmostFull", "severity": "warning"}, "annotations": {}},
                {"labels": {"alertname": "NodeDown", "severity": "critical"}, "annotations": {}},
            ]
        }
        results = ingest.ingest_alertmanager_webhook(payload)
        assert len(results) == 2
        assert len(repo._rows) == 2

    def test_unknown_severity_defaults_to_warning(self) -> None:
        ingest, _ = _build()
        payload = {"alerts": [{"labels": {"alertname": "X"}, "annotations": {}}]}
        results = ingest.ingest_alertmanager_webhook(payload)
        assert results[0].severity == Severity.WARNING

    def test_repeated_webhook_dedupes_via_fingerprint(self) -> None:
        ingest, repo = _build()
        payload = {"alerts": [{"fingerprint": "same-fp", "labels": {"alertname": "X"}, "annotations": {}}]}
        ingest.ingest_alertmanager_webhook(payload)
        ingest.ingest_alertmanager_webhook(payload)
        assert len(repo._rows) == 1


class TestComplianceSignalIngest:
    def test_compliance_signal_creates_critical_alert(self) -> None:
        ingest, repo = _build()
        event_payload = {
            "tenant_id": "tenant-42",
            "signal_type": "COMPLIANCE_SIGNAL_REPEATED_AUTHN_FAILURE",
            "description": "5 auth failures in 300s",
        }
        result = ingest.ingest_compliance_signal(event_payload)
        assert result.source == AlertSource.COMPLIANCE_MONITORING
        assert result.severity == Severity.CRITICAL
        assert result.tenant_id == "tenant-42"
        assert len(repo._rows) == 1
        assert repo._rows[result.alert_id].status == AlertStatus.FIRING

    def test_both_sources_land_in_the_same_alert_history(self) -> None:
        """Two independent sources, one lifecycle model -- no third alerting engine (Sec 13.4)."""
        ingest, repo = _build()
        ingest.ingest_alertmanager_webhook({"alerts": [{"labels": {"alertname": "X"}, "annotations": {}}]})
        ingest.ingest_compliance_signal({"tenant_id": "t1", "signal_type": "Y", "description": ""})
        sources = {row.source for row in repo._rows.values()}
        assert sources == {AlertSource.ALERTMANAGER, AlertSource.COMPLIANCE_MONITORING}
