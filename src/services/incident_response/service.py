"""IncidentResponse — incident lifecycle management + playbook execution (V4 Ch17).

In-process library façade (same pre-Sprint-026 precedent as every VoiceOS
service since Sprint-013 — see ``src/services/policy_engine/service.py``).
Incident state lives in-process for the service's lifetime; every lifecycle
transition is durably recorded via ``AuditLogger`` (V4 Ch17 §17.9 "The whole
lifecycle is audited (Ch 11)"), which is the system of record for
reconstructing incidents after a restart — no separate incidents table is
introduced (Sprint-020.md's file list doesn't specify one).

Architecture: V4 Ch17 (Incident Response) §17.7 (Public Interfaces).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .notifier import RegulatoryNotifier
from .playbooks import IncidentType, Playbook, PlaybookRegistry, PlaybookResult, Severity

__all__ = [
    "Incident",
    "IncidentNotFoundError",
    "IncidentResponse",
    "IncidentStatus",
    "IncidentType",
    "Playbook",
    "PlaybookRegistry",
    "PlaybookResult",
    "Severity",
]


class IncidentStatus(StrEnum):
    """Incident lifecycle status (V4 Ch17 §17.6)."""

    OPEN = "OPEN"
    TRIAGED = "TRIAGED"
    CONTAINED = "CONTAINED"
    ERADICATED = "ERADICATED"
    RECOVERED = "RECOVERED"
    CLOSED = "CLOSED"


class IncidentNotFoundError(KeyError):
    """Raised when an operation references an unknown ``incident_id``."""


@dataclass
class Incident:
    """A tracked incident (V4 Ch17 §17.6). Mutable — the lifecycle status changes in place."""

    incident_id: str
    tenant_id: str
    incident_type: IncidentType
    severity: Severity
    status: IncidentStatus
    context: dict[str, Any]
    opened_at: datetime
    notification_deadline_at: datetime | None = None
    resolution: str | None = None
    closed_at: datetime | None = None


class IncidentResponse:
    """Tracks incidents and drives playbook execution (V4 Ch17 §17.7)."""

    def __init__(
        self,
        playbook_registry: PlaybookRegistry | None = None,
        notifier: RegulatoryNotifier | None = None,
        audit_logger: Any | None = None,
    ) -> None:
        self._playbooks = playbook_registry or PlaybookRegistry()
        self._notifier = notifier or RegulatoryNotifier()
        self._audit_logger = audit_logger
        self._incidents: dict[str, Incident] = {}

    def open(
        self,
        tenant_id: str,
        incident_type: IncidentType,
        severity: Severity,
        context: dict[str, Any],
        actor_id: str = "incident_response_service",
    ) -> Incident:
        """Open a new incident (V4 Ch17 §17.7)."""
        incident_id = str(uuid.uuid4())
        opened_at = datetime.now(UTC)
        deadline = self._notifier.notification_deadline(incident_type, opened_at)

        incident = Incident(
            incident_id=incident_id,
            tenant_id=tenant_id,
            incident_type=incident_type,
            severity=severity,
            status=IncidentStatus.OPEN,
            context=context,
            opened_at=opened_at,
            notification_deadline_at=deadline,
        )
        self._incidents[incident_id] = incident

        if self._audit_logger is not None:
            self._audit_logger.record(
                tenant_id,
                actor_id,
                "incident.opened",
                "Incident",
                incident_id,
                "OPEN",
                event_payload={
                    "incident_type": incident_type.value,
                    "severity": severity.value,
                    "notification_deadline_at": deadline.isoformat() if deadline else None,
                },
            )
        return incident

    def execute_playbook(self, incident_id: str) -> PlaybookResult:
        """Run the registered playbook for this incident (V4 Ch17 §17.7)."""
        incident = self._get(incident_id)
        playbook = self._playbooks.get(incident.incident_type)
        incident.status = IncidentStatus.CONTAINED

        if self._audit_logger is not None:
            self._audit_logger.record(
                incident.tenant_id,
                "incident_response_service",
                "incident.playbook_executed",
                "Incident",
                incident_id,
                "SUCCESS",
                event_payload={"steps": list(playbook.steps)},
            )
        return PlaybookResult(incident_id=incident_id, steps_executed=playbook.steps)

    def notify(self, incident_id: str, stakeholders: list[str]) -> None:
        """Notify on-call + stakeholders (V4 Ch17 §17.7)."""
        incident = self._get(incident_id)

        if self._audit_logger is not None:
            self._audit_logger.record(
                incident.tenant_id,
                "incident_response_service",
                "incident.notified",
                "Incident",
                incident_id,
                "SUCCESS",
                event_payload={"stakeholders": stakeholders},
            )

    def close(self, incident_id: str, resolution: str) -> Incident:
        """Close the incident with resolution notes (V4 Ch17 §17.7)."""
        incident = self._get(incident_id)
        incident.status = IncidentStatus.CLOSED
        incident.resolution = resolution
        incident.closed_at = datetime.now(UTC)

        if self._audit_logger is not None:
            self._audit_logger.record(
                incident.tenant_id,
                "incident_response_service",
                "incident.closed",
                "Incident",
                incident_id,
                "SUCCESS",
                event_payload={"resolution": resolution},
            )
        return incident

    def get(self, incident_id: str) -> Incident:
        """Fetch a tracked incident by id."""
        return self._get(incident_id)

    def _get(self, incident_id: str) -> Incident:
        incident = self._incidents.get(incident_id)
        if incident is None:
            raise IncidentNotFoundError(incident_id)
        return incident
