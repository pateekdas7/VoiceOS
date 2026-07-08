"""RegulatoryNotifier — DPDP breach notification timer enforcement (V4 Ch17 §17.12 P2).

Architecture: V4 Ch17 (Incident Response) §17.12 ("Notify: DPDP breach
notification within timeline"); V4 Ch2 (DPDP Compliance).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .playbooks import IncidentType

DPDP_NOTIFICATION_WINDOW = timedelta(hours=72)
"""DPDP regulatory breach-notification deadline (V4 Ch17 §17.12/§17.13)."""


class RegulatoryNotifier:
    """Computes and checks the DPDP regulatory notification deadline for an incident."""

    def notification_deadline(self, incident_type: IncidentType, opened_at: datetime) -> datetime | None:
        """The regulatory notification deadline for this incident, or ``None`` if not applicable.

        Only ``DATA_BREACH`` incidents carry a DPDP notification timer
        (V4 Ch17 §17.12 P2 "Data leak").
        """
        if incident_type != IncidentType.DATA_BREACH:
            return None
        return opened_at + DPDP_NOTIFICATION_WINDOW

    def is_overdue(self, deadline: datetime | None, *, now: datetime | None = None) -> bool:
        """Whether ``deadline`` has passed without notification having been filed."""
        if deadline is None:
            return False
        current = now if now is not None else datetime.now(UTC)
        return current > deadline
