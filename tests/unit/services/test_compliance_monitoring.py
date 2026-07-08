"""Unit tests for src/services/compliance_monitoring/ (V4 Ch16)."""

from __future__ import annotations

from src.libs.audit.event import AuditEvent
from src.services.compliance_monitoring.service import ComplianceMonitoring, ComplianceStatus


def _consent_denied_event(tenant_id: str, seq: int) -> AuditEvent:
    from datetime import UTC, datetime

    return AuditEvent(
        audit_id=f"audit-{seq}",
        tenant_id=tenant_id,
        actor_id="policy_engine",
        action="consent.denied",
        resource_type="Consent",
        resource_id=f"cust-{seq}",
        outcome="DENY",
        recorded_at=datetime.now(UTC),
    )


class _FakePublisher:
    def __init__(self) -> None:
        self.published: list[dict[str, object]] = []

    def publish(self, event_type: str, tenant_id: str, payload: dict[str, object], correlation_id: str) -> None:
        self.published.append({"event_type": event_type, "tenant_id": tenant_id, "payload": payload})


class TestComplianceMonitoring:
    def test_compliance_monitoring_violation_alert(self) -> None:
        publisher = _FakePublisher()
        monitoring = ComplianceMonitoring.create(publisher=publisher)

        for i in range(5):
            monitoring.ingest(_consent_denied_event("tenant-a", i))

        assert len(publisher.published) == 1
        assert publisher.published[0]["event_type"] == "compliance.violation_alert"

    def test_compliance_monitoring_status_compliant(self) -> None:
        monitoring = ComplianceMonitoring()

        status = monitoring.status("tenant-a")

        assert status == ComplianceStatus.COMPLIANT

    def test_compliance_monitoring_status_violation_after_alert(self) -> None:
        monitoring = ComplianceMonitoring()
        for i in range(5):
            monitoring.ingest(_consent_denied_event("tenant-a", i))

        assert monitoring.status("tenant-a") == ComplianceStatus.VIOLATION

    def test_four_events_do_not_trigger_alert(self) -> None:
        publisher = _FakePublisher()
        monitoring = ComplianceMonitoring.create(publisher=publisher)

        for i in range(4):
            monitoring.ingest(_consent_denied_event("tenant-a", i))

        assert publisher.published == []
        assert monitoring.status("tenant-a") == ComplianceStatus.COMPLIANT
