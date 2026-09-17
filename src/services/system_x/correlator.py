"""IncidentCorrelator — groups alerts into incidents within a time window."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .classifier import IncidentClassifier
from .models import IngestAlert, IncidentSeverity

_CORRELATION_WINDOW = timedelta(minutes=5)
_MAX_INCIDENT_AGE = timedelta(hours=2)


@dataclass
class _OpenIncident:
    incident_id: str
    alerts: list[IngestAlert]
    severity: IncidentSeverity
    opened_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class IncidentCorrelator:
    def __init__(self, classifier: IncidentClassifier) -> None:
        self._classifier = classifier
        self._open: dict[str, _OpenIncident] = {}  # incident_id -> _OpenIncident

    def correlate(self, alert: IngestAlert) -> tuple[str, bool]:
        """Match alert to an existing open incident or open a new one.

        Returns (incident_id, is_new). is_new=True means a new incident was created.
        """
        now = datetime.now(UTC)
        # prune stale open incidents
        self._open = {
            k: v
            for k, v in self._open.items()
            if now - v.opened_at < _MAX_INCIDENT_AGE
        }

        # look for existing incident with matching service
        alert_services = set(self._classifier.classify_services([alert]))
        for incident_id, open_inc in self._open.items():
            existing_services = set(self._classifier.classify_services(open_inc.alerts))
            if alert_services & existing_services:  # service overlap
                if alert.fingerprint not in {a.fingerprint for a in open_inc.alerts}:
                    open_inc.alerts.append(alert)
                return incident_id, False

        # open new incident
        incident_id = str(uuid.uuid4())
        severity = self._classifier.classify_severity(alert)
        self._open[incident_id] = _OpenIncident(
            incident_id=incident_id,
            alerts=[alert],
            severity=severity,
            opened_at=now,
        )
        return incident_id, True

    def get_alerts(self, incident_id: str) -> list[IngestAlert]:
        """Return a snapshot of alerts for the given open incident."""
        inc = self._open.get(incident_id)
        return list(inc.alerts) if inc else []

    def close(self, incident_id: str) -> None:
        """Remove incident from the open set once resolved."""
        self._open.pop(incident_id, None)


__all__ = ["IncidentCorrelator"]
