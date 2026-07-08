"""Unit tests for src/services/incident_response/ (V4 Ch17)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from src.services.incident_response.notifier import DPDP_NOTIFICATION_WINDOW
from src.services.incident_response.playbooks import IncidentType, Severity
from src.services.incident_response.service import IncidentResponse, IncidentStatus


class _FakeAuditLogger:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def record(
        self,
        tenant_id: Any,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        *,
        event_payload: dict[str, Any] | None = None,
        ip_address: str = "",
    ) -> str:
        self.records.append({"action": action, "outcome": outcome, "resource_id": resource_id})
        return "fake-hash"


class TestIncidentLifecycle:
    def test_incident_response_lifecycle(self) -> None:
        audit_logger = _FakeAuditLogger()
        service = IncidentResponse(audit_logger=audit_logger)

        incident = service.open("tenant-a", IncidentType.UNAUTHORIZED_ACCESS, Severity.SEV2, {"source_ip": "1.2.3.4"})
        assert incident.status == IncidentStatus.OPEN

        result = service.execute_playbook(incident.incident_id)
        assert len(result.steps_executed) > 0
        assert service.get(incident.incident_id).status == IncidentStatus.CONTAINED

        service.notify(incident.incident_id, ["security-oncall@voiceos.example"])

        closed = service.close(incident.incident_id, "access revoked, root cause patched")
        assert closed.status == IncidentStatus.CLOSED
        assert closed.resolution == "access revoked, root cause patched"

        recorded_actions = [record["action"] for record in audit_logger.records]
        assert "incident.opened" in recorded_actions
        assert "incident.playbook_executed" in recorded_actions
        assert "incident.notified" in recorded_actions
        assert "incident.closed" in recorded_actions

    def test_incident_response_dpdp_timer(self) -> None:
        service = IncidentResponse()

        incident = service.open("tenant-a", IncidentType.DATA_BREACH, Severity.SEV1, {"records_exposed": 100})

        assert incident.notification_deadline_at is not None
        assert incident.notification_deadline_at - incident.opened_at == DPDP_NOTIFICATION_WINDOW
        assert DPDP_NOTIFICATION_WINDOW == timedelta(hours=72)

    def test_non_data_breach_incident_has_no_notification_deadline(self) -> None:
        service = IncidentResponse()

        incident = service.open("tenant-a", IncidentType.CREDENTIAL_COMPROMISE, Severity.SEV1, {})

        assert incident.notification_deadline_at is None

    def test_each_incident_type_has_a_registered_playbook(self) -> None:
        service = IncidentResponse()

        for incident_type in IncidentType:
            incident = service.open("tenant-a", incident_type, Severity.SEV3, {})
            result = service.execute_playbook(incident.incident_id)
            assert len(result.steps_executed) > 0
