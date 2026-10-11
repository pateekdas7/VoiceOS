"""Unit tests for src/services/ops_intelligence/plumbing/alert_lifecycle.py (ADR-006 Sec 4)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from src.services.ops_intelligence.models import AlertRecord, AlertSource, AlertStatus, Severity
from src.services.ops_intelligence.plumbing.alert_lifecycle import (
    DEFAULT_ESCALATION_TIMEOUTS_SECONDS,
    AlertLifecycleService,
    make_fingerprint,
)


class _FakeAlertRepository:
    """In-memory fake -- Phase 1 local/mock, no real Postgres required."""

    def __init__(self) -> None:
        self._rows: dict[str, AlertRecord] = {}

    def create(self, alert: AlertRecord) -> AlertRecord:
        self._rows[alert.alert_id] = alert
        return alert

    def get(self, alert_id: str) -> AlertRecord | None:
        return self._rows.get(alert_id)

    def find_by_fingerprint_open(self, fingerprint: str) -> AlertRecord | None:
        for alert in self._rows.values():
            if alert.fingerprint == fingerprint and alert.status != AlertStatus.RESOLVED:
                return alert
        return None

    def update_status(self, alert_id: str, alert: AlertRecord) -> AlertRecord:
        self._rows[alert_id] = alert
        return alert

    def list_open(self, tenant_id: str | None = None) -> tuple[AlertRecord, ...]:
        return tuple(a for a in self._rows.values() if a.status != AlertStatus.RESOLVED)

    def count_by_status(self) -> dict[tuple[AlertStatus, str], int]:
        counts: dict[tuple[AlertStatus, str], int] = {}
        for a in self._rows.values():
            if a.status == AlertStatus.RESOLVED:
                continue
            key = (a.status, a.source.value)
            counts[key] = counts.get(key, 0) + 1
        return counts


class _RecordingNotificationPort:
    def __init__(self) -> None:
        self.notified: list[AlertRecord] = []

    def notify_alert(self, alert: AlertRecord) -> None:
        self.notified.append(alert)


def _service() -> tuple[AlertLifecycleService, _FakeAlertRepository, _RecordingNotificationPort]:
    repo = _FakeAlertRepository()
    notifier = _RecordingNotificationPort()
    return AlertLifecycleService(repo, notifier), repo, notifier


class TestIngest:
    def test_ingest_creates_a_new_firing_alert(self) -> None:
        service, repo, notifier = _service()
        alert = service.ingest(
            source=AlertSource.ALERTMANAGER,
            fingerprint="abc123",
            severity=Severity.CRITICAL,
            tenant_id=None,
            labels={"alertname": "GPUUnavailable"},
            annotations={},
        )
        assert alert.status == AlertStatus.FIRING
        assert repo.get(alert.alert_id) is not None
        assert notifier.notified == [alert]

    def test_ingest_dedupes_on_open_fingerprint(self) -> None:
        service, repo, _ = _service()
        first = service.ingest(
            source=AlertSource.ALERTMANAGER,
            fingerprint="dup-fp",
            severity=Severity.WARNING,
            tenant_id=None,
            labels={},
            annotations={},
        )
        second = service.ingest(
            source=AlertSource.ALERTMANAGER,
            fingerprint="dup-fp",
            severity=Severity.WARNING,
            tenant_id=None,
            labels={},
            annotations={},
        )
        assert first.alert_id == second.alert_id
        assert len(repo._rows) == 1

    def test_ingest_after_resolve_creates_a_fresh_alert(self) -> None:
        service, repo, _ = _service()
        first = service.ingest(
            source=AlertSource.ALERTMANAGER,
            fingerprint="re-fire-fp",
            severity=Severity.WARNING,
            tenant_id=None,
            labels={},
            annotations={},
        )
        service.resolve(first.alert_id)
        second = service.ingest(
            source=AlertSource.ALERTMANAGER,
            fingerprint="re-fire-fp",
            severity=Severity.WARNING,
            tenant_id=None,
            labels={},
            annotations={},
        )
        assert second.alert_id != first.alert_id
        assert len(repo._rows) == 2


class TestLifecycleTransitions:
    def test_acknowledge_sets_status_and_actor(self) -> None:
        service, _, _ = _service()
        alert = service.ingest(
            source=AlertSource.COMPLIANCE_MONITORING,
            fingerprint="fp1",
            severity=Severity.CRITICAL,
            tenant_id="tenant-1",
            labels={},
            annotations={},
        )
        acked = service.acknowledge(alert.alert_id, by="alice@voiceos")
        assert acked.status == AlertStatus.ACKNOWLEDGED
        assert acked.acknowledged_by == "alice@voiceos"
        assert acked.acknowledged_at is not None

    def test_escalate_notifies_and_sets_target(self) -> None:
        service, _, notifier = _service()
        alert = service.ingest(
            source=AlertSource.ALERTMANAGER,
            fingerprint="fp2",
            severity=Severity.WARNING,
            tenant_id=None,
            labels={},
            annotations={},
        )
        escalated = service.escalate(alert.alert_id, to="oncall_primary")
        assert escalated.status == AlertStatus.ESCALATED
        assert escalated.escalated_to == "oncall_primary"
        assert escalated in notifier.notified

    def test_resolve_sets_resolved_at(self) -> None:
        service, _, _ = _service()
        alert = service.ingest(
            source=AlertSource.ALERTMANAGER,
            fingerprint="fp3",
            severity=Severity.INFO,
            tenant_id=None,
            labels={},
            annotations={},
        )
        resolved = service.resolve(alert.alert_id)
        assert resolved.status == AlertStatus.RESOLVED
        assert resolved.resolved_at is not None

    def test_acknowledge_unknown_alert_raises(self) -> None:
        service, _, _ = _service()
        with pytest.raises(ValueError, match="no such alert"):
            service.acknowledge("does-not-exist", by="alice")


class TestEscalationEvaluation:
    def test_evaluate_escalations_escalates_overdue_critical(self) -> None:
        service, repo, _ = _service()
        alert = service.ingest(
            source=AlertSource.ALERTMANAGER,
            fingerprint="overdue-critical",
            severity=Severity.CRITICAL,
            tenant_id=None,
            labels={},
            annotations={},
        )
        stale_fired_at = datetime.now(UTC) - timedelta(
            seconds=DEFAULT_ESCALATION_TIMEOUTS_SECONDS[Severity.CRITICAL] + 1
        )
        repo._rows[alert.alert_id] = replace(alert, fired_at=stale_fired_at)

        escalated = service.evaluate_escalations()

        assert len(escalated) == 1
        assert escalated[0].status == AlertStatus.ESCALATED

    def test_evaluate_escalations_leaves_fresh_alerts_alone(self) -> None:
        service, _, _ = _service()
        service.ingest(
            source=AlertSource.ALERTMANAGER,
            fingerprint="fresh",
            severity=Severity.CRITICAL,
            tenant_id=None,
            labels={},
            annotations={},
        )
        assert service.evaluate_escalations() == ()

    def test_evaluate_escalations_ignores_already_acknowledged(self) -> None:
        service, repo, _ = _service()
        alert = service.ingest(
            source=AlertSource.ALERTMANAGER,
            fingerprint="acked",
            severity=Severity.CRITICAL,
            tenant_id=None,
            labels={},
            annotations={},
        )
        service.acknowledge(alert.alert_id, by="bob")
        stale_fired_at = datetime.now(UTC) - timedelta(hours=2)
        current = repo.get(alert.alert_id)
        assert current is not None
        repo._rows[alert.alert_id] = replace(current, fired_at=stale_fired_at)

        assert service.evaluate_escalations() == ()


class TestFingerprint:
    def test_fingerprint_deterministic(self) -> None:
        fp1 = make_fingerprint(source=AlertSource.ALERTMANAGER, name="NodeDown", labels={"node": "gpu-1"})
        fp2 = make_fingerprint(source=AlertSource.ALERTMANAGER, name="NodeDown", labels={"node": "gpu-1"})
        assert fp1 == fp2

    def test_fingerprint_differs_by_label(self) -> None:
        fp1 = make_fingerprint(source=AlertSource.ALERTMANAGER, name="NodeDown", labels={"node": "gpu-1"})
        fp2 = make_fingerprint(source=AlertSource.ALERTMANAGER, name="NodeDown", labels={"node": "gpu-2"})
        assert fp1 != fp2

    def test_fingerprint_label_order_independent(self) -> None:
        fp1 = make_fingerprint(source=AlertSource.ALERTMANAGER, name="X", labels={"a": "1", "b": "2"})
        fp2 = make_fingerprint(source=AlertSource.ALERTMANAGER, name="X", labels={"b": "2", "a": "1"})
        assert fp1 == fp2
