"""Compliance Audit report template (V5 Ch12, V4 Ch11 Audit Trail)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from src.libs.audit.event import AuditEvent

from . import ReportData


def build(tenant_name: str, events: Sequence[AuditEvent], generated_at: datetime) -> ReportData:
    return ReportData(
        title=f"Compliance Audit — {tenant_name}",
        columns=("Recorded At", "Actor", "Action", "Resource", "Outcome"),
        rows=tuple(
            (
                event.recorded_at.isoformat(),
                event.actor_id,
                event.action,
                f"{event.resource_type}:{event.resource_id}",
                event.outcome,
            )
            for event in events
        ),
        generated_at=generated_at,
    )


__all__ = ["build"]
